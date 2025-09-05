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
if 'total_wins' not in st.session_state:
    st.session_state.total_wins = 0
if 'total_losses' not in st.session_state:
    st.session_state.total_losses = 0

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
        # We need a minimum number of candles for the indicators to work.
        required_candles = 50
        if data.empty or len(data) < required_candles:
            return "Neutral", 0, 0, "Insufficient data"

        data = data.tail(required_candles).copy()
        signals = []
        
        # Helper function to safely get a signal and handle errors
        def get_indicator_signal(indicator_func, default_signal="Neutral"):
            try:
                result = indicator_func()
                if isinstance(result, pd.Series) and not result.empty:
                    return result.iloc[-1]
                elif isinstance(result, tuple) and len(result) > 0 and isinstance(result[0], pd.Series) and not result[0].empty:
                    return result[0].iloc[-1]
                return None
            except Exception as e:
                #st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error in indicator: {indicator_func.__name__} - {e}")
                return None

        # 1. RSI logic
        rsi_value = get_indicator_signal(lambda: ta.momentum.RSIIndicator(data['Close']).rsi())
        if rsi_value is not None:
            signals.append("Buy" if rsi_value >= 50 else "Sell")
        
        # 2. Stochastic logic
        stoch_value = get_indicator_signal(lambda: ta.momentum.StochasticOscillator(data['High'], data['Low'], data['Close']).stoch())
        if stoch_value is not None:
            signals.append("Buy" if stoch_value >= 50 else "Sell")
        
        # 3. ROC logic
        roc_value = get_indicator_signal(lambda: ta.momentum.ROCIndicator(data['Close']).roc())
        if roc_value is not None:
            signals.append("Buy" if roc_value >= 0 else "Sell")
        
        # 4. ADX logic
        adx_indicator = ta.trend.ADXIndicator(data['High'], data['Low'], data['Close'])
        adx_pos_val = get_indicator_signal(lambda: adx_indicator.adx_pos())
        adx_neg_val = get_indicator_signal(lambda: adx_indicator.adx_neg())
        if adx_pos_val is not None and adx_neg_val is not None:
            signals.append("Buy" if adx_pos_val >= adx_neg_val else "Sell")
        
        # 5. MACD logic
        macd_indicator = ta.trend.MACD(data['Close'])
        macd_val = get_indicator_signal(lambda: macd_indicator.macd())
        macd_signal_val = get_indicator_signal(lambda: macd_indicator.macd_signal())
        if macd_val is not None and macd_signal_val is not None:
            signals.append("Buy" if macd_val >= macd_signal_val else "Sell")
        
        # 6. Ichimoku logic
        ichimoku_indicator = ta.trend.IchimokuIndicator(data['High'], data['Low'])
        ichimoku_a_val = get_indicator_signal(lambda: ichimoku_indicator.ichimoku_a())
        ichimoku_b_val = get_indicator_signal(lambda: ichimoku_indicator.ichimoku_b())
        last_close_ichimoku = data.iloc[-1]['Close']
        if ichimoku_a_val is not None and ichimoku_b_val is not None:
            if last_close_ichimoku > max(ichimoku_a_val, ichimoku_b_val):
                signals.append("Buy")
            elif last_close_ichimoku < min(ichimoku_a_val, ichimoku_b_val):
                signals.append("Sell")
            else:
                tenkan_sen = (data['High'].rolling(window=9).max() + data['Low'].rolling(window=9).min()) / 2
                tenkan_sen_val = get_indicator_signal(lambda: tenkan_sen)
                if tenkan_sen_val is not None:
                    signals.append("Buy" if last_close_ichimoku > tenkan_sen_val else "Sell")
        
        # 7. EMA 10/20 Crossover logic
        if len(data) >= 20: # EMA needs at least 20 periods
            ema10 = get_indicator_signal(lambda: ta.trend.EMAIndicator(data['Close'], window=10).ema_indicator())
            ema20 = get_indicator_signal(lambda: ta.trend.EMAIndicator(data['Close'], window=20).ema_indicator())
            if ema10 is not None and ema20 is not None:
                signals.append("Buy" if ema10 >= ema20 else "Sell")
        
        # 8. On-Balance Volume (OBV)
        obv_series = ta.volume.OnBalanceVolumeIndicator(data['Close'], data['Volume']).on_balance_volume()
        if not obv_series.empty and len(obv_series) > 1:
            if obv_series.iloc[-1] > obv_series.iloc[-2]: # Compare last value with previous
                signals.append("Buy")
            elif obv_series.iloc[-1] < obv_series.iloc[-2]:
                signals.append("Sell")
        
        # 9. Commodity Channel Index (CCI)
        cci_value = get_indicator_signal(lambda: ta.trend.CCIIndicator(data['High'], data['Low'], data['Close']).cci())
        if cci_value is not None:
            if cci_value > 0:
                signals.append("Buy")
            elif cci_value < 0:
                signals.append("Sell")
        
        # 10. Awesome Oscillator (AO)
        ao_value = get_indicator_signal(lambda: ta.momentum.AwesomeOscillatorIndicator(data['High'], data['Low']).awesome_oscillator())
        if ao_value is not None:
            if ao_value > 0:
                signals.append("Buy")
            elif ao_value < 0:
                signals.append("Sell")

        buy_count = signals.count("Buy")
        sell_count = signals.count("Sell")
        total_indicators = len(signals)
        
        provisional_decision = "Neutral"
        if total_indicators > 0:
            buy_percentage = (buy_count / total_indicators) * 100
            sell_percentage = (sell_count / total_indicators) * 100
            
            if buy_percentage >= 70:
                provisional_decision = "Buy"
            elif sell_percentage >= 70:
                provisional_decision = "Sell"

        # Removed: log_message = f"[{datetime.now().strftime('%H:%M:%S')}] 📊 Indicators: Buy={buy_count}, Sell={sell_count}, Total={total_indicators}"
        # Removed: st.session_state.log_records.append(log_message)
        
        return provisional_decision, buy_count, sell_count, None
    except Exception as e:
        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error in analyse_data: {e}")
        return None, 0, 0, f"An error occurred during analysis: {e}"

def place_order(ws, proposal_id, amount):
    # Round the amount to 2 decimal places as requested
    valid_amount = round(max(0.5, amount), 2)
    
    req = {
        "buy": proposal_id,
        "price": valid_amount,
    }
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        if response.get('error'):
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Order Error: {response['error']['message']}")
            return {"error": response['error']}
        return response
    except Exception as e:
        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Exception in place_order: {e}")
        return {"error": {"message": str(e)}}

def check_contract_status(ws, contract_id):
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = ws.recv() 
        response_data = json.loads(response)

        if response_data.get('msg_type') == 'proposal_open_contract':
            return response_data['proposal_open_contract']
        else:
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Unexpected response type for contract status: {response_data.get('msg_type')}. Response: {response_data}")
            return None
    except websocket.WebSocketTimeoutException:
        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Timeout waiting for contract info for ID {contract_id}.")
        return None
    except Exception as e:
        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error checking contract status for ID {contract_id}: {e}")
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
            next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
            seconds_left = max(0, (next_minute - now).total_seconds() - 5)
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
        
        if (now - st.session_state.last_action_time).total_seconds() >= 60 and seconds_in_minute >= 55:
            st.session_state.last_action_time = now
            
            ws = None
            try:
                ws = websocket.WebSocket()
                ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10) 
                
                auth_req = {"authorize": st.session_state.user_token}
                ws.send(json.dumps(auth_req))
                auth_response = json.loads(ws.recv()) 
                
                if auth_response.get('error'):
                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Authentication failed: {auth_response['error']['message']}")
                    st.session_state.bot_running = False
                else:
                    if st.session_state.initial_balance is None:
                        current_balance = get_balance(ws)
                        if current_balance is not None:
                            st.session_state.initial_balance = current_balance
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💰 Initial Balance: {st.session_state.initial_balance}")
                        else:
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Failed to retrieve initial balance.")
                    
                    # Request 350 ticks for 50 candles (350/7 = 50)
                    ticks_to_request = 350 
                    req = {"ticks_history": "R_100", "end": "latest", "count": ticks_to_request, "style": "ticks"}
                    ws.send(json.dumps(req))
                    tick_data = json.loads(ws.recv())
                    
                    if 'history' in tick_data and tick_data['history']['prices']:
                        ticks = tick_data['history']['prices']
                        timestamps = tick_data['history']['times']
                        df_ticks = pd.DataFrame({'timestamp': timestamps, 'price': ticks})
                        
                        ticks_per_candle = 7
                        candles_df = ticks_to_ohlc_by_count(df_ticks, ticks_per_candle)
                        
                        provisional_decision, buy_count, sell_count, error_msg = analyse_data(candles_df)
                        
                        if error_msg:
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Analysis Error: {error_msg}")
                        
                        # New check: Last 5 ticks direction
                        last_5_ticks = df_ticks.tail(5)
                        last_5_signal = "Neutral"
                        if len(last_5_ticks) == 5:
                            if last_5_ticks['price'].iloc[-1] > last_5_ticks['price'].iloc[0]:
                                last_5_signal = "Buy"
                            elif last_5_ticks['price'].iloc[-1] < last_5_ticks['price'].iloc[0]:
                                last_5_signal = "Sell"
                        
                        # New check: Last 60 ticks direction
                        last_60_ticks = df_ticks.tail(60)
                        last_60_signal = "Neutral"
                        if len(last_60_ticks) == 60:
                            if last_60_ticks['price'].iloc[-1] > last_60_ticks['price'].iloc[0]:
                                last_60_signal = "Buy"
                            elif last_60_ticks['price'].iloc[-1] < last_60_ticks['price'].iloc[0]:
                                last_60_signal = "Sell"
                                
                        # Final decision based on all three conditions
                        # Adjusted based on user's last request: reverse the 60-tick direction
                        final_signal = "Neutral"
                        if provisional_decision == "Buy" and last_5_signal == "Buy" and last_60_signal == "Sell":
                            final_signal = "Buy" # Now it's a BUY trade when last_60_signal is SELL
                        elif provisional_decision == "Sell" and last_5_signal == "Sell" and last_60_signal == "Buy":
                            final_signal = "Sell" # Now it's a SELL trade when last_60_signal is BUY

                        if final_signal in ['Buy', 'Sell']:
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ➡️ Entering a {final_signal.upper()} trade with {round(st.session_state.current_amount, 2)}$")
                            
                            # First, request a proposal to get the proposal ID.
                            proposal_req = {
                                "proposal": 1,
                                "amount": round(st.session_state.current_amount, 2),
                                "basis": "stake",
                                "contract_type": "CALL" if final_signal == 'Buy' else "PUT",
                                "currency": "USD",
                                "duration": 1,
                                "duration_unit": "m",
                                "symbol": "R_100",
                                "passthrough": {"action": final_signal}
                            }
                            ws.send(json.dumps(proposal_req))
                            proposal_response = json.loads(ws.recv())
                            
                            if 'proposal' in proposal_response:
                                proposal_id = proposal_response['proposal']['id']
                                
                                # Now, place the order using the proposal ID.
                                order_response = place_order(ws, proposal_id, st.session_state.current_amount)
                                
                                if 'buy' in order_response and 'contract_id' in order_response['buy']:
                                    st.session_state.is_trade_open = True
                                    st.session_state.trade_start_time = datetime.now()
                                    st.session_state.contract_id = order_response['buy']['contract_id']
                                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Order placed.")
                                elif 'error' in order_response:
                                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Order failed: {order_response['error']['message']}")
                                else:
                                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Unexpected order response: {order_response}")
                            else:
                                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Proposal failed: {proposal_response.get('error', {}).get('message', 'Unknown error')}")
                    
                    else:
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error: Could not get tick history data or data is empty.")
            except websocket.WebSocketConnectionClosedException:
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ WebSocket connection closed unexpectedly.")
                st.session_state.bot_running = False
            except websocket.WebSocketTimeoutException:
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ WebSocket connection timed out.")
                st.session_state.bot_running = False
            except Exception as e:
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ An error occurred during the trading cycle: {e}")
            finally:
                if ws and ws.connected:
                    ws.close()
            st.rerun()

    # --- Check Pending Trade Result ---
    if st.session_state.is_trade_open and st.session_state.trade_start_time:
        if datetime.now() >= st.session_state.trade_start_time + timedelta(seconds=70): 
            ws = None
            try:
                ws = websocket.WebSocket()
                ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                auth_req = {"authorize": st.session_state.user_token}
                ws.send(json.dumps(auth_req))
                auth_response = json.loads(ws.recv())
                
                if auth_response.get('error'):
                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Reconnection failed for result check. Authentication error.")
                    st.session_state.bot_running = False
                    st.session_state.is_trade_open = False
                else:
                    contract_info = check_contract_status(ws, st.session_state.contract_id)
                    if contract_info and contract_info.get('is_sold'):
                        profit = contract_info.get('profit', 0)
                        
                        if profit > 0: # This is a win
                            st.session_state.consecutive_losses = 0
                            st.session_state.total_wins += 1
                            st.session_state.current_amount = st.session_state.base_amount
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🎉 Win! Profit: {profit:.2f}$")
                        elif profit < 0: # This is a loss
                            st.session_state.consecutive_losses += 1
                            st.session_state.total_losses += 1
                            next_bet = st.session_state.current_amount * 2.2
                            st.session_state.current_amount = max(st.session_state.base_amount, next_bet)
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💔 Loss! Loss: {profit:.2f}$")
                        else: # Profit is 0, so no change in amount or consecutive losses
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚪ No change. Profit/Loss: 0$")
                            
                        st.session_state.is_trade_open = False
                        
                        current_balance = get_balance(ws)
                        if current_balance is not None:
                            # Find and remove the last balance log message
                            balance_logs = [l for l in st.session_state.log_records if '💰' in l]
                            if balance_logs:
                                last_balance_log = balance_logs[-1]
                                st.session_state.log_records = [l for l in st.session_state.log_records if l != last_balance_log]
                                
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💰 Current Balance: {current_balance:.2f}")
                            
                            if st.session_state.tp_target and (current_balance - st.session_state.initial_balance) >= st.session_state.tp_target:
                                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🤑 Take Profit target ({st.session_state.tp_target}$) reached! Bot stopped.")
                                st.session_state.bot_running = False
                        else:
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠ Could not retrieve balance after trade.")
                            
                        if st.session_state.consecutive_losses >= st.session_state.max_consecutive_losses:
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 Stop Loss hit ({st.session_state.consecutive_losses} consecutive losses)! Bot stopped.")
                            st.session_state.bot_running = False
                    elif contract_info and not contract_info.get('is_sold'):
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠ Contract {st.session_state.contract_id} is not yet sold/closed.")
                    else:
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠ Could not get contract info for ID: {st.session_state.contract_id}. Contract might have been cancelled or failed.")
                        st.session_state.is_trade_open = False
            except websocket.WebSocketConnectionClosedException:
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ WebSocket connection closed unexpectedly during result check.")
                st.session_state.bot_running = False
                st.session_state.is_trade_open = False
            except websocket.WebSocketTimeoutException:
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ WebSocket connection timed out during result check.")
                st.session_state.bot_running = False
                st.session_state.is_trade_open = False
            except Exception as e:
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ An error occurred getting the trade result: {e}")
            finally:
                if ws and ws.connected:
                    ws.close()
            st.session_state.trade_start_time = None
            st.session_state.contract_id = None
            st.rerun()

    # --- UI Navigation and Controls ---
    if st.session_state.page == 'inputs':
        st.header("1. Bot Settings")
        st.session_state.user_token = st.text_input("Enter your Deriv API token:", type="password", key="api_token_input")
        st.session_state.base_amount = st.number_input("Base Amount ($)", min_value=0.5, step=0.5, value=st.session_state.base_amount)
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
                st.session_state.current_amount = st.session_state.base_amount
                st.session_state.consecutive_losses = 0
                st.session_state.total_wins = 0
                st.session_state.total_losses = 0
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🟢 Bot has been started.")
                st.rerun()
                
        if stop_button:
            st.session_state.bot_running = False
            st.session_state.is_trade_open = False
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 Bot stopped by user.")
            st.rerun()
            
    elif st.session_state.page == 'logs':
        st.header("2. Live Bot Logs")
        st.markdown(f"*Wins: {st.session_state.total_wins}* | *Losses: {st.session_state.total_losses}*")
        with st.container(height=600):
            st.text_area("Logs", "\n".join(st.session_state.log_records), height=600, key="logs_textarea")
            # JavaScript to auto-scroll the textarea to the bottom
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
