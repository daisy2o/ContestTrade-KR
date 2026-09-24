"""
입력 변경 대조 사례(counterfactual) 생성 — 검수 모델이 실제로 입력을 읽는지 진단.

같은 주장을 두고 **근거만** 바꾼다: 회사 / 기준일 / 합산 범위.
바뀐 근거에 맞춰 판정도 바뀌면 입력을 읽는 것이고, 원본과 같은 판정을 유지하면
자기 기억이나 표면 패턴으로 답할 가능성이 있다.

한계: 이것은 오염이 없다는 증명이 아니다. 컷오프 표만 보는 것보다 직접적인 점검일 뿐이며,
판정이 바뀌어도 "다른 이유로 바뀐" 경우를 배제하지 못한다.

본 시험과 조건이 다르다는 점도 명시한다: 본 시험의 source는 '고정 입력 전체'이지만
여기서는 변조 지점을 특정해야 하므로 **근거 문장 하나**를 source로 쓴다.
따라서 이 세트의 점수는 본 시험 점수와 직접 비교하지 않는다.

출력: evaluation/out/counterfactual_set.json
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

REPORTS = ROOT / "agents_workspace" / "reports"
OUT = ROOT / "evaluation" / "out" / "counterfactual_set.json"

# 변조에 쓸 대체 종목 (원본과 다른 회사)
SWAP = {"삼성전자": "SK하이닉스", "SK하이닉스": "삼성전자", "POSCO홀딩스": "현대차",
        "005930": "000660", "000660": "005930", "005490": "005380"}


def pick_tool_units(bank, want=4):
    """수치가 든 도구 단위를 고른다 (변조 지점이 명확한 것)."""
    out = []
    for uid, u in bank.items():
        if u.kind != "tool":
            continue
        if not re.search(r"[+-]?\d+\.\d+%", u.text):
            continue
        if "기준일" not in u.text:
            continue
        out.append((uid, u))
        if len(out) >= want:
            break
    return out


def make_cases(agent: str, bank) -> list:
    from evaluation.evidence_bank import EvidenceUnit  # noqa: F401
    cases = []
    for uid, u in pick_tool_units(bank):
        sym = u.meta.get("symbol", "")
        field = u.meta.get("field", "")
        m = re.search(r"([+-]?\d+\.\d+%)", u.text)
        val = m.group(1) if m else ""
        d = re.search(r"기준일 (\d{4}-\d{2}-\d{2})", u.text)
        base_date = d.group(1) if d else ""
        window = {"return_1_trading_day": "1거래일", "return_5_trading_days": "5거래일",
                  "return_20_trading_days": "20거래일"}.get(field, field)
        claim = f"{sym} 종목의 {window} 수익률은 {val}이다 (기준일 {base_date})."

        # 원본 — 근거가 주장을 그대로 지지
        cases.append({"id": f"CF-{agent}-{uid}-orig", "variant": "원본", "agent": agent,
                      "source_text": f"[{sym}] {u.text}", "claim": claim, "gold": "clean",
                      "expect": "지지됨"})
        # 변조 A — 회사만 교체
        other = SWAP.get(sym, "005380")
        cases.append({"id": f"CF-{agent}-{uid}-company", "variant": "회사변경", "agent": agent,
                      "source_text": f"[{other}] {u.text.replace(sym, other)}", "claim": claim,
                      "gold": "problem", "expect": "반박됨 또는 근거부족",
                      "note": f"근거의 회사를 {sym}→{other}로 바꿈. 주장은 {sym} 그대로"})
        # 변조 B — 기준일만 교체
        if base_date:
            other_date = base_date[:-2] + ("01" if base_date[-2:] != "01" else "02")
            cases.append({"id": f"CF-{agent}-{uid}-date", "variant": "날짜변경", "agent": agent,
                          "source_text": f"[{sym}] " + u.text.replace(base_date, other_date),
                          "claim": claim, "gold": "problem", "expect": "반박됨 또는 근거부족",
                          "note": f"근거의 기준일을 {base_date}→{other_date}로 바꿈"})
        # 변조 C — 합산 범위로 교체 (주장은 단독)
        cases.append({"id": f"CF-{agent}-{uid}-aggregate", "variant": "합산범위변경", "agent": agent,
                      "source_text": f"[{sym}+{other} 합산] {u.text} — 이 수치는 두 종목 합산 기준이다",
                      "claim": claim, "gold": "problem", "expect": "반박됨 또는 근거부족",
                      "note": "근거를 두 회사 합산으로 바꿈. 주장은 단독 회사"})
    return cases


def main():
    from evaluation.evidence_bank import build_bank
    cases = []
    for p in sorted(REPORTS.rglob("2026-05-07_*.json")):   # 개발용 날짜에서만
        d = json.loads(p.read_text())
        bank = build_bank(d.get("background_information", ""), d.get("tool_call_context", ""))
        cases += make_cases(p.parent.name, bank)
    from collections import Counter
    OUT.write_text(json.dumps({
        "meta": {"note": "입력 변경 대조 진단 세트. 본 시험(source=고정 입력 전체)과 조건이 달라 "
                         "점수를 직접 비교하지 않는다. 오염 부재의 증명이 아니라 입력 참조 여부 진단.",
                 "source_date": "2026-05-07 (개발용)"},
        "cases": cases}, ensure_ascii=False, indent=1))
    print(f"대조 사례 {len(cases)}건:", dict(Counter(c["variant"] for c in cases)))
    print(f"저장: {OUT}")


if __name__ == "__main__":
    main()
