import os
import sys
import time
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
import requests

# ================= CONFIG =================
UNIVERSE_FILE = "nifty200.csv"
SECTOR_FILE = "sector_mapping.csv"
POSITIONS_FILE = "portfolio_positions.csv"
EQUITY_FILE = "equity_curve.csv"
OUTPUT_FOLDER = "Portfolio_Outputs"

CAPITAL = 50000  # Starting capital
TOP_N = 10       # Maximum positions
MAX_ADDS = 2     # Max pyramiding adds
MAX_POSITION_MULTIPLIER = 2.0
RS_LOOKBACK_WEEKS = 12
REGIME_MA = 200
CRASH_THRESHOLD = -0.12

TELEGRAM_BOT = "7980904485:AAGJx_cfhsEdwm6rA_utvX--MjusqTnEk4M"
TELEGRAM_CHAT_ID = "8144938221"

# Colors for terminal
GREEN = "\033[1;32m"
RED = "\033[1;31m"
RESET = "\033[0m"

# ==========================================

def timestamp():
    return datetime.now().strftime("%Y_%m_%d_%H_%M")

def ensure_folder(folder):
    if not os.path.exists(folder):
        os.makedirs(folder)

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, data=payload)
    except:
        pass

# ================= LOAD DATA =================

def load_universe():
    df = pd.read_csv(UNIVERSE_FILE)
    tickers = df['Symbol'].dropna().astype(str).tolist()
    tickers = [t if t.endswith(".NS") else t+".NS" for t in tickers]
    return tickers

def load_sector_mapping():
    if not os.path.exists(SECTOR_FILE):
        return {}
    df = pd.read_csv(SECTOR_FILE)
    df["Ticker"] = df["Symbol"].astype(str).apply(lambda x: x if x.endswith(".NS") else x+".NS")
    return dict(zip(df["Ticker"], df["Sector"]))

def load_positions():
    if os.path.exists(POSITIONS_FILE):
        return pd.read_csv(POSITIONS_FILE)
    return pd.DataFrame(columns=["Ticker","Shares","Avg_Cost","Adds"])

def save_positions(df):
    df.to_csv(POSITIONS_FILE, index=False)

# ================= FILTERS =================

def regime_filter(index_symbol="^NSEI"):
    raw = yf.download(index_symbol, period="1y", interval="1d", progress=False)
    close = raw['Close']
    if len(close) < REGIME_MA:
        return False
    ma = close.rolling(REGIME_MA).mean()
    return float(close.iloc[-1]) > float(ma.iloc[-1])

def crash_filter(index_symbol="^NSEI"):
    raw = yf.download(index_symbol, period="6mo", interval="1d", progress=False)
    close = raw['Close']
    if len(close) < 63:
        return True
    ret_3m = (float(close.iloc[-1]) / float(close.iloc[-63])) - 1
    return ret_3m >= CRASH_THRESHOLD

# ================= STRATEGY =================

def compute_rs(data):
    weekly = data.resample("W-FRI").last()
    returns = weekly.pct_change(RS_LOOKBACK_WEEKS)
    latest = returns.iloc[-1].dropna()
    rs_ranked = latest.sort_values(ascending=False)
    return rs_ranked.head(TOP_N).index.tolist()

# ================= ORDER ENGINE =================

def generate_orders(selected, price_data, positions):
    sector_map = load_sector_mapping()
    orders = []

    base_allocation = CAPITAL / TOP_N

    prev_portfolio = positions["Ticker"].tolist()
    sell_list = list(set(prev_portfolio) - set(selected))

    # Sell obsolete positions
    for ticker in sell_list:
        sector = sector_map.get(ticker, "Unknown")
        orders.append((sector, ticker, "SELL_ALL", "", "", ""))

    # Buy new positions or pyramid existing
    for ticker in selected:
        if ticker not in price_data.columns:
            continue
        price = float(price_data[ticker].iloc[-1])
        sector = sector_map.get(ticker, "Unknown")
        existing = positions[positions["Ticker"]==ticker]

        if existing.empty:
            qty = int(base_allocation / price)
            if qty>0:
                orders.append((sector, ticker, "BUY", qty, round(price,2), round(qty*price,2)))
                new_row = pd.DataFrame([{"Ticker":ticker,"Shares":qty,"Avg_Cost":price,"Adds":0}])
                positions = pd.concat([positions,new_row], ignore_index=True)
        else:
            shares = existing["Shares"].values[0]
            avg_cost = existing["Avg_Cost"].values[0]
            adds = existing["Adds"].values[0]

            max_position = base_allocation * MAX_POSITION_MULTIPLIER
            current_value = shares*price

            if adds < MAX_ADDS and price > avg_cost and current_value < max_position:
                add_qty = int(base_allocation / price)
                if add_qty>0:
                    add_label = f"ADD_{adds+1}"
                    orders.append((sector, ticker, add_label, add_qty, round(price,2), round(add_qty*price,2)))
                    new_shares = shares+add_qty
                    new_avg = ((shares*avg_cost)+(add_qty*price))/new_shares
                    positions.loc[positions["Ticker"]==ticker, ["Shares","Avg_Cost","Adds"]] = [new_shares,new_avg,adds+1]

    positions = positions[~positions["Ticker"].isin(sell_list)]
    return orders, positions

# ================= MAIN ENGINE =================

def run_engine():
    ensure_folder(OUTPUT_FOLDER)
    tickers = load_universe()
    positions = load_positions()

    # Download data
    data = yf.download(tickers, period="12mo", interval="1d", auto_adjust=True, progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        data = data["Close"]

    # Market check
    if not regime_filter():
        send_telegram("⚠ Market not in Stage 2. Stay in cash!")
        return
    if not crash_filter():
        send_telegram("⚠ Market crash alert! Immediate action required!")
        return

    # RS ranking for top momentum
    selected = compute_rs(data)
    orders, updated_positions = generate_orders(selected, data, positions)

    # Capital summary
    total_used = 0
    for _, row in updated_positions.iterrows():
        ticker = row["Ticker"]
        if ticker in data.columns:
            total_used += row["Shares"] * float(data[ticker].iloc[-1])
    balance = CAPITAL - total_used

    # Telegram summary
    msg = f"📊 Weekly Portfolio Update ({timestamp()})\nCapital: ₹{CAPITAL}\nUsed: ₹{round(total_used,2)}\nBalance: ₹{round(balance,2)}\nPositions: {', '.join(updated_positions['Ticker'].tolist())}"
    send_telegram(msg)

    # Save positions and equity
    save_positions(updated_positions)
    equity_curve = pd.DataFrame({"Date":[timestamp()],"Equity":[CAPITAL]})
    equity_curve.to_csv(EQUITY_FILE, index=False)

    # Save orders
    if orders:
        df_orders = pd.DataFrame(orders, columns=["Sector","Ticker","Action","Qty","Price","Allocation"])
        filename = os.path.join(OUTPUT_FOLDER,f"weekly_orders_{timestamp()}.csv")
        df_orders.to_csv(filename, index=False)
        print(df_orders)

if __name__=="__main__":
    run_engine()
