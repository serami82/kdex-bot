"""
미국주식 알림 봇
================
[프리장 시작 - 매일 1회]
  - Fear & Greed Index
  - 피보나치 되돌림 가격대 (52주 고점/저점)

[장중 15분마다 - 조건 달성 시 당일 1회]
  1. RSI 30 이하 / 70 이상
  2. 볼린저밴드 상단/하단/중심선 터치
  3. 일봉 5/20/60/120일선 터치
  4. MACD 시그널선 상향/하향 돌파
  5. VIX 25/30 돌파

설치:
  pip install yfinance pandas ta requests python-dotenv fear-greed
"""

import os
import json
import datetime
import requests
import yfinance as yf
import pandas as pd
import ta
import fear_greed
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

KAKAO_TOKEN = os.getenv("KAKAO_TOKEN_FRIEND", "YOUR_KAKAO_TOKEN")
TEST_MODE   = os.getenv("TEST_MODE", "false").lower() == "true"
RUN_TYPE    = os.getenv("RUN_TYPE", "signal")   # signal | premarket

TICKERS = ["AVGO", "TSLA", "TEM", "OKLO", "SOXL", "TQQQ", "HOOD", "BMNR", "PLTR", "GOOGL"]

RSI_OVERSOLD     = 30
RSI_OVERBOUGHT   = 70
BB_TOUCH_PCT     = 0.005
MA_TOUCH_PCT     = 0.003
MA_PERIODS       = [5, 20, 60, 120]
FIBO_LEVELS      = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
FIBO_TOUCH_PCT   = 0.005
VIX_LEVELS       = [25, 30]

SENT_FILE = Path("sent_signals.json")


# ══════════════════════════════════════════════════════════════════
#  1. 당일 발송 기록 관리
# ══════════════════════════════════════════════════════════════════

def load_sent() -> dict:
    today = datetime.date.today().isoformat()
    if SENT_FILE.exists():
        try:
            data = json.loads(SENT_FILE.read_text())
            if data.get("date") == today:
                return data
        except Exception:
            pass
    return {"date": today, "signals": [], "premarket_sent": False}


def save_sent(data: dict):
    SENT_FILE.write_text(json.dumps(data, ensure_ascii=False))


def is_new(sent_data: dict, key: str) -> bool:
    """당일 미발송 신호이면 True"""
    return TEST_MODE or key not in sent_data["signals"]


def mark_sent(sent_data: dict, keys: list):
    for key in keys:
        if key not in sent_data["signals"]:
            sent_data["signals"].append(key)


# ══════════════════════════════════════════════════════════════════
#  2. 데이터 조회
# ══════════════════════════════════════════════════════════════════

def get_ohlcv(ticker: str, period: str = "1y") -> pd.DataFrame:
    df = yf.download(ticker, interval="1d", period=period, progress=False)
    if df.empty:
        raise ValueError(f"{ticker} 데이터 없음")
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    return df.dropna()


# ══════════════════════════════════════════════════════════════════
#  3. 터치 여부
# ══════════════════════════════════════════════════════════════════

def is_touch(price: float, target: float, pct: float) -> bool:
    if not target:
        return False
    return abs(price - target) / target <= pct


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
#  5. 프리장 브리핑 (Fear & Greed + 피보나치)
# ══════════════════════════════════════════════════════════════════

def get_fear_greed_msg() -> str:
    try:
        data    = fear_greed.get()
        score   = data["score"]
        rating  = data["rating"]
        history = data.get("history", {})

        if score <= 25:   emoji = "😱"
        elif score <= 45: emoji = "😰"
        elif score <= 55: emoji = "😐"
        elif score <= 75: emoji = "😏"
        else:             emoji = "🤑"

        rating_kr = {
            "extreme fear": "극단적 공포",
            "fear": "공포",
            "neutral": "중립",
            "greed": "탐욕",
            "extreme greed": "극단적 탐욕",
        }.get(rating.lower(), rating)

        return "\n".join([
            f"😨 Fear & Greed: {score:.0f}  {emoji} {rating_kr}",
            f"  1주전 {history.get('1w','?')} | 1달전 {history.get('1m','?')} | 1년전 {history.get('1y','?')}",
        ])
    except Exception as e:
        return f"😨 Fear & Greed: 조회 실패 ({e})"


def calc_fibonacci(ticker: str) -> str:
    try:
        df    = get_ohlcv(ticker)
        df52  = df.tail(252)
        high  = float(df52["High"].max())
        low   = float(df52["Low"].min())
        price = float(df["Close"].iloc[-1])
        diff  = high - low

        lines = [f"📌 {ticker}  ${price:,.2f}  (52주 고 ${high:,.2f} / 저 ${low:,.2f})"]
        for lv in FIBO_LEVELS:
            fp   = high - diff * lv
            near = " ◀ 현재가" if is_touch(price, fp, FIBO_TOUCH_PCT) else ""
            lines.append(f"  {lv*100:.1f}%  ${fp:,.2f}{near}")
        return "\n".join(lines)
    except Exception as e:
        return f"📌 {ticker}  ❌ {e}"


def run_premarket():
    """프리장 시작 시 1회: Fear & Greed + 피보나치"""
    now       = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    sent_data = load_sent()

    if not TEST_MODE and sent_data.get("premarket_sent"):
        print("[INFO] 오늘 프리장 브리핑 이미 발송됨")
        return

    fg    = get_fear_greed_msg()
    fibos = [calc_fibonacci(t) for t in TICKERS]

    msg = "\n".join([
        "🇺🇸 [프리장 브리핑]",
        f"🕐 {now}",
        "━━━━━━━━━━━━━━━━━━━",
        fg,
        "━━━━━━━━━━━━━━━━━━━",
        "📐 피보나치 되돌림 (52주 고점/저점)",
        "",
        "\n\n".join(fibos),
        "━━━━━━━━━━━━━━━━━━━",
        "※ 참고용이며 투자 책임은 본인에게 있습니다.",
    ])
    print(msg)
    if send_kakao(msg) and not TEST_MODE:
        sent_data["premarket_sent"] = True
        save_sent(sent_data)


# ══════════════════════════════════════════════════════════════════
#  6. 장중 신호 분석
# ══════════════════════════════════════════════════════════════════

def check_vix(sent_data: dict) -> list:
    """VIX 25/30 돌파 체크"""
    results = []
    try:
        df = get_ohlcv("^VIX", period="5d")
        if len(df) < 2:
            return []
        vix      = float(df["Close"].iloc[-1])
        prev_vix = float(df["Close"].iloc[-2])
        print(f"[VIX] 현재 {vix:.2f}  전일 {prev_vix:.2f}")

        for lv in VIX_LEVELS:
            # 상향 돌파
            if prev_vix < lv <= vix:
                key = f"VIX_{lv}_up"
                if is_new(sent_data, key):
                    results.append((key, f"⚠️ VIX {vix:.1f} — {lv} 상향 돌파! 변동성 급등 🔴"))
            # 하향 돌파
            elif prev_vix >= lv > vix:
                key = f"VIX_{lv}_down"
                if is_new(sent_data, key):
                    results.append((key, f"✅ VIX {vix:.1f} — {lv} 하향 돌파, 변동성 완화 🟢"))
    except Exception as e:
        print(f"[ERROR] VIX: {e}")
    return results


def check_ticker(ticker: str, sent_data: dict) -> list:
    """종목별 신호 체크 (RSI / BB / MA / MACD)"""
    results = []
    try:
        df    = get_ohlcv(ticker)
        if len(df) < 30:
            return []
        price = float(df["Close"].iloc[-1])

        def chk(key_suffix: str, msg: str):
            key = f"{ticker}_{key_suffix}"
            if is_new(sent_data, key):
                results.append((key, msg))

        # 1. RSI
        rsi = float(ta.momentum.RSIIndicator(df["Close"], window=14).rsi().iloc[-1])
        if rsi <= RSI_OVERSOLD:
            chk("RSI_과매도", f"RSI {rsi:.1f} ← 과매도 ({RSI_OVERSOLD} 이하) 🟢")
        elif rsi >= RSI_OVERBOUGHT:
            chk("RSI_과매수", f"RSI {rsi:.1f} ← 과매수 ({RSI_OVERBOUGHT} 이상) 🔴")

        # 2. 볼린저밴드
        bb    = ta.volatility.BollingerBands(df["Close"], window=20, window_dev=2)
        upper = float(bb.bollinger_hband().iloc[-1])
        mid   = float(bb.bollinger_mavg().iloc[-1])
        lower = float(bb.bollinger_lband().iloc[-1])
        if is_touch(price, upper, BB_TOUCH_PCT):
            chk("BB_상단", f"볼린저밴드 상단 터치 (${upper:,.2f}) 🔴")
        elif is_touch(price, lower, BB_TOUCH_PCT):
            chk("BB_하단", f"볼린저밴드 하단 터치 (${lower:,.2f}) 🟢")
        elif is_touch(price, mid, BB_TOUCH_PCT):
            chk("BB_중심", f"볼린저밴드 중심선 터치 (${mid:,.2f}) 🟡")

        # 3. 이동평균선
        for p in MA_PERIODS:
            if len(df) >= p:
                ma_val = float(df["Close"].rolling(p).mean().iloc[-1])
                if is_touch(price, ma_val, MA_TOUCH_PCT):
                    chk(f"MA_{p}", f"{p}일선 터치 (${ma_val:,.2f})")

        # 4. MACD 시그널선 돌파
        macd_ind = ta.trend.MACD(df["Close"])
        macd_l   = macd_ind.macd()
        macd_s   = macd_ind.macd_signal()
        if len(macd_l) >= 2:
            prev_diff = float(macd_l.iloc[-2]) - float(macd_s.iloc[-2])
            curr_diff = float(macd_l.iloc[-1]) - float(macd_s.iloc[-1])
            if prev_diff < 0 and curr_diff >= 0:
                chk("MACD_골든", f"MACD 시그널선 상향돌파 🟢 (골든크로스)")
            elif prev_diff > 0 and curr_diff <= 0:
                chk("MACD_데드", f"MACD 시그널선 하향돌파 🔴 (데드크로스)")

    except Exception as e:
        print(f"[ERROR] {ticker}: {e}")

    return results


def run_signal():
    """장중 15분마다: 조건 달성 시 당일 1회"""
    now       = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    sent_data = load_sent()
    blocks    = []
    new_keys  = []

    # VIX
    vix_signals = check_vix(sent_data)
    if vix_signals:
        msgs = [m for _, m in vix_signals]
        blocks.append("📊 VIX\n" + "\n".join(f"  {m}" for m in msgs))
        new_keys += [k for k, _ in vix_signals]

    # 종목별
    for ticker in TICKERS:
        print(f"[분석] {ticker}...")
        signals = check_ticker(ticker, sent_data)
        if signals:
            try:
                price = float(get_ohlcv(ticker)["Close"].iloc[-1])
                header = f"📌 {ticker}  ${price:,.2f}"
            except Exception:
                header = f"📌 {ticker}"
            msgs = [m for _, m in signals]
            blocks.append(header + "\n" + "\n".join(f"  {m}" for m in msgs))
            new_keys += [k for k, _ in signals]

    if not blocks:
        print("[INFO] 새로운 신호 없음 — 미전송")
        return

    header = "🧪 [테스트] " if TEST_MODE else ""
    msg = "\n".join([
        f"🇺🇸 {header}[미국주식 기술적 신호]",
        f"🕐 {now}",
        "━━━━━━━━━━━━━━━━━━━",
        "\n\n".join(blocks),
        "━━━━━━━━━━━━━━━━━━━",
        "쑤랑요니 다이야사줭💓",
    ])
    print(msg)
    if send_kakao(msg) and not TEST_MODE:
        mark_sent(sent_data, new_keys)
        save_sent(sent_data)


# ══════════════════════════════════════════════════════════════════
#  7. 메인
# ══════════════════════════════════════════════════════════════════

def run():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[RUN] {now}  RUN_TYPE={RUN_TYPE}  {'[테스트]' if TEST_MODE else ''}")
    if RUN_TYPE == "premarket":
        run_premarket()
    else:
        run_signal()


if __name__ == "__main__":
    run()
