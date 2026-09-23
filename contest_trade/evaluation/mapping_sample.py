"""
종목 매핑 사람 검수용 무작위 표본 시트 생성.

매핑률(양)이 아니라 매핑 정확도(질)를 사람이 판정하기 위한 도구.
무작위 시드 고정 — 결과를 보고 표본을 고르는 것을 방지.

사용: python -m evaluation.mapping_sample [표본수=50] [시드=42]
출력: evaluation/out/mapping_sample_<n>_<seed>.csv
      (열: 채널/시각/본문 발췌/매핑 결과/판정[사람 기입]/메모[사람 기입])
"""
import csv
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
TELE_DB = ROOT.parent / "data_collection" / "data" / "telegram" / "telegram_research.sqlite"
WINDOW = ("2025-07-01", "2026-07-01")  # 백테스트 구간


def main(n: int = 50, seed: int = 42):
    sys.path.insert(0, str(ROOT))
    from data_source.kr_telegram_research import tag_stock_codes
    from utils.kr_universe import load_universe, load_alias_table

    uni = load_universe()
    table = load_alias_table(uni)
    with sqlite3.connect(TELE_DB) as conn:
        rows = conn.execute(
            "SELECT channel, date_utc, text FROM messages "
            "WHERE date_utc>=? AND date_utc<? AND text!=''", WINDOW
        ).fetchall()
    rng = random.Random(seed)
    # 매핑된 것/안 된 것 반반 — 위양성과 누락을 모두 검수
    mapped = [(c, d, t) for c, d, t in rows if tag_stock_codes(t, uni, table)]
    unmapped = [(c, d, t) for c, d, t in rows if not tag_stock_codes(t, uni, table)]
    sample = rng.sample(mapped, min(n // 2, len(mapped))) + \
             rng.sample(unmapped, min(n - n // 2, len(unmapped)))
    rng.shuffle(sample)

    out = ROOT / "evaluation" / "out" / f"mapping_sample_{n}_{seed}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["채널", "시각(UTC)", "본문(300자)", "매핑결과",
                    "판정(O=정확/X=오류/△=애매)", "메모"])
        for ch, dt, txt in sample:
            codes = tag_stock_codes(txt, uni, table)
            names = ", ".join(uni[c] for c in codes) if codes else "(매핑 없음)"
            w.writerow([ch, dt, txt[:300].replace("\n", " "), names, "", ""])
    print(f"표본 {len(sample)}건 (매핑 {min(n//2,len(mapped))} + 미매핑 {len(sample)-min(n//2,len(mapped))}) → {out}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 42
    main(n, seed)
