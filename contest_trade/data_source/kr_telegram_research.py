"""
D9 — 텔레그램 증권사 리서치 채널 데이터 소스 (역할 B, 판정 1군).

원본: data_collection/data/telegram/telegram_research.sqlite (telegram_collector.py 수집)
종목 매핑: 규칙 기반 2단계 (D41) — ① 6자리 코드 정규식 ② 종목명 문자열 매칭.
NLP 없음 — 실측 샘플에서 리서치 채널 메시지 다수가 "종목명(코드)" 형태로
코드를 직접 포함하는 것을 확인 (예: "** 카카오(035720)").

시각 규율: 수집기가 저장한 서버 타임스탬프(UTC)를 KST로 변환해 pub_time으로 사용.
베이스(KRDataSourceBase)가 as-of 필터를 추가로 강제하므로 이중 안전.

이미지 전용 메시지(본문 없음)는 제외 — 팩터 원료가 아님(2026-09-21 실데이터 점검,
채널별 4~27% 비중 확인).
"""
import re
import sqlite3
from datetime import timedelta
from pathlib import Path

import pandas as pd

from data_source.kr_data_source_base import KRDataSourceBase

DB_PATH = Path(__file__).parents[2] / "data_collection" / "data" / "telegram" / "telegram_research.sqlite"

CODE_RE = re.compile(r"\b(\d{6})\b")

# 1군 채널 (판정표 2026-09-21 최종판)
CHANNELS = ["hanaresearch", "shinhanresearch", "kiwoomresearch", "meritz_research"]

LOOKBACK_DAYS = 2  # trigger 직전 며칠치를 팩터 원료로 볼지


def tag_stock_codes(text: str, universe_names: dict) -> list:
    """텍스트에서 언급된 종목코드를 규칙 기반으로 추출.

    universe_names: {종목코드: 종목명} — D41 2단계용.
    반환: 텍스트에서 발견된 종목코드 리스트 (중복 제거, 순서 보존).
    """
    if not text:
        return []
    found = []
    # 1단계: 6자리 코드 직접 매칭 (universe 한정)
    for code in CODE_RE.findall(text):
        if code in universe_names and code not in found:
            found.append(code)
    # 2단계: 코드가 없는 경우 종목명으로 보완
    if not found:
        for code, name in universe_names.items():
            if name and name in text and code not in found:
                found.append(code)
    return found


def _load_messages(since_utc: str, until_utc: str) -> pd.DataFrame:
    if not DB_PATH.exists():
        raise RuntimeError(
            f"텔레그램 DB가 없습니다: {DB_PATH}\n"
            "data_collection/telegram_collector.py 로 먼저 백필하세요."
        )
    conn = sqlite3.connect(DB_PATH)
    try:
        placeholders = ",".join("?" for _ in CHANNELS)
        df = pd.read_sql_query(
            f"""SELECT channel, message_id, date_utc, text
                FROM messages
                WHERE channel IN ({placeholders})
                  AND date_utc >= ? AND date_utc < ?
                  AND text != ''""",  # 이미지 전용 메시지 제외
            conn,
            params=[*CHANNELS, since_utc, until_utc],
        )
    finally:
        conn.close()
    return df


class KrTelegramResearch(KRDataSourceBase):
    def __init__(self, universe_names: dict | None = None, cache_dir=None):
        """universe_names: {6자리 종목코드: 종목명}. None이면 유니버스 CSV 자동 로드."""
        super().__init__("kr_telegram_research", cache_dir=cache_dir)
        if universe_names is None:
            from utils.kr_universe import load_universe
            universe_names = load_universe()
        self.universe_names = universe_names

    def fetch_raw(self, trigger_time: str) -> pd.DataFrame:
        trigger_dt = pd.to_datetime(trigger_time) - timedelta(hours=9)  # KST -> UTC
        since = (trigger_dt - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
        until = trigger_dt.strftime("%Y-%m-%dT%H:%M:%S")
        raw = _load_messages(since, until)

        rows = []
        for _, r in raw.iterrows():
            codes = tag_stock_codes(r["text"], self.universe_names)
            if not codes:
                continue  # 유니버스 밖/태깅 실패 메시지는 팩터 원료에서 제외
            pub_time_kst = (
                pd.to_datetime(r["date_utc"]).tz_localize(None) + timedelta(hours=9)
            ).strftime("%Y-%m-%d %H:%M:%S")
            names = ", ".join(self.universe_names[c] for c in codes)
            rows.append(
                {
                    "title": f"[{r['channel']}] {names}",
                    "content": r["text"],
                    "pub_time": pub_time_kst,
                    "url": f"https://t.me/{r['channel']}/{r['message_id']}",
                }
            )
        # 빈 결과여도 컬럼 계약 유지 (태깅이 전부 실패한 날 대비 — kr_dart와 동일 버그 예방)
        return pd.DataFrame(rows, columns=["title", "content", "pub_time", "url"])


if __name__ == "__main__":
    # 8종목 유니버스 예시로 라이브 스모크 테스트
    universe = {
        "005930": "삼성전자", "000660": "SK하이닉스",
        "207940": "삼성바이오로직스", "068270": "셀트리온",
        "105560": "KB금융", "055550": "신한지주",
        "090430": "아모레퍼시픽", "051900": "LG생활건강",
    }
    src = KrTelegramResearch(universe)
    df = src.get_data_sync("2025-09-15 09:00:00")
    print(df.head(10))
    print(f"{len(df)}건")
