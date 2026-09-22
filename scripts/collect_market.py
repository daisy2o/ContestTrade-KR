"""
시세·수급·지수 수집 (pykrx → SQLite)

필요 env 변수: 없음 (pykrx는 KRX 공개 데이터 사용)

    python scripts/collect_market.py --universe data/universe.csv --start 20260401 --end 20260630 --db data/market.sqlite

출력: data/market.sqlite (gitignore 대상)

테이블
  prices        ticker, date, open, high, low, close, volume, value, mktcap   (일별 OHLCV + 시총)
  flows         ticker, date, foreign, inst, indiv, other_corp, total          (투자자별 순매수 금액, 원)
  index_prices  index_code, date, open, high, low, close, volume, value       (KTOP30 = 5600, 필요시 KOSPI200 1028)

재실행하면 같은 (ticker,date)는 덮어씀(INSERT OR REPLACE).
"""
import argparse
import csv
import sqlite3
import time

import pandas as pd
from pykrx import stock

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
    volume REAL, value REAL, mktcap REAL, PRIMARY KEY (ticker, date));
CREATE TABLE IF NOT EXISTS flows (
    ticker TEXT, date TEXT, foreign_ REAL, inst REAL, indiv REAL, other_corp REAL, total REAL,
    PRIMARY KEY (ticker, date));
CREATE TABLE IF NOT EXISTS index_prices (
    index_code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, value REAL,
    PRIMARY KEY (index_code, date));
"""


def fmt_dates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.to_datetime(df.index).strftime("%Y-%m-%d")
    return df


def collect_ticker(con, t, start, end):
    ohlcv = fmt_dates(stock.get_market_ohlcv_by_date(start, end, t))
    cap = fmt_dates(stock.get_market_cap_by_date(start, end, t))
    rows = [
        (t, d, r["시가"], r["고가"], r["저가"], r["종가"], r["거래량"], r["거래대금"],
         cap["시가총액"].get(d))
        for d, r in ohlcv.iterrows()
    ]
    con.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?,?,?)", rows)

    flow = fmt_dates(stock.get_market_trading_value_by_date(start, end, t))
    # 컬럼: 기관합계, 기타법인, 개인, 외국인합계, 전체
    rows = [
        (t, d, r["외국인합계"], r["기관합계"], r["개인"], r["기타법인"], r["전체"])
        for d, r in flow.iterrows()
    ]
    con.executemany("INSERT OR REPLACE INTO flows VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    return len(ohlcv)


def collect_index(con, code, start, end):
    df = fmt_dates(stock.get_index_ohlcv_by_date(start, end, code))
    rows = [(code, d, r["시가"], r["고가"], r["저가"], r["종가"], r["거래량"], r["거래대금"])
            for d, r in df.iterrows()]
    con.executemany("INSERT OR REPLACE INTO index_prices VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    return len(df)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="data/universe.csv")
    ap.add_argument("--start", default="20260401")
    ap.add_argument("--end", default="20260630")
    ap.add_argument("--db", default="data/market.sqlite")
    ap.add_argument("--index", nargs="*", default=["5600", "1028"], help="지수코드 (KTOP30=5600, KOSPI200=1028)")
    args = ap.parse_args()

    uni = list(csv.DictReader(open(args.universe, encoding="utf-8-sig")))
    con = sqlite3.connect(args.db)
    con.executescript(SCHEMA)

    for r in uni:
        t = r["ticker"].zfill(6)
        n = collect_ticker(con, t, args.start, args.end)
        print(f"{t} {r['name_kr']}: {n} days")
        time.sleep(0.5)                       # KRX 부하 조절
    for code in args.index:
        n = collect_index(con, code, args.start, args.end)
        print(f"index {code}: {n} days")

    print("\n요약:")
    print(con.execute("SELECT COUNT(DISTINCT ticker), MIN(date), MAX(date), COUNT(*) FROM prices").fetchone())
    print(con.execute("SELECT COUNT(*) FROM flows").fetchone())


if __name__ == "__main__":
    main()
