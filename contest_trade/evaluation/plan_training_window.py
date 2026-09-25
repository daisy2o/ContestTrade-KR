"""
최소 학습 준비 구간 산정 — LLM 호출 없이 날짜·가용 자료만으로 계산.

먼저 '연속 8개 항목'이 **무엇의 연속성인지** 코드에서 확정한다.

`train_lightgbm_model`의 창 구성(research_predictor.py):

    for i in range(H, len(rewards) - P + 1):
        history_window       = rewards[i-H : i]       # H=5
        history_judge_scores = judge_scores_list[i-1] # 창의 '앵커' 위치
        future_rewards       = rewards[i : i+P]       # P=3

  · `rewards`/`judge_scores_list`는 **에이전트별 리스트**이고, 각 항목은
    `load_historical_signals`가 준 **거래일 하나**에 대응한다.
    → 연속성은 **거래일의 연속**이며, 에이전트마다 따로 센다.
  · 창이 만들어지는 조건 셋:
      ① 앵커 위치(i-1)에 **judge 점수**가 있어야 한다
      ② 이력 5칸 중 **유효 보상이 2개 이상**
      ③ 미래 3칸에서 샤프 계산 가능(**유효 보상 1개 이상**)

기권의 영향:
  · 정상 기권일은 보상이 **미정의(None)** 다 — 임의로 0을 넣지 않는다.
    (0을 넣으면 '맞히지도 틀리지도 않은 날'을 '수익 0의 예측'으로 위조한다.)
  · 기권일은 ②의 유효 보상에 기여하지 못하고, **앵커로 쓰일 수도 없다**
    (judge 점수가 없으므로 ①에서 탈락). 즉 **창을 끊는다**.

출력: evaluation/out/training_window_plan.json
"""
from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "evaluation" / "out"
REPORTS = ROOT / "agents_workspace" / "reports"
H, P = 5, 3
MIN_SAMPLES = 30


def observed_state() -> dict:
    """보유 날짜별·에이전트별 상태: 정상신호 / 기권 / 없음."""
    state = {}
    for p in sorted(REPORTS.glob("agent_*/*_08-30-00.json")):
        date = p.name.split("_")[0]
        agent = p.parent.name
        fr = json.loads(p.read_text()).get("final_result", "") or ""
        hos = re.findall(r"<has_opportunity>(.*?)</has_opportunity>", fr, re.S)
        if not hos:
            st = "기권(빈 제출)"
        elif any(h.strip().lower() == "yes" for h in hos):
            st = "정상신호"
        else:
            st = "기권"
        state.setdefault(date, {})[agent] = st
    return state


def samples_from(seq: list) -> int:
    """연속 거래일 시퀀스(각 항목 = '정상신호' 여부)에서 만들 수 있는 창 수."""
    n = 0
    for i in range(H, len(seq) - P + 1):
        if not seq[i - 1]:                       # ① 앵커에 judge 점수 필요
            continue
        if sum(seq[i - H:i]) < 2:                # ② 이력 유효 보상 ≥2
            continue
        if sum(seq[i:i + P]) < 1:                # ③ 미래 유효 보상 ≥1
            continue
        n += 1
    return n


def main():
    state = observed_state()
    dates = sorted(state)
    agents = sorted({a for v in state.values() for a in v})

    cnt = Counter()
    for d in dates:
        for a in agents:
            cnt[state[d].get(a, "없음")] += 1
    total = sum(cnt.values())
    p_signal = cnt["정상신호"] / total if total else 0

    cur = {}
    for a in agents:
        seq = [state[d].get(a) == "정상신호" for d in dates]
        cur[a] = samples_from(seq)

    ideal_n = None
    for n in range(H + P, 200):
        if len(agents) * samples_from([True] * n) >= MIN_SAMPLES:
            ideal_n = n
            break

    rng = random.Random(20260925)
    est_n = None
    for n in range(H + P, 200):
        ok = 0
        for _ in range(200):
            tot = sum(samples_from([rng.random() < p_signal for _ in range(n)])
                      for _a in agents)
            ok += tot >= MIN_SAMPLES
        if ok / 200 >= 0.9:
            est_n = n
            break

    res = {
        "연속성의_정의": {
            "무엇의 연속인가": "**거래일**의 연속이며 에이전트마다 따로 센다. "
                               "rewards/judge_scores_list의 각 항목이 거래일 하나다.",
            "창_조건": ["① 앵커 위치(i-1)에 judge 점수 존재",
                        f"② 이력 {H}칸 중 유효 보상 2개 이상",
                        f"③ 미래 {P}칸 중 유효 보상 1개 이상"],
            "창_길이": f"{H} + {P} = 8",
        },
        "기권의_영향": {
            "보상": "정상 기권은 **미정의(None)** — 임의로 0을 넣지 않는다. "
                    "0을 넣으면 '맞히지도 틀리지도 않은 날'을 '수익 0의 예측'으로 위조한다.",
            "창": "기권일은 ②의 유효 보상에 기여하지 못하고 **앵커로도 쓸 수 없어**"
                  "(judge 점수 없음) 창을 끊는다.",
            "관측_비율": {k: f"{v}/{total} ({v / total:.1%})" for k, v in cnt.items()},
        },
        "현재_보유분": {
            "보유 날짜수(비연속)": len(dates),
            "에이전트별 구성가능 창": cur,
            "합계": sum(cur.values()),
            "주의": "보유 날짜가 1~7일 간격으로 흩어져 '거래일 연속'이 아니다. "
                    "위 수는 보유 항목을 연속으로 가정했을 때의 값이다.",
        },
        "최소_연속_재생_구간": {
            "이상적(모든 날 정상신호)": f"{ideal_n} 거래일",
            "관측 비율 반영 추정": (f"{est_n} 거래일 (정상신호 비율 {p_signal:.0%}에서 "
                                   f"90% 확률로 {MIN_SAMPLES}건 확보)"
                                   if est_n else "200 거래일 내 미달"),
            "주의": "최소 표본 수를 채웠다는 것이 **충분한 학습 성능을 보장하지 않는다**. "
                    "중간발표용 작동 확인이 목적이다.",
        },
        "다음_단계": (f"최소 연속 준비 구간 {est_n or ideal_n} 거래일 + 이후 개발 평가일 "
                      f"2~3개만 생성해 본래 C3를 한 번 작동시킨다. 본실험 전체 재생은 하지 않는다."),
    }
    (OUT / "training_window_plan.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
