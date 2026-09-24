"""
과거 judge 점수 소급 생성 — **각 신호 당시의 판단 시각으로** 만든다.

⚠️ 학습일과 과거 특징 생성일을 구분한다.
   06-08에 학습할 05-13 신호의 judge 특징은 **05-13 08:30까지** 알 수 있었던
   정보로 생성한다. 그 신호의 이후 수익률은 **학습 정답으로만** 쓴다.
   소급 생성 전체에 `as_of=학습일`을 넘기면 안 된다.

그래서 이 스크립트는 과거 날짜 D 각각에 대해 `trigger = D 08:30`으로 판정기를
호출한다. 판정기 내부의 과거 보상 입력도 `eligible_history_dates(D)`로 제한돼
있으므로(누수 차단 완료), D 시점 정보만 들어간다.

기록 규율:
  재생성한 점수는 **당시 실제 점수가 아니라 당시 정보로 재구성한 점수**다.
  모델·프롬프트 지문·입력 지문을 함께 저장해 나중에 구분할 수 있게 한다.

출력: agents_workspace/judger_scores/backfill_<날짜>.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "contest" / "researcher"))

STORE = ROOT / "agents_workspace" / "judger_scores"
TRIGGER_HOUR = "08:30:00"


def fp(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


async def backfill_one(contest, date: str) -> dict:
    """날짜 D의 judge 점수를 **D 08:30 시점 정보로** 생성."""
    from evaluation.check_judge_asof import build_prompt_for
    from config.config import cfg

    trigger = f"{date} {TRIGGER_HOUR}"
    dm = contest.data_manager
    signals = contest.filter_valid_signals(dm.load_current_signals(trigger))
    if not signals:
        return {"date": date, "상태": "채점 대상 신호 0건", "scores": {}}

    prompt, hist = await build_prompt_for(contest.signal_judger, dm, signals, trigger)
    t0 = time.time()
    scores = await contest._get_current_judge_scores(signals, trigger)
    elapsed = time.time() - t0

    return {
        "date": date, "trigger": trigger, "상태": "생성됨",
        "출처": "소급 재구성 — **당시 실제 점수가 아니라 당시 정보로 재구성한 점수**",
        "judge_model": cfg.llm.get("model_name", ""),
        "num_judgers": 5,
        "프롬프트_지문": fp(prompt),
        "입력_신호_지문": fp(json.dumps(
            {k: (v.symbol_code, v.has_opportunity) for k, v in signals.items()},
            ensure_ascii=False, sort_keys=True)),
        "판정기_과거보상_입력": hist,
        "소요초": round(elapsed, 1),
        "scores": {k: (list(v) if isinstance(v, (list, tuple)) else v)
                   for k, v in (scores or {}).items()},
    }


async def main(dates: list, measure_only: bool):
    from research_contest import ResearchContest
    contest = ResearchContest()
    STORE.mkdir(parents=True, exist_ok=True)

    todo = dates[:1] if measure_only else dates
    done = []
    for d in todo:
        out = STORE / f"backfill_{d}.json"
        if out.exists() and not measure_only:
            print(f"{d}: 이미 있음 — 건너뜀")
            continue
        try:
            r = await backfill_one(contest, d)
        except Exception as e:  # noqa: BLE001
            r = {"date": d, "상태": f"실패: {e}", "scores": {}}
        out.write_text(json.dumps(r, ensure_ascii=False, indent=1))
        n = len(r.get("scores") or {})
        print(f"{d}: {r['상태']} | 에이전트 {n}개 | {r.get('소요초', '-')}초 → {out.name}")
        done.append(r)

    if measure_only and done:
        r = done[0]
        print(f"\n=== 비용 실측 (1일)")
        print(f"  소요 {r.get('소요초')}초, judge 호출 5회")
        print(f"  프롬프트 지문 {r.get('프롬프트_지문')}")
        print(f"  점수: {json.dumps(r.get('scores'), ensure_ascii=False)[:300]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dates", nargs="*")
    ap.add_argument("--measure-only", action="store_true",
                    help="첫 날짜만 돌려 비용을 실측한다")
    a = ap.parse_args()
    asyncio.run(main(a.dates, a.measure_only))
