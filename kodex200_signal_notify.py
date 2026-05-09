"""
KODEX200 매매 신호 알림 봇 (계좌 불필요)
=========================================
- 코스피 120일 MA 방향으로 매수/매도 신호 판단
- 매일 카카오톡 나에게 보내기로 알림
- 카카오 토큰 만료 7일 전 경고 알림
- 데이터: Yahoo Finance (무료, 계좌 불필요)

설치:
  pip install yfinance requests python-dotenv

.env 파일:
  KAKAO_TOKEN=카카오_액세스_토큰
  TOKEN_ISSUED_DATE=2026-05-09   ← 오늘 날짜 (토큰 발급일)
"""

import os
import json
import datetime
import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

KAKAO_TOKEN       = os.getenv("KAKAO_TOKEN", "YOUR_KAKAO_TOKEN")
TOKEN_ISSUED_DATE = os.getenv("TOKEN_ISSUED_DATE", "")   # 예: 2026-05-09
MA_PERIOD         = 120
DAILY_BUY         = 100_000
PROFIT_TARGET     = 0.06
TOKEN_EXPIRE_DAYS = 30   # 카카오 토큰 만료일
TOKEN_WARN_DAYS   = 7    # 만료 며칠 전부터 경고


# ══════════════════════════════════════════════════════════════════
#  1. 데이터 조회 (Yahoo Finance — 무료, 계좌 불필요)
# ══════════════════════════════════════════════════════════════════

def get_kospi_closes() -> list[float]:
    """코스피 지수 최근 130일 종가"""
    ticker = yf.Ticker("^KS11")
    df = ticker.history(period="200d")
    if df.empty:
        raise ValueError("코스피 데이터 조회 실패")
    return df["Close"].tolist()


def get_kodex200_price() -> float:
    """KODEX200 현재가"""
    ticker = yf.Ticker("069500.KS")
    info = ticker.fast_info
    return float(info.last_price)


# ══════════════════════════════════════════════════════════════════
#  2. MA120 방향 판단
# ══════════════════════════════════════════════════════════════════

def check_ma120() -> tuple[bool, float, float]:
    """returns: (is_uptrend, today_ma, yesterday_ma)"""
    closes = get_kospi_closes()
    if len(closes) < MA_PERIOD + 1:
        raise ValueError("데이터 부족")
    today_ma     = sum(closes[-MA_PERIOD:]) / MA_PERIOD
    yesterday_ma = sum(closes[-MA_PERIOD - 1:-1]) / MA_PERIOD
    return today_ma > yesterday_ma, today_ma, yesterday_ma


# ══════════════════════════════════════════════════════════════════
#  3. 카카오 토큰 만료일 체크
# ══════════════════════════════════════════════════════════════════

def check_token_expiry():
    """
    토큰 발급일로부터 남은 일수 계산
    returns: (남은일수, 만료일문자열) or None
    """
    if not TOKEN_ISSUED_DATE:
        return None
    try:
        issued = datetime.datetime.strptime(TOKEN_ISSUED_DATE, "%Y-%m-%d").date()
        expire = issued + datetime.timedelta(days=TOKEN_EXPIRE_DAYS)
        remain = (expire - datetime.date.today()).days
        return remain, expire.strftime("%Y-%m-%d")
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
#  4. 카카오톡 전송
# ══════════════════════════════════════════════════════════════════

def send_kakao(message: str) -> bool:
    res = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={
            "Authorization": f"Bearer {KAKAO_TOKEN}",
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
#  5. 메시지 생성
# ══════════════════════════════════════════════════════════════════

def build_message(uptrend: bool, today_ma: float, yesterday_ma: float,
                  price: float, token_info) -> str:
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

    # 토큰 만료 안내
    if token_info:
        remain, expire_date = token_info
        if remain <= 0:
            token_line = f"\n⛔ 카카오 토큰 만료됨! 즉시 재발급 필요"
        elif remain <= TOKEN_WARN_DAYS:
            token_line = f"\n⚠️ 카카오 토큰 {remain}일 후 만료 ({expire_date}) — 재발급 필요"
        else:
            token_line = f"\n🔑 토큰 만료까지 {remain}일 ({expire_date})"
    else:
        token_line = ""

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
        "※ 본 신호는 참고용이며 투자 책임은 본인에게 있습니다." + token_line,
    ])


# ══════════════════════════════════════════════════════════════════
#  6. 메인 실행
# ══════════════════════════════════════════════════════════════════

def run():
    print(f"[RUN] {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    try:
        uptrend, today_ma, yesterday_ma = check_ma120()
        price = get_kodex200_price()
        token_info = check_token_expiry()

        print(f"[MA120] 어제={yesterday_ma:.2f} 오늘={today_ma:.2f} {'▲상향' if uptrend else '▼하향'}")
        print(f"[PRICE] KODEX200 {price:,.0f}원")
        if token_info:
            print(f"[TOKEN] 만료까지 {token_info[0]}일 ({token_info[1]})")

        msg = build_message(uptrend, today_ma, yesterday_ma, price, token_info)
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
