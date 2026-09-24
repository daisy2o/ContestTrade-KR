"""
근거 검증기 시험용 데이터셋 구축 (Jev citation_check 등 외부 검증기 평가용).

목적: "이 입력 근거가 모델이 쓴 주장을 실제로 뒷받침하는가?"를 기계가 판정할 수
있는지 시험한다. 우리가 이미 사람·에이전트 감사로 판정을 확정한 사례만 쓰므로
파이프라인 재실행이 필요 없다.

설계 규율:
- **기준 조정용(dev)과 최종 시험용(test)을 분리**한다. dev로 임계값을 맞추고
  test는 한 번만 본다. 섞으면 성능이 부풀려진다.
- 오류 사례와 정상 사례를 **함께** 넣는다. 오류만 넣으면 오차단율을 못 잰다.
- 라벨은 우리 감사 결과(problem / clean)이며, 검증기에게는 보여주지 않는다.

출력: evaluation/out/jev_dataset.json
  {"dev": [...], "test": [...]}  각 항목:
  {"id", "structure", "agent", "symbol", "source_text"(입력 근거),
   "claim"(모델 서술·해석), "gold": "problem"|"clean", "gold_detail", "reason"}
"""
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

RECOUNT = ROOT / "evaluation" / "out" / "structure_recount_2026-05-07.json"
REPORTS = ROOT / "agents_workspace" / "reports"
OUT = ROOT / "evaluation" / "out" / "jev_dataset.json"

PROBLEM_VERDICTS = {"사실오류", "원문미발견"}
PROBLEM_EXCESS = {"사실추가"}


def load_banks():
    from evaluation.evidence_bank import build_bank
    banks = {}
    for p in sorted(REPORTS.rglob("2026-05-07_*.json")):
        d = json.loads(p.read_text())
        banks[p.parent.name] = build_bank(d.get("background_information", ""),
                                          d.get("tool_call_context", ""))
    return banks


def load_full_inputs():
    """(날짜, 에이전트) -> 그 실행의 고정 입력 전체.

    검증기에게는 문장 단편이 아니라 '모델이 실제로 받은 입력'을 줘야 공정하다.
    단편만 주면 그 단편이 담지 않은 부분을 근거 없음으로 오판한다(1차 시험의 실패)."""
    full = {}
    for p in sorted(REPORTS.rglob("*_08-30-00.json")):
        date = p.stem.split("_")[0]
        d = json.loads(p.read_text())
        bg = d.get("background_information", "")
        tools = d.get("tool_call_context", "")
        full[(date, p.parent.name)] = (bg + "\n\n[도구 출력]\n" + tools)[:24000]
    return full


def source_for(ev: dict, banks: dict) -> str:
    """이 근거가 기대는 입력 항목의 텍스트. 없으면 빈 문자열."""
    uid = ev.get("ref") or ev.get("mapped_input_id") or ""
    uid = str(uid).strip()
    bank = banks.get(ev.get("agent"), {})
    if uid in bank:
        return bank[uid].text
    # mapped_input_id가 여러 개일 수 있음 (A의 경우 감사자가 특정)
    parts = [bank[u].text for u in str(uid).replace(",", " ").split() if u in bank]
    return " / ".join(parts)


def load_blind_items(full):
    """블라인드 감사 3일 결과에서 사례 수집 (v2 재판정 반영).

    quote가 그 근거가 기댄 입력 항목 역할을 하므로 검증기 시험에 쓸 수 있다.
    단 blind는 v1 기준 감사이므로 audit_consistency_check의 재판정을 덮어쓴다."""
    out = []
    # 재판정 매핑: (date, set, evidence_head 앞 30자) -> recheck_verdict
    recheck = {}
    cpath = ROOT / "evaluation" / "out" / "audit_consistency_check.json"
    if cpath.exists():
        for r in json.loads(cpath.read_text()):
            key = (r.get("date"), r.get("set"), (r.get("evidence_head") or "")[:30])
            recheck[key] = r.get("recheck_verdict")
    for date in ("2026-05-07", "2026-05-29", "2026-06-04"):
        p = ROOT / "evaluation" / "out" / f"blind_{date}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        for setk in ("set_A", "set_B"):
            for i, e in enumerate(d.get(setk, {}).get("evidences", [])):
                src = full.get((date, e.get("agent")), "")
                if not src:
                    continue
                head = (e.get("evidence_head") or "")[:30]
                verdict = recheck.get((date, setk, head), e.get("verdict"))
                is_problem = any(k in (verdict or "") for k in ("사실오류", "원문미발견"))
                out.append({
                    "id": f"BL-{date[-5:]}-{setk[-1]}{i}",
                    "structure": f"blind-{setk[-1]}", "rep": date,
                    "agent": e.get("agent"), "symbol": e.get("symbol"),
                    "source_text": src,
                    "claim": e.get("evidence_head", ""),
                    "gold": "problem" if is_problem else "clean",
                    "gold_detail": verdict or "",
                    "요약_결함": False,
                    "reason": (e.get("reason") or "")[:200],
                })
    return out


def main(seed: int = 7):
    d = json.loads(RECOUNT.read_text())
    banks = load_banks()
    full = load_full_inputs()
    items = []
    for struct in ("A", "B"):
        for i, ev in enumerate(d.get(struct, {}).get("evidences", [])):
            is_problem = (ev.get("verdict") in PROBLEM_VERDICTS) or \
                         (ev.get("excess_type") in PROBLEM_EXCESS)
            src = full.get(("2026-05-07", ev.get("agent")), "")
            if not src:
                continue
            items.append({
                "id": f"{struct}{i}",
                "structure": struct,
                "rep": ev.get("rep"), "agent": ev.get("agent"), "symbol": ev.get("symbol"),
                "source_text": src,
                "claim": ev.get("evidence_head", ""),
                "gold": "problem" if is_problem else "clean",
                "gold_detail": ev.get("verdict") or ev.get("excess_type") or "",
                "요약_결함": ev.get("요약_결함", False),
                "reason": (ev.get("reason") or "")[:200],
            })
    items += load_blind_items(full)
    # 같은 주장이 여러 감사에 중복 등장하면 한 번만
    seen, dedup = set(), []
    for x in items:
        k = (x["claim"][:50], x["source_text"][:50])
        if k in seen:
            continue
        seen.add(k); dedup.append(x)
    items = dedup
    prob = [x for x in items if x["gold"] == "problem"]
    clean = [x for x in items if x["gold"] == "clean"]
    rng = random.Random(seed)
    rng.shuffle(prob); rng.shuffle(clean)
    # 오류 사례가 적으므로 dev에 절반, test에 절반 — 양쪽 모두 정상 사례를 섞는다
    half_p, half_c = len(prob) // 2, len(clean) // 2
    dev = prob[:half_p] + clean[:half_c]
    test = prob[half_p:] + clean[half_c:]
    rng.shuffle(dev); rng.shuffle(test)
    out = {"meta": {"source": "structure_recount_2026-05-07", "seed": seed,
                    "note": "gold는 감사 판정. 검증기에게 보여주지 말 것. dev로 임계값 조정, test는 1회만."},
           "dev": dev, "test": test}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"전체 {len(items)}건 (problem {len(prob)} / clean {len(clean)})")
    print(f"dev {len(dev)}건 (problem {sum(1 for x in dev if x['gold']=='problem')})")
    print(f"test {len(test)}건 (problem {sum(1 for x in test if x['gold']=='problem')})")
    print(f"저장: {OUT}")


if __name__ == "__main__":
    main()
