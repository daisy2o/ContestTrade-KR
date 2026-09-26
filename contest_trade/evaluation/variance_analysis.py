"""분산 분해 분석 — variance_runs.py 산출물을 읽는다.

질문: 에이전트 간 차이(belief)가 반복 간 잡음(샘플링)보다 큰가.
      그리고 temperature 를 낮추면 그 비율이 나아지는가.

A. 종목 선택 안정성 — 고른 종목 집합의 Jaccard
   - 에이전트 내: 같은 에이전트, 다른 반복 사이
   - 에이전트 간: 같은 반복, 다른 에이전트 사이
   내 ≫ 간 이면 belief 가 선택을 가른다. 비슷하면 선택은 잡음이다.
   (둘 다 기권한 쌍은 1로 두고, 그런 쌍의 수를 따로 센다)

B. 확률 분산 분해 — 평가기와 같은 규칙(미제출 = 0.5)으로 채운 p_up 행렬
   (날짜, 종목)마다 에이전트 × 반복 일원분산분석을 하고, 정보가 있는 단위를 합산한다.
   ICC(1) = (MS_간 − MS_내) / (MS_간 + (R−1)·MS_내)
   ICC ≈ 0 이면 '어느 에이전트인가'가 반복 잡음 이상을 설명하지 못한다.
   ⚠️ 이 값은 **현재 집계 방식에서의 에이전트 간 변동 비중**이다. belief 의 순수한
      설명력이 아니다 — 0.5 채움 때문에 종목 선택 차이와 제출 확률 차이가 함께 들어가고,
      정보 있는 단위(전원 동일값이 아닌 (날짜,종목))의 수와 구성이 조건마다 다르다.

C. 순위 안정성 — 경쟁 전제의 직접 시험
   (날짜, 반복)마다 에이전트별 Brier(8종목, 0.5 채움)로 순위를 매기고,
   날짜마다 5회 반복에 걸친 Kendall W(일치도, 동률 보정)를 구한다.
   W ≈ 1 이면 반복해도 같은 에이전트가 잘한다. W ≈ 0 이면 순위는 우연이다.
   ⚠️ 이것은 **정답을 이용한 Brier 순위**다. 콘테스트가 학습하는 수익 기반 순위와 다르다.
   ⚠️ 같은 날짜 안의 반복 일치도다. 날짜를 넘는 순위 지속성(콘테스트의 전제)은 아니다.
   조건별 평균 예측 성능의 우열은 여기서 평가하지 않는다.

D. 확률 일관성 — 오류 판정이 아니라 기록
   p_up·p_down·보합(=100−p_up−p_down)의 일관성과 action 선택 규칙을 센다.
   buy 를 'p_up>50' 으로 정의하지 않았으므로 buy 인데 p_up<50 은 그 자체로 모순이 아니다
   (예: 상승 45·하락 30·보합 25 면 상승이 최대).

사용:
    CONTEST_TRADE_MARKET=KR-Stock python -m evaluation.variance_analysis
"""
import itertools
import json
import re
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
V = ROOT / "evaluation" / "out" / "variance"
AGENTS = ("agent_0", "agent_1", "agent_2")
UNI = ["005930", "000660", "005380", "035420", "051910", "105560", "005490", "068270"]
NE = 0.5


def _num(x: str):
    m = re.search(r"-?\d+(?:\.\d+)?", x or "")
    return float(m.group()) if m else None


def parse(path: Path) -> dict:
    """블록마다 상태를 남긴다 — 형식 문제를 '안 고름'이나 '기권'으로 섞지 않기 위해.

    상태: 유효 / 기권 / 형식실패(action) / 유니버스밖 / 코드형식이상 / p_up누락 / p_up범위밖
    picked 는 has_opportunity=yes 이고 유니버스 안 종목이면 넣는다(p_up 유효성과 무관).
    p 에는 유효한 p_up(0~100)만 넣는다.
    """
    fr = json.loads(path.read_text()).get("final_result") or ""
    blocks = re.findall(r"<signal>(.*?)</signal>", fr, re.S)
    picked, p, st_ = set(), {}, []
    le1 = dup = 0
    cons = {"p_down누락": 0, "확률합>100": 0, "buy인데_p_down>=p_up": 0,
            "sell인데_p_up>=p_down": 0, "action방향이_세결과중_최대아님": 0}
    for b in blocks:
        ho = (re.search(r"<has_opportunity>(.*?)</has_opportunity>", b, re.S) or [None, ""])[1].strip().lower()
        act = (re.search(r"<action>(.*?)</action>", b, re.S) or [None, ""])[1].strip().lower()
        if ho != "yes":
            st_.append("형식실패(action)" if act in ("buy", "sell") else "기권"); continue
        if act not in ("buy", "sell"):
            st_.append("형식실패(action)"); continue
        raw = (re.search(r"<symbol_code>(.*?)</symbol_code>", b, re.S) or [None, ""])[1].strip()
        code = re.search(r"\b(\d{6})\b", raw)
        if not code:
            st_.append("코드형식이상"); continue
        sym = code.group(1)
        if sym not in UNI:
            st_.append("유니버스밖"); continue
        if raw != sym:
            st_.append("코드표기_정규화")      # 정상 처리하되 기록
        dup += sym in picked
        picked.add(sym)
        u = re.search(r"<p_up>(.*?)</p_up>", b, re.S)
        v = _num(u.group(1)) if u else None
        if v is None:
            st_.append("p_up누락"); continue
        if not 0 <= v <= 100:
            st_.append("p_up범위밖"); continue
        le1 += v <= 1
        d_ = re.search(r"<p_down>(.*?)</p_down>", b, re.S)
        dv = _num(d_.group(1)) if d_ else None
        if dv is None or not 0 <= dv <= 100:
            cons["p_down누락"] += 1
        else:
            flat = 100 - v - dv
            cons["확률합>100"] += flat < 0
            cons["buy인데_p_down>=p_up"] += act == "buy" and dv >= v
            cons["sell인데_p_up>=p_down"] += act == "sell" and v >= dv
            top = v if act == "buy" else dv
            cons["action방향이_세결과중_최대아님"] += top < max(v, dv, flat)
        p[sym] = v / 100
        st_.append("유효")
    return {"picked": picked, "p": p, "cons": cons, "le1": le1, "dup": dup,
            "blocks": len(blocks), "status": st_, "empty": not fr.strip()}


def load(tag: str) -> dict:
    """{date: {rep: {agent: parsed}}} — 세 에이전트가 모두 있는 반복만"""
    out = {}
    for ddir in sorted((V / tag).glob("20*")):
        reps = {}
        for rdir in sorted(ddir.glob("rep*")):
            files = {a: rdir / f"{a}.json" for a in AGENTS}
            if all(f.exists() for f in files.values()):
                reps[rdir.name] = {a: parse(f) for a, f in files.items()}
        if reps:
            out[ddir.name] = reps
    return out


def jac(a: set, b: set) -> float:
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def selection(data: dict) -> dict:
    within, between, empty_w, empty_b = [], [], 0, 0
    for reps in data.values():
        rk = list(reps)
        for ag in AGENTS:
            for r1, r2 in itertools.combinations(rk, 2):
                s1, s2 = reps[r1][ag]["picked"], reps[r2][ag]["picked"]
                within.append(jac(s1, s2)); empty_w += not s1 and not s2
        for r in rk:
            for a1, a2 in itertools.combinations(AGENTS, 2):
                s1, s2 = reps[r][a1]["picked"], reps[r][a2]["picked"]
                between.append(jac(s1, s2)); empty_b += not s1 and not s2
    return {"에이전트내_Jaccard": st.mean(within), "에이전트간_Jaccard": st.mean(between),
            "쌍수_내": len(within), "쌍수_간": len(between),
            "둘다기권쌍_내": empty_w, "둘다기권쌍_간": empty_b}


def decompose(data: dict) -> dict:
    ss_b = ss_w = 0.0
    df_b = df_w = 0
    units = 0
    R_used = set()
    for reps in data.values():
        rk = list(reps); R = len(rk); R_used.add(R)
        for sym in UNI:
            X = {a: [reps[r][a]["p"].get(sym, NE) for r in rk] for a in AGENTS}
            flat = [v for a in AGENTS for v in X[a]]
            if max(flat) == min(flat):
                continue                    # 전부 같은 값(대개 전원 미제출) — 정보 없음
            units += 1
            g = st.mean(flat)
            m = {a: st.mean(X[a]) for a in AGENTS}
            ss_b += R * sum((m[a] - g) ** 2 for a in AGENTS)
            ss_w += sum((v - m[a]) ** 2 for a in AGENTS for v in X[a])
            df_b += len(AGENTS) - 1
            df_w += len(AGENTS) * (R - 1)
    if not units:
        return {"정보있는_단위": 0}
    R = max(R_used)
    msb, msw = ss_b / df_b, ss_w / df_w
    icc = (msb - msw) / (msb + (R - 1) * msw) if (msb + (R - 1) * msw) else float("nan")
    return {"정보있는_단위": units, "반복수": R, "SS_간": ss_b, "SS_내": ss_w,
            "간_비중": ss_b / (ss_b + ss_w), "MS_간": msb, "MS_내": msw,
            "F": msb / msw if msw else float("inf"), "ICC1": icc}


def ranks(vals: list) -> list:
    """오름차순 평균 순위(Brier 낮을수록 1위)"""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    r = [0.0] * len(vals); i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def ranking(data: dict, truth: dict) -> dict:
    per_date = {}
    for d, reps in data.items():
        ys = truth.get(d) or {}
        if not ys:
            continue
        rows, best, T = [], [], 0.0
        for r, ag in reps.items():
            bs = [st.mean([(ag[a]["p"].get(s, NE) - ys[s]) ** 2 for s in UNI if s in ys]) for a in AGENTS]
            rows.append(ranks(bs))
            T += sum(c ** 3 - c for c in __import__("collections").Counter(bs).values())
            b = [AGENTS[i] for i, v in enumerate(bs) if v == min(bs)]
            best.append(b[0] if len(b) == 1 else "동률")
        m, n = len(rows), len(AGENTS)
        Rj = [sum(row[i] for row in rows) for i in range(n)]
        S = sum((x - m * (n + 1) / 2) ** 2 for x in Rj)
        W_raw = 12 * S / (m ** 2 * (n ** 3 - n))
        W = 12 * S / (m ** 2 * (n ** 3 - n) - m * T)      # 동률 보정
        per_date[d] = {"Kendall_W": W, "Kendall_W_보정전": W_raw, "동률항_합": T, "반복별_1위": best,
                       "평균순위": {a: Rj[i] / m for i, a in enumerate(AGENTS)}}
    return per_date


def truth_cache(dates: list) -> dict:
    f = V / "truth.json"
    have = json.loads(f.read_text()) if f.exists() else {}
    need = [d for d in dates if d not in have]
    if need:
        from evaluation.run_c1c4_pilot import truth_for
        from utils.kr_data_utils import GLOBAL_KR_CLIENT
        for d in need:
            y, _ = truth_for(d, GLOBAL_KR_CLIENT)
            if y:
                have[d] = y
        f.write_text(json.dumps(have, ensure_ascii=False, indent=2))
    return have


def main():
    tags = [t.name for t in sorted(V.glob("t*")) if t.is_dir()]
    alld = sorted({d.name for t in tags for d in (V / t).glob("20*")})
    truth = truth_cache(alld)
    res = {}
    for tag in tags:
        data = load(tag)
        if not data:
            continue
        from collections import Counter
        allp = [ag[a] for reps in data.values() for ag in reps.values() for a in AGENTS]
        expected = sum(1 for _ in (V / tag).glob("20*/rep*")) * len(AGENTS)
        flags = {"보고서_기대": expected, "보고서_세에이전트완비": len(allp),
                 "final_result_비어있음": sum(x["empty"] for x in allp),
                 "신호블록0개": sum(x["blocks"] == 0 for x in allp),
                 "블록상태": dict(Counter(z for x in allp for z in x["status"])),
                 "같은종목_중복제출": sum(x["dup"] for x in allp),
                 "확률일관성(기록용)": {k: sum(x["cons"][k] for x in allp) for k in allp[0]["cons"]},
                 "p_up<=1(평가기_결함_대상)": sum(x["le1"] for x in allp)}
        res[tag] = {"날짜별_반복수": {d: len(r) for d, r in data.items()},
                    "A_선택": selection(data), "B_분산분해": decompose(data),
                    "C_순위": ranking(data, truth), "점검": flags}
    (V / "analysis.json").write_text(json.dumps(res, ensure_ascii=False, indent=2))

    for tag, r in res.items():
        a, b = r["A_선택"], r["B_분산분해"]
        print(f"\n━━ {tag}  반복수 {r['날짜별_반복수']}")
        print(f"A 선택  Jaccard 에이전트내 {a['에이전트내_Jaccard']:.2f}  vs  에이전트간 {a['에이전트간_Jaccard']:.2f}"
              f"   (둘다기권 쌍: 내 {a['둘다기권쌍_내']}/{a['쌍수_내']}, 간 {a['둘다기권쌍_간']}/{a['쌍수_간']})")
        if b.get("정보있는_단위"):
            print(f"B 확률  정보있는 단위 {b['정보있는_단위']}  |  현재 집계방식의 에이전트간 변동 비중 {b['간_비중']:.0%}  "
                  f"F {b['F']:.2f}  ICC1 {b['ICC1']:.2f}")
        for d, c in r["C_순위"].items():
            print(f"C 순위  {d}  W {c['Kendall_W']:.3f} (보정전 {c['Kendall_W_보정전']:.3f})  1위 {c['반복별_1위']}")
        print(f"점검    {r['점검']}")


if __name__ == "__main__":
    main()
