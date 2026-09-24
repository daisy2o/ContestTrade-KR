"""
텔레그램 전달 경로 추적 — 원문부터 출력까지 어디서 끊기는지 본다.

묻는 것은 "텔레그램이 효과가 있나?"가 아니라
**"쓸 만한 텔레그램 정보가 모델에게 도달했고, 어떻게 사용됐나?"**다.

경로 6단계:
  ① 원문        telegram_research.sqlite, as-of 창 안의 메시지
  ② 종목 매핑    tag_stock_codes 로 유니버스 종목이 붙는가
  ③ 선별        팩터의 references 에 들어갔는가
  ④ 팩터 요약    요약 문장에 내용이 반영됐는가
  ⑤ 최종 입력    background_information 의 텔레그램 블록에 남았는가
  ⑥ 출력        신호의 근거로 쓰였는가

중복·선행 여부도 함께 본다. 같은 내용이 Factiva·공시에도 있으면 텔레그램 고유
기여가 아니고, 텔레그램이 더 일찍 전달했다면 그것 자체가 기여다.

한계: 단계 ④~⑥의 판정은 문자열 근거다. LLM이 바꿔 쓴 표현은 놓친다.
따라서 "도달했다"는 강하게, "도달하지 않았다"는 약하게 읽어야 한다.

사용: python -m evaluation.trace_telegram 2026-07-01
출력: evaluation/out/telegram_trace_<날짜>.json
"""
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "evaluation" / "out"
REPORTS = ROOT / "agents_workspace" / "reports"
FACTORS = ROOT / "agents_workspace" / "factors"
DB = ROOT.parent / "data_collection" / "data" / "telegram" / "telegram_research.sqlite"
LOOKBACK_DAYS = 2

BLOCK_RE = re.compile(
    r"\s*<global_summary>\s*<source>\s*([^<\s]+)\s*</source>.*?</global_summary>", re.S)


def asof_window(date: str):
    """trigger 08:30 KST → date_utc < 전일 23:30Z, lookback 2일."""
    d = datetime.fromisoformat(date)
    end = (d - timedelta(days=1)).strftime("%Y-%m-%dT23:30")
    start = (d - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT00:00")
    return start, end


def load_raw(date: str) -> list:
    start, end = asof_window(date)
    con = sqlite3.connect(DB)
    rows = con.execute(
        "select channel, message_id, date_utc, text from messages "
        "where date_utc >= ? and date_utc < ? and text is not null and length(text) > 80 "
        "order by date_utc", (start, end)).fetchall()
    con.close()
    return [{"channel": c, "message_id": m, "date_utc": d, "text": t} for c, m, d, t in rows]


def universe():
    import csv
    p = ROOT.parent / "data_collection" / "universe" / "ktop30.csv"
    with p.open(encoding="utf-8-sig") as f:
        return {r["ticker"]: r["name_kr"] for r in csv.DictReader(f)}


def key_sentences(text: str, names: dict) -> list:
    """유니버스 종목이 언급된 문장만 고른다 — 추적 대상 후보."""
    out = []
    for line in re.split(r"[\n。]", text):
        line = line.strip()
        if len(line) < 25:
            continue
        hits = [c for c, n in names.items() if n and n in line]
        if hits:
            out.append((line, hits))
    return out


def main(date: str):
    names = universe()
    from data_source.kr_telegram_research import tag_stock_codes
    from utils.kr_universe import load_alias_table
    try:
        alias = load_alias_table()
    except Exception:
        alias = None

    raw = load_raw(date)
    fac = json.loads((FACTORS / "kr_telegram_research" / f"{date}_08-30-00.json").read_text())
    refs = fac.get("references", [])
    ref_text = "\n".join((r.get("title", "") + "\n" + r.get("content", "")) for r in refs)
    summary = fac.get("context_string", "") + "\n" + json.dumps(
        fac.get("batch_summaries", []), ensure_ascii=False)

    rep = json.loads((REPORTS / "agent_0" / f"{date}_08-30-00.json").read_text())
    blocks = {m.group(1): m.group(0) for m in BLOCK_RE.finditer(rep["background_information"])}
    tel_block = blocks.get("kr_telegram_research", "")
    other_block = (blocks.get("kr_dart_disclosure", "") +
                   blocks.get("kr_factiva_news", "") + rep.get("tool_call_context", ""))

    # 출력 근거 모으기 (텔레그램 포함 조건)
    ab = OUT / f"telegram_ablation_{date}.json"
    out_ev = ""
    if ab.exists():
        d = json.loads(ab.read_text())
        for agent, reps in d["raw"]["텔레그램_포함"].items():
            for r in reps:
                for s in r["signals"]:
                    out_ev += s["evidence"] + "\n"

    # references ↔ 원문 대조는 (채널, 발행시각)으로 한다.
    # 본문 앞부분 문자열 비교는 신뢰할 수 없었다 — 선별됐는데 False로 나온 건이 많았다.
    ref_key = {}
    for r in refs:
        ch = re.match(r"\[([^\]]+)\]", r.get("title", ""))
        if ch and r.get("pub_time"):
            ref_key[(ch.group(1), r["pub_time"][:19])] = r.get("id")

    def kst(date_utc: str) -> str:
        return (datetime.fromisoformat(date_utc.replace("Z", "")) +
                timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")

    funnel = Counter()
    cases = []
    for m in raw:
        funnel["① 원문(as-of 창, 80자 초과)"] += 1
        codes = tag_stock_codes(m["text"], names, alias)
        if not codes:
            continue
        funnel["② 유니버스 종목 매핑됨"] += 1
        doc_id = ref_key.get((m["channel"], kst(m["date_utc"])))
        in_ref = doc_id is not None
        if in_ref:
            funnel["③ 팩터 references에 선별됨"] += 1
        for line, hits in key_sentences(m["text"], names)[:2]:
            # 문장 단위로 ④⑤⑥ 추적 — 특징 수치·고유 표현으로 대조
            probe = re.findall(r"\d[\d,]*\.?\d*%?|[가-힣A-Za-z]{4,}", line)
            probe = [p for p in probe if len(p) >= 4][:6]
            if not probe:
                continue
            def hit(hay):
                return sum(1 for p in probe if p in hay)
            cases.append({
                "channel": m["channel"], "message_id": m["message_id"],
                "date_utc": m["date_utc"], "매핑종목": [f"{c}({names[c]})" for c in hits],
                "문장": line[:220],
                "대조키": probe,
                "③선별": in_ref,
                "문서번호": doc_id,
                "④팩터요약": f"{hit(summary)}/{len(probe)}",
                "⑤최종입력": f"{hit(tel_block)}/{len(probe)}",
                "⑥출력근거": f"{hit(out_ev)}/{len(probe)}",
                "다른소스중복": f"{hit(other_block)}/{len(probe)}",
            })
    res = {"date": date, "as_of": asof_window(date),
           "깔때기": dict(funnel),
           "references 총수": len(refs),
           "한계": "④~⑥ 판정은 문자열 대조다. LLM이 바꿔 쓴 표현은 놓친다. "
                   "'도달했다'는 강하게, '도달하지 않았다'는 약하게 읽을 것.",
           "사례": cases}
    (OUT / f"telegram_trace_{date}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(f"=== {date} (as-of {asof_window(date)[0]} ~ {asof_window(date)[1]}) ===")
    for k, v in funnel.items():
        print(f"  {k}: {v}")
    print(f"  references 총수: {len(refs)}")
    print(f"  추적 문장 후보: {len(cases)}건 → {OUT / f'telegram_trace_{date}.json'}")


if __name__ == "__main__":
    for d in (sys.argv[1:] or ["2026-05-11", "2026-06-12", "2026-07-01"]):
        main(d)
