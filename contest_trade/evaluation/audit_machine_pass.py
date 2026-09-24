"""
확인 감사 1단계 — 기계로 판정 가능한 축만 먼저 거른다.

기계 판정: 수치 실재 / 날짜 실재·as-of / 단위 환산.
사람 판정: 회사 귀속(파급효과 추론과 구분 필요), 주장 지지 여부.
둘을 섞지 않는다. 기계가 못 보는 것을 통과로 세면 감사가 헐거워진다.

수치 비교는 만/억/조를 확장한 뒤 한다. 확장하지 않으면 정확히 옮긴 수치가
'입력에 없음'으로 잡힌다(실측 확인).

출력: evaluation/out/judge_audit_machine.json
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.judge_model_bench import (NUM, expand_units, norm,  # noqa: E402
                                          significant)

OUT = ROOT / "evaluation" / "out"
REPORTS = ROOT / "agents_workspace" / "reports"
DATE_RE = re.compile(r"20\d\d[-년]\s?\d{1,2}[-월]\s?\d{1,2}")


def fixed_input(date: str) -> str:
    """같은 날짜의 세 에이전트 입력을 합친다. belief 외에는 동일하고,
    감사 시점에 어느 에이전트인지 가려져 있으므로 합집합으로 대조한다."""
    parts = []
    for p in sorted(REPORTS.rglob(f"{date}_08-30-00.json")):
        d = json.loads(p.read_text())
        parts.append(d["background_information"] + d.get("tool_call_context", ""))
    return "\n".join(parts)


def main():
    data = json.loads((OUT / "judge_audit_set.json").read_text())
    cache = {}
    rows, tally = [], Counter()
    for c in data["cases"]:
        date = c["date"]
        if date not in cache:
            hay = fixed_input(date)
            cache[date] = (hay, norm(NUM.findall(hay)) | expand_units(hay))
        hay, haynums = cache[date]

        ev = c["evidence"]
        미발견 = sorted((norm(significant(ev)) | expand_units(ev)) - haynums)
        날짜문제 = []
        for dd in DATE_RE.findall(ev):
            nd = re.sub(r"[년월]", "-", dd).replace(" ", "").rstrip("-")
            ps = [p for p in nd.split("-") if p]
            if len(ps) == 3:
                iso = f"{ps[0]}-{int(ps[1]):02d}-{int(ps[2]):02d}"
                if iso > date:
                    날짜문제.append(f"{iso} (as-of {date} 이후)")
                elif iso not in hay and iso.replace("-0", "-") not in hay:
                    날짜문제.append(f"{iso} (입력에 없음)")
        v = "기계통과" if not 미발견 and not 날짜문제 else "사람확인필요"
        tally[v] += 1
        if 미발견:
            tally["수치_미발견"] += 1
        if 날짜문제:
            tally["날짜_문제"] += 1
        rows.append({**c, "기계판정": v, "수치_미발견": 미발견, "날짜_문제": 날짜문제})

    (OUT / "judge_audit_machine.json").write_text(json.dumps(
        {"meta": {**data["meta"],
                  "1단계": "기계 판정 축 = 수치 실재·날짜 실재/as-of·단위 환산. "
                           "회사 귀속과 주장 지지는 여기서 판정하지 않는다 — "
                           "'기계통과'는 '오류 없음'이 아니다.",
                  "집계": dict(tally)},
         "cases": rows}, ensure_ascii=False, indent=1))
    print(json.dumps(dict(tally), ensure_ascii=False, indent=1))
    print(f"\n사람 확인 필요 {tally['사람확인필요']}건:")
    for r in rows:
        if r["기계판정"] == "사람확인필요":
            print(f"  [{r['audit_id']}] {r['group']} {r['date']} {r['symbol']} "
                  f"수치{r['수치_미발견']} 날짜{r['날짜_문제']}")
            print(f"     {r['evidence'][:150]}")
    print(f"\n저장: {OUT / 'judge_audit_machine.json'}")


if __name__ == "__main__":
    main()
