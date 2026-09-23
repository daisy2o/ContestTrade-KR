"""
근거→원문 전수 감사 도구 (정확성 점검 1단계).

신호의 evidence 각각에 대해 기록한다:
- 판단 시점 이전(as-of)의 후보 원문 ID·관련 구절 (전체 DB가 아니라 어댑터와 동일한
  시간 규칙으로 제한: 텔레그램 date_utc < trigger-9h, Factiva pd_date+1 <= 판단일)
- 실제 모델 입력(background_information) 포함 여부 — 원문에 있어도 모델이 못 받았으면
  "그 원문을 근거로 판단했다"고 할 수 없다
- 회사/지표/방향 일치의 기계 판정 (시점·단위는 구절을 보고 사람이 최종 판정)
- 출처 귀속: 표기출처확인 / 타출처에서만 / 복수출처(귀속불명) / 미확인
  — 중복 전재 가능성 때문에 '타출처에서만'만 오표기 후보로 본다

숫자 일치와 주장 확인은 다른 단계다: 자동 판정은 '주장지지 후보'까지만 올리고,
확정은 구절을 읽는 사람(또는 검수 에이전트)의 몫이다.

사용: python -m evaluation.trace_evidence 2026-05-07 2026-05-29 2026-06-04
출력: evaluation/out/evidence_audit.md / .json
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parents[1]
TELE_DB = ROOT.parent / "data_collection" / "data" / "telegram" / "telegram_research.sqlite"
FACT_DB = ROOT.parent / "data_collection" / "data" / "factiva" / "factiva_news.sqlite"

SIG_RE = re.compile(r"<signal>(.*?)</signal>", re.S)
EV_RE = re.compile(r"<evidence>(.*?)</evidence>\s*<time>(.*?)</time>\s*<from_source>(.*?)</from_source>", re.S)
NUM_RE = re.compile(r"\d[\d,]*\.?\d*%?")
TOOL_SOURCES = {"stock_summary", "KR-Stock Summary", "price_info", "stock_symbol_search"}
METRIC_WORDS = ["영업이익", "매출", "순이익", "주가", "수익률", "거래량", "시가총액", "목표주가", "수주", "계약", "파업", "공시", "배당"]
DIR_WORDS = ["상승", "하락", "증가", "감소", "급등", "급락", "돌파", "경신", "흑자", "적자"]


def _field(tag, s):
    m = re.search(f"<{tag}>(.*?)</{tag}>", s, re.S)
    return m.group(1).strip() if m else ""


def load_asof_docs(judge_date: str):
    """어댑터와 동일한 as-of 규칙의 원문 풀. 반환: [(doc_id, source, text)]"""
    docs = []
    t_utc = (pd.Timestamp(judge_date + " 09:00:00") - pd.Timedelta(hours=9)).isoformat()
    t0 = (pd.Timestamp(judge_date) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    with sqlite3.connect(TELE_DB) as c:
        for ch, mid, txt in c.execute(
            "SELECT channel, message_id, text FROM messages WHERE date_utc>=? AND date_utc<? AND text!=''",
            (t0, t_utc),
        ):
            docs.append((f"tg:{ch}/{mid}", "telegram", txt))
    with sqlite3.connect(FACT_DB) as c:
        # D+1 규칙: pd_date+1 <= 판단일  ⇔  pd_date < 판단일
        for an, h, b in c.execute(
            "SELECT an, headline, body FROM articles WHERE pd_date>=? AND pd_date<?", (t0, judge_date)
        ):
            docs.append((f"fv:{an}", "factiva", (h or "") + "\n" + (b or "")))
    return docs


def excerpt(text: str, needle: str, width: int = 90) -> str:
    i = text.replace(",", "").find(needle)
    if i < 0:
        return ""
    # 콤마 제거 좌표를 원문 좌표로 근사 (간단화: 콤마 제거본에서 발췌)
    t = text.replace(",", "").replace("\n", " ")
    return t[max(0, i - width): i + width]


def audit_day(judge_date: str):
    docs = load_asof_docs(judge_date)
    norm_docs = [(did, src, t.replace(",", "")) for did, src, t in docs]
    rows = []
    for rp in sorted((ROOT / "agents_workspace" / "reports").rglob(f"{judge_date}_09-00-00.json")):
        rep = json.loads(rp.read_text())
        model_input = (rep.get("background_information") or "").replace(",", "")
        for blk in SIG_RE.findall(rep.get("final_result", "")):
            sym = _field("symbol_name", blk)
            for ev, t, src in EV_RE.findall(blk):
                ev, t, src = ev.strip(), t.strip(), src.strip()
                nums = [n.replace(",", "").rstrip("%") for n in NUM_RE.findall(ev)
                        if len(n.replace(",", "").rstrip("%")) >= 3]
                row = {
                    "date": judge_date, "agent": rp.parent.name, "symbol": sym,
                    "claimed_source": src, "claimed_time": t, "evidence": ev,
                    "numbers": nums, "is_tool": src in TOOL_SOURCES,
                    "in_model_input": None, "candidates": [], "source_attribution": None,
                    "auto": {"company": None, "metric": None, "direction": None},
                    "auto_verdict": None,
                }
                # 모델 입력 포함 여부 (숫자 기준; 숫자 없으면 회사명+지표어 근사)
                if nums:
                    row["in_model_input"] = all(n in model_input for n in nums) if model_input else False
                # 후보 원문 탐색 (as-of 풀)
                if nums and not row["is_tool"]:
                    hits_by_src = set()
                    scored = []
                    for did, dsrc, dt in norm_docs:
                        hits = sum(1 for n in nums if n in dt)
                        if hits:
                            scored.append((hits, did, dsrc, dt))
                            if hits == len(nums):
                                hits_by_src.add(dsrc)
                    scored.sort(key=lambda x: -x[0])
                    for hits, did, dsrc, dt in scored[:3]:
                        exc = excerpt(dt, nums[0])
                        row["candidates"].append({
                            "doc_id": did, "source": dsrc, "hits": f"{hits}/{len(nums)}",
                            "excerpt": exc[:180],
                            "company_near": bool(sym and sym in exc),
                            "metric_near": any(w in exc for w in METRIC_WORDS),
                            "direction_near": any(w in exc for w in DIR_WORDS),
                        })
                    # 출처 귀속
                    claimed = "factiva" if "factiva" in src.lower() else ("telegram" if "telegram" in src.lower() else None)
                    if not hits_by_src:
                        row["source_attribution"] = "미확인"
                    elif claimed in hits_by_src and len(hits_by_src) == 1:
                        row["source_attribution"] = "표기출처확인"
                    elif claimed in hits_by_src:
                        row["source_attribution"] = "복수출처(귀속불명)"
                    elif len(hits_by_src) >= 2:
                        row["source_attribution"] = "복수출처(귀속불명)"
                    else:
                        row["source_attribution"] = "타출처에서만"
                    # 기계 판정 (후보 1위 기준) — '주장지지 후보'까지만
                    if row["candidates"]:
                        c0 = row["candidates"][0]
                        row["auto"] = {"company": c0["company_near"], "metric": c0["metric_near"],
                                       "direction": c0["direction_near"]}
                        full = c0["hits"].split("/")[0] == c0["hits"].split("/")[1]
                        if full and c0["company_near"] and c0["metric_near"]:
                            row["auto_verdict"] = "주장지지후보"
                        elif full:
                            row["auto_verdict"] = "숫자일치-맥락검수필요"
                        else:
                            row["auto_verdict"] = "부분일치-검수필요"
                    else:
                        row["auto_verdict"] = "원문미발견-검수필요"
                elif row["is_tool"]:
                    row["auto_verdict"] = "도구인용-재계산검증대상"
                else:
                    row["auto_verdict"] = "정성서술-내용검수필요"
                rows.append(row)
    return rows


def main(dates):
    all_rows = []
    for d in dates:
        all_rows += audit_day(d)
    out_dir = ROOT / "evaluation" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "evidence_audit.json").write_text(json.dumps(all_rows, ensure_ascii=False, indent=1))
    from collections import Counter
    print(f"총 {len(all_rows)}건")
    print("자동 판정 분포:", dict(Counter(r["auto_verdict"] for r in all_rows)))
    print("출처 귀속 분포:", dict(Counter(r["source_attribution"] for r in all_rows if r["source_attribution"])))
    print("모델 입력 포함(숫자 기준):", dict(Counter(str(r["in_model_input"]) for r in all_rows if r["numbers"] and not r["is_tool"])))
    print(f"저장: {out_dir/'evidence_audit.json'}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["2026-05-07", "2026-05-29", "2026-06-04"])
