"""
확대 실험용 날짜 사전 선정 — **출력을 보기 전에** 정한다.

규율:
- 가용성 기준으로만 뽑는다. 결과를 이미 본 날짜(개발용 3일·홀드아웃 3일)는 제외.
- 텔레그램에 새 정보가 풍부한지도 **입력 자료만으로** 분류한다. 출력을 본 뒤
  분류하면 "풍부한 날에 효과가 있었다"가 순환 논증이 된다.
- 최대 10일. 넘치면 기간에 고르게 퍼지도록 균등 간격으로 뽑는다(임의 선택 금지).

가용성 기준 (D54와 동일 계열):
  ① KR 거래일  ② 판단일 직전 2일에 텔레그램 메시지 존재
  ③ 판단일 이전 Factiva 기사 존재  ④ 전 거래일 가격 존재

풍부도 분류 (입력만 사용):
  판단일 as-of 창의 텔레그램 메시지 중 **유니버스 종목이 매핑되고 리서치 신호어
  (목표주가·투자의견·컨센서스·영업이익 등)를 담은 메시지 수**로 상·하를 나눈다.

출력: evaluation/out/expansion_dates.json
"""
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "evaluation" / "out"
DB = ROOT.parent / "data_collection" / "data" / "telegram" / "telegram_research.sqlite"
FACTIVA = ROOT.parent / "data_collection" / "data" / "factiva"

USED = ["2026-05-07", "2026-05-29", "2026-06-04",      # 개발용 — 결과 열람함
        "2026-05-11", "2026-06-12", "2026-07-01"]      # 홀드아웃 — 결과 열람함
MAX_DATES = 10
RESEARCH = re.compile(r"목표주가|투자의견|컨센서스|영업이익|어닝|실적\s*(발표|전망)|상향|하향")


def factiva_db():
    for p in list(FACTIVA.glob("*.sqlite")) + list(FACTIVA.glob("*.db")):
        return p
    return None


def main():
    from utils.kr_data_utils import GLOBAL_KR_CLIENT
    from data_source.kr_telegram_research import tag_stock_codes
    from utils.kr_universe import load_alias_table
    import csv
    with (ROOT.parent / "data_collection" / "universe" / "ktop30.csv").open(encoding="utf-8-sig") as f:
        names = {r["ticker"]: r["name_kr"] for r in csv.DictReader(f)}
    try:
        alias = load_alias_table()
    except Exception:
        alias = None

    days = GLOBAL_KR_CLIENT.get_trade_dates("20260501", "20260701")
    tel = sqlite3.connect(DB)
    fp = factiva_db()
    fac = sqlite3.connect(fp) if fp else None
    fac_table = None
    if fac:
        for (t,) in fac.execute("select name from sqlite_master where type='table'"):
            fac_table = t
            break

    cand = []
    for d8 in days:
        date = f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"
        if date in USED:
            continue
        d = datetime.fromisoformat(date)
        start = (d - timedelta(days=2)).strftime("%Y-%m-%dT00:00")
        end = (d - timedelta(days=1)).strftime("%Y-%m-%dT23:30")
        rows = tel.execute(
            "select text from messages where date_utc>=? and date_utc<? "
            "and text is not null and length(text)>80", (start, end)).fetchall()
        if not rows:
            continue
        # 풍부도: 유니버스 매핑 + 리서치 신호어를 함께 가진 메시지 수 (입력만 사용)
        rich = sum(1 for (t,) in rows
                   if tag_stock_codes(t, names, alias) and RESEARCH.search(t))
        n_fac = 0
        if fac and fac_table:
            try:
                n_fac = fac.execute(
                    f"select count(*) from {fac_table} where pd_date < ?", (date,)).fetchone()[0]
            except Exception:
                n_fac = -1
        cand.append({"date": date, "텔레그램_메시지": len(rows),
                     "리서치신호_메시지": rich, "factiva_누적": n_fac})
    tel.close()
    if fac:
        fac.close()

    ok = [c for c in cand if c["텔레그램_메시지"] > 0 and c["factiva_누적"] != 0]
    # 최대 10일 — 기간에 고르게 퍼지도록 균등 간격 (임의 선택 금지)
    if len(ok) > MAX_DATES:
        step = (len(ok) - 1) / (MAX_DATES - 1)
        pick = [ok[round(i * step)] for i in range(MAX_DATES)]
    else:
        pick = ok

    # 풍부도 상·하 — 선정된 날짜들의 중앙값 기준 (출력을 보기 전에 확정)
    vals = sorted(c["리서치신호_메시지"] for c in pick)
    med = vals[len(vals) // 2]
    for c in pick:
        c["텔레그램_풍부도"] = "상" if c["리서치신호_메시지"] >= med else "하"

    res = {"규율": {
        "선정시점": "출력을 보기 전에 확정한다.",
        "제외": f"결과를 이미 본 날짜 {USED}",
        "가용성": "KR 거래일 + 직전 2일 텔레그램 메시지 존재 + Factiva 존재",
        "풍부도_분류": "as-of 창 텔레그램 중 유니버스 매핑 + 리서치 신호어를 함께 "
                       "가진 메시지 수. 출력이 아니라 **입력만** 본다.",
        "선택방식": f"가용 날짜가 {MAX_DATES}일을 넘으면 기간에 고르게 퍼지도록 "
                    f"균등 간격으로 뽑는다(임의 선택 금지).",
        "중앙값": med,
    }, "후보전체": cand, "선정": pick}
    (OUT / "expansion_dates.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))

    print(f"가용 후보 {len(ok)}일 → 선정 {len(pick)}일 (풍부도 중앙값 {med})")
    print(f"{'날짜':<12}{'텔레그램':>8}{'리서치신호':>10}{'풍부도':>7}")
    for c in pick:
        print(f"{c['date']:<12}{c['텔레그램_메시지']:>8}{c['리서치신호_메시지']:>10}{c['텔레그램_풍부도']:>7}")
    print(f"\n저장: {OUT / 'expansion_dates.json'}")


if __name__ == "__main__":
    main()
