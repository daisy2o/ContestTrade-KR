"""
근거 검수 모델 비교 (OpenRouter 경유).

질문: "이 입력 근거가 모델이 쓴 주장을 지지하는가?"를 어느 모델이 가장 잘 판정하는가.
Jev(systemone)와 달리 일반 chat 모델은 구조화 출력을 JSON으로 받는다.

규율:
- 판정 라벨(gold)은 모델에게 보여주지 않는다.
- dev로 설정을 정하고 test는 1회만. **분할은 판단일 단위**(같은 날 팩터 요약이
  양쪽에 공유되면 test가 독립 검증이 못 된다).
- 첫 지표는 **오통과율**, 그중에서도 우리에게 잦은 유형(합산→개별 귀속, 날짜 이동)을
  통과시키는 비율.
- 실제 신호에는 적용하지 않는다.
- OpenRouter는 같은 모델을 여러 제공자가 서비스할 수 있으므로 **실제 제공자를 기록**한다.

주의: 여기서 좋은 점수를 받아도 백테스트 '판단' 모델 채택과는 별개다. 검수 결과가
과거 추천을 승인·삭제하는 데 쓰이면 그 모델도 백테스트 의사결정에 참여하므로
D9 컷오프 검토가 따로 필요하다.

사용: python -m evaluation.verifier_bench --split dev --models deepseek/deepseek-chat-v3-0324,qwen/qwen3-235b-a22b-2507
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
DATASET = ROOT / "evaluation" / "out" / "jev_dataset.json"
SECRETS = ROOT.parent / "data_collection" / "openrouter_secrets.yaml"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

VERDICTS = ("지지됨", "반박됨", "근거부족", "판정불가")

PROMPT = """당신은 금융 리서치의 근거 검수자입니다.

<입력_근거>
{source}
</입력_근거>

<모델의_주장>
{claim}
</모델의_주장>

위 입력 근거가 모델의 주장을 실제로 뒷받침하는지 판정하세요. 다음을 각각 확인합니다:
회사 귀속(합산 수치를 한 회사 것으로 쓰지 않았는가), 지표 종류, 단위, 기간·비교 창,
사건 날짜(다른 날 수치를 옮기지 않았는가), 방향(상승/하락·증감).

판정 중 하나:
- 지지됨: 입력 근거가 주장을 그대로 뒷받침한다
- 반박됨: 입력 근거가 주장과 어긋난다(수치·날짜·회사·방향이 다름)
- 근거부족: 입력에 그 주장을 뒷받침할 내용이 없다
- 판정불가: 정보가 모호해 판단할 수 없다

JSON만 출력하세요. 설명 문장을 앞뒤에 붙이지 마세요.
{{"verdict": "지지됨|반박됨|근거부족|판정불가", "quote": "판정 근거가 된 입력 구절(최대 120자, 없으면 빈 문자열)", "why": "한 문장 사유"}}"""


def call(key: str, model: str, source: str, claim: str, retries: int = 3,
         prompt: str = None) -> dict:
    """prompt를 주지 않으면 이 모듈의 PROMPT를 쓴다. 다른 시험은 자기 프롬프트를 넘긴다
    (넘기지 않으면 라벨 체계가 어긋나 채점이 전부 '보류'로 떨어진다)."""
    tpl = prompt or PROMPT
    body = {
        "model": model,
        "messages": [{"role": "user", "content": tpl.format(source=source[:20000], claim=claim)}],
        "temperature": 0,
        "max_tokens": 400,
        "provider": {"allow_fallbacks": False},   # 제공자 자동 대체 제한
    }
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for i in range(retries):
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=120).read())
            txt = r["choices"][0]["message"]["content"]
            m = re.search(r"\{.*\}", txt, re.S)
            parsed = json.loads(m.group(0)) if m else {}
            return {"verdict": parsed.get("verdict", ""), "quote": parsed.get("quote", ""),
                    "why": parsed.get("why", ""), "provider": r.get("provider", ""),
                    "usage": r.get("usage", {}), "raw_len": len(txt)}
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and i < retries - 1:
                time.sleep(2 ** i); continue
            return {"error": f"HTTP {e.code}: {e.read().decode()[:160]}"}
        except Exception as e:  # noqa: BLE001
            if i < retries - 1:
                time.sleep(2 ** i); continue
            return {"error": str(e)}
    return {"error": "retries exhausted"}


def score(rows: list, flag_as_problem=("반박됨", "근거부족")) -> dict:
    """flag_as_problem으로 표시된 것을 '문제 표시'로 본다. 판정불가는 보류."""
    tp = fp = tn = fn = hold_p = hold_c = 0
    for r in rows:
        v = r.get("verdict")
        if not v:
            continue
        gp = r["gold"] == "problem"
        if v in flag_as_problem:
            tp += gp; fp += (not gp)
        elif v == "지지됨":
            fn += gp; tn += (not gp)      # fn = 오통과
        else:
            hold_p += gp; hold_c += (not gp)
    np_, nc = tp + fn + hold_p, tn + fp + hold_c
    return {"문제_탐지": tp, "오통과": fn, "보류(문제)": hold_p,
            "정상_통과": tn, "오차단": fp, "보류(정상)": hold_c,
            "오통과율": round(fn / np_, 3) if np_ else None,
            "오차단율": round(fp / nc, 3) if nc else None,
            "보류율": round((hold_p + hold_c) / (np_ + nc), 3) if (np_ + nc) else None}


def main(split: str, models: list, limit: int, dataset: str = "jev"):
    key = yaml.safe_load(SECRETS.read_text())["openrouter_api_key"]
    if dataset == "counterfactual":
        # 입력 변경 대조 진단 세트 (본 시험과 조건이 달라 점수를 직접 비교하지 않는다)
        cf = json.loads((ROOT / "evaluation" / "out" / "counterfactual_set.json").read_text())
        rows = cf["cases"][:limit] if limit else cf["cases"]
        split = "counterfactual"
    else:
        data = json.loads(DATASET.read_text())
        rows = data[split][:limit] if limit else data[split]
    print(f"{split}: {len(rows)}건 (problem {sum(1 for r in rows if r['gold']=='problem')})")

    out = {"split": split, "models": {}}
    for model in models:
        res, cost, errs, providers = [], 0.0, 0, {}
        print(f"\n--- {model} ---")
        for i, r in enumerate(rows, 1):
            a = call(key, model, r["source_text"], r["claim"])
            if "error" in a:
                errs += 1
                res.append({**r, "verdict": None, "error": a["error"]})
            else:
                u = a.get("usage", {})
                cost += u.get("cost", 0) or 0
                pv = a.get("provider", "")
                providers[pv] = providers.get(pv, 0) + 1
                res.append({**r, "verdict": a["verdict"], "quote": a["quote"],
                            "why": a["why"], "provider": pv})
            if i % 25 == 0:
                print(f"  {i}/{len(rows)} … ${cost:.4f}")
        # 우리에게 잦은 유형의 오통과 별도 집계
        key_types = [x for x in res if x["gold"] == "problem" and
                     any(k in (x.get("reason") or "") for k in ("합산", "귀속", "이동", "날짜"))]
        kt_missed = sum(1 for x in key_types if x.get("verdict") == "지지됨")
        out["models"][model] = {
            "cost_usd": round(cost, 5), "errors": errs, "providers": providers,
            "metrics": score(res),
            "핵심유형_오통과": f"{kt_missed}/{len(key_types)}",
            "verdict_분포": {v: sum(1 for x in res if x.get("verdict") == v) for v in VERDICTS},
            "results": res,
        }
        if any("variant" in x for x in res):
            from collections import Counter as _C
            byv = {}
            for v in ("원본", "회사변경", "날짜변경", "합산범위변경"):
                sub = [x for x in res if x.get("variant") == v]
                byv[v] = dict(_C(str(x.get("verdict")) for x in sub))
            out["models"][model]["variant별_판정"] = byv
            print("  variant별 판정:", json.dumps(byv, ensure_ascii=False))
        m = out["models"][model]["metrics"]
        print(f"  오통과율 {m['오통과율']} / 오차단율 {m['오차단율']} / 보류율 {m['보류율']} "
              f"/ 핵심유형 오통과 {kt_missed}/{len(key_types)} / ${cost:.4f} / 제공자 {providers}")

    p = ROOT / "evaluation" / "out" / f"verifier_bench_{split}.json"
    # 후보를 나눠 실행해도 결과가 덮어써지지 않도록 병합 (같은 split 안에서만)
    if p.exists():
        try:
            prev = json.loads(p.read_text())
            if prev.get("split") == split:
                merged = dict(prev.get("models", {}))
                merged.update(out["models"])
                out["models"] = merged
        except Exception:
            pass
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n저장: {p}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--models", default="deepseek/deepseek-chat-v3-0324,qwen/qwen3-235b-a22b-2507")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dataset", default="jev", choices=["jev", "counterfactual"])
    a = ap.parse_args()
    main(a.split, [m.strip() for m in a.models.split(",")], a.limit, a.dataset)
