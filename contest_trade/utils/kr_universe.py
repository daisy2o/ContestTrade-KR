"""
KR 유니버스 로더 (D40/D44 — K-TOP30, 이후 KOSPI200 확장).

원본: data_collection/universe/ktop30.csv (Factiva에서 받은 구성종목 CSV를 그대로 배치)
CSV 형식이 확정돼 있지 않으므로 흔한 형태를 자동 감지한다:
  - 종목코드 컬럼: 6자리 숫자(앞자리 0 보존 이슈 자동 보정 — 엑셀이 '005930'→'5930'으로
    깎는 사고가 흔해서 zfill 처리)
  - 종목명 컬럼: 한글 포함 텍스트 컬럼

⚠️ point-in-time 주의 (D13): 이 CSV는 "다운로드 시점의 구성종목"이다. 백테스트 구간
시작일 기준 구성과 다를 수 있음(리밸런싱). 1차 파일럿에서는 현재 구성으로 진행하되
논문 한계에 명시하고, 본실험 전 D13(리밸런싱 재구성)으로 정밀화한다.
"""
import re
from pathlib import Path

import pandas as pd

DEFAULT_UNIVERSE_CSV = (
    Path(__file__).parents[2] / "data_collection" / "universe" / "ktop30.csv"
)

_CODE_RE = re.compile(r"^\d{1,6}$")
_HANGUL_RE = re.compile(r"[가-힣]")


def _detect_columns(df: pd.DataFrame):
    """코드 컬럼(숫자 1~6자리 위주)과 이름 컬럼(한글 위주)을 자동 감지."""
    code_col = name_col = None
    for col in df.columns:
        series = df[col].astype(str).str.strip()
        if code_col is None and (series.str.match(_CODE_RE).mean() > 0.8):
            code_col = col
        elif name_col is None and (series.str.contains(_HANGUL_RE).mean() > 0.5):
            name_col = col
    return code_col, name_col


def load_universe(csv_path=None) -> dict:
    """유니버스 CSV → {6자리 종목코드: 종목명}. 실패 시 명시적 에러 (조용한 빈 결과 금지)."""
    path = Path(csv_path) if csv_path else DEFAULT_UNIVERSE_CSV
    if not path.exists():
        raise FileNotFoundError(
            f"유니버스 CSV가 없습니다: {path}\n"
            "Factiva에서 받은 K-TOP30 구성종목 CSV를 이 경로에 배치하세요."
        )
    # 인코딩 자동 시도 (엑셀 저장 CSV는 cp949가 흔함)
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            df = pd.read_csv(path, dtype=str, encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"CSV 인코딩을 읽지 못했습니다: {path}")

    code_col, name_col = _detect_columns(df)
    if code_col is None:
        raise ValueError(
            f"종목코드 컬럼을 찾지 못했습니다. 컬럼들: {list(df.columns)}\n"
            "6자리 숫자 코드 컬럼이 포함된 CSV인지 확인하세요."
        )
    universe = {}
    for _, row in df.iterrows():
        code = str(row[code_col]).strip().zfill(6)  # 엑셀의 앞자리 0 소실 보정
        if not code.isdigit() or len(code) != 6:
            continue
        name = str(row[name_col]).strip() if name_col else code
        universe[code] = name
    if not universe:
        raise ValueError(f"유효한 종목이 0개입니다: {path}")
    return universe


if __name__ == "__main__":
    u = load_universe()
    print(f"{len(u)}종목 로드:")
    for code, name in list(u.items())[:35]:
        print(f"  {code}  {name}")
