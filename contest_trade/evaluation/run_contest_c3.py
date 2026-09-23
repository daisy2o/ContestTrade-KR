"""
C3(콘테스트) 오프라인 재집계 러너 — D18 설계: 재생은 1회, 집계는 오프라인.

저장된 리포트(agents_workspace/reports)를 입력으로:
  이력 로드 → reward 산정(다음 거래일, 채점 전용 가격) → LLM judge(5인)
  → 예측(û: LightGBM 있으면 사용, 없으면 judge 평균 폴백[잠정])
  → 가중치 w_i = max(0,û)/Σmax(0,û)
를 계산해 agents_workspace/final_result/에 저장한다.

C2(동일가중)와의 비교는 이 가중치를 채점기에 적용해 오프라인으로 수행한다.
LLM 비용: judge 호출만 발생 (신호 생성 재실행 없음).

사용: CONTEST_TRADE_MARKET=KR-Stock python -m evaluation.run_contest_c3 <시작일> [종료일]
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "contest" / "researcher"))


async def run_range(start: str, end: str):
    from config.config import cfg
    if not cfg.llm.get("api_key"):
        sys.exit("[중단] LLM api_key 필요 (judge 호출) — OPENAI_API_KEY 또는 config_kr.yaml")
    from utils.kr_data_utils import GLOBAL_KR_CLIENT
    from research_contest import ResearchContest

    days = GLOBAL_KR_CLIENT.get_trade_dates(start.replace("-", ""), end.replace("-", ""))
    contest = ResearchContest()
    done, skipped = 0, 0
    for d8 in days:
        date = f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"
        trigger = f"{date} 09:00:00"
        reports = list((ROOT / "agents_workspace" / "reports").rglob(f"{date}_09-00-00.json"))
        if not reports:
            skipped += 1
            continue
        current = contest.data_manager.load_current_signals(trigger)
        current = contest.filter_valid_signals(current)
        if not current:
            print(f"{date}: 유효 신호 0건 — 기권일로 기록 (가중치 없음)")
            skipped += 1
            continue
        try:
            result = await contest.run_research_contest(trigger, current)
            top = sorted(result.optimized_weights.items(), key=lambda x: -x[1])[:3]
            print(f"{date}: 가중치 {['%s=%.2f' % t for t in top]} (유효 {result.valid_signals})")
            done += 1
        except Exception as e:
            print(f"{date}: 콘테스트 실패 — {e}")
    print(f"\n완료 {done}일 / 건너뜀 {skipped}일 → agents_workspace/final_result/")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("사용법: CONTEST_TRADE_MARKET=KR-Stock python -m evaluation.run_contest_c3 <시작일> [종료일]")
    asyncio.run(run_range(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else sys.argv[1]))
