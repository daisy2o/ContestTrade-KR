# KR 연구 확장 — 현황 지도 (2026-09-15)

> DFMBA 현장적용프로젝트용 확장. 이 파일이 "무엇이 구현·검증됐고 무엇이 아닌지"의 정본.
> 설계 정본은 DaisyVault의 결정로그·측정의미론_스펙_D22.

## 우리가 추가한 것 (원본 ContestTrade 대비)

| 파일 | 역할 | 검증 수준 |
|---|---|---|
| `contest_trade/utils/kr_data_utils.py` | KR 시세·거래일 캘린더·캐시 (백엔드 교체형: FDR→KRX) | 단위테스트 9건 + 실데이터 확인(삼성전자·설연휴 캘린더) |
| `contest_trade/utils/market_manager.py` (KR 분기) | Market.KR, 거래일·가격·비용·판정, KRStockTradingConfig | 단위테스트 12건(네트워크 5건 포함) |
| `contest_trade/utils/date_utils.py` (수정) | `get_previous_trading_date`에 market_name 인자(CN 하드코딩 버그 수정, 기존 호출 호환) | 테스트 경유 |
| `contest_trade/evaluation/signal_parser.py` | 신호 XML 관용 파서 — 형식준수(게이트용)/채점가능 분리, 미폐쇄 블록 실패 계상, 확률은 단일 평문 숫자만 | 단위테스트 9건 (합성 샘플 — golden 픽스처는 첫 실LLM 출력 대기) |
| `contest_trade/config/market_config_kr.yaml` | KR 시장 설정 (개발용 8종목, 거래비용 잠정치) | 파싱 테스트 |
| `contest_trade/tests/` (30건) | 위 전체의 회귀 테스트. `python -m pytest contest_trade/tests -q` | 30 passed |
| `RUN_US_DEMO.md` | 키 채우고 US 데모 셀프 실행 가이드 (키 보안 경고 포함) | 문서 |
| `review/2026-09-15/` | 외부(GPT) 리뷰의 진단 스크립트·증거 JSON | 참고 자료 |

## 검증되지 않은 것 (오해 금지)

- **전체 KR 백테스트 흐름**: 구동 검증됨 — 데이터 소스 3종(DART·Factiva·텔레그램)·재생 하네스(backtest_runner)·채점까지 완주.
- **콘테스트(C3)**: 2026-09-25 배선 수리 완료. 네 조건(단일·동일가중·콘테스트·셔플) 비교가 끝까지 실행된다. **다만 개발본이며 확정 실험 기준이 아니다** — 남은 결함은 `docs/TEAM_RUN_GUIDE.md`의 '알려진 결함' 참조
- **파서의 실LLM 출력 견고성**: 합성 샘플만 통과 — 첫 실행 출력을 golden 픽스처로 승격 예정
- **FDR 시세의 수정주가 여부**: 미확인 (기업행위 채점 정책 D8과 함께)
- **콘테스트 모듈**: 위 결함들(부재 메서드 호출, predictor 초기화가 학습 진입 차단, reward 채점의 달력일·CN-Stock 하드코딩)은 수리했다. 시점 누수 차단(보상 확정 시각·모델 학습 시점)도 넣었다. **단, 모델 시점 검사가 `run_contest_c3` 경로에서 우회되는 문제가 남아 있다**

## 주의 규칙 (코드에 각인됨)

- `get_symbol_price`(KR): **채점 전용** — 판단 경로에서 호출 금지(당일 종가 누출). 판단용은 전 거래일까지의 데이터만
- `get_kospi200_constituents`: point-in-time 테이블(D13) 전까지 의도적으로 에러 — 현재 구성종목의 과거 사용(생존편향) 차단
- 캐시 키에 백엔드 정체성 각인 — 소스 교체 시 옛 캐시 혼용 불가
- `.gitignore`에 `contest_trade/tests/` 예외 — 상류의 `test*` 규칙이 테스트를 무시하던 문제 수정(2026-09-15)

## 환경

- venv: `.venv` (Python 3.11), 추가 의존성: FinanceDataReader, pytest (`uv pip install finance-datareader pytest`)
- 키: `config_us.yaml` — **git 추적 파일이므로 키 넣은 채 커밋 금지**
