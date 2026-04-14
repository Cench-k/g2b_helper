"""
나라장터 API 엔드포인트 테스트
각 API가 어떤 필드를 반환하는지 확인
"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
import requests
import json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import API_KEY

BASE_SCSBID = "https://apis.data.go.kr/1230000/as/ScsbidInfoService"
BASE_BID    = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"
BASE_BSS    = "https://apis.data.go.kr/1230000/ad/BssAmtOpengInfoService"

from datetime import datetime, timedelta
now = datetime.now()
DATE_END   = now.strftime("%Y%m%d") + "2359"
DATE_START = (now - timedelta(days=30)).strftime("%Y%m%d") + "0000"

BASE_CNTRCT  = "https://apis.data.go.kr/1230000/ao/CntrctInfoService"       # 계약정보
BASE_PRICE   = "https://apis.data.go.kr/1230000/ao/PriceInfoService"        # 가격정보현황
BASE_OPEN    = "https://apis.data.go.kr/1230000/ao/PubDataOpnStdService"    # 공공데이터개방표준

ENDPOINTS = [
    # ── 낙찰된 목록 현황 (수의계약) ─────────────────────────────────
    ("WIN_STTS 공사", BASE_SCSBID, "getScsbidListSttusCnstwkPPSSrch", {
        "inqryDiv": "1", "inqryBgnDt": DATE_START, "inqryEndDt": DATE_END,
        "numOfRows": 1, "pageNo": 1,
    }),
    # ── 개찰결과 (경쟁입찰) ──────────────────────────────────────────
    ("WIN_OPS 공사", BASE_SCSBID, "getOpengResultListInfoCnstwkPPSSrch", {
        "inqryDiv": "2", "inqryBgnDt": DATE_START, "inqryEndDt": DATE_END,
        "numOfRows": 1, "pageNo": 1,
    }),
    # ── 예비가격상세 ─────────────────────────────────────────────────
    ("PREPAR_PC 공사", BASE_SCSBID, "getOpengResultListInfoCnstwkPreparPcDetail", {
        "inqryDiv": "2", "inqryBgnDt": DATE_START, "inqryEndDt": DATE_END,
        "numOfRows": 3, "pageNo": 1,
    }),
    ("PREPAR_PC 용역", BASE_SCSBID, "getOpengResultListInfoServcPreparPcDetail", {
        "inqryDiv": "2", "inqryBgnDt": DATE_START, "inqryEndDt": DATE_END,
        "numOfRows": 3, "pageNo": 1,
    }),
    # ── 기초금액공개 ─────────────────────────────────────────────────
    ("BSS_AMT 공사", BASE_BSS, "getBssAmtOpengListInfoCnstwkPPSSrch", {
        "inqryDiv": "1", "inqryBgnDt": DATE_START, "inqryEndDt": DATE_END,
        "numOfRows": 1, "pageNo": 1,
    }),
    # ── 입찰공고 ─────────────────────────────────────────────────────
    ("BID_OPS 공사", BASE_BID, "getBidPblancListInfoCnstwkPPSSrch", {
        "inqryDiv": "1", "inqryBgnDt": DATE_START, "inqryEndDt": DATE_END,
        "numOfRows": 1, "pageNo": 1,
    }),
    # ── 면허제한 ─────────────────────────────────────────────────────
    ("LICENSE", BASE_BID, "getBidPblancListInfoLicenseLimit", {
        "inqryDiv": "1", "inqryBgnDt": DATE_START, "inqryEndDt": DATE_END,
        "numOfRows": 1, "pageNo": 1,
    }),
    # ── 계약정보서비스 ────────────────────────────────────────────────
    ("CNTRCT 공사", BASE_CNTRCT, "getCntrctInfoListCnstwk", {
        "inqryDiv": "1", "inqryBgnDt": DATE_START, "inqryEndDt": DATE_END,
        "numOfRows": 1, "pageNo": 1,
    }),
    ("CNTRCT 공사(PPSSrch)", BASE_CNTRCT, "getCntrctInfoListCnstwkPPSSrch", {
        "inqryDiv": "1", "inqryBgnDt": DATE_START, "inqryEndDt": DATE_END,
        "numOfRows": 1, "pageNo": 1,
    }),
    # ── 가격정보현황서비스 ────────────────────────────────────────────
    ("PRICE 건축자재", BASE_PRICE, "getPriceInfoListFcltyCmmnMtrilBildng", {
        "numOfRows": 1, "pageNo": 1,
    }),
    ("PRICE 표준시장단가", BASE_PRICE, "getStdMarkUprcinfoList", {
        "numOfRows": 1, "pageNo": 1,
    }),
    # ── 공공데이터개방표준서비스 ──────────────────────────────────────
    ("OPEN 입찰공고", BASE_OPEN, "getDataSetOpnStdBidPblancInfo", {
        "inqryBgnDt": DATE_START, "inqryEndDt": DATE_END,
        "numOfRows": 1, "pageNo": 1,
    }),
    ("OPEN 낙찰정보", BASE_OPEN, "getDataSetOpnStdScsbidInfo", {
        "inqryBgnDt": DATE_START, "inqryEndDt": DATE_END,
        "numOfRows": 1, "pageNo": 1,
    }),
    ("OPEN 계약정보", BASE_OPEN, "getDataSetOpnStdCntrctInfo", {
        "inqryBgnDt": DATE_START[:8], "inqryEndDt": DATE_END[:8],
        "numOfRows": 1, "pageNo": 1,
    }),
]

SEP = "=" * 70

def call(base, op, params):
    url = f"{base}/{op}"
    p = {**params, "serviceKey": API_KEY, "type": "json"}
    r = requests.get(url, params=p, timeout=15)
    return r.status_code, r.json()

def extract_items(data):
    try:
        body = data["response"]["body"]
        items = body.get("items") or []
        if isinstance(items, dict):
            items = items.get("item", [])
            if isinstance(items, dict):
                items = [items]
        return items, int(body.get("totalCount", 0))
    except Exception:
        return [], 0

def show_fields(item: dict, indent=2):
    pad = " " * indent
    for k, v in item.items():
        display = str(v)[:80] + ("…" if len(str(v)) > 80 else "")
        print(f"{pad}{k}: {display}")

print(SEP)
print(f"  나라장터 API 테스트  ({now.strftime('%Y-%m-%d %H:%M')})")
print(f"  API KEY: {API_KEY[:6]}...")
print(SEP)

results = {}
for label, base, op, params in ENDPOINTS:
    print(f"\n▶ {label}  [{op}]")
    try:
        status, data = call(base, op, params)
        items, total = extract_items(data)

        if status != 200:
            print(f"  ❌ HTTP {status}")
            results[label] = "HTTP_ERROR"
            continue

        # 에러 코드 확인
        try:
            rc = data["response"]["header"]["resultCode"]
            rm = data["response"]["header"]["resultMsg"]
            if rc != "00":
                print(f"  ❌ API 오류 {rc}: {rm}")
                results[label] = f"API_ERR_{rc}"
                continue
        except Exception:
            pass

        print(f"  ✅ 총 {total}건")
        if items:
            print(f"  📋 첫 번째 레코드 필드 ({len(items[0])}개):")
            show_fields(items[0])
            results[label] = list(items[0].keys())
        else:
            print("  ℹ️  결과 없음 (승인됐지만 조회 데이터 없음)")
            results[label] = "EMPTY"
    except Exception as e:
        print(f"  ❌ 예외: {e}")
        results[label] = str(e)

# 요약
print(f"\n{SEP}")
print("  요약")
print(SEP)
for label, val in results.items():
    if isinstance(val, list):
        print(f"  ✅ {label:25s}  {len(val)}개 필드")
    elif val == "EMPTY":
        print(f"  ⚠️  {label:25s}  결과 없음 (접근 가능)")
    else:
        print(f"  ❌ {label:25s}  {val}")
