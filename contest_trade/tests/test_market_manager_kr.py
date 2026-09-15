"""KR-Stock integration tests for utils/market_manager.py.

Offline tests cover config parsing, cost math, and symbol-pool logic.
Tests touching FDR (calendar, prices) are marked `network` — they run by
default in dev (network available) and can be excluded with `-m "not network"`.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.market_manager import (
    KRStockTradingConfig,
    Market,
    MarketManager,
    MarketManagerConfig,
)

KR_CONFIG_PATH = str(Path(__file__).parent.parent / "config" / "market_config_kr.yaml")


@pytest.fixture(scope="module")
def kr_manager():
    config = MarketManagerConfig.from_config_file(KR_CONFIG_PATH)
    return MarketManager(config)


# ---- offline ----

def test_kr_market_enum():
    assert Market("KR-Stock") == Market.KR


def test_config_parses_kr_trading_costs(kr_manager):
    cfg = kr_manager.get_trading_config("KR-Stock")
    assert isinstance(cfg, KRStockTradingConfig)
    assert cfg.transaction_tax_rate == 0.0015
    assert cfg.min_shares == 1


def test_kr_costs_sell_includes_tax(kr_manager):
    amount = 10_000_000  # 1천만원
    buy = kr_manager.calculate_trading_costs("KR-Stock", "buy", 100, 100_000, "005930")
    sell = kr_manager.calculate_trading_costs("KR-Stock", "sell", 100, 100_000, "005930")
    assert buy["stamp_tax"] == 0.0
    assert sell["stamp_tax"] == pytest.approx(amount * 0.0015)
    assert sell["total_cost"] > buy["total_cost"]


def test_kr_tradable_shares_single_share(kr_manager):
    # 1주 단위: 최소 거래 단위 제약 없음
    assert kr_manager.calculate_tradable_shares("KR-Stock", 150_000, 100_000) == 1


def test_kr_symbols_from_custom_list(kr_manager):
    df = kr_manager.get_market_symbols(Market.KR, "2025-01-06 09:00:00", full_market=True)
    assert "005930" in df["ts_code"].values
    assert len(df) >= 5


def test_kr_symbols_guard_without_custom_list():
    empty = MarketManagerConfig(target_markets=["KR-Stock"], custom_symbols=[], trading_configs={})
    mgr = MarketManager(empty)
    with pytest.raises(ValueError, match="point-in-time"):
        mgr.get_market_symbols(Market.KR, "2025-01-06 09:00:00", full_market=True)


def test_kr_is_available_symbol(kr_manager):
    assert kr_manager.is_available_symbol("KR-Stock", "005930")


# ---- network (FDR) ----

@pytest.mark.network
def test_kr_trade_date(kr_manager):
    dates = kr_manager.get_trade_date("KR-Stock")
    assert "20250102" in dates and "20250101" not in dates


@pytest.mark.network
def test_kr_is_market_trading(kr_manager):
    assert kr_manager.is_market_trading("KR-Stock", "2025-01-06 09:00:00") is True
    assert kr_manager.is_market_trading("KR-Stock", "2025-01-01 09:00:00") is False


@pytest.mark.network
def test_kr_symbol_price(kr_manager):
    p = kr_manager.get_symbol_price("KR-Stock", "005930", "2025-01-06 09:00:00")
    assert p is not None
    assert p["trade_date"] == "20250106"
    assert p["close"] > 0 and p["pre_close"] > 0
    assert p["limit_price"] == pytest.approx(p["pre_close"] * 1.3)


@pytest.mark.network
def test_kr_symbol_price_non_trading_day_is_none(kr_manager):
    # 2025-01-01 신정 휴장 — date_diff=0이면 assert 실패로 ValueError가 아니라
    # trade_dates 미포함이라 AssertionError 경로 → 예외 발생을 확인
    with pytest.raises(Exception):
        kr_manager.get_symbol_price("KR-Stock", "005930", "2025-01-01 09:00:00")


@pytest.mark.network
def test_kr_history_price(kr_manager):
    df = kr_manager.get_symbol_history_price("KR-Stock", "005930", "20250102", "20250115")
    assert df is not None and len(df) == 10
