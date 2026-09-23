"""
데이터 깔때기 리포트: 수집 → 종목 매핑 → 팩터 인용 → 최종 신호의 단계별 분포.

'추천 집중'의 원인이 입력 편중인지, 요약·판단 단계의 증폭인지 구분하는 도구.
주의: 팩터 인용은 references(요약이 실제 인용한 원문) 기준이고, 신호의
from_source 표기는 신뢰하지 않는다(출처 주장일 뿐 — 감사로 확인된 한계).

사용: python -m evaluation.funnel_report 2026-05-04 2026-05-06 2026-05-07 ...
출력: 단계별 종목 분포표 + 상위 집중도, evaluation/out/funnel_<first>_<last>.json
"""
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parents[1]
TELE_DB = ROOT.parent / "data_collection" / "data" / "telegram" / "telegram_research.sqlite"


def funnel(dates: list) -> dict:
    sys.path.insert(0, str(ROOT))
    from data_source.kr_telegram_research import tag_stock_codes, LOOKBACK_DAYS
    from utils.kr_universe import load_universe, load_alias_table
    from evaluation.signal_parser import parse_final_result

    uni = load_universe()
    table = load_alias_table(uni)

    tele_tag, tele_total = Counter(), 0
    with sqlite3.connect(TELE_DB) as conn:
        for d in dates:
            t = pd.Timestamp(d + " 09:00:00") - pd.Timedelta(hours=9)
            since, until = (t - pd.Timedelta(days=LOOKBACK_DAYS)).isoformat(), t.isoformat()
            for (m,) in conn.execute(
                "SELECT text FROM messages WHERE date_utc>=? AND date_utc<? AND text!=''", (since, until)
            ):
                tele_total += 1
                for c in tag_stock_codes(m, uni, table):
                    tele_tag[c] += 1

    ref_tag = Counter()
    for d in dates:
        for src in ["kr_factiva_news", "kr_telegram_research", "kr_dart_disclosure"]:
            p = ROOT / "agents_workspace" / "factors" / src / f"{d}_09-00-00.json"
            if not p.exists():
                continue
            for r in json.loads(p.read_text()).get("references", []):
                text = (r.get("title", "") + " " + str(r.get("content", ""))[:200])
                for c in tag_stock_codes(text, uni, table):
                    ref_tag[c] += 1

    sig_tag = Counter()
    for d in dates:
        for p in sorted((ROOT / "agents_workspace" / "reports").rglob(f"{d}_09-00-00.json")):
            res = parse_final_result(json.loads(p.read_text()).get("final_result", ""))
            for s in res.valid_signals:
                sig_tag[s.symbol_code] += 1

    def share(counter, codes):
        tot = sum(counter.values())
        return round(sum(counter[c] for c in codes) / tot, 3) if tot else None

    top2 = [c for c, _ in (tele_tag + ref_tag + sig_tag).most_common(2)]
    return {
        "dates": dates,
        "telegram": {"messages": tele_total, "mapped": sum(tele_tag.values()),
                     "by_stock": {uni[c]: n for c, n in tele_tag.most_common()}},
        "factor_references": {"by_stock": {uni[c]: n for c, n in ref_tag.most_common()}},
        "signals": {"by_stock": {uni[c]: n for c, n in sig_tag.most_common()}},
        "top2_stocks": [uni[c] for c in top2],
        "top2_share_by_stage": {
            "input_mapping": share(tele_tag, top2),
            "factor_refs": share(ref_tag, top2),
            "signals": share(sig_tag, top2),
        },
    }


def main(dates):
    r = funnel(dates)
    print(f"구간 {dates[0]}~{dates[-1]} ({len(dates)}일)")
    print(f"텔레그램: {r['telegram']['messages']}건 중 매핑 {r['telegram']['mapped']}건")
    print(f"상위 2종목 {r['top2_stocks']} 비중: 입력 {r['top2_share_by_stage']['input_mapping']} "
          f"→ 팩터 인용 {r['top2_share_by_stage']['factor_refs']} → 신호 {r['top2_share_by_stage']['signals']}")
    out = ROOT / "evaluation" / "out" / f"funnel_{dates[0]}_{dates[-1]}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2))
    print(f"저장: {out}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("사용법: python -m evaluation.funnel_report <날짜> [날짜 ...]")
    main(sys.argv[1:])
