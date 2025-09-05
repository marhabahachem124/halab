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
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker, declarative_base
import streamlit.components.v1 as components

# --- File-Based Licensing System ---
# هذا الملف يحتوي على معرفات الأجهزة للمستخدمين المسموح لهم بتشغيل البوت.
ALLOWED_USERS_FILE = 'user_ids.txt'

# --- Database Setup ---
DATABASE_URL = "postgresql://khourybot_db_user:wlVAwKwLhfzzH9HFsRMNo3IOo4dX6DYm@dpg-d2smi46r433s73frbbcg-a/khourybot_db"
engine = sa.create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base()

class Device(Base):
    __tablename__ = 'devices'
    id = sa.Column(sa.Integer, primary_key=True)
    device_id = sa.Column(sa.String, unique=True, nullable=False)

Base.metadata.create_all(engine)

# --- Initialization and App State Variables ---
if 'is_authenticated' not in st.session_state:
    st.session_state.is_authenticated = False
if 'user_id' not in st.session_state:
    st.session_state.user_id = None
if 'bot_running' not in st.session_state:
    st.session_state.bot_running = False
if 'current_amount' not in st.session_state:
    st.session_state.current_amount = 0.5
if 'base_amount' not in st.session_state:
    st.session_state.base_amount = 0.5
if 'consecutive_losses' not in st.session_state:
    st.session_state.consecutive_losses = 0
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

# --- License Check and Device ID Generation ---
def get_or_create_device_id():
    """
    Retrieves the device ID from the database or creates a new one and saves it.
    """
    session = Session()
    try:
        device = session.query(Device).first()
        if device:
            return device.device_id, "retrieved"
        else:
            new_id = str(random.randint(1000000000000000, 9999999999999999))
            new_device = Device(device_id=new_id)
            session.add(new_device)
            session.commit()
            return new_id, "created"
    except Exception as e:
        session.rollback()
        return None, f"error: {e}"
    finally:
        session.close()

def is_user_allowed(user_id):
    """Checks if the user's device ID is in the allowed list."""
    try:
        with open(ALLOWED_USERS_FILE, 'r') as f:
            allowed_ids = {line.strip() for line in f}
            if user_id in allowed_ids:
                return True
    except FileNotFoundError:
        st.error(f"Error: '{ALLOWED_USERS_FILE}' not found. Please create this file with a list of allowed user IDs.")
        return False
    except Exception as e:
        st.error(f"Error reading '{ALLOWED_USERS_FILE}': {e}")
        return False
    return False

# --- Your Custom Functions ---
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

def analyse_data(data):
    try:
        if data.empty or len(data) < 50:
            return None, 0, 0, "Error: Insufficient data for analysis (less than 50 candles)."
        data = data.tail(50).copy()
        signals = []
        
        # Modified RSI logic: Always Buy or Sell
        data['RSI'] = ta.momentum.RSIIndicator(data['Close']).rsi()
        if data['RSI'].iloc[-1] >= 50: signals.append("Buy")
        else: signals.append("Sell")
        
        # Modified Stochastic logic: Always Buy or Sell
        data['Stoch_K'] = ta.momentum.StochasticOscillator(data['High'], data['Low'], data['Close']).stoch()
        if data['Stoch_K'].iloc[-1] >= 50: signals.append("Buy")
        else: signals.append("Sell")
        
        # ROC logic: Always Buy or Sell
        data['ROC'] = ta.momentum.ROCIndicator(data['Close']).roc()
        if data['ROC'].iloc[-1] >= 0: signals.append("Buy")
        else: signals.append("Sell")
        
        # Modified ADX logic: Always Buy or Sell
        adx_indicator = ta.trend.ADXIndicator(data['High'], data['Low'], data['Close'])
        data['ADX_pos'] = adx_indicator.adx_pos()
        data['ADX_neg'] = adx_indicator.adx_neg()
        if data['ADX_pos'].iloc[-1] >= data['ADX_neg'].iloc[-1]: signals.append("Buy")
        else: signals.append("Sell")
        
        # MACD logic: Always Buy or Sell
        macd_indicator = ta.trend.MACD(data['Close'])
        data['MACD'] = macd_indicator.macd()
        data['MACD_signal'] = macd_indicator.macd_signal()
        if data['MACD'].iloc[-1] >= data['MACD_signal'].iloc[-1]: signals.append("Buy")
        else: signals.append("Sell")
        
        # Ichimoku logic: Always Buy or Sell
        ichimoku_indicator = ta.trend.IchimokuIndicator(data['High'], data['Low'])
        data['ichimoku_a'] = ichimoku_indicator.ichimoku_a()
        data['ichimoku_b'] = ichimoku_indicator.ichimoku_b()
        last_close_ichimoku = data.iloc[-1]['Close']
        cloud_a = data.iloc[-1]['ichimoku_a']
        cloud_b = data.iloc[-1]['ichimoku_b']
        if last_close_ichimoku > max(cloud_a, cloud_b): signals.append("Buy")
        elif last_close_ichimoku < min(cloud_a, cloud_b): signals.append("Sell")
        else: 
            # If inside the cloud, check if above or below the conversion line (Tenkan-sen)
            tenkan_sen = (data['High'].rolling(9).max() + data['Low'].rolling(9).min()) / 2
            if last_close_ichimoku > tenkan_sen.iloc[-1]: signals.append("Buy")
            else: signals.append("Sell")
        
        # EMA logic: Always Buy or Sell
        if len(data) >= 20:
            data['ema10'] = ta.trend.EMAIndicator(data['Close'], window=10).ema_indicator()
            data['ema20'] = ta.trend.EMAIndicator(data['Close'], window=20).ema_indicator()
            if data['ema10'].iloc[-1] >= data['ema20'].iloc[-1]: signals.append("Buy")
            else: signals.append("Sell")
            last_close = data.iloc[-1]['Close']
            if last_close >= data['ema20'].iloc[-1] and last_close >= data['ema10'].iloc[-1]: signals.append("Buy")
            elif last_close < data['ema20'].iloc[-1] and last_close < data['ema10'].iloc[-1]: signals.append("Sell")
            else:
                if last_close > data['ema20'].iloc[-1]: signals.append("Buy")
                else: signals.append("Sell")
                
        # --- Removed candlestick and support/resistance analysis as per user request ---
        
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
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ An error occurred while checking contract status: {e}")
            return None

def get_balance(ws):
    req = {"balance": 1, "subscribe": 1}
    ws.send(json.dumps(req))
    response = json.loads(ws.recv())
    return response.get('balance', {}).get('balance')

# --- Main App Logic and UI ---
st.title("KHOURYBOT - Automated Trading 🤖")

# Check for 'user_id_checked' to avoid rerunning on every reload
if 'user_id_checked' not in st.session_state:
    st.session_state.user_id, status = get_or_create_device_id()
    if st.session_state.user_id is None:
        st.error("Could not get device ID. Please check database connection.")
        st.session_state.user_id_checked = True
    else:
        log_message = f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Device ID retrieved from database." if status == 'retrieved' else f"[{datetime.now().strftime('%H:%M:%S')}] ✨ New device ID created and saved to database."
        st.session_state.log_records.append(log_message)
        st.session_state.user_id_checked = True

if not st.session_state.is_authenticated:
    st.header("Log in to Your Account")
    if st.session_state.user_id and is_user_allowed(st.session_state.user_id):
        st.session_state.is_authenticated = True
        st.success("Your device has been activated! Redirecting to settings...")
        st.balloons()
        st.rerun()
    else:
        st.warning("Your device has not been activated yet. To activate the bot, please send this ID to the bot administrator:")
        st.code(st.session_state.user_id)
        st.info("After activation, simply refresh this page to continue.")

else:
    # --- Display Status and Timer ---
    status_placeholder = st.empty()
    timer_placeholder = st.empty()

    if st.session_state.bot_running:
        if not st.session_state.is_trade_open:
            status_placeholder.info("Analyzing...")
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

    # --- Main Bot Logic (Runs once per minute) ---
    if st.session_state.bot_running and not st.session_state.is_trade_open:
        now = datetime.now()
        seconds_in_minute = now.second
        
        if (now - st.session_state.last_action_time).seconds >= 60 and seconds_in_minute >= 55:
            st.session_state.last_action_time = now
            
            ws = None
            try:
                ws = websocket.WebSocket()
                ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929")
                
                auth_req = {"authorize": st.session_state.user_token}
                ws.send(json.dumps(auth_req))
                auth_response = json.loads(ws.recv())
                
                if auth_response.get('error'):
                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Authentication failed: {auth_response['error']['message']}")
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
                            
                            # Log the indicator counts
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 Buy Signals: {buy_count}, Sell Signals: {sell_count}")
                            
                            # Check last 60 ticks direction
                            last_60_ticks = df_ticks.tail(60)
                            last_60_signal = "Neutral"
                            if last_60_ticks['price'].iloc[-1] > last_60_ticks['price'].iloc[0]:
                                last_60_signal = "Buy"
                            elif last_60_ticks['price'].iloc[-1] < last_60_ticks['price'].iloc[0]:
                                last_60_signal = "Sell"
                                
                            final_signal = "Neutral"
                            # New, balanced logic: Provisional decision must match 60-tick direction
                            if provisional_decision == "Buy" and last_60_signal == "Buy":
                                final_signal = "Buy"
                            elif provisional_decision == "Sell" and last_60_signal == "Sell":
                                final_signal = "Sell"
                            
                            if final_signal is not None and final_signal in ['Buy', 'Sell']:
                                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Final Signal: {final_signal.upper()}")
                                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ➡️ Placing a {final_signal.upper()} order with {st.session_state.current_amount}$")
                                order_response = place_order(ws, st.session_state.user_token, "R_100", final_signal, st.session_state.current_amount)
                                
                                if 'buy' in order_response:
                                    st.session_state.is_trade_open = True
                                    st.session_state.trade_start_time = datetime.now()
                                    st.session_state.contract_id = order_response.get('buy', {}).get('contract_id')
                                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Order placed. Contract ID: {st.session_state.contract_id}")
                                else:
                                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Order failed: {order_response}")
                    
                    else:
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error: Could not get tick history data from API.")
            except Exception as e:
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ An error occurred during the trading cycle: {e}")
            finally:
                if ws:
                    ws.close()
            st.rerun()

    # --- Check Pending Trade Result ---
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
                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Reconnection failed. Authentication error.")
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
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🎉 Win! Profit: {profit}")
                        else:
                            st.session_state.consecutive_losses += 1
                            st.session_state.current_amount *= 2.2
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💔 Loss! Loss: {profit}")
                        st.session_state.is_trade_open = False
                        current_balance = get_balance(ws)
                        if current_balance is not None:
                            if st.session_state.initial_balance is None:
                                st.session_state.initial_balance = current_balance
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💰 New Balance: {current_balance}")
                            if st.session_state.tp_target and current_balance - st.session_state.initial_balance >= st.session_state.tp_target:
                                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🤑 TP hit! Bot stopped.")
                                st.session_state.bot_running = False
                        if st.session_state.consecutive_losses >= st.session_state.max_consecutive_losses:
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 SL hit ({st.session_state.max_consecutive_losses} consecutive losses)! Bot stopped.")
                            st.session_state.bot_running = False
                    else:
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Could not get contract info.")
                        st.session_state.is_trade_open = False
            except Exception as e:
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ An error occurred getting the result: {e}")
            finally:
                if ws:
                    ws.close()
            st.session_state.trade_start_time = None
            st.session_state.contract_id = None
            st.rerun()
    if st.session_state.page == 'inputs':
        st.header("1. Bot Settings")
        st.session_state.user_token = st.text_input("Enter your Deriv API token:", type="password", key="api_token_input")
        st.session_state.base_amount = st.number_input("Base Amount", min_value=0.5, step=0.5, value=st.session_state.base_amount)
        st.session_state.tp_target = st.number_input("Take Profit Target", min_value=1.0, step=1.0, value=st.session_state.tp_target)
        start_button = st.button("Start Bot")
        stop_button = st.button("Stop Bot")
        if start_button:
            if not st.session_state.user_token:
                st.error("Please enter a valid API token before starting the bot.")
            else:
                st.session_state.bot_running = True
                st.session_state.current_amount = st.session_state.base_amount
                st.session_state.consecutive_losses = 0
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🟢 Bot has been started.")
                st.rerun()
        if stop_button:
            st.session_state.bot_running = False
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 Bot stopped by user.")
            st.rerun()
    elif st.session_state.page == 'logs':
        st.header("2. Live Bot Logs")
        with st.container(height=600):
            st.text_area("Logs", "\n".join(st.session_state.log_records), height=600)
            # إضافة سطر برمجي للتمرير التلقائي إلى الأسفل
            components.html(
                """
                <script>
                    var textarea = parent.document.querySelector('textarea[aria-label="Logs"]');
                    if(textarea) {
                        textarea.scrollTop = textarea.scrollHeight;
                    }
                </script>
                """,
                height=0,
                width=0
            )
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Settings"):
            st.session_state.page = 'inputs'
            st.rerun()
    with col2:
        if st.button("Logs"):
            st.session_state.page = 'logs'
            st.rerun()
    time.sleep(1)
    st.rerun()
