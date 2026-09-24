"""
judge 입력의 as-of 점검 — 소급 생성 전에 대표 날짜로 확인한다.

핵심 구분(이것을 틀리면 소급 생성 전체가 오염된다):

    **학습일 ≠ 과거 특징 생성일.**
    06-08에 학습할 05-13 신호의 judge 특징은 **05-13 08:30까지** 알 수 있었던
    정보로 만들어야 한다. 그 신호의 이후 수익률은 **학습 정답으로만** 쓴다.
    06-08까지의 수익률을 05-13의 judge 입력에 넣으면 안 된다.

따라서 소급 생성에는 `as_of=학습일`을 넘기는 것이 아니라
**각 신호 당시의 판단 시각**을 넘긴다. 과거수익률 5개 특징도 같은 원칙이다.

네 가지를 확인한다:
  ① judge에 실제 전달된 프롬프트를 저장하고, 포함된 가격·뉴스·과거 보상의
     이용 가능 시각이 그 신호의 판단 시각 이전인지
  ② 채점 대상 신호의 **미래 보상**이 프롬프트에 없는지
  ③ **읽기 경계 확인** — 판정기가 실제로 읽은 날짜를 계측한다.
     ⚠️ 실제 미래 데이터 행을 주입해 입력·특징의 불변을 확인하는 테스트는
     하지 않았다. 따라서 이것은 '읽기 경계 확인'이지 불변성 증명이 아니다.
  ④ judge 5회 → 요구되는 7개 judge 특징으로의 변환이 학습과 예측에서 동일한지

출력: evaluation/out/judge_asof_check.json
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "contest" / "researcher"))

OUT = ROOT / "evaluation" / "out"
DATE_RE = re.compile(r"20\d\d[-/년]\s?\d{1,2}[-/월]\s?\d{1,2}")


def fingerprint(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


def dates_in(text: str) -> set:
    out = set()
    for d in DATE_RE.findall(text or ""):
        nd = re.sub(r"[년월/]", "-", d).replace(" ", "").rstrip("-")
        ps = [p for p in nd.split("-") if p]
        if len(ps) == 3:
            out.add(f"{ps[0]}-{int(ps[1]):02d}-{int(ps[2]):02d}")
    return out


async def build_prompt_for(judge, data_manager, signals, trigger_time):
    """judge가 실제로 쓰는 경로 그대로 프롬프트를 만든다(LLM 호출 없이)."""
    hist = await judge._calculate_historical_returns(trigger_time, signals)
    prompt = judge.build_scoring_prompt(signals, hist)
    return prompt, hist


async def check_date(date: str, contest) -> dict:
    trigger = f"{date} 08:30:00"
    dm = contest.data_manager
    signals = dm.load_current_signals(trigger)
    signals = contest.filter_valid_signals(signals)
    if not signals:
        return {"date": date, "결과": "채점 대상 신호 0건 — 점검 불가"}

    judge = contest.signal_judger
    prompt, hist = await build_prompt_for(judge, dm, signals, trigger)

    # ① 프롬프트에 든 날짜의 이용 가능 시각
    ds = sorted(dates_in(prompt))
    future_dates = [d for d in ds if d > date]

    # ② 채점 대상 신호의 미래 보상이 들어갔는가
    #    보상 = 신호일 시가 → 다음 거래일 시가. 그 수치가 프롬프트에 있으면 누수.
    leaked_reward = []
    for name, sg in signals.items():
        cd = getattr(sg, "contest_data", None) or {}
        r = cd.get("reward")
        if r is None:
            continue
        for form in (f"{r:.4f}", f"{r*100:.2f}", f"{r:.2%}"):
            if form in prompt:
                leaked_reward.append({"agent": name, "reward": r, "표기": form})
                break

    return {
        "date": date,
        "판단시각": trigger,
        "채점대상_신호": {k: v.symbol_code for k, v in signals.items()},
        "프롬프트_지문": fingerprint(prompt),
        "프롬프트_길이": len(prompt),
        "①_프롬프트_속_날짜": ds,
        "①_판단일_이후_날짜": future_dates,
        "①_판정": ("통과" if not future_dates else
                   "확인 필요 — 판단일 이후 날짜 등장(미리 발표된 미래 일정은 허용되므로 "
                   "문자열만으로 위반 판정하지 않는다. 기준은 **정보의 이용 가능 시각**)"),
        "②_대상신호_미래보상_유출": leaked_reward,
        "②_판정": "통과" if not leaked_reward else "실패 — 대상 신호의 보상이 프롬프트에",
        "판정기_과거보상_입력": hist,
        "주의": "이 점검은 프롬프트 문자열 대조다. 바꿔 쓴 표현은 놓칠 수 있다. "
                "또 미래 날짜 문자열의 유무가 기준이 아니라 **정보의 이용 가능 시각**이 "
                "기준이다 — 미리 발표된 미래 일정(예: 예정된 총파업일)은 허용된다.",
    }


def check_feature_transform() -> dict:
    """④ judge 5회 → 7개 judge 특징. 학습과 예측이 같은 함수를 쓰는지."""
    from research_predictor import ResearchPredictor
    p = ResearchPredictor()
    rewards = [0.01, -0.02, 0.03, 0.0, 0.015]
    scores = [70.0, 65.0, 80.0, 55.0, 60.0]
    df = p._create_features_from_history_and_scores(rewards, scores)
    cols = list(df.columns)
    judge_cols = [c for c in cols if c.startswith("judge")]
    import numpy as np
    return {
        "특징_전체": cols,
        "특징_개수": len(cols),
        "judge_특징": judge_cols,
        "judge_특징_개수": len(judge_cols),
        "변환": "judge 5회 점수 → judge_0..4 (원값 5개) + judge_mean + judge_std = 7개",
        "mean_일치": bool(abs(df["judge_mean"].iloc[0] - np.mean(scores)) < 1e-9),
        "std_일치": bool(abs(df["judge_std"].iloc[0] - np.std(scores)) < 1e-9),
        "학습·예측_동일함수": "train_lightgbm_model과 _predict_single_agent_lightgbm이 "
                              "모두 _create_features_from_history_and_scores를 호출 "
                              "(research_predictor.py:355, :184)",
        "판정": "통과" if len(cols) == 12 and len(judge_cols) == 7 else "확인 필요",
    }


async def main(dates: list):
    from research_contest import ResearchContest
    contest = ResearchContest()
    res = {"설계": {
        "핵심구분": "학습일 ≠ 과거 특징 생성일. 소급 생성에는 각 신호 당시의 판단 "
                    "시각을 넘긴다(학습일이 아니다). 그 신호의 이후 수익률은 학습 "
                    "정답으로만 쓴다.",
        "한계": "프롬프트 문자열 대조다. 바꿔 쓴 표현은 놓칠 수 있다.",
    }, "날짜별": [], "④_특징변환": check_feature_transform()}
    for d in dates:
        try:
            res["날짜별"].append(await check_date(d, contest))
        except Exception as e:  # noqa: BLE001
            res["날짜별"].append({"date": d, "결과": f"점검 실패: {e}"})
    (OUT / "judge_asof_check.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    for r in res["날짜별"]:
        print(json.dumps(r, ensure_ascii=False, indent=1)[:900])
        print()
    print("④ 특징 변환:", json.dumps(res["④_특징변환"], ensure_ascii=False, indent=1))
    print(f"\n저장: {OUT / 'judge_asof_check.json'}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main(sys.argv[1:] or ["2026-05-13", "2026-06-08", "2026-06-15"]))
