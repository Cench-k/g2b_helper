import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import numpy as np
from datetime import datetime, timedelta

from api.g2b_api import G2BAPI
from analysis.bid_analyzer import calc_bid_range, analyze_winner_stats, recommend_from_stats, extract_keyword, tiered_filter, format_won, format_won_exact, calc_optimal_bid, estimate_competitor_count
from analysis.demo_data import get_demo_bid_list, get_demo_winner_list, get_demo_bid_by_no
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

def check_api_status() -> bool:
    """API 연결 상태 확인"""
    try:
        df = api.get_bid_list(bid_type="용역", rows=1)
        return True
    except Exception:
        return False

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
            st.session_state["api_ok"] = check_api_status()

if st.session_state.get("api_ok"):
    st.sidebar.success("✅ API 연결됨")
else:
    st.sidebar.warning("⚠️ API 미연결 (데모 모드)")
    st.sidebar.caption("data.go.kr 마이페이지에서\nAPI 승인 상태를 확인하세요.")
    if st.sidebar.button("🔄 API 재연결"):
        st.session_state["api_ok"] = check_api_status()
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption("조달청 Open API 기반\n데이터 기준: 실시간")


@st.dialog("📖 분석 결과 용어 설명", width="large")
def show_result_help():
    st.markdown("""
### 🎯 단일 최적 투찰가
시뮬레이션 + 과거 낙찰 통계 + 경쟁사 수를 종합해 계산한 **가장 유리한 투찰 금액 1개**입니다.
경쟁이 많을수록 낮게, 적을수록 높게 산출됩니다.

---
### 📐 예정가격
나라장터가 개찰 당일 기초금액의 **-2%~+3% 범위** 후보 15개 중 2개를 무작위 추첨해 평균낸 금액입니다.
> **투찰가 ≤ 예정가격** 이어야 유효합니다.

---
### 🔒 안전구간 (유효 확률 100%)
어떤 예정가격이 나와도 **반드시 유효한 구간**입니다.
- **하한** = 예정가격 최댓값 × 낙찰하한율 (어떤 경우에도 하한 미달 없음)
- **상한** = 예정가격 최솟값 (어떤 경우에도 예정가 초과 없음)

이 구간 안에 투찰하면 무효될 일이 없습니다.

---
### ⭐ 경쟁력 투찰 구간
안전구간 하단 40% 범위입니다. **낮을수록 낙찰 경쟁력이 높아지므로**, 이 구간 하단에 가깝게 투찰하는 것이 유리합니다.

---
### 📊 낙찰하한금액
예정가격 × 낙찰하한율로 계산됩니다. 투찰가가 이 금액 **미만**이면 무효입니다.
- 예정가격이 추첨으로 결정되므로, 낙찰하한금액도 범위로 표시됩니다.

---
### 📏 사정률
투찰가 ÷ 기초금액 × 100(%)입니다. 예) 사정률 88% = 기초금액의 88% 금액으로 투찰.

---
### 📈 유효 확률
10,000번 시뮬레이션 결과 중 해당 투찰가가 **낙찰하한 이상 & 예정가 이하**인 경우의 비율입니다.
100%면 어떤 상황에서도 유효, 낮을수록 무효가 될 가능성이 높습니다.
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
            is_demo = info.get("_is_demo", False)
            if is_demo:
                st.caption("⚠️ API 미연결 — 데모 데이터 적용됨")

            ic1, ic2, ic3 = st.columns(3)
            ic1.markdown(f"**공고명**  \n{info['공고명']}")
            ic2.markdown(f"**공고기관**  \n{info['공고기관']}")
            ic3.markdown(f"**공사종류**  \n{info['공사종류']}")

            ia1, ia2, ia3 = st.columns(3)
            ia1.markdown(f"**기초금액**  \n{format_won(info['기초금액']) if info['기초금액'] else '-'}")
            ia2.markdown(f"**낙찰하한율**  \n{info['낙찰하한율']}%" if info['낙찰하한율'] else "**낙찰하한율**  \n미제공")
            ia3.markdown(f"**개찰일시**  \n{info['개찰일시'] or '-'}")
            st.success("✅ 아래 계산기에 값이 자동 적용됐습니다.")

    # 공고 자동 적용 값 읽기
    _apply = st.session_state.get("apply_bid", {})
    _wkey = _apply.get("공고번호", "default")  # 공고 변경 시 위젯 재초기화용 key

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
        _base_from_bid = int(_apply["기초금액"]) if _apply.get("기초금액") else None
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
        a_value_input = st.number_input(
            "A값 (순공사원가 등, 원) — 해당 없으면 0",
            min_value=0, value=0, step=1_000_000, format="%d",
            help="A값 적용 공고는 낙찰하한금액 산식이 달라집니다.\n"
                 "적용 산식: (예정가격 - A값) × 낙찰하한율 + A값\n"
                 "공고문에 순공사원가·국민연금·건강보험료·퇴직공제부금비 합산액 명시 시 입력하세요.",
            key=f"a_value_{_wkey}",
        )
        if a_value_input > 0:
            st.caption(f"A값 적용: 낙찰하한금액 = (예정가 - {format_won(a_value_input)}) × {lower_rate_input}% + {format_won(a_value_input)}")

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

                    # 공고번호 로드된 경우 → 기관·계약방식·키워드 추출
                    loaded = st.session_state.get("apply_bid", {})
                    keyword      = extract_keyword(loaded.get("공고명", "")) if loaded else ""
                    agency       = loaded.get("공고기관", "") if loaded else ""
                    contract_type = loaded.get("계약방식", "") if loaded else ""

                    winner_df = pd.DataFrame()
                    try:
                        _w_start = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d") + "000000"
                        _w_end   = datetime.now().strftime("%Y%m%d") + "235959"
                        winner_df = api.get_winner_list(
                            bid_type=bid_type,
                            keyword=keyword,
                            start_date=_w_start,
                            end_date=_w_end,
                            rows=500,
                        )
                    except Exception:
                        pass

                    is_demo = winner_df.empty
                    if is_demo:
                        winner_df = get_demo_winner_list(bid_type=bid_type, rows=500)

                    # 단계적 필터링
                    winner_df, filter_desc = tiered_filter(
                        winner_df,
                        base_price=base_price_input,
                        agency=agency,
                        contract_type=contract_type,
                    )

                    result["stats_is_demo"]   = is_demo
                    result["stats_keyword"]   = keyword
                    result["stats_filter_desc"] = filter_desc
                    result["stats"] = recommend_from_stats(winner_df, result["expected_price_mean"], base_price=base_price_input)
                    # 과거 데이터 기반 경쟁사 수 추정
                    result["estimated_comp"] = estimate_competitor_count(winner_df)
                st.session_state["last_result"] = result
            else:
                result = st.session_state["last_result"]

            r     = result
            stats = r.get("stats")

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
            if stats:
                is_demo_stats = r.get("stats_is_demo", False)
                filter_desc   = r.get("stats_filter_desc", "")
                demo_label    = " (데모)" if is_demo_stats else ""
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
    📊 {best_label} &nbsp;—&nbsp; {filter_desc}{demo_label}
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

                # 사정률 분석 (기초금액 기반 낙찰 경향)
                if stats.get("sajeong_mean"):
                    st.markdown(f"""
<div style="background:#f8f9fa;border-left:5px solid #8e44ad;
border-radius:8px;padding:14px 18px;margin-top:8px;">
  <div style="font-size:13px;color:#666;margin-bottom:4px">
    📏 사정률 분석 — 기초금액 대비 낙찰금액 비율 (발주처별 낙찰 경향)
  </div>
  <div style="font-size:20px;font-weight:700;color:#8e44ad">
    중앙값 {stats['sajeong_median']:.3f}% &nbsp;|&nbsp; 평균 {stats['sajeong_mean']:.3f}%
    &nbsp;|&nbsp; 25~75% 구간 {stats['sajeong_p25']:.2f}~{stats['sajeong_p75']:.2f}%
  </div>
  {"<div style='font-size:13px;color:#666;margin-top:4px'>→ 이 발주처 유사 공고 투찰가 추천: <b>" + format_won_exact(int(stats['sajeong_recommend'])) + "</b> (기초금액 × " + str(stats['sajeong_median']) + "%)</div>" if stats.get('sajeong_recommend') else ""}
</div>""", unsafe_allow_html=True)

            # ── 통합 시각화 차트 ─────────────────────────────────────────
            tab_chart1, tab_chart2 = st.tabs(["투찰가별 유효 확률", "예정가격 분포"])

            with tab_chart1:
                dist_arr = np.array(r["distribution"])
                lr = r["lower_rate_pct"] / 100
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
            st.caption("최적 투찰가를 기준으로 1원 단위로 조정하며 유효 확률을 확인하세요.")

            _av   = r.get("a_value", 0.0)
            _lr   = r["lower_rate_pct"] / 100
            _dist = np.array(r["distribution"])
            if _av > 0:
                _floors = np.ceil(np.round((_dist - _av) * _lr + _av, 5))
            else:
                _floors = np.ceil(np.round(_dist * _lr, 5))

            def _calc_prob(price: int) -> tuple:
                survived = int(((price >= _floors) & (price <= _dist)).sum())
                return survived, survived / len(_dist) * 100

            c_micro1, c_micro2 = st.columns([1, 2])
            with c_micro1:
                micro_price = st.number_input(
                    "최종 투찰가 입력 (원)",
                    value=int(optimal["optimal_bid"]),
                    step=1, format="%d",
                    key=f"micro_{_wkey}",
                )
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

                # ±5원 인근 확률 미니 테이블
                _micro_rows = []
                for delta in range(-5, 6):
                    p = micro_price + delta
                    s, prob = _calc_prob(p)
                    _micro_rows.append({
                        "투찰가": f"{p:,}원",
                        "유효 횟수": f"{s:,}",
                        "유효 확률": f"{prob:.2f}%",
                        "비고": "◀ 현재 입력" if delta == 0 else "",
                    })
                st.dataframe(
                    pd.DataFrame(_micro_rows),
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
            bid_type = st.selectbox("공사 종류", ["용역", "물품", "공사"])
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
            try:
                df = api.get_bid_list(
                    bid_type=bid_type, keyword=keyword,
                    start_date=start_str, end_date=end_str, rows=rows,
                )
                is_demo = df.empty
                if is_demo:
                    df = get_demo_bid_list(bid_type=bid_type, rows=rows)
            except Exception as e:
                st.error(f"API 오류: {e}")
                df = get_demo_bid_list(bid_type=bid_type, rows=rows)
                is_demo = True
        st.session_state["bid_search_result"] = {"df": df, "is_demo": is_demo, "bid_type": bid_type}

    # 결과 표시 (검색 후 버튼 클릭해도 유지)
    if "bid_search_result" in st.session_state:
        _sr = st.session_state["bid_search_result"]
        df, is_demo, _stype = _sr["df"], _sr["is_demo"], _sr["bid_type"]

        if is_demo:
            st.info("⚠️ API 미연결 상태 — 데모 데이터입니다.")
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
            base = float(row.get("추정금액") or 0) * 1.1 if row.get("추정금액") else None
            _agency = str(row.get("공고기관", ""))
            info = {
                "공고번호":  bid_no,
                "공고명":   str(row.get("공고명", "")),
                "공고기관": _agency,
                "기초금액": base,
                "낙찰하한율": float(row.get("낙찰하한율") or 0) or None,
                "계약방식": str(row.get("계약방식", "")),
                "개찰일시": str(row.get("개찰일시", "")),
                "공사종류": _stype,
                "후보수": 15, "추첨수": 4,
                "예가범위_라벨": guess_price_range_label(_agency),
            }
            cache_save_bid(bid_no, info)
            st.session_state["apply_bid"] = info
            st.session_state["loaded_bid"] = info
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

        st.caption("※ 기간이 길수록 통계 정확도 향상 (14일씩 자동 분할 조회)")
        submitted = st.form_submit_button("📊 분석하기", use_container_width=True, type="primary")

    if submitted:
        start_str = start_dt.strftime("%Y%m%d") + "000000"
        end_str = end_dt.strftime("%Y%m%d") + "235959"
        is_demo = False
        with st.spinner("낙찰 데이터 조회 중..."):
            try:
                df = api.get_winner_list(
                    bid_type=bid_type,
                    keyword=keyword,
                    start_date=start_str,
                    end_date=end_str,
                    rows=rows,
                )
                if df.empty:
                    df = get_demo_winner_list(bid_type=bid_type, rows=rows)
                    is_demo = True
            except ConnectionError as e:
                msg = str(e)
                if "한도 초과" in msg or "429" in msg:
                    st.warning("⚠️ API 일일 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.")
                elif "응답 시간 초과" in msg or "Timeout" in msg:
                    st.warning("⚠️ API 응답 시간이 초과됐습니다. 네트워크 상태를 확인하거나 잠시 후 재시도하세요.")
                elif "500" in msg or "서버" in msg:
                    st.warning("⚠️ 조달청 API 서버 오류입니다. 잠시 후 다시 시도하세요.")
                elif "승인" in msg or "인증" in msg or "401" in msg or "403" in msg:
                    st.warning("⚠️ API 키 인증 오류입니다. data.go.kr에서 API 승인 상태를 확인하세요.")
                else:
                    st.warning(f"⚠️ API 연결 실패: {msg}")
                df = get_demo_winner_list(bid_type=bid_type, rows=rows)
                is_demo = True
            except Exception as e:
                st.error(f"예상치 못한 오류: {e}")
                df = get_demo_winner_list(bid_type=bid_type, rows=rows)
                is_demo = True

        if is_demo:
            st.info("⚠️ API 미연결 상태 — 데모 데이터를 표시합니다. 낙찰 패턴은 실제 나라장터 통계를 반영합니다.")

        if not df.empty:
            st.success(f"총 {len(df)}건 낙찰 데이터 분석")

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
                        info = get_demo_bid_by_no(add_bid_no.strip())
                st.session_state["add_bid_info"] = info

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
