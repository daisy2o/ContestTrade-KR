"""
텔레그램 포함/제외 비교 파일럿.

연구 질문:
  텔레그램이 다른 정보원(DART 공시·Factiva)만으로는 얻지 못하는,
  **원문으로 확인 가능한 판단 근거**를 추가하는가?

설계 — 텔레그램 외의 조건을 맞춘다:
  같은 날짜 · 같은 에이전트(belief) · 같은 모델 · 같은 반복 수 · 같은 도구 출력.
  저장된 리포트를 입력으로 재실행하므로 DART·Factiva 팩터와 tool_call_context는
  **비트 단위로 동일**하다. 유일한 차이는 kr_telegram_research 요약 블록의 유무다.

  C2/C3(콘테스트 온·오프)와는 다른 축이다. C2/C3는 같은 1회 재생본을 오프라인에서
  다르게 **집계**할 뿐 입력이 같다. 여기서는 입력을 바꾸므로 재실행이 필요하다.
  두 축을 한 실험에 섞지 않는다.

교란 요인 (결과에 함께 적을 것):
  텔레그램 블록은 입력의 25~36%다. 제거하면 **내용만이 아니라 입력 길이도** 줄어든다.
  따라서 차이가 나와도 "텔레그램 내용 때문"과 "문맥이 짧아져서"를 이 설계만으로는
  가르지 못한다. 가르려면 Factiva에서 비슷한 분량을 지운 위약(placebo) 조건이
  필요하다 — 이번 파일럿에는 넣지 않았고, 차이가 크게 나오면 그때 추가한다.

알려진 비대칭 (결과 해석 시 반드시 함께 적을 것):
  agent_1의 belief가 "증권사 리서치 추종"이고 텔레그램이 리서치 요약의 주된 공급원이다.
  따라서 텔레그램 제거는 세 에이전트에 **같은 크기로 작용하지 않는다**.
  agent_1의 변화가 크게 나오더라도 그것은 정보원 효과와 belief 배치가 겹친 결과다.

출력: evaluation/out/telegram_ablation_<날짜>.json
"""
import argparse
import asyncio
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.structure_ab import (_tools_info, build_prompt_A, call,  # noqa: E402
                                     load_inputs)

OUT = ROOT / "evaluation" / "out"
TELEGRAM_SOURCE = "kr_telegram_research"

BLOCK_RE = re.compile(
    r"\s*<global_summary>\s*<source>\s*([^<\s]+)\s*</source>.*?</global_summary>", re.S)
SIG_RE = re.compile(r"<signal>(.*?)</signal>", re.S)


def strip_source(bg: str, source: str) -> tuple:
    """해당 소스의 요약 블록을 제거. 제거된 블록 수를 함께 돌려준다(0이면 조작 실패)."""
    removed = []

    def sub(m):
        if m.group(1) == source:
            removed.append(m.group(0))
            return ""
        return m.group(0)

    return BLOCK_RE.sub(sub, bg), removed


def _f(tag, blk):
    m = re.search(f"<{tag}>(.*?)</{tag}>", blk, re.S)
    return m.group(1).strip() if m else ""


def parse_signals(raw: str) -> list:
    out = []
    for blk in SIG_RE.findall(raw or ""):
        if _f("has_opportunity", blk).lower() == "no":
            continue
        out.append({"symbol": _f("symbol_code", blk), "name": _f("symbol_name", blk),
                    "action": _f("action", blk).lower(),
                    "probability": _f("probability", blk),
                    "evidence": _f("evidence", blk)[:2000]})
    return out


def prepare(items: dict, tools: str, strip: bool):
    """조건별 프롬프트를 미리 만든다. 조작 실패는 여기서 즉시 중단한다 —
    조작이 안 된 채 비교하면 '차이 없음'을 잘못 결론낸다."""
    prompts, manip = {}, {}
    for agent, d in items.items():
        d = dict(d)
        if strip:
            bg2, removed = strip_source(d.get("background_information", ""), TELEGRAM_SOURCE)
            manip[agent] = {"제거된_블록수": len(removed),
                            "제거_전_길이": len(d.get("background_information", "")),
                            "제거_후_길이": len(bg2)}
            if not removed:
                raise SystemExit(f"[중단] {agent}: 텔레그램 블록을 찾지 못했다. "
                                 f"입력 형식이 바뀌었는지 확인할 것.")
            d["background_information"] = bg2
        prompts[agent] = build_prompt_A(d, tools)
    return prompts, manip


def flatten(arm: dict) -> Counter:
    """(종목, 방향)별 등장 횟수 — 반복·에이전트를 가로질러 센다."""
    c = Counter()
    for agent, reps in arm.items():
        for r in reps:
            for s in r["signals"]:
                c[(s["symbol"], s["action"])] += 1
    return c


def compare(on: dict, off: dict) -> dict:
    con, coff = flatten(on), flatten(off)
    keys = set(con) | set(coff)
    only_on = sorted(k for k in keys if con[k] and not coff[k])
    only_off = sorted(k for k in keys if coff[k] and not con[k])
    both = sorted(k for k in keys if con[k] and coff[k])
    # 기권(신호 0건) 횟수
    def abstain(a):
        return sum(1 for reps in a.values() for r in reps if not r["signals"])
    return {
        "신호_총수": {"텔레그램_포함": sum(con.values()), "텔레그램_제외": sum(coff.values())},
        "기권_실행수": {"텔레그램_포함": abstain(on), "텔레그램_제외": abstain(off)},
        "포함에만_등장": [{"symbol": k[0], "action": k[1], "횟수": con[k]} for k in only_on],
        "제외에만_등장": [{"symbol": k[0], "action": k[1], "횟수": coff[k]} for k in only_off],
        "양쪽_공통": [{"symbol": k[0], "action": k[1],
                      "포함": con[k], "제외": coff[k]} for k in both],
        "방향이_뒤집힌_종목": sorted({k[0] for k in only_on} & {k[0] for k in only_off}),
        "주의": "같은 입력을 여러 번 돌린 것이지 서로 다른 거래일이 아니다. "
                "이 결과로 **기간 전체의 효과를 주장하지 않는다** — 다음 평가를 할 "
                "가치가 있는지만 판단한다. 검토 전까지는 '추천이 달라졌다'까지만 "
                "말할 수 있고 '텔레그램이 더 나은 근거를 줬다'고 말할 수 없다.",
    }


def review_queue(on: dict, off: dict, cmp_: dict) -> list:
    """사람이 볼 순서 — 차이 난 신호 먼저, 공통 신호는 표본."""
    diff_syms = {x["symbol"] for x in cmp_["포함에만_등장"]} | \
                {x["symbol"] for x in cmp_["제외에만_등장"]}
    q = []
    for arm_name, arm in (("텔레그램_포함", on), ("텔레그램_제외", off)):
        for agent, reps in arm.items():
            for r in reps:
                for s in r["signals"]:
                    pri = "1_차이난_신호" if s["symbol"] in diff_syms else "2_공통_표본"
                    q.append({"우선순위": pri, "arm": arm_name, "agent": agent,
                              "rep": r["rep"], "symbol": s["symbol"], "name": s["name"],
                              "action": s["action"], "evidence": s["evidence"],
                              "확인할_것": "이 근거가 텔레그램 요약에만 있는 내용인가, "
                                          "공시·Factiva에도 있는 내용인가. 원문에서 확인 가능한가."})
    q.sort(key=lambda x: (x["우선순위"], x["symbol"]))
    # 공통 표본은 전수가 아니라 일부만
    seen, out = Counter(), []
    for x in q:
        if x["우선순위"] == "2_공통_표본":
            seen[x["symbol"]] += 1
            if seen[x["symbol"]] > 2:
                continue
        out.append(x)
    return out


async def main(date: str, model: str, reps: int, seed: int = 20260924):
    items = load_inputs(date)
    if not items:
        sys.exit(f"[중단] {date} 리포트 없음")
    tools = _tools_info()
    p_on, _ = prepare(items, tools, strip=False)
    p_off, manip = prepare(items, tools, strip=True)
    total = len(items) * reps * 2
    print(f"{date}: 에이전트 {len(items)} × 반복 {reps} × 2조건 = {total} 호출")

    # 포함·제외를 **섞어서** 실행한다. 한쪽을 몰아서 돌리면 제공 측 상태 변화가
    # 조건 차이로 보일 수 있다. 실행 순서를 기록해 나중에 확인할 수 있게 둔다.
    tasks = [(agent, r + 1, arm)
             for agent in items for r in range(reps) for arm in ("포함", "제외")]
    random.Random(seed).shuffle(tasks)

    on, off, order = {}, {}, []
    for i, (agent, rep, arm) in enumerate(tasks, 1):
        prompt = (p_on if arm == "포함" else p_off)[agent]
        raw = await call(model, prompt)
        rec = {"rep": rep, "raw": raw, "signals": parse_signals(raw), "실행순서": i}
        (on if arm == "포함" else off).setdefault(agent, []).append(rec)
        order.append({"순서": i, "조건": arm, "agent": agent, "rep": rep})
        if i % 10 == 0:
            print(f"  {i}/{total}")
    for d_ in (on, off):
        for a in d_:
            d_[a].sort(key=lambda x: x["rep"])
    print("  실행 완료 (조건 섞어 실행, 순서 기록)")

    cmp_ = compare(on, off)
    res = {
        "meta": {"date": date, "model": model, "reps": reps, "실행순서": order,
                 "실행순서_이유": "포함·제외를 몰아서 돌리면 제공 측 상태 변화가 조건 "
                                  "차이로 보일 수 있어 섞어 실행하고 순서를 남긴다.",
                 "구조": "A(자유 서술) — 구조 B는 본실험 기본값으로 채택하지 않았다",
                 "조작": f"{TELEGRAM_SOURCE} global_summary 블록 제거",
                 "조작_확인": manip,
                 "동일하게_유지한_것": ["날짜", "belief(에이전트)", "모델", "반복 수",
                                       "tool_call_context(도구 출력)", "DART·Factiva 팩터"],
                 "알려진_비대칭": "agent_1의 belief가 '증권사 리서치 추종'이고 텔레그램이 "
                                 "리서치 요약의 주 공급원이다. 텔레그램 제거 효과가 "
                                 "에이전트마다 다르게 작용한다.",
                 "교란요인": "텔레그램 블록이 입력의 25~36%다. 내용과 입력 길이가 함께 "
                             "줄어들므로, 이 설계만으로는 둘을 가르지 못한다. "
                             "위약(Factiva 동일 분량 제거) 조건은 넣지 않았다.",
                 "C2C3와의_관계": "C2/C3는 같은 재생본의 집계 방식 차이다. 이 실험은 입력 차이다. "
                                  "두 축을 한 비교에 섞지 않는다."},
        "비교": cmp_,
        "사람검토_대기열": review_queue(on, off, cmp_),
        "raw": {"텔레그램_포함": on, "텔레그램_제외": off},
    }
    p = OUT / f"telegram_ablation_{date}.json"
    p.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("\n" + json.dumps(cmp_, ensure_ascii=False, indent=1))
    print(f"\n사람 검토 대기열 {len(res['사람검토_대기열'])}건 (차이난 신호 우선)")
    print(f"저장: {p}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    # 최종 판단 모델 고정 (D64). 상위 단계(팩터 요약 등)는 현재 설정 유지.
    ap.add_argument("--model", default="gpt-4.1-2025-04-14")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--seed", type=int, default=20260924)
    a = ap.parse_args()
    asyncio.run(main(a.date, a.model, a.reps, a.seed))
