"""
신호 채점 스켈레톤 (D22 1차 지표: WDA — 가중 방향 적중률, T+1).

재생(backtest_runner)이 저장한 리포트를 읽어 belief별 신호를 파싱하고,
D43-lite 포트폴리오 규칙(상쇄 → 확신도 비례 가중 → 무신호 시 현금)으로
일별 WDA와 가중 수익률을 계산한다.

[잠정 정의 — D8/D22 팀 확정 대기]
- 진입: T일 시가(09:00 결정 직후 체결 가정), 청산: T+1일 종가.
- 확신도 가중치: max(0, probability − 50). 모두 0이면 현금(기권일).
- 에이전트 간 가중: 균등 (D22 잠정).
주의: 여기서의 가격 접근은 채점 전용(SCORING-ONLY)이다. 의사결정 시점에는
전 거래일까지의 가격만 허용된다는 규율과 혼동하지 말 것.

사용: CONTEST_TRADE_MARKET=KR-Stock python -m evaluation.score_signals 2026-05-29 [종료일]
출력: 일별 표 + evaluation/out/scores_<시작>_<종료>.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from evaluation.signal_parser import parse_final_result
from utils.kr_data_utils import GLOBAL_KR_CLIENT

REPORTS_DIR = Path(__file__).parent.parent / "agents_workspace" / "reports"
OUT_DIR = Path(__file__).parent / "out"


def load_day_signals(date: str):
    """해당 일자 리포트 전체에서 유효 신호 수집 (에이전트 균등 가중 잠정)."""
    stamp = f"{date}_09-00-00.json"
    signals = []
    for p in sorted(REPORTS_DIR.rglob(stamp)):
        d = json.loads(p.read_text())
        r = parse_final_result(d.get("final_result") or "")
        for s in r.valid_signals:
            signals.append({
                "agent": p.parent.name,
                "symbol": s.symbol_code,
                "name": s.symbol_name,
                "action": s.action,           # buy | sell
                "prob": s.probability,        # 0~100
            })
    return signals


def build_portfolio(signals):
    """D43-lite: 심볼별 상쇄 → 확신도 비례 가중 → 무신호면 현금."""
    net = {}
    for s in signals:
        conf = max(0.0, (s["prob"] or 50.0) - 50.0)
        sign = 1.0 if s["action"] == "buy" else -1.0
        net[s["symbol"]] = net.get(s["symbol"], 0.0) + sign * conf
    net = {k: v for k, v in net.items() if abs(v) > 1e-9}
    total = sum(abs(v) for v in net.values())
    if not total:
        return {}  # 기권일 → 현금 100%
    return {k: v / total for k, v in net.items()}  # 부호 포함 가중치


def t1_return(symbol: str, date: str) -> float | None:
    """[잠정] T일 시가 → T+1일 종가 수익률. 채점 전용 가격 접근."""
    d8 = date.replace("-", "")
    from datetime import datetime, timedelta
    lookahead = (datetime.strptime(d8, "%Y%m%d") + timedelta(days=14)).strftime("%Y%m%d")
    nxt = GLOBAL_KR_CLIENT.get_trade_dates(d8, lookahead)[:2]
    if len(nxt) < 2:
        return None  # T+1 미도래 (최신 날짜)
    t1 = f"{nxt[1][:4]}-{nxt[1][4:6]}-{nxt[1][6:]}"
    df = GLOBAL_KR_CLIENT.get_ohlcv(symbol, date, t1)
    if df is None or len(df) < 2:
        return None
    entry = float(df.iloc[0]["Open"])
    exit_ = float(df.iloc[-1]["Close"])
    if entry <= 0:
        return None
    return exit_ / entry - 1.0


def score_day(date: str) -> dict:
    signals = load_day_signals(date)
    weights = build_portfolio(signals)
    rows, wda_num, wda_den, port_ret = [], 0.0, 0.0, 0.0
    for sym, w in weights.items():
        r = t1_return(sym, date)
        if r is None:
            rows.append({"symbol": sym, "weight": round(w, 4), "t1_return": None, "hit": None})
            continue
        hit = (r > 0) == (w > 0)  # 방향 적중: 가중치 부호 vs 실현 수익률 부호
        wda_num += abs(w) * (1.0 if hit else 0.0)
        wda_den += abs(w)
        port_ret += w * r
        rows.append({"symbol": sym, "weight": round(w, 4),
                     "t1_return": round(r, 5), "hit": hit})
    return {
        "date": date,
        "n_signals": len(signals),
        "n_positions": len(weights),
        "abstained": not weights,
        "wda": round(wda_num / wda_den, 4) if wda_den else None,
        "weighted_return": round(port_ret, 5) if weights else 0.0,
        "positions": rows,
    }


def main(start: str, end: str | None = None):
    end = end or start
    days = GLOBAL_KR_CLIENT.get_trade_dates(start.replace("-", ""), end.replace("-", ""))
    results = []
    for d8 in days:
        date = f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"
        if not list(REPORTS_DIR.rglob(f"{date}_09-00-00.json")):
            continue  # 재생 안 된 날 스킵
        r = score_day(date)
        results.append(r)
        tag = "기권(현금)" if r["abstained"] else f"WDA={r['wda']} ret={r['weighted_return']:+.4f}"
        print(f"{date}  신호 {r['n_signals']}건 → 포지션 {r['n_positions']}개  {tag}")

    if not results:
        sys.exit("채점할 리포트가 없습니다. backtest_runner로 먼저 재생하세요.")
    scored = [r for r in results if r["wda"] is not None]
    if scored:
        avg_wda = sum(r["wda"] for r in scored) / len(scored)
        print(f"\n요약: {len(results)}일 중 기권 {sum(r['abstained'] for r in results)}일, "
              f"평균 WDA {avg_wda:.4f}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"scores_{start}_{end}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"저장: {out}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("사용법: python -m evaluation.score_signals <시작일> [종료일]")
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
