"""
KR 파이프라인 드라이런 — LLM만 가짜, 나머지 전부 진짜 (키 없이 조립 전 구간 검증).

목적: 화요일 전체 실행 전에 "LLM 응답이 정상이라면 파이프라인이 끝까지 도는가"를
미리 확인해 조립 단계 크래시를 제거한다. LLM 판단 품질은 검증 대상 아님.

검증 범위: config 로딩 → 데이터 에이전트 3종(실데이터: DART 라이브·Factiva·텔레그램)
→ 팩터 전달 → 리서치 에이전트 3종(belief) ReAct → 신호 저장 → 신호 파서 검증.

실행: CONTEST_TRADE_MARKET=KR-Stock python dryrun_kr.py [트리거일 기본 2026-05-29]
"""
import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("CONTEST_TRADE_MARKET", "KR-Stock")

MOCK_SIGNAL = """<signals>
<signal>
<has_opportunity>yes</has_opportunity>
<action>buy</action>
<symbol_code>005930</symbol_code>
<symbol_name>삼성전자</symbol_name>
<evidence_list>
<evidence>드라이런 모의 근거: 공시·리서치 코멘트 긍정적</evidence>
<time>2026-05-28</time>
<from_source>kr_dart_disclosure</from_source>
</evidence_list>
<limitations>
<limitation>모의 응답 — 판단 아님</limitation>
</limitations>
<probability>72</probability>
</signal>
</signals>"""


def make_mock():
    calls = {"n": 0, "by_kind": {}}

    async def mock_a_run(messages, **kwargs):
        calls["n"] += 1
        prompt = messages[-1]["content"] if messages else ""
        if "<signals>" in prompt:          # write-result 단계 (output_format 포함)
            kind, content = "write_result", MOCK_SIGNAL
        elif '"tool_name"' in prompt:      # 도구 선택 단계 (parse_bounding_json 형식)
            kind = "tool_select"
            content = '<Output>\n{"tool_name": "final_report", "properties": {}}\n</Output>'
        else:                               # 계획/요약 등 자유 텍스트 단계
            kind, content = "free_text", "모의 응답: 주요 이벤트를 요약하면 반도체 업종 강세."
        calls["by_kind"][kind] = calls["by_kind"].get(kind, 0) + 1
        return SimpleNamespace(content=content, reasoning_content="")

    return mock_a_run, calls


def _artifact_paths(trigger_date: str):
    """이 날짜의 드라이런 산출물(팩터·리포트) 경로 목록."""
    ws = Path(__file__).parent / "agents_workspace"
    stamp = f"{trigger_date}_09-00-00.json"
    return [p for d in ("factors", "reports") if (ws / d).exists()
            for p in (ws / d).rglob(stamp)]


def _quarantine_new_artifacts(trigger_date: str, preexisting: set):
    """드라이런이 새로 만든 가짜 산출물을 실제 실행이 재사용하지 못하게 격리.

    드라이런 전부터 있던 파일(진짜일 수 있음)은 건드리지 않는다."""
    ws = Path(__file__).parent / "agents_workspace"
    qdir = ws / "_dryrun_quarantine" / trigger_date
    moved = 0
    for p in _artifact_paths(trigger_date):
        if p in preexisting:
            continue
        dest = qdir / p.relative_to(ws)
        dest.parent.mkdir(parents=True, exist_ok=True)
        p.rename(dest)
        moved += 1
    if moved:
        print(f"가짜 산출물 {moved}개를 격리: {qdir}")
    return moved


async def main(trigger_date: str):
    from models import llm_model
    mock, calls = make_mock()
    llm_model.GLOBAL_LLM.a_run = mock
    if hasattr(llm_model, "GLOBAL_THINKING_LLM"):
        llm_model.GLOBAL_THINKING_LLM.a_run = mock
    if hasattr(llm_model, "GLOBAL_VISION_LLM"):
        llm_model.GLOBAL_VISION_LLM.a_run = mock

    from main import SimpleTradeCompany
    trigger = f"{trigger_date} 09:00:00"
    preexisting = set(_artifact_paths(trigger_date))  # 드라이런 이전부터 있던 산출물 보호
    print(f"=== KR 드라이런: {trigger} (LLM 모의, 데이터 실물) ===")
    try:
        await _run_and_verify(trigger, trigger_date, calls)
    finally:
        # 중간 크래시여도 이미 생성된 가짜 산출물은 반드시 격리 (외부 리뷰 P1)
        _quarantine_new_artifacts(trigger_date, preexisting)


async def _run_and_verify(trigger: str, trigger_date: str, calls: dict):
    from main import SimpleTradeCompany
    company = SimpleTradeCompany()
    state = await company.run_company(trigger)

    # 결과 검증: 저장된 리포트 파일 → 우리 파서로 재검증 (실파이프라인 산출물 검증)
    import json
    from evaluation.signal_parser import parse_final_result
    reports_dir = Path(__file__).parent / "agents_workspace" / "reports"
    stamp = f"{trigger_date}_09-00-00.json"
    saved = sorted(reports_dir.rglob(stamp)) if reports_dir.exists() else []
    print(f"\nLLM 모의 호출 {calls['n']}회 — 단계별: {calls['by_kind']}")
    print(f"리포트 파일 저장: {len(saved)}개")
    ok = 0
    for p in saved:
        d = json.loads(p.read_text())
        r = parse_final_result(d.get("final_result") or "")
        ok += len(r.valid_signals)
        print(f"  {p.parent.name}: well-formed {sum(1 for s in r.signals if s.is_well_formed)}, valid {len(r.valid_signals)}")
    print(f"파서 검증 합계: 유효 신호 {ok}건")
    if len(saved) >= 3 and ok >= 3:
        note = "" if calls["n"] else " (리포트 캐시 재사용 — LLM 호출 생략됨)"
        print(f"\n✅ 드라이런 완주 — 조립 전 구간 이상 없음{note}")
    else:
        print("\n⚠️ 드라이런 불완전 — 위 수치 확인 필요")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "2026-05-29"))
