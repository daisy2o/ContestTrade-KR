"""
KOSPI200 종목뉴스 일별 수집기 (네이버 stock.naver.com 종목뉴스 API)

- 매일 실행하면 종목별로 새 기사만 SQLite에 누적 저장 (id 기준 중복 제거)
- 첫 실행: API가 허용하는 최대 깊이(약 2~3주)까지 백필
- 이후 실행: 이미 저장된 기사가 나오는 지점에서 중단 → 하루 10~20분
- 저장 위치: <repo>/data/naver_news/naver_news.db  (+ collector.log, daily_csv/ 실행별 CSV)
  환경변수 CONTESTTRADE_DATA_DIR 로 data 폴더 위치를 바꿀 수 있다.
- 여러 PC에서 돌린 DB 합치기:  python scripts/naver_news_collector.py merge <다른PC의 naver_news.db>
- 실시간 전용 소스(백테스트에는 쓰지 않음, docs/data_sources.md 참고)

필요 env 변수: 없음 (선택: CONTESTTRADE_DATA_DIR, SKIP_PYKRX=1)
설치:  pip install requests pandas pykrx truststore
실행:  python scripts/naver_news_collector.py            (또는 scripts/run_naver_news.bat)
"""
import truststore
truststore.inject_into_ssl()

import sqlite3, time, logging, sys, os
from datetime import datetime
from pathlib import Path
import requests
import pandas as pd

# ---------------- 설정 ----------------
PROJECT_DIR = Path(__file__).resolve().parents[1]                     # <repo> (scripts/ 의 상위)
DATA_DIR   = Path(os.environ.get("CONTESTTRADE_DATA_DIR", PROJECT_DIR / "data"))
BASE_DIR   = DATA_DIR / "naver_news"
DB_PATH    = BASE_DIR / "naver_news.db"
CSV_DIR    = BASE_DIR / "daily_csv"
CODES_FILE = BASE_DIR / "kospi200_codes.csv"      # pykrx 실패 시 수동 목록 (code 컬럼)
URL        = "https://stock.naver.com/api/domestic/detail/news"
HEADERS    = {"User-Agent": "Mozilla/5.0", "Referer": "https://stock.naver.com/"}
PAGE_SIZE  = 15          # 50까지 허용 확인 (100은 400)
MAX_PAGES  = 150         # 100은 OK, 200은 400 → 여유 있게 잡고 400 나오면 중단
SLEEP_SEC  = 0.3         # 요청 간 대기
KNOWN_PAGE_STOP = 2      # 연속 N페이지가 전부 기존 기사면 해당 종목 중단
# --------------------------------------

BASE_DIR.mkdir(parents=True, exist_ok=True)
CSV_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(BASE_DIR / "collector.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("collector")


CODES_MAX_AGE_DAYS = 7   # CSV가 이보다 오래됐을 때만 pykrx로 갱신 (KRX 로그인 횟수 절약)

def get_kospi200_codes() -> list[str]:
    """로컬 CSV 우선, 오래됐거나 없을 때만 pykrx(KRX 로그인)로 갱신"""
    csv_fresh = (CODES_FILE.exists() and
                 (time.time() - CODES_FILE.stat().st_mtime) < CODES_MAX_AGE_DAYS * 86400)
    if csv_fresh or os.environ.get("SKIP_PYKRX") == "1":
        codes = pd.read_csv(CODES_FILE, dtype=str)["code"].str.zfill(6).tolist()
        log.info(f"KOSPI200 {len(codes)}종목 (CSV, 갱신 생략)")
        return codes
    try:
        from pykrx import stock
        today = datetime.now().strftime("%Y%m%d")
        codes = list(stock.get_index_portfolio_deposit_file("1028", today))
        if len(codes) < 150:
            raise ValueError(f"구성종목 수 이상: {len(codes)}")
        pd.DataFrame({"code": codes}).to_csv(CODES_FILE, index=False)
        log.info(f"KOSPI200 {len(codes)}종목 (pykrx, CSV 갱신)")
        return codes
    except Exception as e:
        log.warning(f"pykrx 실패({e}) → {CODES_FILE} 사용")
        if not CODES_FILE.exists():
            log.error("종목 목록 파일이 없습니다. code 컬럼을 가진 CSV를 만들어 주세요.")
            sys.exit(1)
        codes = pd.read_csv(CODES_FILE, dtype=str)["code"].str.zfill(6).tolist()
        log.info(f"KOSPI200 {len(codes)}종목 (CSV)")
        return codes


def init_db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id           TEXT,
            item_code    TEXT,
            office_id    TEXT,
            article_id   TEXT,
            office_name  TEXT,
            datetime     TEXT,      -- YYYYMMDDHHMM
            title        TEXT,
            body         TEXT,
            collected_at TEXT,
            PRIMARY KEY (id, item_code)
        )""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_code_dt ON news(item_code, datetime)")
    con.commit()
    return con


def fetch_page(code: str, page: int):
    """(status_code, items) 반환. 400이면 페이지 한도 초과."""
    for attempt in range(3):
        try:
            r = requests.get(URL, params={"itemCode": code, "page": page, "pageSize": PAGE_SIZE},
                             headers=HEADERS, timeout=15)
            if r.status_code == 400:
                return 400, []
            r.raise_for_status()
            data = r.json()
            items = [it for c in data.get("clusters", []) for it in c.get("items", [])]
            return 200, items
        except Exception as e:
            log.warning(f"{code} p{page} 요청 실패({attempt+1}/3): {e}")
            time.sleep(2 * (attempt + 1))
    return 599, []


def collect_code(con: sqlite3.Connection, code: str, now_str: str) -> int:
    known = {row[0] for row in con.execute("SELECT id FROM news WHERE item_code=?", (code,))}
    inserted, known_streak = 0, 0
    for page in range(1, MAX_PAGES + 1):
        status, items = fetch_page(code, page)
        time.sleep(SLEEP_SEC)
        if status == 400 or not items:
            break
        new_rows = []
        for it in items:
            nid = str(it.get("id"))
            if nid in known:
                continue
            known.add(nid)
            new_rows.append((nid, code, str(it.get("officeId")), str(it.get("articleId")),
                             it.get("officeName"), str(it.get("datetime")),
                             it.get("title"), it.get("body"), now_str))
        if new_rows:
            con.executemany("INSERT OR IGNORE INTO news VALUES (?,?,?,?,?,?,?,?,?)", new_rows)
            con.commit()
            inserted += len(new_rows)
            known_streak = 0
        else:
            known_streak += 1
            if known_streak >= KNOWN_PAGE_STOP:
                break
    return inserted


def export_daily_csv(con: sqlite3.Connection, now_str: str):
    df = pd.read_sql("SELECT * FROM news WHERE collected_at=?", con, params=(now_str,))
    if not df.empty:
        out = CSV_DIR / f"news_{now_str}.csv"   # 하루 2회 실행 시 덮어쓰지 않도록 시각까지
        df.to_csv(out, index=False, encoding="utf-8-sig")
        log.info(f"CSV 저장: {out} ({len(df)}건)")


def main():
    start = time.time()
    now_str = datetime.now().strftime("%Y%m%d%H%M")
    codes = get_kospi200_codes()
    con = init_db()
    total = 0
    for i, code in enumerate(codes, 1):
        n = collect_code(con, code, now_str)
        total += n
        if n or i % 20 == 0:
            log.info(f"[{i}/{len(codes)}] {code}: +{n}건 (누적 +{total})")
    export_daily_csv(con, now_str)
    cnt = con.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    log.info(f"완료: 신규 {total}건, DB 총 {cnt}건, {time.time()-start:.0f}초")
    con.close()


def merge(other_db: str):
    """다른 PC에서 수집한 DB를 현재 DB에 병합 (중복은 무시)"""
    con = init_db()
    before = con.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    con.execute("ATTACH DATABASE ? AS other", (other_db,))
    con.execute("INSERT OR IGNORE INTO news SELECT * FROM other.news")
    con.commit()
    after = con.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    log.info(f"병합 완료: {other_db} → +{after - before}건, DB 총 {after}건")
    con.close()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "merge":
        merge(sys.argv[2])
    else:
        main()
