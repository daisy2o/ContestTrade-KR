"""
D1 — DART 공시 이벤트 데이터 소스 (역할 B, 1단계 주 소스).

수집: DART Open API list.json (공시검색) — trigger_time 기준 직전 N일의
상장사 공시 목록을 텍스트 팩터 원료로 반환.

시각 규율 (중요):
- list.json은 접수일(rcept_dt, 일 단위)만 제공하고 분 단위 접수시각은 없다.
- 보수 규칙(v1): 공시의 pub_time을 **rcept_dt 다음날 00:00:00**으로 기록한다.
  → 당일 공시는 당일 판단에 절대 안 들어가고, 다음 날부터 사용된다.
  (D일 정보의 D+1 사용 — 빅카인즈와 같은 규칙. 분 단위가 필요해지면
  open-proxy-mcp/뷰어 페이지의 접수시각으로 승급하는 것이 후속 과제)
- 이 규칙은 look-ahead를 구조적으로 차단하는 대신 당일 공시 반응을 포기한다.
  이 트레이드오프는 논문 방법론에 명시한다.

키: 환경변수 DART_API_KEY 또는 data_collection/kr_secrets.yaml 의 dart_api_key.
키가 없으면 fetch_raw가 명시적 에러 — 조용한 빈 결과 금지.
"""
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from data_source.kr_data_source_base import KRDataSourceBase

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
LOOKBACK_DAYS = 3  # trigger 직전 며칠치 공시를 팩터 원료로 볼지
PAGE_COUNT = 100


def _load_dart_key() -> str:
    import os

    key = os.environ.get("DART_API_KEY")
    if key:
        return key
    secrets = Path(__file__).parents[2] / "data_collection" / "kr_secrets.yaml"
    if secrets.exists():
        import yaml

        key = (yaml.safe_load(secrets.read_text()) or {}).get("dart_api_key")
        if key:
            return key
    raise RuntimeError(
        "DART API 키가 없습니다. 환경변수 DART_API_KEY 또는 "
        "data_collection/kr_secrets.yaml의 dart_api_key에 설정하세요 "
        "(발급: opendart.fss.or.kr, 무료 즉시)."
    )


# ── 공시 유형 필터 (2026-09-21 통합 테스트에서 발견: 해명공시 등 노이즈 유입) ──
# 화이트리스트 = 팀 문서 D1 정의(이벤트 드리븐 공시) 기반. 조정은 이 목록만 수정.
EVENT_KEYWORDS = [
    "영업실적", "잠정실적", "실적공시",
    "단일판매", "공급계약",
    "유상증자", "무상증자", "전환사채", "신주인수권부사채", "교환사채",
    "자기주식", "자사주",
    "최대주주변경", "최대주주등소유주식변동",
    "임원ㆍ주요주주특정증권", "임원·주요주주특정증권",
    "주식등의대량보유",
    "합병", "분할", "영업양수", "영업양도",
    "소송", "파산", "회생절차", "상장폐지", "감자",
    "현금ㆍ현물배당", "현금·현물배당", "배당",
    "기업가치제고",
    "주요사항보고서",
]
# 명시적 노이즈 (화이트리스트에 걸려도 제외 — 예: "풍문...해명"이 '합병' 단어 포함하는 경우)
NOISE_KEYWORDS = [
    "풍문또는보도", "해명",
    "기업설명회", "IR개최",
    "일괄신고서", "증권신고서(집합투자증권",
    "의결권대리행사권유",
]


def classify_event(report_nm: str) -> str | None:
    """공시 제목을 이벤트 유형으로 분류. 노이즈면 None."""
    if any(k in report_nm for k in NOISE_KEYWORDS):
        return None
    for k in EVENT_KEYWORDS:
        if k in report_nm:
            return k
    return None


def parse_dart_rows(rows: list, universe: set | None = None, events_only: bool = True) -> pd.DataFrame:
    """list.json의 list[] 항목들을 컬럼 계약 DataFrame으로 변환 (순수 함수 — 테스트 대상).

    - 상장사만: stock_code 6자리 존재
    - universe가 주어지면 그 종목코드로 한정 (point-in-time 목록은 호출자 책임)
    - pub_time = rcept_dt 다음날 00:00:00 (보수 규칙, 모듈 docstring 참조)
    """
    out = []
    for r in rows:
        stock_code = (r.get("stock_code") or "").strip()
        if len(stock_code) != 6:
            continue  # 비상장/기타법인 제외
        if universe is not None and stock_code not in universe:
            continue
        rcept_dt = r.get("rcept_dt", "")  # YYYYMMDD
        if len(rcept_dt) != 8:
            continue
        report_nm = (r.get("report_nm") or "").strip()
        event = classify_event(report_nm)
        if events_only and event is None:
            continue  # 이벤트 드리븐 공시만 (D1 정의 화이트리스트)
        usable_from = datetime.strptime(rcept_dt, "%Y%m%d") + timedelta(days=1)
        out.append(
            {
                "title": f"[{r.get('corp_name', '')}({stock_code})] {report_nm}",
                "content": (
                    f"공시: {r.get('report_nm', '')} / 회사: {r.get('corp_name', '')}"
                    f"({stock_code}) / 접수일: {rcept_dt} / 제출인: {r.get('flr_nm', '')}"
                ),
                "pub_time": usable_from.strftime("%Y-%m-%d %H:%M:%S"),
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r.get('rcept_no', '')}",
            }
        )
    # 빈 결과여도 컬럼 계약 유지 (필터가 전부 걸러낸 날 — 9/21 라이브에서 발견된 버그)
    return pd.DataFrame(out, columns=["title", "content", "pub_time", "url"])


class KrDartDisclosure(KRDataSourceBase):
    def __init__(self, universe: set | None = None):
        super().__init__("kr_dart_disclosure")
        if universe is None:  # 파이프라인의 무인자 생성 경로 — 유니버스 CSV에서 자동 로드
            from utils.kr_universe import load_universe
            universe = set(load_universe())
        self.universe = universe

    def _fetch_page(self, key: str, bgn_de: str, end_de: str, page_no: int) -> dict:
        resp = requests.get(
            DART_LIST_URL,
            params={
                "crtfc_key": key,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "page_no": page_no,
                "page_count": PAGE_COUNT,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def fetch_raw(self, trigger_time: str) -> pd.DataFrame:
        key = _load_dart_key()
        trigger_dt = datetime.strptime(trigger_time, "%Y-%m-%d %H:%M:%S")
        # pub_time = rcept_dt+1 이므로, trigger 당일 사용 가능한 가장 최신 공시는
        # 어제 접수분. 조회 범위는 [trigger - LOOKBACK, trigger - 1일].
        end_de = (trigger_dt - timedelta(days=1)).strftime("%Y%m%d")
        bgn_de = (trigger_dt - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")

        rows, page_no = [], 1
        while True:
            data = self._fetch_page(key, bgn_de, end_de, page_no)
            status = data.get("status")
            if status == "013":  # 조회 결과 없음
                break
            if status != "000":
                raise RuntimeError(f"DART API 오류 status={status}: {data.get('message')}")
            rows.extend(data.get("list", []))
            if page_no >= int(data.get("total_page", 1)):
                break
            page_no += 1
        return parse_dart_rows(rows, self.universe)


if __name__ == "__main__":
    # 라이브 스모크 테스트 (DART 키 필요)
    src = KrDartDisclosure()
    df = src.get_data("2026-09-19 09:00:00")
    print(df.head(10))
    print(f"{len(df)}건 / pub_time 범위: {df['pub_time'].min()} ~ {df['pub_time'].max()}")
