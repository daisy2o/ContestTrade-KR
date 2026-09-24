"""
텔레그램 고유 근거를 **원래 메시지까지** 연결한다.

왜 필요한가:
- 앞선 집계의 "15건"은 **활용 출력 건수**이지 고유 정보 수가 아니다. 같은 사실이
  여러 실행·에이전트에서 반복 인용되면 15가 부풀려진다. 둘을 나눠 기록한다.
- 대조 대상이 **팩터 요약**이었다. 요약이 원문을 잘못 옮겼을 수 있으므로
  sqlite의 원래 메시지까지 내려가 확인해야 "원문으로 확인 가능"이라 말할 수 있다.

출력: evaluation/out/telegram_source_link.json
"""
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "evaluation" / "out"
FACTORS = ROOT / "agents_workspace" / "factors" / "kr_telegram_research"
DB = ROOT.parent / "data_collection" / "data" / "telegram" / "telegram_research.sqlite"


def raw_messages(date: str) -> list:
    d = datetime.fromisoformat(date)
    start = (d - timedelta(days=2)).strftime("%Y-%m-%dT00:00")
    end = (d - timedelta(days=1)).strftime("%Y-%m-%dT23:30")
    con = sqlite3.connect(DB)
    rows = con.execute(
        "select channel, message_id, date_utc, text from messages "
        "where date_utc >= ? and date_utc < ? and text is not null",
        (start, end)).fetchall()
    con.close()
    return [{"channel": c, "message_id": m, "date_utc": t, "text": x} for c, m, t, x in rows]


def main(date: str, facts: dict):
    """facts: {고유정보_이름: [원문에서 찾을 표기들]}"""
    msgs = raw_messages(date)
    fac = json.loads((FACTORS / f"{date}_08-30-00.json").read_text())
    refs = fac.get("references", [])

    out = {}
    for name, needles in facts.items():
        hit_msgs, hit_refs = [], []
        for m in msgs:
            if any(n in m["text"] for n in needles):
                hit_msgs.append({"channel": m["channel"], "message_id": m["message_id"],
                                 "date_utc": m["date_utc"],
                                 "발췌": next((m["text"][max(0, m["text"].find(n) - 60):
                                                m["text"].find(n) + 90]
                                              for n in needles if n in m["text"]), "")})
        for r in refs:
            body = (r.get("title", "") or "") + (r.get("content", "") or "")
            if any(n in body for n in needles):
                hit_refs.append(r.get("id"))
        out[name] = {"원문_메시지": hit_msgs, "팩터_문서번호": hit_refs,
                     "원문_확인": bool(hit_msgs)}
    return out


if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-05-07"
    # 05-07에서 모델이 쓴 텔레그램 고유 사실 — 표기 변형을 함께 준다
    FACTS = {
        "POSCO 1Q 영업이익 7,070억원": ["7,070", "7070억"],
        "POSCO 컨센서스 5,950억원": ["5,950", "5950억"],
        "POSCO 목표주가 64만원 상향": ["64만원", "640,000"],
        "POSCO 리튬 적자 820억원 축소": ["820억"],
        "POSCO ADR +3.9%": ["ADR"],
        "SK하이닉스 ADR 발행(7~8월)": ["ADR"],
        "DRAM 3배·NAND 2배 전망": ["DRAM", "NAND"],
    }
    res = main(date, FACTS)
    print(f"=== {date} 텔레그램 고유 사실 → 원문 메시지 연결")
    for k, v in res.items():
        n = len(v["원문_메시지"])
        print(f"  {'O' if v['원문_확인'] else 'X'} {k}: 원문 메시지 {n}건 / 팩터 문서 {v['팩터_문서번호']}")
        for m in v["원문_메시지"][:1]:
            print(f"      [{m['channel']} #{m['message_id']} {m['date_utc']}]")
            print(f"      …{re.sub(chr(10), ' ', m['발췌'])[:150]}…")
    (OUT / "telegram_source_link.json").write_text(
        json.dumps({"date": date, "결과": res}, ensure_ascii=False, indent=1))
    print(f"\n저장: {OUT / 'telegram_source_link.json'}")
