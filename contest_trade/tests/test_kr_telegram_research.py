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


# ── 별칭 테이블 (aliases_ktop30.csv + 제외어 가드) ──

def _alias_table():
    from utils.kr_universe import load_universe, load_alias_table
    return load_universe(), load_alias_table(load_universe())


def test_alias_matches_nickname():
    uni, table = _alias_table()
    assert tag_stock_codes("삼전 목표주가 상향", uni, table) == ["005930"]
    assert tag_stock_codes("네이버 광고 실적 개선", uni, table) == ["035420"]
    assert tag_stock_codes("하닉 HBM 수주", uni, table) == ["000660"]


def test_exclude_guard_blocks_false_positive():
    uni, table = _alias_table()
    # 별도 상장사·계열사 언급은 매핑하지 않는다
    assert tag_stock_codes("카카오뱅크 대출 성장", uni, table) == []
    assert tag_stock_codes("현대차증권 리포트 발간", uni, table) == []
    assert tag_stock_codes("포스코퓨처엠 양극재 증설", uni, table) == []
    # 가드 없는 본체 언급은 정상 매핑
    assert tag_stock_codes("카카오 톡비즈 매출", uni, table) == ["035720"]
    assert tag_stock_codes("포스코 철강 시황", uni, table) == ["005490"]


def test_alias_conflict_raises():
    from utils.kr_universe import load_alias_table
    import pandas as pd
    uni = {"005930": "삼성전자", "000660": "SK하이닉스"}
    bad = pd.DataFrame([
        {"alias": "삼전", "ticker": "005930", "exclude": "", "note": ""},
        {"alias": "삼전", "ticker": "000660", "exclude": "", "note": ""},
    ])
    import tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8-sig") as f:
        bad.to_csv(f.name, index=False)
        path = f.name
    try:
        import pytest
        with pytest.raises(ValueError, match="별칭 충돌"):
            load_alias_table(uni, alias_csv_path=path)
    finally:
        os.unlink(path)


def test_backward_compat_without_table():
    # 테이블 없이 호출하면 기존 동작(정식명 매칭) 유지
    assert tag_stock_codes("카카오 목표주가", UNIVERSE) == ["035720"]
