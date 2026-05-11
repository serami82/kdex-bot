"""
KODEX200 매매 신호 알림 봇 (토큰 자동 갱신)
============================================
- 코스피 120일 MA 방향으로 매수/매도 신호 판단
- 매일 카카오톡 나에게 보내기로 알림
- 카카오 토큰 자동 갱신 (6시간마다)

설치:
  pip install yfinance requests python-dotenv
"""

import os
import json
import datetime
import requests
import yfinance as yf
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

KAKAO_TOKEN         = os.getenv("KAKAO_TOKEN", "")
REFRESH_TOKEN       = os.getenv("REFRESH_TOKEN", "")
KAKAO_CLIENT_ID     = "3f270568304f0fe40e51a777536559e9"
KAKAO_CLIENT_SECRET = "8euPa0JN7Su2N7PQwFYCgWTd38VOyP6v"

MA_PERIOD     = 120
DAILY_BUY     = 100_000
PROFIT_TARGET = 0.06

TOKEN_FILE = Path("kakao_token_my.json")


# ══════════════════════════════════════════════════════════════════
#  0. 카카오 토큰 자동 갱신
# ══════════════════════════════════════════════════════════════════

def load_token() -> str:
    if TOKEN_FILE.exists():
        try:
            data    = json.loads(TOKEN_FILE.read_text())
            issued  = datetime.datetime.fromisoformat(data["issued_at"])
            expires = issued + datetime.timedelta(seconds=data["expires_in"] - 300)
            if datetime.datetime.now() < expires:
                remain = int((expires - datetime.datetime.now()).seconds / 60)
                print(f"[TOKEN] 기존 토큰 유효 (만료까지 {remain}분)")
                return data["access_token"]
            print("[TOKEN] 토큰 만료 임박 → 자동 갱신")
            return refresh_access_token(data["refresh_token"])
        except Exception as e:
            print(f"[TOKEN] 파일 로드 실패: {e}")

    if KAKAO_TOKEN:
        print("[TOKEN] 환경변수 토큰 사용 → 파일 저장")
        save_token(KAKAO_TOKEN, REFRESH_TOKEN, 21599)
        return KAKAO_TOKEN

    raise ValueError("카카오 토큰 없음 — KAKAO_TOKEN Secret 확인 필요")


def refresh_access_token(refresh_token: str) -> str:
    res = requests.post(
        "https://kauth.kakao.com/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type":    "refresh_token",
            "client_id":     KAKAO_CLIENT_ID,
            "client_secret": KAKAO_CLIENT_SECRET,
            "refresh_token": refresh_token,
        },
        timeout=10
    )
    res.raise_for_status()
    data = res.json()
    if "error" in data:
        raise ValueError(f"토큰 갱신 실패: {data}")

    new_access  = data["access_token"]
    new_refresh = data.get("refresh_token", refresh_token)
    expires_in  = data.get("expires_in", 21599)
    save_token(new_access, new_refresh, expires_in)
    print("[TOKEN] 갱신 완료 ✓")
    return new_access


def save_token(access: str, refresh: str, expires_in: int):
    TOKEN_FILE.write_text(json.dumps({
        "access_token":  access,
        "refresh_token": refresh,
        "expires_in":    expires_in,
        "issued_at":     datetime.datetime.now().isoformat(),
    }, ensure_ascii=False))


# ══════════════════════════════════════════════════════════════════
#  1. 데이터 조회
# ══════════════════════════════════════════════════════════════════

def get_kospi_closes() -> list:
    ticker = yf.Ticker("^KS11")
    df = ticker.history(period="200d")
    if df.empty:
        raise ValueError("코스피 데이터 조회 실패")
    return df["Close"].tolist()


def get_kodex200_price() -> float:
    ticker = yf.Ticker("069500.KS")
    info = ticker.fast_info
    return float(info.last_price)


# ══════════════════════════════════════════════════════════════════
#  2. MA120 방향 판단
# ══════════════════════════════════════════════════════════════════

def check_ma120():
    closes = get_kospi_closes()
    if len(closes) < MA_PERIOD + 1:
        raise ValueError("데이터 부족")
    today_ma     = sum(closes[-MA_PERIOD:]) / MA_PERIOD
    yesterday_ma = sum(closes[-MA_PERIOD - 1:-1]) / MA_PERIOD
    return today_ma > yesterday_ma, today_ma, yesterday_ma


# ══════════════════════════════════════════════════════════════════
#  3. 카카오톡 전송
# ══════════════════════════════════════════════════════════════════

def send_kakao(message: str) -> bool:
    try:
        token = load_token()
    except Exception as e:
        print(f"[TOKEN] 토큰 로드 실패: {e}")
        return False

    res = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"template_object": json.dumps({
            "object_type": "text",
            "text": message,
            "link": {"web_url": "", "mobile_web_url": ""},
        }, ensure_ascii=False)},
        timeout=10
    )
    ok = res.status_code == 200
    print(f"[KAKAO] {'전송 성공 ✓' if ok else f'실패: {res.text}'}")
    return ok


# ══════════════════════════════════════════════════════════════════
#  4. 메시지 생성
# ══════════════════════════════════════════════════════════════════

def build_message(uptrend: bool, today_ma: float, yesterday_ma: float, price: float) -> str:
    now   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    diff  = today_ma - yesterday_ma
    arrow = "▲" if uptrend else "▼"
    trend = "상향" if uptrend else "하향"

    if uptrend:
        action        = "✅ 매수 신호"
        action_detail = (
            f"📌 오늘 할 일: KODEX200 {DAILY_BUY:,}원어치 매수\n"
            f"💡 목표 수익률 +{PROFIT_TARGET*100:.0f}% 달성 시 매도 후 재매수"
        )
    else:
        action        = "🚨 매도 신호"
        action_detail = (
            "📌 오늘 할 일: 보유 중인 KODEX200 전량 매도\n"
            "💡 MA120 상향 전환 시 재매수"
        )

    return "\n".join([
        "📊 [KODEX200 매매 신호]",
        f"🕐 {now}",
        "━━━━━━━━━━━━━━━━━━━",
        f"{'📈' if uptrend else '📉'} 코스피 120MA: {trend} {arrow}",
        f"   어제 {yesterday_ma:,.2f} → 오늘 {today_ma:,.2f}  ({diff:+.2f})",
        f"💰 KODEX200 현재가: {price:,.0f}원",
        "",
        action,
        action_detail,
        "━━━━━━━━━━━━━━━━━━━",
        "※ 본 신호는 참고용이며 투자 책임은 본인에게 있습니다.",
    ])


# ══════════════════════════════════════════════════════════════════
#  5. 메인 실행
# ══════════════════════════════════════════════════════════════════

def run():
    print(f"[RUN] {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    try:
        uptrend, today_ma, yesterday_ma = check_ma120()
        price = get_kodex200_price()

        print(f"[MA120] 어제={yesterday_ma:.2f} 오늘={today_ma:.2f} {'▲상향' if uptrend else '▼하향'}")
        print(f"[PRICE] KODEX200 {price:,.0f}원")

        msg = build_message(uptrend, today_ma, yesterday_ma, price)
        print(msg)
        send_kakao(msg)

    except Exception as e:
        err = (
            f"⚠️ [KODEX200 봇 오류]\n"
            f"🕐 {datetime.datetime.now():%Y-%m-%d %H:%M}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"❌ {type(e).__name__}: {e}"
        )
        print(f"[ERROR] {e}")
        try:
            send_kakao(err)
        except Exception:
            pass


if __name__ == "__main__":
    run()
