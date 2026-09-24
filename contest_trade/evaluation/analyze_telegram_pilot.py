"""
텔레그램 포함/제외 파일럿 분석 — 세 가지만 본다.

  ① 선택 빈도   특정 종목·방향이 조건별로 몇 회 나오는가
  ② 확인 가능한 추가 근거  텔레그램에서 온 정보가 **정확하게** 쓰였는가
  ③ 추가 오류·기권  텔레그램을 넣었을 때 오류·기권 양상이 어떻게 달라지는가

**제외 조건이 거짓 양성 기준선이다.** 그 조건의 입력에는 텔레그램이 없으므로
"텔레그램에만 있는 요소"가 잡히면 전부 탐지기 오류다. 포함 조건의 수치는
이 기준선과 함께 읽어야 한다.

②에서 **모델의 출처 라벨을 믿지 않는다.** 앞선 점검에서 `kr_telegram_research`로
라벨된 근거가 전부 가격 도구 출력이었다. 대신 근거 본문의 특징 요소(수치·고유
표현)가 **텔레그램 블록에만** 있는지 원문으로 대조한다.

한계: 문자열 대조는 LLM이 바꿔 쓴 표현을 놓친다. 그래서 이 도구는 **판정이 아니라
확인 대상 좁히기**다. 최종 판정은 사람이 읽어야 한다(Claude가 읽으면 AI 감사다).

감사 규율: 새로 등장하거나 방향이 달라진 신호 · 텔레그램 고유 정보 사용 후보를
먼저 보고, 공통 신호는 무작위 표본으로 본다. **선별 검토 결과를 전체 오류율처럼
보고하지 않는다.**

출력: evaluation/out/telegram_pilot_analysis.json
"""
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.judge_model_bench import (NUM, expand_units, norm,  # noqa: E402
                                          significant)
from evaluation.link_to_source_msg import raw_messages  # noqa: E402
from evaluation.telegram_ablation import BLOCK_RE  # noqa: E402

OUT = ROOT / "evaluation" / "out"
REPORTS = ROOT / "agents_workspace" / "reports"
DATES = ["2026-05-07", "2026-05-29", "2026-06-04"]
# 일반 낱말("시가총액", "상승세를")은 어느 블록에나 있어 귀속에 쓸 수 없다.
# 제외 조건(텔레그램이 입력에 없는 조건)에서 잡히면 전부 거짓 양성인데,
# 4자 이상 한글 낱말로는 실제로 그렇게 잡혔다. 고유 표기만 쓴다:
# 영문/숫자가 섞인 용어(iHBM, FC-BGA, P5, HBM4)만 낱말로 인정한다.
TERM = re.compile(r"\b(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d|[A-Z]{3,})[A-Za-z0-9-]{3,}\b")
SEED = 20260924


def blocks(date: str, agent: str):
    d = json.loads((REPORTS / agent / f"{date}_08-30-00.json").read_text())
    b = {m.group(1): m.group(0) for m in BLOCK_RE.finditer(d["background_information"])}
    tel = b.get("kr_telegram_research", "")
    other = (b.get("kr_dart_disclosure", "") + b.get("kr_factiva_news", "")
             + d.get("tool_call_context", ""))
    return tel, other


def _needle(elem: str) -> str:
    """정규화된 대조 요소를 원문 검색어로 되돌린다. 지수 표기는 원문에 없다."""
    try:
        f = float(elem)
        return f"{int(f):,}" if f == int(f) and abs(f) < 1e7 else elem
    except ValueError:
        return elem


def keys_of(text: str) -> set:
    """대조용 특징 요소: 3자리 이상 수치(단위 확장 포함) + 4자 이상 낱말."""
    return (norm(significant(text)) | expand_units(text)
            | {t for t in TERM.findall(text)})


def main():
    per_date, review = {}, []
    rng = random.Random(SEED)
    for date in DATES:
        p = OUT / f"telegram_ablation_{date}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        arms = d["raw"]

        # ① 선택 빈도
        freq = {}
        for name, arm in (("포함", arms["텔레그램_포함"]), ("제외", arms["텔레그램_제외"])):
            c = Counter()
            for agent, reps in arm.items():
                for r in reps:
                    for s in r["signals"]:
                        c[(s["symbol"], s["action"])] += 1
            freq[name] = c
        allk = sorted(set(freq["포함"]) | set(freq["제외"]))
        sel = [{"symbol": k[0], "action": k[1],
                "포함": freq["포함"][k], "제외": freq["제외"][k],
                "차이": freq["포함"][k] - freq["제외"][k]} for k in allk]

        # ②③ 근거별 귀속 + 기권
        msgs = raw_messages(date)
        stat = {}
        for name, arm in (("포함", arms["텔레그램_포함"]), ("제외", arms["텔레그램_제외"])):
            tel_only = shared = miss = nev = 0
            src_msgs = set()
            abst = sum(1 for reps in arm.values() for r in reps if not r["signals"])
            for agent, reps in arm.items():
                tel, other = blocks(date, agent)
                tk, ok = keys_of(tel), keys_of(other)
                for r in reps:
                    for s in r["signals"]:
                        for ev in re.findall(r"<evidence>(.*?)</evidence>", s["evidence"], re.S) \
                                or [s["evidence"]]:
                            k = keys_of(ev)
                            nev += 1
                            t_only = k & tk - ok
                            if t_only:
                                tel_only += 1
                                # 고유 정보 수 = 서로 다른 원문 메시지 수.
                                # 활용 출력 건수와 **섞어 보고하지 않는다**.
                                for mm in msgs:
                                    if any(_needle(e) in mm["text"] for e in t_only):
                                        src_msgs.add((mm["channel"], mm["message_id"]))
                                if name == "포함":
                                    review.append({
                                        "우선순위": "1_텔레그램고유_사용후보", "date": date,
                                        "agent": agent, "rep": r["rep"],
                                        "symbol": s["symbol"], "action": s["action"],
                                        "텔레그램에만_있는_요소": sorted(t_only)[:8],
                                        "근거": ev[:300],
                                        "확인할_것": "이 요소가 텔레그램 원문의 내용과 "
                                                    "일치하는가. 라벨이 아니라 원문으로 볼 것."})
                            elif k & ok:
                                shared += 1
                            else:
                                miss += 1
            stat[name] = {"근거수": nev,
                          "활용_출력_건수": tel_only,
                          "고유_정보_수_자동상한": len(src_msgs),
                          "원문_메시지_후보": sorted(f"{c}#{m}" for c, m in src_msgs),
                          "주의": "고유_정보_수_자동상한은 **상한**이다. 검색어가 느슨해 "
                                  "엉뚱한 메시지가 섞인다(실측: 05-07 자동 12 vs 수동 확인 4 — "
                                  "호텔 ADR·프로브카드 DRAM 등이 섞였다). 보고할 숫자는 "
                                  "원문을 읽어 확인한 수다.",
                          "텔레그램에만_있는_요소_사용": tel_only,
                          "다른소스에도_있음": shared, "어느쪽에도_없음": miss,
                          "기권_실행수": abst}

        # 차이 난 신호 우선 검토
        diff = {x["symbol"] for x in sel if x["차이"] != 0}
        for name, arm in (("포함", arms["텔레그램_포함"]), ("제외", arms["텔레그램_제외"])):
            for agent, reps in arm.items():
                for r in reps:
                    for s in r["signals"]:
                        if s["symbol"] in diff:
                            review.append({"우선순위": "2_차이난_신호", "date": date,
                                           "조건": name, "agent": agent, "rep": r["rep"],
                                           "symbol": s["symbol"], "action": s["action"],
                                           "근거": s["evidence"][:300]})
        # 공통 신호 무작위 표본
        common = [{"우선순위": "3_공통_무작위표본", "date": date, "조건": name,
                   "agent": agent, "rep": r["rep"], "symbol": s["symbol"],
                   "action": s["action"], "근거": s["evidence"][:300]}
                  for name, arm in (("포함", arms["텔레그램_포함"]),
                                    ("제외", arms["텔레그램_제외"]))
                  for agent, reps in arm.items() for r in reps for s in r["signals"]
                  if s["symbol"] not in diff]
        rng.shuffle(common)
        review += common[:6]

        per_date[date] = {"선택빈도": sel, "근거귀속·기권": stat}

    res = {"설계": {
        "반복": "조건별 5회 — 통계적 충분성이 아니라 **탐색 파일럿의 상한**으로 정한 값",
        "출처라벨": "믿지 않는다. 앞선 점검에서 텔레그램 라벨 근거가 전부 가격 도구 "
                    "출력이었다. 근거 본문을 원문 블록과 대조한다.",
        "한계": "같은 입력의 5회 실행은 서로 다른 거래일 5개가 아니다. 이 결과로 기간 "
                "전체의 효과를 주장하지 않는다.",
        "감사규율": "선별 검토 결과를 전체 오류율처럼 보고하지 않는다.",
        "판정자": "Claude — 사람 감사가 아니라 AI 감사다.",
    }, "날짜별": per_date, "검토대기열": review}
    (OUT / "telegram_pilot_analysis.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))

    for date, v in per_date.items():
        print(f"\n=== {date}")
        print("  ① 선택 빈도 (포함/제외):")
        for x in v["선택빈도"]:
            mark = "  ←차이" if x["차이"] else ""
            print(f"       {x['symbol']} {x['action']:<5} {x['포함']:>2} / {x['제외']:>2}{mark}")
        print("  ②③ 근거 귀속·기권:")
        for name, s in v["근거귀속·기권"].items():
            print(f"       {name}: 근거 {s['근거수']:>3} | 활용건수 {s['활용_출력_건수']:>2}"
                  f" | 고유정보(자동상한) {s['고유_정보_수_자동상한']:>2}"
                  f" | 공유 {s['다른소스에도_있음']:>3} | 미발견 {s['어느쪽에도_없음']:>2}"
                  f" | 기권실행 {s['기권_실행수']}")
    print(f"\n검토 대기열 {len(review)}건 → {OUT / 'telegram_pilot_analysis.json'}")
    print("  " + json.dumps(dict(Counter(x["우선순위"] for x in review)), ensure_ascii=False))


if __name__ == "__main__":
    main()
