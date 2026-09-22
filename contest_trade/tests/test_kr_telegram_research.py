"""Tests for D41 stock-tagging logic (pure function, offline)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data_source.kr_telegram_research import tag_stock_codes

UNIVERSE = {"005930": "삼성전자", "000660": "SK하이닉스", "035720": "카카오"}


def test_code_in_parentheses_matched():
    assert tag_stock_codes("** 카카오(035720) 투자의견 매수", UNIVERSE) == ["035720"]


def test_multiple_codes_dedup_and_order():
    text = "삼성전자(005930), SK하이닉스(000660) 반도체 강세, 005930 재언급"
    assert tag_stock_codes(text, UNIVERSE) == ["005930", "000660"]


def test_code_outside_universe_ignored():
    assert tag_stock_codes("엔비디아(999999) 강세", UNIVERSE) == []


def test_fallback_to_name_when_no_code():
    assert tag_stock_codes("카카오 목표주가 상향", UNIVERSE) == ["035720"]


def test_no_match_returns_empty():
    assert tag_stock_codes("코스피 지수 전반 강세", UNIVERSE) == []


def test_empty_text():
    assert tag_stock_codes("", UNIVERSE) == []
    assert tag_stock_codes(None, UNIVERSE) == []
