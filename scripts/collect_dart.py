"""
OpenDART 공시목록 수집 (list.json → SQLite)

필요 env 변수: DART_API_KEY (OpenDART 인증키)

    $env:DART_API_KEY = "..."
    python scripts/collect_dart.py --universe data/universe.csv --start 20260401 --end 20260630 --db data/dart.sqlite

출력: data/dart.sqlite (gitignore 대상)

테이블 disclosures
  rcept_no     접수번호 (PK, 공시 원문 URL: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=<rcept_no>)
  corp_code, corp_name, stock_code(ticker), report_nm(보고서명), flr_nm(제출인), rcept_dt(접수일 YYYY-MM-DD), rm(비고)

주의: OpenDART 공시목록에는 접수 '일자'만 있고 접수 '시각'은 없다.
      파일럿에서는 D일 접수 공시 → D+1 08:30 시그널에 사용(보수적 규칙).
      장중/장후 구분이 필요해지면 KIND(kind.krx.co.kr) 공시 시각을 별도 수집해 rcept_time 컬럼을 채운다.
"""
import argparse
import csv
import os
import sqlite3
import sys
import time

import requests

API = "https://opendart.fss.or.kr/api/list.json"
SCHEMA = """
CREATE TABLE IF NOT EXISTS disclosures (
    rcept_no   TEXT PRIMARY KEY,
    corp_code  TEXT, corp_name TEXT, stock_code TEXT,
    report_nm  TEXT, flr_nm TEXT,
    rcept_dt   TEXT,            -- YYYY-MM-DD
    rcept_time TEXT,            -- HH:MM (KIND에서 보완, 초기 NULL)
    rm         TEXT
);
CREATE INDEX IF NOT EXISTS ix_disc_dt ON disclosures(rcept_dt);
CREATE INDEX IF NOT EXISTS ix_disc_code ON disclosures(stock_code);
"""


def fetch_all(key, corp_code, start, end):
    out, page = [], 1
    while True:
        r = requests.get(API, params=dict(crtfc_key=key, corp_code=corp_code, bgn_de=start, end_de=end,
                                          page_no=page, page_count=100), timeout=30)
        r.raise_for_status()
        j = r.json()
        if j.get("status") == "013":          # 조회된 데이터 없음
            return out
        if j.get("status") != "000":
            raise RuntimeError(f"{corp_code}: {j.get('status')} {j.get('message')}")
        out += j["list"]
        if page >= int(j.get("total_page", 1)):
            return out
        page += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="data/universe.csv")
    ap.add_argument("--start", default="20260401")
    ap.add_argument("--end", default="20260630")
    ap.add_argument("--db", default="data/dart.sqlite")
    args = ap.parse_args()

    key = os.environ.get("DART_API_KEY") or sys.exit("환경변수 DART_API_KEY 필요")
    uni = list(csv.DictReader(open(args.universe, encoding="utf-8-sig")))
    con = sqlite3.connect(args.db)
    con.executescript(SCHEMA)

    for r in uni:
        cc = r["dart_corp_code"].zfill(8)
        items = fetch_all(key, cc, args.start, args.end)
        con.executemany(
            """INSERT OR IGNORE INTO disclosures
               (rcept_no, corp_code, corp_name, stock_code, report_nm, flr_nm, rcept_dt, rm)
               VALUES (?,?,?,?,?,?,?,?)""",
            [(x["rcept_no"], x["corp_code"], x["corp_name"], x["stock_code"], x["report_nm"], x["flr_nm"],
              f"{x['rcept_dt'][:4]}-{x['rcept_dt'][4:6]}-{x['rcept_dt'][6:]}", x.get("rm")) for x in items],
        )
        con.commit()
        print(f"{r['name_kr']} ({cc}): {len(items)}")
        time.sleep(0.2)

    print("\n요약:", con.execute("SELECT COUNT(*), MIN(rcept_dt), MAX(rcept_dt) FROM disclosures").fetchone())
    print("보고서 유형 상위:")
    for row in con.execute("SELECT report_nm, COUNT(*) FROM disclosures GROUP BY 1 ORDER BY 2 DESC LIMIT 10"):
        print("  ", row)


if __name__ == "__main__":
    main()
