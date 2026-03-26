import os
try:
    import streamlit as st
    API_KEY = st.secrets.get("G2B_API_KEY", os.environ.get("G2B_API_KEY", ""))
except Exception:
    API_KEY = os.environ.get("G2B_API_KEY", "")

BASE_URL = "https://apis.data.go.kr/1230000"

# 낙찰하한율 (예정가격 대비 %)
AWARD_LOWER_RATE = {
    "공사": 87.745,
    "용역": 88.0,
    "물품": 80.0,
}

# 복수예가 범위 (기초금액 대비 %)
MULTIPLE_PRICE_RANGE = {
    "min": -2.0,
    "max": 3.0,
    "count": 15,
}
