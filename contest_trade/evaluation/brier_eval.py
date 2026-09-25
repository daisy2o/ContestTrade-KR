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
        preds, perm_preds = {}, {}
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
                # ⚠️ C4는 '확률을 평균한 뒤 채점'이 아니라 **'각 순열을 채점한 뒤 평균'**이다.
                #    mean_π(p_π − y)² = (mean_π p_π − y)² + Var_π(p_π)
                #                     = BS(C2)          + 순열 간 분산
                #    앞의 식(평균 예측의 Brier)은 C2와 항등이라 대조가 되지 않는다.
                #    필요한 것은 '가중치 배정을 무작위로 했을 때 기대되는 손실'이다.
                base = weights_by_date.get(d) or {x: 1.0 / len(agents) for x in agents}
                perm_preds[d] = [combine(pp, pa, u) for pp in shuffle_assignments(base)]
                continue
            preds[d] = combine(w, pa, u)
        if kind == "shuffle":
            # 순열별로 채점한 뒤 평균 — 배정을 무작위로 했을 때의 기대 손실
            per_perm = []
            n_perm = len(next(iter(perm_preds.values()))) if perm_preds else 0
            for i in range(n_perm):
                per_perm.append(brier({d: v[i] for d, v in perm_preds.items()}, truth))
            vals = [b for b in per_perm if b is not None]
            results[name] = {
                "BS": (sum(vals) / len(vals)) if vals else None,
                "날짜수": len(perm_preds), "순열수": n_perm,
                "순열별_BS": [round(b, 6) for b in vals],
                "정의": "mean_π (p_π − y)²  = BS(C2) + Var_π(p_π). "
                        "'확률을 평균한 뒤 채점'(= C2와 항등)이 아니다.",
            }
        else:
            results[name] = {"BS": brier(preds, truth), "날짜수": len(preds)}

    # ── 0.5 기준선과 제출 범위 ──────────────────────────────────────────
    # 미제출이 대부분이면 점수의 상당 부분이 0.5 기본값에서 나온다.
    # 전체 Brier만 보면 '실제 예측이 얼마나 포함된 점수인지' 알 수 없다.
    base_preds = {d: {s: NEUTRAL for s in universe_by_date[d]} for d in per_agent_by_date}
    baseline = brier(base_preds, truth)

    submitted = {a: 0 for a in agents}
    union_by_date, n_slots = {}, 0
    for d, pa in per_agent_by_date.items():
        u = universe_by_date[d]
        n_slots += len(u) * len(agents)
        un = set()
        for a in agents:
            submitted[a] += len(pa.get(a, {}))
            un |= set(pa.get(a, {}))
        union_by_date[d] = sorted(un)

    # 공통 부분집합(하나 이상의 에이전트가 제출한 종목)에서의 조건별 점수 — **보조**
    sub_results = {}
    for name, kind in conds.items():
        preds = {}
        for d, pa in per_agent_by_date.items():
            u = union_by_date[d]
            if not u:
                continue
            if kind == "single":
                a = name.split("_", 1)[1]
                w = {x: (1.0 if x == a else 0.0) for x in agents}
            elif kind == "equal":
                w = {x: 1.0 / len(agents) for x in agents}
            elif kind == "contest":
                w = weights_by_date.get(d) or {x: 1.0 / len(agents) for x in agents}
            else:
                base = weights_by_date.get(d) or {x: 1.0 / len(agents) for x in agents}
                ps = [combine(pp, pa, u) for pp in shuffle_assignments(base)]
                bs = [brier({d: q}, truth) for q in ps]
                bs = [b for b in bs if b is not None]
                preds[d] = None
                sub_results.setdefault(name, []).append(sum(bs) / len(bs) if bs else None)
                continue
            preds[d] = combine(w, pa, u)
        if kind != "shuffle":
            sub_results[name] = brier(preds, truth)
    for k, v in list(sub_results.items()):
        if isinstance(v, list):
            vv = [x for x in v if x is not None]
            sub_results[k] = sum(vv) / len(vv) if vv else None

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
            "C4": "저장된 가중치의 에이전트 배정 3!=6가지(항등 포함)를 **각각 채점한 뒤 "
                  "평균**한다 — mean_π(p_π−y)² = BS(C2) + Var_π(p_π). "
                  "확률을 먼저 평균하면 C2와 항등이 되어 대조가 되지 않는다. "
                  "에이전트의 여러 종목 예측은 한 묶음으로 함께 이동.",
        },
        "결과": results,
        "0.5_기준선": {
            "모든 종목에 항상 0.5": round(baseline, 6) if baseline is not None else None,
            "의미": "U_d 전체에 중립값만 낸 경우의 Brier. 미제출이 많을수록 조건별 "
                    "점수가 이 값에 가까워진다 — 실제 예측이 얼마나 반영된 점수인지 "
                    "판단하려면 반드시 함께 본다.",
            "⚠️ 단위 주의": "최종 평가 단위는 **(날짜, 종목) 조합**이지 "
                            "(날짜, 종목, 에이전트) 슬롯이 아니다. '에이전트 슬롯의 "
                            "87.5%가 미제출'과 '점수의 87.5%가 기본값'은 다른 말이다. "
                            "전원이 미제출한 조합만 최종 확률이 정확히 0.5가 되고, "
                            "일부만 제출한 조합에는 기권의 영향이 섞인다.",
        },
        "제출_범위": {
            "에이전트별_제출_종목수": submitted,
            "하나 이상 제출된 종목수(날짜별)": {d: len(v) for d, v in union_by_date.items()},
            "최종 평가 단위(날짜,종목) 총수": sum(len(universe_by_date[d]) for d in union_by_date),
            "그중 전원 미제출(확률 정확히 0.5)": (
                sum(len(universe_by_date[d]) - len(v) for d, v in union_by_date.items())),
            "전체 슬롯(종목×에이전트×날짜)": n_slots,
            "제출 슬롯": sum(submitted.values()),
        },
        "보조_공통부분집합_점수": {
            "설명": "하나 이상의 에이전트가 제출한 종목만으로 다시 계산한 값. "
                    "**보조 분석이며 주평가 분모를 바꾸지 않는다.**",
            "점수": {k: (round(v, 6) if v is not None else None) for k, v in sub_results.items()},
        },
        "주_비교": {"BS(C3)-BS(C2)": (None if c2 is None or c3 is None else round(c3 - c2, 6)),
                    "해석": "음수면 콘테스트가 더 좋다"},
        "진단": {d: dict(v) for d, v in diag.items()},
    }


if __name__ == "__main__":
    print(__doc__)
