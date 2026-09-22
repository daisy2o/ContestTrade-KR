"""
D6 — Factiva 뉴스 데이터 소스 (역할 B).

원본: data_collection/data/factiva/factiva_news.sqlite (factiva_ingest.py 산출)
종목 매핑 2단계: ① Factiva CO 코드 ↔ universe.csv의 factiva_co_code (정확 매칭)
              ② CO 매칭 실패 시 제목·본문의 종목명 문자열 매칭 (kr_telegram_research와 동일 규칙)

시각 규율: PD는 날짜만 제공(장중/장후 구분 불가) → **D+1 규칙(D45)**:
발행일 D의 기사는 D+1 00:00부터 사용 가능. DART 공시(kr_dart_disclosure)와 동일 원칙.

본문 절단: 팀 규칙 5 — 제목 + 본문 앞 300자 (한국어 토큰 비용 통제).
"""
import sqlite3
from datetime import timedelta
from pathlib import Path

import pandas as pd

from data_source.kr_data_source_base import KRDataSourceBase
from data_source.kr_telegram_research import tag_stock_codes

DB_PATH = Path(__file__).parents[2] / "data_collection" / "data" / "factiva" / "factiva_news.sqlite"

BODY_CHARS = 300  # 팀 규칙 5
LOOKBACK_DAYS = 3


class KrFactivaNews(KRDataSourceBase):
    def __init__(self, universe_names: dict | None = None, factiva_code_map: dict | None = None, cache_dir=None):
        """universe_names/factiva_code_map이 None이면 유니버스 CSV에서 자동 로드 (파이프라인 무인자 경로)."""
        super().__init__("kr_factiva_news", cache_dir=cache_dir)
        if universe_names is None or factiva_code_map is None:
            from utils.kr_universe import load_universe, load_factiva_code_map
            universe_names = universe_names or load_universe()
            factiva_code_map = factiva_code_map or load_factiva_code_map()
        self.universe_names = universe_names
        self.factiva_code_map = {k.lower(): v for k, v in factiva_code_map.items()}

    def _map_stocks(self, co_codes: str, text: str) -> list:
        # ① CO 코드 정확 매칭
        found = []
        for fc in (co_codes or "").split(","):
            code = self.factiva_code_map.get(fc.strip().lower())
            if code and code in self.universe_names and code not in found:
                found.append(code)
        # ② 폴백: 종목명 문자열 매칭 (factiva 코드 없는 종목 커버)
        if not found:
            found = tag_stock_codes(text, self.universe_names)
        return found

    def fetch_raw(self, trigger_time: str) -> pd.DataFrame:
        if not DB_PATH.exists():
            raise RuntimeError(
                f"Factiva DB가 없습니다: {DB_PATH}\n"
                "data_collection/factiva_ingest.py 를 먼저 실행하세요."
            )
        trigger_dt = pd.to_datetime(trigger_time)
        # D+1 규칙: trigger 당일 사용 가능한 최신 기사는 어제 발행분
        end_pd = (trigger_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        start_pd = (trigger_dt - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

        conn = sqlite3.connect(DB_PATH)
        try:
            raw = pd.read_sql_query(
                "SELECT an, pd_date, source, headline, body, co_codes FROM articles "
                "WHERE pd_date >= ? AND pd_date <= ?",
                conn, params=[start_pd, end_pd],
            )
        finally:
            conn.close()

        rows = []
        for _, r in raw.iterrows():
            codes = self._map_stocks(r["co_codes"], f"{r['headline']} {r['body'][:500]}")
            if not codes:
                continue
            usable_from = (pd.to_datetime(r["pd_date"]) + timedelta(days=1)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            names = ", ".join(self.universe_names[c] for c in codes)
            rows.append({
                "title": f"[{r['source']}] ({names}) {r['headline']}",
                "content": r["body"][:BODY_CHARS],
                "pub_time": usable_from,
                "url": f"factiva://{r['an']}",
            })
        return pd.DataFrame(rows, columns=["title", "content", "pub_time", "url"])


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parents[1]))
    from utils.kr_universe import load_universe, load_factiva_code_map

    src = KrFactivaNews(load_universe(), load_factiva_code_map())
    df = src.get_data("2026-05-29 09:00:00")
    print(df.head(8).to_string(max_colwidth=60))
    print(f"\n{len(df)}건 / pub_time: {df['pub_time'].min()} ~ {df['pub_time'].max()}" if len(df) else "0건")
