"""
**판단 모델** 후보 비교 — 검수 모델 비교와 다른 축이다.

역할 구분(혼동 금지):
  판단 모델: 뉴스·텔레그램·가격을 보고 종목·방향·근거를 **생성**한다.
  검수 모델: 생성된 근거가 입력으로 뒷받침되는지 **확인**한다.
검수 성적이 좋다고 판단 모델로 뽑지 않는다. 다른 능력이다.

후보와 순서 (후보를 계속 늘리지 않는다):
  **1단계** gpt-4o-mini(현 임시 기준선) vs gpt-4.1
           — 비용을 더 쓰면 근거 품질이 실제로 좋아지는지. 가장 직접적인 비교다.
  **2단계** 1단계 승자 vs deepseek-chat-v3-0324 vs qwen3-235b-a22b-2507
           — 검수가 아니라 **판단 생성 역할**에서도 대안이 되는지.
  gpt-4.1-mini는 비용 절충 후보로 넣을 수 있으나 과거 결과상 우선순위가 낮다.

⚠️ 기존 1일·2반복 탐색 결과(사실오류 4o-mini 29.9% / 4.1-mini 39.3% / 4.1 21.1%)는
확정 근거가 아니다. 그 뒤 감사 기준(rubric v2)과 입력 결함이 함께 바뀌었다.

⚠️ 이 모듈의 자동 지표는 **그 '사실오류'와 같은 지표가 아니다.** 여기 '수치_입력에없음'은
문자열·수치 대조 선별기이고, '사실오류'는 rubric 감사 판정이다. 자동 지표로 순위를
좁힌 뒤 최종 후보에 대해 rubric 감사를 따로 돌려야 과거 수치와 이어 말할 수 있다.

선택 기준 — 텔레그램 인용량은 기준이 **아니다**:
  ① 정확하고 확인 가능한 근거를 생성하는가 (수치·날짜가 고정 입력에 실재)
  ② 회사·날짜·수치 귀속을 보존하는가
  ③ 판단 기준과 기권 규칙을 따르는가 (0~5건, has_opportunity↔action 일관)
  ④ 반복 변동과 비용이 감당 가능한가

규율:
- **개발용 날짜(05-07·05-29·06-04)만 쓴다.** 홀드아웃(05-11·06-12·07-01)은 태우지 않는다.
- 같은 고정 입력·같은 출력 구조(구조 A)·같은 프롬프트. 모델만 바꾼다.
- 자동 지표는 **사람 검토 대상을 좁히는 용도**다. 회사 귀속처럼 파급효과 추론과
  구분이 필요한 항목은 자동 판정하지 않고 검토 대기열로 넘긴다.

사용: CONTEST_TRADE_MARKET=KR-Stock python -m evaluation.judge_model_bench
출력: evaluation/out/judge_model_bench.json
"""
import argparse
import asyncio
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from itertools import combinations
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "evaluation" / "out"
REPORTS = ROOT / "agents_workspace" / "reports"
OR_SECRETS = ROOT.parent / "data_collection" / "openrouter_secrets.yaml"
DEV_DATES = ["2026-05-07", "2026-05-29", "2026-06-04"]

# 공개 시점이 학습 데이터의 상한이다. 넷 다 평가 구간(2026-05~07)보다 먼저 공개돼
# 그 구간으로 오염될 수 없다. 공개 가중치 모델은 공식 컷오프 문서가 없지만
# 이 논거는 컷오프 문서와 무관하게 성립한다.
CANDIDATES = {
    "gpt-4o-mini": {"api": "openai", "공개": "2024-07", "비고": "현 기준선"},
    "gpt-4.1": {"api": "openai", "공개": "2025-04", "비고": "cutoff 2024-06-01 문서화"},
    "deepseek/deepseek-chat-v3-0324": {"api": "openrouter", "공개": "2025-03",
                                       "비고": "D9(9/12) 베이스 탈락 판정 이력 — 기준 ② 완화 전"},
    "qwen/qwen3-235b-a22b-2507": {"api": "openrouter", "공개": "2025-07",
                                  "비고": "D9(9/12) 베이스 탈락 판정 이력 — 기준 ② 완화 전"},
}

# OpenAI는 usage에 비용을 주지 않으므로 토큰 × 단가로 계산한다 (USD per 1M tokens).
# 단가가 바뀌면 여기만 고친다. OpenRouter는 usage.cost를 그대로 쓴다.
PRICE = {"gpt-4o-mini": (0.15, 0.60), "gpt-4.1": (2.00, 8.00), "gpt-4.1-mini": (0.40, 1.60)}

NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
SIG_RE = re.compile(r"<signal>(.*?)</signal>", re.S)
PAIR = re.compile(r"<evidence>(.*?)</evidence>\s*<time>(.*?)</time>\s*<from_source>(.*?)</from_source>", re.S)
DATE_RE = re.compile(r"20\d\d[-년]\s?\d{1,2}[-월]\s?\d{1,2}")


def _f(tag, blk):
    m = re.search(f"<{tag}>(.*?)</{tag}>", blk, re.S)
    return m.group(1).strip() if m else ""


# 한국어 기사는 "48만원", "203만7000원", "1조 달러"처럼 만/억/조 단위를 섞어 쓴다.
# 모델은 이를 480,000처럼 아라비아 숫자로 옮긴다. 확장하지 않고 비교하면 정확히
# 옮긴 수치가 '입력에 없음'으로 잡힌다 — 실측으로 두 모델의 유일한 수치 플래그가
# 전부 이 거짓 양성이었다.
UNIT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(만|억|조)")
MULT = {"만": 10_000, "억": 100_000_000, "조": 1_000_000_000_000}


def expand_units(text: str) -> set:
    out = set()
    for num, unit in UNIT.findall(text or ""):
        try:
            out.add(f"{float(num.replace(',', '')) * MULT[unit]:.6g}")
        except ValueError:
            continue
    # "203만7000원" 같은 복합 표기
    for a, b in re.findall(r"(\d+)\s*만\s*(\d{1,4})", text or ""):
        out.add(f"{int(a) * 10_000 + int(b):.6g}")
    return out


def norm(tokens) -> set:
    out = set()
    for t in tokens:
        s = str(t).replace(",", "")
        try:
            out.add(f"{float(s):.6g}")
        except ValueError:
            continue
    return out


def significant(text: str) -> list:
    """3자리 이상 숫자만. 연도는 뺀다 (우연 일치가 많다)."""
    keep = []
    for t in NUM.findall(text or ""):
        s = t.replace(",", "")
        if len(s.replace(".", "")) < 3 or re.fullmatch(r"20\d\d", s):
            continue
        keep.append(t)
    return keep


def call(model: str, prompt: str, openai_key: str, or_key: str, retries: int = 5) -> dict:
    spec = CANDIDATES[model]
    if spec["api"] == "openai":
        url, key, extra = "https://api.openai.com/v1/chat/completions", openai_key, {}
    else:
        url, key = "https://openrouter.ai/api/v1/chat/completions", or_key
        extra = {"provider": {"allow_fallbacks": False}}   # 제공자 고정
    # max_tokens를 너무 낮게 잡으면 긴 출력을 내는 모델이 **잘려서** 근거 수가 줄고
    # 형식 실패로 집계된다 — 모델 특성이 아니라 설정 결함이 비교를 오염시킨다.
    # (실측: gpt-4.1이 1,684 완성 토큰을 써 상한 2,000에 근접했다.)
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": 6000, **extra}
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for i in range(retries):
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=180).read())
            ch = r["choices"][0]
            return {"text": ch["message"]["content"], "usage": r.get("usage", {}),
                    "provider": r.get("provider", "openai"),
                    # 길이 상한에 걸려 잘렸으면 반드시 드러낸다 — 조용히 넘기면
                    # 그 모델이 근거를 덜 쓴 것처럼 보인다.
                    "truncated": ch.get("finish_reason") == "length"}
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and i < retries - 1:
                time.sleep(5 * (2 ** i)); continue      # 429는 짧은 대기로 풀리지 않는다
            return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
        except Exception as e:  # noqa: BLE001
            if i < retries - 1:
                time.sleep(3 * (i + 1)); continue
            return {"error": str(e)}
    return {"error": "retries exhausted"}


def evaluate(raw: str, fixed_input: str, names: dict, asof: str) -> dict:
    """자동 지표. 판정이 아니라 사람 검토 대상을 좁히는 용도."""
    hay_nums = norm(NUM.findall(fixed_input)) | expand_units(fixed_input)
    sigs = SIG_RE.findall(raw or "")
    res = {"신호수": 0, "기권": 0, "사유있는_기권": 0, "형식실패": 0, "근거수": 0,
           "수치_입력에없음": 0, "날짜_입력에없음": 0,
           "타사명_언급": 0, "출처라벨_불일치": 0, "검토대기": []}
    for blk in sigs:
        ho = _f("has_opportunity", blk).lower()
        act = _f("action", blk).lower()
        sym = _f("symbol_code", blk)
        pairs = PAIR.findall(blk)
        if ho == "no":
            res["기권"] += 1
            # 기권하면서 사유 근거를 붙이는 것은 **허용된 형태**다(D51: 정상 기권과
            # 실행·형식 실패를 구분해 집계). 근거가 붙었다는 이유로 실패로 세면
            # 신중한 모델이 부당하게 나쁘게 보인다 — 실제로 그렇게 오집계했다.
            # 진짜 모순은 기권인데 매수/매도 방향이나 0 아닌 확률을 함께 내는 것이다.
            prob = _f("probability", blk)
            if act in ("buy", "sell"):
                res["형식실패"] += 1
            elif prob and prob not in ("0", "0.0", ""):
                res["형식실패"] += 1
            else:
                res["사유있는_기권"] += 1 if pairs else 0
            continue
        res["신호수"] += 1
        if act not in ("buy", "sell") or not pairs:
            res["형식실패"] += 1
        own = names.get(sym, "")
        for ev, t, src in pairs:
            ev, src = ev.strip(), src.strip()
            res["근거수"] += 1
            # ① 수치가 고정 입력에 실재하는가
            missing = norm(significant(ev)) - hay_nums
            if missing:
                res["수치_입력에없음"] += 1
                res["검토대기"].append({"유형": "수치_입력에없음", "symbol": sym,
                                        "값": sorted(missing), "근거": ev[:180]})
            # ② 날짜가 입력에 있거나 as-of 이전인가
            for d in DATE_RE.findall(ev):
                nd = re.sub(r"[년월]", "-", d).replace(" ", "").rstrip("-")
                parts = [p for p in re.split(r"-", nd) if p]
                if len(parts) == 3:
                    iso = f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
                    if iso not in fixed_input and iso > asof:
                        res["날짜_입력에없음"] += 1
                        res["검토대기"].append({"유형": "as-of_이후_날짜", "symbol": sym,
                                                "값": iso, "근거": ev[:180]})
            # ③ 다른 유니버스 회사명이 나오는가 — 자동 판정하지 않는다.
            #    파급효과 추론일 수 있어 회사명 차이만으로 오류로 보지 않는다(검토 대기).
            others = [n for c, n in names.items() if n and n != own and n in ev]
            if others:
                res["타사명_언급"] += 1
                res["검토대기"].append({"유형": "타사명_언급(파급효과일 수 있음)",
                                        "symbol": f"{sym}({own})", "값": others,
                                        "근거": ev[:180]})
            # ④ 출처 라벨이 내용과 맞는가 — 가격 라벨인데 수치가 도구에 없으면 불일치
            if src in ("price_info", "stock_summary") and not significant(ev):
                res["출처라벨_불일치"] += 1
                res["검토대기"].append({"유형": "출처라벨_불일치(가격 라벨·수치 없음)",
                                        "symbol": sym, "값": src, "근거": ev[:180]})
    res["파싱실패"] = 1 if (raw and not sigs and "<signals>" not in raw) else 0
    res["규칙위반_신호수초과"] = 1 if res["신호수"] > 5 else 0
    return res


def picks(raw: str) -> set:
    out = set()
    for blk in SIG_RE.findall(raw or ""):
        if _f("has_opportunity", blk).lower() == "no":
            continue
        s = _f("symbol_code", blk)
        if s:
            out.add((s, _f("action", blk).lower()))
    return out


def jaccard(a, b):
    return 1.0 if not a and not b else len(a & b) / len(a | b)


async def main(models: list, reps: int):
    import csv
    from evaluation.structure_ab import _tools_info, build_prompt_A
    cfg = yaml.safe_load((ROOT.parent / "config_kr.yaml").read_text())
    openai_key = cfg["llm"]["api_key"]
    or_key = yaml.safe_load(OR_SECRETS.read_text())["openrouter_api_key"]
    with (ROOT.parent / "data_collection" / "universe" / "ktop30.csv").open(encoding="utf-8-sig") as f:
        names = {r["ticker"]: r["name_kr"] for r in csv.DictReader(f)}
    tools = _tools_info()

    inputs = []
    for date in DEV_DATES:
        for p in sorted(REPORTS.rglob(f"{date}_08-30-00.json")):
            d = json.loads(p.read_text())
            inputs.append((date, p.parent.name, d,
                           d.get("background_information", "") + d.get("tool_call_context", "")))
    print(f"개발용 입력 {len(inputs)}건 × 반복 {reps} × 모델 {len(models)} "
          f"= {len(inputs) * reps * len(models)} 호출\n")

    out = {"설계": {
        "역할": "판단 모델(근거 생성) 비교. 검수 모델 비교와 다른 축이며 성적을 섞지 않는다.",
        "날짜": DEV_DATES, "구조": "A(자유 서술)", "반복": reps,
        "컷오프": {m: CANDIDATES[m] for m in models},
        "컷오프_논거": "공개 시점이 학습 데이터의 상한이다. 넷 다 평가 구간(2026-05~07)보다 "
                       "먼저 공개돼 그 구간으로 오염될 수 없다. 공개 가중치 모델은 공식 "
                       "컷오프 문서가 없지만 이 논거는 그와 무관하게 성립한다.",
        "비선택기준": "텔레그램 인용량이 많은 모델을 뽑는 것이 아니다. 전체 정보에 대한 "
                      "판단·근거 품질로 고른다. 검수 성적으로 순위를 정하지 않는다.",
        "자동지표_한계": "회사 귀속은 자동 판정하지 않는다 — 파급효과 추론과 구분이 필요해 "
                          "검토 대기열로만 넘긴다.",
    }, "models": {}}

    for model in models:
        agg = Counter(); cost = 0.0; errs = 0; queue = []; runs = {}
        tok = Counter(); err_detail = []; trunc = 0; raws = {}
        print(f"--- {model} ---")
        for date, agent, d, fixed in inputs:
            prompt = build_prompt_A(d, tools)
            for r in range(reps):
                a = call(model, prompt, openai_key, or_key)
                if "error" in a:
                    errs += 1
                    err_detail.append({"date": date, "agent": agent, "rep": r + 1,
                                       "error": a["error"][:200]})
                    continue
                if a.get("truncated"):
                    trunc += 1
                u = a.get("usage", {}) or {}
                if model in PRICE:      # OpenAI — usage에 cost가 없다
                    pin, pout = PRICE[model]
                    cost += (u.get("prompt_tokens", 0) * pin +
                             u.get("completion_tokens", 0) * pout) / 1_000_000
                else:
                    cost += u.get("cost", 0) or 0
                tok["in"] += u.get("prompt_tokens", 0)
                tok["out"] += u.get("completion_tokens", 0)
                m = evaluate(a["text"], fixed, names, date)
                for k, v in m.items():
                    if isinstance(v, int):
                        agg[k] += v
                for q in m["검토대기"]:
                    queue.append({**q, "date": date, "agent": agent, "rep": r + 1})
                runs.setdefault((date, agent), []).append(picks(a["text"]))
                # 원문을 남긴다 — rubric 감사는 이 출력으로 오프라인에서 돌린다.
                # 남기지 않으면 형식실패·기권의 성격을 확인하려고 다시 과금해야 한다.
                raws.setdefault(date, {}).setdefault(agent, []).append(
                    {"rep": r + 1, "raw": a["text"]})
            print(f"  {date} {agent} … ${cost:.4f}")
        var = [jaccard(x, y) for v in runs.values() for x, y in combinations(v, 2)]
        n_ev = max(agg["근거수"], 1)
        out["models"][model] = {
            "비용_usd": round(cost, 4), "토큰": dict(tok), "실행실패": errs,
            "실행실패_상세": err_detail, "출력잘림": trunc,
            "신호수": agg["신호수"], "기권": agg["기권"], "근거수": agg["근거수"],
            "① 수치_입력에없음": f"{agg['수치_입력에없음']}/{n_ev} ({agg['수치_입력에없음']/n_ev:.1%})",
            "① as-of_이후_날짜": agg["날짜_입력에없음"],
            "② 타사명_언급(검토대기)": agg["타사명_언급"],
            "③ 형식실패": agg["형식실패"], "③ 파싱실패": agg["파싱실패"],
            "③ 사유있는_기권(허용)": agg["사유있는_기권"],
            "③ 신호수초과(>5)": agg["규칙위반_신호수초과"],
            "④ 반복변동_평균일치도": round(sum(var) / len(var), 3) if var else None,
            "출처라벨_불일치": agg["출처라벨_불일치"],
            "검토대기": queue, "raw": raws,
        }
        s = out["models"][model]
        print(f"  근거 {s['근거수']} | 수치미발견 {s['① 수치_입력에없음']} | "
              f"타사명 {s['② 타사명_언급(검토대기)']} | 형식실패 {s['③ 형식실패']} | "
              f"변동 {s['④ 반복변동_평균일치도']} | ${s['비용_usd']}\n")

    p = OUT / "judge_model_bench.json"
    if p.exists():
        try:
            prev = json.loads(p.read_text())
            merged = dict(prev.get("models", {})); merged.update(out["models"])
            out["models"] = merged
        except Exception:
            pass
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"저장: {p}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(CANDIDATES))
    ap.add_argument("--reps", type=int, default=2)
    a = ap.parse_args()
    asyncio.run(main([m.strip() for m in a.models.split(",")], a.reps))
