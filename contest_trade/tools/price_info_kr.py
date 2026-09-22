"""KR Price Info Tool — 판단용 시세 조회 (전 거래일까지만, D22 §5-0 look-ahead 차단)."""
import pandas as pd
from datetime import timedelta
from pydantic import BaseModel, Field

from tools.tool_utils import smart_tool
from utils.kr_data_utils import GLOBAL_KR_CLIENT


class PriceInfoInput(BaseModel):
    market: str = Field(description="The market of the company. e.g., KR-Stock")
    symbol: str = Field(description="6-digit KR stock code. Only one symbol is allowed.")
    trigger_time: str = Field(description="The trigger time. Format: YYYY-MM-DD HH:MM:SS.")


@smart_tool(
    description="Get recent daily price (OHLCV) of a Korean stock. "
                "Returns data strictly BEFORE the trigger date (no same-day leakage).",
    args_schema=PriceInfoInput,
    max_output_len=4000,
    timeout_seconds=15.0,
)
async def price_info(market: str, symbol: str, trigger_time: str = None) -> dict:
    trigger_dt = pd.to_datetime(trigger_time)
    # 판단 경로: 당일 시세 금지 → 전일까지 (D22 §5-0)
    end = (trigger_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    start = (trigger_dt - timedelta(days=45)).strftime("%Y-%m-%d")
    df = GLOBAL_KR_CLIENT.get_ohlcv(symbol, start, end)
    if df is None or df.empty:
        return {"error": f"No price data for {symbol} in [{start}, {end}]"}
    out = df.tail(20).copy()
    out.index = out.index.strftime("%Y-%m-%d")
    return {
        "symbol": symbol,
        "as_of": end,
        "last_close": float(df["Close"].iloc[-1]),
        "recent_daily": out[["Open", "High", "Low", "Close", "Volume"]].to_dict(orient="index"),
    }
