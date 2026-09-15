"""
KR (Korea/KOSPI) market data utilities.

Design goals (see DaisyVault: 어댑터_인터페이스_명세):
1. Pluggable backend — development uses the free FinanceDataReader (FDR) source;
   once KRX account / OpenAPI keys are available, a KRXBackend can be swapped in
   without touching callers.
2. Local disk cache keyed by (function, args) so that historical replays are
   reproducible and offline after the first fetch.
3. KR trading calendar derived from KOSPI index history (KRX has no free
   calendar endpoint; index trading days ARE the calendar).

Point-in-time discipline: every public function takes explicit date arguments.
Nothing in this module may silently use "today".
"""
import hashlib
import pickle
from pathlib import Path
from typing import List, Optional

import pandas as pd

DEFAULT_KR_CACHE_DIR = Path(__file__).parent / "kr_cache"

KOSPI_INDEX = "KOSPI"  # logical name; each backend maps it to its own symbol


class KRDataBackend:
    """Interface for KR market data backends."""

    def cache_identity(self) -> str:
        """Identity string baked into every cache key. Subclasses with
        configurable endpoints MUST extend this (e.g. include endpoint/host),
        so two differently-configured instances never share cache entries."""
        return type(self).__name__

    def get_ohlcv(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Daily OHLCV, index=DatetimeIndex, cols: Open/High/Low/Close/Volume."""
        raise NotImplementedError

    def get_index_ohlcv(self, index_symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        raise NotImplementedError

    def get_stock_listing(self) -> pd.DataFrame:
        """Current full KRX listing (Code, Name, Market)."""
        raise NotImplementedError


class FDRBackend(KRDataBackend):
    """FinanceDataReader backend — free, no credentials. Development default."""

    def get_ohlcv(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        import FinanceDataReader as fdr
        return fdr.DataReader(symbol, start_date, end_date)

    INDEX_SYMBOLS = {"KOSPI": "KS11", "KOSDAQ": "KQ11", "KOSPI200": "KS200"}

    def get_index_ohlcv(self, index_name: str, start_date: str, end_date: str) -> pd.DataFrame:
        import FinanceDataReader as fdr
        symbol = self.INDEX_SYMBOLS.get(index_name, index_name)
        return fdr.DataReader(symbol, start_date, end_date)

    def get_stock_listing(self) -> pd.DataFrame:
        import FinanceDataReader as fdr
        return fdr.StockListing("KRX")


class CachedKRClient:
    """Disk-cached KR data client with a swappable backend."""

    def __init__(self, backend: Optional[KRDataBackend] = None, cache_dir=None):
        self.backend = backend or FDRBackend()
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_KR_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, name: str, *args) -> Path:
        # Backend identity is part of the key: switching FDR -> KRX must never
        # silently serve data fetched by the old backend or another endpoint.
        backend_id = self.backend.cache_identity()
        raw = backend_id + "|" + name + "|" + "|".join(str(a) for a in args)
        digest = hashlib.md5(raw.encode()).hexdigest()
        return self.cache_dir / f"{backend_id}_{name}_{digest}.pkl"

    def _cached(self, name: str, fetch_fn, *args):
        cache_file = self._cache_key(name, *args)
        if cache_file.exists():
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        result = fetch_fn(*args)
        # Never cache empty results: a transient network hiccup must not be
        # frozen into the replayable record. Deliberate trade-off: genuinely
        # empty ranges (long holidays) are re-fetched every call — correctness
        # over performance; revisit only if profiling shows it matters.
        is_empty = result is None or (isinstance(result, pd.DataFrame) and result.empty)
        if not is_empty:
            with open(cache_file, "wb") as f:
                pickle.dump(result, f)
        return result

    # ---- public API ----

    def get_ohlcv(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._cached("ohlcv", self.backend.get_ohlcv, symbol, start_date, end_date)

    def get_index_ohlcv(self, index_symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._cached("index", self.backend.get_index_ohlcv, index_symbol, start_date, end_date)

    def get_trade_dates(self, start_date: str, end_date: str) -> List[str]:
        """KR trading days as YYYYMMDD strings, derived from KOSPI index history."""
        df = self.get_index_ohlcv(KOSPI_INDEX, start_date, end_date)
        if df is None or df.empty:
            return []
        dates = [d.strftime("%Y%m%d") for d in df.index]
        # Sanity check: a silent gap in the index source would masquerade as
        # holidays. KR has ~245 trading days per year; warn outside 230-260/yr.
        span_years = max((pd.to_datetime(end_date) - pd.to_datetime(start_date)).days / 365.25, 1e-9)
        per_year = len(dates) / span_years
        if span_years >= 0.9 and not (230 <= per_year <= 260):
            from loguru import logger
            logger.warning(
                f"KR calendar sanity check: {per_year:.0f} trading days/year over "
                f"{span_years:.1f}y — possible data gap in index source."
            )
        return dates

    def get_previous_trading_date(self, date_yyyymmdd: str, lookback_start: str = "2018-01-01") -> Optional[str]:
        """Latest trading day strictly before the given date."""
        if date_yyyymmdd <= lookback_start.replace("-", ""):
            raise ValueError(
                f"date {date_yyyymmdd} is not after lookback_start {lookback_start}; "
                "pass an earlier lookback_start explicitly."
            )
        end = pd.to_datetime(date_yyyymmdd).strftime("%Y-%m-%d")
        dates = self.get_trade_dates(lookback_start, end)
        prior = [d for d in dates if d < date_yyyymmdd]
        return prior[-1] if prior else None

    def get_kospi200_constituents(self, as_of_date: str) -> List[str]:
        """Point-in-time KOSPI200 membership as of a date.

        TODO(D13): reconstruct from KRX rebalancing disclosures (정기변경 6/12월 +
        수시변경). Until that table is built this raises, so nothing can silently
        use current membership for a past date (survivorship bias guard).
        """
        raise NotImplementedError(
            "Point-in-time KOSPI200 table not built yet (D13). "
            "Do NOT fall back to current membership for historical dates."
        )


GLOBAL_KR_CLIENT = CachedKRClient()


if __name__ == "__main__":
    client = GLOBAL_KR_CLIENT
    df = client.get_ohlcv("005930", "2025-01-01", "2025-01-15")
    print("Samsung OHLCV:", len(df), "rows")
    dates = client.get_trade_dates("2025-01-01", "2025-01-31")
    print("Jan 2025 KR trading days:", dates)
    print("prev trading day of 20250106:", client.get_previous_trading_date("20250106"))
