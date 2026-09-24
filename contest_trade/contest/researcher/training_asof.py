"""
예측기 학습의 as-of 규율 — 학습 자료·정답·모델 파일 전부에 시점 제한을 건다.

왜 필요한가:
  기존 `_collect_historical_training_data`는 `datetime.now()`(실행 시점) 기준으로
  180일을 훑었다. 재생 실험에서는 **판단 시각과 무관한 자료**를 학습에 넣게 되고,
  한 번 만든 모델 파일을 **더 이른 판단일에 재사용**하면 미래 정보가 들어간다.

세 겹으로 막는다:
  ① 특징(features)  — 신호 자체가 판단 시각 이전에 생성된 것만
  ② 정답(target)    — 보상 계산에 필요한 가격이 판단 시각 이전에 확정된 것만
                      (`reward_availability`의 규칙을 그대로 쓴다)
  ③ 모델 파일       — 학습 as-of 를 파일에 함께 저장하고, **학습 as-of > 판단일**인
                      모델은 로드를 거부한다

부족하면 부족하다고 말한다:
  '파일 수'는 학습 표본 수가 아니다. 유효한 (특징, 보상) 쌍의 수, 결측, 보상 분포를
  세어서 함께 돌려준다. 부족한 자료를 억지로 학습시켜 '실행 완료'로 처리하지 않는다.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

# 결과를 보기 전에 고정한 값
MIN_TRAIN_SAMPLES = 30      # 유효 (특징, 보상) 쌍의 최소 수
TRAIN_WINDOW_DAYS = 180     # 학습 창 (판단일 기준 과거)
RETRAIN_EVERY_DAYS = 5      # 재학습 주기

METHOD_LIGHTGBM = "lightgbm"
METHOD_JUDGE = "judge_fallback"
METHOD_INSUFFICIENT = "insufficient_history"


def model_meta_path(model_dir: Path) -> Path:
    return Path(model_dir) / "train_meta.json"


def save_train_meta(model_dir: Path, as_of: str, n_samples: int, diag: dict) -> None:
    """as_of 는 'YYYY-MM-DD HH:MM:SS' 로 저장한다 — 날짜만으로는 같은 날 08:30 이후
    정보로 학습한 모델을 걸러내지 못한다."""
    Path(model_dir).mkdir(parents=True, exist_ok=True)
    model_meta_path(model_dir).write_text(json.dumps(
        {"as_of": as_of, "n_samples": n_samples, "진단": diag,
         "규율": "이 모델은 as_of 이전 자료로만 학습됐다. as_of 보다 이른 판단일에 "
                 "재사용하면 미래 정보가 들어가므로 로더가 거부한다."},
        ensure_ascii=False, indent=1))


def model_usable_for(model_dir: Path, judgment_date: str,
                     trigger_hour: str = "08:30:00") -> tuple:
    """(사용 가능 여부, 사유). 학습 as-of 가 판단 **시각**보다 뒤면 거부한다.

    날짜만 비교하면 안 된다 — 같은 날짜라도 08:30 **이후** 정보로 학습한 모델은
    그 판단에 쓸 수 없다. 그래서 as_of 를 'YYYY-MM-DD HH:MM:SS'로 비교한다.
    """
    judgment_dt = f"{judgment_date} {trigger_hour}"
    p = model_meta_path(model_dir)
    if not p.exists():
        return False, "학습 메타(train_meta.json) 없음 — 어느 시점 자료로 학습됐는지 알 수 없다"
    try:
        meta = json.loads(p.read_text())
    except Exception as e:  # noqa: BLE001
        return False, f"학습 메타 읽기 실패: {e}"
    as_of = meta.get("as_of")
    if not as_of:
        return False, "학습 메타에 as_of 없음"
    as_of_dt = as_of if len(as_of) > 10 else f"{as_of} 00:00:00"
    if as_of_dt > judgment_dt:
        return False, (f"모델 학습 as_of({as_of_dt})가 판단 시각({judgment_dt})보다 뒤 — "
                       f"미래 정보 재사용이므로 거부")
    stale = (datetime.strptime(judgment_date, "%Y-%m-%d")
             - datetime.strptime(as_of[:10], "%Y-%m-%d")).days
    if stale > RETRAIN_EVERY_DAYS:
        return False, f"학습 as_of({as_of})가 {stale}일 지남 — 재학습 주기({RETRAIN_EVERY_DAYS}일) 초과"
    return True, f"사용 가능 (학습 as_of={as_of}, {stale}일 전)"


def summarize(pairs: list) -> dict:
    """유효 (특징, 보상) 쌍의 진단. '파일 수'와 구분해서 보고하기 위한 것."""
    rewards = [r for _, r in pairs if r is not None]
    n_missing = len(pairs) - len(rewards)
    diag = {
        "수집된_신호수(파일 기준)": len(pairs),
        "유효_학습표본수(보상 확정)": len(rewards),
        "보상_결측": n_missing,
        "최소_요구표본": MIN_TRAIN_SAMPLES,
    }
    if rewards:
        rs = sorted(rewards)
        n = len(rs)
        diag["보상_분포"] = {
            "min": round(rs[0], 5), "max": round(rs[-1], 5),
            "median": round(rs[n // 2], 5),
            "평균": round(sum(rs) / n, 5),
            "양수비율": round(sum(1 for r in rs if r > 0) / n, 3),
            "전부_동일값": rs[0] == rs[-1],
        }
        # 전부 같은 값이면 학습해도 아무것도 배우지 못한다
        if rs[0] == rs[-1]:
            diag["경고"] = "보상이 전부 같은 값 — 학습 불가"
    return diag


def decide_method(diag: dict) -> tuple:
    """학습 가능 여부 판정. 부족하면 부족하다고 말한다."""
    n = diag.get("유효_학습표본수(보상 확정)", 0)
    if n < MIN_TRAIN_SAMPLES:
        return METHOD_INSUFFICIENT, (
            f"유효 학습 표본 {n}건 < 최소 {MIN_TRAIN_SAMPLES}건. "
            f"수집 신호 {diag.get('수집된_신호수(파일 기준)', 0)}건은 **파일 수**이지 "
            f"학습 표본 수가 아니다.")
    if diag.get("보상_분포", {}).get("전부_동일값"):
        return METHOD_INSUFFICIENT, "보상이 전부 같은 값 — 학습할 신호가 없다"
    return METHOD_LIGHTGBM, f"유효 학습 표본 {n}건 — 학습 가능"
