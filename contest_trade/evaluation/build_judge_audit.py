"""
판단 모델 확인 감사 세트 — 모델 선정을 다시 하는 게 아니라 **선택을 유지하기
어려울 정도의 문제가 있는지** 보는 마지막 점검.

범위(고정):
  1. 2026-05-07 — 두 모델 **전수**
  2. 나머지 두 날짜 — 모델·에이전트·반복별 **층화 무작위 표본**.
     매수·매도 근거와 기권 사유를 함께 담는다.
  3. 의심 사례(목표주가 인용·복수 출처·타사 파급효과)는 **따로** 뽑는다.
     무작위 표본의 오류율과 **섞지 않는다** — 섞으면 오류율이 부풀거나 꺾인다.
  4. 모델명을 가리고 같은 기준으로 판정한다.

⚠️ 이 감사의 판정자는 Claude다. **사람 감사가 아니라 AI 감사**다. 사람이 다시 볼
여지를 남겨 두고, 보고할 때 "사람이 확인했다"고 쓰지 않는다.

과거 오류율(29.9%·21.1%)과 **직접 잇지 않는다.** 같은 날짜라도 입력·설정·감사
기준이 달라져 전후 비교가 성립하지 않는다.

출력: evaluation/out/judge_audit_set.json  (판정용, 모델명 가림)
      /private/tmp/.../judge_audit_key.json (정답 키 — 판정 끝난 뒤에만 연다)
"""
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parents[1]
OUT = ROOT / "evaluation" / "out"
REPORTS = ROOT / "agents_workspace" / "reports"
KEYDIR = Path("/private/tmp/claude-501/-Users-hijeong-Documents-Workspace/"
              "40c6d6e7-fa63-49eb-94ae-6d142ab0ec1f/scratchpad")

FULL_DATE = "2026-05-07"
SAMPLE_PER_CELL = 3          # 모델 × 에이전트 × 반복 한 칸당 무작위 표본 수
SEED = 20260924

SIG = re.compile(r"<signal>(.*?)</signal>", re.S)
PAIR = re.compile(r"<evidence>(.*?)</evidence>\s*<time>(.*?)</time>\s*<from_source>(.*?)</from_source>", re.S)
SUSPECT = re.compile(r"목표주가|투자의견|애널리스트|컨센서스")


def _f(tag, blk):
    m = re.search(f"<{tag}>(.*?)</{tag}>", blk, re.S)
    return m.group(1).strip() if m else ""


def collect() -> list:
    d = json.loads((OUT / "judge_model_bench.json").read_text())
    items = []
    for model, mv in d["models"].items():
        for date, ags in mv["raw"].items():
            for agent, reps in ags.items():
                for r in reps:
                    for si, blk in enumerate(SIG.findall(r["raw"])):
                        abst = _f("has_opportunity", blk).lower() == "no"
                        sym = _f("symbol_code", blk)
                        act = _f("action", blk).lower()
                        for ei, (ev, t, src) in enumerate(PAIR.findall(blk)):
                            others = bool(re.search(r",", src))
                            items.append({
                                "model": model, "date": date, "agent": agent,
                                "rep": r["rep"], "signal_index": si, "ev_index": ei,
                                "symbol": sym, "action": act, "기권사유": abst,
                                "evidence": ev.strip(), "time": t.strip(),
                                "from_source": src.strip(),
                                "의심": bool(SUSPECT.search(ev)) or others,
                                "의심사유": ("목표주가·투자의견 인용" if SUSPECT.search(ev) else "")
                                            + (" 복수출처표기" if others else ""),
                            })
    return items


def main():
    rng = random.Random(SEED)
    items = collect()

    full = [x for x in items if x["date"] == FULL_DATE]
    rest = [x for x in items if x["date"] != FULL_DATE]

    # 층화: 모델 × 날짜 × 에이전트 × 반복, 매수/매도/기권사유를 고르게
    cells = defaultdict(list)
    for x in rest:
        kind = "기권" if x["기권사유"] else x["action"] or "기타"
        cells[(x["model"], x["date"], x["agent"], x["rep"], kind)].append(x)
    sample = []
    for k in sorted(cells):
        pool = cells[k]
        rng.shuffle(pool)
        sample += pool[:SAMPLE_PER_CELL]

    # 의심 사례는 별도 — 무작위 표본과 섞지 않는다.
    picked = {id(x) for x in full} | {id(x) for x in sample}
    suspect = [x for x in rest if x["의심"] and id(x) not in picked]

    def emit(rows, group):
        out = []
        for i, x in enumerate(rows):
            rp = json.loads((REPORTS / x["agent"] / f"{x['date']}_08-30-00.json").read_text())
            out.append({
                "audit_id": f"{group}-{i:03d}",
                "group": group,
                "date": x["date"], "symbol": x["symbol"], "action": x["action"],
                "기권사유근거": x["기권사유"],
                "evidence": x["evidence"], "time": x["time"],
                "from_source": x["from_source"],
                "의심사유": x["의심사유"] or None,
                "고정입력_길이": len(rp["background_information"]) + len(rp["tool_call_context"]),
            })
        return out

    blind = emit(full, "전수") + emit(sample, "무작위표본") + emit(suspect, "의심별도")
    rng.shuffle(blind)
    key = {}
    for row in blind:
        src = next(x for x in items
                   if x["evidence"] == row["evidence"] and x["date"] == row["date"])
        key[row["audit_id"]] = {"model": src["model"], "agent": src["agent"],
                                "rep": src["rep"], "group": row["group"]}

    meta = {
        "용도": "GPT-4.1 선택을 유지하기 어려운 수준의 문제가 있는지 보는 확인 감사. "
                "모델 선정을 다시 하는 것이 아니다.",
        "범위": {"전수": f"{FULL_DATE} 두 모델 전부 ({len(full)}건)",
                 "무작위표본": f"나머지 2일, 모델×에이전트×반복×(매수/매도/기권) 칸당 "
                               f"최대 {SAMPLE_PER_CELL}건 ({len(sample)}건)",
                 "의심별도": f"목표주가 인용·복수 출처 ({len(suspect)}건) — "
                             f"**무작위 표본 오류율에 합산하지 않는다**"},
        "블라인드": "모델명·에이전트·반복을 감춘 채 판정한다. 키는 판정 후에만 연다.",
        "과거수치와의_관계": "29.9%·21.1%와 직접 잇지 않는다. 같은 날짜라도 입력·설정·"
                             "감사 기준이 달라져 전후 비교가 성립하지 않는다.",
        "판정축": ["회사 귀속", "날짜", "단위", "주장 지지 여부"],
        "채택판단_기준": "텔레그램 인용량이 아니라 **추가로 쓴 정보를 정확히 해석했는가**. "
                         "중대한 왜곡이 반복되거나 기권 사유가 틀린 사실에 의존하면 재검토.",
        "seed": SEED,
    }
    (OUT / "judge_audit_set.json").write_text(
        json.dumps({"meta": meta, "cases": blind}, ensure_ascii=False, indent=1))
    KEYDIR.mkdir(parents=True, exist_ok=True)
    (KEYDIR / "judge_audit_key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1))

    print(json.dumps(meta["범위"], ensure_ascii=False, indent=1))
    print(f"총 {len(blind)}건 → {OUT / 'judge_audit_set.json'}")
    print(f"키(판정 후 개봉) → {KEYDIR / 'judge_audit_key.json'}")


if __name__ == "__main__":
    main()
