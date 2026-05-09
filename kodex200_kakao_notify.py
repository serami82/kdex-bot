"""
KODEX200 자동매매 + 카카오톡 알림 통합 봇
==========================================
전략:
  - 코스피 120일 MA 상향 → 매일 10만원 매수
  - 수익률 6% 달성 → 전량 익절 후 재매수
  - 코스피 120일 MA 하향 → 전량 매도

알림: 카카오톡 나에게 보내기 (매일 09:05)

설치:
  pip install requests pandas schedule python-dotenv

.env 파일 예시:
  KIS_APP_KEY=your_app_key
  KIS_APP_SECRET=your_app_secret
  KIS_ACCOUNT_NO=00000000-01
  KIS_IS_REAL=false
  KAKAO_TOKEN=your_kakao_access_token
"""

import os
import json
import time
import datetime
import requests
import pandas as pd
import schedule
from dotenv import load_dotenv

load_dotenv()

# ── 환경변수 ────────────────────────────────────────────────────────────────
APP_KEY    = os.getenv("KIS_APP_KEY", "YOUR_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET", "YOUR_APP_SECRET")
ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "00000000-01")
IS_REAL    = os.getenv("KIS_IS_REAL", "false").lower() == "true"
KAKAO_TOKEN = os.getenv("KAKAO_TOKEN", "YOUR_KAKAO_TOKEN")

BASE_URL = "https://openapi.koreainvestment.com:9443" if IS_REAL \
           else "https://openapivts.koreainvestment.com:29443"

# ── 전략 파라미터 ────────────────────────────────────────────────────────────
DAILY_BUY_KRW  = 100_000   # 매일 매수 금액
PROFIT_TARGET  = 0.06      # 익절 목표 (6%)
MA_PERIOD      = 120       # 이동평균 기간
KODEX200_CODE  = "069500"  # KODEX 200
KOSPI_IDX_CD   = "0001"    # 코스피 지수

# ── 전역 토큰 캐시 ───────────────────────────────────────────────────────────
_access_token     = None
_token_expired_at = None


# ══════════════════════════════════════════════════════════════════════════════
#  1. KIS 인증
# ══════════════════════════════════════════════════════════════════════════════

def get_access_token() -> str:
    global _access_token, _token_expired_at
    now = datetime.datetime.now()
    if _access_token and _token_expired_at and now < _token_expired_at:
        return _access_token

    res = requests.post(
        f"{BASE_URL}/oauth2/tokenP",
        json={"grant_type": "client_credentials",
              "appkey": APP_KEY, "appsecret": APP_SECRET},
        timeout=10
    )
    res.raise_for_status()
    data = res.json()
    _access_token = data["access_token"]
    expires_in = int(data.get("expires_in", 86400))
    _token_expired_at = now + datetime.timedelta(seconds=expires_in - 300)
    return _access_token


def _h(tr_id: str) -> dict:
    return {
        "Content-Type": "application/json",
        "authorization": f"Bearer {get_access_token()}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": tr_id,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  2. 시세 조회
# ══════════════════════════════════════════════════════════════════════════════

def get_current_price(code: str) -> float:
    res = requests.get(
        f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
        headers=_h("FHKST01010100"),
        params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        timeout=10
    )
    res.raise_for_status()
    return float(res.json()["output"]["stck_prpr"])


def get_kospi_daily_close(days: int = 150) -> pd.Series:
    today = datetime.date.today()
    start = (today - datetime.timedelta(days=days * 2)).strftime("%Y%m%d")
    end   = today.strftime("%Y%m%d")

    res = requests.get(
        f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-daily-price",
        headers=_h("FHKUP03500100"),
        params={
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": KOSPI_IDX_CD,
            "FID_INPUT_DATE_1": start,
            "FID_INPUT_DATE_2": end,
            "FID_PERIOD_DIV_CODE": "D",
        },
        timeout=10
    )
    res.raise_for_status()
    output = res.json().get("output2", [])
    if not output:
        raise ValueError("코스피 일별 데이터 조회 실패")

    df = pd.DataFrame(output)
    df["date"]  = pd.to_datetime(df["stck_bsop_date"])
    df["close"] = df["bstp_nmix_prpr"].astype(float)
    return df.sort_values("date").set_index("date")["close"].tail(days)


# ══════════════════════════════════════════════════════════════════════════════
#  3. MA120 방향 판단
# ══════════════════════════════════════════════════════════════════════════════

def check_ma120() -> tuple[bool, float, float]:
    """
    returns: (is_uptrend, today_ma, yesterday_ma)
    """
    closes = get_kospi_daily_close(MA_PERIOD + 5)
    ma = closes.rolling(MA_PERIOD).mean().dropna()
    if len(ma) < 2:
        return False, 0.0, 0.0
    return ma.iloc[-1] > ma.iloc[-2], float(ma.iloc[-1]), float(ma.iloc[-2])


# ══════════════════════════════════════════════════════════════════════════════
#  4. 잔고 조회
# ══════════════════════════════════════════════════════════════════════════════

def get_holdings() -> dict:
    acc, prod = ACCOUNT_NO.split("-")
    tr_id = "TTTC8434R" if IS_REAL else "VTTC8434R"
    res = requests.get(
        f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance",
        headers=_h(tr_id),
        params={
            "CANO": acc, "ACNT_PRDT_CD": prod,
            "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
            "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDMP_YN": "N", "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        },
        timeout=10
    )
    res.raise_for_status()
    holdings = {}
    for item in res.json().get("output1", []):
        qty = int(item["hldg_qty"])
        if qty > 0:
            holdings[item["pdno"]] = {
                "qty": qty,
                "avg_price": float(item["pchs_avg_pric"]),
                "eval_profit_rate": float(item["evlu_pfls_rt"]),
            }
    return holdings


# ══════════════════════════════════════════════════════════════════════════════
#  5. 주문
# ══════════════════════════════════════════════════════════════════════════════

def place_order(code: str, qty: int, side: str) -> dict:
    """side: 'BUY' | 'SELL'  (시장가 주문)"""
    acc, prod = ACCOUNT_NO.split("-")
    if side == "BUY":
        tr_id = "TTTC0802U" if IS_REAL else "VTTC0802U"
        sll_buy = "02"
    else:
        tr_id = "TTTC0801U" if IS_REAL else "VTTC0801U"
        sll_buy = "01"

    res = requests.post(
        f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-cash",
        headers=_h(tr_id),
        json={
            "CANO": acc, "ACNT_PRDT_CD": prod,
            "PDNO": code, "ORD_DVSN": "01",
            "ORD_QTY": str(qty), "ORD_UNPR": "0",
            "SLL_BUY_DVSN_CD": sll_buy,
        },
        timeout=10
    )
    res.raise_for_status()
    return res.json()


# ══════════════════════════════════════════════════════════════════════════════
#  6. 카카오톡 나에게 보내기
# ══════════════════════════════════════════════════════════════════════════════

def send_kakao(message: str) -> bool:
    """카카오톡 나에게 메시지 전송"""
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {
        "Authorization": f"Bearer {KAKAO_TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    template = {
        "object_type": "text",
        "text": message,
        "link": {"web_url": "", "mobile_web_url": ""},
    }
    res = requests.post(
        url, headers=headers,
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=10
    )
    ok = res.status_code == 200
    print(f"[KAKAO] {'전송 성공' if ok else f'전송 실패 {res.text}'}")
    return ok


def build_kakao_message(
    uptrend: bool,
    action: str,
    current_price: float,
    today_ma: float,
    yesterday_ma: float,
    qty_before: int,
    qty_after: int,
    avg_price: float,
    profit_rate: float,
    buy_qty: int = 0,
    buy_amount: int = 0,
) -> str:
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    ma_dir  = "상향 ▲" if uptrend else "하향 ▼"
    ma_diff = today_ma - yesterday_ma

    lines = [
        f"📊 [KODEX200 매매 알림]",
        f"🕐 {now_str}",
        "━━━━━━━━━━━━━━━━━━━",
        f"{'📈' if uptrend else '📉'} 코스피 120MA: {ma_dir}",
        f"   ({yesterday_ma:,.2f} → {today_ma:,.2f} / {ma_diff:+.2f})",
        f"💰 KODEX200 현재가: {current_price:,.0f}원",
        "",
    ]

    if action == "SELL_ALL":
        lines += [
            "🚨 오늘의 신호: 전량 매도",
            "",
            f"❌ MA120 하향 전환 — 방어 매도",
            f"💼 {qty_before}주 전량 매도 실행",
            "🔒 현금 보유 전환",
            "",
            "⏸ 재매수 조건: MA120 상향 전환",
        ]
    elif action == "PROFIT_SELL_REBUY":
        lines += [
            f"🎯 오늘의 신호: 익절 후 재매수",
            "",
            f"✅ 수익률 {profit_rate:.2%} — 목표 달성!",
            f"💸 {qty_before}주 전량 익절",
            f"🛒 재매수: {buy_qty}주 ({buy_amount:,}원)",
            f"💼 보유: {qty_after}주 | 평균단가: {avg_price:,.0f}원",
        ]
    elif action == "BUY":
        profit_str = f"{profit_rate:.2%}" if qty_before > 0 else "—"
        lines += [
            "🛒 오늘의 신호: 정기 매수",
            "",
            f"✅ {buy_qty}주 매수 ({buy_amount:,}원)",
            f"💼 보유: {qty_before}주 → {qty_after}주",
            f"📉 평균단가: {avg_price:,.0f}원",
            f"📊 수익률: {profit_str}",
            "",
            f"⚡ 익절 목표가: {avg_price * (1 + PROFIT_TARGET):,.0f}원 (+{PROFIT_TARGET*100:.0f}%)",
        ]
    else:  # NO_POSITION
        lines += [
            "⏸ 오늘의 신호: 관망",
            "",
            "ℹ️  보유 없음 & 매수 조건 미충족",
        ]

    lines += [
        "━━━━━━━━━━━━━━━━━━━",
        f"[{'실전' if IS_REAL else '모의'} 자동매매 실행 완료]",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
#  7. 핵심 전략 실행
# ══════════════════════════════════════════════════════════════════════════════

def run_strategy():
    now = datetime.datetime.now()
    print(f"\n{'='*55}")
    print(f"[RUN] {now:%Y-%m-%d %H:%M:%S}")
    print(f"{'='*55}")

    try:
        uptrend, today_ma, yesterday_ma = check_ma120()
        holdings = get_holdings()
        kodex = holdings.get(KODEX200_CODE)

        qty_before  = kodex["qty"]       if kodex else 0
        avg_price   = kodex["avg_price"] if kodex else 0.0
        profit_rate = 0.0
        current_price = get_current_price(KODEX200_CODE)

        if kodex:
            profit_rate = (current_price - avg_price) / avg_price

        print(f"[MA120] 어제={yesterday_ma:.2f}  오늘={today_ma:.2f}  {'▲상향' if uptrend else '▼하향'}")
        print(f"[PRICE] {current_price:,.0f}원  보유={qty_before}주  수익률={profit_rate:.2%}")

        # ── 케이스 1: MA120 하향 → 전량 매도 ──────────────────────────────
        if not uptrend:
            if qty_before > 0:
                print(f"[ACTION] MA120 하향 → 전량 매도 {qty_before}주")
                place_order(KODEX200_CODE, qty_before, "SELL")
                msg = build_kakao_message(
                    uptrend=False, action="SELL_ALL",
                    current_price=current_price,
                    today_ma=today_ma, yesterday_ma=yesterday_ma,
                    qty_before=qty_before, qty_after=0,
                    avg_price=avg_price, profit_rate=profit_rate,
                )
            else:
                print("[INFO] MA120 하향 — 보유 없음, 관망")
                msg = build_kakao_message(
                    uptrend=False, action="NO_POSITION",
                    current_price=current_price,
                    today_ma=today_ma, yesterday_ma=yesterday_ma,
                    qty_before=0, qty_after=0,
                    avg_price=0, profit_rate=0,
                )
            send_kakao(msg)
            return

        # ── 케이스 2: MA120 상향 + 6% 익절 ────────────────────────────────
        if qty_before > 0 and profit_rate >= PROFIT_TARGET:
            print(f"[ACTION] 익절 {profit_rate:.2%} → 전량 매도 후 재매수")
            place_order(KODEX200_CODE, qty_before, "SELL")
            time.sleep(2)
            buy_qty    = max(1, int(DAILY_BUY_KRW / current_price))
            buy_amount = buy_qty * int(current_price)
            place_order(KODEX200_CODE, buy_qty, "BUY")
            msg = build_kakao_message(
                uptrend=True, action="PROFIT_SELL_REBUY",
                current_price=current_price,
                today_ma=today_ma, yesterday_ma=yesterday_ma,
                qty_before=qty_before, qty_after=buy_qty,
                avg_price=current_price, profit_rate=profit_rate,
                buy_qty=buy_qty, buy_amount=buy_amount,
            )
            send_kakao(msg)
            return

        # ── 케이스 3: MA120 상향 + 정기 매수 ──────────────────────────────
        buy_qty    = max(1, int(DAILY_BUY_KRW / current_price))
        buy_amount = buy_qty * int(current_price)
        place_order(KODEX200_CODE, buy_qty, "BUY")

        qty_after = qty_before + buy_qty
        new_avg   = ((avg_price * qty_before) + (current_price * buy_qty)) / qty_after \
                    if qty_after > 0 else current_price
        new_profit = (current_price - new_avg) / new_avg if new_avg > 0 else 0.0

        print(f"[ACTION] 정기 매수 {buy_qty}주 ({buy_amount:,}원)")
        msg = build_kakao_message(
            uptrend=True, action="BUY",
            current_price=current_price,
            today_ma=today_ma, yesterday_ma=yesterday_ma,
            qty_before=qty_before, qty_after=qty_after,
            avg_price=new_avg, profit_rate=new_profit,
            buy_qty=buy_qty, buy_amount=buy_amount,
        )
        send_kakao(msg)

    except Exception as e:
        err_msg = (
            f"⚠️ [KODEX200 자동매매 오류]\n"
            f"🕐 {datetime.datetime.now():%Y-%m-%d %H:%M}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"❌ {type(e).__name__}: {e}\n"
            f"수동 확인이 필요합니다."
        )
        print(f"[ERROR] {e}")
        try:
            send_kakao(err_msg)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
#  8. 진입점 (GitHub Actions에서 직접 호출 — 스케줄러 불필요)
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    env_label = "실전투자" if IS_REAL else "모의투자"
    print("=" * 55)
    print(f"  KODEX200 자동매매 봇 ({env_label})")
    print(f"  매일 매수: {DAILY_BUY_KRW:,}원 | 익절: {PROFIT_TARGET*100:.0f}%")
    print(f"  MA 기간: {MA_PERIOD}일 | 알림: 카카오톡")
    print("=" * 55)
    run_strategy()
