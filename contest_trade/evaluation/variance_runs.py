"""분산 분해용 반복 실행기 — 같은 입력, 리서치 단계만 다시 샘플링.

질문: belief 가 만드는 에이전트 간 차이가 실재하는가, 아니면 샘플링 잡음인가.

설계
- 팩터(데이터 에이전트 출력)는 날짜별 캐시를 그대로 쓴다 → **모든 반복이 같은 입력**
- 보고서(리서치 에이전트 출력)만 지우고 다시 돌린다 → belief 가 작동하는 단계만 흔든다
- temperature 는 config_kr.yaml 의 값을 쓰며, 실행 전에 러너 출력으로 확인한다
- 각 반복의 보고서는 evaluation/out/variance/t{온도}/{날짜}/rep{N}/ 에 보존한다
  (보고서 파일명이 {날짜}_{시각}.json 이라 그대로 두면 다음 반복이 덮어쓴다)
- 끝나면 해당 날짜의 원래 보고서를 복원한다

사용:
    CONTEST_TRADE_MARKET=KR-Stock python -m evaluation.variance_runs \\
        --temp 0.7 --reps 5 --dates 2026-05-15 2026-05-21 2026-06-10
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # contest_trade/
CFG = ROOT.parent / "config_kr.yaml"
REPORTS = ROOT / "agents_workspace" / "reports"
OUT = ROOT / "evaluation" / "out" / "variance"
AGENTS = ("agent_0", "agent_1", "agent_2")
TRIGGER = "08-30-00"


def set_temperature(t: float) -> None:
    """config_kr.yaml 의 temperature 두 곳(상위·판단)을 t 로 맞춘다. 키 줄은 건드리지 않는다."""
    s = CFG.read_text()
    s2, n = re.subn(r"(^\s*temperature:\s*)[0-9.]+", rf"\g<1>{t}", s, flags=re.M)
    if n != 2:
        raise SystemExit(f"config_kr.yaml 의 temperature 줄이 2개가 아니다({n}개) — 수동 확인 필요")
    CFG.write_text(s2)


def report_path(agent: str, date: str) -> Path:
    return REPORTS / agent / f"{date}_{TRIGGER}.json"


def run_once(date: str, temp: float) -> dict:
    for a in AGENTS:
        report_path(a, date).unlink(missing_ok=True)
    p = subprocess.run(
        [sys.executable, "-m", "backtest_runner", date, date],
        cwd=ROOT, capture_output=True, text=True,
        env={**__import__("os").environ, "CONTEST_TRADE_MARKET": "KR-Stock"},
    )
    log = p.stdout + p.stderr
    # 조건 확인: 러너가 실제로 이 온도로 떴는가
    m = re.search(r"temperature 판단=([0-9.]+) 상위=([0-9.]+)", log)
    if not m or float(m.group(1)) != temp or float(m.group(2)) != temp:
        raise SystemExit(f"[중단] 온도 불일치 — 기대 {temp}, 러너 출력 {m.groups() if m else '없음'}")
    done = re.search(r"완료 \(([0-9]+)s, LLM ([0-9]+)회, ~\$([0-9.]+)\)", log)
    rid = re.search(r"run_id=(\S+)", log)
    missing = [a for a in AGENTS if not report_path(a, date).exists()]
    # 온도 배너는 import 시점에 찍히므로 그것만으로는 '실행됐다'는 증거가 아니다.
    # 완료 줄이 없거나, LLM 을 한 번도 안 불렀거나, 보고서가 하나도 없으면 중단한다.
    if not done or int(done.group(2)) == 0 or len(missing) == len(AGENTS):
        raise SystemExit(f"[중단] {date} 실행이 실제로 이뤄지지 않았다 "
                         f"(완료줄={'있음' if done else '없음'}, 누락={missing})\n{log[-1500:]}")
    return {
        "run_id": rid.group(1) if rid else None,
        "초": int(done.group(1)) if done else None,
        "LLM호출": int(done.group(2)) if done else None,
        "표시비용_상위단계만": float(done.group(3)) if done else None,
        "보고서_누락": missing,
        "returncode": p.returncode,
        "log_tail": log[-1500:] if (missing or p.returncode) else "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--temp", type=float, required=True)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--dates", nargs="+", required=True)
    args = ap.parse_args()
    bad = [d for d in args.dates if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)]
    if bad:
        raise SystemExit(f"[중단] 날짜 형식이 아니다: {bad} — 셸에서 날짜를 한 덩어리로 넘기지 않았는지 확인")

    # 원래 보고서 보관 → 끝나면 복원
    keep = OUT / f"_orig_{datetime.now():%Y%m%d-%H%M%S}"
    for d in args.dates:
        for a in AGENTS:
            if report_path(a, d).exists():
                (keep / a).mkdir(parents=True, exist_ok=True)
                shutil.copy2(report_path(a, d), keep / a / report_path(a, d).name)

    set_temperature(args.temp)
    meta = {"temperature": args.temp, "reps": args.reps, "dates": args.dates,
            "설계": "팩터 캐시 고정, 리서치 단계만 재샘플링", "runs": []}
    tag = f"t{args.temp}"
    try:
        for d in args.dates:
            for r in range(1, args.reps + 1):
                info = run_once(d, args.temp)
                dst = OUT / tag / d / f"rep{r}"
                dst.mkdir(parents=True, exist_ok=True)
                for a in AGENTS:
                    if report_path(a, d).exists():
                        shutil.copy2(report_path(a, d), dst / f"{a}.json")
                meta["runs"].append({"date": d, "rep": r, **info})
                print(f"{tag} {d} rep{r}: LLM {info['LLM호출']}회 · {info['초']}s"
                      f"{' · 누락 ' + str(info['보고서_누락']) if info['보고서_누락'] else ''}", flush=True)
                (OUT / tag / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    finally:
        for a in AGENTS:
            for f in (keep / a).glob("*.json") if (keep / a).exists() else []:
                shutil.copy2(f, REPORTS / a / f.name)
        print(f"원래 보고서 복원 완료 ({keep})")


if __name__ == "__main__":
    main()
