import streamlit as st
import websocket
import json
import pandas as pd
import ta
import time
import numpy as np
import requests
from datetime import datetime
import os
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
import collections

# --- Database Setup ---
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    st.error("DATABASE_URL environment variable is not set.")
    st.stop()

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, index=True)
    api_token = Column(String)
    is_active = Column(Boolean, default=True)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Configuration and State Variables ---
if 'is_authenticated' not in st.session_state:
    st.session_state.is_authenticated = False
if 'user_id' not in st.session_state:
    st.session_state.user_id = None
if 'bot_running' not in st.session_state:
    st.session_state.bot_running = False
if 'is_trade_open' not in st.session_state:
    st.session_state.is_trade_open = False
if 'log_records' not in st.session_state:
    st.session_state.log_records = []
if 'user_token' not in st.session_state:
    st.session_state.user_token = None
if 'user_token_exists' not in st.session_state:
    st.session_state.user_token_exists = False
if 'tick_history' not in st.session_state:
    st.session_state.tick_history = collections.deque(maxlen=200)

# --- User Authentication and Data Management ---
def login(user_id):
    db = next(get_db())
    try:
        user = db.query(User).filter(User.user_id == user_id).first()
        if user:
            st.session_state.is_authenticated = True
            st.session_state.user_id = user.user_id
            
            if user.api_token:
                st.session_state.user_token_exists = True
                st.session_state.user_token = user.api_token
            else:
                st.session_state.user_token_exists = False
                
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Login successful for User ID: {user_id}")
            return True
        else:
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Login failed: User ID not found.")
            return False
    finally:
        db.close()

def save_api_token(user_id, token):
    db = next(get_db())
    try:
        user = db.query(User).filter(User.user_id == user_id).first()
        if user:
            user.api_token = token
            db.commit()
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ API Token saved for User ID: {user_id}")
            st.session_state.user_token_exists = True
            return True
        return False
    except Exception as e:
        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Failed to save API Token: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def logout():
    st.session_state.is_authenticated = False
    st.session_state.bot_running = False
    st.session_state.is_trade_open = False
    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 Session logged out.")

# --- Functions from your code ---
def ticks_to_ohlc_by_count(ticks_df, tick_count):
    if ticks_df.empty:
        return pd.DataFrame()

    ohlc_data = []
    prices = ticks_df['price'].values
    timestamps = ticks_df['timestamp'].values
    
    for i in range(0, len(prices), tick_count):
        chunk = prices[i:i + tick_count]
        if len(chunk) == tick_count:
            open_price = chunk[0]
            high_price = np.max(chunk)
            low_price = np.min(chunk)
            close_price = chunk[-1]
            ohlc_data.append({
                'timestamp': timestamps[i+tick_count-1],
                'Open': open_price,
                'High': high_price,
                'Low': low_price,
                'Close': close_price,
                'Volume': tick_count
            })
    ohlc_df = pd.DataFrame(ohlc_data)
    if not ohlc_df.empty:
        ohlc_df['timestamp'] = pd.to_datetime(ohlc_df['timestamp'], unit='s')
        ohlc_df.set_index('timestamp', inplace=True)
    return ohlc_df

def find_support_resistance(data):
    supports = []
    resistances = []
    for i in range(1, len(data) - 1):
        prev_candle = data.iloc[i-1]
        current_candle = data.iloc[i]
        next_candle = data.iloc[i+1]
        
        if prev_candle['Close'] < prev_candle['Open'] and next_candle['Close'] > next_candle['Open']:
            supports.append(current_candle['Low'])
            
        if prev_candle['Close'] > prev_candle['Open'] and next_candle['Close'] < next_candle['Open']:
            resistances.append(current_candle['High'])
            
    supports = sorted(list(set(supports)), reverse=True)[:5]
    resistances = sorted(list(set(resistances)))[:5]
    return supports, resistances

def analyze_candlesticks(data):
    score = 0
    if len(data) >= 2:
        last = data.iloc[-1]
        prev = data.iloc[-2]
        
        if (last['Close'] > last['Open'] and prev['Close'] < prev['Open'] and last['High'] > prev['High'] and last['Low'] < prev['Low']):
            score += 30
        
        if (last['Close'] < last['Open'] and prev['Close'] > prev['Open'] and last['High'] > prev['High'] and last['Low'] < prev['Low']):
            score -= 30

        body = abs(last['Close'] - last['Open'])
        lower_shadow = last['Open'] - last['Low'] if last['Open'] > last['Close'] else last['Close'] - last['Low']
        upper_shadow = last['High'] - last['Close'] if last['Open'] > last['Close'] else last['High'] - last['Open']
        
        if last['Close'] > last['Open'] and lower_shadow > body * 2 and upper_shadow < body:
            score += 20
        
        if last['Close'] < last['Open'] and upper_shadow > body * 2 and lower_shadow < body:
            score -= 20
    return score

def analyse_data(data):
    try:
        if data.empty or len(data) < 50:
            return None, "Error: Insufficient data for analysis (less than 50 candles)."

        data = data.tail(50).copy()

        data['RSI'] = ta.momentum.RSIIndicator(data['Close']).rsi()
        data['Stoch_K'] = ta.momentum.StochasticOscillator(data['High'], data['Low'], data['Close']).stoch()
        data['ROC'] = ta.momentum.ROCIndicator(data['Close']).roc()
        
        adx_indicator = ta.trend.ADXIndicator(data['High'], data['Low'], data['Close'])
        data['ADX'] = adx_indicator.adx()
        data['ADX_pos'] = adx_indicator.adx_pos()
        data['ADX_neg'] = adx_indicator.adx_neg()
        
        macd_indicator = ta.trend.MACD(data['Close'])
        data['MACD'] = macd_indicator.macd()
        data['MACD_signal'] = macd_indicator.macd_signal()
        
        ichimoku_indicator = ta.trend.IchimokuIndicator(data['High'], data['Low'])
        data['ichimoku_base_line'] = ichimoku_indicator.ichimoku_base_line()
        data['ichimoku_conversion_line'] = ichimoku_indicator.ichimoku_conversion_line()
        data['ichimoku_a'] = ichimoku_indicator.ichimoku_a()
        data['ichimoku_b'] = ichimoku_indicator.ichimoku_b()
        
        data['ema10'] = ta.trend.EMAIndicator(data['Close'], window=10).ema_indicator()
        data['ema20'] = ta.trend.trend.EMAIndicator(data['Close'], window=20).ema_indicator()

        score = 0
        candlestick_score = analyze_candlesticks(data)
        score += candlestick_score
        
        supports, resistances = find_support_resistance(data)
        last_close = data.iloc[-1]['Close']
        
        for support in supports:
            if abs(last_close - support) / support < 0.0001:  
                score += 40
            elif last_close < support:
                score -= 50
        
        for resistance in resistances:
            if abs(last_close - resistance) / resistance < 0.0001:
                score -= 40
            elif last_close > resistance:
                score += 50
        
        if data['RSI'].iloc[-1] > 70: score -= 20
        elif data['RSI'].iloc[-1] < 30: score += 20
        
        if data['Stoch_K'].iloc[-1] > 80: score -= 20
        elif data['Stoch_K'].iloc[-1] < 20: score += 20

        if data['ROC'].iloc[-1] > 0: score += 10
        elif data['ROC'].iloc[-1] < 0: score -= 10
        
        if data['ADX'].iloc[-1] > 25:
            if data['ADX_pos'].iloc[-1] > data['ADX_neg'].iloc[-1]: score += 30
            elif data['ADX_neg'].iloc[-1] > data['ADX_pos'].iloc[-1]: score -= 30

        if data['MACD'].iloc[-1] > data['MACD_signal'].iloc[-1]: score += 25
        elif data['MACD'].iloc[-1] < data['MACD_signal'].iloc[-1]: score -= 25
        
        last_close_ichimoku = data.iloc[-1]['Close']
        cloud_a = data.iloc[-1]['ichimoku_a']
        cloud_b = data.iloc[-1]['ichimoku_b']
        
        if last_close_ichimoku > max(cloud_a, cloud_b): score += 40
        elif last_close_ichimoku < min(cloud_a, cloud_b): score -= 40
        
        if len(data) >= 20:
            if data['ema10'].iloc[-1] > data['ema20'].iloc[-1]: score += 20
            elif data['ema10'].iloc[-1] < data['ema20'].iloc[-1]: score -= 20
            
            if last_close > data['ema20'].iloc[-1] and last_close > data['ema10'].iloc[-1]: score += 20
            elif last_close < data['ema20'].iloc[-1] and last_close < data['ema10'].iloc[-1]: score -= 20
        
        provisional_decision = ""
        if score > 0:
            provisional_decision = "Buy"
        elif score < 0:
            provisional_decision = "Sell"
        else:
            provisional_decision = "Neutral"

        if len(data) >= 1:
            last_candle = data.iloc[-1]
            last_candle_is_up = last_candle['Close'] > last_candle['Open']
            
            if (provisional_decision == "Buy" and last_candle_is_up) or (provisional_decision == "Sell" and not last_candle_is_up):
                return provisional_decision, None
            elif (provisional_decision == "Buy" and not last_candle_is_up) or (provisional_decision == "Sell" and last_candle_is_up):
                return "Neutral", None
            else:
                return "Neutral", None
        else:
            return provisional_decision, None
            
    except Exception as e:
        return None, f"An error occurred during analysis: {e}"

def place_order(ws, api_token, symbol, action, amount):
    req = {
        "buy": 1,
        "price": amount,
        "type": "CALL" if action == 'buy' else "PUT",
        "duration": 1,
        "duration_unit": "m",
        "symbol": symbol
    }
    ws.send(json.dumps(req))
    response = json.loads(ws.recv())
    return response

# --- Streamlit UI Layout ---
st.title("KHOURYBOT - Autotrading 🤖")
st.markdown("---")

if not st.session_state.is_authenticated:
    st.header("Login to Your Account")
    with st.form("login_form"):
        user_id_input = st.text_input("User ID")
        login_button = st.form_submit_button("Login")
        
        if login_button:
            if login(user_id_input):
                st.success("Login successful!")
                st.experimental_rerun()
            else:
                st.error("Login failed. Please check your User ID.")
elif not st.session_state.user_token_exists:
    st.header("1. First-time Configuration")
    st.info("Please enter your Deriv API Token to link it to your account permanently.")
    with st.form("first_time_config_form"):
        api_token_input = st.text_input("Deriv API Token:", type="password")
        config_button = st.form_submit_button("Save and Continue")
        
        if config_button:
            if save_api_token(st.session_state.user_id, api_token_input):
                st.session_state.user_token = api_token_input
                st.success("API Token saved successfully! Redirecting...")
                st.experimental_rerun()
            else:
                st.error("Failed to save token. Please try again.")
else:
    st.header("1. Bot Settings")
    st.write(f"Logged in as: **{st.session_state.user_id}**")
    st.write("Your Deriv API Token has been loaded automatically.")
    
    symbol_input = "R_100"
    amount_input = st.number_input("Amount to trade", min_value=0.5, step=0.5)

    start_button = st.button("Start Bot")
    stop_button = st.button("Stop Bot")

    if start_button:
        st.session_state.bot_running = True
    if stop_button:
        st.session_state.bot_running = False

    st.markdown("---")
    st.header("2. Live Logs")
    log_area = st.empty()
    log_area.text_area("Logs", "\n".join(st.session_state.log_records), height=400, key="logs")

    if st.session_state.bot_running:
        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🟢 Bot is running for {symbol_input} with {amount_input}$")
        
        def get_deriv_ws_connection(api_token):
            try:
                ws = websocket.WebSocket()
                ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929")
                return ws
            except Exception as e:
                st.error(f"Failed to connect to Deriv: {e}")
                return None

        def authorize_deriv(ws, api_token):
            req = {"authorize": api_token}
            ws.send(json.dumps(req))
            response = json.loads(ws.recv())
            return response

        ws = get_deriv_ws_connection(st.session_state.user_token)
        if ws:
            auth_response = authorize_deriv(ws, st.session_state.user_token)
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🔑 Authorization status: {auth_response.get('authorize', {}).get('is_virtual', 'N/A')}")
            
            req = {"ticks_history": symbol_input, "end": "latest", "count": 250, "style": "ticks"}
            ws.send(json.dumps(req))

            while st.session_state.bot_running:
                try:
                    tick_data = json.loads(ws.recv())
                    if 'history' in tick_data:
                        ticks = tick_data['history']['prices']
                        timestamps = tick_data['history']['times']
                        df_ticks = pd.DataFrame({'timestamp': timestamps, 'price': ticks})

                        candles_5ticks = ticks_to_ohlc_by_count(df_ticks, 5)
                        
                        entry_signal, error = analyse_data(candles_5ticks)
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 Signal: {entry_signal.upper()}")
                        
                        if entry_signal in ['Buy', 'Sell'] and not st.session_state.is_trade_open:
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ➡️ Placing a {entry_signal.upper()} order...")
                            order_response = place_order(ws, st.session_state.user_token, symbol_input, entry_signal, amount_input)
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Order placed. ID: {order_response.get('buy', {}).get('contract_id')}")
                            st.session_state.is_trade_open = True
                        
                        log_area.text_area("Logs", "\n".join(st.session_state.log_records), height=400, key="logs")
                    
                    time.sleep(10)
                
                except websocket.WebSocketTimeoutException:
                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⏳ Waiting for next tick...")
                except Exception as e:
                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error: {e}")
                    time.sleep(5)
            
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 Bot stopped.")
