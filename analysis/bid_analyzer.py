import numpy as np
import pandas as pd
from config import AWARD_LOWER_RATE, MULTIPLE_PRICE_RANGE


def calc_multiple_prices(base_price: float, count: int = 15) -> list[float]:
    """복수예가 후보값 15개 생성 (기초금액 ±2~3% 범위)"""
    low = base_price * (1 + MULTIPLE_PRICE_RANGE["min"] / 100)
    high = base_price * (1 + MULTIPLE_PRICE_RANGE["max"] / 100)
    return [round(low + (high - low) * i / (count - 1)) for i in range(count)]


def simulate_expected_price(
    base_price: float,
    simulations: int = 10000,
    candidate_count: int = 15,
    draw_count: int = 2,
) -> dict:
    """
    몬테카를로 시뮬레이션으로 예정가격 분포 계산
    나라장터 복수예가: candidate_count개 후보 중 draw_count개 추첨 후 평균 = 예정가격
    (공고마다 다름: totPrdprcNum / drwtPrdprcNum 으로 결정)
    """
    candidates = calc_multiple_prices(base_price, count=candidate_count)
    draw = min(draw_count, len(candidates))
    results = []
    rng = np.random.default_rng(42)
    for _ in range(simulations):
        chosen = rng.choice(candidates, size=draw, replace=False)
        results.append(chosen.mean())
    arr = np.array(results)
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
    }


def calc_bid_range(
    base_price: float,
    bid_type: str = "용역",
    custom_lower_rate: float | None = None,
    candidate_count: int = 15,
    draw_count: int = 2,
) -> dict:
    """
    낙찰 예상 범위 계산 + 안전구간(어떤 예정가격에도 유효한 구간) 산출
    """
    sim = simulate_expected_price(base_price, candidate_count=candidate_count, draw_count=draw_count)
    lower_rate_pct = custom_lower_rate if custom_lower_rate is not None else AWARD_LOWER_RATE.get(bid_type, 88.0)
    lower_rate = lower_rate_pct / 100

    expected_mean = sim["mean"]
    expected_min  = sim["min"]
    expected_max  = sim["max"]
    expected_p10  = sim["p10"]
    expected_p90  = sim["p90"]

    # 낙찰하한금액 범위
    award_floor_min  = expected_min * lower_rate   # 예정가 최소일 때 하한금액
    award_floor_mean = expected_mean * lower_rate
    award_floor_max  = expected_max * lower_rate   # 예정가 최대일 때 하한금액

    # ── 안전구간 ──────────────────────────────────────────────────────
    # 어떤 예정가격이 나와도 투찰이 유효(낙찰 자격)한 구간
    #   하한: max(낙찰하한금액) = 최대 예정가격 × 낙찰하한율
    #         → 이 이상이면 절대 하한 미달 없음
    #   상한: min(예정가격)
    #         → 이 이하면 절대 예정가 초과 없음
    safe_low  = award_floor_max          # 안전구간 하한 (= 최대 낙찰하한금액)
    safe_high = expected_min             # 안전구간 상한 (= 최소 예정가격)
    safe_mid  = (safe_low + safe_high) / 2
    safe_exists = safe_high > safe_low   # 안전구간이 존재하는지 여부

    # 90% 확률 안전구간 (극단값 제외 — 현실적 안전구간)
    safe_low_p90  = expected_p90 * lower_rate
    safe_high_p90 = expected_p10
    safe_mid_p90  = (safe_low_p90 + safe_high_p90) / 2

    # 각 투찰가별 유효 확률 계산 (시뮬레이션 분포 기반)
    dist = np.array(sim["distribution"])
    def valid_prob(bid_price: float) -> float:
        """해당 투찰가가 유효할 확률 (낙찰하한 이상 AND 예정가 이하)"""
        valid = ((bid_price >= dist * lower_rate) & (bid_price <= dist))
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
        # 분포 데이터
        "distribution": sim["distribution"],
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


def tiered_filter(
    df: pd.DataFrame,
    base_price: float,
    agency: str = "",
    contract_type: str = "",
) -> tuple[pd.DataFrame, str]:
    """
    단계적 필터링 — 엄격한 조건부터 시작해 데이터 부족 시 자동 완화
    Returns: (필터링된 df, 적용된 단계 설명)
    """
    MIN_SAMPLES = 15

    # 1단계: 기관 + 계약방식 + 금액 좁게 (±1/3~3배)
    if agency and contract_type:
        t = filter_by_agency(df, agency)
        t = filter_by_contract_type(t, contract_type)
        t = filter_by_amount_log(t, base_price, half_decade=0.3)
        if len(t) >= MIN_SAMPLES:
            return apply_time_weight(t), f"1단계: 동일기관·{contract_type}·유사금액 {len(t)}건"

    # 2단계: 계약방식 + 금액 (±1/3~3배)
    if contract_type:
        t = filter_by_contract_type(df, contract_type)
        t = filter_by_amount_log(t, base_price, half_decade=0.3)
        if len(t) >= MIN_SAMPLES:
            return apply_time_weight(t), f"2단계: {contract_type}·유사금액 {len(t)}건"

    # 3단계: 금액만 좁게
    t = filter_by_amount_log(df, base_price, half_decade=0.3)
    if len(t) >= MIN_SAMPLES:
        return apply_time_weight(t), f"3단계: 유사금액(±1/3배) {len(t)}건"

    # 4단계: 금액 넓게 (±3~10배)
    t = filter_by_amount_log(df, base_price, half_decade=0.5)
    if len(t) >= MIN_SAMPLES:
        return apply_time_weight(t), f"4단계: 유사금액(±3배) {len(t)}건"

    # 5단계: 전체 사용
    return apply_time_weight(df), f"5단계: 전체 {len(df)}건 (금액 필터 없음)"


def recommend_from_stats(winner_df: pd.DataFrame, expected_price_mean: float) -> dict | None:
    """
    과거 낙찰 데이터 기반 추천 투찰가 산출
    낙찰률(예정가격 대비 낙찰금액 %) 분포를 분석해 최적 구간 도출
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

    # 각 구간별 낙찰 확률
    total = len(rates)
    def pct_in_range(lo, hi):
        return round(float(((rates >= lo) & (rates < hi)).sum() / total * 100), 1)

    # 추천가: 최빈 낙찰률 구간을 예정가격에 적용
    rec_rate_low  = mode_low / 100
    rec_rate_high = mode_high / 100
    rec_rate_mean = mean_rate / 100

    return {
        "sample_count": total,
        "mean_rate": mean_rate,
        "median_rate": median_rate,
        "std_rate": std_rate,
        "mode_range": (round(mode_low, 3), round(mode_high, 3)),
        "mode_pct": pct_in_range(mode_low, mode_high),
        # 추천 투찰가 (예정가격 평균 기준)
        "recommend_low":  expected_price_mean * rec_rate_low,
        "recommend_high": expected_price_mean * rec_rate_high,
        "recommend_mean": expected_price_mean * rec_rate_mean,
        # 확률 구간
        "prob_p25_p75": pct_in_range(
            float(np.percentile(rates, 25)),
            float(np.percentile(rates, 75))
        ),
        "rate_p25": float(np.percentile(rates, 25)),
        "rate_p75": float(np.percentile(rates, 75)),
        "rate_distribution": rates.tolist(),
    }


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

    if base_price and "낙찰금액" in df.columns and "추정금액" in df.columns:
        # 사정률 계산 (낙찰금액/추정금액 × 100)
        valid = df[["낙찰금액", "추정금액"]].dropna()
        if not valid.empty:
            sajeong = (valid["낙찰금액"] / valid["추정금액"] * 100)
            result["사정률_mean"] = float(sajeong.mean())
            result["사정률_median"] = float(sajeong.median())
            result["사정률_distribution"] = sajeong.tolist()

    return result


def format_won(amount: float) -> str:
    """금액 포맷 (억/만원 단위)"""
    if amount >= 1_0000_0000:
        return f"{amount / 1_0000_0000:.2f}억원"
    elif amount >= 10000:
        return f"{amount / 10000:.0f}만원"
    return f"{amount:,.0f}원"
