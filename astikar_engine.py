# =============================================
# ASTIKAR PROFESSIONAL PORTFOLIO ENGINE
# Weekly Automated Version with Telegram Alerts
# =============================================

import os
import sys
import pandas as pd
import numpy as np
import yfinance as yf
import requests
from datetime import datetime

# ================= CONFIG =================
CAPITAL = 50000
TOP_N = 10
LOOKBACK_WEEKS = 12
MAX_ADDS = 2
MAX_POSITION_MULTIPLIER = 2.0
REGIME_MA = 200
CRASH_THRESHOLD = -0.12
RSI_THRESHOLD = 55
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"

OUTPUT_FOLDER = "Portfolio_Output"
POSITIONS_FILE = os.path.join(OUTPUT_FOLDER, "portfolio_positions.csv")
EQUITY_FILE = os.path.join(OUTPUT_FOLDER, "equity_curve.csv")

UNIVERSE_FILE = "nifty200.csv"
INDEX_SYMBOL = "^NSEI"

# ================= UTILITIES =================

def timestamp():
    return datetime.now().strftime("%Y_%m_%d_%H_%M")

def ensure_output_folder():
    if not os.path.exists(OUTPUT_FOLDER):
        os.makedirs(OUTPUT_FOLDER)

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print("Telegram send failed:", e)

def safe_read_csv(filepath):
    if os.path.exists(filepath):
        return pd.read_csv(filepath)
    else:
        return pd.DataFrame()

# ================= MARKET FILTERS =================

def extract_close(data):
    if isinstance(data.columns, pd.MultiIndex):
        data = data["Close"]
    if isinstance(data, pd.DataFrame):
        if "Close" in data.columns:
            data = data["Close"]
        else:
            data = data.iloc[:, 0]
    return data.squeeze()

def regime_filter():
    raw = yf.download(INDEX_SYMBOL, period="1y", auto_adjust=True, progress=False)
    index = extract_close(raw)
    if len(index) < REGIME_MA:
        return False
    ma = index.rolling(REGIME_MA).mean()
    return float(index.iloc[-1]) > float(ma.iloc[-1])

def crash_filter():
    raw = yf.download(INDEX_SYMBOL, period="6mo", auto_adjust=True, progress=False)
    index = extract_close(raw)
    if len(index) < 63:
        return True
    ret_3m = (float(index.iloc[-1]) / float(index.iloc[-63])) - 1
    return ret_3m >= CRASH_THRESHOLD

# ================= PORTFOLIO LOGIC =================

def load_universe():
    df = pd.read_csv(UNIVERSE_FILE)
    tickers = df.iloc[:, 0].dropna().astype(str).tolist()
    tickers = [t if t.endswith(".NS") else t + ".NS" for t in tickers]
    return tickers

def load_positions():
    return safe_read_csv(POSITIONS_FILE)

def save_positions(df):
    df.to_csv(POSITIONS_FILE, index=False)

def save_equity(total_equity):
    if not os.path.exists(EQUITY_FILE):
        df = pd.DataFrame(columns=["Date", "Equity"])
    else:
        df = pd.read_csv(EQUITY_FILE)
    df = pd.concat([df, pd.DataFrame([{"Date": timestamp(), "Equity": total_equity}])], ignore_index=True)
    df.to_csv(EQUITY_FILE, index=False)

def generate_portfolio(data):
    weekly = data.resample("W-FRI").last()
    returns = weekly.pct_change(LOOKBACK_WEEKS)
    latest = returns.iloc[-1].dropna()
    momentum = latest.sort_values(ascending=False)
    selected = momentum.head(TOP_N).index.tolist()
    return selected

def generate_orders(selected, capital, price_data):
    positions = load_positions()
    orders = []
    base_allocation = capital / TOP_N

    # Sell positions not in selected
    for ticker in positions["Ticker"].tolist():
        if ticker not in selected:
            orders.append((ticker, "SELL_ALL"))

    # Buy / Pyramid positions
    for ticker in selected:
        if ticker not in price_data.columns:
            continue
        price = float(price_data[ticker].iloc[-1])
        existing = positions[positions["Ticker"] == ticker]

        if existing.empty:
            qty = int(base_allocation / price)
            if qty > 0:
                orders.append((ticker, "BUY", qty))
                new_row = pd.DataFrame([{"Ticker": ticker, "Shares": qty, "Avg_Cost": price, "Adds": 0}])
                positions = pd.concat([positions, new_row], ignore_index=True)
        else:
            shares = existing["Shares"].values[0]
            avg_cost = existing["Avg_Cost"].values[0]
            adds = existing["Adds"].values[0]
            max_value = base_allocation * MAX_POSITION_MULTIPLIER
            current_value = shares * price
            if adds < MAX_ADDS and price > avg_cost and current_value < max_value:
                add_qty = int(base_allocation / price)
                if add_qty > 0:
                    orders.append((ticker, f"ADD_{adds+1}", add_qty))
                    new_shares = shares + add_qty
                    new_avg = ((shares*avg_cost) + (add_qty*price))/new_shares
                    positions.loc[positions["Ticker"]==ticker, ["Shares","Avg_Cost","Adds"]] = [new_shares,new_avg,adds+1]

    save_positions(positions)
    return orders, positions

# ================= MAIN =================

def main():
    ensure_output_folder()
    tickers = load_universe()
    data = yf.download(tickers, period="12mo", interval="1d", auto_adjust=True, progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        data = data["Close"]

    if not regime_filter():
        send_telegram("Market not in Stage 2. Stay in cash.")
        return

    if not crash_filter():
        send_telegram("Market approaching huge loss! Move to cash immediately!")
        return

    selected = generate_portfolio(data)
    orders, positions = generate_orders(selected, CAPITAL, data)

    # Equity summary
    total_equity = 0
    for _, row in positions.iterrows():
        if row["Ticker"] in data.columns:
            total_equity += row["Shares"] * float(data[row["Ticker"]].iloc[-1])
    save_equity(total_equity)

    msg = f"Astikar Weekly Update ({timestamp()}):\nEquity: ₹{round(total_equity,2)}\nPositions: {', '.join(selected)}"
    send_telegram(msg)

    print(msg)
    print("Orders:", orders)

if __name__=="__main__":
    main()
