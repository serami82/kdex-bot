"""
미국주식 기술적 지표 알림 봇 (중복 알림 방지)
===============================================
- 조건 달성 시 당일 1회만 알림
- 프리장~나이트장 (한국시간 22:00~08:00) 15분마다 실행
- 당일 발송 기록을 sent_signals.json 파일로 관리

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
from pathlib import Path

load_dotenv()

KAKAO_TOKEN = os.getenv("KAKAO_TOKEN_FRIEND", "YOUR_KAKAO_TOKEN")
TEST_MODE   = os.getenv("TEST_MODE", "false").lower() == "true"

TICKERS = ["AVGO", "TSLA", "TEM", "OKLO", "SOXL", "TQQQ", "HOOD", "BMNR", "PLTR", "GOOGL"]

RSI_OVERSOLD     = 30
RSI_OVERBOUGHT   = 70
STOCH_OVERSOLD   = 30
STOCH_OVERBOUGHT = 70
BB_TOUCH_PCT     = 0.005
MA_TOUCH_PCT     = 0.003
MA_PERIODS       = [5, 20, 60, 120]

SENT_FILE = Path("sent_signals.json")


# ══════════════════════════════════════════════════════════════════
#  1. 당일 발송 기록 관리
# ══════════════════════════════════════════════════════════════════

def load_sent() -> dict:
    """당일 발송 기록 로드. 날짜 바뀌면 초기화."""
    today = datetime.date.today().isoformat()
    if SENT_FILE.exists():
        try:
            data = json.loads(SENT_FILE.read_text())
            if data.get("date") == today:
                return data
        except Exception:
            pass
    return {"date": today, "signals": []}


def save_sent(data: dict):
    SENT_FILE.write_text(json.dumps(data, ensure_ascii=False))


def already_sent(sent_data: dict, key: str) -> bool:
    return key in sent_data["signals"]


def mark_sent(sent_data: dict, key: str):
    if key not in sent_data["signals"]:
        sent_data["signals"].append(key)


# ══════════════════════════════════════════════════════════════════
#  2. 데이터 조회
# ══════════════════════════════════════════════════════════════════

def get_data(ticker: str, interval: str) -> pd.DataFrame:
    period = "3y" if interval == "1wk" else "1y"
    df = yf.download(ticker, interval=interval, period=period, progress=False)
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
#  4. 신호 분석 (신호키 포함)
# ══════════════════════════════════════════════════════════════════

def analyze(ticker: str, sent_data: dict) -> list:
    """
    새로운 신호만 반환 (당일 이미 보낸 신호 제외)
    신호키 형식: TSLA_일봉_RSI_과매수
    """
    new_signals = []

    for interval, label in [("1d", "일봉"), ("1wk", "주봉")]:
        try:
            df = get_data(ticker, interval)
            if len(df) < 30:
                continue
            price = float(df["Close"].iloc[-1])

            def check(key_suffix: str, msg: str):
                key = f"{ticker}_{label}_{key_suffix}"
                if TEST_MODE or not already_sent(sent_data, key):
                    new_signals.append((key, msg))

            # RSI
            rsi = float(ta.momentum.RSIIndicator(df["Close"], window=14).rsi().iloc[-1])
            if rsi <= RSI_OVERSOLD:
                check("RSI_과매도", f"[{label}] RSI {rsi:.1f} ← 과매도 ({RSI_OVERSOLD} 이하) 🟢")
            elif rsi >= RSI_OVERBOUGHT:
                check("RSI_과매수", f"[{label}] RSI {rsi:.1f} ← 과매수 ({RSI_OVERBOUGHT} 이상) 🔴")

            # 볼린저밴드
            bb     = ta.volatility.BollingerBands(df["Close"], window=20, window_dev=2)
            upper  = float(bb.bollinger_hband().iloc[-1])
            mid    = float(bb.bollinger_mavg().iloc[-1])
            lower  = float(bb.bollinger_lband().iloc[-1])
            if is_touch(price, upper, BB_TOUCH_PCT):
                check("BB_상단", f"[{label}] 볼린저밴드 상단 터치 ({upper:,.2f}) 🔴")
            elif is_touch(price, lower, BB_TOUCH_PCT):
                check("BB_하단", f"[{label}] 볼린저밴드 하단 터치 ({lower:,.2f}) 🟢")
            elif is_touch(price, mid, BB_TOUCH_PCT):
                check("BB_중심", f"[{label}] 볼린저밴드 중심선 터치 ({mid:,.2f}) 🟡")

            # 이동평균선
            for p in MA_PERIODS:
                if len(df) >= p:
                    ma_val = float(df["Close"].rolling(p).mean().iloc[-1])
                    if is_touch(price, ma_val, MA_TOUCH_PCT):
                        check(f"MA_{p}", f"[{label}] {p}일선 터치 ({ma_val:,.2f})")

            # Fast 스토캐스틱
            fk = float(ta.momentum.StochasticOscillator(
                df["High"], df["Low"], df["Close"], window=5, smooth_window=3
            ).stoch().iloc[-1])
            if fk <= STOCH_OVERSOLD:
                check("FAST_과매도", f"[{label}] Fast 스토캐스틱 {fk:.1f} ← 과매도 🟢")
            elif fk >= STOCH_OVERBOUGHT:
                check("FAST_과매수", f"[{label}] Fast 스토캐스틱 {fk:.1f} ← 과매수 🔴")

            # Slow 스토캐스틱
            sk = float(ta.momentum.StochasticOscillator(
                df["High"], df["Low"], df["Close"], window=14, smooth_window=3
            ).stoch().iloc[-1])
            if sk <= STOCH_OVERSOLD:
                check("SLOW_과매도", f"[{label}] Slow 스토캐스틱 {sk:.1f} ← 과매도 🟢")
            elif sk >= STOCH_OVERBOUGHT:
                check("SLOW_과매수", f"[{label}] Slow 스토캐스틱 {sk:.1f} ← 과매수 🔴")

        except Exception as e:
            print(f"[ERROR] {ticker} {label}: {e}")

    return new_signals


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
    print(f"[RUN] {now} {'[테스트 모드]' if TEST_MODE else ''}")

    sent_data  = load_sent()
    all_blocks = []
    new_keys   = []

    for ticker in TICKERS:
        print(f"[분석] {ticker}...")
        try:
            signals = analyze(ticker, sent_data)
            if signals:
                keys  = [k for k, _ in signals]
                msgs  = [m for _, m in signals]
                block = [f"📌 {ticker}"] + [f"  {m}" for m in msgs]
                all_blocks.append("\n".join(block))
                new_keys.extend(keys)
        except Exception as e:
            print(f"[ERROR] {ticker}: {e}")

    if not all_blocks:
        print("[INFO] 새로운 신호 없음 — 알림 미전송")
        return

    header = "🧪 [테스트] " if TEST_MODE else ""
    msg = "\n".join([
        f"🇺🇸 {header}[미국주식 기술적 신호]",
        f"🕐 {now}",
        "━━━━━━━━━━━━━━━━━━━",
        "\n\n".join(all_blocks),
        "━━━━━━━━━━━━━━━━━━━",
        "※ 참고용 신호이며 투자 책임은 본인에게 있습니다.",
    ])
    print(msg)

    if send_kakao(msg) and not TEST_MODE:
        for key in new_keys:
            mark_sent(sent_data, key)
        save_sent(sent_data)
        print(f"[SENT] {len(new_keys)}개 신호 기록 저장")


if __name__ == "__main__":
    run()
