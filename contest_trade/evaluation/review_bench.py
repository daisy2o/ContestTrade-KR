"""
선택 근거–해석 검수 모델 후보 선별 — 개발용, 마지막 후보 시험.

묻는 것: **잘못된 근거 연결을 걸러내면서 정상 근거를 과도하게 버리지 않는가.**
오통과만 보지 않고 오차단·보류·실행 실패·비용을 함께 보고한다. 오차단이 높으면
정상 근거까지 지워버리므로 검수 도구로 쓸 수 없다.

채택 기준(사전 고정):
  - 검토 대상 표시용으로 쓰려면  오통과율 ≤ 0.30 **그리고** 오차단율 ≤ 0.20
  - 둘 중 하나라도 넘으면 자동 검수 보류 → 소규모 파일럿은 사람이 확인
어느 쪽이든 다음 단계(텔레그램 포함/제외 비교)로 간다. 검수 모델 성공이
프로젝트 진행의 전제 조건이 아니다.

사용: python -m evaluation.review_bench --models deepseek/deepseek-chat-v3-0324,qwen/qwen3-235b-a22b-2507
"""
import argparse
import json
from collections import Counter
from pathlib import Path

from evaluation.verifier_bench import SECRETS, call  # 호출·제공자 고정 로직 재사용

import yaml

ROOT = Path(__file__).parents[1]
SET = ROOT / "evaluation" / "out" / "review_set.json"

VERDICTS = ("연결타당", "연결부적절", "입력에없음", "판정불가")
FLAGGED = ("연결부적절", "입력에없음")

PROMPT = """당신은 금융 리서치의 근거 검수자입니다. 분석가가 **직접 지목한 근거**가
그 분석가의 **해석**을 실제로 뒷받침하는지만 판정하세요. 해석이 투자 판단으로 좋은지는
묻지 않습니다.

{source}

[분석가의 해석]
{claim}

판정 기준:
- 지목한 근거의 회사·기준일·지표 종류·단위·방향이 해석의 내용과 맞는지 확인하세요.
- 해석에 나온 수치가 근거에 실제로 있는지 확인하세요. 근거에 없는 수치나 사실을
  해석이 새로 만들어 냈다면 "입력에없음"입니다.
- **중요**: 근거의 회사와 대상 종목이 다르다는 사실만으로 부적절하다고 판정하지 마세요.
  구분해야 할 두 가지입니다.
    (가) 다른 회사의 문서를 근거로 삼아 **대상 종목에 대한 사실을 주장**한 경우
         → 연결부적절
    (나) 다른 회사의 사건이지만 대상 종목에 미치는 **파급효과를 명시적으로 추론**한 경우
         → 연결부적절이 아님 (추론의 타당성은 묻지 않음)
- 근거가 해석을 뒷받침하지만 해석이 평가어("높은 편", "긍정적")를 덧붙인 경우,
  인용한 사실 자체가 맞다면 "연결타당"으로 두세요. 평가어의 적절성은 판정 대상이 아닙니다.

판정 중 하나:
- 연결타당: 지목한 근거가 해석의 사실 내용을 뒷받침한다
- 연결부적절: 지목한 근거가 해석과 다른 회사·날짜·지표·방향을 가리킨다
- 입력에없음: 해석의 핵심 사실이 지목한 근거에도, 함께 제시된 근거에도 없다
- 판정불가: 정보가 모호해 판단할 수 없다

JSON만 출력하세요.
{{"verdict": "연결타당|연결부적절|입력에없음|판정불가", "quote": "판정 근거가 된 입력 구절(최대 120자)", "why": "한 문장 사유"}}"""


def score(rows: list) -> dict:
    tp = fp = tn = fn = hp = hc = 0
    for r in rows:
        v = r.get("verdict")
        if not v or r["gold"] == "경계":
            continue
        gp = r["gold"] == "problem"
        if v in FLAGGED:
            tp += gp; fp += (not gp)
        elif v == "연결타당":
            fn += gp; tn += (not gp)
        else:
            hp += gp; hc += (not gp)
    np_, nc = tp + fn + hp, tn + fp + hc
    return {"문제_탐지": tp, "오통과": fn, "보류(문제)": hp,
            "정상_통과": tn, "오차단": fp, "보류(정상)": hc,
            "오통과율": round(fn / np_, 3) if np_ else None,
            "오차단율": round(fp / nc, 3) if nc else None,
            "보류율": round((hp + hc) / (np_ + nc), 3) if (np_ + nc) else None,
            "판정된_문제": np_, "판정된_정상": nc}


def verdict_ok(m: dict) -> str:
    fn, fp = m["오통과율"], m["오차단율"]
    if fn is None or fp is None:
        return "판정 불가 — 유효 응답 부족"
    if fn <= 0.30 and fp <= 0.20:
        return "채택 가능 — 검토 대상 '표시용'으로만 사용 (자동 삭제·승인에는 쓰지 않음)"
    return (f"채택 보류 — 기준(오통과≤0.30, 오차단≤0.20) 미달. "
            f"소규모 파일럿은 사람이 확인한다.")


def main(models: list, limit: int):
    key = yaml.safe_load(SECRETS.read_text())["openrouter_api_key"]
    data = json.loads(SET.read_text())
    rows = data["cases"][:limit] if limit else data["cases"]
    print(f"검수 세트 {len(rows)}건 — {dict(Counter(c['gold'] for c in rows))}")
    print(f"근거종류 균형: {data['meta']['근거종류_균형']}\n")

    out = {"set_meta": data["meta"], "models": {}}
    for model in models:
        res, cost, errs, providers = [], 0.0, 0, {}
        print(f"--- {model} ---")
        for i, r in enumerate(rows, 1):
            a = call(key, model, r["source_text"], r["claim"], prompt=PROMPT)
            if a.get("verdict") and a["verdict"] not in VERDICTS:
                a = {"error": f"라벨 체계 불일치: {a['verdict']!r}"}   # 조용히 보류로 세지 않는다
            if "error" in a:
                errs += 1
                res.append({**r, "verdict": None, "error": a["error"]})
            else:
                cost += (a.get("usage", {}) or {}).get("cost", 0) or 0
                pv = a.get("provider", "")
                providers[pv] = providers.get(pv, 0) + 1
                res.append({**r, "verdict": a["verdict"], "quote": a["quote"],
                            "why": a["why"], "provider": pv})
            if i % 20 == 0:
                print(f"  {i}/{len(rows)} … ${cost:.4f} (실행실패 {errs})")
        m = score(res)
        border = [x for x in res if x["gold"] == "경계"]
        out["models"][model] = {
            "cost_usd": round(cost, 5), "실행실패": errs, "providers": providers,
            "metrics": m,
            "경계_판정분포": dict(Counter(str(x.get("verdict")) for x in border)),
            "근거종류별_오통과": {
                k: sum(1 for x in res if x["gold"] == "problem" and x["ref"][0] == k
                       and x.get("verdict") == "연결타당")
                for k in ("S", "T")},
            "채택판정": verdict_ok(m),
            "results": res,
        }
        print(f"  오통과 {m['오통과']}/{m['판정된_문제']} ({m['오통과율']}) | "
              f"오차단 {m['오차단']}/{m['판정된_정상']} ({m['오차단율']}) | "
              f"보류율 {m['보류율']} | 실행실패 {errs} | ${cost:.4f}")
        print(f"  경계 {len(border)}건 판정: {out['models'][model]['경계_판정분포']}")
        print(f"  → {out['models'][model]['채택판정']}\n")

    p = ROOT / "evaluation" / "out" / "review_bench.json"
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
    ap.add_argument("--models", default="deepseek/deepseek-chat-v3-0324,qwen/qwen3-235b-a22b-2507")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    main([m.strip() for m in a.models.split(",")], a.limit)
