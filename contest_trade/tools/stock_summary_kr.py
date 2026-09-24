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
    # 값을 단위가 박힌 문자열로 반환한다: 비율(1.8059)을 퍼센트(1.81%)로 오독하는
    # 사고가 실측으로 확인됨 — 필드명(pct_)과 값 형식의 불일치가 원인이었다.
    pct = lambda x: None if x is None else f"{x * 100:+.2f}%"
    ret = lambda n: float(close.iloc[-1] / close.iloc[-1 - n] - 1) if len(close) > n else None
    vol20 = float(close.pct_change().tail(20).std())
    name = load_universe().get(symbol, symbol)
    last_bar = df.index[-1].strftime("%Y-%m-%d")  # 조회 종료일이 아니라 실제 마지막 봉
    return {
        "symbol": symbol,
        "name": name,
        "as_of_last_bar": last_bar,
        "window_note": f"모든 수익률은 거래일 기준, 최종 봉 {last_bar} 종가 대비",
        "last_close_krw": float(close.iloc[-1]),
        "return_1_trading_day": pct(ret(1)),
        "return_5_trading_days": pct(ret(5)),
        "return_20_trading_days": pct(ret(20)),
        "volatility_20d_daily": pct(vol20),
        "pct_below_120d_high": pct(float(close.iloc[-1] / close.max() - 1)),
        "pct_above_120d_low": pct(float(close.iloc[-1] / close.min() - 1)),
        "volume_5d_vs_20d": f"{float(df['Volume'].tail(5).mean() / df['Volume'].tail(20).mean()):.2f}배"
                            f" ({'평균 이상' if df['Volume'].tail(5).mean() > df['Volume'].tail(20).mean() else '평균 이하'})",
    }
