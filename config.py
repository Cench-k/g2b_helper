API_KEY = "be409a58c354adca5848ee8ba7349582d68c447d4b4ec3f4b95dd10dc0eaacee"
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
