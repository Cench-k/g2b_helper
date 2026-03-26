import requests
import time
import pandas as pd
from datetime import datetime, timedelta
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import API_KEY

# ── 확인된 정확한 엔드포인트 ──────────────────────────────────────────────
BID_BASE   = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"   # 입찰공고
SCSBID_BASE = "https://apis.data.go.kr/1230000/as/ScsbidInfoService"     # 낙찰결과

BID_OPS = {
    "용역": "getBidPblancListInfoServcPPSSrch",
    "물품": "getBidPblancListInfoThngPPSSrch",
    "공사": "getBidPblancListInfoCnstwkPPSSrch",
}
WIN_OPS = {
    "용역": "getOpengResultListInfoServcPPSSrch",
    "물품": "getOpengResultListInfoThngPPSSrch",
    "공사": "getOpengResultListInfoCnstwkPPSSrch",
}


def _fmt_bid(dt: datetime) -> str:
    """입찰공고 날짜 형식: YYYYMMDD"""
    return dt.strftime("%Y%m%d")

def _fmt_win(dt: datetime) -> str:
    """낙찰결과 날짜 형식: YYYYMMDDHHMM"""
    return dt.strftime("%Y%m%d%H%M")


class G2BAPI:
    def __init__(self):
        self.key = API_KEY

    def _get(self, url: str, params: dict) -> dict:
        p = {**params, "serviceKey": self.key}
        p.setdefault("type", "json")
        for attempt in range(3):
            try:
                r = requests.get(url, params=p, timeout=15)
                if r.status_code == 429:
                    if attempt < 2:
                        time.sleep(2 ** attempt + 1)
                        continue
                    raise ConnectionError("API 요청 한도 초과 (분당 요청 수 제한). 잠시 후 다시 시도하세요.")
                r.raise_for_status()
                data = r.json()
                if "nkoneps.com.response.ResponseError" in data:
                    err = data["nkoneps.com.response.ResponseError"]["header"]
                    raise ConnectionError(f"API 오류 {err['resultCode']}: {err['resultMsg']}")
                return data
            except requests.exceptions.Timeout:
                if attempt < 2:
                    time.sleep(1)
                    continue
                raise ConnectionError("API 응답 시간 초과")
            except requests.exceptions.HTTPError as e:
                raise ConnectionError(f"HTTP 오류: {e}")
            except ConnectionError:
                raise
            except Exception as e:
                raise ConnectionError(f"API 호출 실패: {e}")
        raise ConnectionError("API 재시도 실패")

    def _items(self, data: dict) -> list:
        items = data.get("response", {}).get("body", {}).get("items", [])
        if not items:
            return []
        return items if isinstance(items, list) else [items]

    # ── 입찰공고 조회 ──────────────────────────────────────────────────────
    def get_bid_list(
        self,
        bid_type: str = "용역",
        keyword: str = "",
        start_date: str = "",
        end_date: str = "",
        page: int = 1,
        rows: int = 20,
    ) -> pd.DataFrame:
        now = datetime.now()
        # 최대 조회 가능 기간: 14일
        start = start_date or _fmt_bid(now - timedelta(days=14))
        end   = end_date   or _fmt_bid(now)

        params = {
            "numOfRows": rows, "pageNo": page,
            "inqryDiv": "1",
            "inqryBgnDt": start[:8],
            "inqryEndDt": end[:8],
        }
        if keyword:
            params["bidNtceNm"] = keyword

        url = f"{BID_BASE}/{BID_OPS.get(bid_type, BID_OPS['용역'])}"
        data = self._get(url, params)
        return self._parse_bid(self._items(data))

    def _parse_bid(self, items: list) -> pd.DataFrame:
        if not items:
            return pd.DataFrame()
        df = pd.DataFrame(items)
        rename = {
            "bidNtceNo":          "공고번호",
            "bidNtceNm":          "공고명",
            "ntceInsttNm":        "공고기관",
            "dminsttNm":          "수요기관",
            "asignBdgtAmt":       "배정예산액",
            "presmptPrce":        "추정금액",
            "bidNtceDt":          "공고일자",
            "bidBeginDt":         "입찰시작일",
            "bidClseDt":          "입찰마감일",
            "opengDt":            "개찰일시",
            "sucsfbidMthdNm":     "낙찰방법",
            "cntrctCnclsMthdNm":  "계약방식",
            "sucsfbidLwltRate":   "낙찰하한율",
            "prearngPrceDcsnMthdNm": "예가방식",
            "srvceDivNm":         "용역구분",
        }
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
        for col in ["배정예산액", "추정금액"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "낙찰하한율" in df.columns:
            df["낙찰하한율"] = pd.to_numeric(df["낙찰하한율"], errors="coerce")
        return df

    # ── 공고번호로 상세 조회 ───────────────────────────────────────────────
    def get_bid_by_no(self, bid_no: str) -> dict | None:
        """
        공고번호로 조회
        공고번호 앞 8자리(YYYYMMDD)로 날짜를 추정해 해당 월부터 검색 → 빠른 탐색
        """
        now = datetime.now()
        bid_no_clean = bid_no.split("-")[0].strip()

        # 공고번호에서 날짜 추정 (앞 8자리 = YYYYMMDD)
        search_start_days = 0
        try:
            prefix = bid_no_clean[:8]
            bid_dt = datetime.strptime(prefix, "%Y%m%d")
            search_start_days = (now - bid_dt).days
        except Exception:
            try:
                prefix = bid_no_clean[:6]
                bid_dt = datetime.strptime(prefix + "01", "%Y%m%d")
                search_start_days = (now - bid_dt).days
            except Exception:
                search_start_days = 0

        # 추정 날짜 기준 ±7일 먼저 탐색, 그 후 전체 90일 순회
        priority_days = list(range(
            max(0, search_start_days - 3),
            min(90, search_start_days + 8)
        ))
        remaining_days = [d for d in range(0, 90) if d not in priority_days]
        search_order = priority_days + remaining_days

        for days_ago in search_order:
            day_dt   = now - timedelta(days=days_ago)
            day_str  = _fmt_bid(day_dt)
            next_str = _fmt_bid(day_dt + timedelta(days=1))

            for bid_type, op in BID_OPS.items():
                url = f"{BID_BASE}/{op}"
                try:
                    # 페이지 1 조회 + totalCount 파악
                    first = self._get(url, {
                        "numOfRows": 100, "pageNo": 1,
                        "inqryDiv": "1",
                        "inqryBgnDt": day_str,
                        "inqryEndDt": next_str,
                    })
                    total = int(
                        first.get("response", {}).get("body", {}).get("totalCount", 0)
                    )
                    if total == 0:
                        continue

                    total_pages = (total + 99) // 100  # 올림 나눗셈

                    # 페이지 1 결과 확인
                    for item in self._items(first):
                        no = item.get("bidNtceNo", "")
                        if no == bid_no_clean or no == bid_no:
                            return self._parse_bid_detail(item, bid_type)

                    # 나머지 페이지 순회 (최대 30페이지 = 3,000건/일)
                    for page in range(2, min(total_pages + 1, 31)):
                        data = self._get(url, {
                            "numOfRows": 100, "pageNo": page,
                            "inqryDiv": "1",
                            "inqryBgnDt": day_str,
                            "inqryEndDt": next_str,
                        })
                        for item in self._items(data):
                            no = item.get("bidNtceNo", "")
                            if no == bid_no_clean or no == bid_no:
                                return self._parse_bid_detail(item, bid_type)
                except Exception:
                    continue
        return None

    def _parse_bid_detail(self, item: dict, bid_type: str) -> dict:
        def num(v):
            try: return float(v) if v else None
            except: return None

        # 기초금액 = 추정가격(VAT 제외) + 부가세 = 나라장터 공종별 "금액(추정가격+부가세)"
        presmpt = num(item.get("presmptPrce"))
        vat     = num(item.get("VAT")) or num(item.get("indutyVAT"))
        if presmpt and vat:
            base = presmpt + vat
        elif presmpt:
            base = round(presmpt * 1.1)  # VAT 10% 추정
        else:
            base = (num(item.get("bssAmt"))
                    or num(item.get("asignBdgtAmt"))
                    or num(item.get("bdgtAmt")))
        # 복수예가 후보 수 / 추첨 수 (공고마다 다를 수 있음, 기본 15개/2개)
        total_prd = int(item.get("totPrdprcNum") or 15)
        draw_prd  = int(item.get("drwtPrdprcNum") or 2)
        return {
            "공고번호":   item.get("bidNtceNo", ""),
            "공고명":    item.get("bidNtceNm", ""),
            "공고기관":  item.get("ntceInsttNm", ""),
            "수요기관":  item.get("dminsttNm", ""),
            "기초금액":  base,
            "추정금액":  num(item.get("presmptPrce")),
            "낙찰하한율": num(item.get("sucsfbidLwltRate")),
            "낙찰방법":  item.get("sucsfbidMthdNm", ""),
            "계약방식":  item.get("cntrctCnclsMthdNm", ""),
            "예가방식":  item.get("prearngPrceDcsnMthdNm", ""),
            "입찰마감일": item.get("bidClseDt", ""),
            "개찰일시":  item.get("opengDt", ""),
            "공사종류":  bid_type,
            "후보수":    total_prd,
            "추첨수":    draw_prd,
        }

    # ── 낙찰 결과 조회 ─────────────────────────────────────────────────────
    def get_winner_list(
        self,
        bid_type: str = "용역",
        keyword: str = "",
        start_date: str = "",
        end_date: str = "",
        page: int = 1,
        rows: int = 200,
    ) -> pd.DataFrame:
        now = datetime.now()
        # 최대 조회 가능 기간: 14일
        start = start_date or _fmt_win(now - timedelta(days=14))
        end   = end_date   or _fmt_win(now)

        # 낙찰결과는 YYYYMMDDHHMM (12자리)
        if len(start) == 8: start += "0000"
        if len(end)   == 8: end   += "2359"

        params = {
            "numOfRows": rows, "pageNo": page,
            "inqryDiv": "2",       # 개찰일자 기준
            "inqryBgnDt": start[:12],
            "inqryEndDt": end[:12],
        }
        if keyword:
            params["bidNtceNm"] = keyword

        url = f"{SCSBID_BASE}/{WIN_OPS.get(bid_type, WIN_OPS['용역'])}"
        data = self._get(url, params)
        return self._parse_winner(self._items(data))

    def _parse_winner(self, items: list) -> pd.DataFrame:
        if not items:
            return pd.DataFrame()
        rows = []
        for item in items:
            corp_info = item.get("opengCorpInfo", "")
            award_amt = None
            corp_name = ""
            if corp_info:
                parts = corp_info.split("^")
                if len(parts) >= 4:
                    corp_name = parts[0]
                    try: award_amt = float(parts[3])
                    except: pass

            try: presmpt = float(item.get("presmptPrce") or 0) or None
            except: presmpt = None

            rows.append({
                "공고번호":   item.get("bidNtceNo", ""),
                "공고명":    item.get("bidNtceNm", ""),
                "공고기관":  item.get("ntceInsttNm", ""),
                "수요기관":  item.get("dminsttNm", ""),
                "개찰일시":  item.get("opengDt", ""),
                "참가업체수": int(item.get("prtcptCnum") or 0),
                "낙찰업체명": corp_name,
                "낙찰금액":  award_amt,
                "예정가격":  presmpt,
                "진행상태":  item.get("progrsDivCdNm", ""),
            })
        df = pd.DataFrame(rows)
        df["낙찰금액"] = pd.to_numeric(df["낙찰금액"], errors="coerce")
        df["예정가격"] = pd.to_numeric(df["예정가격"], errors="coerce")
        mask = df["예정가격"].notna() & (df["예정가격"] > 0) & df["낙찰금액"].notna()
        df.loc[mask, "낙찰률"] = (df.loc[mask, "낙찰금액"] / df.loc[mask, "예정가격"] * 100).round(3)
        return df
