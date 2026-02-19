# ============================================
# ASTIKAR PORTFOLIO MANAGER - PRO
# Professional version: weekly execution + Telegram + pyramiding
# ============================================

import os
import pandas as pd
import numpy as np
import yfinance as yf
import requests
from datetime import datetime

# ================= CONFIG =================
UNIVERSE_FILE = "nifty200.csv"
SECTOR_FILE = "sector_mapping.csv"
POSITIONS_FILE = "portfolio_positions.csv"
OUTPUT_FOLDER = "Monthly Portfolio_Do not delete"
INDEX_SYMBOL = "^NSEI"
CAPITAL = 50000
TOP_N = 10
MAX_ADDS = 2
MAX_POSITION_MULTIPLIER = 2.0
REGIME_MA = 200
CRASH_THRESHOLD = -0.12

# Telegram Config
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"

# ================= UTILITIES =================
def timestamp():
    return datetime.now().strftime("%Y_%m_%d_%H_%M")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print("Telegram error:", e)

def ensure_folder():
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

def safe_read_csv(filepath):
    try:
        return pd.read_csv(filepath, encoding="utf-8")
    except:
        return pd.read_csv(filepath, encoding="latin1")

# ================= LOADERS =================
def load_universe():
    df = safe_read_csv(UNIVERSE_FILE)
    df.columns = df.columns.str.strip()
    tickers = df.iloc[:,0].dropna().astype(str).tolist()
    tickers = [t if t.endswith(".NS") else t+".NS" for t in tickers]
    return tickers

def load_sector_mapping():
    if not os.path.exists(SECTOR_FILE):
        return {}
    df = safe_read_csv(SECTOR_FILE)
    df.columns = df.columns.str.strip()
    if "Symbol" not in df.columns or "Sector" not in df.columns:
        return {}
    df["Symbol"] = df["Symbol"].astype(str).str.strip()
    df["Ticker"] = df["Symbol"].apply(lambda x: x if x.endswith(".NS") else x+".NS")
    return dict(zip(df["Ticker"], df["Sector"]))

def load_positions():
    if os.path.exists(POSITIONS_FILE):
        df = safe_read_csv(POSITIONS_FILE)
        # ensure columns exist
        for col in ["Ticker","Shares","Avg_Cost","Adds"]:
            if col not in df.columns:
                df[col] = pd.Series(dtype="object" if col=="Ticker" else "float")
        return df
    return pd.DataFrame({
        "Ticker": pd.Series(dtype="str"),
        "Shares": pd.Series(dtype="int"),
        "Avg_Cost": pd.Series(dtype="float"),
        "Adds": pd.Series(dtype="int")
    })

def save_positions(df):
    df.to_csv(POSITIONS_FILE, index=False)

# ================= FILTERS =================
def extract_close(data):
    if isinstance(data.columns, pd.MultiIndex):
        data = data["Close"]
    if isinstance(data, pd.DataFrame):
        if "Close" in data.columns:
            data = data["Close"]
        else:
            data = data.iloc[:,0]
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
    ret_3m = (float(index.iloc[-1])/float(index.iloc[-63])) - 1
    return ret_3m >= CRASH_THRESHOLD

# ================= PORTFOLIO LOGIC =================
def generate_portfolio(data):
    weekly = data.resample("W-FRI").last()
    if len(weekly) < 12+1:
        raise Exception("Not enough weekly history.")
    valid_columns = weekly.columns[weekly.notna().sum() >= 13]
    weekly = weekly[valid_columns]
    returns = weekly.pct_change(12)
    latest = returns.iloc[-1].dropna()
    momentum = latest.sort_values(ascending=False)
    selected = momentum.head(TOP_N).index.tolist()
    weights = {ticker: 1/TOP_N for ticker in selected}
    return selected, weights

def generate_orders(selected, weights, capital, price_data):
    sector_map = load_sector_mapping()
    positions = load_positions().copy()
    prev_portfolio = positions["Ticker"].tolist()
    sell_list = list(set(prev_portfolio)-set(selected))
    orders = []
    base_allocation = capital*(1/TOP_N)

    # SELL OUT UNSELECTED
    for ticker in sell_list:
        sector = sector_map.get(ticker,"Unknown")
        orders.append((sector,ticker,"SELL_ALL","","",""))
        positions = positions[positions["Ticker"]!=ticker]

    # BUY / PYRAMID
    for ticker in selected:
        if ticker not in price_data.columns:
            continue
        price = float(price_data[ticker].iloc[-1])
        sector = sector_map.get(ticker,"Unknown")
        existing = positions[positions["Ticker"]==ticker]
        if existing.empty:
            qty = int(base_allocation/price)
            if qty>0:
                orders.append((sector,ticker,"BUY",qty,round(price,2),round(qty*price,2)))
                new_row = pd.DataFrame([{
                    "Ticker": ticker,
                    "Shares": qty,
                    "Avg_Cost": price,
                    "Adds": 0
                }])
                positions = pd.concat([positions,new_row],ignore_index=True)
        else:
            shares = existing["Shares"].values[0]
            avg_cost = existing["Avg_Cost"].values[0]
            adds = existing["Adds"].values[0]
            max_value = base_allocation*MAX_POSITION_MULTIPLIER
            curr_value = shares*price
            if adds<MAX_ADDS and price>avg_cost and curr_value<max_value:
                add_qty = int(base_allocation/price)
                if add_qty>0:
                    add_label = f"ADD_{adds+1}"
                    orders.append((sector,ticker,add_label,add_qty,round(price,2),round(add_qty*price,2)))
                    new_shares = shares+add_qty
                    new_avg = ((shares*avg_cost)+(add_qty*price))/new_shares
                    positions.loc[positions["Ticker"]==ticker,["Shares","Avg_Cost","Adds"]] = [new_shares,new_avg,adds+1]
    return orders, positions

# ================= MAIN =================
def main():
    ensure_folder()
    tickers = load_universe()
    data = yf.download(tickers, period="12mo", interval="1d", auto_adjust=True, progress=True)
    if isinstance(data.columns,pd.MultiIndex):
        data = data["Close"]

    # Market Filters
    if not regime_filter():
        send_telegram("Market not in bullish stage. Stay in cash!")
        print("Market not bullish. Exiting.")
        return
    if not crash_filter():
        send_telegram("Market near crash threshold! Stay in cash!")
        print("Crash threshold reached. Exiting.")
        return

    # Portfolio
    try:
        selected, weights = generate_portfolio(data)
        orders, positions = generate_orders(selected, weights, CAPITAL, data)
    except Exception as e:
        send_telegram(f"Engine error: {e}")
        raise e

    # Capital Summary
    total_used = sum([row[4]*row[3] if row[3] else 0 for row in orders])
    balance = CAPITAL - total_used

    # CSV logs
    timestamp_str = timestamp()
    if orders:
        df = pd.DataFrame(orders, columns=["Sector","Ticker","Action","Quantity","Price","Allocation_Value"])
        out_file = os.path.join(OUTPUT_FOLDER,f"weekly_orders_{timestamp_str}.csv")
        df.to_csv(out_file,index=False)
        print("Orders saved to", out_file)
        send_telegram(f"Weekly Portfolio executed.\nOrders saved: {out_file}\nBalance: ₹{round(balance,2)}")
    else:
        send_telegram("No trades this week. Portfolio remains unchanged.")

    save_positions(positions)
    print("Positions updated.")

if __name__=="__main__":
    main()
