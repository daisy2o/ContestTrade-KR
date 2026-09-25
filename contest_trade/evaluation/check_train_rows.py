"""
학습 행 수 확정 — `354건`과 `30건`의 관계를 설명만이 아니라 **실측**으로 가른다.

두 경우가 있고 함의가 다르다:
  · 학습기에 전달된 행이 30건, 메타만 354  → 메타데이터를 정정하면 끝
  · 학습기에 전달된 행이 354건, 고유 표본 30건 → **중복이 학습 가중에 영향**.
    현재 결과를 '중복 포함 학습 파일럿'으로 표시하고 본실험 전 처리 규칙을 정해야 함

확인 방법: 학습 직전 `X.shape`, 고유 표본 키 수, 표본별 중복 횟수.
모델을 다시 학습시키지 않는다(현재 버전·결과 보존).

출력: evaluation/out/train_rows_check.json
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "contest" / "researcher"))

OUT = ROOT / "evaluation" / "out"
AS_OF = "2026-06-08"


async def main():
    import logging
    logging.disable(logging.WARNING)
    from research_contest import ResearchContest

    contest = ResearchContest()

    # 학습과 **같은 경로**로 자료를 모으고 judge 특징을 주입한다.
    # train_prediction_model이 하는 일을 그대로 재현하되 학습은 하지 않는다.
    training_data = contest._collect_historical_training_data(AS_OF)
    from evaluation.backfill_judge_scores import STORE as BF
    for agent_name, signals in training_data.items():
        for sig in signals:
            d = sig.trigger_time.split(" ")[0]
            f = BF / f"backfill_{d}.json"
            if not f.exists():
                continue
            raw = json.loads(f.read_text()).get("scores", {}).get(agent_name)
            if not raw:
                continue
            vals = [x.get("score") if isinstance(x, dict) else x for x in raw]
            cd = dict(getattr(sig, "contest_data", None) or {})
            cd["judge_scores"] = vals
            if "reward" not in cd:
                ho = (getattr(sig, "has_opportunity", "") or "").strip().lower()
                if ho != "yes":
                    cd["reward"] = None
                else:
                    try:
                        cd["reward"] = await contest.data_manager.calculate_signal_reward(sig)
                    except Exception:
                        cd["reward"] = None
            sig.contest_data = cd

    # 입력 쪽 중복 — 같은 (날짜, 에이전트)가 몇 번 들어갔는가
    key_counts = Counter()
    for agent_name, signals in training_data.items():
        for sig in signals:
            key_counts[(sig.trigger_time.split(" ")[0], agent_name)] += 1
    dup_dist = Counter(key_counts.values())

    # **학습 직전 X.shape** — 학습기에 실제로 전달되는 행 수
    X = y_mean = y_std = None
    err = None
    try:
        X, y_mean, y_std = contest.predictor._prepare_training_data(training_data)
    except Exception as e:  # noqa: BLE001
        err = str(e)[:200]

    n_rows = int(getattr(X, "shape", [0])[0]) if X is not None else None
    n_cols = int(X.shape[1]) if X is not None and len(X.shape) > 1 else None

    uniq_keys = len(key_counts)
    total_entries = sum(key_counts.values())

    if n_rows is None:
        verdict = f"학습 행 수 확인 실패: {err}"
    elif n_rows <= uniq_keys:
        verdict = (f"학습기에 전달된 행이 **{n_rows}건**이다. 메타의 354는 "
                   f"창 누적 카운트일 뿐 학습에 쓰이지 않았다 → **메타데이터만 정정하면 된다**.")
    else:
        verdict = (f"학습기에 전달된 행이 **{n_rows}건**이고 고유 (날짜, 에이전트) 쌍은 "
                   f"{uniq_keys}건이다 → **중복이 학습 가중에 영향을 줬다**. "
                   f"현재 결과를 '중복 포함 학습 파일럿'으로 표시하고, 본실험 전에 "
                   f"중복 처리 규칙을 정해야 한다.")

    res = {
        "as_of": AS_OF,
        "학습_직전_X_shape": [n_rows, n_cols],
        "고유_(날짜,에이전트)_쌍": uniq_keys,
        "수집_리스트_총_항목수": total_entries,
        "표본별_중복횟수_분포": {f"{k}회": v for k, v in sorted(dup_dist.items())},
        "중복_최대": max(key_counts.values()) if key_counts else 0,
        "판정": verdict,
        "주의": "모델을 다시 학습시키지 않았다. 현재 버전과 결과를 보존한다.",
    }
    (OUT / "train_rows_check.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
