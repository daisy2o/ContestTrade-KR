"""
주평가 — 콘테스트 가중치로 **집계한 상승 확률**의 일반 Brier score.

식:
    p̂^(c)_{d,s} = Σ_a w^(c)_{a,d,s} · p_{a,d,s}      (w ≥ 0, Σ_a w = 1)
    y_{d,s}     = 1[ O_{d+1,s} > O_{d,s} ]
    BS_c        = (1/|D|) Σ_d (1/|U_d|) Σ_{s∈U_d} ( p̂^(c)_{d,s} − y_{d,s} )²

**날짜별로 먼저 종목 평균을 내고, 날짜에 동일 가중으로 평균한다.**
신호가 많은 날이 결과를 끌고 가지 않게 하기 위함이다.

⚠️ **오차 항에 경쟁 가중치를 다시 곱하지 않는다.** 가중치는 확률을 합치는 데만 쓴다.
실현 수익률이 큰 사례에 사후 가중치를 붙이지도 않는다 — 그것은 다른 평가 질문이고
확률 점수의 성질(적정성)도 달라진다.

주 비교: **BS(C3) − BS(C2)**. 음수면 콘테스트가 더 좋다.

확률의 의미:
    p_{a,d,s} = P(다음 거래일 시가 > 당일 시가)  — 출력의 `<p_up>`

    ⚠️ **같은 사건을 두 번 추정하게 하지 않는다.** buy의 '선택 방향 적중 확률'은
    정의상 p_up과 **같은 사건**이므로 두 값이 다르면 그것은 분리가 아니라
    **의미 불일치**다(실측으로 67 vs 62가 나왔고, 이는 결함이다).
    새 출력은 `<p_up>`(엄격 상승)과 `<p_down>`(엄격 하락)만 받고,
    방향 적중 확률은 **시스템이 유도**한다: buy → p_up, sell → p_down.

    두 값은 여집합이 아니다. 나머지 1−p_up−p_down 이 보합이므로
    **p_up + p_down ≤ 1** 을 검사한다. 넘으면 그 신호는 확률 결함으로 기록한다.

미제출·기권 처리:
    U_d 는 **출력을 보기 전에 정한 공통 평가 종목군**이다.
    제출하지 않았거나 정상 기권한 종목은 **0.5**로 둔다. 이는 에이전트가 50%라고
    판단했다는 뜻이 아니라 **예측을 제출하지 않았을 때의 공통 처리 규칙**이다.
    형식·실행 실패는 기권과 **별도로 기록**하되, 그 때문에 조건마다 평가 종목을
    다르게 빼지 않는다(빼면 조건 간 분모가 달라져 비교가 깨진다).

출력: evaluation/out/brier_eval.json
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from itertools import permutations
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "evaluation" / "out"
SIG = re.compile(r"<signal>(.*?)</signal>", re.S)
NEUTRAL = 0.5


def _f(tag: str, blk: str) -> str:
    m = re.search(f"<{tag}>(.*?)</{tag}>", blk, re.S)
    return m.group(1).strip() if m else ""


def _pct(v: str):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x / 100.0 if x > 1.0 else x


def classify(blk: str) -> str:
    ho = _f("has_opportunity", blk).lower()
    act = _f("action", blk).lower()
    prob = _f("probability", blk)
    if act not in {"buy", "sell", "neutral", ""}:
        return "형식실패"
    if ho == "no":
        return "형식실패" if (act in ("buy", "sell") or (prob and prob not in ("0", "0.0"))) else "기권"
    return "정상신호" if act in ("buy", "sell") else "형식실패"


def agent_probs(raw: str, universe: list) -> tuple:
    """한 실행 → {종목: p_up}. 제출하지 않은 종목은 넣지 않는다(호출부가 0.5로 채운다).

    실패 종류를 **기권과 섞지 않고** 따로 센다:
      · p_up_누락      : 필수 확률이 없음 → 실행 실패로 기록, 0.5 기본값 적용
      · 확률합_초과    : p_up + p_down > 1 → 확률 결함으로 기록(값은 살려 쓰되 표시)
    """
    out, kinds = {}, Counter()
    for blk in SIG.findall(raw or ""):
        kind = classify(blk)
        kinds[kind] += 1
        if kind != "정상신호":
            continue
        sym = _f("symbol_code", blk)
        if sym not in universe:
            kinds["유니버스밖"] += 1
            continue
        pu, pd = _pct(_f("p_up", blk)), _pct(_f("p_down", blk))
        if pu is None:
            kinds["p_up_누락(실행실패)"] += 1
            continue
        if pd is not None and pu + pd > 1.0 + 1e-9:
            kinds["확률합_초과"] += 1
        # 파생: 방향 적중 확률은 모델이 아니라 시스템이 만든다
        act = _f("action", blk).lower()
        derived = pu if act == "buy" else (pd if pd is not None else None)
        if derived is not None:
            kinds["방향적중확률_유도됨"] += 1
        out[sym] = min(max(pu, 0.0), 1.0)
    return out, kinds, kinds.get("p_up_누락(실행실패)", 0)


def combine(weights: dict, per_agent: dict, universe: list) -> dict:
    """p̂_s = Σ_a w_a · p_{a,s}. 가중치는 합이 1이어야 한다."""
    tot = sum(weights.values())
    if abs(tot - 1.0) > 1e-6:
        raise ValueError(f"가중치 합이 1이 아니다: {tot}")
    out = {}
    for s in universe:
        out[s] = sum(w * per_agent.get(a, {}).get(s, NEUTRAL) for a, w in weights.items())
    return out


def brier(preds_by_date: dict, truth: dict) -> float | None:
    """날짜별 종목 평균 → 날짜 동일 가중 평균."""
    per_day = []
    for d, preds in preds_by_date.items():
        ys = truth.get(d, {})
        vals = [(p - ys[s]) ** 2 for s, p in preds.items() if s in ys]
        if vals:
            per_day.append(sum(vals) / len(vals))
    return sum(per_day) / len(per_day) if per_day else None


def shuffle_assignments(weights: dict) -> list:
    """C4 — 가중치 **값**을 보존하고 에이전트 배정만 바꾼 모든 경우(항등 포함).

    에이전트 하나가 가진 여러 종목 예측은 **한 묶음으로 함께 이동**한다.
    종목마다 따로 섞으면 에이전트 단위 경쟁과 다른 대조가 된다.
    """
    agents = sorted(weights)
    vals = [weights[a] for a in agents]
    return [dict(zip(perm, vals)) for perm in permutations(agents)]


def evaluate(runs: dict, weights_by_date: dict, universe_by_date: dict,
             truth: dict) -> dict:
    """runs: {date: {agent: raw}}  weights_by_date: {date: {agent: w}} (C3용)"""
    agents = sorted({a for v in runs.values() for a in v})
    conds = {f"C1_{a}": "single" for a in agents}
    conds.update({"C2_동일가중": "equal", "C3_콘테스트": "contest", "C4_셔플": "shuffle"})

    per_agent_by_date, diag = {}, defaultdict(Counter)
    for d, per in runs.items():
        u = universe_by_date[d]
        pa = {}
        for a, raw in per.items():
            probs, kinds, miss = agent_probs(raw, u)
            pa[a] = probs
            for k, n in kinds.items():
                diag[d][k] += n
            diag[d]["미제출_종목"] += len(u) - len(probs)
        per_agent_by_date[d] = pa

    results = {}
    for name, kind in conds.items():
        preds = {}
        for d, pa in per_agent_by_date.items():
            u = universe_by_date[d]
            if kind == "single":
                a = name.split("_", 1)[1]
                w = {x: (1.0 if x == a else 0.0) for x in agents}
            elif kind == "equal":
                w = {x: 1.0 / len(agents) for x in agents}
            elif kind == "contest":
                w = weights_by_date.get(d)
                if not w:
                    w = {x: 1.0 / len(agents) for x in agents}   # 이력 부족 → 동일가중
            else:
                base = weights_by_date.get(d) or {x: 1.0 / len(agents) for x in agents}
                perms = shuffle_assignments(base)
                ps = [combine(pp, pa, u) for pp in perms]
                preds[d] = {s: sum(p[s] for p in ps) / len(ps) for s in u}
                continue
            preds[d] = combine(w, pa, u)
        results[name] = {"BS": brier(preds, truth), "날짜수": len(preds)}

    c2, c3 = results.get("C2_동일가중", {}).get("BS"), results.get("C3_콘테스트", {}).get("BS")
    return {
        "설계": {
            "주평가": "콘테스트 가중치로 집계한 상승 확률의 **일반** Brier score. "
                      "오차 항에 경쟁 가중치를 다시 곱하지 않는다.",
            "집계": "날짜별 종목 평균 → 날짜 동일 가중 평균",
            "확률": "<p_up> = P(다음 거래일 시가 > 당일 시가). <probability>(방향 적중 "
                    "확률)는 환산해 쓰지 않는다 — sell에서 1-p는 '상승 또는 보합'이 된다.",
            "미제출·기권": f"공통 종목군 U_d에서 미제출·정상기권은 {NEUTRAL}로 처리. "
                            "에이전트가 50%라고 판단했다는 뜻이 아니라 공통 처리 규칙이다.",
            "형식·실행 실패": "기권과 별도 기록. 그 때문에 조건마다 평가 종목을 빼지 않는다.",
            "C4": "저장된 가중치의 에이전트 배정 3!=6가지(항등 포함) 전부 계산해 평균. "
                  "에이전트의 여러 종목 예측은 한 묶음으로 함께 이동.",
        },
        "결과": results,
        "주_비교": {"BS(C3)-BS(C2)": (None if c2 is None or c3 is None else round(c3 - c2, 6)),
                    "해석": "음수면 콘테스트가 더 좋다"},
        "진단": {d: dict(v) for d, v in diag.items()},
    }


if __name__ == "__main__":
    print(__doc__)
