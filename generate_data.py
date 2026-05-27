"""
웹 대시보드용 데이터 생성기
GitHub Actions에서 실행 → data.json 생성 → GitHub Pages에서 읽음
"""
import json, datetime, os
import yfinance as yf
import pandas as pd
import ta
import fear_greed
from pathlib import Path

TICKERS = ["AVGO","TSLA","TEM","OKLO","SOXL","TQQQ","QCOM","BMNR","PLTR","GOOGL"]
MA_PERIODS = [5, 20, 60, 120]
FIBO_LEVELS = [0.0, 0.236, 0.382, 0.5]

def get_ohlcv(ticker, period="1y"):
    df = yf.download(ticker, interval="1d", period=period, progress=False)
    if df.empty: raise ValueError(f"{ticker} 데이터 없음")
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    return df.dropna()

def is_touch(price, target, pct=0.005):
    if not target: return False
    return abs(price - target) / target <= pct

def analyze_ticker(ticker):
    df = get_ohlcv(ticker)
    price = float(df["Close"].iloc[-1])
    signals = []

    # RSI
    rsi = float(ta.momentum.RSIIndicator(df["Close"], window=14).rsi().iloc[-1])
    if rsi <= 30: signals.append({"type": "RSI", "msg": f"RSI {rsi:.1f} 과매도", "color": "green"})
    elif rsi >= 70: signals.append({"type": "RSI", "msg": f"RSI {rsi:.1f} 과매수", "color": "red"})

    # 볼린저밴드
    bb = ta.volatility.BollingerBands(df["Close"], window=20, window_dev=2)
    upper = float(bb.bollinger_hband().iloc[-1])
    mid   = float(bb.bollinger_mavg().iloc[-1])
    lower = float(bb.bollinger_lband().iloc[-1])
    if is_touch(price, upper): signals.append({"type": "BB", "msg": f"BB상단 ${upper:,.2f}", "color": "red"})
    elif is_touch(price, lower): signals.append({"type": "BB", "msg": f"BB하단 ${lower:,.2f}", "color": "green"})
    elif is_touch(price, mid): signals.append({"type": "BB", "msg": f"BB중심 ${mid:,.2f}", "color": "yellow"})

    # 이동평균선
    for p in MA_PERIODS:
        if len(df) >= p:
            ma = float(df["Close"].rolling(p).mean().iloc[-1])
            if is_touch(price, ma, 0.003):
                signals.append({"type": "MA", "msg": f"{p}일선 ${ma:,.2f}", "color": "pink"})

    # MACD
    macd_ind = ta.trend.MACD(df["Close"])
    ml = macd_ind.macd()
    ms = macd_ind.macd_signal()
    if len(ml) >= 2:
        prev_d = float(ml.iloc[-2]) - float(ms.iloc[-2])
        curr_d = float(ml.iloc[-1]) - float(ms.iloc[-1])
        if prev_d < 0 and curr_d >= 0: signals.append({"type": "MACD", "msg": "MACD 골든크로스", "color": "green"})
        elif prev_d > 0 and curr_d <= 0: signals.append({"type": "MACD", "msg": "MACD 데드크로스", "color": "red"})

    # 피보나치
    df52 = df.tail(252)
    high = float(df52["High"].max())
    low  = float(df52["Low"].min())
    diff = high - low
    fibos = []
    for lv in FIBO_LEVELS:
        fp = high - diff * lv
        fibos.append({"level": lv, "price": round(fp, 2), "near": is_touch(price, fp)})

    return {
        "ticker": ticker,
        "price": round(price, 2),
        "rsi": round(rsi, 1),
        "signals": signals,
        "fibonacci": fibos,
        "fib_high": round(high, 2),
        "fib_low": round(low, 2),
    }

def get_vix():
    df = get_ohlcv("^VIX", period="5d")
    vix = float(df["Close"].iloc[-1])
    prev = float(df["Close"].iloc[-2])
    return {"vix": round(vix, 2), "prev": round(prev, 2), "change": round(vix-prev, 2)}

def get_fg():
    try:
        data = fear_greed.get()
        rating_kr = {
            "extreme fear": "극단적 공포", "fear": "공포",
            "neutral": "중립", "greed": "탐욕", "extreme greed": "극단적 탐욕"
        }.get(data["rating"].lower(), data["rating"])
        return {"score": round(data["score"]), "rating": data["rating"], "rating_kr": rating_kr}
    except:
        return {"score": None, "rating": "unknown", "rating_kr": "조회 실패"}

def main():
    print("[DATA] 데이터 생성 시작...")
    result = {
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "tickers": [],
        "vix": {},
        "fear_greed": {},
    }

    for ticker in TICKERS:
        print(f"  [{ticker}] 분석 중...")
        try:
            result["tickers"].append(analyze_ticker(ticker))
        except Exception as e:
            print(f"  [{ticker}] 오류: {e}")
            result["tickers"].append({"ticker": ticker, "price": None, "rsi": None, "signals": [], "fibonacci": []})

    try:
        result["vix"] = get_vix()
        print(f"  [VIX] {result['vix']['vix']}")
    except Exception as e:
        print(f"  [VIX] 오류: {e}")

    try:
        result["fear_greed"] = get_fg()
        print(f"  [F&G] {result['fear_greed']['score']}")
    except Exception as e:
        print(f"  [F&G] 오류: {e}")

    Path("data.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print("[DATA] data.json 생성 완료!")

if __name__ == "__main__":
    main()
