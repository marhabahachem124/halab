import streamlit as st
import websocket
import json
import pandas as pd
import ta
import time
import numpy as np
import requests
from datetime import datetime, timedelta
import os
import collections
import random

# --- File-based Licensing ---
# This file contains the User IDs that are allowed to run the bot.
ALLOWED_USERS_FILE = 'user_ids.txt'
# This file stores the unique ID for the user's device.
DEVICE_ID_FILE = 'khourybot_user_id.txt'

# --- Configuration and State Variables ---
if 'is_authenticated' not in st.session_state:
    st.session_state.is_authenticated = False
if 'user_id' not in st.session_state:
    st.session_state.user_id = None
if 'bot_running' in st.session_state and st.session_state.bot_running:
    if 'current_amount' not in st.session_state:
        st.session_state.current_amount = st.session_state.base_amount
    if 'consecutive_losses' not in st.session_state:
        st.session_state.consecutive_losses = 0
else:
    st.session_state.bot_running = False

if 'is_trade_open' not in st.session_state:
    st.session_state.is_trade_open = False
if 'trade_start_time' not in st.session_state:
    st.session_state.trade_start_time = None
if 'contract_id' not in st.session_state:
    st.session_state.contract_id = None
if 'log_records' not in st.session_state:
    st.session_state.log_records = []
if 'user_token' not in st.session_state:
    st.session_state.user_token = None
if 'tick_history' not in st.session_state:
    st.session_state.tick_history = collections.deque(maxlen=200)
if 'current_amount' not in st.session_state:
    st.session_state.current_amount = 0.5
if 'base_amount' not in st.session_state:
    st.session_state.base_amount = 0.5
if 'consecutive_losses' not in st.session_state:
    st.session_state.consecutive_losses = 0
if 'initial_balance' not in st.session_state:
    st.session_state.initial_balance = None
if 'tp_target' not in st.session_state:
    st.session_state.tp_target = None
if 'max_consecutive_losses' not in st.session_state:
    st.session_state.max_consecutive_losses = 5
if 'last_action_time' not in st.session_state:
    st.session_state.last_action_time = datetime.min
if 'page' not in st.session_state:
    st.session_state.page = 'inputs'
if 'is_analysing' not in st.session_state:
    st.session_state.is_analysing = False

# --- License Check and User ID Generation ---
def get_user_id():
    """
    Generates or retrieves a unique numerical ID for the user's device.
    The ID is stored in a visible file to persist across sessions.
    """
    if 'user_id_session' not in st.session_state:
        if os.path.exists(DEVICE_ID_FILE):
            try:
                with open(DEVICE_ID_FILE, 'r') as f:
                    st.session_state.user_id_session = f.read().strip()
            except Exception as e:
                st.error(f"Error reading {DEVICE_ID_FILE}: {e}")
                st.session_state.user_id_session = str(random.randint(1000000000000000, 9999999999999999))
        else:
            new_id = str(random.randint(1000000000000000, 9999999999999999))
            try:
                with open(DEVICE_ID_FILE, 'w') as f:
                    f.write(new_id)
                st.session_state.user_id_session = new_id
            except Exception as e:
                st.error(f"Error creating {DEVICE_ID_FILE}: {e}")
                st.session_state.user_id_session = new_id
    return st.session_state.user_id_session

def is_user_allowed(user_id):
    """Checks if the User ID is in the allowed list."""
    try:
        with open(ALLOWED_USERS_FILE, 'r') as f:
            allowed_ids = {line.strip() for line in f}
            return user_id in allowed_ids
    except FileNotFoundError:
        st.error(f"Error: '{ALLOWED_USERS_FILE}' not found. Please create this file with a list of allowed User IDs.")
        return False
    except Exception as e:
        st.error(f"Error reading '{ALLOWED_USERS_FILE}': {e}")
        return False

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
    signal = "Neutral"
    if len(data) >= 2:
        last = data.iloc[-1]
        prev = data.iloc[-2]
        
        # Bullish Engulfing
        if (last['Close'] > last['Open'] and prev['Close'] < prev['Open'] and last['High'] > prev['High'] and last['Low'] < prev['Low']):
            signal = "Buy"
        
        # Bearish Engulfing
        if (last['Close'] < last['Open'] and prev['Close'] > prev['Open'] and last['High'] > prev['High'] and last['Low'] < prev['Low']):
            signal = "Sell"

        # Hammer (Bullish)
        body = abs(last['Close'] - last['Open'])
        lower_shadow = last['Open'] - last['Low'] if last['Open'] > last['Close'] else last['Close'] - last['Low']
        upper_shadow = last['High'] - last['Close'] if last['Open'] > last['Close'] else last['High'] - last['Open']
        
        if last['Close'] > last['Open'] and lower_shadow > body * 2 and upper_shadow < body:
            signal = "Buy"
        
        # Inverted Hammer (Bearish)
        if last['Close'] < last['Open'] and upper_shadow > body * 2 and lower_shadow < body:
            signal = "Sell"
    return signal

def analyse_data(data):
    try:
        if data.empty or len(data) < 50:
            return None, "Error: Insufficient data for analysis (less than 50 candles)."

        data = data.tail(50).copy()

        signals = []

        # RSI Signal
        data['RSI'] = ta.momentum.RSIIndicator(data['Close']).rsi()
        if data['RSI'].iloc[-1] < 30: signals.append("Buy")
        elif data['RSI'].iloc[-1] > 70: signals.append("Sell")

        # Stoch Signal
        data['Stoch_K'] = ta.momentum.StochasticOscillator(data['High'], data['Low'], data['Close']).stoch()
        if data['Stoch_K'].iloc[-1] < 20: signals.append("Buy")
        elif data['Stoch_K'].iloc[-1] > 80: signals.append("Sell")

        # ROC Signal
        data['ROC'] = ta.momentum.ROCIndicator(data['Close']).roc()
        if data['ROC'].iloc[-1] > 0: signals.append("Buy")
        elif data['ROC'].iloc[-1] < 0: signals.append("Sell")
        
        # ADX Signal
        adx_indicator = ta.trend.ADXIndicator(data['High'], data['Low'], data['Close'])
        data['ADX'] = adx_indicator.adx()
        data['ADX_pos'] = adx_indicator.adx_pos()
        data['ADX_neg'] = adx_indicator.adx_neg()
        if data['ADX'].iloc[-1] > 25:
            if data['ADX_pos'].iloc[-1] > data['ADX_neg'].iloc[-1]: signals.append("Buy")
            elif data['ADX_neg'].iloc[-1] > data['ADX_pos'].iloc[-1]: signals.append("Sell")

        # MACD Signal
        macd_indicator = ta.trend.MACD(data['Close'])
        data['MACD'] = macd_indicator.macd()
        data['MACD_signal'] = macd_indicator.macd_signal()
        if data['MACD'].iloc[-1] > data['MACD_signal'].iloc[-1]: signals.append("Buy")
        elif data['MACD'].iloc[-1] < data['MACD_signal'].iloc[-1]: signals.append("Sell")

        # Ichimoku Signal
        ichimoku_indicator = ta.trend.IchimokuIndicator(data['High'], data['Low'])
        data['ichimoku_a'] = ichimoku_indicator.ichimoku_a()
        data['ichimoku_b'] = ichimoku_indicator.ichimoku_b()
        last_close_ichimoku = data.iloc[-1]['Close']
        cloud_a = data.iloc[-1]['ichimoku_a']
        cloud_b = data.iloc[-1]['ichimoku_b']
        if last_close_ichimoku > max(cloud_a, cloud_b): signals.append("Buy")
        elif last_close_ichimoku < min(cloud_a, cloud_b): signals.append("Sell")

        # EMA Signal
        if len(data) >= 20:
            data['ema10'] = ta.trend.EMAIndicator(data['Close'], window=10).ema_indicator()
            data['ema20'] = ta.trend.EMAIndicator(data['Close'], window=20).ema_indicator()
            if data['ema10'].iloc[-1] > data['ema20'].iloc[-1]: signals.append("Buy")
            elif data['ema10'].iloc[-1] < data['ema20'].iloc[-1]: signals.append("Sell")
            
            last_close = data.iloc[-1]['Close']
            if last_close > data['ema20'].iloc[-1] and last_close > data['ema10'].iloc[-1]: signals.append("Buy")
            elif last_close < data['ema20'].iloc[-1] and last_close < data['ema10'].iloc[-1]: signals.append("Sell")
        
        # Candlestick Signal
        candlestick_signal = analyze_candlesticks(data)
        if candlestick_signal != "Neutral":
            signals.append(candlestick_signal)

        # Support & Resistance Signal
        supports, resistances = find_support_resistance(data)
        last_close = data.iloc[-1]['Close']
        for support in supports:
            if abs(last_close - support) / support < 0.0001:  
                signals.append("Buy")
            elif last_close < support:
                signals.append("Sell")
        
        for resistance in resistances:
            if abs(last_close - resistance) / resistance < 0.0001:
                signals.append("Sell")
            elif last_close > resistance:
                signals.append("Buy")

        # Determine majority signal
        buy_count = signals.count("Buy")
        sell_count = signals.count("Sell")
        
        final_decision = "Neutral"
        if buy_count > sell_count:
            final_decision = "Buy"
        elif sell_count > buy_count:
            final_decision = "Sell"
        
        return final_decision, buy_count, sell_count, None
            
    except Exception as e:
        return None, 0, 0, f"An error occurred during analysis: {e}"

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

def check_contract_status(ws, contract_id):
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    ws.send(json.dumps(req))
    while True:
        try:
            response = json.loads(ws.recv())
            if response.get('msg_type') == 'proposal_open_contract':
                is_sold = response['proposal_open_contract']['is_sold']
                if is_sold:
                    return response['proposal_open_contract']
        except websocket.WebSocketTimeoutException:
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Timeout waiting for contract info. Re-checking...")
            time.sleep(5)
        except Exception as e:
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error while checking contract status: {e}")
            return None

def get_balance(ws):
    req = {"balance": 1, "subscribe": 1}
    ws.send(json.dumps(req))
    response = json.loads(ws.recv())
    return response.get('balance', {}).get('balance')

# --- Main App Logic and UI ---
st.title("KHOURYBOT - Autotrading 🤖")

# --- Authentication Section ---
user_id = get_user_id()
st.session_state.user_id = user_id

if not st.session_state.is_authenticated:
    st.header("Login to Your Account")
    if is_user_allowed(user_id):
        st.session_state.is_authenticated = True
        st.success("Your device is activated! Redirecting to settings...")
        st.balloons()
        st.rerun()
    else:
        st.warning("Your device is not yet activated. To activate the bot, please send this User ID to the bot administrator:")
        st.code(user_id)
        st.info("After activation, simply refresh this page to continue.")

else:
    # --- Status and Timer Display ---
    status_placeholder = st.empty()
    timer_placeholder = st.empty()

    if st.session_state.bot_running:
        if not st.session_state.is_trade_open:
            status_placeholder.info("Analysing...")
            now = datetime.now()
            next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
            seconds_left = max(0, (next_minute - now).seconds - 5)
            timer_placeholder.metric("Next action in", f"{seconds_left}s")
        else:
            status_placeholder.info("Waiting for trade result...")
            timer_placeholder.empty()
    else:
        status_placeholder.empty()
        timer_placeholder.empty()

    # --- Check for pending trade result ---
    if st.session_state.is_trade_open and st.session_state.trade_start_time:
        if datetime.now() >= st.session_state.trade_start_time + timedelta(seconds=70):
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⏱️ Checking trade result...")
            
            ws = None
            try:
                ws = websocket.WebSocket()
                ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929")
                
                auth_req = {"authorize": st.session_state.user_token}
                ws.send(json.dumps(auth_req))
                auth_response = json.loads(ws.recv())
                
                if auth_response.get('error'):
                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Reconnection failed. Authorization error.")
                    st.session_state.bot_running = False
                    st.session_state.is_trade_open = False
                else:
                    contract_info = check_contract_status(ws, st.session_state.contract_id)
                    
                    if contract_info:
                        profit = contract_info.get('profit', 0)
                        is_win = profit > 0
                        
                        if is_win:
                            st.session_state.consecutive_losses = 0
                            st.session_state.current_amount = st.session_state.base_amount
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🎉 WIN! Profit: {profit}")
                        else:
                            st.session_state.consecutive_losses += 1
                            st.session_state.current_amount *= 2.2
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💔 LOSS! Loss: {profit}")
                        
                        st.session_state.is_trade_open = False
                        
                        current_balance = get_balance(ws)
                        if current_balance is not None:
                            if st.session_state.initial_balance is None:
                                st.session_state.initial_balance = current_balance
                            
                            if st.session_state.tp_target and current_balance - st.session_state.initial_balance >= st.session_state.tp_target:
                                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🤑 TP Reached! Bot stopped.")
                                st.session_state.bot_running = False
                            
                        if st.session_state.consecutive_losses >= st.session_state.max_consecutive_losses:
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 SL Reached ({st.session_state.max_consecutive_losses} consecutive losses)! Bot stopped.")
                            st.session_state.bot_running = False
                            
                    else:
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Could not get contract info.")
                        st.session_state.is_trade_open = False
                        
            except Exception as e:
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error getting result: {e}")
            finally:
                if ws:
                    ws.close()
            
            st.session_state.trade_start_time = None
            st.session_state.contract_id = None
            st.rerun()

    # --- Main Bot Logic (runs once per minute) ---
    if st.session_state.bot_running and not st.session_state.is_trade_open:
        now = datetime.now()
        seconds_in_minute = now.second
        
        # Check if 60 seconds have passed since the last action
        if (now - st.session_state.last_action_time).seconds >= 60:
            st.session_state.last_action_time = now
            if seconds_in_minute >= 55:
                
                ws = None
                try:
                    ws = websocket.WebSocket()
                    ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929")
                    
                    auth_req = {"authorize": st.session_state.user_token}
                    ws.send(json.dumps(auth_req))
                    auth_response = json.loads(ws.recv())
                    
                    if auth_response.get('error'):
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Authorization failed: {auth_response['error']['message']}")
                    else:
                        if st.session_state.initial_balance is None:
                            st.session_state.initial_balance = get_balance(ws)
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💰 Initial Balance: {st.session_state.initial_balance}")
                            
                        req = {"ticks_history": "R_100", "end": "latest", "count": 70, "style": "ticks"}
                        ws.send(json.dumps(req))
                        tick_data = json.loads(ws.recv())
                        
                        if 'history' in tick_data:
                            ticks = tick_data['history']['prices']
                            timestamps = tick_data['history']['times']
                            df_ticks = pd.DataFrame({'timestamp': timestamps, 'price': ticks})
                            
                            if len(df_ticks) >= 70:
                                candles_5ticks = ticks_to_ohlc_by_count(df_ticks.tail(70), 5)

                                provisional_decision, buy_count, sell_count, error_msg = analyse_data(candles_5ticks)
                                
                                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 📈 Buy Signals: {buy_count}, Sell Signals: {sell_count}")

                                last_5_ticks = df_ticks.tail(5)
                                last_5_signal = "Neutral"
                                if last_5_ticks['price'].iloc[-1] > last_5_ticks['price'].iloc[0]:
                                    last_5_signal = "Buy"
                                elif last_5_ticks['price'].iloc[-1] < last_5_ticks['price'].iloc[0]:
                                    last_5_signal = "Sell"

                                final_signal = "Neutral"
                                if provisional_decision == "Buy" and last_5_signal == "Buy":
                                    final_signal = "Buy"
                                elif provisional_decision == "Sell" and last_5_signal == "Sell":
                                    final_signal = "Sell"
                                
                                if final_signal is not None and final_signal in ['Buy', 'Sell']:
                                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 Provisional Signal: {provisional_decision}, Last 5 Ticks Signal: {last_5_signal}")
                                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Final Signal: {final_signal.upper()}")
                                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ➡️ Placing a {final_signal.upper()} order with {st.session_state.current_amount}$")
                                    order_response = place_order(ws, st.session_state.user_token, "R_100", final_signal, st.session_state.current_amount)
                                    
                                    if 'buy' in order_response:
                                        st.session_state.is_trade_open = True
                                        st.session_state.trade_start_time = datetime.now()
                                        st.session_state.contract_id = order_response.get('buy', {}).get('contract_id')
                                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Order placed. ID: {st.session_state.contract_id}")
                                    else:
                                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Order failed: {order_response}")
                                else:
                                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ No strong signal found. No trade placed.")


                except Exception as e:
                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error during trading cycle: {e}")
                finally:
                    if ws:
                        ws.close()
                
                st.rerun()

    # --- Display Pages based on state ---
    if st.session_state.page == 'inputs':
        st.header("1. Bot Settings")
        
        # New API Token input field
        st.session_state.user_token = st.text_input("Enter your Deriv API Token:", type="password", key="api_token_input")
        
        st.session_state.base_amount = st.number_input("Base Amount", min_value=0.5, step=0.5, value=st.session_state.base_amount)
        st.session_state.tp_target = st.number_input("Take Profit (TP)", min_value=1.0, step=1.0, value=st.session_state.tp_target)
        
        start_button = st.button("Start Bot")
        stop_button = st.button("Stop Bot")

        if start_button:
            if not st.session_state.user_token:
                st.error("Please enter a valid API Token before starting the bot.")
            else:
                st.session_state.bot_running = True
                st.session_state.current_amount = st.session_state.base_amount
                st.session_state.consecutive_losses = 0
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🟢 Bot has started.")
                st.rerun()
        
        if stop_button:
            st.session_state.bot_running = False
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 Bot stopped by user.")
            st.rerun()
            
    elif st.session_state.page == 'logs':
        st.header("2. Live Logs")
        with st.container(height=600):
            st.text_area("Logs", "\n".join(st.session_state.log_records), height=600)

    # --- Footer with Navigation Buttons ---
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Inputs"):
            st.session_state.page = 'inputs'
            st.rerun()
    with col2:
        if st.button("Logs"):
            st.session_state.page = 'logs'
            st.rerun()
            
    # Rerun the script periodically to check the time and trigger the next cycle
    time.sleep(1)
    st.rerun()
