"""KR Stock Summary Tool — 시세 기반 통계 요약 (판단용, 전 거래일까지)."""
import pandas as pd
from datetime import timedelta
from pydantic import BaseModel, Field

from tools.tool_utils import smart_tool
from utils.kr_data_utils import GLOBAL_KR_CLIENT
from utils.kr_universe import load_universe


class StockSummaryInput(BaseModel):
    market: str = Field(description="The market of the company. e.g., KR-Stock")
    symbol: str = Field(description="6-digit KR stock code.")
    trigger_time: str = Field(description="The trigger time. Format: YYYY-MM-DD HH:MM:SS.")


@smart_tool(
    description="Summarize a Korean stock's recent price behavior: returns over "
                "1/5/20 days, volatility, volume trend, distance from recent high/low. "
                "Data strictly before the trigger date.",
    args_schema=StockSummaryInput,
    max_output_len=3000,
    timeout_seconds=15.0,
)
async def stock_summary(market: str, symbol: str, trigger_time: str = None) -> dict:
    trigger_dt = pd.to_datetime(trigger_time)
    end = (trigger_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    start = (trigger_dt - timedelta(days=180)).strftime("%Y-%m-%d")
    df = GLOBAL_KR_CLIENT.get_ohlcv(symbol, start, end)
    if df is None or len(df) < 21:
        return {"error": f"Insufficient price history for {symbol}"}
    close = df["Close"]
    ret = lambda n: float(close.iloc[-1] / close.iloc[-1 - n] - 1) if len(close) > n else None
    vol20 = float(close.pct_change().tail(20).std())
    name = load_universe().get(symbol, symbol)
    return {
        "symbol": symbol,
        "name": name,
        "as_of": end,
        "last_close": float(close.iloc[-1]),
        "return_1d": ret(1),
        "return_5d": ret(5),
        "return_20d": ret(20),
        "volatility_20d_daily": vol20,
        "pct_from_120d_high": float(close.iloc[-1] / close.max() - 1),
        "pct_from_120d_low": float(close.iloc[-1] / close.min() - 1),
        "volume_ratio_5d_vs_20d": float(df["Volume"].tail(5).mean() / df["Volume"].tail(20).mean()),
    }
