# 팀원 실행 확인 가이드 — 샘플 1일

목적은 **각자 환경에서 파이프라인이 도는지 확인하고 출력을 직접 열어 보는 것**이다.
**전체 학습·본실험을 각자 반복하지 않는다.** 공통 결과는 한 번 생성해 공유한다.

> ⚠️ 이 브랜치는 **함께 검토할 개발본**이다. 팀의 확정 실험 기준이 아니다.
> 고치지 않은 결함은 아래 **6. 알려진 결함**에 적어 뒀다.

---

## 0. 설치

```bash
git clone -b feat/HJ https://github.com/daisy2o/ContestTrade-KR.git
cd ContestTrade-KR
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt        # 기본 (openai, loguru, PyYAML, pydantic …)
uv pip install -r requirements_kr.txt     # KR 추가 (pykrx, telethon, scikit-learn …)
```

> `requirements_kr.txt`만 깔면 `openai` 등 기본 패키지가 빠져 실행되지 않는다. **둘 다** 깐다.

---

## 1. 키 설정 — **환경변수로**

```bash
export OPENAI_API_KEY="sk-..."     # 필수
export DART_API_KEY="..."          # 필수(공시). https://opendart.fss.or.kr 무료 발급
```

`~/.zshrc`에 넣어두면 매번 입력하지 않아도 된다.

> ⚠️ **`config_kr.yaml`에 키를 직접 쓰지 말 것.** 이 파일은 **Git에 추적되고 있어**
> 키를 넣고 커밋하면 공개된다. 코드가 환경변수를 먼저 읽으므로 YAML은 비워 둔다.
>
> 앞선 안내에서 "`skip-worktree`라 커밋되지 않는다"고 적었는데 **틀렸다.**
> 그건 내 컴퓨터의 로컬 설정이라 **복제되지 않는다** — 새로 받은 환경은
> `git ls-files -v config_kr.yaml`이 `H`(보통 추적)다.

모델 설정은 `config_kr.yaml`에 있다. 키 칸만 비워 두면 된다.

```yaml
llm:
  model_name: "gpt-4o-mini"           # 상위 단계(팩터 요약·계획·도구 선택)
  api_key: ""                          # 비워 둘 것 — 환경변수를 쓴다
llm_judgment:
  model_name: "gpt-4.1-2025-04-14"    # 최종 판단 단계만 교체
  api_key: ""
```

---

## 2. 실행 데이터 받기

코드는 GitHub에서 받지만 **뉴스·텔레그램 데이터는 포함돼 있지 않다.**
별도로 받아야 하고, **모두 같은 데이터를 써야 결과를 비교할 수 있다.**

`파일럿_실행데이터.zip`(약 36MB)을 받아 저장소 루트에서 푼다.

```bash
mkdir -p data_collection/data/telegram data_collection/data/factiva
unzip -o ~/Downloads/파일럿_실행데이터.zip -d /tmp/pilotdata
mv /tmp/pilotdata/telegram_research.sqlite data_collection/data/telegram/
mv /tmp/pilotdata/factiva_news.sqlite      data_collection/data/factiva/
```

같은 파일인지 확인:

```bash
shasum -a 256 data_collection/data/telegram/telegram_research.sqlite
# fc2ffc802c91692b… 로 시작하면 같은 파일
```

> SQLite를 직접 다룰 필요는 없다. 파일만 제자리에 있으면 된다.
> 공용 클라우드에서 함께 실행하면 공용 경로에 한 번 두고 같이 쓰면 된다.

---

## 3. 샘플 1일 실행

```bash
cd contest_trade
CONTEST_TRADE_MARKET=KR-Stock python -m backtest_runner 2026-06-16 2026-06-16
```

```
■ 2026-06-16 08:30:00 완료 (40s, LLM 33회, ~$0.0445)
```

> **`LLM 0회`로 끝나면 실행된 게 아니다.** 러너는 이미 산출물이 있는 날짜를 건너뛴다.
> 다른 미실행 거래일을 쓰거나 해당 날짜의 `agents_workspace/reports/agent_*/` 파일을
> 지우고 다시 돌린다.
>
> ⚠️ 같은 날짜를 다시 돌리면 **이전 보고서를 덮어쓴다.** 남기려면 미리 복사해 둘 것.

---

## 4. **출력을 반드시 열어 본다**

로그만 보고 "성공"으로 넘기지 않는다. 팩터가 비었는데 완료로 찍힌 적이 있다.

```bash
python - <<'PY'
import json, re
DATE = "2026-06-16"
for a in ("agent_0", "agent_1", "agent_2"):
    d = json.load(open(f"agents_workspace/reports/{a}/{DATE}_08-30-00.json"))
    bg, fr = d["background_information"], d.get("final_result", "") or ""
    srcs = re.findall(r"<source>(.*?)</source>", bg)
    yes = [b for b in re.findall(r"<signal>(.*?)</signal>", fr, re.S)
           if "<has_opportunity>yes" in b.replace(" ", "")]
    syms = [re.search(r"<symbol_code>(.*?)</symbol_code>", b, re.S).group(1).strip()
            for b in yes if re.search(r"<symbol_code>", b)]
    print(f"{a}: 소스 {len(srcs)}개 {srcs} | 배경 {len(bg)}자 | "
          f"신호 {syms or '없음(기권)'} | p_up {len(re.findall(r'<p_up>', fr))}")
PY
```

**확인할 것**

- 소스 3개(`kr_dart_disclosure`, `kr_factiva_news`, `kr_telegram_research`)가 다 있는가
- 배경 정보가 수천 자 이상인가 (수백 자면 팩터가 비었다는 뜻)
- 신호가 나왔거나 **기권**인가 — 기권은 정상이다
- **`p_up` 개수가 신호 수와 맞는가** — 0이면 옛 프롬프트로 만든 산출물이다

`final_result` 원문도 한 번 읽어 본다. 근거가 입력에 있는 내용인지 눈으로 본다.

---

## 5. 비용

완료 로그의 `~$0.0445`는 **상위 단계(gpt-4o-mini)만** 집계한 값이다.
**최종 판단(gpt-4.1) 호출은 빠져 있다** — 집계기가 그 모델을 감싸지 않는다.

실제 비용은 이보다 크다. 정확한 값은 OpenAI 대시보드에서 확인할 것.

---

## 6. 알려진 결함

공유 시점에 **고치지 않은 상태**다. 샘플 1일 실행 확인에는 지장이 없지만,
이 코드를 **확정 실험 기준으로 쓰기 전에** 정리해야 한다.

| 결함 | 영향 |
|---|---|
| **모델 시점 검사 우회** | `run_contest_c3` 경로는 저장된 LightGBM 파일을 검사 없이 로드한다. 나중 날짜에 학습한 모델이 더 이른 판단일 재생에 쓰일 수 있다 |
| **확률 단위 혼동** | 평가기가 `p_up=1`을 1%가 아닌 **100%**로 읽는다. 음수·NaN도 통과한다 |
| **옛 채점기 비호환** | `signal_parser`가 `probability`를 필수로 요구해, `p_up/p_down`만 내는 새 출력이 탈락한다 |
| **가중치 검사 부족** | 합만 1이면 통과해 음수 가중치가 허용된다(합성 확률이 범위를 벗어날 수 있음) |
| 같은 날짜 재실행 시 덮어씀 | 반복 실험 결과가 남지 않는다 |

---

## 7. 막힐 때

| 증상 | 원인 |
|---|---|
| `No module named openai` | `requirements.txt` 미설치 |
| `No module named sklearn` | `requirements_kr.txt` 미설치 |
| DART 관련 오류 | `DART_API_KEY` 미설정 |
| `api_key` 오류 | `OPENAI_API_KEY` 미설정 |
| 배경 정보가 수백 자 | sqlite가 위 경로에 없음 |
| **`LLM 0회`로 즉시 끝남** | 그 날짜 산출물이 이미 있어 **건너뛴 것** |
| `p_up`이 0개 | 옛 프롬프트로 만든 산출물 — 새 날짜로 실행 |

---

## 하지 않을 것

- 본실험 전체 재생 — 공통 결과를 한 번 만들어 공유한다
- 결과를 좋게 만들기 위한 파라미터 조정
- 새 모델 탐색

**명백한 실행 오류만 고치고 변경 내역을 남긴다.**
