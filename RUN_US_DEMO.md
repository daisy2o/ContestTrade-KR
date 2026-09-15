# US 데모 5분 실행 가이드 (Daisy 셀프 실행용)

> 목적: 키만 발급되면 Claude 없이도 바로 US 데모를 돌릴 수 있게.
> 환경은 이미 구성돼 있음 (.venv, 의존성 설치 완료, CLI 기동 확인 2026-09-12).

## ⚠️ 키 보안 주의

`config_us.yaml`은 **git이 추적하는 파일**입니다. 키를 채운 상태로 커밋/푸시하면 키가 유출됩니다.
- 이 저장소에서 커밋할 일이 생기면 반드시 키를 지운 뒤 커밋
- 나중에 공개 fork를 만들 때는 키 이력이 없는지 확인 (이 로컬에선 아직 커밋 안 했으므로 현재는 안전)

## 1. 키 채우기

`config_us.yaml`을 열고 4곳만 채운다 (나머지는 비워도 됨):

```yaml
fmp_key: "발급받은 FMP 키"
alpha_vantage_key: "발급받은 Alpha Vantage 키"
polygon_key: "발급받은 Polygon 키"

llm:
  provider: "openai"
  base_url: "https://api.deepseek.com"   # DeepSeek 쓰는 경우 그대로
  api_key: "발급받은 LLM 키"
  model_name: "deepseek-chat"
```

- 다른 OpenAI 호환 API를 쓰면 base_url·model_name만 그에 맞게 교체
- llm_thinking / vlm 은 비워두면 일반 LLM으로 대체 동작

## 2. 실행

```bash
cd ~/Documents/Workspace/ContestTrade
source .venv/bin/activate
python -m cli.main run
```

- 터미널 메뉴에서 **US-Stock** 선택, 분석 시각은 기본값(현재) 그대로 Enter
- 데이터 에이전트 2개 → 리서치 에이전트 2개 순서로 돌고, 끝나면 신호 요약 표가 뜸

## 3. 성공 판정 (이게 나오면 "US 데모 재현 ✅"로 기록)

- 에러 없이 완주 + 신호 요약에 종목이 1개 이상
- 상세 리서치 리포트 열람 가능

## 4. 자주 나는 문제

| 증상 | 원인 | 조치 |
|---|---|---|
| 401/403 에러 | 키 오타·미활성 | 키 재확인, 발급 메일 인증 여부 확인 |
| rate limit 에러 | Alpha Vantage 무료 일 25회 제한 | 다음날 재시도 or 데모 1회만 |
| LLM timeout | 잔액 부족·네트워크 | DeepSeek 콘솔에서 잔액 확인 |
| 중간에 멈춤 | 뉴스 크롤 실패 | 재실행 (캐시가 있어 두 번째는 빠름) |

## 5. 실행 후 할 일

- 소요 시간·대략적 토큰 사용량을 기록해서 Claude 세션에 알려주기 (비용 추산 검증용)
- 마일스톤의 "US 데모 재현" 체크 (9/19 마감, 9/21 강등 트리거 연동)
