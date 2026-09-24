"""
근거 구조 A/B 비교 하네스 (모델 고정).

구조 A(현행): 모델이 근거를 자유 서술("약 100단어")
구조 B(신규): 모델은 근거 은행에서 **ID만 선택**하고 해석은 별도 필드.
              인용문·출처·시각은 프로그램이 원문에서 채운다.

모델·입력·날짜·반복 수를 고정하고 구조만 바꾸므로, 차이는 구조에 귀속된다.
(모델 교체 효과와 섞이지 않게 기본 모델은 현행 기준 모델로 둔다.)

집계 단위 고정: 근거 1건 = 선택된 ID 1개 + 그에 붙은 해석 1개.
구조 B가 ID를 여러 개 고른다고 분모가 자동으로 늘지 않도록,
'신호당 근거 수'도 함께 보고한다.

사용:
  CONTEST_TRADE_MARKET=KR-Stock python -m evaluation.structure_ab 2026-05-07 \
      --model gpt-4o-mini --reps 2
출력: evaluation/out/structure_ab_<날짜>.json
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.evidence_bank import build_bank, render_bank, resolve  # noqa: E402

REPORTS = ROOT / "agents_workspace" / "reports"
OUT = ROOT / "evaluation" / "out"

STRUCT_B_FORMAT = """<signals>
<signal>
<has_opportunity>yes 또는 no</has_opportunity>
<action>buy(상승 예측) 또는 sell(하락 예측)</action>
<symbol_code>6자리 종목코드</symbol_code>
<symbol_name>종목명</symbol_name>
<evidence_list>
<evidence>
<ref>근거 ID 하나 (아래 <evidence_bank>에 실제로 있는 ID만. 예: S12 또는 T7)</ref>
<interpretation>그 근거가 이 판단을 왜 뒷받침하는지 — 당신의 해석. 근거의 수치·날짜·문구를 여기서 다시 쓰지 말 것. 해석만 쓸 것.</interpretation>
</evidence>
<!-- evidence 블록을 근거 수만큼 반복 (최대 20) -->
</evidence_list>
<limitations>
<limitation>이 제안의 한계·위험</limitation>
</limitations>
<probability>0-100: 평가 구간(당일 시가 → 다음 거래일 종가)에서 선택한 방향이 맞을 확률</probability>
</signal>
<!-- signal 블록을 0~5개. 기회가 없으면 <signals></signals> 로 비워 제출 -->
</signals>

중요 규칙:
- 인용문은 쓰지 않는다. 근거는 <ref>에 ID로만 지정한다. 인용문·출처·시각은 시스템이 원문에서 채운다.
- ID는 반드시 <evidence_bank> 목록의 대괄호 안 형식 그대로다: 알파벳 S 또는 T로 시작하고 숫자가 뒤따른다(S12, T7).
  본문에 보이는 [21], [37] 같은 숫자만의 표기는 문서 참조번호이지 근거 ID가 아니다 — <ref>에 쓰면 무효 처리된다.
- <evidence_bank>에 없는 ID를 지어내지 않는다.
- <interpretation>에는 근거의 숫자·날짜를 옮겨 적지 말고, 그것이 의미하는 바만 쓴다.
  당신이 고른 ID의 내용과 다른 수치를 해석에 쓰면 불일치로 검출된다."""


def load_inputs(date: str):
    items = {}
    for p in sorted(REPORTS.rglob(f"{date}_*.json")):
        d = json.loads(p.read_text())
        items[p.parent.name] = d
    return items


def build_prompt_B(d: dict, bank_text: str, tools_info: str) -> str:
    from agents.prompts import prompt_for_research_write_result
    base = prompt_for_research_write_result.format(
        current_time=d.get("trigger_time", ""),
        task=d.get("task", ""),
        background_information=d.get("background_information", ""),
        plan=d.get("plan_result", ""),
        tool_call_context=d.get("tool_call_context", ""),
        tools_info=tools_info,
        output_format=STRUCT_B_FORMAT,
        output_language="Korean",
    )
    return base + f"\n\n<evidence_bank>\n{bank_text}\n</evidence_bank>\n"


def build_prompt_A(d: dict, tools_info: str) -> str:
    from agents.prompts import (prompt_for_research_write_result,
                                prompt_for_research_invest_output_format)
    return prompt_for_research_write_result.format(
        current_time=d.get("trigger_time", ""),
        task=d.get("task", ""),
        background_information=d.get("background_information", ""),
        plan=d.get("plan_result", ""),
        tool_call_context=d.get("tool_call_context", ""),
        tools_info=tools_info,
        output_format=prompt_for_research_invest_output_format,
        output_language="Korean",
    )


def _tools_info() -> str:
    from tools.tool_utils import ToolManager, ToolManagerConfig
    from config.config import cfg
    paths = cfg.research_agent_config.get("tools", []) if isinstance(
        getattr(cfg, "research_agent_config", None), dict) else []
    try:
        return ToolManager(ToolManagerConfig(tool_paths=paths)).build_toolcall_context()
    except Exception:
        return "[]"


async def call(model: str, prompt: str) -> str:
    from models.llm_model import LLMModel, LLMModelConfig
    from config.config import cfg
    llm = LLMModel(LLMModelConfig(
        provider=cfg.llm.get("provider", "openai"), model_name=model,
        api_key=cfg.llm.get("api_key", ""), base_url=cfg.llm.get("base_url", "")))
    r = await llm.a_run([{"role": "user", "content": prompt}],
                        thinking=False, verbose=False, max_retries=3)
    return r.content


REF_RE = re.compile(r"<ref>(.*?)</ref>\s*<interpretation>(.*?)</interpretation>", re.S)
SIG_RE = re.compile(r"<signal>(.*?)</signal>", re.S)
NUM_RE = re.compile(r"\d[\d,]*\.?\d*")


def _nums(text: str) -> set:
    """의미 있는 수치만 (2자리 이상) — 연도·한자리 수는 잡음."""
    out = set()
    for n in NUM_RE.findall(text or ""):
        v = n.replace(",", "")
        if len(v.replace(".", "")) >= 3:
            out.add(v.rstrip("0").rstrip(".") if "." in v else v)
    return out


def check_interpretation(resolved: dict, interp: str) -> dict:
    """해석이 선택한 근거와 정합하는지 기계 검사.

    인용이 정확해도 해석은 틀릴 수 있으므로(합산을 개별로 해석 등) 이 검사는
    '수치 불일치'만 잡는다. 의미 수준의 지지 여부는 감사에서 따로 판정한다."""
    if not resolved.get("valid"):
        return {"number_mismatch": None, "note": "무효 ID"}
    src_nums = _nums(resolved.get("quote", ""))
    itp_nums = _nums(interp)
    stray = sorted(itp_nums - src_nums)
    return {"number_mismatch": bool(stray), "stray_numbers": stray,
            "note": "해석에 근거 밖 수치 등장" if stray else ""}


def expand_B(raw: str, bank) -> dict:
    """구조 B 출력에서 ID를 원문으로 확장 + 무효 ID 집계."""
    signals, invalid, total_refs = [], 0, 0
    for blk in SIG_RE.findall(raw or ""):
        evs = []
        for uid, interp in REF_RE.findall(blk):
            uid = uid.strip()
            total_refs += 1
            r = resolve(bank, uid)
            if not r["valid"]:
                invalid += 1
            chk = check_interpretation(r, interp)
            evs.append({"ref": uid, "interpretation": interp.strip(),
                        "resolved": r, "check": chk})
        sym = re.search(r"<symbol_code>(.*?)</symbol_code>", blk, re.S)
        act = re.search(r"<action>(.*?)</action>", blk, re.S)
        signals.append({"symbol": sym.group(1).strip() if sym else "",
                        "action": act.group(1).strip() if act else "",
                        "evidences": evs})
    mismatch = sum(1 for s in signals for e in s["evidences"]
                   if e.get("check", {}).get("number_mismatch"))
    return {"signals": signals, "n_refs": total_refs, "n_invalid_refs": invalid,
            "n_number_mismatch": mismatch}


async def main(date: str, model: str, reps: int, only: str = "AB"):
    items = load_inputs(date)
    if not items:
        sys.exit(f"{date} 리포트 없음")
    tools_info = _tools_info()
    # only="B"면 A는 기존 산출물을 재사용한다(입력·설정 동일). 회귀 점검용.
    prev = {}
    if only == "B":
        pp = OUT / f"structure_ab_{date}.json"
        if pp.exists():
            prev = json.loads(pp.read_text()).get("structures", {}).get("A", {})
    res = {"date": date, "model": model, "only": only,
           "structures": {"A": prev, "B": {}}, "bank_sizes": {},
           "note": "only=B인 경우 A는 이전 실행 산출물 재사용(입력·설정 동일)"}
    banks = {}
    for agent, d in items.items():
        banks[agent] = build_bank(d.get("background_information", ""), d.get("tool_call_context", ""))
        res["bank_sizes"][agent] = len(banks[agent])
    print(f"근거 은행 크기: {res['bank_sizes']}")

    for r in range(reps):
        rk = f"rep{r+1}"
        if only != "B":
            res["structures"]["A"][rk] = {}
        res["structures"]["B"][rk] = {}
        for agent, d in items.items():
            if only != "B":
                a_out = await call(model, build_prompt_A(d, tools_info))
                res["structures"]["A"].setdefault(rk, {})[agent] = {"raw": a_out}
            else:
                a_out = (prev.get(rk, {}).get(agent, {}) or {}).get("raw", "")
            b_out = await call(model, build_prompt_B(d, render_bank(banks[agent]), tools_info))
            res["structures"]["B"][rk][agent] = {"raw": b_out, "expanded": expand_B(b_out, banks[agent])}
            inv = res["structures"]["B"][rk][agent]["expanded"]
            print(f"  {rk} {agent}: A {len(a_out)}자 | B refs {inv['n_refs']} "
                  f"(무효 {inv['n_invalid_refs']}, 수치불일치 {inv['n_number_mismatch']})")

    OUT.mkdir(parents=True, exist_ok=True)
    suffix = "_regression" if only == "B" else ""
    p = OUT / f"structure_ab_{date}{suffix}.json"
    p.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(f"\n저장: {p}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--only", default="AB", choices=["AB", "B"])
    a = ap.parse_args()
    asyncio.run(main(a.date, a.model, a.reps, a.only))
