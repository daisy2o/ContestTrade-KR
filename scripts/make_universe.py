"""
KTOP30 파일럿 유니버스 생성 → universe.csv

    pip install pykrx requests
    set DART_API_KEY=...        (OpenDART 인증키, https://opendart.fss.or.kr)
    python scripts/make_universe.py --date 20260430 --out data/universe.csv

출력: data/universe.csv (gitignore 대상). 30행 마스터 사본은 config/universe_ktop30_20260430.csv 로 커밋.
주의: 엑셀로 열어 저장하면 ticker/dart_corp_code 앞자리 0이 사라진다. 텍스트 편집기로만 수정할 것.

컬럼:
    ticker            6자리 종목코드 (pykrx, 인덱스 조인 키)
    name_kr           종목명
    market            KOSPI/KOSDAQ
    dart_corp_code    OpenDART 8자리 고유번호 (공시목록 API용)
    name_en           Factiva Company 검색용 영문명 (사전에 있는 것만 채움, 나머지 수기)
    factiva_co_code   Factiva 기업코드 (수기 입력: factiva_company 테이블 또는 Company 검색 결과)
    asof              구성종목 기준일

point-in-time: pykrx get_index_portfolio_deposit_file(code, date)는 해당 일자의 구성종목을 돌려준다.
"""
import argparse
import csv
import io
import os
import sys
import zipfile
import xml.etree.ElementTree as ET

import requests
from pykrx import stock

# Factiva Company 검색에 그대로 칠 수 있는 영문명 (KTOP30 후보군, 없으면 빈칸으로 두고 수기 보완)
NAME_EN = {
    "005930": "Samsung Electronics", "000660": "SK Hynix", "005380": "Hyundai Motor",
    "000270": "Kia", "068270": "Celltrion", "105560": "KB Financial Group",
    "055550": "Shinhan Financial Group", "035420": "Naver", "207940": "Samsung Biologics",
    "373220": "LG Energy Solution", "005490": "POSCO Holdings", "012330": "Hyundai Mobis",
    "051910": "LG Chem", "028260": "Samsung C&T", "086790": "Hana Financial Group",
    "006400": "Samsung SDI", "032830": "Samsung Life Insurance", "138040": "Meritz Financial Group",
    "329180": "HD Hyundai Heavy Industries", "033780": "KT&G", "096770": "SK Innovation",
    "066570": "LG Electronics", "035720": "Kakao", "011200": "HMM", "012450": "Hanwha Aerospace",
    "034020": "Doosan Enerbility", "000810": "Samsung Fire & Marine Insurance",
    "402340": "SK Square", "010130": "Korea Zinc", "030200": "KT", "017670": "SK Telecom",
    "259960": "Krafton", "316140": "Woori Financial Group", "003550": "LG Corp",
    "034730": "SK Inc", "267260": "HD Hyundai Electric", "009540": "HD Korea Shipbuilding & Offshore Engineering",
    "042660": "Hanwha Ocean", "003670": "POSCO Future M", "015760": "Korea Electric Power",
    "018260": "Samsung SDS", "010950": "S-Oil", "011070": "LG Innotek", "009150": "Samsung Electro-Mechanics",
    "047050": "POSCO International", "090430": "Amorepacific", "051900": "LG H&H",
    "024110": "Industrial Bank of Korea", "000100": "Yuhan", "302440": "SK Bioscience",
    "352820": "HYBE", "377300": "Kakao Pay", "323410": "KakaoBank",
}


def find_ktop30_code():
    """pykrx 인덱스 목록에서 KTOP 30 코드를 이름으로 찾는다."""
    for market in ("KOSPI", "KRX", "테마"):
        try:
            for code in stock.get_index_ticker_list(market=market):
                name = stock.get_index_ticker_name(code)
                if "KTOP" in name.upper().replace(" ", ""):
                    return code, name
        except Exception:
            continue
    raise SystemExit("KTOP 30 인덱스 코드를 찾지 못했습니다. stock.get_index_ticker_list()로 직접 확인하세요.")


def load_dart_corp_map(api_key: str) -> dict:
    """OpenDART corpCode.xml → {종목코드: corp_code}"""
    r = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": api_key}, timeout=60)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        xml_bytes = z.read(z.namelist()[0])
    root = ET.fromstring(xml_bytes)
    out = {}
    for el in root.iter("list"):
        sc = (el.findtext("stock_code") or "").strip()
        if sc:
            out[sc] = el.findtext("corp_code").strip()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20260501", help="구성종목 기준일 YYYYMMDD")
    ap.add_argument("--out", default="universe.csv")
    ap.add_argument("--index-code", default=None, help="KTOP30 코드를 알면 직접 지정")
    args = ap.parse_args()

    api_key = os.environ.get("DART_API_KEY")
    if not api_key:
        sys.exit("환경변수 DART_API_KEY 가 필요합니다 (OpenDART 인증키).")

    if args.index_code:
        code, idx_name = args.index_code, stock.get_index_ticker_name(args.index_code)
    else:
        code, idx_name = find_ktop30_code()
    print(f"index: {code} {idx_name}, as of {args.date}")

    from datetime import datetime, timedelta
    asof = datetime.strptime(args.date, "%Y%m%d")
    tickers = []
    for _ in range(7):                      # 휴장일이면 직전 영업일로 최대 7일 후퇴
        tickers = stock.get_index_portfolio_deposit_file(code, asof.strftime("%Y%m%d"))
        if tickers:
            break
        asof -= timedelta(days=1)
    args.date = asof.strftime("%Y%m%d")
    if not tickers:
        sys.exit("구성종목을 받지 못했습니다. 인덱스 코드/기준일을 확인하세요.")
    if len(tickers) != 30:
        print(f"[주의] 구성종목 수 {len(tickers)} (30 아님)")
    print(f"구성종목 기준일(영업일): {args.date}, {len(tickers)}종목")

    dart = load_dart_corp_map(api_key)
    kospi = set(stock.get_market_ticker_list(args.date, market="KOSPI"))

    rows = []
    for t in tickers:
        rows.append(dict(
            ticker=t,
            name_kr=stock.get_market_ticker_name(t),
            market="KOSPI" if t in kospi else "KOSDAQ",
            dart_corp_code=dart.get(t, ""),
            name_en=NAME_EN.get(t, ""),
            factiva_co_code="",
            asof=args.date,
        ))

    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    missing_dart = [r["name_kr"] for r in rows if not r["dart_corp_code"]]
    missing_en = [r["name_kr"] for r in rows if not r["name_en"]]
    print(f"saved {args.out}: {len(rows)} rows")
    if missing_dart:
        print("DART corp_code 미매칭:", missing_dart)
    if missing_en:
        print("영문명 수기 필요:", missing_en)
    print("factiva_co_code 열은 수기 입력 (factiva_company 테이블의 co_code 또는 Factiva Company 검색)")


if __name__ == "__main__":
    main()
