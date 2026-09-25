# 팀원 실행 확인 가이드 — 샘플 1일

목적은 **각자 환경에서 파이프라인이 도는지 확인하고 출력을 직접 열어 보는 것**이다.
**전체 학습·본실험을 각자 반복하지 않는다.** 공통 결과는 한 번 생성해 공유한다.

소요: 약 5분 · 비용: 날짜당 약 $0.05

---

## 0. 준비

```bash
git clone <팀 저장소> && cd ContestTrade
uv venv && source .venv/bin/activate
uv pip install -r requirements_kr.txt
```

드라이브에서 받은 sqlite 2개를 그대로 둔다.

```
data_collection/data/telegram/telegram_research.sqlite
data_collection/data/factiva/factiva_news.sqlite
```

`config_kr.yaml`에 **본인 OpenAI 키**를 넣는다. 이 파일은 `skip-worktree`라
커밋되지 않는다 — 키를 커밋하지 말 것.

```yaml
llm:
  model_name: "gpt-4o-mini"          # 상위 단계(팩터 요약·계획·도구 선택)
  api_key: "sk-..."
llm_judgment:
  model_name: "gpt-4.1-2025-04-14"   # 최종 판단 단계만 교체
  api_key: ""                         # 비우면 위 키를 쓴다
```

---

## 1. 샘플 1일 실행

```bash
cd contest_trade
CONTEST_TRADE_MARKET=KR-Stock python -m backtest_runner 2026-06-15 2026-06-15
```

끝나면 이런 줄이 나온다.

```
■ 2026-06-15 08:30:00 완료 (50s, LLM 33회, ~$0.0468)
```

---

## 2. **출력을 반드시 열어 본다**

로그만 보고 "성공"으로 넘기지 않는다. 실제로 팩터가 비어 있는데 완료로 찍힌 적이 있다.

```bash
python - <<'PY'
import json, re
for a in ("agent_0", "agent_1", "agent_2"):
    d = json.load(open(f"agents_workspace/reports/{a}/2026-06-15_08-30-00.json"))
    bg = d["background_information"]
    srcs = re.findall(r"<source>(.*?)</source>", bg)
    sigs = re.findall(r"<symbol_code>(.*?)</symbol_code>", d.get("final_result", "") or "")
    print(f"{a}: 소스 {srcs} | 배경 {len(bg)}자 | 신호 {sigs or '기권'}")
PY
```

**확인할 것**

- 소스 3개(`kr_dart_disclosure`, `kr_factiva_news`, `kr_telegram_research`)가 다 있는가
- 배경 정보가 수천 자 이상인가 (수백 자면 팩터가 비었다는 뜻)
- 신호가 나왔거나, **기권이면 `<signals></signals>`** 인가 (빈 제출은 정상이다)

`final_result` 원문도 한 번 읽어 본다. 근거가 입력에 있는 내용인지 눈으로 본다.

---

## 3. 콘테스트 가중치까지 (선택)

```bash
CONTEST_TRADE_MARKET=KR-Stock python -m evaluation.run_contest_c3 2026-06-15 2026-06-15 08:30:00
```

학습 이력이 부족하면 `insufficient_history`로 뜨고 judge 폴백으로 동작한다.
**이 경우 본래 C3가 아니므로 결과를 섞지 않는다.**

---

## 4. 막힐 때

| 증상 | 원인 |
|---|---|
| `No module named sklearn` | `requirements_kr.txt` 재설치 (scikit-learn 포함됨) |
| 배경 정보가 수백 자 | sqlite 경로 확인 |
| `api_key` 오류 | `config_kr.yaml`의 `llm.api_key` |
| 콘테스트가 신호 0건이라 건너뜀 | 그날 전원 기권 — 정상 |

---

## 하지 않을 것

- 본실험 전체 재생 — 공통 결과를 한 번 만들어 공유한다
- 결과를 좋게 만들기 위한 파라미터 조정
- 새 모델 탐색

**명백한 실행 오류만 고치고 변경 내역을 남긴다.**
