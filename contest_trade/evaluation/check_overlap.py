"""
평가 자료의 원문 중복 점검.

새 날짜를 골랐다고 자료가 독립인 것은 아니다. 입력 기간(텔레그램 직전 2일,
Factiva D+1)이 겹치거나 같은 기사가 여러 날 인용되면 원문이 재등장한다.
검수 모델 최종 평가 자료를 만들 때 이 중복을 확인하지 않으면, 튜닝에 쓴 원문이
평가에 다시 들어온다.

점검 단위:
- Factiva: 기사 ID(an)
- 텔레그램: (채널, message_id)
- 판단일 간 교집합 비율

사용: python -m evaluation.check_overlap 2026-05-07 2026-05-29 2026-06-04 -- 2026-05-11 2026-06-12 2026-07-01
      ('--' 앞: 기존(개발용) 날짜, 뒤: 새(평가용) 날짜)
"""
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parents[1]
FACT_DB = ROOT.parent / "data_collection" / "data" / "factiva" / "factiva_news.sqlite"
TELE_DB = ROOT.parent / "data_collection" / "data" / "telegram" / "telegram_research.sqlite"
LOOKBACK_TELE_DAYS = 2
LOOKBACK_FACT_DAYS = 3


def docs_for(date: str) -> tuple:
    """그 판단일의 입력에 들어갈 수 있는 문서 집합 (어댑터와 같은 시간 규칙)."""
    t_utc = pd.Timestamp(f"{date} 08:30:00") - pd.Timedelta(hours=9)
    with sqlite3.connect(TELE_DB) as c:
        tele = {f"{ch}/{mid}" for ch, mid in c.execute(
            "SELECT channel, message_id FROM messages WHERE date_utc>=? AND date_utc<? AND text!=''",
            ((t_utc - timedelta(days=LOOKBACK_TELE_DAYS)).isoformat(), t_utc.isoformat()))}
    with sqlite3.connect(FACT_DB) as c:
        fact = {an for (an,) in c.execute(
            "SELECT an FROM articles WHERE pd_date>=? AND pd_date<?",
            ((pd.Timestamp(date) - pd.Timedelta(days=LOOKBACK_FACT_DAYS)).strftime("%Y-%m-%d"), date))}
    return tele, fact


def main(old: list, new: list):
    old_t, old_f = set(), set()
    for d in old:
        t, f = docs_for(d)
        old_t |= t; old_f |= f
    print(f"기존(개발용) {len(old)}일 → 텔레그램 {len(old_t)}건 / Factiva {len(old_f)}건")
    print()
    tot_t = tot_f = ov_t = ov_f = 0
    for d in new:
        t, f = docs_for(d)
        it, if_ = t & old_t, f & old_f
        tot_t += len(t); tot_f += len(f); ov_t += len(it); ov_f += len(if_)
        print(f"{d}: 텔레그램 {len(t):4d}건 중 중복 {len(it):3d} ({len(it)/max(1,len(t)):.0%}) | "
              f"Factiva {len(f):4d}건 중 중복 {len(if_):3d} ({len(if_)/max(1,len(f)):.0%})")
    print()
    print(f"합계 중복률 — 텔레그램 {ov_t}/{tot_t} ({ov_t/max(1,tot_t):.1%}), "
          f"Factiva {ov_f}/{tot_f} ({ov_f/max(1,tot_f):.1%})")
    # 새 날짜들끼리의 중복도 본다 (같은 기사가 3일에 걸쳐 재등장하는지)
    print("\n새 날짜 간 중복:")
    for i, a in enumerate(new):
        for b in new[i + 1:]:
            ta, fa = docs_for(a); tb, fb = docs_for(b)
            print(f"  {a} ∩ {b}: 텔레그램 {len(ta & tb)}건, Factiva {len(fa & fb)}건")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--" not in args:
        sys.exit("사용법: python -m evaluation.check_overlap <기존날짜...> -- <새날짜...>")
    i = args.index("--")
    main(args[:i], args[i + 1:])
