"""
입력 고정 모델 비교 하네스 (통제 실험).

같은 팩터 요약·같은 도구 응답·같은 프롬프트를 주고 **최종 리서치 모델만** 바꾼다.
모델이 각자 도구를 다시 호출하면 무엇 때문에 차이가 났는지 흐려지므로,
저장된 리포트의 tool_call_context(도구 호출·응답 전문)를 그대로 재생한다.

재현 대상은 ReAct 이후의 '근거 작성' 단계(write_result)다: 저장된 리포트에
task·belief·background_information·plan_result·tool_call_context가 모두 있으므로
프롬프트를 원본과 동일하게 재구성할 수 있다.

사용:
  CONTEST_TRADE_MARKET=KR-Stock python -m evaluation.model_ab 2026-05-07 \
      --models gpt-4o-mini,gpt-4.1-mini,gpt-4.1 --reps 2
출력: evaluation/out/model_ab_<날짜>.json
      {model: {rep: {agent: final_result}}} + 입력 지문(해시)으로 동일 입력 확인
"""
import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

REPORTS = ROOT / "agents_workspace" / "reports"
OUT = ROOT / "evaluation" / "out"


def load_fixed_inputs(date: str):
    """저장된 리포트에서 write_result 단계의 입력을 복원 (에이전트별)."""
    items = {}
    for p in sorted(REPORTS.rglob(f"{date}_*.json")):
        d = json.loads(p.read_text())
        items[p.parent.name] = {
            "task": d.get("task", ""),
            "trigger_time": d.get("trigger_time", ""),
            "belief": d.get("belief", ""),
            "background_information": d.get("background_information", ""),
            "plan": d.get("plan_result", ""),
            "tool_call_context": d.get("tool_call_context", ""),
            "original_final_result": d.get("final_result", ""),
        }
    return items


def input_fingerprint(item: dict) -> str:
    h = hashlib.sha256()
    for k in ("task", "trigger_time", "background_information", "plan", "tool_call_context"):
        h.update((item.get(k) or "").encode())
    return h.hexdigest()[:12]


async def run_one(model_name: str, item: dict, tools_info: str) -> str:
    from agents.prompts import prompt_for_research_write_result
    from models.llm_model import LLMModel, LLMModelConfig
    from config.config import cfg

    prompt = prompt_for_research_write_result.format(
        current_time=item["trigger_time"],
        task=item["task"],
        background_information=item["background_information"],
        plan=item["plan"],
        tool_call_context=item["tool_call_context"],
        tools_info=tools_info,
        output_format=_output_format(),
        output_language=cfg.research_agent_config.get("output_language", "korean")
        if isinstance(getattr(cfg, "research_agent_config", None), dict) else "korean",
    )
    llm = LLMModel(LLMModelConfig(
        provider=cfg.llm.get("provider", "openai"),
        model_name=model_name,
        api_key=cfg.llm.get("api_key", ""),
        base_url=cfg.llm.get("base_url", ""),
    ))
    resp = await llm.a_run([{"role": "user", "content": prompt}],
                           thinking=False, verbose=False, max_retries=3)
    return resp.content


def _output_format() -> str:
    from agents.prompts import prompt_for_research_invest_output_format
    return prompt_for_research_invest_output_format


def _tools_info() -> str:
    from tools.tool_utils import ToolManager, ToolManagerConfig
    from config.config import cfg
    paths = cfg.research_agent_config.get("tools", []) if isinstance(
        getattr(cfg, "research_agent_config", None), dict) else []
    try:
        return ToolManager(ToolManagerConfig(tool_paths=paths)).build_toolcall_context()
    except Exception:
        return "[]"


async def main(date: str, models: list, reps: int):
    items = load_fixed_inputs(date)
    if not items:
        sys.exit(f"{date} 리포트가 없습니다 — 먼저 재생하세요.")
    tools_info = _tools_info()
    fps = {a: input_fingerprint(it) for a, it in items.items()}
    print(f"고정 입력 {len(items)}개 (에이전트별 지문): {fps}")

    results = {"date": date, "input_fingerprints": fps, "models": {}}
    for m in models:
        results["models"][m] = {}
        for r in range(reps):
            results["models"][m][f"rep{r+1}"] = {}
            for agent, item in items.items():
                try:
                    out = await run_one(m, item, tools_info)
                except Exception as e:
                    out = f"[ERROR] {e}"
                results["models"][m][f"rep{r+1}"][agent] = out
                print(f"  {m} rep{r+1} {agent}: {len(out)}자")
    # 원본(현행 모델의 실제 실행 결과)도 비교 대상으로 보존
    results["original"] = {a: it["original_final_result"] for a, it in items.items()}
    OUT.mkdir(parents=True, exist_ok=True)
    out_p = OUT / f"model_ab_{date}.json"
    out_p.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"\n저장: {out_p}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--models", default="gpt-4o-mini,gpt-4.1-mini,gpt-4.1")
    ap.add_argument("--reps", type=int, default=2)
    a = ap.parse_args()
    asyncio.run(main(a.date, [m.strip() for m in a.models.split(",")], a.reps))
