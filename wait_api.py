"""
API 연결 대기 스크립트
연결되면 소리 + 메시지로 알려줍니다
"""
import requests
import time
import os
from datetime import datetime

KEY = "be409a58c354adca5848ee8ba7349582d68c447d4b4ec3f4b95dd10dc0eaacee"
CHECK_INTERVAL = 120  # 2분마다 체크

def test_api() -> bool:
    url = (
        f"https://apis.data.go.kr/1230000/BidPublicInfoService04/getBidPblancListInfoServc"
        f"?serviceKey={KEY}&numOfRows=1&pageNo=1&type=json"
        f"&inqryDiv=1&inqryBgnDt=20260301000000&inqryEndDt=20260326235959"
    )
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200 and "response" in r.text:
            return True
    except Exception:
        pass
    return False

print("=" * 50)
print("나라장터 API 연결 대기 중...")
print(f"2분마다 자동 체크합니다. Ctrl+C로 중단.")
print("=" * 50)

attempt = 1
while True:
    now = datetime.now().strftime("%H:%M:%S")
    ok = test_api()
    if ok:
        print(f"\n✅ [{now}] API 연결 성공!")
        # Windows 알림음
        for _ in range(5):
            os.system("powershell -c (New-Object Media.SoundPlayer).PlaySync()")
            time.sleep(0.3)
        print("앱을 새로고침하거나 사이드바의 [🔄 API 재연결] 버튼을 눌러주세요.")
        break
    else:
        print(f"[{now}] 시도 {attempt}회 — 아직 대기 중...")
        attempt += 1
        time.sleep(CHECK_INTERVAL)
