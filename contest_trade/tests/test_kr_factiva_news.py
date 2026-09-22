"""Tests for D6 Factiva adapter — CO-code mapping, name fallback, D+1 rule. Offline."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import data_source.kr_factiva_news as mod
from data_source.kr_factiva_news import KrFactivaNews

UNIVERSE = {"005930": "삼성전자", "068270": "셀트리온"}
FACTIVA_MAP = {"sansel": "005930"}  # 셀트리온은 factiva 코드 없음 → 이름 폴백 대상


@pytest.fixture
def fake_db(tmp_path, monkeypatch):
    db = tmp_path / "factiva.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE articles (an TEXT PRIMARY KEY, pd_date TEXT, source TEXT,
           headline TEXT, body TEXT, co_codes TEXT, src_file TEXT, ingested_at TEXT)"""
    )
    rows = [
        ("A1", "2026-05-28", "한경", "삼성전자 신고가", "본문A" * 200, "sansel,samgup", "f", "t"),
        ("A2", "2026-05-28", "서경", "셀트리온 AI 도입", "셀트리온이 신약 개발에…", "", "f", "t"),
        ("A3", "2026-05-28", "매경", "코스피 전망", "지수 전반 이야기", "unknowncorp", "f", "t"),
        ("A4", "2026-05-29", "한경", "삼성전자 당일기사", "당일 발행", "sansel", "f", "t"),
    ]
    conn.executemany("INSERT INTO articles VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit(); conn.close()
    monkeypatch.setattr(mod, "DB_PATH", db)
    return db


def make(tmp_path):
    return KrFactivaNews(UNIVERSE, FACTIVA_MAP, cache_dir=tmp_path / "cache")


def test_co_code_mapping(fake_db, tmp_path):
    df = make(tmp_path).get_data("2026-05-29 09:00:00")
    assert any("삼성전자" in t for t in df["title"])


def test_name_fallback_without_factiva_code(fake_db, tmp_path):
    df = make(tmp_path).get_data("2026-05-29 09:00:00")
    assert any("셀트리온" in t for t in df["title"])


def test_unmapped_article_excluded(fake_db, tmp_path):
    df = make(tmp_path).get_data("2026-05-29 09:00:00")
    assert not any("코스피 전망" in t for t in df["title"])


def test_d_plus_one_rule(fake_db, tmp_path):
    """5/29 발행 기사는 5/29 09:00 trigger에 절대 안 들어옴 (D+1 = 5/30부터)."""
    df = make(tmp_path).get_data("2026-05-29 09:00:00")
    assert not any("당일기사" in t for t in df["title"])
    assert all(df["pub_time"] <= "2026-05-29 00:00:00")


def test_body_truncated_to_300(fake_db, tmp_path):
    df = make(tmp_path).get_data("2026-05-29 09:00:00")
    assert all(len(c) <= 300 for c in df["content"])
