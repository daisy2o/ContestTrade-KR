"""Tests for KRDataSourceBase (as-of enforcement) and D1 DART adapter (parsing). Offline."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from data_source.kr_data_source_base import KRDataSourceBase
from data_source.kr_dart_disclosure import parse_dart_rows


class FakeSource(KRDataSourceBase):
    """미래 데이터가 섞인 응답을 돌려주는 가짜 소스 — 베이스의 as-of 강제 검증용."""

    def __init__(self, tmp_cache):
        super().__init__("fake_kr_source", cache_dir=tmp_cache)
        self.fetch_count = 0

    def fetch_raw(self, trigger_time):
        self.fetch_count += 1
        return pd.DataFrame(
            {
                "title": ["past", "same-moment", "future"],
                "content": ["a", "b", "c"],
                "pub_time": [
                    "2026-09-18 15:00:00",
                    "2026-09-19 09:00:00",  # == trigger → 제외돼야 함(엄격 미만)
                    "2026-09-19 10:00:00",  # 미래 → 제외
                ],
                "url": ["u1", "u2", "u3"],
            }
        )


@pytest.fixture
def fake_source(tmp_path):
    return FakeSource(tmp_path)


TRIGGER = "2026-09-19 09:00:00"


def test_asof_filter_strict(fake_source):
    df = fake_source.get_data_sync(TRIGGER)
    assert list(df["title"]) == ["past"]  # 동시각·미래 모두 제거


def test_cache_after_first_fetch(fake_source):
    fake_source.get_data_sync(TRIGGER)
    fake_source.get_data_sync(TRIGGER)
    assert fake_source.fetch_count == 1


def test_missing_column_raises(tmp_path):
    class Broken(KRDataSourceBase):
        def __init__(self):
            super().__init__("broken_kr", cache_dir=tmp_path)

        def fetch_raw(self, trigger_time):
            return pd.DataFrame({"title": ["x"], "pub_time": ["2026-01-01 00:00:00"]})

    with pytest.raises(ValueError, match="필수 컬럼"):
        Broken().get_data_sync(TRIGGER)


# ---- D1 DART parsing ----

DART_ROWS = [
    {  # 정상 상장사 공시
        "corp_name": "삼성전자", "stock_code": "005930", "rcept_dt": "20260918",
        "report_nm": "단일판매ㆍ공급계약체결", "rcept_no": "20260918000001", "flr_nm": "삼성전자",
    },
    {  # 비상장 (stock_code 없음) → 제외
        "corp_name": "비상장회사", "stock_code": "", "rcept_dt": "20260918",
        "report_nm": "감사보고서", "rcept_no": "20260918000002", "flr_nm": "감사인",
    },
    {  # 유니버스 밖 종목
        "corp_name": "기타상장", "stock_code": "999999", "rcept_dt": "20260918",
        "report_nm": "주요사항보고서", "rcept_no": "20260918000003", "flr_nm": "기타",
    },
]


def test_dart_parse_listed_only():
    df = parse_dart_rows(DART_ROWS)
    assert len(df) == 2  # 비상장 제외
    assert "005930" in df.iloc[0]["title"]


def test_dart_universe_filter():
    df = parse_dart_rows(DART_ROWS, universe={"005930"})
    assert len(df) == 1


def test_dart_conservative_next_day_rule():
    """접수일 D의 공시는 D+1 00:00부터 사용 가능 — 당일 look-ahead 구조적 차단."""
    df = parse_dart_rows(DART_ROWS, universe={"005930"})
    assert df.iloc[0]["pub_time"] == "2026-09-19 00:00:00"


def test_dart_event_filter_excludes_noise():
    """9/21 통합 테스트 발견 반영: 해명·IR 공시는 이벤트 필터에서 제외."""
    rows = [
        {"corp_name": "신한지주", "stock_code": "055550", "rcept_dt": "20260918",
         "report_nm": "풍문또는보도에대한해명", "rcept_no": "20260918000009", "flr_nm": "신한지주"},
        {"corp_name": "탑선", "stock_code": "180060", "rcept_dt": "20260918",
         "report_nm": "기업설명회(IR)개최", "rcept_no": "20260918000010", "flr_nm": "탑선"},
        {"corp_name": "삼성전자", "stock_code": "005930", "rcept_dt": "20260918",
         "report_nm": "자기주식취득결정", "rcept_no": "20260918000011", "flr_nm": "삼성전자"},
    ]
    df = parse_dart_rows(rows)
    assert len(df) == 1 and "자기주식" in df.iloc[0]["title"]


def test_dart_events_only_false_keeps_all_listed():
    rows = [
        {"corp_name": "회사A", "stock_code": "111111", "rcept_dt": "20260918",
         "report_nm": "감사보고서", "rcept_no": "1", "flr_nm": "A"},
    ]
    assert len(parse_dart_rows(rows, events_only=False)) == 1
    assert len(parse_dart_rows(rows, events_only=True)) == 0


def test_dart_all_filtered_day_keeps_column_contract(tmp_path):
    """전부 걸러진 날에도 빈 DataFrame이 컬럼 계약을 지켜야 함 (9/21 라이브 발견 버그)."""
    class NoisyDart(KRDataSourceBase):
        def __init__(self):
            super().__init__("noisy_dart", cache_dir=tmp_path)

        def fetch_raw(self, trigger_time):
            rows = [{"corp_name": "신한지주", "stock_code": "055550", "rcept_dt": "20260918",
                     "report_nm": "풍문또는보도에대한해명", "rcept_no": "9", "flr_nm": "x"}]
            return parse_dart_rows(rows)

    df = NoisyDart().get_data_sync("2026-09-19 09:00:00")
    assert len(df) == 0 and list(df.columns) == ["title", "content", "pub_time", "url"]


def test_dart_url_contains_rcept_no():
    df = parse_dart_rows(DART_ROWS, universe={"005930"})
    assert "20260918000001" in df.iloc[0]["url"]


def test_dart_plus_base_asof_integration(tmp_path):
    """D+1 규칙 + 베이스 as-of: 9/19 09:00 trigger면 9/18 접수분이 포함된다."""
    class FixtureDart(KRDataSourceBase):
        def __init__(self):
            super().__init__("fixture_dart", cache_dir=tmp_path)

        def fetch_raw(self, trigger_time):
            return parse_dart_rows(DART_ROWS)

    df = FixtureDart().get_data_sync("2026-09-19 09:00:00")
    assert len(df) == 2 and all(df["pub_time"] < "2026-09-19 09:00:00")
