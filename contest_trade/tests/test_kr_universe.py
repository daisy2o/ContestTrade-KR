"""Tests for universe CSV loader — column auto-detection, zero-padding, encodings."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.kr_universe import load_universe


def _write(tmp_path, content, encoding="utf-8"):
    p = tmp_path / "u.csv"
    p.write_bytes(content.encode(encoding))
    return p


def test_standard_csv(tmp_path):
    p = _write(tmp_path, "종목코드,종목명\n005930,삼성전자\n000660,SK하이닉스\n")
    u = load_universe(p)
    assert u == {"005930": "삼성전자", "000660": "SK하이닉스"}


def test_excel_stripped_leading_zeros(tmp_path):
    """엑셀이 '005930'을 '5930'으로 깎아 저장한 경우 자동 보정."""
    p = _write(tmp_path, "code,name\n5930,삼성전자\n660,SK하이닉스\n")
    u = load_universe(p)
    assert "005930" in u and "000660" in u


def test_column_order_agnostic(tmp_path):
    p = _write(tmp_path, "이름,티커\n삼성전자,005930\n")
    assert load_universe(p) == {"005930": "삼성전자"}


def test_cp949_encoding(tmp_path):
    p = _write(tmp_path, "종목코드,종목명\n005930,삼성전자\n", encoding="cp949")
    assert load_universe(p)["005930"] == "삼성전자"


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_universe("/nonexistent/u.csv")


def test_no_code_column_raises(tmp_path):
    p = _write(tmp_path, "이름,설명\n삼성전자,반도체\n")
    with pytest.raises(ValueError, match="종목코드"):
        load_universe(p)
