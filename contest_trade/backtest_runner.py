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
              root / "config" / "market_config_kr.yaml",
              # 데이터 해석 규칙도 실험 조건이다: 유니버스·종목 별칭 사전이 바뀌면
              # 팩터 입력이 달라지므로 지문에 포함
              root.parent / "data_collection" / "universe" / "ktop30.csv",
              root.parent / "data_collection" / "universe" / "aliases_ktop30.csv"]:
        if p.exists():
            h.update(p.read_bytes())
    h.update(trigger_hour.encode())
    return h.hexdigest()[:12]


class TokenMeter:
    """LLM 호출량 계측 (토크나이저 기반 추정 — 스트리밍이라 API usage 미수집).

    조건 지문(replay_id)은 '같은 실험인가'를, run_id는 '어느 실행인가'를 식별한다.
    비용은 요금표 기반 추정치이며 청구액은 OpenAI 대시보드가 정본."""

    PRICE_PER_M = {"gpt-4o-mini": (0.15, 0.60), "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1": (2.00, 8.00)}

    def __init__(self, model_name: str):
        from utils.llm_utils import count_tokens
        self._count = count_tokens
        self.model_name = model_name
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def wrap(self, llm):
        orig = llm.a_run

        async def metered(messages, **kwargs):
            resp = await orig(messages, **kwargs)
            try:
                self.calls += 1
                self.prompt_tokens += sum(self._count(m.get("content", "")) for m in messages)
                self.completion_tokens += self._count(
                    (resp.content or "") + (getattr(resp, "reasoning_content", "") or ""))
            except Exception:
                pass
            return resp

        llm.a_run = metered

    def snapshot(self):
        pi, po = self.PRICE_PER_M.get(self.model_name, (0.0, 0.0))
        cost = self.prompt_tokens / 1e6 * pi + self.completion_tokens / 1e6 * po
        return {"calls": self.calls, "prompt_tokens_est": self.prompt_tokens,
                "completion_tokens_est": self.completion_tokens,
                "cost_usd_est": round(cost, 4)}


def _git_commit() -> str:
    try:
        import subprocess
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              cwd=Path(__file__).parent, capture_output=True, text=True).stdout.strip()
    except Exception:
        return ""


async def run_replay(start_date: str, end_date: str, trigger_hour: str = "09:00:00"):
    from datetime import datetime
    from config.config import cfg
    if not cfg.llm.get("api_key"):
        sys.exit(
            "[중단] LLM api_key가 없습니다.\n"
            "환경변수 OPENAI_API_KEY를 설정하거나(권장), config_kr.yaml의 llm.api_key에\n"
            "본인 키를 넣어주세요 (파일 방식은 커밋 금지 — RUN_KR_PILOT.md §3 참고)."
        )
    from utils.kr_data_utils import GLOBAL_KR_CLIENT
    from models import llm_model
    from main import SimpleTradeCompany

    days = [d for d in GLOBAL_KR_CLIENT.get_trade_dates(start_date, end_date)]
    replay_id = config_fingerprint(trigger_hour)  # 실험 '조건' 지문
    run_id = f"{replay_id}_{datetime.now().strftime('%Y%m%d-%H%M%S')}"  # 이 '실행' 식별자
    print(f"replay_id={replay_id} run_id={run_id} | 구간 {start_date}~{end_date} | KR 거래일 {len(days)}일")

    meter = TokenMeter(cfg.llm.get("model_name", ""))
    meter.wrap(llm_model.GLOBAL_LLM)
    if getattr(llm_model, "GLOBAL_THINKING_LLM", None) is not None:
        meter.wrap(llm_model.GLOBAL_THINKING_LLM)

    results = []
    for d in days:
        trigger = f"{d[:4]}-{d[4:6]}-{d[6:]} {trigger_hour}"
        t0 = time.time()
        before = meter.snapshot()
        print(f"\n▶ {trigger} 재생 시작")
        company = SimpleTradeCompany()
        state = await company.run_company(trigger)
        elapsed = time.time() - t0
        after = meter.snapshot()
        n_signals = len(getattr(state, "research_signals", None) or state.get("research_signals", []) or []) \
            if state is not None else 0
        day_usage = {k: (after[k] - before[k] if isinstance(after[k], (int, float)) else after[k])
                     for k in ("calls", "prompt_tokens_est", "completion_tokens_est", "cost_usd_est")}
        day_usage["cost_usd_est"] = round(day_usage["cost_usd_est"], 4)
        results.append({"trigger": trigger, "elapsed_s": round(elapsed, 1),
                        "signals": n_signals, "usage": day_usage})
        print(f"■ {trigger} 완료 ({elapsed:.0f}s, LLM {day_usage['calls']}회, ~${day_usage['cost_usd_est']})")

    # 실행 명세(run manifest): 조건 지문 + 실행 정보 + 사용량. 키는 절대 기록하지 않음.
    manifest = {
        "run_id": run_id,
        "replay_id": replay_id,
        "range": [start_date, end_date],
        "trigger_hour": trigger_hour,
        "model_name": cfg.llm.get("model_name", ""),
        "git_commit": _git_commit(),
        "market": cfg.market_type,
        "runs": results,
        "usage_total": meter.snapshot(),
        "note": "usage는 토크나이저 기반 추정 — 청구 정본은 OpenAI 대시보드",
    }
    out = Path(__file__).parent / "agents_workspace" / "runs" / f"run_{run_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"\n실행 명세 저장: {out}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("사용법: CONTEST_TRADE_MARKET=KR-Stock python backtest_runner.py <시작일> <종료일>")
    os.environ.setdefault("CONTEST_TRADE_MARKET", "KR-Stock")
    asyncio.run(run_replay(sys.argv[1], sys.argv[2]))
