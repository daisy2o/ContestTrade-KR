"""
C1~C4 통합 파일럿 — 8종목(D32), 개발 날짜, 주평가 Brier.

순서:
  ① 저장 입력(같은 날짜·같은 자료)으로 **p_up·p_down 포함** 판단 출력을 생성
     (기존 저장 출력에는 p_up이 없어 주평가에 쓸 수 없다)
  ② 정답 y = 1[다음 거래일 시가 > 당일 시가] 를 8종목에 대해 계산
  ③ C3 가중치(저장분)를 읽어 C1~C4의 Brier를 계산

규율:
  · 종목군 U_d = D32의 8종목 고정. 데이터 부족을 종목 제외로 처리하지 않는다.
  · 미제출·정상 기권 → 0.5. 형식·실행 실패는 **별도 기록**하되 그 때문에
    조건마다 평가 종목을 빼지 않는다.
  · 평가에 필요한 시가 결측·거래정지는 **모든 조건에 공통 적용**하고 제외 건수를 보고.
  · 가중치가 퇴화한 날짜도 그대로 포함한다(빼지 않는다).
  · C3가 judge 폴백으로 산출된 것이면 결과에 그렇게 적는다 — 본래 C3와 섞지 않는다.

⚠️ C4(6가지 배정의 **평균**)는 C2와 **수학적으로 항등**이다.
   에이전트 a가 모든 순열에서 받는 가중치의 평균 = (w값들의 평균) = 1/N 이므로
   p̂ = Σ_a (1/N)·p_a = 동일가중. 따라서 **평균으로는 대조가 되지 않는다.**
   정보가 있는 쪽은 **순열별 Brier의 분포**와 그 안에서 C3 실제 배정의 위치다.
   합의한 C4 정의는 바꾸지 않고, 분포를 **함께** 보고한다.

출력: evaluation/out/c1c4_pilot.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "evaluation" / "out"
REPORTS = ROOT / "agents_workspace" / "reports"
RAW = OUT / "c1c4_raw"

# D32: 섹터 대표 대형주 8종목 — 앞선 실기동과 동일
UNIVERSE = ["005930", "000660", "005380", "035420",
            "051910", "105560", "005490", "068270"]
MODEL = "gpt-4.1-2025-04-14"


async def generate(date: str, reps: int) -> dict:
    """저장 입력 + 갱신된 프롬프트(p_up/p_down)로 판단 출력을 만든다."""
    from evaluation.structure_ab import _tools_info, build_prompt_A, call
    tools = _tools_info()
    per = {}
    for p in sorted(REPORTS.rglob(f"{date}_08-30-00.json")):
        agent = p.parent.name
        d = json.loads(p.read_text())
        prompt = build_prompt_A(d, tools)
        outs = []
        for _ in range(reps):
            outs.append(await call(MODEL, prompt))
        per[agent] = outs
    return per


def truth_for(date: str, kr_client) -> tuple:
    """y = 1[다음 거래일 시가 > 당일 시가]. 결측·거래정지는 공통 제외하고 센다."""
    from datetime import datetime, timedelta
    d8 = date.replace("-", "")
    end = (datetime.fromisoformat(date) + timedelta(days=21)).strftime("%Y%m%d")
    tds = kr_client.get_trade_dates(d8, end)
    if len(tds) < 2:
        return {}, {"제외": "다음 거래일 미도래", "제외종목": UNIVERSE}
    nxt = tds[1]
    y, excluded = {}, []
    for sym in UNIVERSE:
        try:
            df = kr_client.get_ohlcv(sym, d8, nxt)
            if df is None or len(df) < 2 or "Open" not in df:
                excluded.append({"symbol": sym, "사유": "시가 결측"})
                continue
            o0 = float(df["Open"].iloc[0])
            o1 = float(df["Open"].iloc[-1])
            if o0 <= 0 or o1 <= 0:
                excluded.append({"symbol": sym, "사유": "시가 0 이하(거래정지 의심)"})
                continue
            y[sym] = 1 if o1 > o0 else 0
        except Exception as e:  # noqa: BLE001
            excluded.append({"symbol": sym, "사유": f"조회 실패: {str(e)[:50]}"})
    return y, {"평가종목수": len(y), "제외": excluded,
               "규칙": "결측·거래정지는 **모든 조건에 공통** 적용한다"}


async def main(dates: list, reps: int, regenerate: bool):
    from evaluation.brier_eval import evaluate
    from utils.kr_data_utils import GLOBAL_KR_CLIENT
    RAW.mkdir(parents=True, exist_ok=True)

    runs, truth, excl = {}, {}, {}
    for date in dates:
        f = RAW / f"{date}.json"
        if f.exists() and not regenerate:
            per = json.loads(f.read_text())
            print(f"{date}: 기존 출력 사용")
        else:
            print(f"{date}: 출력 생성 중 ({MODEL}, 반복 {reps})…")
            per = await generate(date, reps)
            f.write_text(json.dumps(per, ensure_ascii=False, indent=1))
        # 반복 중 첫 회만 주평가에 쓴다(반복은 변동 확인용으로 따로 본다)
        runs[date] = {a: outs[0] for a, outs in per.items()}
        y, ex = truth_for(date, GLOBAL_KR_CLIENT)
        truth[date], excl[date] = y, ex
        print(f"   정답 확보 {len(y)}/{len(UNIVERSE)}종목 | 제외 {len(ex.get('제외', []))}건")

    # C3 가중치 — 저장분. 산출 방식(method)을 함께 들고 온다.
    wpath = OUT / ("c3_weights_minpilot.json"
                   if (OUT / "c3_weights_minpilot.json").exists() and
                   set(dates) <= set(json.loads((OUT / "c3_weights_minpilot.json").read_text()))
                   else "c3_weights_pilot.json")
    wraw = json.loads(wpath.read_text()) if wpath.exists() else {}
    weights, methods = {}, {}
    for date in dates:
        v = wraw.get(date) or {}
        w = v.get("weights")
        if w and abs(sum(w.values())) > 1e-9:
            weights[date] = w
        elif w:
            # 전 에이전트 가중치 0 = 콘테스트가 그날 기권했다는 뜻.
            # 합이 0이면 확률 합성이 정의되지 않으므로 **동일가중 대체**를 쓰고
            # 그 사실을 기록한다(임의로 날짜를 빼지 않는다).
            weights[date] = {k: 1.0 / len(w) for k in w}
            methods[date] = (v.get("method", "")) + " + 전원0가중(기권일)→동일가중 대체"
        methods[date] = v.get("method", "미상")

    res = evaluate(runs, weights, {d: UNIVERSE for d in dates}, truth)

    # 실행에 **실제로 사용한 집계 방식**을 이름과 상태에 반영한다.
    # 학습 표본 부족(training_status)과 실제 사용 방식(aggregation_method)을 분리 기록.
    agg = ("judge_fallback" if all(m in ("insufficient_history", "judge_fallback")
                                   for m in methods.values()) else "lightgbm")
    if agg == "judge_fallback" and "C3_콘테스트" in res["결과"]:
        res["결과"]["C3-judge"] = res["결과"].pop("C3_콘테스트")
        res["주_비교"]["이름"] = "BS(C3-judge) − BS(C2)"
    res["실행_상태"] = {
        "training_status": ("insufficient_history"
                            if any(m == "insufficient_history" for m in methods.values())
                            else "trained"),
        "aggregation_method": agg,
        "구분": "학습 표본 부족(training_status)과 실행에 실제 사용한 방식"
                "(aggregation_method)은 다른 항목이다.",
        "해석": ("본래 LightGBM 기반 C3의 성능에 대해서는 **아직 결론을 내릴 수 없다**. "
                 "이 수치는 judge 가중 방식의 결과다."),
    }
    res["파일럿"] = {
        "날짜": dates, "종목군": UNIVERSE, "종목수": len(UNIVERSE),
        "판단모델": MODEL, "반복": reps,
        "C3_산출방식": methods,
        "⚠️": ("C3가 insufficient_history/judge_fallback 으로 산출됐다면 **본래 C3가 "
               "아니라 judge 폴백 경로**다. 본래 C3 결과와 섞지 않는다."),
        "정답_제외": excl,
        "종목군_주의": "D32의 8종목에서의 결과다. KOSPI200 전체로 일반화하거나 "
                       "point-in-time KOSPI200 연구를 완료한 것으로 표현하지 않는다.",
    }
    (OUT / "c1c4_pilot.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))

    print(f"\n{'조건':<22}{'Brier':>10}{'날짜수':>7}")
    for k, v in res["결과"].items():
        bs = v["BS"]
        print(f"{k:<22}{(f'{bs:.4f}' if bs is not None else '-'):>10}{v['날짜수']:>7}")
    print(f"\n주 비교 BS(C3)-BS(C2) = {res['주_비교']['BS(C3)-BS(C2)']}  (음수면 콘테스트 우위)")
    print(f"C3 산출 방식: {methods}")
    print(f"\n저장: {OUT / 'c1c4_pilot.json'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dates", nargs="*", default=["2026-06-15", "2026-06-19", "2026-06-24"])
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--regenerate", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(a.dates or ["2026-06-15", "2026-06-19", "2026-06-24"],
                     a.reps, a.regenerate))
