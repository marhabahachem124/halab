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
# This file contains device IDs for authorized users.
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
            # Log an error if insufficient data, but don't return 0 signals yet
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Warning: Insufficient data for full analysis (less than 50 candles).")
            # Attempt to proceed with available data if possible, or return neutral with counts
            return "Neutral", 0, 0, "Insufficient data" # Return Neutral and 0 counts in case of insufficient data

        data = data.tail(50).copy()
        signals = []
        
        # RSI logic: Ensure always Buy or Sell
        rsi_indicator = ta.momentum.RSIIndicator(data['Close'])
        rsi_value = rsi_indicator.rsi()
        if not rsi_value.empty:
            if rsi_value.iloc[-1] >= 50: signals.append("Buy")
            else: signals.append("Sell")
        else:
            signals.append("Neutral") # Append neutral if calculation fails

        # Stochastic logic: Ensure always Buy or Sell
        stoch_k = ta.momentum.StochasticOscillator(data['High'], data['Low'], data['Close'])
        stoch_value = stoch_k.stoch()
        if not stoch_value.empty:
            if stoch_value.iloc[-1] >= 50: signals.append("Buy")
            else: signals.append("Sell")
        else:
            signals.append("Neutral")

        # ROC logic: Ensure always Buy or Sell
        roc_indicator = ta.momentum.ROCIndicator(data['Close'])
        roc_value = roc_indicator.roc()
        if not roc_value.empty:
            if roc_value.iloc[-1] >= 0: signals.append("Buy")
            else: signals.append("Sell")
        else:
            signals.append("Neutral")
        
        # ADX logic: Ensure always Buy or Sell
        adx_indicator = ta.trend.ADXIndicator(data['High'], data['Low'], data['Close'])
        adx_pos = adx_indicator.adx_pos()
        adx_neg = adx_indicator.adx_neg()
        if not adx_pos.empty and not adx_neg.empty:
            if adx_pos.iloc[-1] >= adx_neg.iloc[-1]: signals.append("Buy")
            else: signals.append("Sell")
        else:
            signals.append("Neutral")
        
        # MACD logic: Ensure always Buy or Sell
        macd_indicator = ta.trend.MACD(data['Close'])
        macd_val = macd_indicator.macd()
        macd_signal_val = macd_indicator.macd_signal()
        if not macd_val.empty and not macd_signal_val.empty:
            if macd_val.iloc[-1] >= macd_signal_val.iloc[-1]: signals.append("Buy")
            else: signals.append("Sell")
        else:
            signals.append("Neutral")
        
        # Ichimoku logic: Ensure always Buy or Sell
        ichimoku_indicator = ta.trend.IchimokuIndicator(data['High'], data['Low'])
        tenkan_sen = (data['High'].rolling(9).max() + data['Low'].rolling(9).min()) / 2 # Pre-calculate for case inside cloud
        ichimoku_a_val = ichimoku_indicator.ichimoku_a()
        ichimoku_b_val = ichimoku_indicator.ichimoku_b()

        if not ichimoku_a_val.empty and not ichimoku_b_val.empty and not tenkan_sen.empty:
            last_close_ichimoku = data.iloc[-1]['Close']
            cloud_a = ichimoku_a_val.iloc[-1]
            cloud_b = ichimoku_b_val.iloc[-1]
            
            if last_close_ichimoku > max(cloud_a, cloud_b): signals.append("Buy")
            elif last_close_ichimoku < min(cloud_a, cloud_b): signals.append("Sell")
            else:
                # If inside the cloud, check if above or below the conversion line (Tenkan-sen)
                if last_close_ichimoku > tenkan_sen.iloc[-1]: signals.append("Buy")
                else: signals.append("Sell")
        else:
            signals.append("Neutral")
        
        # EMA logic: Ensure always Buy or Sell
        if len(data) >= 20:
            ema10_indicator = ta.trend.EMAIndicator(data['Close'], window=10)
            ema20_indicator = ta.trend.EMAIndicator(data['Close'], window=20)
            ema10 = ema10_indicator.ema_indicator()
            ema20 = ema20_indicator.ema_indicator()
            
            if not ema10.empty and not ema20.empty:
                if ema10.iloc[-1] >= ema20.iloc[-1]: signals.append("Buy")
                else: signals.append("Sell")
                
                last_close = data.iloc[-1]['Close']
                if last_close >= ema20.iloc[-1] and last_close >= ema10.iloc[-1]: signals.append("Buy")
                elif last_close < ema20.iloc[-1] and last_close < ema10.iloc[-1]: signals.append("Sell")
                else:
                    if last_close > ema20.iloc[-1]: signals.append("Buy")
                    else: signals.append("Sell")
            else:
                 signals.append("Neutral") # Append neutral if EMA calculation fails
        else:
            signals.append("Neutral") # Append neutral if not enough data for EMA

        # --- Removed candlestick and support/resistance analysis as per user request ---
        
        buy_count = signals.count("Buy")
        sell_count = signals.count("Sell")
        neutral_count = signals.count("Neutral") # Count neutral signals
        
        final_decision = "Neutral"
        if buy_count > sell_count:
            final_decision = "Buy"
        elif sell_count > buy_count:
            final_decision = "Sell"
        # If buy_count == sell_count, final_decision remains "Neutral" as requested.
        
        # Log the indicator counts
        log_message = f"[{datetime.now().strftime('%H:%M:%S')}] 📊 Indicators: Buy={buy_count}, Sell={sell_count}, Neutral={neutral_count}"
        st.session_state.log_records.append(log_message)
        
        # If no clear decision (buy_count == sell_count), we still return Neutral
        # The main loop logic will then check if final_decision is Buy or Sell.
        # If it's Neutral, no trade will be placed, which is the intended behavior as per user's instruction to keep it as is.

        return final_decision, buy_count, sell_count, None
    except Exception as e:
        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error in analyse_data: {e}")
        return None, 0, 0, f"An error occurred during analysis: {e}"

def place_order(ws, api_token, symbol, action, amount):
    # Ensure amount is within valid limits for Deriv (e.g., minimum $0.50)
    valid_amount = max(0.5, amount) 
    
    req = {
        "buy": 1,
        "price": valid_amount,
        "type": "CALL" if action == 'buy' else "PUT",
        "duration": 1,
        "duration_unit": "m",
        "symbol": symbol
    }
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        if response.get('error'):
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Order Error: {response['error']['message']}")
            return {"error": response['error']} # Return error structure
        return response
    except Exception as e:
        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Exception in place_order: {e}")
        return {"error": {"message": str(e)}}

def check_contract_status(ws, contract_id):
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        # Set a timeout for receiving the response
        response = ws.recv()
        response_data = json.loads(response)

        if response_data.get('msg_type') == 'proposal_open_contract':
            return response_data['proposal_open_contract']
        else:
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Unexpected response type for contract status: {response_data.get('msg_type')}")
            return None
    except websocket.WebSocketTimeoutException:
        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Timeout waiting for contract info.")
        return None
    except Exception as e:
        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error checking contract status: {e}")
        return None

def get_balance(ws):
    req = {"balance": 1, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        if 'balance' in response:
            return response['balance']['balance']
        elif 'error' in response:
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error getting balance: {response['error']['message']}")
            return None
        return None
    except Exception as e:
        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Exception in get_balance: {e}")
        return None

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
            # Calculate time until the next minute starts, minus a small buffer (5 seconds)
            next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
            seconds_left = max(0, (next_minute - now).total_seconds() - 5) # Use total_seconds for more precision
            timer_placeholder.metric("Next action in", f"{int(seconds_left)}s")
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
        
        # Execute trade logic only when the minute is almost over (e.g., last 5 seconds)
        if (now - st.session_state.last_action_time).total_seconds() >= 60 and seconds_in_minute >= 55:
            st.session_state.last_action_time = now # Update last action time
            
            ws = None
            try:
                ws = websocket.WebSocket()
                ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929")
                
                auth_req = {"authorize": st.session_state.user_token}
                ws.send(json.dumps(auth_req))
                auth_response = json.loads(ws.recv())
                
                if auth_response.get('error'):
                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Authentication failed: {auth_response['error']['message']}")
                    st.session_state.bot_running = False # Stop bot if auth fails
                else:
                    if st.session_state.initial_balance is None:
                        current_balance = get_balance(ws)
                        if current_balance is not None:
                            st.session_state.initial_balance = current_balance
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💰 Initial Balance: {st.session_state.initial_balance}")
                        else:
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Failed to retrieve initial balance.")
                            
                    # Fetch tick history for analysis
                    req = {"ticks_history": "R_100", "end": "latest", "count": 70, "style": "ticks"}
                    ws.send(json.dumps(req))
                    tick_data = json.loads(ws.recv())
                    
                    if 'history' in tick_data and tick_data['history']['prices']:
                        ticks = tick_data['history']['prices']
                        timestamps = tick_data['history']['times']
                        df_ticks = pd.DataFrame({'timestamp': timestamps, 'price': ticks})
                        
                        if len(df_ticks) >= 70: # Ensure enough ticks for OHLC conversion
                            candles_5ticks = ticks_to_ohlc_by_count(df_ticks.tail(70), 5)
                            
                            # Call analyse_data, which now logs counts and ensures non-zero signals if possible
                            provisional_decision, buy_count, sell_count, error_msg = analyse_data(candles_5ticks)
                            
                            if error_msg: # Log analysis errors
                                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Analysis Error: {error_msg}")
                            
                            # Check last 60 ticks direction as a confirmation
                            last_60_ticks = df_ticks.tail(60)
                            last_60_signal = "Neutral"
                            if len(last_60_ticks) >= 2: # Ensure there are at least two points to compare
                                if last_60_ticks['price'].iloc[-1] > last_60_ticks['price'].iloc[0]:
                                    last_60_signal = "Buy"
                                elif last_60_ticks['price'].iloc[-1] < last_60_ticks['price'].iloc[0]:
                                    last_60_signal = "Sell"
                                
                            final_signal = "Neutral"
                            # The logic here is: provisional_decision must match last_60_signal for a trade
                            if provisional_decision == "Buy" and last_60_signal == "Buy":
                                final_signal = "Buy"
                            elif provisional_decision == "Sell" and last_60_signal == "Sell":
                                final_signal = "Sell"
                            
                            if final_signal in ['Buy', 'Sell']:
                                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Final Signal: {final_signal.upper()}")
                                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ➡️ Placing a {final_signal.upper()} order with {st.session_state.current_amount}$")
                                order_response = place_order(ws, st.session_state.user_token, "R_100", final_signal, st.session_state.current_amount)
                                
                                if 'buy' in order_response and 'contract_id' in order_response['buy']:
                                    st.session_state.is_trade_open = True
                                    st.session_state.trade_start_time = datetime.now()
                                    st.session_state.contract_id = order_response['buy']['contract_id']
                                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Order placed. Contract ID: {st.session_state.contract_id}")
                                elif 'error' in order_response:
                                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Order failed: {order_response['error']['message']}")
                                else:
                                     st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Unexpected order response: {order_response}")
                            else:
                                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ No strong signal found for trade. Signal: {final_signal}")
                    
                    else:
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error: Could not get tick history data or data is empty.")
            except Exception as e:
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ An error occurred during the trading cycle: {e}")
            finally:
                if ws:
                    ws.close() # Ensure WebSocket connection is closed
            st.rerun() # Rerun the app to update UI based on new state

    # --- Check Pending Trade Result ---
    if st.session_state.is_trade_open and st.session_state.trade_start_time:
        # Check if at least 70 seconds have passed since the trade was opened (to ensure contract closure)
        if datetime.now() >= st.session_state.trade_start_time + timedelta(seconds=70):
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⏱️ Checking trade result for contract ID: {st.session_state.contract_id}...")
            ws = None
            try:
                ws = websocket.WebSocket()
                ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929")
                auth_req = {"authorize": st.session_state.user_token}
                ws.send(json.dumps(auth_req))
                auth_response = json.loads(ws.recv())
                
                if auth_response.get('error'):
                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Reconnection failed for result check. Authentication error.")
                    st.session_state.bot_running = False # Stop bot if auth fails
                    st.session_state.is_trade_open = False
                else:
                    contract_info = check_contract_status(ws, st.session_state.contract_id)
                    if contract_info and contract_info.get('is_sold'): # Ensure contract is sold and info is available
                        profit = contract_info.get('profit', 0)
                        is_win = profit > 0
                        
                        if is_win:
                            st.session_state.consecutive_losses = 0
                            st.session_state.current_amount = st.session_state.base_amount
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🎉 Win! Profit: {profit}")
                        else:
                            st.session_state.consecutive_losses += 1
                            # Martingale logic: double bet after a loss, but ensure it doesn't exceed a reasonable limit or go below base amount if issues arise.
                            # Using 2.2 multiplier as per previous logic.
                            next_bet = st.session_state.current_amount * 2.2
                            st.session_state.current_amount = max(st.session_state.base_amount, next_bet) # Ensure not to go below base amount
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💔 Loss! Loss: {profit}")
                        
                        st.session_state.is_trade_open = False # Trade is no longer open
                        
                        current_balance = get_balance(ws)
                        if current_balance is not None:
                            # If initial balance wasn't set (e.g., bot restarted), set it now.
                            if st.session_state.initial_balance is None:
                                st.session_state.initial_balance = current_balance
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💰 New Balance: {current_balance}")
                            
                            # Check Take Profit
                            if st.session_state.tp_target and (current_balance - st.session_state.initial_balance) >= st.session_state.tp_target:
                                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🤑 Take Profit target ({st.session_state.tp_target}$) reached! Bot stopped.")
                                st.session_state.bot_running = False # Stop bot
                        else:
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Could not retrieve balance after trade.")
                            
                        # Check Stop Loss (consecutive losses)
                        if st.session_state.consecutive_losses >= st.session_state.max_consecutive_losses:
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 Stop Loss hit ({st.session_state.consecutive_losses} consecutive losses)! Bot stopped.")
                            st.session_state.bot_running = False # Stop bot
                    elif contract_info and not contract_info.get('is_sold'):
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Contract {st.session_state.contract_id} is not yet sold/closed.")
                        # Keep trade_open as True to re-check later, or implement a re-check timeout
                    else:
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Could not get contract info for ID: {st.session_state.contract_id}. Contract might have been cancelled or failed.")
                        st.session_state.is_trade_open = False # Mark as not open if info is missing
            except Exception as e:
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ An error occurred getting the trade result: {e}")
            finally:
                if ws:
                    ws.close() # Ensure WebSocket connection is closed
            # Reset trade state variables after checking result
            st.session_state.trade_start_time = None
            st.session_state.contract_id = None
            st.rerun() # Rerun to update UI

    # --- UI Navigation and Controls ---
    if st.session_state.page == 'inputs':
        st.header("1. Bot Settings")
        st.session_state.user_token = st.text_input("Enter your Deriv API token:", type="password", key="api_token_input")
        st.session_state.base_amount = st.number_input("Base Amount", min_value=0.5, step=0.5, value=st.session_state.base_amount)
        st.session_state.tp_target = st.number_input("Take Profit Target ($)", min_value=1.0, step=1.0, value=st.session_state.tp_target)
        st.session_state.max_consecutive_losses = st.number_input("Max Consecutive Losses", min_value=1, step=1, value=st.session_state.max_consecutive_losses)
        
        col1, col2 = st.columns(2)
        with col1:
            start_button = st.button("Start Bot", type="primary")
        with col2:
            stop_button = st.button("Stop Bot")
            
        if start_button:
            if not st.session_state.user_token:
                st.error("Please enter a valid API token before starting the bot.")
            else:
                st.session_state.bot_running = True
                st.session_state.current_amount = st.session_state.base_amount # Reset amount to base
                st.session_state.consecutive_losses = 0 # Reset losses
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🟢 Bot has been started.")
                st.rerun()
                
        if stop_button:
            st.session_state.bot_running = False
            st.session_state.is_trade_open = False # Ensure no pending trade is considered running
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 Bot stopped by user.")
            st.rerun()
            
    elif st.session_state.page == 'logs':
        st.header("2. Live Bot Logs")
        with st.container(height=600):
            st.text_area("Logs", "\n".join(st.session_state.log_records), height=600, key="logs_textarea")
            # Auto-scroll to bottom
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
            
    # Rerun every second to keep UI updated and check timers
    time.sleep(1)
    st.rerun()
