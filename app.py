import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import numpy as np
from datetime import datetime, timedelta

from api.g2b_api import G2BAPI
from analysis.bid_analyzer import calc_bid_range, analyze_winner_stats, recommend_from_stats, extract_keyword, tiered_filter, format_won, format_won_exact, calc_optimal_bid, estimate_competitor_count
from analysis.demo_data import get_demo_bid_list, get_demo_winner_list, get_demo_bid_by_no

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
page = st.sidebar.radio(
    "메뉴",
    ["💰 낙찰 예상가 계산기", "🔍 입찰공고 검색", "📊 낙찰 통계 분석"],
    label_visibility="collapsed",
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


# ══════════════════════════════════════════════════════════════════════════
# PAGE 1: 낙찰 예상가 계산기
# ══════════════════════════════════════════════════════════════════════════
if page == "💰 낙찰 예상가 계산기":
    st.title("💰 낙찰 예상가 계산기")
    st.caption("기초금액 기반 복수예가 시뮬레이션 및 낙찰하한율 적용")

    # ── 공고번호 검색 ──────────────────────────────
    with st.expander("🔎 공고번호로 자동 불러오기", expanded=True):
        c_no, c_btn = st.columns([3, 1])
        with c_no:
            bid_no_input = st.text_input(
                "공고번호", placeholder="예: 20250123456-00",
                label_visibility="collapsed",
            )
        with c_btn:
            search_btn = st.button("불러오기", use_container_width=True, type="secondary")

        if search_btn and bid_no_input.strip():
            with st.spinner("공고 조회 중..."):
                info = None
                try:
                    info = api.get_bid_by_no(bid_no_input.strip())
                except Exception:
                    pass
                if not info:
                    info = get_demo_bid_by_no(bid_no_input.strip())

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

    col_input, col_result = st.columns([1, 2], gap="large")

    with col_input:
        st.subheader("입력")
        TYPE_OPTIONS = ["용역", "물품", "공사"]
        _default_type_idx = TYPE_OPTIONS.index(_apply["공사종류"]) if _apply.get("공사종류") in TYPE_OPTIONS else 0
        bid_type = st.selectbox("공사 종류", TYPE_OPTIONS, index=_default_type_idx)

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
        )
        if lower_rate_input != default_rate:
            st.caption(f"※ 기본값({default_rate}%)에서 변경됨")

        # 기초금액 — 공고 자동적용 또는 직접 입력
        _base_from_bid = int(_apply["기초금액"]) if _apply.get("기초금액") else None
        input_method = st.radio("금액 입력 방식", ["직접 입력 (원)", "억원 단위"])
        if input_method == "직접 입력 (원)":
            base_price_input = st.number_input(
                "기초금액 (원)", min_value=100000,
                value=_base_from_bid if _base_from_bid else 100_000_000,
                step=1_000_000, format="%d"
            )
        else:
            _default_eok = round(_base_from_bid / 1e8, 2) if _base_from_bid else 1.0
            base_eok = st.number_input("기초금액 (억원)", min_value=0.01, value=_default_eok, step=0.1, format="%.2f")
            base_price_input = int(base_eok * 1_0000_0000)
            st.info(f"= {base_price_input:,}원")

        st.markdown("---")
        competitor_count = st.number_input(
            "예상 경쟁사 수 (0=과거 데이터 자동 추정)",
            min_value=0, max_value=100, value=0, step=1,
            help="0이면 같은 기관·유사금액 과거 입찰의 참가업체수 중앙값을 자동 사용합니다.",
        )
        if "last_result" in st.session_state:
            _ec = st.session_state["last_result"].get("estimated_comp")
            if _ec:
                st.caption(f"📊 과거 유사 입찰 추정: **{_ec['median']}개사** (범위 {_ec['min']}~{_ec['max']}개사, {_ec['sample']}건 기준)")

        calc_btn = st.button("📊 계산하기", use_container_width=True, type="primary")

        st.markdown("---")
        st.caption("※ 복수예가: 기초금액의 -2%~+3% 범위\n15개 후보 중 2개 추첨 후 평균 = 예정가격\n※ 10,000회 몬테카를로 시뮬레이션")

    with col_result:
        if calc_btn or "last_result" in st.session_state:
            if calc_btn:
                with st.spinner("시뮬레이션 + 과거 낙찰 데이터 조회 중..."):
                    _loaded_bid = st.session_state.get("apply_bid", {})
                    _cand = int(_loaded_bid.get("후보수") or 15)
                    _draw = int(_loaded_bid.get("추첨수") or 2)
                    result = calc_bid_range(
                        base_price_input, bid_type,
                        custom_lower_rate=lower_rate_input,
                        candidate_count=_cand,
                        draw_count=_draw,
                    )

                    # 공고번호 로드된 경우 → 기관·계약방식·키워드 추출
                    loaded = st.session_state.get("apply_bid", {})
                    keyword      = extract_keyword(loaded.get("공고명", "")) if loaded else ""
                    agency       = loaded.get("공고기관", "") if loaded else ""
                    contract_type = loaded.get("계약방식", "") if loaded else ""

                    winner_df = pd.DataFrame()
                    try:
                        winner_df = api.get_winner_list(
                            bid_type=bid_type,
                            keyword=keyword,
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
                    result["stats"] = recommend_from_stats(winner_df, result["expected_price_mean"])
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

            st.subheader("분석 결과")

            # ── 상단 요약 지표 ──────────────────────────────────────────
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("기초금액",       format_won(r["base_price"]))
            c2.metric("예정가격 (평균)", format_won(r["expected_price_mean"]),
                      f"사정률 {r['expected_price_mean']/r['base_price']*100:.2f}%")
            c3.metric("낙찰하한금액 (평균)", format_won(r["award_floor_mean"]),
                      f"하한율 {r['lower_rate_pct']}%")
            c4.metric("안전구간 크기",
                      format_won(r["safe_high"] - r["safe_low"]) if r["safe_exists"] else "구간 없음",
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
    ⭐ 최적 투찰 구간 — 90% 확률 안전구간{' + 과거 통계 반영' if stats else ''}
  </div>
  <div style="font-size:22px;font-weight:800;margin-bottom:2px">
    {format_won_exact(r['safe_low_p90'])} ~ {format_won_exact(r['safe_high_p90'])}
  </div>
  <div style="font-size:13px;opacity:.75;margin-bottom:4px">
    ({format_won(r['safe_low_p90'])} ~ {format_won(r['safe_high_p90'])})
  </div>
  <div style="font-size:13px;opacity:.8">
    사정률 {r['safe_low_p90']/r['base_price']*100:.3f}% ~ {r['safe_high_p90']/r['base_price']*100:.3f}%
    &nbsp;|&nbsp; 하단: 유효확률 {r.get('safe_low_prob',0):.1f}%
    &nbsp;|&nbsp; 중간: 유효확률 {r.get('safe_mid_prob',0):.1f}%
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
    {format_won(best_low)} ~ {format_won(best_high)}
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
                detail_df["금액(표시)"] = detail_df["금액"].apply(format_won)
                detail_df["금액(원)"]   = detail_df["금액"].apply(lambda x: f"{x:,.0f}")
                detail_df["사정률(%)"]  = detail_df["금액"].apply(
                    lambda x: f"{x / r['base_price'] * 100:.3f}%"
                )
                st.dataframe(
                    detail_df[["구분", "금액(표시)", "금액(원)", "사정률(%)"]],
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

        submitted = st.form_submit_button("🔍 검색", use_container_width=True, type="primary")

    if submitted:
        start_str = start_dt.strftime("%Y%m%d") + "000000"
        end_str = end_dt.strftime("%Y%m%d") + "235959"
        is_demo = False
        with st.spinner("공고 조회 중..."):
            try:
                df = api.get_bid_list(
                    bid_type=bid_type,
                    keyword=keyword,
                    start_date=start_str,
                    end_date=end_str,
                    rows=rows,
                )
                if df.empty:
                    df = get_demo_bid_list(bid_type=bid_type, rows=rows)
                    is_demo = True
            except Exception:
                df = get_demo_bid_list(bid_type=bid_type, rows=rows)
                is_demo = True

        if is_demo:
            st.info("⚠️ API 미연결 상태 — 데모 데이터를 표시합니다. data.go.kr에서 API 승인을 확인하세요.")
        else:
            st.success(f"총 {len(df)}건 조회됨")

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
            start_dt = st.date_input("조회 시작일", value=datetime.now() - timedelta(days=90))
        with col5:
            end_dt = st.date_input("조회 종료일", value=datetime.now())

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
            except Exception:
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

            tab1, tab2, tab3 = st.tabs(["낙찰률 분포", "낙찰금액 분포", "원본 데이터"])

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

            with tab3:
                display_df = df.copy()
                for col in ["낙찰금액", "추정금액", "예정가격"]:
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
