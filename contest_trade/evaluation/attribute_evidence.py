"""
근거에 쓰인 수치가 **어느 정보원에서 왔는지** 귀속한다.

텔레그램 비교 파일럿의 두 번째 질문("다른 소스의 반복인가, 추가 정보인가")에 답한다.

수치 비교는 반드시 정규화한다. 모델은 `268,500`으로 쓰고 도구 출력은 `268500.0`으로
담고 있어, 문자열을 그대로 비교하면 **실재하는 수치를 '어느 소스에도 없음'으로
잘못 집계**한다(실측: 05-11에서 7건). 같은 이유로 텔레그램 전용 수치도 놓친다.

한계: 수치가 여러 소스에 동시에 등장하면 어느 쪽을 읽었는지 이 방법으로는 못 가른다.
'텔레그램에만 있는 수치'만 텔레그램 고유 기여로 센다(보수적).

사용: python -m evaluation.attribute_evidence 2026-05-11 2026-06-12 2026-07-01
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.telegram_ablation import BLOCK_RE  # noqa: E402

OUT = ROOT / "evaluation" / "out"
REPORTS = ROOT / "agents_workspace" / "reports"
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def norm(tokens) -> set:
    """쉼표·후행 0 제거. 1,686,000 과 1686000.0 을 같은 값으로 본다."""
    out = set()
    for t in tokens:
        s = t.replace(",", "")
        try:
            f = float(s)
        except ValueError:
            continue
        out.add(f"{f:.6g}")
    return out


def norm_text(text: str) -> set:
    return norm(NUM.findall(text or ""))


def significant(tokens) -> set:
    """3자리 미만·연도는 우연 일치가 많아 제외한다."""
    keep = set()
    for t in tokens:
        s = t.replace(",", "")
        d = s.replace(".", "")
        if len(d) < 3:
            continue
        if re.fullmatch(r"20\d\d", s):
            continue
        keep.add(t)
    return keep


def main(dates):
    print(f"{'날짜':<12}{'신호':>5}{'텔레그램전용':>12}{'다른소스에도':>12}{'미발견':>8}")
    grand = {"tel": 0, "shared": 0, "miss": 0, "sig": 0}
    detail, off_rate = [], {}
    for date in dates:
        p = OUT / f"telegram_ablation_{date}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        n_sig = tel_only = shared = miss = 0
        # 제외 조건의 미발견 수치도 함께 센다. 양쪽에서 비슷하게 나오면
        # 그 오류는 텔레그램 때문에 새로 들어온 것이 아니다.
        off_sig = off_miss = 0
        for agent, reps in d["raw"]["텔레그램_제외"].items():
            r0 = json.loads((REPORTS / agent / f"{date}_08-30-00.json").read_text())
            hay = norm_text(r0["background_information"]) | norm_text(r0.get("tool_call_context", ""))
            for r in reps:
                for s in r["signals"]:
                    off_sig += 1
                    if norm(significant(NUM.findall(s["evidence"]))) - hay:
                        off_miss += 1
                        detail.append({"date": date, "agent": agent, "symbol": s["symbol"],
                                       "arm": "텔레그램_제외",
                                       "미발견_수치": sorted(norm(significant(
                                           NUM.findall(s["evidence"]))) - hay),
                                       "근거": s["evidence"][:200]})
        off_rate[date] = (off_miss, off_sig)
        for agent, reps in d["raw"]["텔레그램_포함"].items():
            r0 = json.loads((REPORTS / agent / f"{date}_08-30-00.json").read_text())
            bg, tc = r0["background_information"], r0.get("tool_call_context", "")
            blocks = {m.group(1): m.group(0) for m in BLOCK_RE.finditer(bg)}
            tel = norm_text(blocks.get("kr_telegram_research", ""))
            other = norm_text(blocks.get("kr_dart_disclosure", "")) | \
                norm_text(blocks.get("kr_factiva_news", "")) | norm_text(tc)
            for r in reps:
                for s in r["signals"]:
                    n_sig += 1
                    vals = norm(significant(NUM.findall(s["evidence"])))
                    t = vals & tel - other
                    o = vals & other
                    m = vals - tel - other
                    tel_only += bool(t); shared += bool(o); miss += bool(m)
                    if t:
                        detail.append({"date": date, "agent": agent, "symbol": s["symbol"],
                                       "텔레그램_전용_수치": sorted(t),
                                       "근거": s["evidence"][:200]})
                    if m:
                        detail.append({"date": date, "agent": agent, "symbol": s["symbol"],
                                       "미발견_수치": sorted(m),
                                       "근거": s["evidence"][:200]})
        om, os_ = off_rate[date]
        print(f"{date:<12}{n_sig:>5}{tel_only:>12}{shared:>12}{miss:>8}"
              f"   (제외 조건 미발견 {om}/{os_})")
        grand["tel"] += tel_only; grand["shared"] += shared
        grand["miss"] += miss; grand["sig"] += n_sig
    print(f"{'합계':<12}{grand['sig']:>5}{grand['tel']:>12}{grand['shared']:>12}{grand['miss']:>8}")
    (OUT / "evidence_attribution.json").write_text(json.dumps(
        {"요약": grand, "제외조건_미발견": off_rate,
         "판독": "'텔레그램전용'은 텔레그램 요약에만 있고 공시·Factiva·도구 출력에는 없는 "
                 "수치를 쓴 신호 수다. 0이면 이 날짜들에서 텔레그램이 추천의 수치 근거를 "
                 "따로 공급하지 않았다는 뜻이다(추천 자체가 달라지는 것과는 별개).",
         "새오류_판독": "'미발견'이 포함·제외 양쪽에서 비슷하게 나오면 그 오류는 "
                         "텔레그램이 새로 들여온 것이 아니라 두 조건에 공통된 결함이다.",
         "한계": "수치가 여러 소스에 함께 있으면 어느 쪽을 읽었는지 가르지 못한다. "
                 "또 수치 없는 서술형 근거(공시 제목 등)는 이 방법으로 귀속되지 않는다.",
         "상세": detail}, ensure_ascii=False, indent=1))
    print(f"\n상세 저장: {OUT / 'evidence_attribution.json'} ({len(detail)}건)")
    for x in detail[:6]:
        k = "텔레그램_전용_수치" if "텔레그램_전용_수치" in x else "미발견_수치"
        print(f"  [{k}] {x['date']} {x['symbol']} {x[k]}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["2026-05-11", "2026-06-12", "2026-07-01"])
