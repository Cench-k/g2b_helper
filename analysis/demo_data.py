"""
API 키 미승인 또는 오프라인 상태일 때 사용하는 데모 데이터
실제 나라장터 낙찰 패턴을 반영한 샘플 데이터
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import random


def get_demo_bid_list(bid_type: str = "용역", rows: int = 20) -> pd.DataFrame:
    random.seed(42)
    orgs = ["서울특별시", "경기도", "인천광역시", "부산광역시", "대구광역시",
            "한국수자원공사", "한국도로공사", "국방부", "교육부", "환경부"]
    keywords = {
        "용역": ["소프트웨어 개발", "시스템 유지보수", "컨설팅", "설계 용역", "조사 연구",
                 "홈페이지 구축", "DB 구축", "보안 점검", "감리", "교육훈련"],
        "물품": ["사무용품 구매", "PC 구매", "서버 도입", "의료기기", "차량 구매",
                 "소모품 구매", "전산장비", "실험장비", "방호장비", "청소용품"],
        "공사": ["도로 보수", "건물 신축", "시설 개보수", "전기공사", "배관공사",
                 "환경 정비", "조경공사", "수도시설", "하수도", "교량 보수"],
    }
    now = datetime.now()
    records = []
    for i in range(rows):
        base = random.randint(50_000_000, 5_000_000_000)
        kw = random.choice(keywords.get(bid_type, keywords["용역"]))
        org = random.choice(orgs)
        bid_start = now - timedelta(days=random.randint(0, 7))
        bid_close = bid_start + timedelta(days=random.randint(5, 14))
        records.append({
            "공고번호": f"2026{str(i+1).zfill(8)}",
            "공고명": f"{kw} ({org})",
            "공고기관": org,
            "수요기관": org,
            "추정금액": base,
            "입찰시작일": bid_start.strftime("%Y/%m/%d %H:%M"),
            "입찰마감일": bid_close.strftime("%Y/%m/%d %H:%M"),
            "입찰방식": random.choice(["일반경쟁", "제한경쟁", "지명경쟁"]),
            "계약방식": random.choice(["최저가낙찰제", "적격심사"]),
        })
    return pd.DataFrame(records)


def get_demo_bid_by_no(bid_no: str) -> dict:
    """공고번호 기반 데모 공고 데이터 (API 미연결 시)"""
    import hashlib
    seed = int(hashlib.md5(bid_no.encode()).hexdigest(), 16) % 10000
    random.seed(seed)

    bid_types = ["용역", "물품", "공사"]
    bid_type = bid_types[seed % 3]
    base_price = random.randint(5, 500) * 10_000_000

    DEFAULT_RATES = {"용역": 88.0, "물품": 80.0, "공사": 87.745}
    orgs = ["서울특별시", "경기도", "한국수자원공사", "국방부", "교육부"]
    keywords = {"용역": "소프트웨어 개발 용역", "물품": "사무용 PC 구매", "공사": "청사 리모델링 공사"}

    return {
        "공고번호": bid_no,
        "공고명": f"[데모] {keywords[bid_type]} ({bid_no})",
        "공고기관": random.choice(orgs),
        "수요기관": random.choice(orgs),
        "기초금액": base_price,
        "추정금액": int(base_price * random.uniform(0.98, 1.05)),
        "낙찰하한율": DEFAULT_RATES[bid_type],
        "입찰방식": "일반경쟁",
        "계약방식": "적격심사",
        "입찰마감일": "",
        "개찰일시": "",
        "공사종류": bid_type,
        "_is_demo": True,
    }


def get_demo_winner_list(bid_type: str = "용역", rows: int = 100) -> pd.DataFrame:
    """
    실제 나라장터 낙찰 패턴 기반 데모 데이터
    낙찰률 분포: 용역 88~91%, 물품 80~88%, 공사 87~90%
    """
    np.random.seed(42)
    rate_params = {
        "용역": (88.5, 1.2),
        "물품": (83.0, 2.5),
        "공사": (88.2, 0.8),
    }
    orgs = ["서울특별시", "경기도", "인천광역시", "부산광역시",
            "한국수자원공사", "한국도로공사", "국방부", "교육부"]
    companies = [f"(주){x}기술" for x in ["한국", "대한", "서울", "동국", "미래",
                                          "정보", "시스템", "솔루션", "테크", "코리아"]]
    regions = ["서울특별시", "경기도", "인천광역시", "부산광역시", "대구광역시",
               "대전광역시", "광주광역시", "울산광역시", "경상남도", "경상북도",
               "전라남도", "충청남도", "제주특별자치도", "전국"]
    industries_by_type = {
        "용역": ["IT서비스", "시설관리용역", "청소용역", "경비용역", "연구용역",
                 "감리용역", "설계용역", "홍보용역", "교육훈련", "환경용역"],
        "물품": ["전산장비", "사무용품", "차량", "의료기기", "실험장비",
                 "소방장비", "통신장비", "건설장비", "생활용품", "방산물자"],
        "공사": ["건축공사", "토목공사", "전기공사", "통신공사", "소방공사",
                 "상하수도공사", "조경공사", "철도공사", "도로포장", "리모델링"],
    }

    # 공사종류별 현실적 경쟁사 수 분포
    comp_params = {
        "용역": (7, 4),
        "물품": (5, 3),
        "공사": (10, 5),
    }
    mu, sigma = rate_params.get(bid_type, (88.5, 1.2))
    comp_mu, comp_sigma = comp_params.get(bid_type, (7, 4))
    rates = np.random.normal(mu, sigma, rows)
    rates = np.clip(rates, mu - 3, mu + 5)
    comp_counts = np.random.normal(comp_mu, comp_sigma, rows).clip(1, 50).astype(int)

    now = datetime.now()
    industry_list = industries_by_type.get(bid_type, industries_by_type["용역"])
    records = []
    for i, rate in enumerate(rates):
        base = np.random.randint(50_000_000, 3_000_000_000, dtype=np.int64)
        estimated_price = base * np.random.uniform(0.98, 1.03)
        award_amount = estimated_price * (rate / 100)
        open_date = now - timedelta(days=np.random.randint(1, 90))
        records.append({
            "공고번호":     f"2026{str(i+1).zfill(8)}",
            "공고명":      f"데모 공고 {i+1}",
            "공고기관":    np.random.choice(orgs),
            "추정금액":    int(base),
            "예정가격":    int(estimated_price),
            "낙찰금액":    int(award_amount),
            "낙찰률":      round(float(rate), 3),
            "낙찰업체명":   np.random.choice(companies),
            "참가업체수":   int(comp_counts[i]),
            "개찰일시":    open_date.strftime("%Y/%m/%d %H:%M"),
            "참가제한지역": np.random.choice(regions),
            "업종":        np.random.choice(industry_list),
        })
    return pd.DataFrame(records)
