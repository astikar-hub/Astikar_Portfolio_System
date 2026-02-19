# ============================================
# ASTIKAR FUND SYSTEM – INSTITUTIONAL VERSION
# TOP 5 RS MOMENTUM MODEL
# ============================================

import os
import pandas as pd
import numpy as np
import yfinance as yf
import requests
from datetime import datetime

# ================= CONFIG =================

CAPITAL = 50000
TOP_N = 5
RS_LOOKBACK = 126  # 6 months
CRASH_THRESHOLD = -0.15
PYRAMID_LIMIT = 2
MAX_POSITION_MULTIPLIER = 1.5

INDEX_SYMBOL = "^NSEI"
UNIVERSE_FILE = "nifty200.csv"
POSITIONS_FILE = "portfolio_positions.csv"
EQUITY_FILE = "equity_curve.csv"

TELEGRAM_TOKEN = "PASTE_YOUR_TOKEN"
TELEGRAM_CHAT_ID = "PASTE_YOUR_CHAT_ID"

# ==========================================


def send_telegram(msg):
    if TELEGRAM_TOKEN == "PASTE_YOUR_TOKEN":
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})


def load_universe():
    df = pd.read_csv(UNIVERSE_FILE)
    tickers = df.iloc[:, 0].dropna().astype(str).tolist()
    return [t if t.endswith(".NS") else t + ".NS" for t in tickers]


def market_regime():
    data = yf.download(INDEX_SYMBOL, period="1y", auto_adjust=True, progress=False)
    close = data["Close"]
    ema50 = close.ewm(span=50).mean()
    ema200 = close.ewm(span=200).mean()

    return (close.iloc[-1] > ema200.iloc[-1]) or (ema50.iloc[-1] > ema200.iloc[-1])


def crash_monitor():
    data = yf.download(INDEX_SYMBOL, period="6mo", auto_adjust=True, progress=False)
    close = data["Close"]

    if len(close) < 63:
        return False

    ret = (close.iloc[-1] / close.iloc[-63]) - 1
    return ret < CRASH_THRESHOLD


def load_positions():
    if os.path.exists(POSITIONS_FILE):
        return pd.read_csv(POSITIONS_FILE)
    return pd.DataFrame(columns=["Ticker", "Shares", "Avg_Cost", "Adds"])


def save_positions(df):
    df.to_csv(POSITIONS_FILE, index=False)


def update_equity(value):
    today = datetime.now().strftime("%Y-%m-%d")
    row = pd.DataFrame([[today, value]], columns=["Date", "Equity"])

    if os.path.exists(EQUITY_FILE):
        existing = pd.read_csv(EQUITY_FILE)
        df = pd.concat([existing, row], ignore_index=True)
    else:
        df = row

    df.to_csv(EQUITY_FILE, index=False)


def compute_rs(data):
    returns = data.pct_change(RS_LOOKBACK)
    latest = returns.iloc[-1].dropna()
    return latest.sort_values(ascending=False)


def run_engine():

    print("Checking market regime...")

    if crash_monitor():
        send_telegram("⚠ CRASH ALERT: Market drawdown detected.")
        print("Crash detected.")
        return

    if not market_regime():
        send_telegram("Market below regime filter. Staying in cash.")
        print("Market weak.")
        return

    tickers = load_universe()
    data = yf.download(tickers, period="1y", auto_adjust=True, progress=False)["Close"]

    rs_rank = compute_rs(data)
    selected = rs_rank.head(TOP_N).index.tolist()

    positions = load_positions()
    previous = positions["Ticker"].tolist()

    to_buy = list(set(selected) - set(previous))
    to_sell = list(set(previous) - set(selected))
    to_hold = list(set(selected).intersection(previous))

    allocation = CAPITAL / TOP_N

    report = "\nASTIKAR WEEKLY REPORT\n\n"
    report += f"IN: {to_buy}\n"
    report += f"OUT: {to_sell}\n"
    report += f"HOLD: {to_hold}\n\n"

    new_positions = []

    for ticker in selected:
        price = data[ticker].iloc[-1]
        existing = positions[positions["Ticker"] == ticker]

        if existing.empty:
            qty = int(allocation / price)
            new_positions.append([ticker, qty, price, 0])
        else:
            shares = existing["Shares"].values[0]
            avg_cost = existing["Avg_Cost"].values[0]
            adds = existing["Adds"].values[0]

            if adds < PYRAMID_LIMIT and price > avg_cost:
                current_value = shares * price
                if current_value < allocation * MAX_POSITION_MULTIPLIER:
                    add_qty = int(allocation / price)
                    shares += add_qty
                    avg_cost = (avg_cost + price) / 2
                    adds += 1

            new_positions.append([ticker, shares, avg_cost, adds])

    df_new = pd.DataFrame(new_positions,
                          columns=["Ticker", "Shares", "Avg_Cost", "Adds"])

    portfolio_value = 0
    for _, row in df_new.iterrows():
        price = data[row["Ticker"]].iloc[-1]
        portfolio_value += row["Shares"] * price

    update_equity(portfolio_value)
    save_positions(df_new)

    report += f"\nPortfolio Value: ₹{round(portfolio_value,2)}"
    send_telegram(report)

    print("Weekly rebalance complete.")


if __name__ == "__main__":
    run_engine()
