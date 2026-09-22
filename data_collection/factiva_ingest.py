"""
Factiva RTF 내보내기 → SQLite 수집기 (D6/뉴스 소스, 역할 B).

입력: data_collection/factiva/<월>/ 아래의 Factiva RTF 파일들 (파일당 ~100건)
출력: data_collection/data/factiva/factiva_news.sqlite

Factiva 필드 코드 (striprtf 변환 후 ' XX|내용|' 형태):
  HD 제목 / PD 발행일(날짜만!) / SN 매체 / LP 리드 / TD 본문 /
  CO 회사코드(factiva_co_code와 매칭) / AN 문서ID(중복 제거 키, 기사 종결자)

주의: PD는 날짜 단위 — 장중/장후 구분 불가 → 어댑터에서 D+1 규칙(D45) 적용.

사용법:
  python data_collection/factiva_ingest.py            # factiva/ 아래 전체 인제스트
  python data_collection/factiva_ingest.py stats      # 현황 요약
"""
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from striprtf.striprtf import rtf_to_text

BASE = Path(__file__).parent
RTF_ROOT = BASE / "factiva"
DB_PATH = BASE / "data" / "factiva" / "factiva_news.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    an          TEXT PRIMARY KEY,   -- Factiva 문서 ID
    pd_date     TEXT NOT NULL,      -- 발행일 YYYY-MM-DD (시각 없음)
    source      TEXT,
    headline    TEXT,
    body        TEXT,
    co_codes    TEXT,               -- 'sansel,hylec' 등 소문자 콤마 구분
    src_file    TEXT,
    ingested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_date ON articles (pd_date);
"""

FIELD_SPLIT = re.compile(r"\n(?=\s?[A-Z]{2,3}\|)")
FIELD_HEAD = re.compile(r"^\s?([A-Z]{2,3})\|", re.S)
CO_CODE = re.compile(r"([a-z0-9]+)\s*:")


def parse_rtf_articles(rtf_path: Path) -> list[dict]:
    text = rtf_to_text(rtf_path.read_text(encoding="utf-8", errors="ignore"))
    articles, cur = [], {}
    for chunk in FIELD_SPLIT.split(text):
        m = FIELD_HEAD.match(chunk)
        if not m:
            continue
        code = m.group(1)
        content = chunk[m.end():].rstrip()
        content = re.sub(r"\|\s*$", "", content).strip()
        cur[code] = content
        if code == "AN":  # 기사 종결자
            pd_raw = cur.get("PD", "")
            try:
                pd_date = datetime.strptime(pd_raw.strip(), "%d %B %Y").strftime("%Y-%m-%d")
            except ValueError:
                cur = {}
                continue  # 날짜 파싱 실패 기사는 스킵 (건수는 stats에서 원본 대비 확인)
            an = re.sub(r"^Document\s+", "", cur.get("AN", "")).strip()
            articles.append({
                "an": an,
                "pd_date": pd_date,
                "source": cur.get("SN", "").strip(),
                "headline": cur.get("HD", "").strip(),
                "body": (cur.get("LP", "") + "\n" + cur.get("TD", "")).strip(),
                "co_codes": ",".join(CO_CODE.findall(cur.get("CO", ""))),
                "src_file": rtf_path.name,
            })
            cur = {}
    return articles


def ingest():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    total, files = 0, 0
    for rtf in sorted(RTF_ROOT.rglob("*.rtf")):
        rows = parse_rtf_articles(rtf)
        now = datetime.now(timezone.utc).isoformat()
        conn.executemany(
            "INSERT OR IGNORE INTO articles VALUES (?,?,?,?,?,?,?,?)",
            [(a["an"], a["pd_date"], a["source"], a["headline"],
              a["body"], a["co_codes"], a["src_file"], now) for a in rows],
        )
        total += len(rows)
        files += 1
    conn.commit()
    print(f"인제스트 완료: RTF {files}개 파일에서 {total}건 파싱")
    print_stats(conn)
    conn.close()


def print_stats(conn):
    n, lo, hi = conn.execute("SELECT COUNT(*), MIN(pd_date), MAX(pd_date) FROM articles").fetchone()
    tagged = conn.execute("SELECT COUNT(*) FROM articles WHERE co_codes != ''").fetchone()[0]
    print(f"DB 총 {n}건 | 기간 {lo} ~ {hi} | CO 태깅 있음 {tagged}건 ({tagged/max(n,1)*100:.0f}%)")
    for row in conn.execute(
        "SELECT substr(pd_date,1,7), COUNT(*) FROM articles GROUP BY 1 ORDER BY 1"
    ):
        print(f"  {row[0]}: {row[1]}건")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        conn = sqlite3.connect(DB_PATH)
        print_stats(conn)
        conn.close()
    else:
        ingest()
