# KR 파일럿 실행 가이드 (팀원용)

> 목표: 각자 자기 컴퓨터에서 KR 파이프라인을 하루치 돌려보고 문제를 찾는다 (9/29 화요일 전체 실행 준비).

## 0. 준비물

- Python 3.11 이상, git
- 본인 OpenAI API 키 (팀 규칙: 키는 각자 발급, 로컬에만 저장, 주간 비용 공유)
- 저장소 접근 권한 (private repo 초대 필요 — 하희정에게 GitHub 아이디 전달)

## 1. 클론 및 설치

```bash
git clone https://github.com/daisy2o/contesttrade-kr-research.git
cd contesttrade-kr-research
git checkout kr-research
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. 데이터 파일 받기 (git에 없음 — 하희정이 전달)

`data/` 폴더는 gitignore 대상입니다. 아래 파일을 받아 같은 위치에 놓으세요.

| 파일 | 위치 | 내용 |
|---|---|---|
| `telegram_research.sqlite` | `data_collection/data/` | 증권사 텔레그램 리서치 (2024-01~) |
| `factiva_news.sqlite` | `data_collection/data/` | Factiva 뉴스 (2026-05~06) |

universe(K-TOP30) 파일은 저장소에 포함되어 있습니다 (`data_collection/universe/ktop30.csv`).

## 3. API 키 설정 (로컬 전용, 커밋 절대 금지)

루트의 `config_kr.yaml`을 열어 두 곳을 채웁니다:

- `llm.api_key`: 본인 OpenAI 키 (`sk-...`)
- 모델은 그대로 `gpt-4o-mini` (팀 합의: 테스트는 저가 모델)

DART 키(공시 수집용)는 `data_collection/kr_secrets.yaml`에 넣습니다. 파일이 없으면:

```yaml
dart_api_key: "본인 DART 키"
```

(DART 키는 https://opendart.fss.or.kr 에서 무료 발급, 1분 소요.)

키 넣은 뒤 실수 커밋 방지 잠금(권장):

```bash
git update-index --skip-worktree config_kr.yaml
```

## 4. 실행

### 4-1. 키 없이 조립 점검 (선택, 무료)

```bash
cd contest_trade
CONTEST_TRADE_MARKET=KR-Stock python dryrun_kr.py 2026-05-29
```

가짜 LLM으로 전체 파이프라인이 크래시 없이 도는지 확인합니다. 마지막 줄에
`✅ 드라이런 완주`가 나오면 정상. **주의: 드라이런이 만든 결과물은 가짜이므로
실제 실행 전에 삭제**: `agents_workspace/factors/`와 `agents_workspace/reports/`에서
해당 날짜 파일 삭제.

### 4-2. 실제 하루 실행 (약 $0.1 이하 예상)

```bash
cd contest_trade
CONTEST_TRADE_MARKET=KR-Stock python backtest_runner.py 2026-05-29 2026-05-29
```

- 시작 시 `replay_id=xxxx`가 출력됩니다 (설정 지문 — 결과 보고 시 함께 적어주세요).
- 소요 시간: 수 분 (데이터 에이전트 요약 + 리서치 에이전트 3개 ReAct).

## 5. 결과 확인

- `contest_trade/agents_workspace/factors/<agent>/2026-05-29_09-00-00.json` — 데이터 팩터 3종
- `contest_trade/agents_workspace/reports/agent_{0,1,2}/2026-05-29_09-00-00.json` — belief별 신호
  - `final_result` 안에 `<signals>...</signals>` 블록이 있으면 신호 산출 성공
- `contest_trade/agents_workspace/replay_*.json` — 실행 로그 (replay_id, 소요 시간)

## 6. 문제 보고 양식 (단톡/노션)

```
[KR 파일럿] 이름 / 날짜 / replay_id
- 실행 구간: 2026-05-29
- 결과: 성공 | 실패(단계: 데이터/리서치/저장)
- 오류 메시지: (있으면 마지막 30줄)
- 비용: OpenAI 대시보드 usage 스크린샷 or 금액
```

## 자주 나는 문제

| 증상 | 원인/해법 |
|---|---|
| `api_key가 없습니다` 중단 | config_kr.yaml의 `llm.api_key` 확인 |
| `ModuleNotFoundError` | `contest_trade/` 폴더 안에서 실행했는지, venv 활성화했는지 확인 |
| DART 수집 0건 | `kr_secrets.yaml`의 DART 키 확인 (또는 환경변수 `DART_API_KEY`) |
| telegram/factiva 0건 | 2번 데이터 파일 위치 확인 |
| 첫 실행이 느림 | 가격 캐시(FDR) 최초 구축 때문 — 두 번째부터 빨라짐 |
