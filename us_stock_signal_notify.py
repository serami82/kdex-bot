"""
미국주식 기술적 지표 알림 봇
=============================
보유종목: AVGO, TSLA, TEM, OKLO, SOXL, TQQQ, HOOD, BMNR, PLTR, GOOGL
알림 조건:
  1. RSI 30 이하 / 70 이상
  2. 볼린저밴드 상단/하단/중심선 터치
  3. 일봉/주봉 5일/20일/60일/120일선 터치
  4. Fast/Slow 스토캐스틱 30 이하 / 70 이상

15분마다 실행 (GitHub Actions)
데이터: Yahoo Finance (무료)

설치:
  pip install yfinance pandas ta requests python-dotenv
"""

import os
import json
import datetime
import requests
import yfinance as yf
import pandas as pd
import ta
from dotenv import load_dotenv

load_dotenv()

KAKAO_TOKEN = os.getenv("KAKAO_TOKEN_FRIEND", "YOUR_KAKAO_TOKEN")

TICKERS = ["AVGO", "TSLA", "TEM", "OKLO", "SOXL", "TQQQ", "HOOD", "BMNR", "PLTR", "GOOGL"]

RSI_OVERSOLD     = 30
RSI_OVERBOUGHT   = 70
STOCH_OVERSOLD   = 30
STOCH_OVERBOUGHT = 70
BB_TOUCH_PCT     = 0.005
MA_TOUCH_PCT     = 0.003
MA_PERIODS       = [5, 20, 60, 120]


def get_data(ticker: str, interval: str) -> pd.DataFrame:
    period = "2y" if interval == "1wk" else "1y"
    df = yf.download(ticker, interval=interval, period=period, progress=False)
    if df.empty:
        raise ValueError(f"{ticker} 데이터 없음")
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    return df.dropna()


def is_touch(price: float, target: float, pct: float) -> bool:
    if target is None or target == 0:
        return False
    return abs(price - target) / target <= pct


def analyze(ticker: str) -> list:
    signals = []
    for interval, label in [("1d", "일봉"), ("1wk", "주봉")]:
        try:
            df = get_data(ticker, interval)
            if len(df) < 120:
                continue
            price = float(df["Close"].iloc[-1])

            # RSI
            rsi = float(ta.momentum.RSIIndicator(df["Close"], window=14).rsi().iloc[-1])
            if rsi <= RSI_OVERSOLD:
                signals.append(f"[{label}] RSI {rsi:.1f} ← 과매도 🟢")
            elif rsi >= RSI_OVERBOUGHT:
                signals.append(f"[{label}] RSI {rsi:.1f} ← 과매수 🔴")

            # 볼린저밴드
            bb = ta.volatility.BollingerBands(df["Close"], window=20, window_dev=2)
            upper = float(bb.bollinger_hband().iloc[-1])
            mid   = float(bb.bollinger_mavg().iloc[-1])
            lower = float(bb.bollinger_lband().iloc[-1])
            if is_touch(price, upper, BB_TOUCH_PCT):
                signals.append(f"[{label}] 볼린저밴드 상단 터치 ({upper:,.2f}) 🔴")
            elif is_touch(price, lower, BB_TOUCH_PCT):
                signals.append(f"[{label}] 볼린저밴드 하단 터치 ({lower:,.2f}) 🟢")
            elif is_touch(price, mid, BB_TOUCH_PCT):
                signals.append(f"[{label}] 볼린저밴드 중심선 터치 ({mid:,.2f}) 🟡")

            # 이동평균선
            for p in MA_PERIODS:
                if len(df) >= p:
                    ma_val = float(df["Close"].rolling(p).mean().iloc[-1])
                    if is_touch(price, ma_val, MA_TOUCH_PCT):
                        signals.append(f"[{label}] {p}일선 터치 ({ma_val:,.2f})")

            # Fast 스토캐스틱
            fk = float(ta.momentum.StochasticOscillator(
                df["High"], df["Low"], df["Close"], window=5, smooth_window=3
            ).stoch().iloc[-1])
            if fk <= STOCH_OVERSOLD:
                signals.append(f"[{label}] Fast 스토캐스틱 {fk:.1f} ← 과매도 🟢")
            elif fk >= STOCH_OVERBOUGHT:
                signals.append(f"[{label}] Fast 스토캐스틱 {fk:.1f} ← 과매수 🔴")

            # Slow 스토캐스틱
            sk = float(ta.momentum.StochasticOscillator(
                df["High"], df["Low"], df["Close"], window=14, smooth_window=3
            ).stoch().iloc[-1])
            if sk <= STOCH_OVERSOLD:
                signals.append(f"[{label}] Slow 스토캐스틱 {sk:.1f} ← 과매도 🟢")
            elif sk >= STOCH_OVERBOUGHT:
                signals.append(f"[{label}] Slow 스토캐스틱 {sk:.1f} ← 과매수 🔴")

        except Exception as e:
            print(f"[ERROR] {ticker} {label}: {e}")

    return signals


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


def run():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    test_mode = os.getenv("TEST_MODE", "false").lower() == "true"
    print(f"[RUN] {now} {'[테스트 모드]' if test_mode else ''}")
    all_signals = []

    for ticker in TICKERS:
        print(f"[분석] {ticker}...")
        try:
            signals = analyze(ticker)
            if test_mode:
                # 테스트 모드: 조건 미달성 종목도 현재 지표값 표시
                if not signals:
                    df = get_data(ticker, "1d")
                    price = float(df["Close"].iloc[-1])
                    rsi = float(ta.momentum.RSIIndicator(df["Close"], window=14).rsi().iloc[-1])
                    signals = [f"[일봉] 현재가 ${price:,.2f} / RSI {rsi:.1f} (조건 미달성)"]
            if signals:
                block = [f"📌 {ticker}"] + [f"  {s}" for s in signals]
                all_signals.append("\n".join(block))
        except Exception as e:
            print(f"[ERROR] {ticker}: {e}")
            if test_mode:
                all_signals.append(f"📌 {ticker}\n  ❌ 오류: {e}")

    if not all_signals:
        print("[INFO] 조건 달성 종목 없음 — 알림 미전송")
        return

    header = "🧪 [테스트] " if test_mode else ""
    msg = "\n".join([
        f"🇺🇸 {header}[미국주식 기술적 신호]",
        f"🕐 {now}",
        "━━━━━━━━━━━━━━━━━━━",
        "\n\n".join(all_signals),
        "━━━━━━━━━━━━━━━━━━━",
        "※ 참고용 신호이며 투자 책임은 본인에게 있습니다.",
    ])
    print(msg)
    send_kakao(msg)


if __name__ == "__main__":
    run()
