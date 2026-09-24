"""
소급 생성 후 학습 자료 보고 — 요청한 4항목 중 ①②.

① 완전한 학습 표본 수 · 고유 날짜 수 · 중복 표본 수
   '완전한'은 (보상 확정) **그리고** (judge 특징 확보) 를 모두 만족한 것.
   보상만 있는 것은 **후보 표본**이지 완전한 학습 표본이 아니다.

② judge 점수의 반복 간 차이와 특징별 상수 여부
   '5인 패널'이 아니라 **동일 judge 설정의 5회 반복**이다. 관측 표본에서
   점수가 같아 judge_std=0이고 특징이 중복됐다 — 전체 기간에서도 항상 같다고
   확정한 것은 아니다.

출력: evaluation/out/training_data_report.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "evaluation" / "out"
STORE = ROOT / "agents_workspace" / "judger_scores"
REPORTS = ROOT / "agents_workspace" / "reports"


def load_backfill() -> dict:
    """{date: {agent: [5개 점수]}}"""
    out = {}
    for p in sorted(STORE.glob("backfill_*.json")):
        d = json.loads(p.read_text())
        if d.get("scores"):
            out[d["date"]] = {a: [x.get("score") if isinstance(x, dict) else x for x in v]
                              for a, v in d["scores"].items() if isinstance(v, list)}
    return out


def main():
    bf = load_backfill()

    # ② judge 점수의 반복 간 차이
    rep_diff, per_agent_vals = [], defaultdict(set)
    for date, per in bf.items():
        for agent, vals in per.items():
            uniq = len({str(v) for v in vals})
            rep_diff.append({"date": date, "agent": agent, "점수": vals,
                             "반복_고유값수": uniq})
            per_agent_vals[agent].add(vals[0] if vals else None)
    n_varied = sum(1 for r in rep_diff if r["반복_고유값수"] > 1)

    # 특징별 상수 여부 — judge_0..4 는 반복값, mean/std 는 파생
    feature_const = {
        "judge_0..4": ("관측 전부 동일값 → 서로 중복" if n_varied == 0
                       else f"{n_varied}건에서 반복 간 차이 있음"),
        "judge_std": ("관측 전부 0" if n_varied == 0 else "일부 0 아님"),
        "judge_mean": "에이전트마다 다름 — 상수 아님",
    }

    # ① 완전한 학습 표본
    dates = sorted(bf)
    rows = []
    for date in dates:
        for agent, vals in bf[date].items():
            rows.append({"date": date, "agent": agent, "judge": vals})
    key = Counter((r["date"], r["agent"]) for r in rows)
    dup = sum(c - 1 for c in key.values() if c > 1)

    res = {
        "①_학습자료": {
            "judge 특징 확보 (날짜, 에이전트) 쌍": len(rows),
            "고유_날짜수": len(dates),
            "날짜_목록": dates,
            "중복_표본수": dup,
            "에이전트별_쌍수": dict(Counter(r["agent"] for r in rows)),
            "주의": "이 수는 judge 특징을 확보한 쌍이다. **완전한 학습 표본**은 여기에 "
                    "보상 확정까지 더해져야 하며, 학습 시점에 최종 확정된다. "
                    "보상만 있는 수(앞서 보고한 48·205)는 **후보 표본**이다.",
        },
        "②_judge_반복": {
            "설명": "'5인 패널'이 아니라 **동일 judge 설정의 5회 반복**이다 "
                    "(judger_id는 로그에만 쓰이고 프롬프트·temperature가 같다).",
            "반복_간_차이_있는_사례": f"{n_varied}/{len(rep_diff)}",
            "특징별_상수여부": feature_const,
            "에이전트별_관측값": {a: sorted(v) for a, v in per_agent_vals.items()},
            "한계": "관측한 날짜에서 그랬다는 것이지, 전체 기간에서도 항상 같다고 "
                    "확정한 것은 아니다. 입력은 여전히 명목상 12개 특징이며, 중복으로 "
                    "실질 정보가 줄어든 것이지 구현이 원 설계와 달라졌다고 단정할 수 없다 "
                    "— 원 설계가 서로 다른 관점의 판정자를 요구했는지는 별도 확인 사항이다.",
            "그래도_시험 가능한 것": "반복 간에는 같아도 **에이전트별 점수는 다르다**"
                                     f"({dict((a, sorted(v)) for a, v in per_agent_vals.items())}). "
                                     "과거 보상과 결합해 에이전트 가중치를 달리하는 "
                                     "메커니즘은 여전히 시험할 수 있다.",
        },
        "상세": rep_diff,
    }
    (OUT / "training_data_report.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k != "상세"}, ensure_ascii=False, indent=1))
    print(f"\n저장: {OUT / 'training_data_report.json'}")


if __name__ == "__main__":
    main()
