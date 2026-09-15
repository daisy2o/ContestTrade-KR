"""
Signal parser — turns a research agent's free-text final_result into
structured signals, per 측정의미론_스펙_D22 §1.

The agent output format (agents/prompts.py: prompt_for_research_invest_output_format)
is XML-ish but produced by an LLM, so this parser is deliberately tolerant:
regex block extraction, never a strict XML parser. Every failure mode is
recorded, not raised — parse failure rate is itself a base-quality metric
(condition-invariant), so the parser must always return an accounting.

Spec rules implemented here:
- No retry on failure: invalid signals are recorded and excluded downstream,
  with the denominator reported (D22 §1).
- Universe filter is NOT applied here — it needs the point-in-time table
  (D13) and lives in the evaluation layer; the parser only normalizes.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ParsedSignal:
    has_opportunity: Optional[bool] = None
    action: Optional[str] = None          # "buy" | "sell"
    symbol_code: Optional[str] = None
    symbol_name: Optional[str] = None
    probability: Optional[float] = None   # 0-100
    evidence_count: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def is_well_formed(self) -> bool:
        """Format compliance: parsed without errors. An honest
        "no opportunity" block IS well-formed — the gate criterion
        (파싱 성공률) must use this, not scoreability."""
        return not self.errors

    @property
    def is_valid(self) -> bool:
        """Scoreable for direction metrics: opportunity with action,
        symbol, and an in-range probability."""
        return (
            self.is_well_formed
            and self.has_opportunity is True
            and self.action in ("buy", "sell")
            and bool(self.symbol_code)
            and self.probability is not None
        )


@dataclass
class ParseResult:
    signals: List[ParsedSignal] = field(default_factory=list)
    raw_block_count: int = 0

    @property
    def valid_signals(self) -> List[ParsedSignal]:
        return [s for s in self.signals if s.is_valid]

    @property
    def parse_success_rate(self) -> Optional[float]:
        """GATE metric: share of raw blocks that are WELL-FORMED (format
        compliance). A correct "no opportunity" block counts as success.
        None when the text contained no signal blocks at all."""
        if self.raw_block_count == 0:
            return None
        well_formed = [s for s in self.signals if s.is_well_formed]
        return len(well_formed) / self.raw_block_count


_TAG = r"<{tag}>\s*(.*?)\s*</{tag}>"


def _field(block: str, tag: str) -> Optional[str]:
    m = re.search(_TAG.format(tag=tag), block, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else None


def _parse_bool(raw: Optional[str]) -> Optional[bool]:
    if raw is None:
        return None
    v = raw.strip().lower()
    if v in ("yes", "y", "true"):
        return True
    if v in ("no", "n", "false"):
        return False
    return None


def parse_signal_block(block: str) -> ParsedSignal:
    sig = ParsedSignal()

    raw_opp = _field(block, "has_opportunity")
    sig.has_opportunity = _parse_bool(raw_opp)
    if sig.has_opportunity is None:
        sig.errors.append(f"has_opportunity unparseable: {raw_opp!r}")

    raw_action = _field(block, "action")
    if raw_action:
        v = raw_action.strip().lower()
        if v in ("buy", "sell"):
            sig.action = v
        else:
            sig.errors.append(f"action not buy/sell: {raw_action!r}")
    elif sig.has_opportunity:
        sig.errors.append("action missing")

    sig.symbol_code = _field(block, "symbol_code") or None
    if sig.has_opportunity and not sig.symbol_code:
        sig.errors.append("symbol_code missing")
    sig.symbol_name = _field(block, "symbol_name") or None

    raw_prob = _field(block, "probability")
    if raw_prob is not None:
        # Strict: the field must be exactly one plain number (optional % suffix).
        # "80 to 90", "1e2", "high" all fail — we record, never guess (D22/9-15 리뷰).
        m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*%?\s*", raw_prob)
        if m:
            p = float(m.group(1))
            if 0.0 <= p <= 100.0:
                sig.probability = p
            else:
                sig.errors.append(f"probability out of [0,100]: {p}")
        else:
            sig.errors.append(f"probability not a single plain number: {raw_prob!r}")
    elif sig.has_opportunity:
        sig.errors.append("probability missing")

    sig.evidence_count = len(re.findall(r"<evidence>", block, re.IGNORECASE))
    return sig


def parse_final_result(text: str) -> ParseResult:
    """Parse an agent's final_result text into signals with full accounting."""
    result = ParseResult()
    if not text:
        return result
    closed_blocks = re.findall(r"<signal>(.*?)</signal>", text, re.DOTALL | re.IGNORECASE)
    # Denominator = every attempted block (open tags), so an unclosed trailing
    # block counts as a FAILED attempt instead of silently vanishing (9-15 리뷰).
    open_tags = len(re.findall(r"<signal>", text, re.IGNORECASE))
    result.raw_block_count = max(open_tags, len(closed_blocks))
    for block in closed_blocks:
        result.signals.append(parse_signal_block(block))
    for _ in range(result.raw_block_count - len(closed_blocks)):
        result.signals.append(ParsedSignal(errors=["unclosed signal block"]))
    return result
