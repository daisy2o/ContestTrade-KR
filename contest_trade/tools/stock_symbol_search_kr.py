"""KR Symbol Search Tool — 유니버스 CSV 기반 이름↔코드 확정 (LLM 추측 금지 원칙)."""
from typing import List
from pydantic import BaseModel, Field

from tools.tool_utils import smart_tool
from utils.kr_universe import load_universe


class StockSymbolSearchInput(BaseModel):
    market: str = Field(description="The target market. e.g., KR-Stock")
    queries: List[str] = Field(description="Company names or 6-digit codes (partial match supported)")
    trigger_time: str = Field(description="The trigger time. Format: YYYY-MM-DD HH:MM:SS")


@smart_tool(
    description="Resolve Korean company names to 6-digit stock codes within the "
                "research universe (K-TOP30). Exact master-table lookup, no guessing.",
    args_schema=StockSymbolSearchInput,
    max_output_len=2000,
    timeout_seconds=5.0,
)
async def stock_symbol_search(market: str, queries: List[str], trigger_time: str = None) -> dict:
    universe = load_universe()  # {code: name}
    results = {}
    for q in queries:
        q = str(q).strip()
        hits = [
            {"symbol": code, "name": name}
            for code, name in universe.items()
            if q == code or q in name or name in q
        ]
        results[q] = hits if hits else "NOT_IN_UNIVERSE"
    return {"universe_size": len(universe), "results": results}
