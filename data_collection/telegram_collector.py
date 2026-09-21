"""
Telegram research-channel collector (역할 B — D9 데이터 소스).

수집 대상: 판정 1군 증권사 공식 리서치 채널 (판정표 2026-09-21 참조).
동작: 첫 실행 시 채널별 전량 백필 → 이후 실행은 마지막 저장 message id 이후만 증분.
저장: SQLite (naver_news_collector와 같은 폴더 규약: data_collection/data/telegram/)

사용법:
  1) data_collection/telegram_secrets.yaml 작성 (telegram_secrets.yaml.example 참조)
     — 이 파일은 .gitignore 대상. 절대 커밋 금지 (팀 규칙 2)
  2) python data_collection/telegram_collector.py            # 백필/증분 수집
     python data_collection/telegram_collector.py stats      # 채널별 기간·건수·밀도 요약
  3) 최초 1회 전화번호 인증 코드 입력 필요 → 세션 파일 생성 후 무인 실행 가능

look-ahead 규율: 텔레그램 서버 타임스탬프(UTC)를 그대로 저장하고,
백테스트 투입 시 as-of 필터는 어댑터 층(kr_telegram_research)에서 적용한다.
저작권 규율: 원문은 내부 보관·요약 생성용만. 공개 산출물에는 파생 통계만.
"""
import asyncio
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "telegram" / "telegram_research.sqlite"
SECRETS_PATH = BASE_DIR / "telegram_secrets.yaml"
SESSION_PATH = BASE_DIR / "data" / "telegram" / "collector_session"

# 백필 시작일 — 백테스트 구간(2025.7~2026.6) + 워밍업 여유. 전체 히스토리(2017~)가
# 필요해지면 이 값을 비우고 재실행 (증분 로직이 과거 방향은 안 채우므로 전량 재수집 필요).
BACKFILL_SINCE = "2024-01-01"

# 판정 1군 (공유용_텔레그램채널_판정표_0921). 2군 추가 시 여기에만 핸들 추가.
CHANNELS = [
    "hanaresearch",      # 하나증권 리서치 (2017-06~)
    "shinhanresearch",   # 신한 리서치 (2017-04~)
    "kiwoomresearch",    # 키움증권 리서치센터 (2017-09~)
    "meritz_research",   # 메리츠증권 리서치 (2017-03~)
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    channel     TEXT NOT NULL,
    message_id  INTEGER NOT NULL,
    date_utc    TEXT NOT NULL,   -- 서버 타임스탬프 (UTC, ISO8601)
    text        TEXT,
    is_forward  INTEGER NOT NULL DEFAULT 0,
    views       INTEGER,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (channel, message_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_date ON messages (channel, date_utc);
"""


def load_secrets():
    if not SECRETS_PATH.exists():
        sys.exit(
            f"[중단] {SECRETS_PATH} 가 없습니다.\n"
            "telegram_secrets.yaml.example을 복사해 api_id/api_hash/phone을 채워주세요."
        )
    s = yaml.safe_load(SECRETS_PATH.read_text())
    for k in ("api_id", "api_hash", "phone"):
        if not s.get(k):
            sys.exit(f"[중단] telegram_secrets.yaml에 {k} 가 비어 있습니다.")
    return s


def db_connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def last_saved_id(conn, channel: str) -> int:
    row = conn.execute(
        "SELECT MAX(message_id) FROM messages WHERE channel = ?", (channel,)
    ).fetchone()
    return row[0] or 0


async def collect(client, conn, channel: str):
    min_id = last_saved_id(conn, channel)
    mode = "증분" if min_id else f"백필({BACKFILL_SINCE or '전량'}~)"
    print(f"[{channel}] {mode} 시작 (message_id > {min_id})")
    saved = 0
    kwargs = {"min_id": min_id, "reverse": bool(min_id)}
    if not min_id and BACKFILL_SINCE:
        kwargs = {"offset_date": datetime.fromisoformat(BACKFILL_SINCE), "reverse": True}
    async for msg in client.iter_messages(channel, **kwargs):
        if msg.message is None and not msg.media:
            continue  # 서비스 메시지 스킵
        conn.execute(
            "INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?,?)",
            (
                channel,
                msg.id,
                msg.date.astimezone(timezone.utc).isoformat(),
                msg.message or "",
                1 if msg.forward else 0,
                msg.views,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        saved += 1
        if saved % 500 == 0:
            conn.commit()
            print(f"  … {saved}건")
    conn.commit()
    print(f"[{channel}] 완료: {saved}건 저장")


def print_stats(conn):
    print(f"\nDB: {DB_PATH}")
    rows = conn.execute(
        """SELECT channel, COUNT(*), MIN(date_utc), MAX(date_utc),
                  SUM(CASE WHEN is_forward=1 THEN 1 ELSE 0 END)
           FROM messages GROUP BY channel"""
    ).fetchall()
    for ch, n, lo, hi, fwd in rows:
        days = max(
            1,
            (
                __import__("datetime").datetime.fromisoformat(hi)
                - __import__("datetime").datetime.fromisoformat(lo)
            ).days,
        )
        print(
            f"  {ch:20s} {n:7d}건  {lo[:10]} ~ {hi[:10]}  "
            f"평균 {n/days:.1f}건/일  포워딩 {fwd}건"
        )
    # 백테스트 구간 밀도 (판정 기준 검증용)
    print("\n[백테스트 구간 2025-07-01 ~ 2026-06-30 밀도]")
    rows = conn.execute(
        """SELECT channel, COUNT(*) FROM messages
           WHERE date_utc >= '2025-07-01' AND date_utc < '2026-07-01'
           GROUP BY channel"""
    ).fetchall()
    total = 0
    for ch, n in rows:
        total += n
        print(f"  {ch:20s} {n:6d}건  ({n/245:.1f}건/거래일)")
    print(f"  {'합산':20s} {total:6d}건  ({total/245:.1f}건/거래일 — 기준: 합산 20건/일)")


async def login(code=None):
    """비대화형 로그인 2단계: `login`으로 코드 요청 → `login <코드>`로 완료."""
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError

    secrets = load_secrets()
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(SESSION_PATH), secrets["api_id"], secrets["api_hash"])
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"이미 로그인됨 (세션 유효): {me.first_name}")
            return
        state_file = SESSION_PATH.parent / "phone_code_hash.txt"
        if code is None:
            sent = await client.send_code_request(secrets["phone"])
            state_file.write_text(sent.phone_code_hash)
            print("인증 코드를 전송했습니다. 텔레그램 앱에 온 코드로:")
            print("  python data_collection/telegram_collector.py login <코드>")
        else:
            try:
                await client.sign_in(
                    secrets["phone"], code,
                    phone_code_hash=state_file.read_text().strip(),
                )
                me = await client.get_me()
                print(f"로그인 성공 — 세션 저장됨: {me.first_name}")
            except SessionPasswordNeededError:
                print(
                    "[2단계 인증 계정] 클라우드 비밀번호가 필요합니다.\n"
                    "비밀번호는 본인이 직접 입력해야 하므로, 터미널에서 아래를 실행해\n"
                    "프롬프트에 직접 입력하세요:\n"
                    "  cd ~/Documents/Workspace/ContestTrade && source .venv/bin/activate\n"
                    "  python -c \"from telethon.sync import TelegramClient; import yaml; "
                    "s=yaml.safe_load(open('data_collection/telegram_secrets.yaml')); "
                    "TelegramClient('data_collection/data/telegram/collector_session', "
                    "s['api_id'], s['api_hash']).start(phone=s['phone'])\""
                )
    finally:
        await client.disconnect()


async def main():
    from telethon import TelegramClient

    secrets = load_secrets()
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(SESSION_PATH), secrets["api_id"], secrets["api_hash"])
    await client.start(phone=secrets["phone"])  # 최초 1회 코드 입력
    conn = db_connect()
    try:
        for ch in CHANNELS:
            await collect(client, conn, ch)
        print_stats(conn)
    finally:
        conn.close()
        await client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        conn = db_connect()
        print_stats(conn)
        conn.close()
    elif len(sys.argv) > 1 and sys.argv[1] == "login":
        asyncio.run(login(sys.argv[2] if len(sys.argv) > 2 else None))
    else:
        asyncio.run(main())
