import math
import numpy as np
import pandas as pd
from config import AWARD_LOWER_RATE, MULTIPLE_PRICE_RANGE

# 심리 가중치: 입찰자들은 중간 번호(7~9번)를 선호하고 끝 번호를 회피하는 경향
# → 각 위치(1~15번)가 추첨 풀에 뽑힐 상대적 확률
_PSYCH_WEIGHTS_15 = np.array([
    2, 3, 4, 5, 7, 8, 12, 13, 11, 9, 8, 6, 5, 4, 3
], dtype=float)
_PSYCH_WEIGHTS_15 /= _PSYCH_WEIGHTS_15.sum()


def simulate_expected_price(
    base_price: float,
    simulations: int = 10000,
    candidate_count: int = 15,
    draw_count: int = 4,
    price_range: tuple = (-2.0, 3.0),
    use_psychology_weight: bool = False,
) -> dict:
    """
    몬테카를로 시뮬레이션으로 예정가격 분포 계산
    - 매 시뮬레이션마다 candidate_count개 예비가격을 구간 내 난수로 새로 생성
      (선형 등간격 아닌 진짜 랜덤 → 실제 나라장터 동작과 동일)
    - draw_count개 무작위 추첨 후 평균 floor = 예정가격
    price_range: (min%, max%) — 조달청 (-2, 3) / 지자체 (-3, 3)
    use_psychology_weight: 입찰자들의 번호 선호 편향 반영
      (사람들은 중간 번호 7~9번 선호, 끝 번호 회피 → 중간 후보가 뽑힐 확률 상승)
    """
    low  = base_price * (1 + price_range[0] / 100)
    high = base_price * (1 + price_range[1] / 100)
    draw = min(draw_count, candidate_count)
    results = []
    interval = (high - low) / candidate_count  # 구간 폭
    rng = np.random.default_rng()  # 매 실행마다 진짜 랜덤 (시드 고정 해제)

    # 심리 가중치 설정 (15개 표준 후보에만 적용)
    if use_psychology_weight and candidate_count == 15:
        _weights = _PSYCH_WEIGHTS_15
    else:
        _weights = None

    slot_counts = np.zeros(candidate_count, dtype=int)

    for _ in range(simulations):
        # 층화 추출: 전체 범위를 candidate_count개 구간으로 나누고 각 구간에서 1개씩 난수 생성
        # → 15개가 전체 범위에 고르게 분포 (쏠림 방지, 실제 G2B 방식)
        offsets = rng.random(candidate_count)          # 각 구간 내 위치 (0~1)
        candidates = np.floor(
            low + (np.arange(candidate_count) + offsets) * interval
        ).astype(np.int64)
        if _weights is not None:
            chosen_idx = rng.choice(candidate_count, size=draw, replace=False, p=_weights)
        else:
            chosen_idx = rng.choice(candidate_count, size=draw, replace=False)
        slot_counts[chosen_idx] += 1
        results.append(math.floor(candidates[chosen_idx].mean()))  # 나라장터: 예정가격 소수점 버림(절사)
    arr = np.array(results)
    # 슬롯별 가격 범위 (확정적 경계값)
    slot_ranges = [
        (int(low + i * interval), int(low + (i + 1) * interval))
        for i in range(candidate_count)
    ]
    return {
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "min": float(arr.min()),
        "max": float(arr.max()),
        "p10": float(np.percentile(arr, 10)),
        "p25": float(np.percentile(arr, 25)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
        "distribution": arr.tolist(),
        "slot_counts": slot_counts.tolist(),
        "slot_ranges": slot_ranges,
    }


def calc_bid_range(
    base_price: float,
    bid_type: str = "용역",
    custom_lower_rate: float | None = None,
    candidate_count: int = 15,
    draw_count: int = 4,
    a_value: float = 0.0,
    price_range: tuple = (-2.0, 3.0),
    use_psychology_weight: bool = False,
) -> dict:
    """
    낙찰 예상 범위 계산 + 안전구간(어떤 예정가격에도 유효한 구간) 산출
    a_value: 순공사원가(A값). 0이면 기존 산식, 0 초과면 A값 적용 산식 사용
      A값 적용: 낙찰하한금액 = (예정가격 - A값) × 낙찰하한율 + A값
    use_psychology_weight: 입찰자 심리 가중치 반영 여부
    """
    sim = simulate_expected_price(base_price, candidate_count=candidate_count, draw_count=draw_count, price_range=price_range, use_psychology_weight=use_psychology_weight)
    lower_rate_pct = custom_lower_rate if custom_lower_rate is not None else AWARD_LOWER_RATE.get(bid_type, 88.0)
    lower_rate = lower_rate_pct / 100

    expected_mean = sim["mean"]
    expected_min  = sim["min"]
    expected_max  = sim["max"]
    expected_p10  = sim["p10"]
    expected_p90  = sim["p90"]

    def _floor(ep: float) -> float:
        """낙찰하한금액 계산 (A값 적용 여부에 따라 산식 분기)
        나라장터: 낙찰하한금액 소수점 올림(절상) → math.ceil 강제 적용
        부동소수점 오차 방어: ceil 전 round(5자리)로 미세 먼지 제거"""
        if a_value > 0:
            return math.ceil(round((ep - a_value) * lower_rate + a_value, 5))
        return math.ceil(round(ep * lower_rate, 5))

    # 낙찰하한금액 범위
    award_floor_min  = _floor(expected_min)
    award_floor_mean = _floor(expected_mean)
    award_floor_max  = _floor(expected_max)

    # ── 안전구간 ──────────────────────────────────────────────────────
    # 어떤 예정가격이 나와도 투찰이 유효(낙찰 자격)한 구간
    #   하한: max(낙찰하한금액) = 최대 예정가격 × 낙찰하한율
    #         → 이 이상이면 절대 하한 미달 없음
    #   상한: min(예정가격)
    #         → 이 이하면 절대 예정가 초과 없음
    safe_low  = award_floor_max          # 안전구간 하한 (= 최대 낙찰하한금액)
    safe_high = expected_min             # 안전구간 상한 (= 최소 예정가격)
    safe_mid  = (safe_low + safe_high) / 2
    safe_exists = safe_high >= safe_low   # 안전구간이 존재하는지 여부

    # 경쟁력 투찰 구간: 안전구간 하단 40% (낮을수록 경쟁력 ↑)
    # 안전구간이 없으면 평균낙찰하한금액 ± 소폭 완충
    if safe_exists:
        _band = (safe_high - safe_low) * 0.40
        safe_low_p90  = safe_low
        safe_high_p90 = safe_low + _band
    else:
        safe_low_p90  = expected_mean * lower_rate
        safe_high_p90 = expected_mean * lower_rate * 1.015
    safe_mid_p90  = (safe_low_p90 + safe_high_p90) / 2

    # 각 투찰가별 유효 확률 계산 (시뮬레이션 분포 기반)
    dist = np.array(sim["distribution"])
    def valid_prob(bid_price: float) -> float:
        """해당 투찰가가 유효할 확률 (낙찰하한 이상 AND 예정가 이하)"""
        if a_value > 0:
            floors = np.ceil(np.round((dist - a_value) * lower_rate + a_value, 5))
        else:
            floors = np.ceil(np.round(dist * lower_rate, 5))
        valid = ((bid_price >= floors) & (bid_price <= dist))
        return float(valid.mean() * 100)

    safe_low_prob  = valid_prob(safe_low)
    safe_mid_prob  = valid_prob(safe_mid)
    safe_high_prob = valid_prob(safe_high)

    # 사정률
    sajeong_safe_low  = safe_low  / base_price * 100
    sajeong_safe_high = safe_high / base_price * 100
    sajeong_safe_mid  = safe_mid  / base_price * 100

    return {
        "base_price":      base_price,
        "bid_type":        bid_type,
        "lower_rate_pct":  lower_rate_pct,
        # 예정가격
        "expected_price_mean": expected_mean,
        "expected_price_min":  expected_min,
        "expected_price_max":  expected_max,
        "expected_price_p10":  expected_p10,
        "expected_price_p90":  expected_p90,
        "expected_price_p25":  sim["p25"],
        "expected_price_p75":  sim["p75"],
        # 낙찰하한금액
        "award_floor_min":  award_floor_min,
        "award_floor_mean": award_floor_mean,
        "award_floor_max":  award_floor_max,
        # 안전구간 (100% 유효)
        "safe_low":   safe_low,
        "safe_high":  safe_high,
        "safe_mid":   safe_mid,
        "safe_exists": safe_exists,
        "safe_low_prob":  safe_low_prob,
        "safe_mid_prob":  safe_mid_prob,
        "safe_high_prob": safe_high_prob,
        # 90% 안전구간
        "safe_low_p90":  safe_low_p90,
        "safe_high_p90": safe_high_p90,
        "safe_mid_p90":  safe_mid_p90,
        # 사정률
        "sajeong_safe_low":  sajeong_safe_low,
        "sajeong_safe_high": sajeong_safe_high,
        "sajeong_safe_mid":  sajeong_safe_mid,
        # A값 / 예비가격 범위
        "a_value": a_value,
        "price_range": price_range,
        "candidate_count": candidate_count,
        "draw_count": draw_count,
        # 분포 데이터
        "distribution": sim["distribution"],
        # 슬롯 선택 빈도 (15개 예비가격 번호별)
        "slot_counts": sim["slot_counts"],
        "slot_ranges": sim["slot_ranges"],
    }


def extract_keyword(bid_name: str) -> str:
    """공고명에서 핵심 키워드 추출"""
    import re
    text = re.sub(r'\d{4}년|\d+차|\(.*?\)|\[.*?\]', '', bid_name)
    stopwords = {
        '용역', '공사', '구매', '입찰', '조달', '공급', '설치', '운영', '관리',
        '사업', '업무', '서비스', '지원', '수행', '추진', '개선', '구축', '및',
        '위한', '대한', '관련', '수립', '용', '의', '을', '를', '에', '의',
    }
    tokens = text.strip().split()
    keywords = [t for t in tokens if t not in stopwords and len(t) >= 2]
    return ' '.join(keywords[:2]) if keywords else ''


def _amount_col(df: pd.DataFrame) -> str | None:
    for col in ["낙찰금액", "추정금액"]:
        if col in df.columns:
            return col
    return None


def filter_by_amount_log(df: pd.DataFrame, base_price: float, half_decade: float = 0.5) -> pd.DataFrame:
    """
    로그 스케일 금액 필터 (선형 ±% 보다 합리적)
    half_decade=0.5 → base_price * 10^(-0.5) ~ base_price * 10^(0.5)
    즉 기초금액의 약 1/3 ~ 3배 범위
    """
    if df.empty:
        return df
    col = _amount_col(df)
    if not col:
        return df
    lo = base_price * (10 ** -half_decade)
    hi = base_price * (10 ** half_decade)
    filtered = df[(df[col] >= lo) & (df[col] <= hi)]
    return filtered if len(filtered) >= 10 else df


def filter_by_contract_type(df: pd.DataFrame, contract_type: str) -> pd.DataFrame:
    """계약방식 필터 (적격심사 / 최저가 등)"""
    if df.empty or not contract_type or "계약방식" not in df.columns:
        return df
    filtered = df[df["계약방식"].str.contains(contract_type, na=False)]
    return filtered if len(filtered) >= 10 else df


def filter_by_agency(df: pd.DataFrame, agency: str) -> pd.DataFrame:
    """공고기관 필터"""
    if df.empty or not agency or "공고기관" not in df.columns:
        return df
    filtered = df[df["공고기관"].str.contains(agency, na=False)]
    return filtered if len(filtered) >= 5 else df


def apply_time_weight(df: pd.DataFrame) -> pd.DataFrame:
    """
    최근 낙찰 데이터에 가중치 부여 (행 복제 방식)
    개찰일시 기준 6개월 이내 → 2배 가중
    """
    if df.empty or "개찰일시" not in df.columns:
        return df
    try:
        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(days=180)
        dates = pd.to_datetime(df["개찰일시"], errors="coerce")
        recent_mask = dates >= cutoff
        recent = df[recent_mask]
        if len(recent) >= 5:
            return pd.concat([df, recent], ignore_index=True)  # 최근 건 2배 반영
    except Exception:
        pass
    return df


def filter_by_region(df: pd.DataFrame, region: str) -> pd.DataFrame:
    """참가제한지역 필터"""
    if df.empty or not region or "참가제한지역" not in df.columns:
        return df
    filtered = df[df["참가제한지역"].str.contains(region, na=False)]
    return filtered if len(filtered) >= 5 else df


def filter_by_industry(df: pd.DataFrame, industry: str) -> pd.DataFrame:
    """업종 필터 (대소문자 무관)"""
    if df.empty or not industry or "업종" not in df.columns:
        return df
    filtered = df[df["업종"].str.contains(industry, na=False, case=False)]
    return filtered if len(filtered) >= 5 else df


def tiered_filter(
    df: pd.DataFrame,
    base_price: float,
    agency: str = "",
    contract_type: str = "",
    region: str = "",
    industry: str = "",
) -> tuple[pd.DataFrame, str]:
    """
    단계적 필터링 — 엄격한 조건부터 시작해 데이터 부족 시 자동 완화
    Returns: (필터링된 df, 적용된 단계 설명)
    """
    MIN_SAMPLES = 15

    # 지역/업종 사전 필터 (데이터 충분할 때만 적용)
    base_df = df
    pre_filters = []
    if region:
        _r = filter_by_region(df, region)
        if len(_r) >= MIN_SAMPLES:
            base_df = _r
            pre_filters.append(f"지역:{region}")
    if industry:
        _i = filter_by_industry(base_df, industry)
        if len(_i) >= MIN_SAMPLES:
            base_df = _i
            pre_filters.append(f"업종:{industry}")
    pre_label = ("·" + "·".join(pre_filters)) if pre_filters else ""

    # 1단계: 기관 + 계약방식 + 금액 좁게 (±1/3~3배)
    if agency and contract_type:
        t = filter_by_agency(base_df, agency)
        t = filter_by_contract_type(t, contract_type)
        t = filter_by_amount_log(t, base_price, half_decade=0.3)
        if len(t) >= MIN_SAMPLES:
            return apply_time_weight(t), f"1단계: 동일기관·{contract_type}·유사금액{pre_label} {len(t)}건"

    # 2단계: 계약방식 + 금액 (±1/3~3배)
    if contract_type:
        t = filter_by_contract_type(base_df, contract_type)
        t = filter_by_amount_log(t, base_price, half_decade=0.3)
        if len(t) >= MIN_SAMPLES:
            return apply_time_weight(t), f"2단계: {contract_type}·유사금액{pre_label} {len(t)}건"

    # 3단계: 금액만 좁게
    t = filter_by_amount_log(base_df, base_price, half_decade=0.3)
    if len(t) >= MIN_SAMPLES:
        return apply_time_weight(t), f"3단계: 유사금액(±1/3배){pre_label} {len(t)}건"

    # 4단계: 금액 넓게 (±3~10배)
    t = filter_by_amount_log(base_df, base_price, half_decade=0.5)
    if len(t) >= MIN_SAMPLES:
        return apply_time_weight(t), f"4단계: 유사금액(±3배){pre_label} {len(t)}건"

    # 5단계: 전체 사용
    return apply_time_weight(base_df), f"5단계: 전체 {len(base_df)}건{pre_label} (금액 필터 없음)"


def recommend_from_stats(winner_df: pd.DataFrame, expected_price_mean: float, base_price: float | None = None) -> dict | None:
    """
    과거 낙찰 데이터 기반 추천 투찰가 산출
    낙찰률(예정가격 대비 낙찰금액 %) 분포를 분석해 최적 구간 도출
    base_price: 현재 기초금액 — 제공 시 사정률 기반 추천도 함께 산출
    """
    if winner_df.empty or "낙찰률" not in winner_df.columns:
        return None

    rates = winner_df["낙찰률"].dropna()
    if len(rates) < 5:
        return None

    rates = rates[(rates > 50) & (rates <= 110)]  # 이상치 제거
    if len(rates) < 5:
        return None

    # 구간별 빈도 (0.1% 단위)
    bins = np.arange(rates.min() - 0.05, rates.max() + 0.15, 0.1)
    counts, edges = np.histogram(rates, bins=bins)
    peak_idx = counts.argmax()
    mode_low = edges[peak_idx]
    mode_high = edges[peak_idx + 1]

    mean_rate = float(rates.mean())
    median_rate = float(rates.median())
    std_rate = float(rates.std())

    total = len(rates)
    def pct_in_range(lo, hi):
        return round(float(((rates >= lo) & (rates < hi)).sum() / total * 100), 1)

    rec_rate_low  = mode_low / 100
    rec_rate_high = mode_high / 100
    rec_rate_mean = mean_rate / 100

    result = {
        "sample_count": total,
        "mean_rate": mean_rate,
        "median_rate": median_rate,
        "std_rate": std_rate,
        "mode_range": (round(mode_low, 3), round(mode_high, 3)),
        "mode_pct": pct_in_range(mode_low, mode_high),
        "recommend_low":  expected_price_mean * rec_rate_low,
        "recommend_high": expected_price_mean * rec_rate_high,
        "recommend_mean": expected_price_mean * rec_rate_mean,
        "prob_p25_p75": pct_in_range(
            float(np.percentile(rates, 25)),
            float(np.percentile(rates, 75))
        ),
        "rate_p25": float(np.percentile(rates, 25)),
        "rate_p75": float(np.percentile(rates, 75)),
        "rate_distribution": rates.tolist(),
    }

    # 사정률 분석: 낙찰금액/기초금액 — 발주처별 낙찰 경향
    if "사정률" in winner_df.columns:
        sj = winner_df["사정률"].dropna()
        sj = sj[(sj > 50) & (sj <= 110)]
        if len(sj) >= 5:
            sj_bins = np.arange(sj.min() - 0.05, sj.max() + 0.15, 0.1)
            sj_counts, sj_edges = np.histogram(sj, bins=sj_bins)
            sj_peak = sj_counts.argmax()
            result["sajeong_mean"]   = round(float(sj.mean()), 3)
            result["sajeong_median"] = round(float(sj.median()), 3)
            result["sajeong_std"]    = round(float(sj.std()), 3)
            result["sajeong_p25"]    = round(float(np.percentile(sj, 25)), 3)
            result["sajeong_p75"]    = round(float(np.percentile(sj, 75)), 3)
            result["sajeong_mode_range"] = (
                round(float(sj_edges[sj_peak]), 3),
                round(float(sj_edges[sj_peak + 1]), 3),
            )
            result["sajeong_mode_pct"] = round(
                float(((sj >= sj_edges[sj_peak]) & (sj < sj_edges[sj_peak + 1])).sum() / len(sj) * 100), 1
            )
            result["sajeong_distribution"] = sj.tolist()
            # 사정률 기반 추천 투찰가 (기초금액 × 사정률 중앙값)
            if base_price:
                result["sajeong_recommend"] = base_price * result["sajeong_median"] / 100

    return result


def analyze_winner_stats(df: pd.DataFrame, base_price: float | None = None) -> dict:
    """낙찰 데이터 통계 분석"""
    if df.empty:
        return {}

    result = {}

    if "낙찰률" in df.columns:
        rates = df["낙찰률"].dropna()
        result["낙찰률_mean"] = float(rates.mean())
        result["낙찰률_median"] = float(rates.median())
        result["낙찰률_std"] = float(rates.std())
        result["낙찰률_min"] = float(rates.min())
        result["낙찰률_max"] = float(rates.max())
        result["낙찰률_distribution"] = rates.tolist()

    if base_price and "낙찰금액" in df.columns and "기초금액" in df.columns:
        # 사정률 계산 (낙찰금액/기초금액 × 100)
        valid = df[["낙찰금액", "기초금액"]].dropna()
        valid = valid[valid["기초금액"] > 0]
        if not valid.empty:
            sajeong = (valid["낙찰금액"] / valid["기초금액"] * 100)
            result["사정률_mean"] = float(sajeong.mean())
            result["사정률_median"] = float(sajeong.median())
            result["사정률_distribution"] = sajeong.tolist()

    return result


def estimate_competitor_count(winner_df: pd.DataFrame) -> dict | None:
    """
    과거 낙찰 데이터의 참가업체수로 경쟁사 수 추정
    tiered_filter 적용 후 데이터를 넘기면 동일 기관·유사금액 기준으로 추정됨
    """
    if winner_df.empty or "참가업체수" not in winner_df.columns:
        return None
    vals = winner_df["참가업체수"][winner_df["참가업체수"] > 0]
    if len(vals) < 3:
        return None
    return {
        "median": int(round(vals.median())),
        "mean":   round(float(vals.mean()), 1),
        "min":    int(vals.min()),
        "max":    int(vals.max()),
        "sample": len(vals),
    }


def calc_optimal_bid(
    bid_result: dict,
    stats: dict | None = None,
    competitor_count: int = 0,
) -> dict:
    """
    단일 최적 투찰가 산출
    - 안전구간(90%) ∩ 과거통계 범위에서 경쟁률 반영
    - 복수예가 방식: 최저 유효입찰가 낙찰 → 경쟁 많을수록 하단 노림
    """
    low_90  = bid_result["safe_low_p90"]
    high_90 = bid_result["safe_high_p90"]
    base    = bid_result["base_price"]
    dist    = np.array(bid_result["distribution"])
    lr      = bid_result["lower_rate_pct"] / 100

    # 1. 범위 결정: 안전구간(90%) ∩ 과거통계 (사정률 우선 반영)
    if stats:
        # 사정률 기반 추천가가 있으면 이를 중심으로 타겟 밴드 형성 (발주처 고유 성향 우선)
        if stats.get("sajeong_recommend") and stats["sajeong_recommend"] > 0:
            target_bid = stats["sajeong_recommend"]
            _band_half = (stats["recommend_high"] - stats["recommend_low"]) / 2
            opt_low  = max(target_bid - _band_half, low_90)
            opt_high = min(target_bid + _band_half, high_90)
        else:
            opt_low  = max(stats["recommend_low"],  low_90)
            opt_high = min(stats["recommend_high"], high_90)
        if opt_low >= opt_high:
            opt_low, opt_high = low_90, high_90
    else:
        opt_low, opt_high = low_90, high_90

    # 2. 경쟁률 기반 포지션 (0=하한, 1=상한)
    if competitor_count <= 0:
        position = 0.35
        comp_label = "경쟁사 수 미입력"
    elif competitor_count == 1:
        position = 0.65
        comp_label = "단독 입찰"
    elif competitor_count <= 3:
        position = 0.55
        comp_label = f"{competitor_count}개사 (소수 경쟁)"
    elif competitor_count <= 5:
        position = 0.42
        comp_label = f"{competitor_count}개사 (보통 경쟁)"
    elif competitor_count <= 10:
        position = 0.30
        comp_label = f"{competitor_count}개사 (경쟁 심함)"
    elif competitor_count <= 20:
        position = 0.20
        comp_label = f"{competitor_count}개사 (매우 경쟁)"
    else:
        position = 0.12
        comp_label = f"{competitor_count}개사 (치열)"

    optimal = opt_low + (opt_high - opt_low) * position
    optimal = max(opt_low, min(opt_high, optimal))

    # 3. 유효 확률 및 사정률
    _av = bid_result.get("a_value", 0.0)
    if _av > 0:
        _floors = np.ceil(np.round((dist - _av) * lr + _av, 5))
    else:
        _floors = np.ceil(np.round(dist * lr, 5))
    valid_prob = float(((optimal >= _floors) & (optimal <= dist)).mean() * 100)
    sajeong    = optimal / base * 100

    # 단일 최적 투찰가 ±1% 협폭 범위 (opt_low/opt_high는 산출 근거 범위)
    band_low  = max(opt_low,  optimal * 0.990)
    band_high = min(opt_high, optimal * 1.010)

    return {
        "optimal_bid":      optimal,
        "sajeong":          sajeong,
        "valid_prob":       valid_prob,
        "opt_low":          band_low,
        "opt_high":         band_high,
        "position":         position,
        "comp_label":       comp_label,
        "competitor_count": competitor_count,
    }


def format_won(amount: float) -> str:
    """금액 포맷 (억/만원 단위 — 간략 표시용)"""
    if amount >= 1_0000_0000:
        return f"{amount / 1_0000_0000:.2f}억원"
    elif amount >= 10000:
        return f"{amount / 10000:.0f}만원"
    return f"{amount:,.0f}원"


def format_won_exact(amount: float) -> str:
    """금액 포맷 (1원 단위 정확 표시)"""
    return f"{int(amount):,}원"
