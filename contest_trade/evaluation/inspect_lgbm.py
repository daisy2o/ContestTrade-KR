"""
학습된 LightGBM 점검 — 추가 API 호출 없음.

`trained`는 **학습 함수가 완료됐다**는 뜻이지 유용한 관계를 학습했다는 증거가 아니다.
표본이 30건뿐이라 설정에 따라 **분기가 없는 상수 예측 모델**이 만들어질 수 있다.

기록하는 것:
  · 실제 사용된 학습 표본 수 · 고유 날짜 수
  · 트리의 분기(split) 수 · 리프 수
  · 에이전트별 예측값과 최종 가중치
  · 어떤 특징이 실제 분기에 사용됐는지

분기가 없으면 숨기지 않고 그대로 적는다. 좋은 결과를 만들려고 그 자리에서
튜닝하지 않는다.

출력: evaluation/out/lgbm_inspection.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "contest" / "researcher"))

OUT = ROOT / "evaluation" / "out"
MODEL_DIR = ROOT / "contest" / "researcher" / "lightgbm_predictor"


# _create_features_from_history_and_scores 의 feature_cols_order 와 동일
FEATURE_NAMES = ["reward_mean_1d", "reward_mean_3d", "reward_std_3d",
                 "reward_mean_5d", "reward_std_5d",
                 "judge_0", "judge_1", "judge_2", "judge_3", "judge_4",
                 "judge_mean", "judge_std"]


def rename(col: str) -> str:
    """Column_N → 실제 특징명. 학습 시 이름이 보존되지 않아 복원한다."""
    if col.startswith("Column_"):
        try:
            return FEATURE_NAMES[int(col.split("_")[1])]
        except (ValueError, IndexError):
            return col
    return col


def tree_stats(booster) -> dict:
    """분기·리프 수와 실제 분기에 쓰인 특징."""
    df = booster.trees_to_dataframe()
    splits = df[df["split_feature"].notna()]
    leaves = df[df["split_feature"].isna()]
    used = Counter(rename(c) for c in splits["split_feature"].tolist())
    reward_splits = sum(v for k, v in used.items() if k.startswith("reward"))
    judge_splits = sum(v for k, v in used.items() if k.startswith("judge"))
    return {
        "트리수": int(booster.num_trees()),
        "분기_수": int(len(splits)),
        "리프_수": int(len(leaves)),
        "분기에_쓰인_특징": dict(used.most_common()),
        "보상특징_분기": reward_splits,
        "judge특징_분기": judge_splits,
        "상수예측_여부": len(splits) == 0,
    }


def main():
    import joblib

    meta_p = MODEL_DIR / "train_meta.json"
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}

    if meta.get("n_samples") and (meta.get("진단", {}) or {}).get("실제_구성가능_학습표본"):
        meta = dict(meta)
        meta["n_samples_주의"] = (
            f"메타의 n_samples({meta['n_samples']})는 **창 누적으로 중복된 후보 수**다. "
            f"실제 학습 표본은 {meta['진단']['실제_구성가능_학습표본']}건이다.")
    res = {"학습_메타": meta, "⚠️": "'trained'는 학습 함수가 완료됐다는 뜻이지 "
                                    "유용한 관계를 학습했다는 증거가 아니다."}

    for name in ("lgbm_mean_model", "lgbm_std_model"):
        p = MODEL_DIR / f"{name}.joblib"
        if not p.exists():
            res[name] = "파일 없음"
            continue
        m = joblib.load(p)
        booster = getattr(m, "booster_", None) or m
        try:
            st = tree_stats(booster)
        except Exception as e:  # noqa: BLE001
            st = {"오류": str(e)[:120]}
        try:
            st["특징명"] = list(getattr(m, "feature_name_", []) or booster.feature_name())
            imp = getattr(m, "feature_importances_", None)
            if imp is not None:
                st["특징_중요도"] = {f: int(v) for f, v in zip(st["특징명"], imp) if v > 0}
        except Exception:
            pass
        res[name] = st

    # 파일럿 3일의 에이전트별 예측·가중치
    wp = OUT / "c3_weights_minpilot.json"
    if wp.exists():
        w = json.loads(wp.read_text())
        res["날짜별_가중치"] = {d: {"weights": v.get("weights"),
                                   "predicted(û)": v.get("predicted"),
                                   "유효신호": v.get("유효신호")}
                               for d, v in w.items()}

    mean = res.get("lgbm_mean_model", {})
    if isinstance(mean, dict) and mean.get("상수예측_여부"):
        res["판정"] = ("**학습 경로는 작동했으나, 현재 자료·설정에서는 비상수 예측을 "
                       "학습하지 못했다.** 분기가 0개이므로 모든 입력에 같은 값을 낸다. "
                       "튜닝으로 덮지 않고 그대로 보고한다.")
    elif isinstance(mean, dict) and "분기_수" in mean:
        n_train = (meta.get("진단", {}) or {}).get("실제_구성가능_학습표본")
        ratio = (mean["분기_수"] / n_train) if n_train else None
        res["판정"] = (f"분기 {mean['분기_수']}개 · 리프 {mean['리프_수']}개로 "
                       f"**비상수 예측을 학습했다**(상수 모델이 아니다).")
        res["⚠️ 과적합"] = {
            "실제 학습 표본": n_train,
            "분기 수": mean["분기_수"],
            "표본당 분기": round(ratio, 1) if ratio else None,
            "판독": (f"학습 표본 {n_train}건에 분기 {mean['분기_수']}개 — "
                     f"표본보다 분기가 {ratio:.0f}배 많다. **심한 과적합 신호**이며, "
                     f"비상수라는 사실이 유용한 관계를 학습했다는 뜻은 아니다. "
                     f"여기서 튜닝하지 않고 한계로 기록한다."),
            "특징 편중": (f"보상 특징 분기 {mean.get('보상특징_분기')}개 vs "
                          f"judge 특징 분기 {mean.get('judge특징_분기')}개"),
        }
    (OUT / "lgbm_inspection.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
