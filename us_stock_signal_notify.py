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
  pip install yfinance pandas pandas-ta requests python-dotenv

.env 파일:
  KAKAO_TOKEN=카카오_액세스_토큰
"""

import os
import json
import datetime
import requests
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from dotenv import load_dotenv

load_dotenv()

KAKAO_TOKEN = os.getenv("KAKAO_TOKEN", "YOUR_KAKAO_TOKEN")

TICKERS = ["AVGO", "TSLA", "TEM", "OKLO", "SOXL", "TQQQ", "HOOD", "BMNR", "PLTR", "GOOGL"]

# 지표 임계값
RSI_OVERSOLD    = 30
RSI_OVERBOUGHT  = 70
STOCH_OVERSOLD  = 30
STOCH_OVERBOUGHT = 70
BB_TOUCH_PCT    = 0.005   # 볼린저밴드 터치 허용 오차 0.5%
MA_TOUCH_PCT    = 0.003   # 이동평균선 터치 허용 오차 0.3%
MA_PERIODS      = [5, 20, 60, 120]


# ══════════════════════════════════════════════════════════════════
#  1. 데이터 조회
# ══════════════════════════════════════════════════════════════════

def get_data(ticker: str, interval: str, period: str) -> pd.DataFrame:
    """
    interval: '1d' (일봉) | '1wk' (주봉)
    period: 데이터 기간
    """
    df = yf.download(ticker, interval=interval, period=period, progress=False)
    if df.empty:
        raise ValueError(f"{ticker} 데이터 없음")
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    return df


# ══════════════════════════════════════════════════════════════════
#  2. 지표 계산
# ══════════════════════════════════════════════════════════════════

def calc_rsi(df: pd.DataFrame, period: int = 14) -> float:
    rsi = ta.rsi(df["Close"], length=period)
    return float(rsi.iloc[-1]) if rsi is not None else None


def calc_bollinger(df: pd.DataFrame) -> tuple[float, float, float]:
    """returns: (upper, mid, lower)"""
    bb = ta.bbands(df["Close"], length=20, std=2)
    if bb is None:
        return None, None, None
    return (
        float(bb["BBU_20_2.0"].iloc[-1]),
        float(bb["BBM_20_2.0"].iloc[-1]),
        float(bb["BBL_20_2.0"].iloc[-1]),
    )


def calc_stoch(df: pd.DataFrame) -> tuple[float, float, float, float]:
    """returns: (fast_k, fast_d, slow_k, slow_d)"""
    fast = ta.stoch(df["High"], df["Low"], df["Close"], k=5, d=3, smooth_k=1)
    slow = ta.stoch(df["High"], df["Low"], df["Close"], k=14, d=3, smooth_k=3)
    if fast is None or slow is None:
        return None, None, None, None
    return (
        float(fast.iloc[-1, 0]),
        float(fast.iloc[-1, 1]),
        float(slow.iloc[-1, 0]),
        float(slow.iloc[-1, 1]),
    )


def calc_mas(df: pd.DataFrame) -> dict[int, float]:
    mas = {}
    for p in MA_PERIODS:
        if len(df) >= p:
            mas[p] = float(df["Close"].rolling(p).mean().iloc[-1])
    return mas


# ══════════════════════════════════════════════════════════════════
#  3. 터치 여부 판단 (허용 오차 범위 내)
# ══════════════════════════════════════════════════════════════════

def is_touch(price: float, target: float, pct: float) -> bool:
    if target is None or target == 0:
        return False
    return abs(price - target) / target <= pct


# ══════════════════════════════════════════════════════════════════
#  4. 종목별 신호 분석
# ══════════════════════════════════════════════════════════════════

def analyze(ticker: str) -> list[str]:
    signals = []

    for interval, label in [("1d", "일봉"), ("1wk", "주봉")]:
        try:
            period = "2y" if interval == "1wk" else "1y"
            df = get_data(ticker, interval, period)
            price = float(df["Close"].iloc[-1])

            # RSI
            rsi = calc_rsi(df)
            if rsi is not None:
                if rsi <= RSI_OVERSOLD:
                    signals.append(f"[{label}] RSI {rsi:.1f} ← 과매도 ({RSI_OVERSOLD} 이하)")
                elif rsi >= RSI_OVERBOUGHT:
                    signals.append(f"[{label}] RSI {rsi:.1f} ← 과매수 ({RSI_OVERBOUGHT} 이상)")

            # 볼린저밴드
            upper, mid, lower = calc_bollinger(df)
            if upper:
                if is_touch(price, upper, BB_TOUCH_PCT):
                    signals.append(f"[{label}] 볼린저밴드 상단 터치 ({upper:,.2f})")
                elif is_touch(price, lower, BB_TOUCH_PCT):
                    signals.append(f"[{label}] 볼린저밴드 하단 터치 ({lower:,.2f})")
                elif is_touch(price, mid, BB_TOUCH_PCT):
                    signals.append(f"[{label}] 볼린저밴드 중심선 터치 ({mid:,.2f})")

            # 이동평균선
            mas = calc_mas(df)
            for p, ma_val in mas.items():
                if is_touch(price, ma_val, MA_TOUCH_PCT):
                    signals.append(f"[{label}] {p}일선 터치 ({ma_val:,.2f})")

            # 스토캐스틱
            fk, fd, sk, sd = calc_stoch(df)
            if fk is not None:
                if fk <= STOCH_OVERSOLD:
                    signals.append(f"[{label}] Fast 스토캐스틱 K {fk:.1f} ← 과매도")
                elif fk >= STOCH_OVERBOUGHT:
                    signals.append(f"[{label}] Fast 스토캐스틱 K {fk:.1f} ← 과매수")
            if sk is not None:
                if sk <= STOCH_OVERSOLD:
                    signals.append(f"[{label}] Slow 스토캐스틱 K {sk:.1f} ← 과매도")
                elif sk >= STOCH_OVERBOUGHT:
                    signals.append(f"[{label}] Slow 스토캐스틱 K {sk:.1f} ← 과매수")

        except Exception as e:
            signals.append(f"[{label}] 데이터 오류: {e}")

    return signals


# ══════════════════════════════════════════════════════════════════
#  5. 카카오톡 전송
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
#  6. 메인 실행
# ══════════════════════════════════════════════════════════════════

def run():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[RUN] {now}")

    all_signals = []

    for ticker in TICKERS:
        print(f"[분석] {ticker}...")
        try:
            signals = analyze(ticker)
            if signals:
                ticker_block = [f"📌 {ticker}"]
                ticker_block += [f"  {s}" for s in signals]
                all_signals.append("\n".join(ticker_block))
        except Exception as e:
            print(f"[ERROR] {ticker}: {e}")

    if not all_signals:
        print("[INFO] 조건 달성 종목 없음 — 알림 미전송")
        return

    msg = "\n".join([
        "🇺🇸 [미국주식 기술적 신호]",
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
