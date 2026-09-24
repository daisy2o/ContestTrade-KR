"""
확대 실험 정리 — 추가 실행 없이 저장 자료만으로 다시 집계한다.

고치는 것:
1. **분모 불일치.** 앞선 귀속 분석은 `parse_signals`가 신호당 **첫 `<evidence>`만**
   담아서 실제 근거의 1/3에서만 돌았다(날짜별 표 146 vs 본문 445). 저장된 raw에서
   `<evidence>/<time>/<from_source>` 삼중을 **전수** 파싱해 하나의 분모로 통일한다.
2. **활용 후보의 3중 연결.** 원문(sqlite)만이 아니라 **그 호출이 실제로 받은 입력**
   (해당 날짜·에이전트의 텔레그램 블록)과 **그 호출의 출력**까지 묶어 확인하고,
   아래로 나눈다.
     · 정확한 활용    : 그 호출의 입력에 있고 원문에도 있음
     · 원문 미확인    : 입력(팩터 요약)에는 있으나 원문에서 못 찾음 → 요약 왜곡 후보
     · 입력 미확인    : 그 호출의 입력에 없음 → 모델이 만든 수치
   뒤 둘은 **정확한 활용과 합산하지 않는다.**
3. **추천 비교에서 형식 실패 분리.** `action`에 서술문이 들어갔거나 기권인데 방향이
   있는 신호를 정상 신호·기권과 섞지 않는다.

출력: evaluation/out/expansion_consolidated.json
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.analyze_telegram_pilot import blocks, keys_of  # noqa: E402
from evaluation.link_to_source_msg import raw_messages  # noqa: E402

OUT = ROOT / "evaluation" / "out"
DATES = ["2026-05-04", "2026-05-13", "2026-05-18", "2026-05-22", "2026-06-01",
         "2026-06-08", "2026-06-15", "2026-06-19", "2026-06-24", "2026-06-30"]
SIG = re.compile(r"<signal>(.*?)</signal>", re.S)
PAIR = re.compile(r"<evidence>(.*?)</evidence>\s*<time>(.*?)</time>\s*<from_source>(.*?)</from_source>", re.S)
VALID_ACTION = {"buy", "sell", "neutral", ""}


def _f(tag, blk):
    m = re.search(f"<{tag}>(.*?)</{tag}>", blk, re.S)
    return m.group(1).strip() if m else ""


def needles(elem: str):
    """정규화 요소를 원문 검색어 후보로 되돌린다.

    **소수 단위 표기를 반드시 만든다.** `1.3e+12`에 대해 "1.3조"를 만들지 못하면,
    원문·입력에 실재하는 값을 '입력 미확인'으로 잘못 분류한다(실측으로 그랬다).
    """
    out = {elem}
    try:
        f = float(elem)
    except ValueError:
        return out
    if f == int(f):
        n = int(f)
        out |= {str(n), f"{n:,}"}
    else:
        # 소수도 쉼표 표기를 만든다 — "8,203.84"를 놓쳐 실재 값을 '입력 미확인'으로
        # 잘못 분류했다.
        out |= {f"{f:,}", f"{f:,.2f}", f"{f:,.1f}"}
    for div, u in ((10**12, "조"), (10**8, "억"), (10**4, "만")):
        if abs(f) >= div:
            q = f / div
            if q == int(q):
                out |= {f"{int(q)}{u}", f"{int(q):,}{u}"}
            # 1.3조 / 219.5억 같은 소수 표기
            out |= {f"{q:g}{u}", f"{q:.1f}{u}".rstrip("0").rstrip(".") + ("" if f"{q:.1f}".endswith("0") else "")}
            out.add(f"{q:.1f}{u}")
            out.add(f"{q:.2f}{u}")
    # 복합 표기 "1만 277" — 원문은 이렇게 쓰고 요약이 "10,277"로 환산한다.
    # 이 형태를 만들지 않으면 정확한 환산을 '원문 미확인(왜곡)'으로 잘못 본다.
    if f == int(f):
        n = int(f)
        for div, u in ((10**8, "억"), (10**4, "만")):
            if n >= div:
                hi, lo = divmod(n, div)
                if lo:
                    out |= {f"{hi}{u} {lo}", f"{hi}{u}{lo}", f"{hi}{u} {lo:,}", f"{hi}{u}{lo:,}"}
    return out


def classify_signal(blk: str) -> str:
    """정상 신호 / 기권 / 형식실패 — 섞지 않는다."""
    ho = _f("has_opportunity", blk).lower()
    act = _f("action", blk).lower()
    prob = _f("probability", blk)
    act_ok = act in VALID_ACTION
    if not act_ok:
        return "형식실패"            # action에 서술문이 들어감
    if ho == "no":
        if act in ("buy", "sell") or (prob and prob not in ("0", "0.0")):
            return "형식실패"        # 기권인데 방향·확률이 있음
        return "기권"
    if ho == "yes" and act in ("buy", "sell"):
        return "정상신호"
    return "형식실패"


def main():
    per_date, cand = {}, []
    for date in DATES:
        d = json.loads((OUT / f"telegram_ablation_{date}.json").read_text())
        msgs = raw_messages(date)
        stat = {}
        picks = {}
        for arm_key, arm_name in (("텔레그램_포함", "포함"), ("텔레그램_제외", "제외")):
            n_ev = tel_only = shared = miss = 0
            kinds = Counter()
            sel = Counter()
            for agent, reps in d["raw"][arm_key].items():
                tel, other = blocks(date, agent)
                tk, ok = keys_of(tel), keys_of(other)
                for r in reps:
                    for blk in SIG.findall(r["raw"]):
                        kind = classify_signal(blk)
                        kinds[kind] += 1
                        if kind == "정상신호":
                            sel[(_f("symbol_code", blk), _f("action", blk).lower())] += 1
                        # 근거는 신호 종류와 무관하게 전수 집계 (분모 통일)
                        for ev, t, src in PAIR.findall(blk):
                            n_ev += 1
                            k = keys_of(ev)
                            t_only = k & tk - ok
                            if t_only:
                                tel_only += 1
                                if arm_name == "포함":
                                    cand.append({
                                        "date": date, "agent": agent, "rep": r["rep"],
                                        "신호종류": kind, "symbol": _f("symbol_code", blk),
                                        "요소": sorted(t_only), "근거": ev.strip()[:220],
                                        "from_source": src.strip()})
                            elif k & ok:
                                shared += 1
                            else:
                                miss += 1
            stat[arm_name] = {"근거_전수": n_ev, "텔레그램고유": tel_only,
                              "다른소스공유": shared, "어느쪽에도없음": miss,
                              "신호분류": dict(kinds)}
            picks[arm_name] = sel
        keys = set(picks["포함"]) | set(picks["제외"])
        per_date[date] = {
            "근거·신호": stat,
            "정상신호_선택빈도": sorted(
                [{"symbol": k[0], "action": k[1], "포함": picks["포함"][k],
                  "제외": picks["제외"][k]} for k in keys],
                key=lambda x: (x["symbol"], x["action"])),
        }

    # 활용 후보 3중 연결 — 그 호출의 입력 + 원문
    for c in cand:
        tel, _ = blocks(c["date"], c["agent"])
        ms = [m for m in raw_messages(c["date"])]
        in_input, in_raw = [], []
        for e in c["요소"]:
            ns = needles(e)
            in_input.append(any(n in tel for n in ns))
            in_raw.append(any(any(n in m["text"] for n in ns) for m in ms))
        c["입력에_있음"] = all(in_input)
        c["원문에_있음"] = all(in_raw)
        c["판정"] = ("정확한 활용" if c["입력에_있음"] and c["원문에_있음"]
                     else "원문 미확인(요약 왜곡 후보)" if c["입력에_있음"]
                     else "입력 미확인(모델 생성)")
    res = {
        "정리규율": {
            "분모": "저장 raw에서 <evidence>/<time>/<from_source> 삼중을 전수 파싱. "
                    "앞선 분석은 신호당 첫 근거만 담아 실제의 1/3이었다(146 vs 445).",
            "활용_후보_연결": "원문(sqlite) + **그 호출이 실제로 받은 입력 블록** + 그 호출의 출력.",
            "분리": "정확한 활용 / 원문 미확인 / 입력 미확인을 합산하지 않는다.",
            "신호분류": "정상신호·기권·형식실패를 섞지 않는다.",
        },
        "날짜별": per_date,
        "활용후보": cand,
        "활용후보_판정요약": dict(Counter(c["판정"] for c in cand)),
    }
    (OUT / "expansion_consolidated.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))

    print(f"{'날짜':<12}{'근거 포함/제외':>14}{'텔레그램고유':>11}{'정상신호':>10}{'기권':>7}{'형식실패':>8}")
    tot = Counter()
    for date, v in per_date.items():
        on, off = v["근거·신호"]["포함"], v["근거·신호"]["제외"]
        print(f"{date:<12}{on['근거_전수']:>6}/{off['근거_전수']:<7}{on['텔레그램고유']:>11}"
              f"{on['신호분류'].get('정상신호',0):>5}/{off['신호분류'].get('정상신호',0):<5}"
              f"{on['신호분류'].get('기권',0):>3}/{off['신호분류'].get('기권',0):<4}"
              f"{on['신호분류'].get('형식실패',0):>4}/{off['신호분류'].get('형식실패',0):<4}")
        for arm, s in v["근거·신호"].items():
            tot[(arm, "근거")] += s["근거_전수"]
            tot[(arm, "고유")] += s["텔레그램고유"]
            for k, n in s["신호분류"].items():
                tot[(arm, k)] += n
    print(f"\n합계 포함: 근거 {tot[('포함','근거')]} | 텔레그램고유 {tot[('포함','고유')]} | "
          f"정상 {tot[('포함','정상신호')]} 기권 {tot[('포함','기권')]} 형식실패 {tot[('포함','형식실패')]}")
    print(f"합계 제외: 근거 {tot[('제외','근거')]} | 텔레그램고유 {tot[('제외','고유')]} | "
          f"정상 {tot[('제외','정상신호')]} 기권 {tot[('제외','기권')]} 형식실패 {tot[('제외','형식실패')]}")
    print(f"\n활용 후보 판정: {res['활용후보_판정요약']}")


if __name__ == "__main__":
    main()
