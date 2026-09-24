"""
선택 근거–해석 검수 시험 세트 — 개발용.

목적: 구조 B가 드러낸 **잘못된 근거 연결**을, 검수 모델이 **정상 근거를 과도하게
버리지 않고** 걸러낼 수 있는지 본다.

세 가지 규율:
1. 코드로 판정되는 것(무효 ref, has_opportunity↔action 모순)은 **넣지 않는다**.
   → evaluation/consistency_checks.py 가 따로 처리한다.
2. 라벨을 셋으로 나눈다. `경계`를 정상·오류 어느 쪽에도 섞지 않는다.
   - problem : 유효한 ID를 다른 회사·다른 문서·다른 수치의 주장에 연결 (ref_mismatch),
               또는 입력에 없는 수치·사실 (사실오류)
   - clean   : 인용과 해석이 모두 입력으로 뒷받침됨
   - 경계     : **인용한 사실은 맞지만 해석이 입력으로 지지되지 않음**(해석_불지지 등).
               rubric v2에서 문제로 세지 않는 유형이므로 오통과·오차단에 합산하지 않고
               별도로 보고한다.
3. 입력에 **주변 문맥과 복수 근거**를 함께 준다. 문장 하나만 잘라 주면 검수 모델이
   회사·기준일·단위를 확인할 방법이 없어, 실제 운용과 다른 조건이 된다.

판정에서 구분해야 할 것 (프롬프트에 명시):
  "다른 회사 문서를 근거로 그 회사 주장을 했다"  → 잘못된 연결
  "다른 회사 사건이지만 대상 종목에 대한 파급효과를 명시적으로 추론했다" → 잘못된 연결 아님
회사명이 다르다는 사실만으로 잘못된 연결로 보지 않는다.

출력: evaluation/out/review_set.json
"""
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parents[1]
OUT = ROOT / "evaluation" / "out"
DATES = ["2026-05-11", "2026-06-12", "2026-07-01"]


def load_expanded(date: str) -> dict:
    """(rep, agent, symbol, ref) → {interpretation, resolved, 같은 신호의 다른 근거}"""
    idx = {}
    for suffix in ("", "_regression"):
        p = OUT / f"structure_ab_{date}{suffix}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        for rep, agents in (d["structures"].get("B") or {}).items():
            for agent, v in agents.items():
                for sig in ((v or {}).get("expanded") or {}).get("signals", []):
                    evs = sig.get("evidences", [])
                    for e in evs:
                        key = (rep, agent, sig.get("symbol"), e.get("ref"),
                               (e.get("interpretation") or "")[:40])
                        idx[key] = {"signal": sig, "ev": e, "siblings": evs}
    return idx


def load_names() -> dict:
    """종목코드 → 종목명. 이름을 주지 않으면 검수 모델이 코드를 자기 기억으로 회사에
    연결하고, 그 기억이 틀리면 정상 근거를 차단한다(실측: 005490을 HD한국조선해양으로
    오인). 회사 귀속이 이 시험의 핵심 실패 유형이므로 이름을 입력에 넣는다."""
    import csv
    p = ROOT.parent / "data_collection" / "universe" / "ktop30.csv"
    names = {}
    if p.exists():
        with p.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                names[row["ticker"]] = row["name_kr"]
    return names


NAMES = load_names()


def build_source(entry: dict, symbol: str) -> str:
    """검수 모델에 주는 입력: 대상 종목 + 선택 근거 + 같은 신호의 다른 근거."""
    ev, sibs = entry["ev"], entry["siblings"]
    r = ev.get("resolved", {})
    meta = r.get("meta", {})
    nm = NAMES.get(symbol)
    label = f"{symbol} ({nm})" if nm else f"{symbol} (종목명 미확인 — 코드로만 판단할 것)"
    lines = [f"[대상 종목] {label}",
             f"[판단 방향] {entry['signal'].get('action', '')}",
             "",
             "[모델이 지목한 근거]",
             f"  ID: {r.get('uid')}",
             f"  출처: {r.get('source')} ({'팩터 요약 문장' if r.get('kind') == 'summary' else '도구 출력 필드'})",
             f"  내용: {r.get('quote')}"]
    if meta.get("symbol"):
        lines.append(f"  이 도구 출력이 조회한 종목: {meta['symbol']}")
    if meta.get("field"):
        lines.append(f"  필드명: {meta['field']} (단위는 내용에 표기된 그대로)")
    if meta.get("cited_doc_ids"):
        lines.append(f"  이 요약 문장이 인용한 원문 문서 번호: {meta['cited_doc_ids']}")
    others = [x for x in sibs if x is not ev]
    if others:
        lines += ["", "[같은 신호에서 함께 제시된 다른 근거]"]
        for o in others:
            orr = o.get("resolved", {})
            q = orr.get("quote")
            if not orr.get("valid") or q is None:
                q = "(존재하지 않는 ID — 이 항목은 참조 실패로 별도 처리됨)"
            lines.append(f"  - {orr.get('uid')}: {q}")
    return "\n".join(lines)


# 감사 기록에서 flags 칸이 비어 있어도 사유 문장에는 남아 있는 경우가 있다.
# flags만 보고 분류하면 그런 사례가 '정상'으로 들어가, 검수 모델이 옳게 지적해도
# 오차단으로 잘못 집계된다. 사유 문장도 함께 본다.
BORDER_KEYS = ("해석_불지지", "시점_불명확", "태그_이용가능일", "근거 밖 부가사실",
               "부가절", "비교 기준이 입력에 없", "보수 판정")


def classify(a: dict) -> str:
    if a.get("invalid_ref"):
        return "코드검사"                     # 의미 검수 대상 아님 — 제외
    if a.get("ref_mismatch"):
        return "problem"
    if a.get("문제여부"):
        return "problem"
    if a.get("flags"):
        return "경계"
    if any(k in (a.get("reason") or "") for k in BORDER_KEYS):
        return "경계"
    return "clean"


def hard_score(a: dict, src: str) -> int:
    """정상 사례의 난이도 — 헷갈릴 요소가 많을수록 높다."""
    s = 0
    if "함께 제시된 다른 근거" in src:
        s += 1
    if re.search(r"\d", a.get("evidence_head", "")):
        s += 1
    if "%" in src:
        s += 1
    if a.get("universe_excluded"):
        s += 1
    if len(src) > 700:
        s += 1
    return s


def main():
    cases, skipped, unmatched = [], Counter(), 0
    for date in DATES:
        idx = load_expanded(date)
        ad = json.loads((OUT / f"regression_audit_{date}.json").read_text())
        for struct in ("X", "Y"):
            for a in ad.get(struct, {}).get("evidence", []):
                lab = classify(a)
                if lab == "코드검사":
                    skipped["무효ref(코드검사로 분리)"] += 1
                    continue
                # 원문 문맥 복원
                hit = None
                for (rep, agent, sym, ref, head), v in idx.items():
                    if (rep == a["rep"] and agent == a["agent"] and sym == a["symbol"]
                            and ref == a["ref"]):
                        hit = v
                        break
                if hit is None:
                    unmatched += 1
                    continue
                src = build_source(hit, a["symbol"])
                cases.append({
                    "id": f"{date}-{struct}-{a['rep']}-{a['agent']}-{a['symbol']}-{a['ref']}",
                    "date": date, "symbol": a["symbol"], "ref": a["ref"],
                    "claim": hit["ev"].get("interpretation", ""),
                    "source_text": src,
                    "gold": lab,
                    "gold_reason": a.get("reason", ""),
                    "flags": a.get("flags", []),
                    "hard": hard_score(a, src),
                })

    # 같은 (주장, 근거) 중복 제거 — 반복 실행·수정 전후에서 같은 사례가 겹친다
    seen, dedup = {}, []
    for c in cases:
        k = (c["symbol"], c["ref"], c["claim"][:80])
        if k in seen:
            seen[k]["중복_사례수"] = seen[k].get("중복_사례수", 1) + 1
            continue
        seen[k] = c
        dedup.append(c)

    prob = [c for c in dedup if c["gold"] == "problem"]
    border = [c for c in dedup if c["gold"] == "경계"]
    clean = sorted([c for c in dedup if c["gold"] == "clean"],
                   key=lambda c: -c["hard"])
    # 정상 사례의 근거 종류 구성을 **문제 사례와 맞춘다**.
    # 맞추지 않으면 "S면 문제, T면 정상"이라고만 답해도 점수가 나와서,
    # 검수 모델이 내용을 읽었는지 알 수 없게 된다.
    want = Counter(c["ref"][0] for c in prob)
    clean_sel, got = [], Counter()
    for c in clean:                      # 난이도 높은 것부터
        k = c["ref"][0]
        if got[k] < want.get(k, 0):
            clean_sel.append(c)
            got[k] += 1
    종류균형 = {"문제": dict(want), "정상": dict(got),
                "미충족": {k: want[k] - got[k] for k in want if want[k] > got[k]}}

    final = prob + clean_sel + border
    meta = {
        "용도": "개발용. 이 결과로 구조 B를 본실험 기본값으로 채택하지 않는다.",
        "제외": dict(skipped),
        "원문_문맥_복원_실패": unmatched,
        "중복제거": {"수집": len(cases), "고유": len(dedup)},
        "구성": {"problem": len(prob), "clean(어려운 것 우선)": len(clean_sel),
                 "경계(해석_불지지 — 별도 보고)": len(border),
                 "clean 전체 중 선택": f"{len(clean_sel)}/{len(clean)}"},
        "근거종류_균형": 종류균형,
        "균형_이유": "정상·문제의 S/T 구성을 맞추지 않으면 근거 종류만 보고 답해도 점수가 나온다.",
        "라벨_주의": "경계는 오통과·오차단 계산에 넣지 않는다. rubric v2에서 문제로 세지 않는 유형이다.",
    }
    (OUT / "review_set.json").write_text(
        json.dumps({"meta": meta, "cases": final}, ensure_ascii=False, indent=1))
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    print(f"\n난이도 분포(clean 선택분): {dict(Counter(c['hard'] for c in clean_sel))}")
    print(f"저장: {OUT / 'review_set.json'} (총 {len(final)}건)")


if __name__ == "__main__":
    main()
