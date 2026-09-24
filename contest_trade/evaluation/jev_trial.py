"""
Jev 근거 검증기 시험 (OpenRouter 경유).

질문: "이 입력 근거가 모델이 쓴 주장을 실제로 뒷받침하는가?"
우리가 이미 감사로 판정한 사례(problem/clean)에 Jev를 돌려, 검수 보조로 쓸 만한지 잰다.

규율:
- dev로 임계값을 맞추고 test는 한 번만 본다.
- 첫 지표는 **오통과율**(problem을 통과시킨 비율). 오류를 놓치면 검수 보조로 못 쓴다.
- 오차단율(clean을 막은 비율), 보류율, 비용도 함께 본다.
- Jev에게 우리 라벨을 보여주지 않는다.
- 이 결과로 실제 신호를 차단하지 않는다. 검토 대상 표시용 비교일 뿐.

주의: Jev 문서는 영어가 주 언어이고 CJK는 동등하지 않다고 명시한다. 우리 데이터는
한국어이고 오류에 숫자·날짜가 많아, 성능은 직접 재봐야 한다(이 스크립트의 목적).

사용: python -m evaluation.jev_trial --split dev [--limit N]
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
DATASET = ROOT / "evaluation" / "out" / "jev_dataset.json"
SECRETS = ROOT.parent / "data_collection" / "openrouter_secrets.yaml"
ENDPOINT = "https://openrouter.ai/api/v1/systemone"

INSTRUCTIONS = (
    "Does the SOURCE evidence actually support the CLAIM? "
    "Check company attribution, metric type, unit, period/window, event date, and direction. "
    "Answer yes only if the source genuinely supports the claim as stated."
)


def call_jev(key: str, source: str, claim: str, retries: int = 3) -> dict:
    body = {
        "model": "jev-1.13",
        "state": f"SOURCE EVIDENCE:\n{source}\n\nCLAIM BY MODEL:\n{claim}",
        "questions": {"supported": {"type": "noul", "instructions": INSTRUCTIONS}},
    }
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for i in range(retries):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=60).read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and i < retries - 1:
                time.sleep(2 ** i)
                continue
            return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
        except Exception as e:  # noqa: BLE001
            if i < retries - 1:
                time.sleep(2 ** i)
                continue
            return {"error": str(e)}
    return {"error": "retries exhausted"}


def evaluate(rows: list, low: float, high: float) -> dict:
    """low 미만 = 문제로 표시, high 이상 = 통과, 사이 = 보류(사람 검토)."""
    tp = fp = tn = fn = hold_p = hold_c = 0
    for r in rows:
        s = r.get("noul")
        if s is None:
            continue
        gold_problem = r["gold"] == "problem"
        if s < low:              # 검증기가 '문제'로 판정
            if gold_problem:
                tp += 1
            else:
                fp += 1
        elif s >= high:          # 검증기가 '통과'로 판정
            if gold_problem:
                fn += 1          # ← 오통과: 가장 중요한 실패
            else:
                tn += 1
        else:                    # 보류
            hold_p += int(gold_problem)
            hold_c += int(not gold_problem)
    n_prob = tp + fn + hold_p
    n_clean = tn + fp + hold_c
    return {
        "threshold_low": low, "threshold_high": high,
        "문제_탐지": tp, "오통과": fn, "보류(문제)": hold_p,
        "정상_통과": tn, "오차단": fp, "보류(정상)": hold_c,
        "오통과율": round(fn / n_prob, 3) if n_prob else None,
        "오차단율": round(fp / n_clean, 3) if n_clean else None,
        "보류율": round((hold_p + hold_c) / (n_prob + n_clean), 3) if (n_prob + n_clean) else None,
    }


def main(split: str, limit: int):
    key = yaml.safe_load(SECRETS.read_text())["openrouter_api_key"]
    data = json.loads(DATASET.read_text())
    rows = data[split][:limit] if limit else data[split]
    print(f"{split}: {len(rows)}건 (problem {sum(1 for r in rows if r['gold']=='problem')})")

    results, cost, errors = [], 0.0, 0
    for i, r in enumerate(rows, 1):
        resp = call_jev(key, r["source_text"], r["claim"])
        if "error" in resp:
            errors += 1
            results.append({**r, "noul": None, "error": resp["error"]})
        else:
            noul = resp.get("answers", {}).get("supported", {}).get("noul")
            cost += resp.get("usage", {}).get("cost", 0) or 0
            results.append({**r, "noul": noul})
        if i % 20 == 0:
            print(f"  {i}/{len(rows)} … 누적 ${cost:.4f}")

    out = ROOT / "evaluation" / "out" / f"jev_trial_{split}.json"
    summary = {}
    if split == "dev":
        # dev에서만 임계값 탐색 (test는 확정 임계값으로 1회)
        best = None
        for low in (0.3, 0.4, 0.5, 0.6, 0.7):
            for high in (0.7, 0.8, 0.9):
                if high <= low:
                    continue
                m = evaluate(results, low, high)
                if m["오통과율"] is None:
                    continue
                # 오통과를 최우선으로 낮추고, 동률이면 보류율이 낮은 쪽
                k = (m["오통과율"], m["보류율"] or 1.0, m["오차단율"] or 1.0)
                if best is None or k < best[0]:
                    best = (k, m)
        summary = {"grid_best": best[1] if best else None,
                   "all": [evaluate(results, l, h) for l in (0.3, 0.5, 0.7) for h in (0.8, 0.9)]}
    else:
        # dev 그리드 탐색으로 확정한 임계값 — test는 이 값으로 1회만
        summary = {"fixed_from_dev": evaluate(results, 0.7, 0.9)}

    out.write_text(json.dumps({"split": split, "cost_usd": round(cost, 5),
                               "errors": errors, "summary": summary,
                               "results": results}, ensure_ascii=False, indent=1))
    print(f"\n비용 ${cost:.5f} / 오류 {errors}건")
    print(json.dumps(summary, ensure_ascii=False, indent=1)[:1200])
    print(f"저장: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    main(a.split, a.limit)
