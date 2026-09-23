"""
백테스트 러너 (역할 A 신규 구현 — 원본에 없는 replay 루프).

원본 ContestTrade는 '지금 이 순간'만 실행하는 실시간 설계라 백테스트 루프가 없다
(구조 발견 3.2). 이 러너가 과거 거래일을 하루씩 순회하며 파이프라인을 재생한다.

사용법:
  CONTEST_TRADE_MARKET=KR-Stock python backtest_runner.py 2026-05-04 2026-05-08
  → 구간 내 KR 거래일마다 09:00 trigger로 전체 파이프라인 실행.
  신호는 에이전트가 agents_workspace/ 아래 trigger_time별로 저장(원본 구조 재사용).

전제: config_kr.yaml(로컬 사본)에 LLM api_key 설정. 키 없으면 시작 전에 명시적 중단.
채점(다음 거래일 수익률)·조건 재집계는 evaluation 모듈에서 별도 수행 (D18: 재생 1회,
집계는 오프라인 — 이 러너는 '재생' 절반만 담당한다).

replay 기록(D26): 실행마다 config 스냅샷 해시를 출력·기록해 로그 계보를 남긴다.
"""
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def config_fingerprint(trigger_hour: str = "09:00:00") -> str:
    """실험 조건의 지문 (D26 replay ID) — belief/config_kr/market_config + 판단 시각 해시.

    판단 시각은 입력에 포함되는 정보의 마감선이므로 설정 파일과 동급의 조건이다
    (예: 08:30 vs 09:00 합의 변경 시 지문이 달라져야 다른 실험으로 식별된다)."""
    h = hashlib.sha256()
    root = Path(__file__).parent
    for p in [root.parent / "config_kr.yaml",
              root / "config" / "belief_list_kr.json",
              root / "config" / "market_config_kr.yaml"]:
        if p.exists():
            h.update(p.read_bytes())
    h.update(trigger_hour.encode())
    return h.hexdigest()[:12]


async def run_replay(start_date: str, end_date: str, trigger_hour: str = "09:00:00"):
    from config.config import cfg
    if not cfg.llm.get("api_key"):
        sys.exit(
            "[중단] config_kr.yaml에 LLM api_key가 없습니다.\n"
            "US E2E 실행에 쓴 팀 OpenAI 키를 로컬 config_kr.yaml에 넣어주세요 (커밋 금지)."
        )
    from utils.kr_data_utils import GLOBAL_KR_CLIENT
    from main import SimpleTradeCompany

    days = [d for d in GLOBAL_KR_CLIENT.get_trade_dates(start_date, end_date)]
    replay_id = config_fingerprint(trigger_hour)
    print(f"replay_id={replay_id} | 구간 {start_date}~{end_date} | KR 거래일 {len(days)}일")

    results = []
    for d in days:
        trigger = f"{d[:4]}-{d[4:6]}-{d[6:]} {trigger_hour}"
        t0 = time.time()
        print(f"\n▶ {trigger} 재생 시작")
        company = SimpleTradeCompany()
        state = await company.run_company(trigger)
        elapsed = time.time() - t0
        n_signals = len(getattr(state, "research_signals", None) or state.get("research_signals", []) or []) \
            if state is not None else 0
        results.append({"trigger": trigger, "elapsed_s": round(elapsed, 1), "signals": n_signals})
        print(f"■ {trigger} 완료 ({elapsed:.0f}s)")

    log = {"replay_id": replay_id, "range": [start_date, end_date], "runs": results}
    out = Path(__file__).parent / "agents_workspace" / f"replay_{replay_id}_{start_date}_{end_date}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(log, ensure_ascii=False, indent=2))
    print(f"\n재생 로그 저장: {out}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("사용법: CONTEST_TRADE_MARKET=KR-Stock python backtest_runner.py <시작일> <종료일>")
    os.environ.setdefault("CONTEST_TRADE_MARKET", "KR-Stock")
    asyncio.run(run_replay(sys.argv[1], sys.argv[2]))
