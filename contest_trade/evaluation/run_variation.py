"""
동일 조건 재실행 변동 측정 — 포함/제외 차이를 해석하기 전에 먼저 본다.

포함·제외 차이가 작을 때, 그 차이가 **평소 실행 변동보다 큰지** 모르면 아무 말도
할 수 없다. 새로 돌리지 않고 이미 있는 출력을 쓴다.

동일 조건 4회 = structure_ab의 구조 A 2회 + telegram_ablation 포함 조건 2회.
넷 다 같은 날짜·같은 고정 입력(텔레그램 포함)·같은 모델·구조 A다.
(`structure_ab_<날짜>_regression.json`의 A는 재사용본이라 독립 반복이 아니다 — 제외.)

한계: 두 출처의 실행 시점이 다르고 그 사이 프롬프트 가드가 바뀌었을 수 있다.
따라서 이 4회는 '완전히 동일한 조건의 4회'가 아니라 **'같은 조건으로 의도된 4회'**다.
변동이 크게 나오면 그 자체가 상한 추정으로 쓸 만하고, 작게 나오면 해석에 주의한다.

출력: evaluation/out/run_variation.json
"""
import json
import re
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).parents[1]
OUT = ROOT / "evaluation" / "out"
SIG_RE = re.compile(r"<signal>(.*?)</signal>", re.S)


def _f(tag, blk):
    m = re.search(f"<{tag}>(.*?)</{tag}>", blk, re.S)
    return m.group(1).strip() if m else ""


def picks(raw: str) -> set:
    """한 실행이 내놓은 (종목, 방향) 집합."""
    out = set()
    for blk in SIG_RE.findall(raw or ""):
        if _f("has_opportunity", blk).lower() == "no":
            continue
        s = _f("symbol_code", blk)
        if s:
            out.add((s, _f("action", blk).lower()))
    return out


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def collect(date: str) -> dict:
    """에이전트별로 같은 조건의 실행들을 모은다."""
    runs = {}
    p = OUT / f"structure_ab_{date}.json"
    if p.exists():
        for rep, agents in (json.loads(p.read_text())["structures"].get("A") or {}).items():
            for agent, v in agents.items():
                runs.setdefault(agent, []).append(
                    (f"structure_ab/{rep}", picks(v.get("raw", "") if isinstance(v, dict) else v)))
    p = OUT / f"telegram_ablation_{date}.json"
    if p.exists():
        d = json.loads(p.read_text())
        for agent, reps in d["raw"]["텔레그램_포함"].items():
            for r in reps:
                runs.setdefault(agent, []).append(
                    (f"ablation_on/rep{r['rep']}", picks(r["raw"])))
        off = {agent: [(f"ablation_off/rep{r['rep']}", picks(r["raw"])) for r in reps]
               for agent, reps in d["raw"]["텔레그램_제외"].items()}
    else:
        off = {}
    return runs, off


def main(dates):
    res = {}
    print(f"{'날짜':<12}{'에이전트':<10}{'동일조건 쌍별 일치도':<22}{'포함vs제외 일치도':<20}판정")
    for date in dates:
        on, off = collect(date)
        res[date] = {}
        for agent in sorted(on):
            same = [jaccard(a[1], b[1]) for a, b in combinations(on[agent], 2)]
            cross = [jaccard(a[1], b[1]) for a in on[agent] for b in off.get(agent, [])]
            if not same or not cross:
                continue
            s_avg, c_avg = sum(same) / len(same), sum(cross) / len(cross)
            # 포함↔제외 일치도가 동일조건 일치도보다 낮아야 '차이가 변동을 넘는다'
            verdict = ("차이가 변동 범위 안" if c_avg >= min(same)
                       else "차이가 변동보다 큼 — 검토 대상")
            res[date][agent] = {
                "동일조건_쌍": len(same), "동일조건_일치도_평균": round(s_avg, 3),
                "동일조건_일치도_최소": round(min(same), 3),
                "포함vs제외_일치도_평균": round(c_avg, 3),
                "판정": verdict,
                "실행_목록": [n for n, _ in on[agent]],
            }
            print(f"{date:<12}{agent:<10}"
                  f"평균 {s_avg:.2f} (최소 {min(same):.2f}, {len(same)}쌍)  "
                  f"평균 {c_avg:.2f}        {verdict}")
    (OUT / "run_variation.json").write_text(json.dumps({
        "설계": "동일 조건 = 같은 날짜·같은 고정 입력(텔레그램 포함)·같은 모델·구조 A. "
                "structure_ab의 A 2회 + telegram_ablation 포함 2회를 모았다. "
                "structure_ab_*_regression.json의 A는 재사용본이라 제외했다.",
        "지표": "Jaccard — 한 실행이 고른 (종목, 방향) 집합끼리의 일치도. 1이면 완전 동일.",
        "판정규칙": "포함vs제외 평균 일치도가 동일조건 최소 일치도보다 낮을 때만 "
                    "'차이가 변동보다 크다'고 본다. 그 외에는 실행 변동과 구분되지 않는다.",
        "한계": "두 출처의 실행 시점이 다르고 그 사이 프롬프트가 바뀌었을 수 있어 "
                "'같은 조건으로 의도된 4회'다. 쌍 수가 적어 정밀한 추정이 아니다.",
        "결과": res}, ensure_ascii=False, indent=1))
    print(f"\n저장: {OUT / 'run_variation.json'}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["2026-05-11", "2026-06-12", "2026-07-01"])
