"""Tests for evaluation/signal_parser.py — synthetic LLM outputs, no network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from evaluation.signal_parser import parse_final_result

GOOD = """
Some preamble the model wrote.
<signals>
<signal>
<has_opportunity>yes</has_opportunity>
<action>buy</action>
<symbol_code>005930.KS</symbol_code>
<symbol_name>삼성전자</symbol_name>
<evidence_list>
<evidence>HBM demand surge</evidence>
<evidence>Buyback announced</evidence>
</evidence_list>
<probability>72</probability>
</signal>
<signal>
<has_opportunity>no</has_opportunity>
</signal>
</signals>
"""

MALFORMED_PROB = """
<signals>
<signal>
<has_opportunity>yes</has_opportunity>
<action>buy</action>
<symbol_code>000660.KS</symbol_code>
<probability>quite high</probability>
</signal>
</signals>
"""

BAD_ACTION = """
<signals>
<signal>
<has_opportunity>yes</has_opportunity>
<action>hold</action>
<symbol_code>035420.KS</symbol_code>
<probability>60</probability>
</signal>
</signals>
"""

PROSE_ONLY = "The market looks uncertain; I would not recommend anything today."


def test_good_output():
    r = parse_final_result(GOOD)
    assert r.raw_block_count == 2
    valid = r.valid_signals
    assert len(valid) == 1
    s = valid[0]
    assert s.action == "buy" and s.symbol_code == "005930.KS"
    assert s.probability == 72.0 and s.evidence_count == 2
    # "no opportunity" block is not an error, just not scoreable
    assert r.signals[1].has_opportunity is False and not r.signals[1].is_valid


def test_probability_text_recorded_as_error():
    r = parse_final_result(MALFORMED_PROB)
    assert r.raw_block_count == 1
    assert r.valid_signals == []
    assert any("probability" in e for e in r.signals[0].errors)


def test_action_outside_vocab_rejected():
    r = parse_final_result(BAD_ACTION)
    assert r.valid_signals == []
    assert any("action" in e for e in r.signals[0].errors)


def test_prose_only_gives_none_rate():
    r = parse_final_result(PROSE_ONLY)
    assert r.raw_block_count == 0
    assert r.parse_success_rate is None  # distinct from 0%: no blocks at all


def test_probability_out_of_range():
    text = GOOD.replace("<probability>72</probability>", "<probability>140</probability>")
    r = parse_final_result(text)
    assert r.valid_signals == []


def test_success_rate_counts_well_formed_not_scoreable():
    """An honest no-opportunity block is a parse SUCCESS for the gate metric."""
    r = parse_final_result(GOOD)
    assert r.raw_block_count == 2
    assert r.parse_success_rate == 1.0   # both well-formed
    assert len(r.valid_signals) == 1     # but only one scoreable


def test_success_rate_accounting():
    text = GOOD + MALFORMED_PROB
    r = parse_final_result(text)
    assert r.raw_block_count == 3
    assert r.parse_success_rate == 2 / 3  # malformed prob block fails format


def test_unclosed_block_counted_as_failure():
    """9-15 리뷰: 닫히지 않은 블록이 분모에서 사라지면 성공률 과대."""
    text = GOOD + "\n<signal><has_opportunity>yes</has_opportunity><action>buy"
    r = parse_final_result(text)
    assert r.raw_block_count == 3  # 2 closed + 1 unclosed attempt
    assert r.parse_success_rate == 2 / 3
    assert any("unclosed" in e for s in r.signals for e in s.errors)


def test_probability_strict_single_number():
    """9-15 리뷰: '80 to 90'→80, '1e2'→1 같은 관대한 해석 금지."""
    for bad in ["80 to 90", "1e2", "high", "70-80"]:
        text = GOOD.replace("<probability>72</probability>", f"<probability>{bad}</probability>")
        r = parse_final_result(text)
        assert r.valid_signals == [], bad
    ok = GOOD.replace("<probability>72</probability>", "<probability>85%</probability>")
    assert parse_final_result(ok).valid_signals[0].probability == 85.0
