"""
최소 파일럿 실행 계획 — 생성 전에 날짜·재사용·비용을 확정한다.

목표: 본실험 전체가 아니라, **본래 LightGBM 기반 C3가 누수 없이 학습되고
      비교까지 완료되는 최소 파일럿**.

산출하는 것:
  ① 준비 구간 22거래일 (2026-05-04 시작) — **추정 준비량이지 완성 학습 표본
     30건을 보장하지 않는다**
  ② 학습 정답(미래 3칸)의 보상 확정 시각으로 **실제 평가 시작일** 산출
  ③ 재사용 가능한 날짜 / 새로 생성할 날짜
  ④ 비용 항목 분리 (입력·팩터 / 신호 / judge / 평가일 판단 / 재시도·감사)

⚠️ 학습 정답의 확정 시각:
   학습 행의 미래 보상은 `rewards[i:i+P]` (P=3)이고, 각 보상 r_s 는
   **s 시가 → s+1 시가**다. 따라서 마지막 미래 보상은 **거래일 (i+P) 의 시가**를
   요구한다. 그 값이 평가일 D 08:30(개장 전)에 확정돼 있으려면

       거래일(i+P) ≤ D-1  (거래일 기준)

   즉 "준비 구간 마지막 날까지 신호를 만들었다"가 "마지막 학습 행의 정답까지
   준비됐다"는 뜻이 **아니다**.

출력: evaluation/out/min_pilot_plan.json
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "evaluation" / "out"
REPORTS = ROOT / "agents_workspace" / "reports"
BACKFILL = ROOT / "agents_workspace" / "judger_scores"

START = "2026-05-04"
PREP_DAYS = 22
EVAL_DAYS = 3
H, P = 5, 3
COST_PER_DAY_REPLAY = 0.047     # 실측 (입력·팩터 + 신호 생성 포함, gpt-4o-mini 상위 단계)
COST_PER_DAY_JUDGE = 0.001      # 실측 9.7초/5회, gpt-4o-mini
COST_PER_EVAL_CALL = 0.025      # gpt-4.1 판단 1회 실측 근사


def main():
    from utils.kr_data_utils import GLOBAL_KR_CLIENT as K

    end = (datetime.fromisoformat(START) + timedelta(days=120)).strftime("%Y%m%d")
    tds = K.get_trade_dates(START.replace("-", ""), end)
    fmt = lambda d8: f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"

    prep = [fmt(d) for d in tds[:PREP_DAYS]]

    # ② 마지막 학습 행의 정답이 확정되는 시점 → 실제 평가 시작일
    #    앵커 i 의 미래 보상은 거래일 (i+P) 시가까지 필요. 준비 구간 안에서
    #    가능한 마지막 앵커는 i = PREP_DAYS - P - 1 (0-index), 그 정답은
    #    거래일 (i+P) = PREP_DAYS-1 의 시가를 쓴다.
    last_anchor_idx = PREP_DAYS - P - 1
    last_target_day_idx = last_anchor_idx + P          # 정답에 필요한 마지막 거래일
    # 그 시가가 확정돼 있으려면 평가일 D 는 그 다음 거래일 이후
    first_eval_idx = last_target_day_idx + 1
    evals = [fmt(d) for d in tds[first_eval_idx:first_eval_idx + EVAL_DAYS]]

    # ③ 재사용 / 신규
    have = {p.name.split("_")[0] for p in REPORTS.glob("agent_0/*_08-30-00.json")}
    have_judge = {p.name.replace("backfill_", "").replace(".json", "")
                  for p in BACKFILL.glob("backfill_*.json")}
    prep_reuse = [d for d in prep if d in have]
    prep_new = [d for d in prep if d not in have]
    judge_reuse = [d for d in prep if d in have_judge]
    judge_new = [d for d in prep if d not in have_judge]
    eval_reuse = [d for d in evals if d in have]
    eval_new = [d for d in evals if d not in have]

    # 학습 신호를 만든 모델 — 기록 의무
    model_note = {}
    for d in sorted(have)[:1]:
        pass
    from config.config import cfg
    model_note = {
        "학습 신호(과거) 생성 모델": "기존 저장분은 상위 단계 gpt-4o-mini + 최종 판단이 "
                                     "날짜에 따라 gpt-4o-mini 또는 gpt-4.1-2025-04-14",
        "신규 생성분": f"상위 단계 {cfg.llm.get('model_name')} + 최종 판단 "
                       f"{(getattr(cfg, 'llm_judgment', {}) or {}).get('model_name')}",
        "judge 점수": cfg.llm.get("model_name"),
        "평가일 판단": (getattr(cfg, "llm_judgment", {}) or {}).get("model_name"),
        "⚠️ 한계": "과거 학습 신호와 평가일 판단의 **모델이 다르면 학습·평가 분포가 "
                   "다르다**. 작동 확인에는 쓸 수 있으나 성능 해석에는 이 한계를 붙인다.",
    }

    cost = {
        "입력·팩터 생성 (신규 준비일)": round(len(prep_new) * COST_PER_DAY_REPLAY, 3),
        "부족한 에이전트 신호 생성": "위 항목에 포함 (재생이 신호까지 만든다)",
        "judge 점수 생성 (신규)": round(len(judge_new) * COST_PER_DAY_JUDGE, 4),
        "평가일 GPT-4.1 판단": round(len(evals) * 3 * COST_PER_EVAL_CALL, 3),
        "평가일 입력 생성 (신규)": round(len(eval_new) * COST_PER_DAY_REPLAY, 3),
        "재시도·감사": "별도 — 실측 전 추정하지 않는다",
        "⚠️": "이 값들은 **항목별 추정**이며 총액을 단일 수치로 확정하지 않는다. "
              "재시도·감사 비용이 빠져 있다.",
    }

    res = {
        "목표": "본실험 전체가 아니라, 본래 LightGBM 기반 C3가 누수 없이 학습되고 "
                "비교까지 완료되는 최소 파일럿.",
        "①_준비구간": {
            "시작": START, "거래일수": PREP_DAYS,
            "날짜": prep,
            "⚠️": "22일은 **관측 비율(정상신호 79%)로 낸 추정 준비량**이며 "
                  "완성 학습 표본 30건을 **보장하지 않는다**. 날짜·에이전트 간 "
                  "의존성과 계산 가정은 확인하지 않았다. 생성 후 실제 완성 표본을 "
                  "세고, 부족하면 기준을 낮추거나 기권에 임의 보상을 넣지 말고 "
                  "부족량과 추가 필요 날짜를 보고한다.",
        },
        "②_평가일": {
            "규칙": "학습 행의 미래 보상은 거래일(i+P)의 시가를 쓴다. 그 값이 평가일 "
                    "08:30(개장 전)에 확정돼 있으려면 거래일(i+P) ≤ 평가일-1 이어야 한다.",
            "준비구간 마지막 앵커(0-index)": last_anchor_idx,
            "그 정답에 필요한 마지막 거래일": fmt(tds[last_target_day_idx]),
            "따라서 첫 평가일": evals[0] if evals else None,
            "평가일 3일": evals,
            "분리": "평가일의 결과는 학습에 들어가지 않는다. 첫 평가일 **이전** 자료로 "
                    "학습한 모델을 3일간 **고정**해 C1~C4를 점검한다.",
        },
        "③_재사용": {
            "준비구간 재사용": prep_reuse, "준비구간 신규 생성": prep_new,
            "judge 재사용": judge_reuse, "judge 신규": judge_new,
            "평가일 재사용": eval_reuse, "평가일 신규": eval_new,
        },
        "④_비용_항목별": cost,
        "⑤_모델_버전": model_note,
    }
    (OUT / "min_pilot_plan.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
