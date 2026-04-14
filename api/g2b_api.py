import requests
import time
import pandas as pd
from datetime import datetime, timedelta
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import API_KEY

# ── 건설업 면허제한 업종명 → 코드 매핑 (getBidPblancListInfoLicenseLimit 기준) ─────
CNSTTY_CODE_MAP: dict[str, str] = {
    # 전문건설업
    "실내건축공사업":                    "4990",
    "금속창호ㆍ지붕건축물조립공사업":    "4991",
    "조경식재ㆍ시설물공사업":            "4993",
    "상ㆍ하수도설비공사업":              "4996",
    # 설비·통신·소방
    "정보통신공사업":                    "0036",
    "전기공사업":                        "0037",
    "전문소방시설공사업":                "0040",
    "일반소방시설공사업(전기)":          "0039",
    # 환경
    "환경전문공사업(대기분야)":          "0044",
    "환경전문공사업(수질분야)":          "0046",
    # 기타
    "건축사사무소":                      "4817",
}

# ── 확인된 정확한 엔드포인트 ──────────────────────────────────────────────
BID_BASE    = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"   # 입찰공고
SCSBID_BASE = "https://apis.data.go.kr/1230000/as/ScsbidInfoService"      # 낙찰결과
BSS_BASE    = "https://apis.data.go.kr/1230000/ad/BssAmtOpengInfoService" # 기초금액공개 (미승인)
CNTRCT_BASE = "https://apis.data.go.kr/1230000/ao/CntrctInfoService"      # 계약정보
OPEN_BASE   = "https://apis.data.go.kr/1230000/ao/PubDataOpnStdService"   # 공공데이터개방표준

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
# 참가제한지역/업종명 필터 지원 낙찰된 목록 현황 PPSSrch 엔드포인트
WIN_STTS_OPS = {
    "공사": "getScsbidListSttusCnstwkPPSSrch",
    "용역": "getScsbidListSttusServcPPSSrch",
    "물품": "getScsbidListSttusThngPPSSrch",
}
BSS_OPS = {
    "용역": "getBssAmtOpengListInfoServcPPSSrch",
    "물품": "getBssAmtOpengListInfoThngPPSSrch",
    "공사": "getBssAmtOpengListInfoCnstwkPPSSrch",
}
# 예비가격상세 — plnprc(예정가격)/bssamt(기초금액) 실제값 제공 (경쟁입찰 전용)
PREPAR_PC_OPS = {
    "공사": "getOpengResultListInfoCnstwkPreparPcDetail",
    "용역": "getOpengResultListInfoServcPreparPcDetail",
    "물품": "getOpengResultListInfoThngPreparPcDetail",
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
    def get_bid_by_no(self, bid_no: str, bid_type: str | None = None) -> dict | None:
        """
        최근 90일을 14일씩 구간으로 나눠 페이지네이션으로 전수 탐색.
        bidNtceNo API 파라미터가 서버 필터링 미지원이므로 클라이언트 직접 비교.
        최신 구간부터 검색해 최근 공고는 빠르게 발견.
        bid_type 지정 시 해당 업종만 검색 (속도 3배 향상).
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

        # bid_type 지정 시 해당 업종만, 아니면 전체 검색
        ops_to_search = {bid_type: BID_OPS[bid_type]} if bid_type and bid_type in BID_OPS else BID_OPS

        for start_str, end_str in windows:
            for bid_type, op in ops_to_search.items():
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
                # 전체 페이지 역순 탐색 (최신 공고가 마지막 페이지에 있음)
                pages_to_check = list(range(last_page, 0, -1))

                for page in pages_to_check:
                    try:
                        data = self._get(url, {**base_params, "pageNo": page})
                        for item in self._items(data):
                            no = item.get("bidNtceNo", "")
                            if no == bid_no_clean or no == bid_no:
                                result = self._parse_bid_detail(item, bid_type)
                                # 기초금액공개 API로 정확한 기초금액 덮어쓰기
                                bss = self._get_bss_amt(bid_no_clean, bid_type)
                                if bss:
                                    result["기초금액"] = bss
                                return result
                    except Exception:
                        continue
        return None

    def get_price_detail(self, bid_no: str, bid_type: str = "공사") -> dict | None:
        """예비가격상세 API로 실제 예정가격·기초금액·예비가격 후보 전체 조회.
        경쟁입찰(복수예가) 공고에만 데이터 존재. 수의계약은 None 반환.
        반환: {"예정가격": float, "기초금액": float, "예비가격목록": [float, ...]}
        """
        op = PREPAR_PC_OPS.get(bid_type) or PREPAR_PC_OPS["공사"]
        url = f"{SCSBID_BASE}/{op}"
        bid_no_clean = bid_no.split("-")[0].strip()
        try:
            data = self._get(url, {
                "bidNtceNo": bid_no_clean,
                "numOfRows": 20,   # 최대 15개 후보 + 여유
                "pageNo": 1,
            })
            items = self._items(data)
            if not items:
                return None
            # 첫 레코드에서 공통 필드 추출
            plnprc = float(items[0].get("plnprc") or 0) or None
            bssamt = float(items[0].get("bssamt") or 0) or None
            if not plnprc and not bssamt:
                return None
            # 전체 레코드에서 예비가격 후보 수집 (번호순 정렬)
            candidates = []
            for it in sorted(items, key=lambda x: int(x.get("compnoRsrvtnPrceSno") or 0)):
                v = float(it.get("bsisPlnprc") or 0)
                if v > 0:
                    candidates.append(v)
            return {"예정가격": plnprc, "기초금액": bssamt, "예비가격목록": candidates}
        except Exception:
            return None

    def get_openg_compt(self, bid_no: str) -> list[dict]:
        """개찰완료 목록 조회 — 해당 공고의 전체 입찰자 데이터 반환.
        반환: [{"예정가격": float, "입찰금액": float, "낙찰률": float,
                "drwt1": int, "drwt2": int}, ...] (opengRank 순)
        """
        bid_no_clean = bid_no.split("-")[0].strip()
        url = f"{SCSBID_BASE}/getOpengResultListInfoOpengCompt"
        try:
            data = self._get(url, {
                "bidNtceNo": bid_no_clean,
                "numOfRows": 300,
                "pageNo": 1,
            })
            items = self._items(data)
            result = []
            for it in sorted(items, key=lambda x: int(x.get("opengRank") or 999)):
                try:
                    bid_amt  = float(it.get("bidprcAmt") or 0)
                    bid_rate = float(it.get("bidprcrt")  or 0)
                    if bid_amt <= 0 or bid_rate <= 0:
                        continue
                    presmpt = round(bid_amt / (bid_rate / 100))
                    drwt1 = int((it.get("drwtNo1") or "0").strip() or 0)
                    drwt2 = int((it.get("drwtNo2") or "0").strip() or 0)
                    result.append({
                        "예정가격": presmpt,
                        "입찰금액": bid_amt,
                        "낙찰률":   bid_rate,
                        "drwt1":   drwt1,
                        "drwt2":   drwt2,
                    })
                except Exception:
                    continue
            return result
        except Exception:
            return []

    def get_rsrvtn_prc(self, bid_no: str, openg_date: str = "") -> float | None:
        """공공데이터개방표준서비스 계약정보로 예정가격(rsrvtnPrce) 조회.
        openg_date: 개찰일 (YYYYMMDD). 개찰일 ±14일 범위로 계약정보 검색 후 bidNtceNo 매칭.
        반환: 예정가격(float) 또는 None
        """
        bid_no_clean = bid_no.split("-")[0].strip()
        url = f"{OPEN_BASE}/getDataSetOpnStdCntrctInfo"
        try:
            # 계약 체결은 개찰 후 수 일~수 주 소요 → 개찰일 기준 0~60일 이후 범위
            if openg_date and len(openg_date) >= 8:
                from datetime import datetime, timedelta
                base_dt = datetime.strptime(openg_date[:8], "%Y%m%d")
            else:
                base_dt = datetime.now()
            bgndt = base_dt.strftime("%Y%m%d")
            enddt = (base_dt + timedelta(days=60)).strftime("%Y%m%d")
            data = self._get(url, {
                "cntrctCnclsBgnDate": bgndt,
                "cntrctCnclsEndDate": enddt,
                "numOfRows": 500,
                "pageNo": 1,
            })
            items = self._items(data)
            for it in items:
                if it.get("bidNtceNo", "").split("-")[0] == bid_no_clean:
                    v = float(it.get("rsrvtnPrce") or 0)
                    if v > 0:
                        return v
        except Exception:
            pass
        return None

    def _get_bss_amt(self, bid_no: str, bid_type: str,
                    keyword: str = "", presmpt: float = 0) -> float | None:
        """기초금액 조회 — 3단계 시도
        1) 기초금액공개정보서비스 (BssAmtOpengInfoService)
        2) 낙찰결과 API — 동일 공고번호 (이미 개찰된 경우 정확한 bssAmt 존재)
        3) 낙찰결과 API — 유사 공고(같은 업종·금액대) bssAmt/presmptPrce 비율 추정
        """
        now = datetime.now()
        bid_no_clean = bid_no.split("-")[0].strip()

        # ── 1단계: 기초금액공개 API ───────────────────────────────────────
        for days_back in [30, 90, 180]:
            start = _fmt_bid(now - timedelta(days=days_back))
            end   = _fmt_bid(now)
            for btype in ([bid_type] + [t for t in BSS_OPS if t != bid_type]):
                url = f"{BSS_BASE}/{BSS_OPS[btype]}"
                try:
                    data = self._get(url, {
                        "numOfRows": 500, "pageNo": 1,
                        "inqryDiv": "1",
                        "inqryBgnDt": start + "0000",
                        "inqryEndDt": end   + "2359",
                    })
                    for item in self._items(data):
                        no = item.get("bidNtceNo", "")
                        if no == bid_no_clean or no == bid_no:
                            for field in ("bssAmt", "bidBasicAmt", "opengBssAmt"):
                                try:
                                    amt = float(item.get(field) or 0)
                                    if amt > 0:
                                        return amt
                                except Exception:
                                    pass
                except Exception:
                    continue

        # ── 2단계: 낙찰결과 API — 동일 공고번호 ──────────────────────────
        w_start = _fmt_win(now - timedelta(days=180))
        w_end   = _fmt_win(now)
        for btype in ([bid_type] + [t for t in WIN_OPS if t != bid_type]):
            url = f"{SCSBID_BASE}/{WIN_OPS[btype]}"
            try:
                data = self._get(url, {
                    "numOfRows": 100, "pageNo": 1,
                    "inqryDiv": "2",
                    "inqryBgnDt": w_start,
                    "inqryEndDt": w_end,
                })
                for item in self._items(data):
                    no = item.get("bidNtceNo", "")
                    if no == bid_no_clean or no == bid_no:
                        try:
                            amt = float(item.get("bssAmt") or 0)
                            if amt > 0:
                                return amt
                        except Exception:
                            pass
            except Exception:
                continue

        # ── 3단계: 낙찰결과 유사 공고 bssAmt/presmptPrce 비율 추정 ────────
        if presmpt and presmpt > 0:
            try:
                similar_df = self.get_winner_list(
                    bid_type=bid_type,
                    keyword=keyword[:6] if keyword else "",
                    start_date=_fmt_win(now - timedelta(days=180)),
                    end_date=_fmt_win(now),
                    rows=200,
                )
                if not similar_df.empty and "기초금액" in similar_df.columns and "예정가격" in similar_df.columns:
                    mask = (
                        similar_df["기초금액"].notna() & (similar_df["기초금액"] > 0) &
                        similar_df["예정가격"].notna() & (similar_df["예정가격"] > 0)
                    )
                    sub = similar_df[mask]
                    # 금액대 필터: presmpt 기준 ±3배
                    sub = sub[(sub["기초금액"] >= presmpt / 3) & (sub["기초금액"] <= presmpt * 3)]
                    if len(sub) >= 5:
                        # 기초금액/예정가격 비율 중앙값으로 현재 추정가격에서 기초금액 역산
                        ratio = (sub["기초금액"] / sub["예정가격"]).median()
                        estimated = round(presmpt * ratio)
                        if estimated > 0:
                            return estimated
            except Exception:
                pass

        return None

    def _get_license_code(self, bid_no: str, reg_dt: str = "") -> str:
        """입찰공고 면허제한 API로 업종코드 조회.
        bidNtceNo는 서버 필터 미지원 → 날짜 범위를 좁혀 클라이언트에서 매칭.
        lcnsLmtNm 형식: "금속창호ㆍ지붕건축물조립공사업/4991" → "4991" 반환."""
        try:
            now = datetime.now()
            url = f"{BID_BASE}/getBidPblancListInfoLicenseLimit"
            # reg_dt로 ±1일 좁힘, 없으면 최근 14일
            if reg_dt:
                try:
                    base = datetime.strptime(reg_dt[:10], "%Y-%m-%d")
                except Exception:
                    base = now
                bgn = _fmt_bid(base - timedelta(days=1)) + "0000"
                end = _fmt_bid(base + timedelta(days=1)) + "2359"
            else:
                bgn = _fmt_bid(now - timedelta(days=14)) + "0000"
                end = _fmt_bid(now) + "2359"

            bid_no_clean = bid_no.split("-")[0].strip()
            page = 1
            while True:
                data = self._get(url, {
                    "inqryDiv": "1", "inqryBgnDt": bgn, "inqryEndDt": end,
                    "numOfRows": 500, "pageNo": page,
                })
                items = self._items(data)
                if not items:
                    break
                for it in items:
                    if it.get("bidNtceNo", "") == bid_no_clean:
                        nm = it.get("lcnsLmtNm", "")
                        if "/" in nm:
                            return nm.split("/")[-1].strip()
                total = int(data.get("response", {}).get("body", {}).get("totalCount", 0))
                if page * 500 >= total:
                    break
                page += 1
        except Exception:
            pass
        return ""

    def get_license_code_map(self, start_dt: str, end_dt: str,
                             bsns_div: str = "공사",
                             target_bid_nos: set | None = None,
                             max_pages: int = 30) -> dict:
        """기간 내 공고의 면허코드 배치 조회. {bid_no: set(industry_cd)}.

        target_bid_nos가 주어지면 해당 공고번호들을 모두 찾는 즉시 조기 종료.
        """
        result: dict[str, set] = {}
        remaining = set(target_bid_nos) if target_bid_nos else None
        try:
            url = f"{BID_BASE}/getBidPblancListInfoLicenseLimit"
            page = 1
            while page <= max_pages:
                data = self._get(url, {
                    "inqryDiv": "1", "inqryBgnDt": start_dt, "inqryEndDt": end_dt,
                    "numOfRows": 500, "pageNo": page,
                })
                items = self._items(data)
                if not items:
                    break
                for it in items:
                    if bsns_div and it.get("bsnsDivNm", "") != bsns_div:
                        continue
                    bn = it.get("bidNtceNo", "")
                    nm = it.get("lcnsLmtNm", "")
                    if not bn or "/" not in nm:
                        continue
                    cd = nm.split("/")[-1].strip()
                    if cd:
                        result.setdefault(bn, set()).add(cd)
                        if remaining is not None:
                            remaining.discard(bn)
                if remaining is not None and not remaining:
                    break  # 타겟 공고 모두 찾음
                total = int(data.get("response", {}).get("body", {}).get("totalCount", 0))
                if page * 500 >= total:
                    break
                page += 1
        except Exception:
            pass
        return result

    def _parse_bid_detail(self, item: dict, bid_type: str) -> dict:
        def num(v):
            try: return float(v) if v else None
            except: return None

        # 기초금액: 직접 필드 → 배정예산액(VAT 포함 그대로) → 추정가격+VAT 계산
        def pos(v):
            """양수인 경우만 반환"""
            x = num(v)
            return x if x and x > 0 else None

        # 1순위: 기초금액 직접 필드 (bssAmt, bidBasicAmt)
        base = pos(item.get("bssAmt")) or pos(item.get("bidBasicAmt"))

        # 2순위: 추정가격 + VAT 계산 (bdgtAmt/asignBdgtAmt는 예산금액이라 사용 안 함)
        if not base:
            presmpt = pos(item.get("presmptPrce"))
            vat     = pos(item.get("VAT")) or pos(item.get("indutyVAT"))
            if presmpt and vat:
                base = round(presmpt + vat)
            elif presmpt:
                base = round(presmpt * 1.1)  # VAT 10% 추정
        # 복수예가 후보 수 / 추첨 수 (공고마다 다를 수 있음, 기본 15개/2개)
        total_prd = int(item.get("totPrdprcNum") or 15)
        draw_prd  = int(item.get("drwtPrdprcNum") or 4)
        # 지역: 공사현장지역명(공사) → 참가제한지역명 → 기관지역명 순
        region = (
            item.get("cnstrtsiteRgnNm") or      # 공사현장지역명 (공사)
            item.get("prtcptLmtRgnNm") or       # 참가제한지역명
            item.get("ntceInsttRgnNm") or        # 공고기관지역명
            ""
        ).strip()
        # 업종명: 유형별로 실제 존재하는 필드 우선
        industry_nm = (
            item.get("mainCnsttyNm") or          # 주요업종명 — 업종제한사항 (공사, 예: "전기공사업")
            item.get("pubPrcrmntClsfcNm") or     # 공공조달분류명 (용역 상세)
            item.get("pubPrcrmntMidClsfcNm") or  # 중분류
            item.get("dtilPrdctClsfcNoNm") or    # 품목분류명 (물품)
            item.get("srvceDivNm") or            # 용역구분명
            item.get("mainCnstwkBsnsNm") or      # 공사업종명
            item.get("prdctClsfcNm") or
            item.get("indstrytyClsfNm") or
            ""
        ).strip()
        industry_cd = (
            item.get("pubPrcrmntClsfcNo") or     # 공공조달분류번호 (용역)
            item.get("dtilPrdctClsfcNo") or      # 품목분류번호 (물품)
            item.get("srvceDivCd") or
            item.get("prdctClsfcCd") or
            item.get("indstrytyClsfCd") or
            ""
        ).strip()
        # 공사 업종코드: API 응답에 직접 필드 없음
        # 1) 정적 매핑 (빠름) → 2) 면허제한 API (느리지만 정확)
        if not industry_cd and industry_nm:
            industry_cd = CNSTTY_CODE_MAP.get(industry_nm, "")
        if not industry_cd and item.get("indstrytyLmtYn") == "Y":
            bid_no_for_lcns = item.get("bidNtceNo", "")
            if bid_no_for_lcns:
                reg_dt = item.get("rgstDt", "") or item.get("bidNtceDt", "")
                lcns_cd = self._get_license_code(bid_no_for_lcns, reg_dt)
                if lcns_cd:
                    industry_cd = lcns_cd
                    # 업종명도 역매핑으로 채울 수 있으면 채움
                    if not industry_nm:
                        rev = {v: k for k, v in CNSTTY_CODE_MAP.items()}
                        industry_nm = rev.get(industry_cd, "")
        industry = industry_nm or industry_cd
        return {
            "공고번호":     item.get("bidNtceNo", ""),
            "공고명":      item.get("bidNtceNm", ""),
            "공고기관":    item.get("ntceInsttNm", ""),
            "수요기관":    item.get("dminsttNm", ""),
            "기초금액":    base,
            "추정금액":    num(item.get("presmptPrce")),
            "낙찰하한율":   num(item.get("sucsfbidLwltRate")),
            "낙찰방법":    item.get("sucsfbidMthdNm", ""),
            "계약방식":    item.get("cntrctCnclsMthdNm", ""),
            "예가방식":    item.get("prearngPrceDcsnMthdNm", ""),
            "입찰마감일":   item.get("bidClseDt", ""),
            "개찰일시":    item.get("opengDt", ""),
            "공사종류":    bid_type,
            "후보수":      total_prd,
            "추첨수":      draw_prd,
            "참가제한지역": region,
            "업종":        industry,
            "업종코드":    industry_cd,
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
        ntce_instt_nm: str = "",        # 공고기관명 키워드 (구형 openg 엔드포인트용)
        bid_ntce_nm: str = "",          # 공고명 키워드 (구형 openg 엔드포인트용)
        prtcpt_lmt_rgn_nm: str = "",    # 참가제한지역명 (stts PPSSrch 엔드포인트: 실제 작동)
        indstryty_nm: str = "",         # 업종명 — API가 무시함, 사용 비권장
        indstryty_cd: str = "",         # 업종코드 (stts PPSSrch 엔드포인트: 실제 작동)
    ) -> pd.DataFrame:
        now = datetime.now()
        start_str = start_date or _fmt_win(now - timedelta(days=14))
        end_str   = end_date   or _fmt_win(now)

        def parse_dt(s: str) -> datetime:
            return datetime.strptime(s[:8], "%Y%m%d")

        dt_start = parse_dt(start_str)
        dt_end   = parse_dt(end_str)

        # 14일씩 구간 분할 — 최신 구간 먼저 처리해 rows 한도에 최근 데이터가 채워지도록
        CHUNK = timedelta(days=14)
        windows = []
        cur = dt_start
        while cur < dt_end:
            chunk_end = min(cur + CHUNK - timedelta(days=1), dt_end)
            windows.append((cur, chunk_end))
            cur = chunk_end + timedelta(days=1)
        windows.reverse()  # newest first

        # prtcpt_lmt_rgn_nm / indstryty_cd 제공 시 stts PPSSrch 엔드포인트 사용
        # indstryty_nm 은 API가 무시하므로 indstryty_cd(업종코드)를 사용해야 함
        use_stts = bool(prtcpt_lmt_rgn_nm or indstryty_cd or indstryty_nm)
        if use_stts:
            url = f"{SCSBID_BASE}/{WIN_STTS_OPS.get(bid_type, WIN_STTS_OPS['용역'])}"
            inqry_div = "1"
        else:
            url = f"{SCSBID_BASE}/{WIN_OPS.get(bid_type, WIN_OPS['용역'])}"
            inqry_div = "2"

        all_items = []
        rows_per_chunk = min(rows, 500)

        for w_start, w_end in windows:
            params = {
                "numOfRows": rows_per_chunk, "pageNo": page,
                "inqryDiv": inqry_div,
                "inqryBgnDt": w_start.strftime("%Y%m%d") + "0000",
                "inqryEndDt": w_end.strftime("%Y%m%d") + "2359",
            }
            if keyword:
                params["bidNtceNm"] = keyword
            if not use_stts:
                if bid_ntce_nm and not keyword:
                    params["bidNtceNm"] = bid_ntce_nm
                if ntce_instt_nm:
                    params["ntceInsttNm"] = ntce_instt_nm
            else:
                if prtcpt_lmt_rgn_nm:
                    params["prtcptLmtRgnNm"] = prtcpt_lmt_rgn_nm
                if indstryty_cd:
                    params["indstrytyCd"] = indstryty_cd
                # indstryty_nm 은 API가 무시하므로 전달하지 않음
                if bid_ntce_nm and not keyword:
                    params["bidNtceNm"] = bid_ntce_nm
                if ntce_instt_nm:
                    params["ntceInsttNm"] = ntce_instt_nm
            try:
                data = self._get(url, params)
                all_items.extend(self._items(data))
            except Exception:
                continue

            if len(all_items) >= rows:
                break

        if use_stts:
            return self._parse_winner_stts(all_items[:rows])
        return self._parse_winner(all_items[:rows])

    def _parse_winner_stts(self, items: list) -> pd.DataFrame:
        """낙찰된 목록 현황 PPSSrch (getScsbidListSttusXXXPPSSrch) 응답 파싱.
        sucsfbidAmt / sucsfbidRate / bidwinnrNm 구조 사용."""
        if not items:
            return pd.DataFrame()
        rows = []
        for item in items:
            try: award_amt = float(item.get("sucsfbidAmt") or 0) or None
            except: award_amt = None
            try: rate = float(item.get("sucsfbidRate") or 0) or None
            except: rate = None
            # 예정가격 역산 (sucsfbidAmt / sucsfbidRate%)
            presmpt = None
            if award_amt and rate and rate > 0:
                presmpt = round(award_amt / (rate / 100))
            bss_amt = presmpt  # 기초금액 필드 없으므로 예정가격으로 근사

            # stts 응답: ntceInsttNm 없음 → dminsttNm을 공고기관으로 사용
            instt = item.get("dminsttNm", "")
            rows.append({
                "공고번호":     item.get("bidNtceNo", ""),
                "공고명":      item.get("bidNtceNm", ""),
                "공고기관":    instt,
                "수요기관":    instt,
                "입찰시작일시": item.get("bidNtceDt", "") or item.get("rgstDt", ""),
                "입찰마감일시": item.get("bidClseDt", ""),
                "개찰일시":    item.get("rlOpengDt", "") or item.get("opengDt", ""),
                "참가업체수":   int(item.get("prtcptCnum") or 0),
                "낙찰업체명":   item.get("bidwinnrNm", ""),
                "낙찰금액":    award_amt,
                "예정가격":    presmpt,
                "기초금액":    bss_amt,
                "낙찰률":      rate,
                "진행상태":    "",
                "참가제한지역": (item.get("prtcptLmtRgnNm") or "").strip(),
                "업종":        "",
                "업종코드":    "",
                "계약방식":    "수의계약",
            })
        df = pd.DataFrame(rows)
        for col in ["낙찰금액", "예정가격", "기초금액", "낙찰률"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        # 사정률 = 낙찰금액 / 기초금액 × 100
        mask = df["기초금액"].notna() & (df["기초금액"] > 0) & df["낙찰금액"].notna()
        df.loc[mask, "사정률"] = (df.loc[mask, "낙찰금액"] / df.loc[mask, "기초금액"] * 100).round(3)
        return df

    def _parse_winner(self, items: list) -> pd.DataFrame:
        if not items:
            return pd.DataFrame()
        rows = []
        for item in items:
            corp_info = item.get("opengCorpInfo", "")
            award_amt = None
            corp_name = ""
            direct_rate = None  # opengCorpInfo parts[4] 낙찰률
            if corp_info:
                parts = corp_info.split("^")
                if len(parts) >= 4:
                    corp_name = parts[0]
                    try: award_amt = float(parts[3])
                    except: pass
                if len(parts) >= 5:
                    try:
                        v = float(parts[4])
                        if 50 < v <= 110:  # 유효 낙찰률 범위
                            direct_rate = v
                    except: pass

            try: presmpt = float(item.get("presmptPrce") or 0) or None
            except: presmpt = None
            # presmptPrce 없으면 낙찰금액 / 낙찰률로 역산
            if presmpt is None and award_amt and direct_rate:
                presmpt = round(award_amt / (direct_rate / 100))

            # 기초금액: bssAmt → 없으면 역산된 예정가격으로 근사 (복수예가: 예정가 ≈ 기초금액 ±2%)
            try: bss_amt = float(item.get("bssAmt") or 0) or None
            except: bss_amt = None
            if bss_amt is None and presmpt:
                bss_amt = presmpt

            # 참가제한지역: prtcptLmtRgnNm (없으면 ntceInsttRgnNm 시도)
            region = (item.get("prtcptLmtRgnNm") or item.get("ntceInsttRgnNm") or "").strip()

            # 업종명 + 업종코드 분리 파싱
            industry_nm = (
                item.get("srvceDivNm") or
                item.get("mainCnstwkBsnsNm") or
                item.get("prdctClsfcNm") or
                item.get("indstrytyClsfNm") or
                ""
            ).strip()
            industry_cd = (
                item.get("srvceDivCd") or
                item.get("mainCnstwkBsns") or   # 공사는 코드가 mainCnstwkBsns에 있음
                item.get("prdctClsfcCd") or
                item.get("indstrytyClsfCd") or
                ""
            ).strip()
            industry = industry_nm or industry_cd

            rows.append({
                "공고번호":     item.get("bidNtceNo", ""),
                "공고명":      item.get("bidNtceNm", ""),
                "공고기관":    item.get("ntceInsttNm", ""),
                "수요기관":    item.get("dminsttNm", ""),
                "입찰시작일시": item.get("bidNtceBgnDt", "") or item.get("bidNtceDt", ""),
                "입찰마감일시": item.get("bidClseDt", ""),
                "개찰일시":    item.get("opengDt", ""),
                "참가업체수":   int(item.get("prtcptCnum") or 0),
                "낙찰업체명":   corp_name,
                "낙찰금액":    award_amt,
                "예정가격":    presmpt,
                "기초금액":    bss_amt,
                "낙찰률_직접":  direct_rate,
                "진행상태":    item.get("progrsDivCdNm", ""),
                "참가제한지역": region,
                "업종":        industry,
                "업종코드":    industry_cd,
                "계약방식":    item.get("cntrctCnclsMthdNm", ""),
            })
        df = pd.DataFrame(rows)
        df["낙찰금액"] = pd.to_numeric(df["낙찰금액"], errors="coerce")
        df["예정가격"] = pd.to_numeric(df["예정가격"], errors="coerce")
        df["기초금액"] = pd.to_numeric(df["기초금액"], errors="coerce")
        df["낙찰률_직접"] = pd.to_numeric(df["낙찰률_직접"], errors="coerce")
        # 낙찰률: 예정가격 역산값 우선, 없으면 opengCorpInfo parts[4] 직접값
        mask = df["예정가격"].notna() & (df["예정가격"] > 0) & df["낙찰금액"].notna()
        df.loc[mask, "낙찰률"] = (df.loc[mask, "낙찰금액"] / df.loc[mask, "예정가격"] * 100).round(3)
        mask_direct = df["낙찰률"].isna() & df["낙찰률_직접"].notna()
        df.loc[mask_direct, "낙찰률"] = df.loc[mask_direct, "낙찰률_직접"]
        df.drop(columns=["낙찰률_직접"], inplace=True)
        # 사정률 = 낙찰금액 / 기초금액 × 100 (발주처별 낙찰 경향 분석용)
        mask2 = df["기초금액"].notna() & (df["기초금액"] > 0) & df["낙찰금액"].notna()
        df.loc[mask2, "사정률"] = (df.loc[mask2, "낙찰금액"] / df.loc[mask2, "기초금액"] * 100).round(3)
        return df
