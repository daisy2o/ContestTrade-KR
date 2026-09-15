"""Regression tests for utils/kr_data_utils.py — network-free via FakeBackend."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.kr_data_utils import CachedKRClient, KRDataBackend, KOSPI_INDEX


def make_df(dates):
    idx = pd.to_datetime(dates)
    return pd.DataFrame(
        {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 100},
        index=idx,
    )


class FakeBackend(KRDataBackend):
    """Deterministic in-memory backend; counts fetches to test caching."""

    def __init__(self, name="FakeA", index_dates=None, empty=False):
        self._name = name
        self.fetch_count = 0
        self.empty = empty
        self.index_dates = index_dates or ["2025-01-02", "2025-01-03", "2025-01-06"]

    def cache_identity(self):
        return self._name

    def get_ohlcv(self, symbol, start_date, end_date):
        self.fetch_count += 1
        if self.empty:
            return pd.DataFrame()
        return make_df(self.index_dates)

    def get_index_ohlcv(self, index_name, start_date, end_date):
        self.fetch_count += 1
        if self.empty:
            return pd.DataFrame()
        return make_df(self.index_dates)


@pytest.fixture
def tmp_client(tmp_path):
    def _make(backend):
        return CachedKRClient(backend=backend, cache_dir=tmp_path)
    return _make


def test_cache_hit_avoids_refetch(tmp_client):
    be = FakeBackend()
    client = tmp_client(be)
    client.get_ohlcv("005930", "2025-01-01", "2025-01-10")
    client.get_ohlcv("005930", "2025-01-01", "2025-01-10")
    assert be.fetch_count == 1  # second call served from cache


def test_cache_isolated_between_backends(tmp_path):
    """Switching backends must never serve the other backend's cached data."""
    be_a, be_b = FakeBackend(name="FakeA"), FakeBackend(name="FakeB")
    CachedKRClient(backend=be_a, cache_dir=tmp_path).get_ohlcv("005930", "2025-01-01", "2025-01-10")
    CachedKRClient(backend=be_b, cache_dir=tmp_path).get_ohlcv("005930", "2025-01-01", "2025-01-10")
    assert be_a.fetch_count == 1 and be_b.fetch_count == 1  # B did its own fetch


def test_empty_results_are_not_cached(tmp_client):
    be = FakeBackend(empty=True)
    client = tmp_client(be)
    client.get_ohlcv("005930", "2025-01-01", "2025-01-10")
    client.get_ohlcv("005930", "2025-01-01", "2025-01-10")
    assert be.fetch_count == 2  # empty result refetched, never frozen


def test_trade_dates_format(tmp_client):
    client = tmp_client(FakeBackend())
    dates = client.get_trade_dates("2025-01-01", "2025-01-10")
    assert dates == ["20250102", "20250103", "20250106"]


def test_previous_trading_date(tmp_client):
    client = tmp_client(FakeBackend())
    assert client.get_previous_trading_date("20250106", lookback_start="2025-01-01") == "20250103"


def test_previous_trading_date_guard(tmp_client):
    client = tmp_client(FakeBackend())
    with pytest.raises(ValueError):
        client.get_previous_trading_date("20170103")  # before default lookback_start


def test_calendar_sanity_warning_on_gap(tmp_client, caplog):
    # One-year span with only 3 trading days must trigger the gap warning.
    be = FakeBackend(index_dates=["2025-01-02", "2025-06-02", "2025-12-02"])
    client = tmp_client(be)
    import loguru
    messages = []
    sink_id = loguru.logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        client.get_trade_dates("2025-01-01", "2025-12-31")
    finally:
        loguru.logger.remove(sink_id)
    assert any("sanity" in m for m in messages)


def test_kospi200_pit_guard(tmp_client):
    """Survivorship-bias guard: PIT membership must raise until D13 table exists."""
    client = tmp_client(FakeBackend())
    with pytest.raises(NotImplementedError):
        client.get_kospi200_constituents("20250102")


def test_index_logical_name_resolution():
    """FDR backend maps logical index names to FDR symbols."""
    from utils.kr_data_utils import FDRBackend
    assert FDRBackend.INDEX_SYMBOLS[KOSPI_INDEX] == "KS11"
