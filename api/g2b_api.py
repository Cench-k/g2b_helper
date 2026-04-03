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
            "inqryBgnDt": start[:8] + "0000",
            "inqryEndDt": end[:8] + "2359",
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
        최근 90일을 14일씩 구간으로 나눠 페이지네이션으로 전수 탐색.
        bidNtceNo API 파라미터가 서버 필터링 미지원이므로 클라이언트 직접 비교.
        최신 구간부터 검색해 최근 공고는 빠르게 발견.
        """
        now = datetime.now()
        bid_no_clean = bid_no.split("-")[0].strip()

        # 최근 90일을 14일씩 나눈 창 (최신 → 과거 순)
        windows = []
        cur_end = now
        while cur_end > now - timedelta(days=90):
            cur_start = max(cur_end - timedelta(days=13), now - timedelta(days=90))
            windows.append((_fmt_bid(cur_start), _fmt_bid(cur_end)))
            cur_end = cur_start - timedelta(days=1)

        ROWS = 500
        MAX_PAGES = 4  # 구간당 앞뒤 각 2페이지 (최신·오래된 공고 모두 대응)

        for start_str, end_str in windows:
            for bid_type, op in BID_OPS.items():
                url = f"{BID_BASE}/{op}"
                base_params = {
                    "numOfRows": ROWS,
                    "inqryDiv": "1",
                    "inqryBgnDt": start_str,
                    "inqryEndDt": end_str,
                }
                # 총 건수 파악
                try:
                    data = self._get(url, {**base_params, "pageNo": 1})
                    total = int(data.get("response", {}).get("body", {}).get("totalCount", 0))
                except Exception:
                    continue
                if total == 0:
                    continue

                last_page = (total + ROWS - 1) // ROWS
                # 마지막 페이지부터 역순으로 탐색 (최신 공고) + 앞쪽도 탐색
                pages_to_check = list(range(last_page, max(last_page - MAX_PAGES, 0), -1))
                pages_to_check += list(range(1, min(MAX_PAGES + 1, last_page)))

                for page in pages_to_check:
                    try:
                        data = self._get(url, {**base_params, "pageNo": page})
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
        draw_prd  = int(item.get("drwtPrdprcNum") or 4)
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

    # ── 낙찰 결과 조회 (14일 제한 → 자동 분할 호출) ──────────────────────
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
        start_str = start_date or _fmt_win(now - timedelta(days=14))
        end_str   = end_date   or _fmt_win(now)

        # 날짜 파싱 (YYYYMMDD 또는 YYYYMMDDHHMM 또는 YYYYMMDDHHMMSS)
        def parse_dt(s: str) -> datetime:
            s = s[:8]
            return datetime.strptime(s, "%Y%m%d")

        dt_start = parse_dt(start_str)
        dt_end   = parse_dt(end_str)

        # 14일씩 구간 분할
        CHUNK = timedelta(days=14)
        windows = []
        cur = dt_start
        while cur < dt_end:
            chunk_end = min(cur + CHUNK - timedelta(days=1), dt_end)
            windows.append((cur, chunk_end))
            cur = chunk_end + timedelta(days=1)

        url = f"{SCSBID_BASE}/{WIN_OPS.get(bid_type, WIN_OPS['용역'])}"
        all_items = []
        rows_per_chunk = min(rows, 500)  # 구간당 최대 500건

        for w_start, w_end in windows:
            params = {
                "numOfRows": rows_per_chunk, "pageNo": page,
                "inqryDiv": "2",
                "inqryBgnDt": w_start.strftime("%Y%m%d") + "0000",
                "inqryEndDt": w_end.strftime("%Y%m%d") + "2359",
            }
            if keyword:
                params["bidNtceNm"] = keyword
            try:
                data = self._get(url, params)
                all_items.extend(self._items(data))
            except Exception:
                continue  # 구간 실패 시 건너뜀

            if len(all_items) >= rows:
                break

        return self._parse_winner(all_items[:rows])

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

            # 기초금액: bssAmt → 사정률 계산에 사용
            try: bss_amt = float(item.get("bssAmt") or 0) or None
            except: bss_amt = None

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
                "기초금액":  bss_amt,
                "진행상태":  item.get("progrsDivCdNm", ""),
            })
        df = pd.DataFrame(rows)
        df["낙찰금액"] = pd.to_numeric(df["낙찰금액"], errors="coerce")
        df["예정가격"] = pd.to_numeric(df["예정가격"], errors="coerce")
        df["기초금액"] = pd.to_numeric(df["기초금액"], errors="coerce")
        mask = df["예정가격"].notna() & (df["예정가격"] > 0) & df["낙찰금액"].notna()
        df.loc[mask, "낙찰률"] = (df.loc[mask, "낙찰금액"] / df.loc[mask, "예정가격"] * 100).round(3)
        # 사정률 = 낙찰금액 / 기초금액 × 100 (발주처별 낙찰 경향 분석용)
        mask2 = df["기초금액"].notna() & (df["기초금액"] > 0) & df["낙찰금액"].notna()
        df.loc[mask2, "사정률"] = (df.loc[mask2, "낙찰금액"] / df.loc[mask2, "기초금액"] * 100).round(3)
        return df
