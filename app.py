import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

from api.g2b_api import G2BAPI
from analysis.bid_analyzer import calc_bid_range, analyze_winner_stats, recommend_from_stats, extract_keyword, tiered_filter, filter_by_region, filter_by_industry, format_won, format_won_exact, calc_optimal_bid, estimate_competitor_count
from analysis.demo_data import get_demo_winner_list
# 에러 코드 정의
ERR = {
    "E-01": "[E-01] API 키 인증 오류입니다. data.go.kr에서 서비스 승인 상태를 확인하세요.",
    "E-02": "[E-02] API 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.",
    "E-03": "[E-03] API 응답 시간이 초과됐습니다. 네트워크 상태를 확인하세요.",
    "E-04": "[E-04] 조달청 API 서버 오류입니다. 잠시 후 다시 시도하세요.",
    "E-05": "[E-05] 검색 결과가 없습니다. 검색어 또는 날짜 범위를 조정해보세요.",
    "E-06": "[E-06] 해당 공고번호로 공고를 찾을 수 없습니다. 번호를 확인하세요.",
    "E-07": "[E-07] 알 수 없는 오류가 발생했습니다.",
}
from db.supabase_client import save_bid_record, load_bid_records, delete_bid_record, cache_get_bid, cache_save_bid

st.set_page_config(
    page_title="나라장터 낙찰 도우미",
    page_icon="🏛️",
    layout="wide",
)

# ── CSS ──────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.metric-card {
    background: #f0f2f6;
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 8px;
}
.metric-label { font-size: 13px; color: #666; margin-bottom: 4px; }
.metric-value { font-size: 22px; font-weight: 700; color: #1f3c88; }
.metric-sub { font-size: 12px; color: #888; margin-top: 2px; }
.recommend-box {
    background: linear-gradient(135deg, #1f3c88, #3498db);
    color: white;
    border-radius: 12px;
    padding: 20px 24px;
    text-align: center;
    margin: 12px 0;
}
.recommend-box .label { font-size: 14px; opacity: 0.85; margin-bottom: 6px; }
.recommend-box .value { font-size: 28px; font-weight: 800; }
.recommend-box .sub { font-size: 13px; opacity: 0.75; margin-top: 4px; }
</style>
""", unsafe_allow_html=True)

api = G2BAPI()

# 발주처별 예비가격 범위 라벨 (UI selectbox 키와 동일해야 함)
PRICE_RANGE_OPTIONS = {
    "국가기관·조달청 (-2% ~ +2%)":            (-2.0, 2.0),
    "지자체·교육청 (-3% ~ +3%)":              (-3.0, 3.0),
    "수자원공사·철도공단·가스공사 (-2.5% ~ +2.5%)": (-2.5, 2.5),
    "공기업 LH·도로공사·한전 등 (-2% ~ +2%)": (-2.0, 2.0),
    "직접 입력 (방위사업청·군 시설 등)":        None,
}

def guess_price_range_label(agency: str) -> str:
    """공고기관명 기반 예비가격 범위 라벨 추정"""
    a = agency or ""
    if any(k in a for k in ["교육청", "시청", "군청", "구청", "도청",
                              "특별시", "광역시", "특별자치시", "특별자치도",
                              "시의회", "군의회", "구의회", "도의회"]):
        return "지자체·교육청 (-3% ~ +3%)"
    if any(k in a for k in ["수자원공사", "국가철도공단", "한국가스공사", "K-water"]):
        return "수자원공사·철도공단·가스공사 (-2.5% ~ +2.5%)"
    if any(k in a for k in ["LH", "토지주택", "도로공사", "한국전력", "한전"]):
        return "공기업 LH·도로공사·한전 등 (-2% ~ +2%)"
    if any(k in a for k in ["방위사업청", "국방부", "육군", "해군", "공군", "국군", "병무청"]):
        return "직접 입력 (방위사업청·군 시설 등)"
    return "국가기관·조달청 (-2% ~ +2%)"

def build_recent_cards(
    bid_type: str,
    region: str,
    industry_cd: str,
    base_price: float,
    contract_type: str = "",
    before_date: str = "",
) -> list:
    """동일 업종·지역 낙찰 사례 카드 (최대 6개). before_date 이전 개찰건만."""
    _recent5 = []
    if not region:
        return _recent5

    now = datetime.now()
    # before_date가 있으면 그 시점 이전까지만 조회
    if before_date:
        try:
            _end_dt = pd.to_datetime(before_date)
            _w_end = _end_dt.strftime("%Y%m%d%H%M")
        except Exception:
            _w_end = (now + timedelta(days=1)).strftime("%Y%m%d") + "0000"
    else:
        _w_end = (now + timedelta(days=1)).strftime("%Y%m%d") + "0000"

    _r5_df = pd.DataFrame()
    try:
        _anchor = pd.to_datetime(before_date) if before_date else now
    except Exception:
        _anchor = now
    # 1차: 업종+지역 필터 유지하고 기간만 넓힘
    for _days in [7, 14, 30, 90, 180, 365]:
        _r5_start = (_anchor - timedelta(days=_days)).strftime("%Y%m%d") + "000000"
        try:
            _df_tmp = api.get_winner_list(
                bid_type=bid_type,
                start_date=_r5_start, end_date=_w_end, rows=50,
                prtcpt_lmt_rgn_nm=region,
                indstryty_cd=industry_cd,
            )
            if not _df_tmp.empty and _df_tmp["낙찰금액"].dropna().shape[0] >= 3:
                _r5_df = _df_tmp
                break
        except Exception:
            continue
    # 2차(최후): 365일 내에서도 3건 미만이면 업종 필터만 제거 (지역은 유지)
    if _r5_df.empty and industry_cd:
        _r5_start = (_anchor - timedelta(days=365)).strftime("%Y%m%d") + "000000"
        try:
            _df_tmp = api.get_winner_list(
                bid_type=bid_type,
                start_date=_r5_start, end_date=_w_end, rows=50,
                prtcpt_lmt_rgn_nm=region,
                indstryty_cd="",
            )
            if not _df_tmp.empty:
                _r5_df = _df_tmp
        except Exception:
            pass

    if _r5_df.empty or "개찰일시" not in _r5_df.columns:
        return _recent5

    # 업종코드 기반 정밀 필터 (LICENSE API)
    if industry_cd:
        try:
            _target_bids = set(_r5_df["공고번호"].apply(lambda x: str(x).split("-")[0].strip()))
            _lic_start = (_anchor - timedelta(days=_days + 7)).strftime("%Y%m%d") + "0000"
            _lic_end   = _anchor.strftime("%Y%m%d%H%M")
            _lic_map   = api.get_license_code_map(
                _lic_start, _lic_end, bsns_div=bid_type,
                target_bid_nos=_target_bids,
            )
            if _lic_map:
                _r5_df = _r5_df[_r5_df["공고번호"].apply(
                    lambda bn: industry_cd in _lic_map.get(str(bn).split("-")[0].strip(), set())
                )]
        except Exception:
            pass

    if _r5_df.empty:
        return _recent5

    _sorted = _r5_df.dropna(subset=["낙찰금액"]).copy()
    _sorted["_dt"] = pd.to_datetime(_sorted["개찰일시"], errors="coerce")
    _sorted = _sorted.dropna(subset=["_dt"]).sort_values("_dt", ascending=False)

    # 계약방식 필터
    if contract_type and "계약방식" in _sorted.columns:
        _is_suui = "수의" in contract_type
        _ct_mask = _sorted["계약방식"].str.contains("수의", na=False) == _is_suui
        _ct_filtered = _sorted[_ct_mask]
        if len(_ct_filtered) >= 1:
            _sorted = _ct_filtered

    # 비슷한 금액대 필터
    _amt_col = "기초금액" if "기초금액" in _sorted.columns else "낙찰금액"
    _price_filtered = _sorted
    for _ratio in [0.5, 1.0]:
        _lo, _hi = base_price * (1 - _ratio), base_price * (1 + _ratio)
        _tmp = _sorted[
            _sorted[_amt_col].notna() &
            (_sorted[_amt_col] >= _lo) &
            (_sorted[_amt_col] <= _hi)
        ]
        if len(_tmp) >= 3:
            _price_filtered = _tmp
            break

    _N_SIM, _N_CAND = 5000, 15

    def _build_one(_row):
        _lr      = _row.get("낙찰률")
        _award   = _row.get("낙찰금액")
        _base    = _row.get("기초금액")
        _agency  = str(_row.get("공고기관") or _row.get("수요기관") or "")
        _bid_no_card = str(_row.get("공고번호", ""))

        _award_f = float(_award) if pd.notna(_award) else None
        _base_f  = float(_base)  if pd.notna(_base)  else None

        # 카드 공고별 API 3개 병렬 호출
        _detail = None; _pd = None; _compt = []
        if _bid_no_card:
            with ThreadPoolExecutor(max_workers=3) as _ex:
                _f1 = _ex.submit(api.get_bid_by_no, _bid_no_card, bid_type)
                _f2 = _ex.submit(api.get_price_detail, _bid_no_card, bid_type)
                _f3 = _ex.submit(api.get_openg_compt, _bid_no_card)
                try: _detail = _f1.result()
                except Exception: pass
                try: _pd = _f2.result()
                except Exception: pass
                try: _compt = _f3.result() or []
                except Exception: pass

        _lr_pct  = 87.745
        _n_draw  = 2
        _n_cand  = 15
        _pr_lo   = 0.98
        _pr_hi   = 1.02
        if _detail:
            _lr_pct = float(_detail.get("낙찰하한율") or _lr_pct)
            _n_draw = int(_detail.get("추첨수")  or _n_draw)
            _n_cand = int(_detail.get("후보수")  or _n_cand)
            _agency = _detail.get("공고기관") or _agency
            _bss_from_bid = _detail.get("기초금액")
            if _bss_from_bid and float(_bss_from_bid) > 0:
                _base_f = float(_bss_from_bid)
            _pr_label = guess_price_range_label(_agency)
            _pr_vals  = PRICE_RANGE_OPTIONS.get(_pr_label)
            if _pr_vals:
                _pr_lo = 1 + _pr_vals[0] / 100
                _pr_hi = 1 + _pr_vals[1] / 100

        _presmpt_f   = None
        _prepar_pool = None
        if _pd:
            if _pd.get("기초금액"):
                _base_f = _pd["기초금액"]
            _presmpt_f = _pd.get("예정가격")
            _pool = _pd.get("예비가격목록") or []
            if len(_pool) >= 2:
                _prepar_pool = _pool

        # A값 (순공사원가) — 예비가격상세 API에서
        _a_f = float(_pd.get("A값") or 0) if _pd else 0.0

        _card_vp = None
        if len(_compt) >= 3:
            _ps = [x["예정가격"] for x in _compt if x["예정가격"] > 0]
            if len(_ps) >= 3:
                # 기초금액: 실제 예정가격 분포의 중앙값
                _base_est = float(np.median(_ps))
                if not _base_f or abs(_base_est - _base_f) / _base_f < 0.05:
                    _base_f = _base_est
                # 유효율: 자기 예정가격에 대한 A값 반영 유효 하한 이상 투찰한 비율
                _lr_frac = _lr_pct / 100
                def _min_bid(p):
                    return (p - _a_f) * _lr_frac + _a_f if _a_f > 0 else p * _lr_frac
                _valid = sum(
                    1 for x in _compt
                    if x["예정가격"] > 0 and x["입찰금액"] > 0
                    and _min_bid(x["예정가격"]) <= x["입찰금액"] <= x["예정가격"]
                )
                _card_vp = _valid / len(_compt) * 100

        # 예정가격 역산 (위 API 실패 시)
        if not _presmpt_f:
            _presmpt_raw = _row.get("예정가격")
            _lr_f = float(_lr) if pd.notna(_lr) and float(_lr) > 0 else None
            if pd.notna(_presmpt_raw) and float(_presmpt_raw) > 0:
                _presmpt_f = float(_presmpt_raw)
            elif _award_f and _lr_f:
                _presmpt_f = round(_award_f / (_lr_f / 100))

        # 개찰완료 데이터 없으면 (수의계약 등) 시뮬레이션으로 fallback
        if _card_vp is None and _award_f and _base_f and _base_f > 0:
            if _prepar_pool:
                _pool_arr = np.array(_prepar_pool, dtype=float)
                _idx_r    = np.array([np.random.choice(len(_pool_arr), min(_n_draw, len(_pool_arr)), replace=False)
                                      for _ in range(_N_SIM)])
                _d_sim    = np.floor(_pool_arr[_idx_r].mean(axis=1))
            else:
                _cands = np.random.uniform(_pr_lo, _pr_hi, (_N_SIM, _n_cand)) * _base_f
                _idx_r = np.array([np.random.choice(_n_cand, _n_draw, replace=False)
                                   for _ in range(_N_SIM)])
                _d_sim = np.floor(_cands[np.arange(_N_SIM)[:, None], _idx_r].mean(axis=1))
            _f_sim   = np.ceil(np.round(_d_sim * (_lr_pct / 100), 5))
            _card_vp = float(((_award_f >= _f_sim) & (_award_f <= _d_sim)).mean() * 100)

        # 유효 낙찰하한율 = ((예정-A) × 낙찰하한율 + A) / 예정 × 100
        _lr_eff = _lr_pct
        if _a_f > 0 and _presmpt_f and _presmpt_f > 0:
            _lr_eff = ((_presmpt_f - _a_f) * (_lr_pct / 100) + _a_f) / _presmpt_f * 100

        # 투찰률 = (입찰가격 - A) / (예정가격 - A) × 100
        _tuchal = None
        if _award_f and _presmpt_f and _presmpt_f > 0:
            _denom = _presmpt_f - _a_f
            if _denom > 0:
                _tuchal = (_award_f - _a_f) / _denom * 100

        return {
            "공고명":    _row.get("공고명", "-"),
            "개찰일시":  str(_row.get("개찰일시", "-"))[:10],
            "참가업체수": int(_row.get("참가업체수", 0) or 0),
            "낙찰금액":  _award_f,
            "기초금액":  _base_f,
            "예정가격":  _presmpt_f,
            "낙찰률":    float(_lr) if pd.notna(_lr) else None,
            "낙찰하한율": _lr_eff,
            "유효율":    _card_vp,
            "투찰률":    _tuchal,
        }

    _rows_list = [r for _, r in _price_filtered.head(6).iterrows()]
    with ThreadPoolExecutor(max_workers=6) as _ex:
        _recent5 = list(_ex.map(_build_one, _rows_list))
    return _recent5


def render_cards(recent5: list, title: str = "#### 📋 직전 낙찰 사례 (동일 업종·지역 기준)"):
    """카드 리스트를 3열 그리드로 렌더링."""
    if not recent5:
        return
    st.markdown(title)
    _rank_labels = ["직전", "2번 전", "3번 전", "4번 전", "5번 전", "6번 전"]

    def _fmt_amt(v):
        if v is None: return "-"
        return f"{v:,.0f}원"

    _COLS_PER_ROW = 3
    for _row_start in range(0, len(recent5), _COLS_PER_ROW):
        _row_recs = recent5[_row_start:_row_start + _COLS_PER_ROW]
        _cols = st.columns(len(_row_recs))
        for _ci, (_col, _rec) in enumerate(zip(_cols, _row_recs)):
            _ci_abs  = _row_start + _ci
            _parts   = int(_rec["참가업체수"])
            _dt      = _rec["개찰일시"][:10] if _rec["개찰일시"] not in ("-", "") else "-"
            _name    = _rec["공고명"][:28] + "…" if len(_rec["공고명"]) > 28 else _rec["공고명"]
            _rate    = _rec.get("낙찰률")
            _award   = _rec.get("낙찰금액")
            _presmpt = _rec.get("예정가격")
            _lrpct   = _rec.get("낙찰하한율")
            _vp      = _rec.get("유효율")
            _tuchal  = _rec.get("투찰률")
            _rank_label = _rank_labels[_ci_abs]
            _vp_color = (
                "#e74c3c" if _vp is not None and _vp < 40 else
                "#f39c12" if _vp is not None and _vp < 70 else
                "#2ecc71"
            )
            _col.markdown(f"""
<div style="background:#f0f4ff;border:1px solid #c5d0e6;border-radius:10px;
padding:14px 14px;font-size:13px;line-height:1.8">
  <div style="font-size:11px;color:#888;margin-bottom:4px">{_rank_label} ({_dt})</div>
  <div style="font-size:12px;font-weight:600;color:#1f3c88;margin-bottom:8px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis"
  title="{_rec['공고명']}">{_name}</div>
  <div>👥 <b>{_parts}개사</b> 참여</div>
  <div>🎯 예정가격 <b>{_fmt_amt(_presmpt)}</b></div>
  <div>🏆 최종입찰가 <b>{_fmt_amt(_award)}</b></div>
  <div>📈 낙찰률 <b>{"%.3f" % _rate if _rate else "-"}%</b></div>
  <div>🎲 투찰률 <b>{"%.3f" % _tuchal if _tuchal else "-"}%</b></div>
  <div>📉 낙찰하한율 <b>{"%.3f" % _lrpct if _lrpct else "-"}%</b></div>
  <div>✅ 유효율 <b style="color:{_vp_color}">{"%.1f" % _vp if _vp is not None else "-"}%</b></div>
</div>""", unsafe_allow_html=True)


def check_api_status() -> tuple[bool, str]:
    """API 연결 상태 확인. (성공여부, 에러메시지) 반환"""
    try:
        df = api.get_bid_list(bid_type="용역", rows=1)
        if df.empty:
            return False, ERR["E-01"]
        return True, ""
    except ConnectionError as e:
        msg = str(e)
        if "429" in msg or "한도" in msg:
            return False, ERR["E-02"]
        if "시간 초과" in msg or "Timeout" in msg:
            return False, ERR["E-03"]
        if "500" in msg or "서버" in msg:
            return False, ERR["E-04"]
        if "401" in msg or "403" in msg or "승인" in msg or "인증" in msg:
            return False, ERR["E-01"]
        return False, f"{ERR['E-07']} ({msg})"
    except Exception as e:
        return False, f"{ERR['E-07']} ({e})"

# ── 사이드바 ──────────────────────────────────────────────────────────────
st.sidebar.title("🏛️ 나라장터 낙찰 도우미")
st.sidebar.markdown("---")

# 다른 탭에서 이동 요청이 있으면 radio 렌더링 전에 미리 설정
if "nav_to" in st.session_state:
    st.session_state["page"] = st.session_state.pop("nav_to")

page = st.sidebar.radio(
    "메뉴",
    ["🔍 입찰공고 검색", "💰 낙찰 예상가 계산기", "📊 낙찰 통계 분석", "📁 내 입찰 기록"],
    label_visibility="collapsed",
    key="page",
)
st.sidebar.markdown("---")

# API 상태 표시
if "api_ok" not in st.session_state:
    with st.sidebar:
        with st.spinner("API 연결 확인 중..."):
            ok, err = check_api_status()
            st.session_state["api_ok"] = ok
            st.session_state["api_err"] = err

if st.session_state.get("api_ok"):
    st.sidebar.success("✅ API 연결됨")
else:
    st.sidebar.warning("⚠️ API 미연결 (데모 모드)")
    _err = st.session_state.get("api_err", "")
    if _err:
        st.sidebar.caption(f"오류: {_err}")
    else:
        st.sidebar.caption("data.go.kr 마이페이지에서\nAPI 승인 상태를 확인하세요.")
    if st.sidebar.button("🔄 API 재연결"):
        ok, err = check_api_status()
        st.session_state["api_ok"] = ok
        st.session_state["api_err"] = err
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption("조달청 Open API 기반\n데이터 기준: 실시간")


@st.dialog("📖 용어 설명 — 입력부터 결과까지", width="large")
def show_result_help():
    st.markdown("## 📥 입력 항목")
    st.markdown("""
### 🏗️ 공사 종류
용역 / 물품 / 공사 중 해당하는 유형을 선택합니다.
낙찰하한율 기본값이 달라집니다 (용역 88% / 물품 80% / 공사 87.745%).

---
### 💰 기초금액
발주기관이 공고문에 명시한 **예산 기준 금액**입니다.
나라장터는 이 금액을 기준으로 예비가격 후보를 생성합니다.
> 공고에서 직접 확인하거나, 추정금액 × 1.1(VAT 포함)로 근사합니다.

---
### 📉 낙찰하한율
투찰가가 예정가격의 몇 % 이상이어야 유효한지를 나타냅니다.
- 용역: **88%** / 물품: **80%** / 공사: **87.745%** (기본값)
- 공고마다 다를 수 있으므로 공고문에서 반드시 확인하세요.

---
### 🔢 A값 (순공사원가)
공사 입찰 일부에서 낙찰하한금액 산식이 달라집니다.
- **A값 없음 (기본):** 낙찰하한금액 = 예정가격 × 낙찰하한율
- **A값 있음:** 낙찰하한금액 = (예정가격 − A값) × 낙찰하한율 + A값

A값은 API로 자동 불러오기가 불가합니다. 공고 첨부 원문에서 직접 확인 후 입력하세요.

---
### 📊 예비가격 범위 (발주처 유형)
나라장터가 예비가격 후보를 생성하는 **기초금액 대비 범위**입니다.
발주처마다 다르게 운용됩니다.

| 발주처 유형 | 범위 |
|---|---|
| 국가기관·조달청 | −2% ~ +2% |
| 지자체·교육청 | −3% ~ +3% |
| 수자원공사·철도공단·가스공사 | −2.5% ~ +2.5% |
| 공기업 LH·도로공사·한전 | −2% ~ +2% |
| 방위사업청·군 시설 | 공고마다 다름 (직접 입력) |

공고번호로 불러오면 기관명 기반으로 자동 설정됩니다.

---
### 🎲 복수예가 추첨수
예비가격 후보 15개 중 **몇 개를 추첨해 평균 낼지**입니다.
나라장터 표준은 **4개**이며, 공고마다 다를 수 있습니다.
추첨수가 많을수록 예정가격이 기초금액 평균에 수렴합니다.

---
### 🧠 심리 가중치 반영
입찰자들이 예비가격 번호를 고를 때 **중간 번호(7~9번)를 선호**하는 경향을 반영합니다.
- 켜면: 7~9번 후보가 추첨될 확률이 높아져 예정가격이 기초금액 중심부 쪽으로 약간 이동
- 꺼면: 1~15번 모두 동등한 확률 (순수 랜덤)

실제 나라장터는 참가자들이 제출한 번호 중 추첨하므로 이 편향이 현실적으로 존재합니다.

---
### 👥 예상 경쟁사 수
경쟁이 많을수록 최적 투찰가가 낮게, 적을수록 높게 산출됩니다.
- **0 입력 시:** 과거 유사 입찰의 참가업체수 중앙값을 자동 사용합니다.
""")

    st.markdown("---")
    st.markdown("## 📤 결과 항목")
    st.markdown("""
### 🎯 단일 최적 투찰가
시뮬레이션 + 과거 낙찰 통계(사정률/낙찰률) + 경쟁사 수를 종합한 **최종 추천 투찰가 1개**입니다.

**산출 순서:**
1. 과거 사정률 중앙값 → 발주처 성향 기준점 설정
2. 과거 낙찰률 밴드 → 기준점 중심 유동 범위 확보
3. 안전구간(90%)으로 절대 방어선 적용
4. 경쟁사 수로 범위 내 포지션 결정

---
### 📐 예정가격
나라장터가 개찰 당일 **기초금액 기준 예비가격 후보 15개 중 4개를 추첨**해 평균 낸 금액입니다.
소수점은 버림(절사)합니다.
> **투찰가 ≤ 예정가격** 이어야 유효합니다.

---
### 📊 낙찰하한금액
**투찰가의 최저 한도**입니다. 이 금액 미만으로 투찰하면 무효 처리됩니다.
- 기본 산식: 예정가격 × 낙찰하한율 (소수점 올림)
- A값 적용 산식: (예정가격 − A값) × 낙찰하한율 + A값 (소수점 올림)

예정가격이 추첨으로 결정되므로 낙찰하한금액도 범위로 표시됩니다.

---
### 🔒 안전구간 (유효 확률 100%)
어떤 예정가격이 나와도 **반드시 유효한 구간**입니다.
- **하한** = 예정가격 최댓값 × 낙찰하한율 → 어떤 경우에도 하한 미달 없음
- **상한** = 예정가격 최솟값 → 어떤 경우에도 예정가 초과 없음

이 구간 안에 투찰하면 무효될 일이 없습니다.

---
### ⭐ 경쟁력 투찰 구간
안전구간 중 **하단 40% 범위**입니다.
복수예가 방식은 유효 입찰 중 **최저가가 낙찰**되므로, 이 구간 하단에 가깝게 투찰할수록 경쟁력이 높아집니다.

---
### 📈 유효 확률
10,000회 시뮬레이션 중 해당 투찰가가 **낙찰하한 이상 & 예정가 이하**인 경우의 비율입니다.
- 100%: 어떤 상황에서도 무효 없음 (절대 안전)
- 80% 이상: 안정적
- 50% 미만: 하한 미달 위험 높음

---
### 📏 사정률
**투찰가 ÷ 기초금액 × 100(%)**입니다.
예) 사정률 88% = 기초금액의 88%로 투찰.

결과 화면의 '사정률'은 해당 투찰가를 기초금액 기준으로 나타낸 것이고,
통계 분석의 '사정률'은 과거 낙찰금액 ÷ 기초금액으로 **발주처별 낙찰 경향**을 보여줍니다.

---
### 📉 낙찰률
**낙찰금액 ÷ 예정가격 × 100(%)**입니다.
과거 낙찰 데이터에서 경쟁자들이 예정가격의 몇 %에서 낙찰됐는지를 나타냅니다.

> **사정률 vs 낙찰률:** 사정률은 발주처 성향(기초금액 기준), 낙찰률은 경쟁자 성향(예정가격 기준)입니다. 이 계산기는 두 가지를 모두 반영합니다.

---
### 🎯 가격 선택 핵심 지표 (과거 낙찰 데이터 기반)

#### 최빈 사정률 구간
과거 유사 공고에서 낙찰자들이 **가장 많이 몰린 사정률 범위(0.1% 단위)**입니다.
예) 88.1~88.2% (전체의 15.3%) → 과거 낙찰자 7명 중 1명은 이 구간에서 투찰했다는 의미입니다.
이 구간이 **발주처·업종별 실질 낙찰 밀집 구간**으로, 투찰 가격의 핵심 기준점이 됩니다.

#### 사정률 중앙값
과거 낙찰자들의 사정률을 크기 순으로 나열했을 때 **정중앙 값**입니다.
평균보다 극단값(이상치) 영향을 덜 받아 더 안정적인 기준입니다.
→ 옆에 표시되는 금액은 **현재 기초금액 × 사정률 중앙값**으로 산출한 추천 투찰가입니다.

#### 과거 1등 유효율 최빈 구간
과거 낙찰자들의 투찰가(사정률 기반)를 **현재 시뮬레이션에 대입**해 계산한 유효 확률의 최빈 구간입니다.
예) 65~70% (전체의 42%) → 과거 낙찰자 중 42%는 투찰 당시 유효율이 65~70% 수준이었다는 의미입니다.

**활용법:** 이 구간의 유효율로 투찰가를 맞추면 "과거 낙찰자들과 비슷한 공격성"으로 입찰하는 것입니다.
- 유효율이 **높을수록 안전**하지만 경쟁자보다 가격이 높아 낙찰 확률이 낮아집니다.
- 유효율이 **낮을수록 공격적**이지만 하한 미달 위험이 커집니다.

#### 과거 1등 유효율 평균
위 유효율들의 평균입니다. 개별 구간보다 전체적인 경향을 파악하는 데 사용합니다.

---
### 🎲 예비가격 번호별 선택 빈도
10,000회 시뮬레이션에서 **1~15번(또는 공고 설정 수) 각 예비가격 번호가 최종 추첨에 뽑힌 횟수**입니다.
많이 뽑힌 번호일수록 그 구간 가격이 예정가격 평균에 자주 포함됩니다.
심리 가중치를 켜면 중간 번호(7~9번)의 선택 빈도가 올라가는 것을 확인할 수 있습니다.

---
### 🔬 1원 단위 투찰가 정밀 튜닝
최적 투찰가를 기준으로 ±5원 범위의 유효 확률을 비교합니다.
투찰 직전 최종 금액을 1원 단위로 조정할 때 사용합니다.
""")
    if st.button("닫기", use_container_width=True):
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════
# PAGE 1: 낙찰 예상가 계산기
# ══════════════════════════════════════════════════════════════════════════
if page == "💰 낙찰 예상가 계산기":
    st.title("💰 낙찰 예상가 계산기")
    st.caption("기초금액 기반 복수예가 시뮬레이션 및 낙찰하한율 적용")

    # ── 공고번호 검색 ──────────────────────────────
    with st.expander("🔎 공고 자동 불러오기", expanded=True):
        st.info("💡 **🔍 입찰공고 검색** 탭에서 공고를 찾은 뒤 **'이 공고로 계산기 적용'** 버튼을 누르면 자동으로 아래에 채워집니다.")

        c_no, c_btn = st.columns([3, 1])
        with c_no:
            bid_no_input = st.text_input(
                "이전에 적용한 공고번호 재불러오기", placeholder="예: R26BK01409831",
                label_visibility="collapsed",
            )
        with c_btn:
            search_btn = st.button("재불러오기", use_container_width=True, type="secondary")

        if search_btn and bid_no_input.strip():
            bid_no_key = bid_no_input.strip()
            info = cache_get_bid(bid_no_key)
            if not info:
                st.warning("⚠️ 저장된 기록이 없습니다. 🔍 입찰공고 검색 탭에서 공고를 찾아 먼저 '계산기 적용'을 해주세요.")
                st.stop()

            # 불러오는 즉시 계산기에 적용
            st.session_state["apply_bid"] = info
            st.session_state["loaded_bid"] = info
            st.rerun()

        # 불러온 공고 정보 표시
        if "loaded_bid" in st.session_state:
            info = st.session_state["loaded_bid"]
            ic1, ic2, ic3 = st.columns(3)
            ic1.markdown(f"**공고명**  \n{info['공고명']}")
            ic2.markdown(f"**공고기관**  \n{info['공고기관']}")
            ic3.markdown(f"**공사종류**  \n{info['공사종류']}")

            ia1, ia2, ia3 = st.columns(3)
            _base_disp = info.get('기초금액')
            ia1.markdown(f"**기초금액**  \n{format_won_exact(int(_base_disp))} ({format_won(int(_base_disp))})" if _base_disp else "**기초금액**  \n❌ 미제공 (직접 입력 필요)")
            ia2.markdown(f"**낙찰하한율**  \n{info['낙찰하한율']}%" if info['낙찰하한율'] else "**낙찰하한율**  \n미제공")
            ia3.markdown(f"**개찰일시**  \n{info['개찰일시'] or '-'}")
            if not _base_disp:
                st.warning("⚠️ API에서 기초금액을 가져오지 못했습니다. 아래 기초금액란에 공고문 기재 금액을 직접 입력하세요.")
            else:
                st.success("✅ 아래 계산기에 값이 자동 적용됐습니다.")

    # 공고 자동 적용 값 읽기
    _apply = st.session_state.get("apply_bid", {})
    _wkey = _apply.get("공고번호", "default")  # 공고 변경 시 위젯 재초기화용 key

    # ── 직전 낙찰 사례 카드 (컬럼 위쪽 전체 너비) — 미노출 (나중에 다시 사용) ──
    # _cards_top = None
    # if "last_result" in st.session_state:
    #     _cards_top = st.session_state["last_result"].get("recent5")
    # elif "preloaded_cards" in st.session_state:
    #     _cards_top = st.session_state["preloaded_cards"]
    # if _cards_top:
    #     render_cards(_cards_top, title="#### 📋 유사 낙찰 사례 (동일 업종·지역)")
    #     st.markdown("---")

    col_input, col_result = st.columns([1, 2], gap="large")

    with col_input:
        st.subheader("입력")
        TYPE_OPTIONS = ["용역", "물품", "공사"]
        _default_type_idx = TYPE_OPTIONS.index(_apply["공사종류"]) if _apply.get("공사종류") in TYPE_OPTIONS else 0
        bid_type = st.selectbox("공사 종류", TYPE_OPTIONS, index=_default_type_idx, key=f"bid_type_{_wkey}")

        # 낙찰하한율 — 공고 자동적용 또는 공사종류 기본값
        DEFAULT_RATES = {"용역": 88.0, "물품": 80.0, "공사": 87.745}
        default_rate = DEFAULT_RATES[bid_type]
        _rate_from_bid = _apply.get("낙찰하한율") if _apply.get("낙찰하한율") else default_rate
        lower_rate_input = st.number_input(
            "낙찰하한율 (%)",
            min_value=50.0, max_value=100.0,
            value=float(_rate_from_bid),
            step=0.001,
            format="%.3f",
            help="공사 87.745% / 용역 88% / 물품 80% 기본값. 공고별로 다를 수 있으니 직접 수정하세요.",
            key=f"lower_rate_{_wkey}",
        )
        if lower_rate_input != default_rate:
            st.caption(f"※ 기본값({default_rate}%)에서 변경됨")

        # 기초금액 — 공고 자동적용 또는 직접 입력
        _base_raw = _apply.get("기초금액")
        _base_from_bid = int(_base_raw) if _base_raw and int(_base_raw) >= 100000 else None
        input_method = st.radio("금액 입력 방식", ["직접 입력 (원)", "억원 단위"], key=f"input_method_{_wkey}")
        if input_method == "직접 입력 (원)":
            base_price_input = st.number_input(
                "기초금액 (원)", min_value=100000,
                value=_base_from_bid if _base_from_bid else 100_000_000,
                step=1_000_000, format="%d",
                key=f"base_price_{_wkey}",
            )
        else:
            _default_eok = round(_base_from_bid / 1e8, 2) if _base_from_bid else 1.0
            base_eok = st.number_input("기초금액 (억원)", min_value=0.01, value=_default_eok, step=0.1, format="%.2f", key=f"base_eok_{_wkey}")
            base_price_input = int(base_eok * 1_0000_0000)
            st.info(f"= {base_price_input:,}원")

        st.markdown("---")

        # A값 (순공사원가) — 해당 공고에 적용되는 경우에만 입력
        _a_default = int(_apply.get("A값") or 0)
        a_value_input = st.number_input(
            "A값 (순공사원가 등, 원) — 해당 없으면 0",
            min_value=0, value=_a_default, step=1_000_000, format="%d",
            help="A값 적용 공고는 낙찰하한금액 산식이 달라집니다.\n"
                 "적용 산식: (예정가격 - A값) × 낙찰하한율 + A값\n"
                 "공고문에 순공사원가·국민연금·건강보험료·퇴직공제부금비 합산액 명시 시 입력하세요.",
            key=f"a_value_{_wkey}",
        )
        if a_value_input > 0:
            st.caption(f"A값 적용: 낙찰하한금액 = (예정가 - {format_won_exact(a_value_input)}) × {lower_rate_input}% + {format_won_exact(a_value_input)}")

        # 예비가격 범위 — 발주처 유형별 선택 (공고 불러오면 자동 설정)
        _range_labels = list(PRICE_RANGE_OPTIONS.keys())
        _auto_label = _apply.get("예가범위_라벨", _range_labels[0])
        _default_range_idx = _range_labels.index(_auto_label) if _auto_label in _range_labels else 0
        price_range_label = st.selectbox(
            "예비가격 범위 (발주처 유형)",
            options=_range_labels,
            index=_default_range_idx,
            help="공고문에서 발주처를 확인 후 선택하세요.\n"
                 "공고번호로 불러오면 기관명 기반으로 자동 설정됩니다.\n"
                 "방위사업청·군 시설공사는 공고마다 범위가 다르므로 직접 입력하세요.",
            key=f"price_range_{_wkey}",
        )
        if _apply.get("예가범위_라벨") and _apply["예가범위_라벨"] != "직접 입력 (방위사업청·군 시설 등)":
            st.caption(f"공고기관 '{_apply.get('공고기관', '')}' 기반 자동 설정됨")
        if PRICE_RANGE_OPTIONS[price_range_label] is None:
            _pr_col1, _pr_col2 = st.columns(2)
            _pr_min = _pr_col1.number_input("최소 (%)", value=-2.0, min_value=-10.0, max_value=0.0, step=0.5, key=f"pr_min_{_wkey}")
            _pr_max = _pr_col2.number_input("최대 (%)", value=2.0, min_value=0.0, max_value=10.0, step=0.5, key=f"pr_max_{_wkey}")
            price_range_input = (_pr_min, _pr_max)
        else:
            price_range_input = PRICE_RANGE_OPTIONS[price_range_label]

        # 복수예가 추첨수 — 공고에서 자동 적용, 직접 변경 가능
        _draw_from_bid = int(_apply.get("추첨수") or 4)
        DRAW_OPTIONS = [2, 3, 4, 5]
        _draw_default_idx = DRAW_OPTIONS.index(_draw_from_bid) if _draw_from_bid in DRAW_OPTIONS else 2
        draw_count_input = st.selectbox(
            "복수예가 추첨수",
            options=DRAW_OPTIONS,
            index=_draw_default_idx,
            help="나라장터 표준: 15개 후보 중 4개 추첨. 공고에 따라 다를 수 있습니다.\n"
                 "공고번호로 불러오면 공고의 실제 추첨수가 자동 적용됩니다.",
            key=f"draw_count_{_wkey}",
        )
        if _apply.get("추첨수"):
            st.caption(f"공고 고지 추첨수: {_apply['추첨수']}개 자동 적용됨")

        use_psych_weight = st.checkbox(
            "심리 가중치 반영",
            value=False,
            help="입찰자들은 예비가격 번호를 고를 때 중간(7~9번)을 선호하는 경향이 있습니다.\n"
                 "이 옵션을 켜면 중간 번호 후보가 추첨될 확률을 높여 시뮬레이션합니다.\n"
                 "15개 후보 표준 방식에만 적용됩니다.",
            key=f"psych_{_wkey}",
        )

        st.markdown("---")
        competitor_count = st.number_input(
            "예상 경쟁사 수 (0=과거 데이터 자동 추정)",
            min_value=0, max_value=1000, value=0, step=1,
            help="0이면 같은 기관·유사금액 과거 입찰의 참가업체수 중앙값을 자동 사용합니다.",
            key=f"competitor_count_{_wkey}",
        )
        if "last_result" in st.session_state:
            _ec = st.session_state["last_result"].get("estimated_comp")
            if _ec:
                st.caption(f"📊 과거 유사 입찰 추정: **{_ec['median']}개사** (범위 {_ec['min']}~{_ec['max']}개사, {_ec['sample']}건 기준)")

        calc_btn = st.button("📊 계산하기", use_container_width=True, type="primary")

        st.markdown("---")
        _pr = price_range_input
        st.caption(f"※ 예비가격 범위: {_pr[0]:+g}% ~ {_pr[1]:+g}% | 추첨 {draw_count_input}개 평균(버림) = 예정가격\n"
                   "※ 매 시뮬레이션마다 후보 난수 재생성 | 10,000회 몬테카를로")

    with col_result:
        if calc_btn or "last_result" in st.session_state:
            if calc_btn:
                with st.spinner("시뮬레이션 + 과거 낙찰 데이터 조회 중..."):
                    _loaded_bid = st.session_state.get("apply_bid", {})
                    _cand = int(_loaded_bid.get("후보수") or 15)
                    result = calc_bid_range(
                        base_price_input, bid_type,
                        custom_lower_rate=lower_rate_input,
                        candidate_count=_cand,
                        draw_count=draw_count_input,
                        a_value=float(a_value_input),
                        price_range=price_range_input,
                        use_psychology_weight=use_psych_weight,
                    )

                    # 공고번호 로드된 경우 → 기관·계약방식·키워드·지역·업종 추출
                    loaded = st.session_state.get("apply_bid", {})
                    keyword       = extract_keyword(loaded.get("공고명", "")) if loaded else ""
                    agency        = loaded.get("공고기관", "") if loaded else ""
                    contract_type = loaded.get("계약방식", "") if loaded else ""
                    region        = loaded.get("참가제한지역", "") if loaded else ""
                    industry      = loaded.get("업종", "") if loaded else ""
                    industry_cd   = loaded.get("업종코드", "") if loaded else ""

                    winner_df = pd.DataFrame()
                    _winner_is_demo = False
                    _w_start = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d") + "000000"
                    _w_end   = datetime.now().strftime("%Y%m%d") + "235959"

                    # 단계적 API 호출: 지역+업종 → 지역만 → 전체
                    # prtcptLmtRgnNm / indstrytyNm — stts PPSSrch 엔드포인트에서 실제 작동
                    _MIN_RATES = 30
                    def _valid_rate_count(df):
                        if df.empty or "낙찰률" not in df.columns:
                            return 0
                        return int(df["낙찰률"].dropna().count())

                    _rgn_full = region if region else ""        # 참가제한지역명 전체
                    _ind_cd   = industry_cd if industry_cd else ""  # 업종코드 (indstrytyCd — 실제 작동)

                    # 단계: (지역+업종코드) → (지역만) → (전체)
                    for _rk, _ik in [(_rgn_full, _ind_cd), (_rgn_full, ""), ("", "")]:
                        try:
                            _df = api.get_winner_list(
                                bid_type=bid_type,
                                start_date=_w_start, end_date=_w_end, rows=500,
                                prtcpt_lmt_rgn_nm=_rk,
                                indstryty_cd=_ik,
                            )
                            if _valid_rate_count(_df) >= _MIN_RATES:
                                winner_df = _df
                                break
                        except Exception:
                            continue

                    # 여전히 부족하면 데모 폴백
                    if _valid_rate_count(winner_df) < 5:
                        winner_df = get_demo_winner_list(bid_type=bid_type, rows=200)
                        _winner_is_demo = True

                    # 단계적 필터링 (통계용 — 금액대 포함)
                    winner_df, filter_desc = tiered_filter(
                        winner_df,
                        base_price=base_price_input,
                        agency=agency,
                        contract_type=contract_type,
                        region=region,
                        industry=industry,
                        industry_cd=industry_cd,
                    )

                    result["stats_keyword"]    = keyword
                    result["stats_filter_desc"] = filter_desc
                    result["stats_is_demo"]    = _winner_is_demo
                    result["stats"] = recommend_from_stats(winner_df, result["expected_price_mean"], base_price=base_price_input)

                    # 직전 낙찰 사례 카드 — 검색 페이지에서 미리 로드된 경우 재사용
                    if "preloaded_cards" in st.session_state:
                        result["recent5"] = st.session_state.pop("preloaded_cards")
                    else:
                        result["recent5"] = build_recent_cards(
                            bid_type=bid_type,
                            region=region,
                            industry_cd=industry_cd if industry_cd else "",
                            base_price=base_price_input,
                            contract_type=contract_type,
                        )

                    # 과거 데이터 기반 경쟁사 수 추정
                    result["estimated_comp"] = estimate_competitor_count(winner_df)
                st.session_state["last_result"] = result
            else:
                result = st.session_state["last_result"]

            r     = result
            stats = r.get("stats")

            # 시뮬레이션 분포 & 낙찰하한 배열 — 이후 여러 계산에서 공용
            _sim_dist = np.array(r["distribution"])
            _sim_lr   = r["lower_rate_pct"] / 100
            _sim_av   = r.get("a_value", 0.0)
            if _sim_av > 0:
                _sim_floors = np.ceil(np.round((_sim_dist - _sim_av) * _sim_lr + _sim_av, 5))
            else:
                _sim_floors = np.ceil(np.round(_sim_dist * _sim_lr, 5))

            # ── 단일 최적 투찰가 (경쟁률 반영) ─────────────────────────
            est_comp = r.get("estimated_comp")
            # 경쟁사 수: 직접 입력 우선, 0이면 과거 데이터 추정값 자동 사용
            effective_comp = int(competitor_count) if int(competitor_count) > 0 else (
                est_comp["median"] if est_comp else 0
            )
            optimal = calc_optimal_bid(r, stats, effective_comp)
            if est_comp:
                comp_hint = f" | 과거 유사 입찰 {est_comp['sample']}건 기준 추정 (중앙값 {est_comp['median']}개사, 범위 {est_comp['min']}~{est_comp['max']}개사)"
            else:
                comp_hint = ""

            st.markdown(f"""
<div style="background:linear-gradient(135deg,#6c3483,#a93226);color:white;
border-radius:16px;padding:24px 28px;margin-bottom:16px;text-align:center;">
  <div style="font-size:14px;opacity:.85;margin-bottom:6px">
    🎯 단일 최적 투찰가 &nbsp;—&nbsp; {optimal['comp_label']}{comp_hint}
  </div>
  <div style="font-size:36px;font-weight:900;letter-spacing:-1px;margin-bottom:4px">
    {format_won_exact(optimal['optimal_bid'])}
  </div>
  <div style="font-size:15px;opacity:.75;margin-bottom:8px">
    ({format_won(optimal['optimal_bid'])})
  </div>
  <div style="font-size:13px;opacity:.8">
    사정률 {optimal['sajeong']:.3f}%
    &nbsp;|&nbsp; 유효 확률 {optimal['valid_prob']:.1f}%
    &nbsp;|&nbsp; 산출 범위 {format_won_exact(optimal['opt_low'])} ~ {format_won_exact(optimal['opt_high'])}
  </div>
</div>""", unsafe_allow_html=True)

            _h1, _h2 = st.columns([4, 1])
            _h1.subheader("분석 결과")
            if _h2.button("📖 용어 설명", use_container_width=True):
                show_result_help()

            # ── 상단 요약 지표 ──────────────────────────────────────────
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("기초금액",       format_won_exact(r["base_price"]))
            c2.metric("예정가격 (평균)", format_won_exact(r["expected_price_mean"]),
                      f"사정률 {r['expected_price_mean']/r['base_price']*100:.2f}%")
            c3.metric("낙찰하한금액 (평균)", format_won_exact(r["award_floor_mean"]),
                      f"하한율 {r['lower_rate_pct']}%")
            c4.metric("안전구간 크기",
                      format_won_exact(r["safe_high"] - r["safe_low"]) if r["safe_exists"] else "구간 없음",
                      f"{r['sajeong_safe_low']:.2f}% ~ {r['sajeong_safe_high']:.2f}%" if r["safe_exists"] else "")

            st.markdown("---")

            # ── 안전구간 (핵심) ─────────────────────────────────────────
            if r["safe_exists"]:
                st.markdown(f"""
<div style="background:linear-gradient(135deg,#1a5276,#2ecc71);color:white;
border-radius:14px;padding:22px 28px;margin-bottom:12px;">
  <div style="font-size:13px;opacity:.85;margin-bottom:6px">
    🔒 안전구간 — 어떤 예정가격이 나와도 투찰 유효 (유효 확률 100%)
  </div>
  <div style="font-size:22px;font-weight:800;margin-bottom:2px">
    {format_won_exact(r['safe_low'])} ~ {format_won_exact(r['safe_high'])}
  </div>
  <div style="font-size:13px;opacity:.75;margin-bottom:4px">
    ({format_won(r['safe_low'])} ~ {format_won(r['safe_high'])})
  </div>
  <div style="font-size:13px;opacity:.8">
    사정률 {r['sajeong_safe_low']:.3f}% ~ {r['sajeong_safe_high']:.3f}%
    &nbsp;|&nbsp; 이 구간 하단에 가깝게 투찰할수록 최저가 경쟁력 ↑
  </div>
</div>""", unsafe_allow_html=True)
            else:
                st.warning("⚠️ 안전구간 없음 — 낙찰하한금액 최댓값이 예정가격 최솟값을 초과합니다. 90% 구간 기준으로 대체합니다.")

            # ── 90% 안전구간 ────────────────────────────────────────────
            st.markdown(f"""
<div style="background:linear-gradient(135deg,#1f3c88,#3498db);color:white;
border-radius:14px;padding:22px 28px;margin-bottom:12px;">
  <div style="font-size:13px;opacity:.85;margin-bottom:6px">
    ⭐ 경쟁력 투찰 구간 — 안전구간 하단 (낮을수록 경쟁력 ↑){' + 과거 통계 반영' if stats else ''}
  </div>
  <div style="font-size:22px;font-weight:800;margin-bottom:2px">
    {format_won_exact(r['safe_low_p90'])} ~ {format_won_exact(r['safe_high_p90'])}
  </div>
  <div style="font-size:13px;opacity:.75;margin-bottom:4px">
    ({format_won(r['safe_low_p90'])} ~ {format_won(r['safe_high_p90'])})
  </div>
  <div style="font-size:13px;opacity:.8">
    사정률 {r['safe_low_p90']/r['base_price']*100:.3f}% ~ {r['safe_high_p90']/r['base_price']*100:.3f}%
    &nbsp;|&nbsp; 유효확률 100% 구간의 하단 40% — 최저가 경쟁 유리
  </div>
</div>""", unsafe_allow_html=True)

            # ── 과거 통계 반영 추천가 ────────────────────────────────────
            if r.get("stats_is_demo"):
                st.warning("⚠️ 낙찰결과 API 데이터를 가져오지 못해 **데모 데이터** 기반 통계를 표시합니다. 참고용으로만 활용하세요.")
            if stats:
                filter_desc   = r.get("stats_filter_desc", "")
                # 안전구간과 과거통계 추천가의 교집합
                stat_low  = max(stats["recommend_low"],  r["safe_low_p90"])
                stat_high = min(stats["recommend_high"], r["safe_high_p90"])
                if stat_low < stat_high:
                    best_label = "안전구간 ∩ 과거통계 교집합"
                    best_low, best_high = stat_low, stat_high
                else:
                    best_label = "과거통계 기준 (안전구간 밖)"
                    best_low, best_high = stats["recommend_low"], stats["recommend_high"]

                st.markdown(f"""
<div style="background:#f8f9fa;border-left:5px solid #f39c12;
border-radius:8px;padding:16px 20px;margin-bottom:12px;">
  <div style="font-size:13px;color:#666;margin-bottom:4px">
    📊 {best_label} &nbsp;—&nbsp; {filter_desc}
  </div>
  <div style="font-size:22px;font-weight:700;color:#1f3c88">
    {format_won_exact(best_low)} ~ {format_won_exact(best_high)}
  </div>
  <div style="font-size:12px;color:#888;margin-top:4px">
    최빈 낙찰률 {stats['mode_range'][0]}~{stats['mode_range'][1]}%
    &nbsp;|&nbsp; 이 구간 낙찰 비율 {stats['mode_pct']}%
    &nbsp;|&nbsp; 낙찰률 평균 {stats['mean_rate']:.3f}% / 중앙 {stats['median_rate']:.3f}%
  </div>
</div>""", unsafe_allow_html=True)

                sc1, sc2, sc3, sc4 = st.columns(4)
                sc1.metric("낙찰률 평균",   f"{stats['mean_rate']:.3f}%")
                sc2.metric("낙찰률 중앙값", f"{stats['median_rate']:.3f}%")
                sc3.metric("표준편차",      f"{stats['std_rate']:.3f}%p")
                sc4.metric("25~75% 구간",  f"{stats['rate_p25']:.2f}~{stats['rate_p75']:.2f}%")

                with st.expander("📖 이 통계, 어떻게 활용하나요?"):
                    _sj_rec_amt = int(stats['sajeong_median'] / 100 * r['base_price']) if stats.get('sajeong_median') else None
                    st.markdown(f"""
**📊 위 4개 지표 — 과거 낙찰자들이 예정가격의 몇 %로 투찰했는지**
| 지표 | 의미 | 활용법 |
|---|---|---|
| 낙찰률 평균/중앙값 | 투찰가 ÷ 예정가격 × 100 | 이 % × 현재 예정가 추정치 = 참고 투찰가 |
| 표준편차 | 낙찰률 분산도 | 클수록 낙찰가 예측 어려움 → 보수적 투찰 권장 |
| 25~75% 구간 | 낙찰자 절반이 몰린 구간 | 이 구간 진입 시 중간 경쟁력 확보 |

**🎯 아래 핵심 지표 — 기초금액 대비 실제 낙찰 경향 (사정률)**
| 지표 | 의미 | 활용법 |
|---|---|---|
| 최빈 사정률 구간 | 낙찰자들이 **가장 많이 몰린** 기초금액 대비 % | 경쟁이 가장 치열한 구간. 낙찰 확률 최대화 시 진입 |
| 사정률 중앙값 → 금액 | 안정적인 낙찰 기준가 | **바로 옆 금액이 추천 투찰가** |
| 과거 1등 유효율 최빈 | 과거 낙찰자들이 가졌던 유효 확률 구간 | 이 유효율로 투찰가를 맞추면 과거 낙찰자와 같은 공격성 |
| 과거 1등 유효율 평균 | 낙찰자들의 평균 유효 확률 | 이보다 **낮게** 투찰 = 공격적, **높게** = 안전 |

**💡 의사결정 요약**
- **안전 우선** → 사정률 중앙값 금액({format_won_exact(_sj_rec_amt) if _sj_rec_amt else '-'})으로 투찰
- **경쟁 우선** → 최빈 사정률 구간 **하단**으로 낮춰 투찰
- **유효율이 낮을수록** 공격적(하한 미달 위험↑), **높을수록** 안전(낙찰 경쟁력↓)
""")

                # ── 가격 선택 핵심 지표 ──────────────────────────────────
                if stats.get("sajeong_mean"):
                    # 과거 낙찰가 기준 유효율 계산
                    _sj_vals  = np.array(stats["sajeong_distribution"])
                    _past_bids = r["base_price"] * _sj_vals / 100
                    _past_vps  = np.array([
                        float(((b >= _sim_floors) & (b <= _sim_dist)).mean() * 100)
                        for b in _past_bids
                    ])
                    _mean_vp   = float(_past_vps.mean())
                    _bins_vp   = np.arange(0, 105, 5)
                    _hist_vp, _edges_vp = np.histogram(_past_vps, bins=_bins_vp)
                    _mi        = int(_hist_vp.argmax())
                    _mode_vp_lo = int(_edges_vp[_mi])
                    _mode_vp_hi = int(_edges_vp[_mi + 1])
                    _mode_vp_pct = round(float(_hist_vp[_mi] / len(_past_vps) * 100), 1)

                    _sj_mode = stats.get("sajeong_mode_range", (0, 0))
                    _sj_mode_pct = stats.get("sajeong_mode_pct", 0)
                    _sj_rec  = stats.get("sajeong_recommend")

                    st.markdown(f"""
<div style="background:linear-gradient(135deg,#0d3349,#145a7c);color:white;
border-radius:12px;padding:18px 22px;margin-top:10px;">
  <div style="font-size:13px;opacity:.8;margin-bottom:10px">
    🎯 가격 선택 핵심 지표 &nbsp;—&nbsp; {filter_desc}
  </div>
  <div style="display:flex;gap:28px;flex-wrap:wrap;margin-bottom:12px;">
    <div>
      <div style="font-size:11px;opacity:.7;margin-bottom:2px">최빈 사정률 구간</div>
      <div style="font-size:22px;font-weight:800;color:#f1c40f">{_sj_mode[0]}~{_sj_mode[1]}%</div>
      <div style="font-size:11px;opacity:.65">전체의 {_sj_mode_pct}%가 여기서 낙찰</div>
    </div>
    <div>
      <div style="font-size:11px;opacity:.7;margin-bottom:2px">사정률 중앙값</div>
      <div style="font-size:22px;font-weight:800;color:#2ecc71">{stats['sajeong_median']:.3f}%</div>
      <div style="font-size:11px;opacity:.65">{("→ " + format_won_exact(int(_sj_rec))) if _sj_rec else "기초금액 기준"}</div>
    </div>
    <div>
      <div style="font-size:11px;opacity:.7;margin-bottom:2px">과거 1등 유효율 최빈</div>
      <div style="font-size:22px;font-weight:800;color:#3498db">{_mode_vp_lo}~{_mode_vp_hi}%</div>
      <div style="font-size:11px;opacity:.65">전체의 {_mode_vp_pct}%가 이 유효율 구간</div>
    </div>
    <div>
      <div style="font-size:11px;opacity:.7;margin-bottom:2px">과거 1등 유효율 평균</div>
      <div style="font-size:22px;font-weight:800;color:#e74c3c">{_mean_vp:.1f}%</div>
      <div style="font-size:11px;opacity:.65">사정률 평균 {stats['sajeong_mean']:.3f}%</div>
    </div>
  </div>
  <div style="font-size:12px;opacity:.75;border-top:1px solid rgba(255,255,255,.2);padding-top:8px">
    💡 과거 낙찰자들은 기초금액의 <b>{_sj_mode[0]}~{_sj_mode[1]}%</b> 수준에서 가장 많이 낙찰됐으며,
    해당 투찰가의 유효율은 평균 <b>{_mean_vp:.1f}%</b>였습니다.
    유효율이 높을수록 안전하지만 낙찰 경쟁력은 낮아집니다.
  </div>
</div>""", unsafe_allow_html=True)


            # ── 통합 시각화 차트 ─────────────────────────────────────────
            tab_chart1, tab_chart2 = st.tabs(["투찰가별 유효 확률", "예정가격 분포"])

            with tab_chart1:
                dist_arr = _sim_dist
                lr = _sim_lr
                # x축: 기초금액의 80%~105% 구간
                x_range = np.linspace(r["base_price"] * 0.80, r["base_price"] * 1.05, 300)
                probs = [float(((x >= dist_arr * lr) & (x <= dist_arr)).mean() * 100)
                         for x in x_range]
                fig_p = go.Figure()
                fig_p.add_trace(go.Scatter(
                    x=x_range / 1e8, y=probs,
                    mode="lines", line=dict(color="#3498db", width=2.5),
                    fill="tozeroy", fillcolor="rgba(52,152,219,0.1)",
                    name="유효 확률",
                ))
                # 안전구간 표시
                if r["safe_exists"]:
                    fig_p.add_vrect(
                        x0=r["safe_low"] / 1e8, x1=r["safe_high"] / 1e8,
                        fillcolor="rgba(46,204,113,0.2)", line_color="#2ecc71",
                        annotation_text="안전구간 (100%)", annotation_position="top left",
                    )
                # 90% 구간 표시
                fig_p.add_vrect(
                    x0=r["safe_low_p90"] / 1e8, x1=r["safe_high_p90"] / 1e8,
                    fillcolor="rgba(52,152,219,0.15)", line_color="#3498db",
                    line_dash="dash",
                    annotation_text="최적구간 (90%)", annotation_position="top right",
                )
                fig_p.add_hline(y=100, line_dash="dot", line_color="#2ecc71", opacity=0.5)
                fig_p.update_layout(
                    xaxis_title="투찰가 (억원)", yaxis_title="유효 확률 (%)",
                    yaxis=dict(range=[0, 110]),
                    height=320, margin=dict(l=0, r=0, t=20, b=0), showlegend=False,
                )
                st.plotly_chart(fig_p, use_container_width=True)
                st.caption("투찰가를 x축에서 고르면 해당 금액이 유효(낙찰 자격)할 확률을 보여줍니다.")

            with tab_chart2:
                fig2 = go.Figure()
                fig2.add_trace(go.Histogram(
                    x=dist_arr / 1e8, nbinsx=50,
                    marker_color="#3498db", opacity=0.7, name="예정가격",
                ))
                fig2.add_vrect(
                    x0=r["safe_low"] / 1e8, x1=r["safe_high"] / 1e8,
                    fillcolor="rgba(46,204,113,0.25)", line_color="#2ecc71",
                    annotation_text="안전구간", annotation_position="top left",
                )
                if stats and stats.get("rate_distribution"):
                    rate_arr = np.array(stats["rate_distribution"])
                    fig2.add_vrect(
                        x0=stats["mode_range"][0] * r["expected_price_mean"] / 100 / 1e8,
                        x1=stats["mode_range"][1] * r["expected_price_mean"] / 100 / 1e8,
                        fillcolor="rgba(241,196,15,0.2)", line_color="#f39c12",
                        annotation_text="과거 최빈", annotation_position="top right",
                    )
                fig2.add_vline(
                    x=r["expected_price_mean"] / 1e8,
                    line_dash="dash", line_color="#1f3c88",
                    annotation_text=f"예정가 평균 {format_won(r['expected_price_mean'])}",
                )
                fig2.update_layout(
                    xaxis_title="금액 (억원)", yaxis_title="빈도",
                    height=320, margin=dict(l=0, r=0, t=20, b=0), showlegend=False,
                )
                st.plotly_chart(fig2, use_container_width=True)

            # ── 상세 수치 ────────────────────────────────────────────────
            with st.expander("📋 상세 수치 보기"):
                rows_data = {
                    "구분": [
                        "기초금액",
                        "예정가격 최솟값 (P10)",
                        "예정가격 평균",
                        "예정가격 최댓값 (P90)",
                        "낙찰하한금액 평균",
                        "낙찰하한금액 최댓값",
                        "── 안전구간 하한 (= 하한금액 최댓값)",
                        "── 안전구간 중간",
                        "── 안전구간 상한 (= 예정가 최솟값)",
                        "최적 투찰가 하단 (90%)",
                        "최적 투찰가 상단 (90%)",
                    ],
                    "금액": [
                        r["base_price"],
                        r["expected_price_p10"], r["expected_price_mean"], r["expected_price_p90"],
                        r["award_floor_mean"], r["award_floor_max"],
                        r["safe_low"], r["safe_mid"], r["safe_high"],
                        r["safe_low_p90"], r["safe_high_p90"],
                    ],
                }
                detail_df = pd.DataFrame(rows_data)
                detail_df["금액(표시)"] = detail_df["금액"].apply(format_won_exact)
                detail_df["금액(원)"]   = detail_df["금액"].apply(lambda x: f"{x:,.0f}")
                detail_df["사정률(%)"]  = detail_df["금액"].apply(
                    lambda x: f"{x / r['base_price'] * 100:.3f}%"
                )
                st.dataframe(
                    detail_df[["구분", "금액(표시)", "금액(원)", "사정률(%)"]],
                    use_container_width=True, hide_index=True,
                )

            # ── 1원 단위 정밀 검증기 ─────────────────────────────────────
            st.markdown("---")
            st.subheader("🎯 1원 단위 투찰가 정밀 튜닝")
            st.caption("유효 확률 1% 단위 구간으로 바로 이동하거나, 1원 단위로 직접 조정하세요.")

            # 상단에서 정의한 공용 배열 참조
            _dist   = _sim_dist
            _floors = _sim_floors

            def _calc_prob(price: int) -> tuple:
                survived = int(((price >= _floors) & (price <= _dist)).sum())
                return survived, survived / len(_dist) * 100

            # 유효 확률 1% 단위 구간별 대표 금액 미리 계산
            # 각 1% 구간에서 해당 확률 이상이 되는 가장 낮은 금액 탐색
            @st.cache_data(show_spinner=False)
            def _build_prob_table(floors_key: str, dist_key: str, search_low: int, search_high: int):
                prob_to_price = {}
                for pct in range(0, 101):
                    target = pct / 100
                    # 이진 탐색: 유효확률 >= pct% 가 되는 최솟값
                    lo, hi = search_low, search_high
                    found = None
                    while lo <= hi:
                        mid = (lo + hi) // 2
                        s = int(((mid >= _floors) & (mid <= _dist)).sum())
                        if s / len(_dist) >= target:
                            found = mid
                            hi = mid - 1
                        else:
                            lo = mid + 1
                    prob_to_price[pct] = found
                return prob_to_price

            _search_low  = int(_floors.min()) - 100
            _search_high = int(_dist.max()) + 100
            _prob_table  = _build_prob_table(
                str(int(_floors.sum())), str(int(_dist.sum())),
                _search_low, _search_high
            )

            # 세션 상태로 투찰가 관리
            if f"micro_val_{_wkey}" not in st.session_state:
                st.session_state[f"micro_val_{_wkey}"] = int(optimal["optimal_bid"])

            # 유효 확률 셀렉트박스 이동
            _cur_prob = _calc_prob(st.session_state[f"micro_val_{_wkey}"])[1]
            _cur_pct  = int(_cur_prob)

            _sc1, _sc2 = st.columns([1, 3])
            with _sc1:
                _jump_options = list(range(100, 49, -1))
                _default_idx  = _jump_options.index(_cur_pct) if _cur_pct in _jump_options else 0
                _selected_pct = st.selectbox(
                    "유효 확률 구간 바로 이동",
                    _jump_options,
                    index=_default_idx,
                    format_func=lambda x: f"{x}%",
                    key=f"jump_sel_{_wkey}",
                )
            _price_at = _prob_table.get(_selected_pct)
            with _sc2:
                st.write("")
                st.write("")
                if _price_at is not None:
                    if st.button(
                        f"→ {_selected_pct}% 구간으로 이동  ({_price_at:,}원)",
                        key=f"jump_btn_{_wkey}", use_container_width=True, type="secondary",
                    ):
                        st.session_state[f"micro_val_{_wkey}"] = _price_at
                        st.rerun()
                else:
                    st.button(f"→ {_selected_pct}% 구간 없음", disabled=True,
                              key=f"jump_btn_{_wkey}", use_container_width=True)

            c_micro1, c_micro2 = st.columns([1, 2])
            with c_micro1:
                micro_price = st.number_input(
                    "최종 투찰가 입력 (원)",
                    value=st.session_state[f"micro_val_{_wkey}"],
                    step=1, format="%d",
                    key=f"micro_{_wkey}",
                )
                # number_input 직접 수정 시 세션 동기화
                if micro_price != st.session_state[f"micro_val_{_wkey}"]:
                    st.session_state[f"micro_val_{_wkey}"] = micro_price

                survived, win_rate = _calc_prob(micro_price)
                st.metric("유효 확률", f"{win_rate:.2f}%", f"{survived:,} / {len(_dist):,}회")

            with c_micro2:
                if win_rate == 100.0:
                    st.success(f"**{micro_price:,}원** — 어떤 예정가격이 나와도 **100% 무효 없음** (절대 안전)")
                elif win_rate >= 80:
                    st.info(f"**{micro_price:,}원** — {survived:,} / {len(_dist):,}회 유효 ({win_rate:.2f}%)")
                elif win_rate >= 50:
                    st.warning(f"**{micro_price:,}원** — {survived:,} / {len(_dist):,}회 유효 ({win_rate:.2f}%) ⚠️ 약간 위험")
                else:
                    st.error(f"**{micro_price:,}원** — {survived:,} / {len(_dist):,}회만 유효 ({win_rate:.2f}%) 🚨 하한 미달 위험 높음")

                # 유효확률 ±1% 범위 테이블
                _lo_prob = max(win_rate - 1.0, 0.0)
                _hi_prob = min(win_rate + 1.0, 100.0)

                # 아래 경계: win_rate-1% 되는 첫 가격 탐색 (하방)
                _p_low = micro_price
                while _p_low > micro_price - 5000:
                    _, _pr = _calc_prob(_p_low - 1)
                    if _pr < _lo_prob:
                        break
                    _p_low -= 1

                # 위 경계: win_rate+1% 되는 마지막 가격 탐색 (상방)
                _p_high = micro_price
                while _p_high < micro_price + 5000:
                    _, _pr = _calc_prob(_p_high + 1)
                    if _pr > _hi_prob:
                        break
                    _p_high += 1

                _presmpt_med = float(np.median(_dist))
                _av = float(_sim_av) if _sim_av else 0.0
                _denom = _presmpt_med - _av
                _micro_rows = []
                for p in range(_p_low, _p_high + 1):
                    s, prob = _calc_prob(p)
                    _tuchal_adj = (p - _av) / _denom * 100 if _denom > 0 else None
                    _micro_rows.append({
                        "투찰가": f"{p:,}원",
                        "(투찰가-A)/(예정가-A)×100": f"{_tuchal_adj:.3f}" if _tuchal_adj is not None else "-",
                        "유효 횟수": f"{s:,}",
                        "유효 확률": f"{prob:.2f}%",
                    })
                _micro_df = pd.DataFrame(_micro_rows)

                # 현재 입력가 행 강조
                _cur_idx = micro_price - _p_low
                def _highlight_cur(row):
                    if row.name == _cur_idx:
                        return ["background-color:#ffd700;font-weight:bold;color:#000"] * len(row)
                    return [""] * len(row)

                st.dataframe(
                    _micro_df.style.apply(_highlight_cur, axis=1),
                    use_container_width=True, hide_index=True,
                )

            # ── 예비가격 슬롯별 선택 빈도 표 ───────────────────────────────
            if r.get("slot_counts") and r.get("slot_ranges"):
                st.markdown("---")
                st.subheader("🎲 예비가격 번호별 선택 빈도")
                _n_slots = len(r["slot_counts"])
                st.caption(
                    f"10,000회 시뮬레이션에서 {_n_slots}개 예비가격 후보 중 "
                    f"각 번호가 최종 추첨(상위 {r.get('draw_count', 4)}개 평균)에 뽑힌 횟수입니다. "
                    "많이 뽑힌 번호일수록 예정가격에 영향이 큽니다."
                )
                _total_draws = sum(r["slot_counts"])
                _slot_rows = []
                for _i, (_cnt, (_lo, _hi)) in enumerate(zip(r["slot_counts"], r["slot_ranges"])):
                    _mid = (_lo + _hi) // 2
                    _pct = _cnt / _total_draws * 100
                    _slot_rows.append({
                        "번호": _i + 1,
                        "예비가격 범위": f"{_lo:,} ~ {_hi:,}",
                        "사정률 범위": f"{_lo/r['base_price']*100:.2f}% ~ {_hi/r['base_price']*100:.2f}%",
                        "선택 횟수": _cnt,
                        "선택 비율": round(_pct, 1),
                    })
                _slot_df = pd.DataFrame(_slot_rows)
                # 선택 비율 기준 내림차순 정렬해서 상위 강조
                _slot_df_sorted = _slot_df.sort_values("선택 비율", ascending=False).reset_index(drop=True)
                _max_pct = _slot_df_sorted["선택 비율"].iloc[0]

                def _highlight_slot(row):
                    if row["선택 비율"] == _max_pct:
                        return ["background-color:#ffd700;font-weight:bold;color:#000"] * len(row)
                    elif row["선택 비율"] >= _max_pct * 0.9:
                        return ["background-color:#fff3cd;color:#000"] * len(row)
                    return [""] * len(row)

                tab_slot1, tab_slot2 = st.tabs(["비율 높은 순", "번호 순"])
                with tab_slot1:
                    st.dataframe(
                        _slot_df_sorted.style.apply(_highlight_slot, axis=1),
                        use_container_width=True, hide_index=True,
                    )
                with tab_slot2:
                    st.dataframe(
                        _slot_df.style.apply(_highlight_slot, axis=1),
                        use_container_width=True, hide_index=True,
                    )

        else:
            st.info("왼쪽에서 기초금액을 입력하고 '계산하기' 버튼을 누르세요.")



# ══════════════════════════════════════════════════════════════════════════
# PAGE 2: 입찰공고 검색
# ══════════════════════════════════════════════════════════════════════════
elif page == "🔍 입찰공고 검색":
    st.title("🔍 입찰공고 검색")
    st.caption("나라장터 입찰공고 실시간 조회")

    with st.form("bid_search_form"):
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            keyword = st.text_input("공고명 검색어", placeholder="예: 소프트웨어 개발")
        with col2:
            bid_type = st.selectbox("공사 종류", ["공사", "물품", "용역"])
        with col3:
            rows = st.selectbox("조회 건수", [10, 20, 50, 100], index=1)

        col4, col5 = st.columns(2)
        with col4:
            start_dt = st.date_input("조회 시작일", value=datetime.now() - timedelta(days=7))
        with col5:
            end_dt = st.date_input("조회 종료일", value=datetime.now())

        st.caption("※ API 제한: 최대 14일 범위까지 조회 가능")
        submitted = st.form_submit_button("🔍 검색", use_container_width=True, type="primary")

    # 검색 실행 → 결과 session_state에 저장 (버튼 클릭 후에도 유지)
    if submitted:
        days_diff = (end_dt - start_dt).days
        if days_diff > 14:
            st.warning(f"⚠️ 조회 기간이 {days_diff}일입니다. API 제한(14일)을 초과하면 결과가 없을 수 있습니다.")
        start_str = start_dt.strftime("%Y%m%d") + "000000"
        end_str = end_dt.strftime("%Y%m%d") + "235959"
        with st.spinner("공고 조회 중..."):
            err_msg = None
            df = None
            try:
                df = api.get_bid_list(
                    bid_type=bid_type, keyword=keyword,
                    start_date=start_str, end_date=end_str, rows=rows,
                )
                if df.empty:
                    err_msg = ERR["E-05"]
                    df = None
            except ConnectionError as e:
                msg = str(e)
                if "429" in msg or "한도" in msg:
                    err_msg = ERR["E-02"]
                elif "시간 초과" in msg or "Timeout" in msg:
                    err_msg = ERR["E-03"]
                elif "500" in msg or "서버" in msg:
                    err_msg = ERR["E-04"]
                elif "401" in msg or "403" in msg or "승인" in msg or "인증" in msg:
                    err_msg = ERR["E-01"]
                else:
                    err_msg = f"{ERR['E-07']} ({msg})"
            except Exception as e:
                err_msg = f"{ERR['E-07']} ({e})"
        st.session_state["bid_search_result"] = {"df": df, "err_msg": err_msg, "bid_type": bid_type}

    # 결과 표시 (검색 후 버튼 클릭해도 유지)
    if "bid_search_result" in st.session_state:
        _sr = st.session_state["bid_search_result"]
        df, _stype = _sr["df"], _sr["bid_type"]
        err_msg = _sr.get("err_msg")

        if err_msg:
            st.warning(err_msg)
        if df is None:
            st.stop()
        else:
            st.success(f"총 {len(df)}건 조회됨")

        # 공고 선택 → 계산기 연동
        st.markdown("**공고를 선택해 계산기에 바로 적용하세요**")
        bid_names = df.apply(
            lambda r: f"{r.get('공고번호','?')} | {str(r.get('공고명',''))[:40]}", axis=1
        ).tolist()
        selected_idx = st.selectbox("공고 선택", range(len(bid_names)),
                                    format_func=lambda i: bid_names[i],
                                    label_visibility="collapsed")
        if st.button("📊 이 공고로 계산기 적용", type="primary"):
            row = df.iloc[selected_idx]
            bid_no = str(row.get("공고번호", ""))
            _agency = str(row.get("공고기관", ""))

            # 1단계: Supabase 캐시 확인
            info = cache_get_bid(bid_no)

            # 캐시에 업종코드 키 자체가 없으면 구버전 캐시 → 무효 처리
            if info and "업종코드" not in info:
                info = None

            if not info:
                # 2단계: 검색 결과 row에서 직접 추출 (API 재호출 없음 → 즉시)
                _presmpt = float(row.get("추정금액") or 0)
                # 기초금액: VAT 필드가 있으면 추정금액+VAT, 없으면 ×1.1
                _vat = 0.0
                for _vf in ["VAT", "indutyVAT"]:
                    try:
                        _v = float(row.get(_vf) or 0)
                        if _v > 0:
                            _vat = _v
                            break
                    except Exception:
                        pass
                if _vat > 0:
                    base = round(_presmpt + _vat) if _presmpt > 0 else None
                else:
                    base = round(_presmpt * 1.1) if _presmpt > 0 else None

                # 후보수/추첨수: 목록 row에 있으면 사용, 없으면 기본값
                try:
                    _cand = int(row.get("totPrdprcNum") or 0) or 15
                except Exception:
                    _cand = 15
                try:
                    _draw = int(row.get("drwtPrdprcNum") or 0) or 4
                except Exception:
                    _draw = 4

                # 참가제한지역 / 업종 — 실제 응답 필드명 기준
                _region = str(
                    row.get("cnstrtsiteRgnNm") or    # 공사현장지역명 (공사)
                    row.get("prtcptLmtRgnNm") or
                    row.get("ntceInsttRgnNm") or ""
                ).strip()
                _industry = str(
                    row.get("mainCnsttyNm") or        # 주요업종명 — 업종제한사항 (예: "전기공사업")
                    row.get("pubPrcrmntClsfcNm") or   # 공공조달분류명 (용역)
                    row.get("pubPrcrmntMidClsfcNm") or
                    row.get("dtilPrdctClsfcNoNm") or  # 품목분류명 (물품)
                    row.get("srvceDivNm") or row.get("용역구분") or
                    row.get("mainCnstwkBsnsNm") or
                    row.get("mainCnstwkBsns") or row.get("prdctClsfcNm") or ""
                ).strip()

                # 업종/지역/기초금액을 get_bid_by_no로 정확히 조회
                _industry_cd = ""
                _full = api.get_bid_by_no(bid_no, bid_type=_stype)
                if _full:
                    _industry    = _full.get("업종", _industry) or _industry
                    _industry_cd = _full.get("업종코드", "") or ""
                    _region      = _full.get("참가제한지역", _region) or _region
                    if _full.get("기초금액"):
                        base = _full["기초금액"]

                _a_val = 0
                try:
                    _pd_info = api.get_price_detail(bid_no, _stype)
                    if _pd_info and _pd_info.get("A값"):
                        _a_val = int(_pd_info["A값"])
                except Exception:
                    pass
                info = {
                    "공고번호":      bid_no,
                    "공고명":       str(row.get("공고명", "")),
                    "공고기관":     _agency,
                    "기초금액":     base,
                    "낙찰하한율":    float(row.get("낙찰하한율") or 0) or None,
                    "계약방식":     str(row.get("계약방식", "")),
                    "개찰일시":     str(row.get("개찰일시", "")),
                    "공사종류":     _stype,
                    "후보수":       _cand,
                    "추첨수":       _draw,
                    "참가제한지역":  _region,
                    "업종":         _industry,
                    "업종코드":     _industry_cd,
                    "예가범위_라벨": guess_price_range_label(_agency),
                    "A값":          _a_val,
                }
                cache_save_bid(bid_no, info)

            st.session_state["apply_bid"]  = info
            st.session_state["loaded_bid"] = info
            # 카드 미리 로드 → 계산기 페이지에서 바로 표시 (임시 비활성, 나중에 다시 사용)
            # with st.spinner("유사 낙찰 사례 조회 중..."):
            #     _cards = build_recent_cards(
            #         bid_type=_stype,
            #         region=info.get("참가제한지역", ""),
            #         industry_cd=info.get("업종코드", ""),
            #         base_price=info.get("기초금액") or 100_000_000,
            #         contract_type=info.get("계약방식", ""),
            #         before_date=info.get("개찰일시", ""),
            #     )
            # st.session_state["preloaded_cards"] = _cards
            st.session_state["nav_to"] = "💰 낙찰 예상가 계산기"
            st.rerun()

        st.markdown("---")
        display_df = df.copy()
        if "추정금액" in display_df.columns:
            display_df["추정금액(표시)"] = display_df["추정금액"].apply(
                lambda x: format_won(x) if pd.notna(x) else "-"
            )
        st.dataframe(display_df, use_container_width=True, hide_index=True)
        csv = df.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            "⬇️ CSV 다운로드",
            data=csv.encode("utf-8-sig"),
            file_name=f"입찰공고_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )


# ══════════════════════════════════════════════════════════════════════════
# PAGE 3: 낙찰 통계 분석
# ══════════════════════════════════════════════════════════════════════════
elif page == "📊 낙찰 통계 분석":
    st.title("📊 낙찰 통계 분석")
    st.caption("과거 낙찰 데이터 기반 사정률·낙찰률 분포 분석")

    with st.form("winner_search_form"):
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            keyword = st.text_input("공고명 검색어 (선택)", placeholder="예: 용역")
        with col2:
            bid_type = st.selectbox("공사 종류", ["용역", "물품", "공사"])
        with col3:
            rows = st.selectbox("조회 건수", [50, 100, 200, 500], index=1)

        col4, col5 = st.columns(2)
        with col4:
            start_dt = st.date_input("조회 시작일", value=datetime.now() - timedelta(days=180))
        with col5:
            end_dt = st.date_input("조회 종료일", value=datetime.now())

        col6, col7 = st.columns(2)
        _REGIONS = [
            "전체", "서울특별시", "부산광역시", "대구광역시", "인천광역시",
            "광주광역시", "대전광역시", "울산광역시", "세종특별자치시",
            "경기도", "강원특별자치도", "충청북도", "충청남도",
            "전북특별자치도", "전라남도", "경상북도", "경상남도",
            "제주특별자치도", "전국",
        ]
        with col6:
            region_filter = st.selectbox(
                "참가제한지역 (선택)",
                _REGIONS,
                help="해당 지역 업체만 참가 가능한 공고 필터링. '전국'은 지역 제한 없는 공고입니다.",
            )
        with col7:
            industry_filter = st.text_input(
                "업종 (선택)",
                placeholder="예: IT서비스, 건축공사, 전산장비",
                help="용역구분·공사업종·물품분류 키워드 필터. 일부 입력도 검색됩니다.",
            )

        st.caption("※ 기간이 길수록 통계 정확도 향상 (14일씩 자동 분할 조회)")
        submitted = st.form_submit_button("📊 분석하기", use_container_width=True, type="primary")

    if submitted:
        start_str = start_dt.strftime("%Y%m%d") + "000000"
        end_str = end_dt.strftime("%Y%m%d") + "235959"
        with st.spinner("낙찰 데이터 조회 중..."):
            df = None
            try:
                df = api.get_winner_list(
                    bid_type=bid_type,
                    keyword=keyword,
                    start_date=start_str,
                    end_date=end_str,
                    rows=rows,
                )
                if df.empty:
                    st.warning(ERR["E-05"])
                    df = None
            except ConnectionError as e:
                msg = str(e)
                if "429" in msg or "한도" in msg:
                    st.warning(ERR["E-02"])
                elif "시간 초과" in msg or "Timeout" in msg:
                    st.warning(ERR["E-03"])
                elif "500" in msg or "서버" in msg:
                    st.warning(ERR["E-04"])
                elif "401" in msg or "403" in msg or "승인" in msg or "인증" in msg:
                    st.warning(ERR["E-01"])
                else:
                    st.warning(f"{ERR['E-07']} ({msg})")
            except Exception as e:
                st.warning(f"{ERR['E-07']} ({e})")

        if df is not None and not df.empty:
            total_fetched = len(df)

            # ── 참가제한지역 / 업종 클라이언트 필터링 ──────────────────────
            if region_filter and region_filter != "전체" and "참가제한지역" in df.columns:
                df = df[df["참가제한지역"].str.contains(region_filter, na=False)]
            if industry_filter and "업종" in df.columns:
                df = df[df["업종"].str.contains(industry_filter, na=False, case=False)]

            if df.empty:
                st.warning("조회된 데이터가 없습니다. 필터 조건을 완화해 보세요.")
                st.stop()

            filter_info = f"총 {total_fetched}건 조회 → 필터 적용 후 **{len(df)}건** 분석"
            if region_filter != "전체" or industry_filter:
                applied = []
                if region_filter != "전체":
                    applied.append(f"지역: {region_filter}")
                if industry_filter:
                    applied.append(f"업종: {industry_filter}")
                filter_info += f" ({', '.join(applied)})"
            st.success(filter_info)

            # 핵심 통계
            c1, c2, c3, c4 = st.columns(4)
            if "낙찰률" in df.columns:
                rates = df["낙찰률"].dropna()
                with c1:
                    st.metric("낙찰률 평균", f"{rates.mean():.3f}%")
                with c2:
                    st.metric("낙찰률 중앙값", f"{rates.median():.3f}%")
                with c3:
                    st.metric("낙찰률 최솟값", f"{rates.min():.3f}%")
                with c4:
                    st.metric("낙찰률 최댓값", f"{rates.max():.3f}%")

            # 사정률 요약 (기초금액 있을 때)
            if "사정률" in df.columns and not df["사정률"].dropna().empty:
                sj = df["사정률"].dropna()
                with c1:
                    st.metric("사정률 평균", f"{sj.mean():.3f}%", help="낙찰금액/기초금액 × 100")
                with c2:
                    st.metric("사정률 중앙값", f"{sj.median():.3f}%")

            tab1, tab2, tab3, tab4 = st.tabs(["낙찰률 분포", "사정률 분포", "낙찰금액 분포", "원본 데이터"])

            with tab1:
                if "낙찰률" in df.columns and not df["낙찰률"].dropna().empty:
                    fig = px.histogram(
                        df.dropna(subset=["낙찰률"]),
                        x="낙찰률",
                        nbins=40,
                        title="낙찰률 분포",
                        labels={"낙찰률": "낙찰률 (%)"},
                        color_discrete_sequence=["#3498db"],
                    )
                    fig.add_vline(
                        x=df["낙찰률"].mean(),
                        line_dash="dash", line_color="red",
                        annotation_text=f"평균 {df['낙찰률'].mean():.3f}%",
                    )
                    fig.update_layout(height=400, margin=dict(t=40, b=0))
                    st.plotly_chart(fig, use_container_width=True)

                    # 구간별 빈도표
                    st.markdown("**구간별 낙찰 건수**")
                    bins = pd.cut(df["낙찰률"].dropna(), bins=10)
                    freq = bins.value_counts().sort_index().reset_index()
                    freq.columns = ["낙찰률 구간", "건수"]
                    freq["비율(%)"] = (freq["건수"] / freq["건수"].sum() * 100).round(1)
                    st.dataframe(freq, use_container_width=True, hide_index=True)
                else:
                    st.info("낙찰률 데이터가 없습니다.")

            with tab2:
                if "사정률" in df.columns and not df["사정률"].dropna().empty:
                    sj_df = df.dropna(subset=["사정률"])
                    sj_df = sj_df[(sj_df["사정률"] > 50) & (sj_df["사정률"] <= 110)]
                    if not sj_df.empty:
                        fig_sj = px.histogram(
                            sj_df,
                            x="사정률",
                            nbins=40,
                            title="사정률 분포 (낙찰금액 / 기초금액 × 100%)",
                            labels={"사정률": "사정률 (%)"},
                            color_discrete_sequence=["#8e44ad"],
                        )
                        fig_sj.add_vline(
                            x=sj_df["사정률"].mean(),
                            line_dash="dash", line_color="red",
                            annotation_text=f"평균 {sj_df['사정률'].mean():.3f}%",
                        )
                        fig_sj.add_vline(
                            x=float(sj_df["사정률"].median()),
                            line_dash="dot", line_color="#8e44ad",
                            annotation_text=f"중앙값 {sj_df['사정률'].median():.3f}%",
                        )
                        fig_sj.update_layout(height=400, margin=dict(t=40, b=0))
                        st.plotly_chart(fig_sj, use_container_width=True)
                        st.caption("사정률 = 낙찰금액 ÷ 기초금액 × 100. 이 발주처에서 기초금액의 몇 %로 투찰해야 낙찰되는지 보여줍니다.")

                        bins_sj = pd.cut(sj_df["사정률"], bins=10)
                        freq_sj = bins_sj.value_counts().sort_index().reset_index()
                        freq_sj.columns = ["사정률 구간", "건수"]
                        freq_sj["비율(%)"] = (freq_sj["건수"] / freq_sj["건수"].sum() * 100).round(1)
                        st.dataframe(freq_sj, use_container_width=True, hide_index=True)
                    else:
                        st.info("유효한 사정률 데이터가 없습니다.")
                else:
                    st.info("기초금액 데이터가 없어 사정률을 계산할 수 없습니다. (API에서 bssAmt 필드 미제공)")

            with tab3:
                if "낙찰금액" in df.columns and not df["낙찰금액"].dropna().empty:
                    df_valid = df.dropna(subset=["낙찰금액"])
                    df_valid = df_valid[df_valid["낙찰금액"] > 0]
                    fig2 = px.histogram(
                        df_valid,
                        x=df_valid["낙찰금액"] / 1e8,
                        nbins=30,
                        title="낙찰금액 분포",
                        labels={"x": "낙찰금액 (억원)"},
                        color_discrete_sequence=["#2ecc71"],
                    )
                    fig2.update_layout(height=400, margin=dict(t=40, b=0))
                    st.plotly_chart(fig2, use_container_width=True)
                else:
                    st.info("낙찰금액 데이터가 없습니다.")

            with tab4:
                display_df = df.copy()
                for col in ["낙찰금액", "기초금액", "예정가격"]:
                    if col in display_df.columns:
                        display_df[col] = display_df[col].apply(
                            lambda x: format_won(x) if pd.notna(x) and x > 0 else "-"
                        )
                st.dataframe(display_df, use_container_width=True, hide_index=True)
                csv = df.to_csv(index=False, encoding="utf-8-sig")
                st.download_button(
                    "⬇️ CSV 다운로드",
                    data=csv.encode("utf-8-sig"),
                    file_name=f"낙찰통계_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv",
                )


# ══════════════════════════════════════════════════════════════════════════
# PAGE 4: 내 입찰 기록
# ══════════════════════════════════════════════════════════════════════════
elif page == "📁 내 입찰 기록":
    st.title("📁 내 입찰 기록")
    st.caption("내 투찰 이력 누적 관리 및 패턴 분석")

    # ── 사용자 ID (사업자번호) ──────────────────────────────────────────
    user_id = st.text_input(
        "사업자번호 (하이픈 없이)",
        placeholder="예: 1234567890",
        help="사용자 식별에 사용됩니다. 같은 번호로 입력해야 내 기록이 조회됩니다.",
    )

    if not user_id.strip():
        st.info("사업자번호를 입력하면 내 입찰 기록을 조회하고 추가할 수 있습니다.")
        st.stop()

    user_id = user_id.strip().replace("-", "")

    tab_view, tab_add = st.tabs(["📋 기록 조회 및 분석", "➕ 기록 추가"])

    # ── 탭 1: 조회 및 분석 ────────────────────────────────────────────
    with tab_view:
        records = load_bid_records(user_id)

        if not records:
            st.info("아직 입찰 기록이 없습니다. '기록 추가' 탭에서 추가해주세요.")
        else:
            df_my = pd.DataFrame(records)

            # 요약 지표
            total = len(df_my)
            won = len(df_my[df_my["result"] == "낙찰"])
            win_rate = won / total * 100 if total > 0 else 0

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("총 입찰 횟수", f"{total}건")
            c2.metric("낙찰 횟수", f"{won}건")
            c3.metric("낙찰률", f"{win_rate:.1f}%")
            if "my_sajeong" in df_my.columns:
                won_df = df_my[df_my["result"] == "낙찰"]["my_sajeong"].dropna()
                c4.metric("낙찰 평균 사정률", f"{won_df.mean():.3f}%" if len(won_df) > 0 else "-")

            st.markdown("---")

            # 사정률 분포 차트
            if "my_sajeong" in df_my.columns and df_my["my_sajeong"].notna().sum() > 1:
                fig = go.Figure()
                won_mask = df_my["result"] == "낙찰"
                fig.add_trace(go.Scatter(
                    x=df_my[won_mask]["open_date"],
                    y=df_my[won_mask]["my_sajeong"],
                    mode="markers", name="낙찰",
                    marker=dict(color="#2ecc71", size=10, symbol="circle"),
                ))
                fig.add_trace(go.Scatter(
                    x=df_my[~won_mask]["open_date"],
                    y=df_my[~won_mask]["my_sajeong"],
                    mode="markers", name="탈락",
                    marker=dict(color="#e74c3c", size=10, symbol="x"),
                ))
                fig.update_layout(
                    title="내 투찰 사정률 추이",
                    xaxis_title="개찰일", yaxis_title="사정률 (%)",
                    height=350, margin=dict(t=40, b=0),
                )
                st.plotly_chart(fig, use_container_width=True)

            # 기록 테이블
            display_cols = ["open_date", "bid_name", "agency", "bid_type",
                            "base_price", "my_bid_price", "my_sajeong", "result", "memo"]
            display_cols = [c for c in display_cols if c in df_my.columns]
            rename_map = {
                "open_date": "개찰일", "bid_name": "공고명", "agency": "공고기관",
                "bid_type": "공사종류", "base_price": "기초금액",
                "my_bid_price": "내 투찰가", "my_sajeong": "사정률(%)",
                "result": "결과", "memo": "메모",
            }
            disp = df_my[display_cols].rename(columns=rename_map)
            for col in ["기초금액", "내 투찰가"]:
                if col in disp.columns:
                    disp[col] = disp[col].apply(
                        lambda x: format_won_exact(x) if pd.notna(x) and x > 0 else "-"
                    )
            st.dataframe(disp, use_container_width=True, hide_index=True)

            # 삭제
            with st.expander("🗑️ 기록 삭제"):
                _del_options = {
                    row.get("id"): f"[{row.get('open_date','')}] {row.get('bid_name','(공고명 없음)')}"
                    for row in records if row.get("id")
                }
                if _del_options:
                    _del_id = st.selectbox(
                        "삭제할 기록 선택",
                        options=list(_del_options.keys()),
                        format_func=lambda i: _del_options[i],
                    )
                    if st.button("삭제", type="secondary"):
                        if delete_bid_record(int(_del_id)):
                            st.success("삭제됐습니다.")
                            st.rerun()
                else:
                    st.info("삭제할 기록이 없습니다.")

            # CSV 다운로드
            csv = df_my.to_csv(index=False, encoding="utf-8-sig")
            st.download_button(
                "⬇️ CSV 다운로드",
                data=csv.encode("utf-8-sig"),
                file_name=f"내입찰기록_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )

    # ── 탭 2: 기록 추가 ───────────────────────────────────────────────
    with tab_add:
        st.subheader("입찰 기록 추가")

        # 공고번호로 자동 불러오기
        with st.expander("🔎 공고번호로 자동 불러오기", expanded=True):
            c_no, c_btn = st.columns([3, 1])
            with c_no:
                add_bid_no = st.text_input("공고번호", key="add_bid_no",
                                           placeholder="예: 20250123456-00")
            with c_btn:
                add_search = st.button("불러오기", key="add_search_btn")
            if add_search and add_bid_no.strip():
                with st.spinner("공고 조회 중..."):
                    info = None
                    try:
                        info = api.get_bid_by_no(add_bid_no.strip())
                    except Exception:
                        pass
                    if not info:
                        st.warning(ERR["E-06"])
                st.session_state["add_bid_info"] = info or {}

        _add_info = st.session_state.get("add_bid_info", {})

        with st.form("add_record_form"):
            col1, col2 = st.columns(2)
            with col1:
                f_bid_no   = st.text_input("공고번호", value=_add_info.get("공고번호", ""))
                f_bid_name = st.text_input("공고명",   value=_add_info.get("공고명", ""))
                f_agency   = st.text_input("공고기관", value=_add_info.get("공고기관", ""))
                f_bid_type = st.selectbox("공사종류", ["용역", "물품", "공사"],
                    index=["용역","물품","공사"].index(_add_info.get("공사종류","용역"))
                    if _add_info.get("공사종류") in ["용역","물품","공사"] else 0)
            with col2:
                f_base     = st.number_input("기초금액 (원)", min_value=0,
                                             value=int(_add_info.get("기초금액") or 0), step=1_000_000)
                f_my_bid   = st.number_input("내 투찰가 (원)", min_value=0, step=1000)
                f_sajeong  = round(f_my_bid / f_base * 100, 3) if f_base > 0 else 0.0
                st.metric("사정률", f"{f_sajeong:.3f}%")
                f_result   = st.selectbox("결과", ["낙찰", "탈락"])
                f_date     = st.date_input("개찰일", value=datetime.now().date())
                f_memo     = st.text_input("메모 (선택)")

            submitted = st.form_submit_button("💾 저장", use_container_width=True, type="primary")
            if submitted:
                if not f_my_bid:
                    st.error("투찰가를 입력해주세요.")
                else:
                    rec = {
                        "user_id":      user_id,
                        "bid_no":       f_bid_no,
                        "bid_name":     f_bid_name,
                        "agency":       f_agency,
                        "bid_type":     f_bid_type,
                        "base_price":   int(f_base),
                        "my_bid_price": int(f_my_bid),
                        "my_sajeong":   f_sajeong,
                        "result":       f_result,
                        "open_date":    f_date.isoformat(),
                        "memo":         f_memo,
                    }
                    if save_bid_record(rec):
                        st.success("저장됐습니다!")
                        st.session_state.pop("add_bid_info", None)
                        st.rerun()
