# 데이터 소스

## 백테스트용 (point-in-time 재현 가능)

| 소스 | 내용 | 수집 스크립트 | 저장 위치 | 필요 키 | 컷오프 |
| :-- | :-- | :-- | :-- | :-- | :-- |
| DART 공시 | OpenDART 공시목록 (`list.json`), 유니버스 30종목 | `scripts/collect_dart.py` | `data/dart.sqlite` | `DART_API_KEY` | 접수 시각 ≤ D 08:30 → D, 그 외 D+1 |
| pykrx 시세·수급 | 일별 OHLCV·시가총액, 투자자별 순매수(외국인·기관·개인·기타법인), KTOP30(5600)·KOSPI200(1028) 지수 | `scripts/collect_market.py` | `data/market.sqlite` | 없음 | D-1 종가까지 |
| 텔레그램 증권사 리서치 4채널 | 신한 리서치·키움 리서치센터·메리츠 리서치·하나증권 리서치 (판정표: `docs/telegram_channels.md`) | `scripts/collect_telegram.py` | `data/telegram.sqlite` | `TG_API_ID`, `TG_API_HASH` | 메시지 시각 < D 08:30 |
| Factiva 경제지 3곳 | 한국경제·매일경제·서울경제 기사 (RTF 내보내기 → SQLite) | `scripts/factiva_rtf_to_sqlite.py` | `data/factiva/*.rtf` → `data/factiva.sqlite` | Factiva 구독(수동 내보내기) | pub_date ≤ D-1 |

유니버스: KTOP30 구성종목 2026-04-30 기준 30종목. 마스터 사본 `config/universe_ktop30_20260430.csv`, 작업 파일 `data/universe.csv` (`scripts/make_universe.py`).

## 실시간 전용 (백테스트에 쓰지 않음)

| 소스 | 내용 | 수집 | 백테스트 제외 사유 |
| :-- | :-- | :-- | :-- |
| 네이버 종목뉴스 | stock.naver.com 종목뉴스 API, KOSPI200 | `scripts/naver_news_collector.py` (매일 실행, 약 2~3주 백필만 가능) | 과거 구간을 소급 수집할 수 없어 point-in-time 재현 불가 |
| open-proxy 컨센서스 | 증권사 실적 추정치·목표주가 컨센서스 (open-proxy MCP) | MCP 호출 (실시간) | 조회 시점 값만 제공, 과거 시점 스냅샷 없음 |

## 사용하지 않는 소스

| 소스 | 사유 |
| :-- | :-- |
| 빅카인즈 (BIG Kinds) | Factiva로 대체. 동일 경제지 기사를 Factiva에서 회사 코드·발행일 메타데이터와 함께 일괄 내보낼 수 있어 중복 수집 불필요 |

## 공통 원칙

- `data/` 아래 실데이터, `*.sqlite`, `*.session`, `config_kr.yaml`, `.env` 는 저장소에 넣지 않는다.
- 모든 소스는 `docs/lookahead_rules.md`의 컷오프를 어댑터 SQL에서 직접 적용한다.
- 원문 저작권: 뉴스·리서치는 요약과 메타데이터 중심으로 사용하고 전문 재배포는 하지 않는다.
