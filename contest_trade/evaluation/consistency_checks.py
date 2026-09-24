"""
코드로 판정 가능한 형식·참조 검사 — 의미 검수에 맡기지 않는다.

두 가지는 모델 판단이 필요 없다:
1. **존재하지 않는 ref**: 참조 실패로 기록하고 정상 근거로 승인하지 않는다.
   프로그램이 비슷한 ID로 임의 연결하지도, 조용히 버리지도 않는다.
2. **has_opportunity와 action의 모순**: `no`인데 `action=buy`와 확률·근거를 함께
   출력하면 형식 실패로 분리한다. 임의로 매수나 기권 중 하나로 해석하지 않는다.

여기서 거른 뒤 남는 것 — **유효한 ID를 엉뚱한 주장에 연결한 경우** — 이 의미 검수의
대상이다. 이 구분을 해야 검수 모델에 코드가 할 일을 떠넘기지 않는다.

사용: python -m evaluation.consistency_checks 2026-05-11 2026-06-12 2026-07-01
출력: evaluation/out/consistency_<날짜>.json
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
OUT = ROOT / "evaluation" / "out"

SIG_RE = re.compile(r"<signal>(.*?)</signal>", re.S)
REF_RE = re.compile(r"<ref>(.*?)</ref>\s*<interpretation>(.*?)</interpretation>", re.S)
EV_RE = re.compile(r"<evidence>(.*?)</evidence>", re.S)


def _field(tag: str, blk: str) -> str:
    m = re.search(f"<{tag}>(.*?)</{tag}>", blk, re.S)
    return m.group(1).strip() if m else ""


def check_signal_consistency(raw: str, structure: str) -> list:
    """has_opportunity ↔ action ↔ 근거·확률의 일관성. 형식 실패를 분리한다."""
    out = []
    for i, blk in enumerate(SIG_RE.findall(raw or "")):
        ho = _field("has_opportunity", blk).lower()
        act = _field("action", blk).lower()
        prob = _field("probability", blk)
        n_ev = len(REF_RE.findall(blk)) if structure == "B" else len(EV_RE.findall(blk))
        problems = []
        if ho == "no" and act in ("buy", "sell"):
            problems.append(f"has_opportunity=no인데 action={act}")
        if ho == "no" and n_ev > 0:
            problems.append(f"has_opportunity=no인데 근거 {n_ev}건 제시")
        if ho == "no" and prob and prob not in ("0", "0.0"):
            problems.append(f"has_opportunity=no인데 probability={prob}")
        if ho == "yes" and n_ev == 0:
            problems.append("has_opportunity=yes인데 근거 0건")
        if problems:
            out.append({"signal_index": i, "has_opportunity": ho, "action": act,
                        "probability": prob, "n_evidence": n_ev,
                        "형식실패": problems,
                        "처리": "형식 실패로 분리 — 매수/기권 어느 쪽으로도 해석하지 않음",
                        "symbol": _field("symbol_code", blk)})
    return out


def check_refs(expanded: dict) -> dict:
    """무효 ref를 참조 실패로 집계 (자동 교정 없음)."""
    invalid, valid = [], 0
    for sig in (expanded or {}).get("signals", []):
        for e in sig.get("evidences", []):
            r = e.get("resolved", {})
            if r.get("valid"):
                valid += 1
            else:
                invalid.append({"ref": e.get("ref"), "사유": r.get("error", ""),
                                "symbol": sig.get("symbol"),
                                "interpretation_head": (e.get("interpretation") or "")[:60]})
    return {"유효_ref": valid, "무효_ref": len(invalid), "무효_상세": invalid}


def main(dates: list):
    for date in dates:
        res = {"date": date, "structures": {}}
        for suffix, label in (("", "수정전"), ("_regression", "수정후")):
            p = OUT / f"structure_ab_{date}{suffix}.json"
            if not p.exists():
                continue
            d = json.loads(p.read_text())
            for st in ("A", "B"):
                key = f"{st}_{label}" if st == "B" else "A"
                if key in res["structures"]:
                    continue
                sig_fail, ref_stat = [], {"유효_ref": 0, "무효_ref": 0, "무효_상세": []}
                for rep, agents in (d["structures"].get(st) or {}).items():
                    for agent, v in agents.items():
                        raw = v.get("raw", "") if isinstance(v, dict) else ""
                        for f in check_signal_consistency(raw, st):
                            sig_fail.append({**f, "rep": rep, "agent": agent})
                        if st == "B" and isinstance(v, dict) and v.get("expanded"):
                            r = check_refs(v["expanded"])
                            ref_stat["유효_ref"] += r["유효_ref"]
                            ref_stat["무효_ref"] += r["무효_ref"]
                            for x in r["무효_상세"]:
                                ref_stat["무효_상세"].append({**x, "rep": rep, "agent": agent})
                res["structures"][key] = {"형식실패_신호": sig_fail, "참조": ref_stat}
        (OUT / f"consistency_{date}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
        print(f"\n=== {date} ===")
        for k, v in res["structures"].items():
            print(f"  {k:12s} 형식실패 신호 {len(v['형식실패_신호']):2d}건 | "
                  f"무효 ref {v['참조']['무효_ref']}건 / 유효 {v['참조']['유효_ref']}건")
            for f in v["형식실패_신호"][:3]:
                print(f"       - {f['rep']}/{f['agent']} {f['symbol']}: {'; '.join(f['형식실패'])}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["2026-05-11", "2026-06-12", "2026-07-01"])
