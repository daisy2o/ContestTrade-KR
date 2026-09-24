"""
근거 은행(evidence bank) — 모델이 인용문을 재작성하지 못하게 하는 구조.

현행 구조는 모델에게 "약 100단어로 근거를 서술"하게 해서, 재서술 과정 자체가
수치·날짜·한정어 왜곡의 경로가 된다. 이 모듈은 고정 입력을 **문장/필드 단위로
분해해 식별자를 붙이고**, 모델은 식별자만 고르게 한다. 인용문·출처·시각은
프로그램이 입력에서 채운다.

용어 주의: S 단위는 **팩터 요약의 문장**이지 기사·텔레그램 원문이 아니다.
"인용이 정확하다"는 것은 그 요약 문장과 일치한다는 뜻이며, 요약 자체의 정확성과
기사 원문까지의 연결은 별도로 남는 문제다.

두 종류의 근거 단위:
- `S{n}` : 팩터 요약의 한 문장 (소스명·요약 문장·그 문장이 인용한 doc id)
- `T{n}` : 도구 호출 결과의 한 필드 (도구명·종목·필드명·값 문자열 그대로)

주의: 인용이 정확해도 해석은 틀릴 수 있다(합산 수치를 정확히 인용한 뒤 한 회사
실적으로 해석하는 등). 그래서 모델의 해석은 별도 필드로 받고, 원문이 그 해석을
뒷받침하는지는 감사에서 따로 판정한다. 이 구조는 재서술 오류를 줄일 뿐
해석 오류를 막지 못한다.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List

SENT_SPLIT = re.compile(r"(?<=[.!?。])\s+|\n+")
TOOL_CALL_RE = re.compile(r'\{"tool_called":\s*(\{.*?\}),\s*"tool_result":\s*(\{.*?\})\}(?=\s*\{"tool_called"|\s*$)', re.S)


@dataclass
class EvidenceUnit:
    uid: str
    kind: str          # "summary" | "tool"
    source: str        # 소스명 또는 도구명
    text: str          # 입력 문장/필드 그대로 (모델이 다시 쓰지 못하게 프로그램이 보관)
    meta: Dict = field(default_factory=dict)

    def to_line(self) -> str:
        if self.kind == "tool":
            return f"[{self.uid}] ({self.source}/{self.meta.get('symbol','')}) {self.text}"
        return f"[{self.uid}] ({self.source}) {self.text}"


def _split_summary(bg: str) -> List[tuple]:
    """background_information에서 (소스명, 문장) 목록 추출."""
    out = []
    for m in re.finditer(r"<source>(.*?)</source>.*?<content>(.*?)</content>", bg, re.S):
        src, content = m.group(1).strip(), m.group(2)
        for raw in SENT_SPLIT.split(content):
            s = raw.strip(" -*#\t")
            if len(s) < 20:
                continue
            # 제목·머리글(콜론으로 끝나고 서술이 없는 줄)은 근거 단위가 아니다
            if s.rstrip("*: ").endswith(("관련 정보", "Summary", "요약", "동향", "정보")) or \
               (s.endswith(":") and not re.search(r"\d", s)):
                continue
            out.append((src, s))
    return out


def _split_tools(tcc: str) -> List[tuple]:
    """tool_call_context에서 (도구명, 종목, 필드, 값문자열) 목록 추출."""
    out = []
    for m in TOOL_CALL_RE.finditer(tcc or ""):
        try:
            called = json.loads(m.group(1))
            result = json.loads(m.group(2))
        except Exception:
            continue
        tool = called.get("tool_name", "?")
        props = called.get("properties", {}) or {}
        symbol = props.get("symbol", "") or ",".join(props.get("queries", []) or [])
        data = result.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {"raw": data[:200]}
        if not isinstance(data, dict):
            continue
        META_KEYS = {"symbol", "name", "as_of_last_bar", "as_of", "window_note", "universe_size"}
        asof = data.get("as_of_last_bar") or data.get("as_of") or ""
        def emit(key, val, prefix=""):
            full = f"{prefix}{key}"
            if full in META_KEYS:
                return
            out.append((tool, str(symbol), full, f"{full} = {val} (기준일 {asof})" if asof else f"{full} = {val}"))
        for k, v in data.items():
            if isinstance(v, dict):
                # derived_returns 같은 중첩 수치는 평탄화 — 핵심 근거가 빠지면 안 된다
                if k in ("derived_returns",):
                    for k2, v2 in v.items():
                        emit(k2, v2)
                continue          # 일별 OHLCV 등 대형 구조는 단위로 쪼개지 않음
            if isinstance(v, list):
                continue
            emit(k, v)
    return out


def build_bank(background_information: str, tool_call_context: str) -> Dict[str, EvidenceUnit]:
    bank: Dict[str, EvidenceUnit] = {}
    for i, (src, sent) in enumerate(_split_summary(background_information), 1):
        uid = f"S{i}"
        bank[uid] = EvidenceUnit(uid, "summary", src, sent,
                                 {"cited_doc_ids": re.findall(r"\[(?:doc\s*id\s*=\s*)?(\d+)\]", sent)})
    for i, (tool, symbol, fieldname, text) in enumerate(_split_tools(tool_call_context), 1):
        uid = f"T{i}"
        bank[uid] = EvidenceUnit(uid, "tool", tool, text, {"symbol": symbol, "field": fieldname})
    return bank


def render_bank(bank: Dict[str, EvidenceUnit], max_chars: int = 24000) -> str:
    """모델에게 보여줄 근거 목록. 모델은 여기서 uid만 고른다."""
    lines, total = [], 0
    for uid, u in bank.items():
        line = u.to_line()
        if total + len(line) > max_chars:
            lines.append(f"... (이하 {len(bank) - len(lines)}개 생략)")
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def resolve(bank: Dict[str, EvidenceUnit], uid: str) -> dict:
    """모델이 고른 uid를 입력 문장·메타데이터로 확장 (프로그램이 채움).

    kind="summary"면 quote는 팩터 요약 문장이며 기사 원문이 아니다."""
    u = bank.get(uid)
    if u is None:
        return {"uid": uid, "valid": False, "error": "존재하지 않는 근거 ID"}
    return {"uid": uid, "valid": True, "kind": u.kind, "source": u.source,
            "quote": u.text, "meta": u.meta}
