import streamlit as st
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
import os
import uuid
import streamlit.components.v1 as components
import websocket
import json
import pandas as pd
import ta
import time
import numpy as np
import collections
import threading

# --- Database Setup (WARNING: HARDCODED URL) ---
DATABASE_URL = "postgresql://khourybot_db_user:wlVAwKwLhfzzH9HFsRMNo3IOo4dX6DYm@dpg-d2smi46r433s73frbbcg-a/khourybot_db"
engine = sa.create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base()

class BotState(Base):
    __tablename__ = 'bot_state'
    id = sa.Column(sa.Integer, primary_key=True)
    device_id = sa.Column(sa.String, unique=True, nullable=False)
    is_running = sa.Column(sa.Boolean, default=False)
    user_token = sa.Column(sa.String, nullable=True)
    current_amount = sa.Column(sa.Float, default=0.5)
    base_amount = sa.Column(sa.Float, default=0.5)
    consecutive_losses = sa.Column(sa.Integer, default=0)
    is_trade_open = sa.Column(sa.Boolean, default=False)
    trade_start_time = sa.Column(sa.DateTime, nullable=True)
    contract_id = sa.Column(sa.String, nullable=True)
    last_action_time = sa.Column(sa.DateTime, nullable=True)
    total_wins = sa.Column(sa.Integer, default=0)
    total_losses = sa.Column(sa.Integer, default=0)
    initial_balance = sa.Column(sa.Float, nullable=True)
    tp_target = sa.Column(sa.Float, nullable=True)
    max_consecutive_losses = sa.Column(sa.Integer, default=5)

class BotLog(Base):
    __tablename__ = 'bot_logs'
    id = sa.Column(sa.Integer, primary_key=True)
    device_id = sa.Column(sa.String, nullable=False)
    timestamp = sa.Column(sa.DateTime, default=datetime.utcnow)
    message = sa.Column(sa.String, nullable=False)

class Device(Base):
    __tablename__ = 'devices'
    id = sa.Column(sa.Integer, primary_key=True)
    device_id = sa.Column(sa.String, unique=True, nullable=False)

Base.metadata.create_all(engine)

# --- Bot Core Logic (The engine) ---
def log_message(device_id, message):
    session = Session()
    try:
        new_log = BotLog(device_id=device_id, message=message)
        session.add(new_log)
        session.commit()
    except Exception as e:
        print(f"Error logging to DB for {device_id}: {e}")
        session.rollback()
    finally:
        session.close()

def get_bot_state(device_id):
    session = Session()
    try:
        state = session.query(BotState).filter_by(device_id=device_id).first()
        if not state:
            state = BotState(device_id=device_id)
            session.add(state)
            session.commit()
        return state
    finally:
        session.close()

def update_bot_state(device_id, **kwargs):
    session = Session()
    try:
        state = session.query(BotState).filter_by(device_id=device_id).first()
        if state:
            for key, value in kwargs.items():
                setattr(state, key, value)
            session.commit()
    finally:
        session.close()

def ticks_to_ohlc_by_count(ticks_df, tick_count):
    if ticks_df.empty: return pd.DataFrame()
    ohlc_data = []
    prices = ticks_df['price'].values
    timestamps = ticks_df['timestamp'].values
    for i in range(0, len(prices), tick_count):
        chunk = prices[i:i + tick_count]
        if len(chunk) == tick_count:
            open_price = chunk[0]; high_price = np.max(chunk); low_price = np.min(chunk); close_price = chunk[-1]
            ohlc_data.append({'timestamp': timestamps[i+tick_count-1], 'Open': open_price, 'High': high_price, 'Low': low_price, 'Close': close_price, 'Volume': tick_count})
    ohlc_df = pd.DataFrame(ohlc_data)
    if not ohlc_df.empty:
        ohlc_df['timestamp'] = pd.to_datetime(ohlc_df['timestamp'], unit='s')
        ohlc_df.set_index('timestamp', inplace=True)
    return ohlc_df

def analyse_data(data, device_id):
    try:
        required_candles = 50;
        if data.empty or len(data) < required_candles: return "Neutral", 0, 0, "Insufficient data"
        data = data.tail(required_candles).copy(); signals = []
        def get_indicator_signal(indicator_func, default_signal="Neutral"):
            try:
                result = indicator_func()
                if isinstance(result, pd.Series) and not result.empty: return result.iloc[-1]
                elif isinstance(result, tuple) and len(result) > 0 and isinstance(result[0], pd.Series) and not result[0].empty: return result[0].iloc[-1]
                return None
            except Exception as e: return None
        rsi_value = get_indicator_signal(lambda: ta.momentum.RSIIndicator(data['Close']).rsi())
        if rsi_value is not None: signals.append("Buy" if rsi_value >= 50 else "Sell")
        stoch_value = get_indicator_signal(lambda: ta.momentum.StochasticOscillator(data['High'], data['Low'], data['Close']).stoch())
        if stoch_value is not None: signals.append("Buy" if stoch_value >= 50 else "Sell")
        roc_value = get_indicator_signal(lambda: ta.momentum.ROCIndicator(data['Close']).roc())
        if roc_value is not None: signals.append("Buy" if roc_value >= 0 else "Sell")
        adx_indicator = ta.trend.ADXIndicator(data['High'], data['Low'], data['Close']); adx_pos_val = get_indicator_signal(lambda: adx_indicator.adx_pos()); adx_neg_val = get_indicator_signal(lambda: adx_indicator.adx_neg())
        if adx_pos_val is not None and adx_neg_val is not None: signals.append("Buy" if adx_pos_val >= adx_neg_val else "Sell")
        macd_indicator = ta.trend.MACD(data['Close']); macd_val = get_indicator_signal(lambda: macd_indicator.macd()); macd_signal_val = get_indicator_signal(lambda: macd_indicator.macd_signal())
        if macd_val is not None and macd_signal_val is not None: signals.append("Buy" if macd_val >= macd_signal_val else "Sell")
        ichimoku_indicator = ta.trend.IchimokuIndicator(data['High'], data['Low']); ichimoku_a_val = get_indicator_signal(lambda: ichimoku_indicator.ichimoku_a()); ichimoku_b_val = get_indicator_signal(lambda: ichimoku_indicator.ichimoku_b()); last_close_ichimoku = data.iloc[-1]['Close']
        if ichimoku_a_val is not None and ichimoku_b_val is not None:
            if last_close_ichimoku > max(ichimoku_a_val, ichimoku_b_val): signals.append("Buy")
            elif last_close_ichimoku < min(ichimoku_a_val, ichimoku_b_val): signals.append("Sell")
            else:
                tenkan_sen = (data['High'].rolling(window=9).max() + data['Low'].rolling(window=9).min()) / 2
                tenkan_sen_val = get_indicator_signal(lambda: tenkan_sen)
                if tenkan_sen_val is not None: signals.append("Buy" if last_close_ichimoku > tenkan_sen_val else "Sell")
        if len(data) >= 20:
            ema10 = get_indicator_signal(lambda: ta.trend.EMAIndicator(data['Close'], window=10).ema_indicator())
            ema20 = get_indicator_signal(lambda: ta.trend.EMAIndicator(data['Close'], window=20).ema_indicator())
            if ema10 is not None and ema20 is not None: signals.append("Buy" if ema10 >= ema20 else "Sell")
        obv_series = ta.volume.OnBalanceVolumeIndicator(data['Close'], data['Volume']).on_balance_volume()
        if not obv_series.empty and len(obv_series) > 1:
            if obv_series.iloc[-1] > obv_series.iloc[-2]: signals.append("Buy")
            elif obv_series.iloc[-1] < obv_series.iloc[-2]: signals.append("Sell")
        cci_value = get_indicator_signal(lambda: ta.trend.CCIIndicator(data['High'], data['Low'], data['Close']).cci())
        if cci_value is not None:
            if cci_value > 0: signals.append("Buy")
            elif cci_value < 0: signals.append("Sell")
        ao_value = get_indicator_signal(lambda: ta.momentum.AwesomeOscillatorIndicator(data['High'], data['Low']).awesome_oscillator())
        if ao_value is not None:
            if ao_value > 0: signals.append("Buy")
            elif ao_value < 0: signals.append("Sell")
        buy_count = signals.count("Buy"); sell_count = signals.count("Sell"); total_indicators = len(signals); provisional_decision = "Neutral"
        if total_indicators > 0:
            buy_percentage = (buy_count / total_indicators) * 100; sell_percentage = (sell_count / total_indicators) * 100
            if buy_percentage >= 70: provisional_decision = "Buy"
            elif sell_percentage >= 70: provisional_decision = "Sell"
        return provisional_decision, buy_count, sell_count, None
    except Exception as e: log_message(device_id, f"❌ Error in analyse_data: {e}"); return None, 0, 0, f"An error occurred during analysis: {e}"

def place_order(ws, proposal_id, amount, device_id):
    valid_amount = round(max(0.5, amount), 2); req = {"buy": proposal_id, "price": valid_amount}
    try:
        ws.send(json.dumps(req)); response = json.loads(ws.recv())
        if response.get('error'):
            log_message(device_id, f"❌ Order Error: {response['error']['message']}")
            return {"error": response['error']}
        return response
    except Exception as e:
        log_message(device_id, f"❌ Exception in place_order: {e}")
        return {"error": {"message": str(e)}}

def check_contract_status(ws, contract_id, device_id):
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    try:
        ws.send(json.dumps(req)); response = ws.recv(); response_data = json.loads(response)
        if response_data.get('msg_type') == 'proposal_open_contract': return response_data['proposal_open_contract']
        else:
            log_message(device_id, f"⚠️ Unexpected response type for contract status: {response_data.get('msg_type')}.")
            return None
    except websocket.WebSocketTimeoutException:
        log_message(device_id, f"⚠️ Timeout waiting for contract info for ID {contract_id}.")
        return None
    except Exception as e:
        log_message(device_id, f"❌ Error checking contract status for ID {contract_id}: {e}")
        return None

def get_balance(ws, device_id):
    req = {"balance": 1, "subscribe": 1}
    try:
        ws.send(json.dumps(req)); response = json.loads(ws.recv())
        if 'balance' in response: return response['balance']['balance']
        elif 'error' in response: log_message(device_id, f"❌ Error getting balance: {response['error']['message']}"); return None
        return None
    except Exception as e: log_message(device_id, f"❌ Exception in get_balance: {e}"); return None

# --- Main Bot Loop for a SINGLE User ---
def run_bot_for_user(device_id):
    log_message(device_id, "🟢 Bot logic thread has started for this user.")
    while True:
        state = get_bot_state(device_id)
        if not state or not state.is_running:
            time.sleep(5)
            continue
        
        if state.is_trade_open:
            if state.trade_start_time and (datetime.now() - state.trade_start_time).total_seconds() >= 70:
                ws = None
                try:
                    ws = websocket.WebSocket(); ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10); auth_req = {"authorize": state.user_token}; ws.send(json.dumps(auth_req)); auth_response = json.loads(ws.recv())
                    if auth_response.get('error'):
                        log_message(device_id, "❌ Auth failed during result check."); update_bot_state(device_id, is_running=False, is_trade_open=False); continue
                    contract_info = check_contract_status(ws, state.contract_id, device_id)
                    if contract_info and contract_info.get('is_sold'):
                        profit = contract_info.get('profit', 0); wins = state.total_wins; losses = state.total_losses; consecutive = state.consecutive_losses; current_amount = state.current_amount
                        if profit > 0: consecutive = 0; wins += 1; current_amount = state.base_amount
                        elif profit < 0: consecutive += 1; losses += 1; next_bet = state.current_amount * 2.2; current_amount = max(state.base_amount, next_bet)
                        update_bot_state(device_id, is_trade_open=False, trade_start_time=None, contract_id=None, consecutive_losses=consecutive, total_wins=wins, total_losses=losses, current_amount=current_amount)
                        current_balance = get_balance(ws, device_id)
                        if current_balance is not None:
                            log_message(device_id, f"💰 Current Balance: {current_balance:.2f}")
                            if state.tp_target and state.initial_balance and (current_balance - state.initial_balance) >= state.tp_target:
                                log_message(device_id, f"🤑 Take Profit target ({state.tp_target}$) reached! Bot stopped."); update_bot_state(device_id, is_running=False)
                        if consecutive >= state.max_consecutive_losses:
                            log_message(device_id, f"🛑 Stop Loss hit ({consecutive} consecutive losses)! Bot stopped."); update_bot_state(device_id, is_running=False)
                    else:
                        log_message(device_id, f"⚠ Could not get contract info for ID: {state.contract_id}."); update_bot_state(device_id, is_trade_open=False, trade_start_time=None, contract_id=None)
                except Exception as e: log_message(device_id, f"❌ An error occurred getting the trade result: {e}"); update_bot_state(device_id, is_trade_open=False)
                finally:
                    if ws and ws.connected: ws.close()
            time.sleep(1); continue
        
        now = datetime.now()
        if state.last_action_time and (now - state.last_action_time).total_seconds() < 60:
            time.sleep(1); continue
        update_bot_state(device_id, last_action_time=now)
        
        ws = None
        try:
            ws = websocket.WebSocket(); ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10); auth_req = {"authorize": state.user_token}; ws.send(json.dumps(auth_req)); auth_response = json.loads(ws.recv())
            if auth_response.get('error'): log_message(device_id, f"❌ Auth failed: {auth_response['error']['message']}"); update_bot_state(device_id, is_running=False); continue
            if state.initial_balance is None:
                current_balance = get_balance(ws, device_id)
                if current_balance is not None:
                    update_bot_state(device_id, initial_balance=current_balance); log_message(device_id, f"💰 Initial Balance: {current_balance}")
                else: log_message(device_id, "❌ Failed to retrieve initial balance.")
            
            ticks_to_request = 350; req = {"ticks_history": "R_100", "end": "latest", "count": ticks_to_request, "style": "ticks"}; ws.send(json.dumps(req)); tick_data = json.loads(ws.recv())
            if 'history' in tick_data and tick_data['history']['prices']:
                ticks = tick_data['history']['prices']; timestamps = tick_data['history']['times']; df_ticks = pd.DataFrame({'timestamp': timestamps, 'price': ticks})
                ticks_per_candle = 7; candles_df = ticks_to_ohlc_by_count(df_ticks, ticks_per_candle)
                provisional_decision, _, _, error_msg = analyse_data(candles_df, device_id)
                if error_msg: log_message(device_id, f"❌ Analysis Error: {error_msg}"); continue
                
                final_signal = "Neutral"
                if provisional_decision == "Buy": final_signal = "Buy"
                elif provisional_decision == "Sell": final_signal = "Sell"

                if final_signal in ['Buy', 'Sell']:
                    proposal_req = {"proposal": 1, "amount": round(state.current_amount, 2), "basis": "stake", "contract_type": "CALL" if final_signal == 'Buy' else "PUT", "currency": "USD", "duration": 1, "duration_unit": "m", "symbol": "R_100", "passthrough": {"action": final_signal}}
                    ws.send(json.dumps(proposal_req)); proposal_response = json.loads(ws.recv())
                    if 'proposal' in proposal_response:
                        proposal_id = proposal_response['proposal']['id']; order_response = place_order(ws, proposal_id, state.current_amount, device_id)
                        if 'buy' in order_response and 'contract_id' in order_response['buy']:
                            update_bot_state(device_id, is_trade_open=True, trade_start_time=datetime.now(), contract_id=order_response['buy']['contract_id'])
                        elif 'error' in order_response: log_message(device_id, f"❌ Order failed: {order_response['error']['message']}")
                        else: log_message(device_id, f"❌ Unexpected order response: {order_response}")
                    else: log_message(device_id, f"❌ Proposal failed: {proposal_response.get('error', {}).get('message', 'Unknown error')}")
            else: log_message(device_id, "❌ Error: Could not get tick history data or data is empty.")
        except Exception as e: log_message(device_id, f"❌ An error occurred during the trading cycle: {e}")
        finally:
            if ws and ws.connected: ws.close()


# --- Streamlit App ---
def get_or_create_device_id():
    if 'device_id' not in st.session_state:
        st.session_state.device_id = str(uuid.uuid4())
    device_id = st.session_state.device_id
    session = Session()
    try:
        device = session.query(Device).filter_by(device_id=device_id).first()
        if device:
            return device.device_id, "retrieved"
        else:
            new_device = Device(device_id=device_id)
            session.add(new_device)
            session.commit()
            return device_id, "created"
    except Exception as e:
        session.rollback()
        return None, f"error: {e}"
    finally:
        session.close()

def is_user_allowed(user_id):
    ALLOWED_USERS_FILE = 'user_ids.txt'
    try:
        with open(ALLOWED_USERS_FILE, 'r') as f:
            allowed_ids = {line.strip() for line in f}
            if user_id in allowed_ids: return True
    except FileNotFoundError: st.error(f"Error: '{ALLOWED_USERS_FILE}' not found. Please create this file with a list of allowed user IDs."); return False
    except Exception as e: st.error(f"Error reading '{ALLOWED_USERS_FILE}': {e}"); return False
    return False

def update_bot_state_from_ui(device_id, **kwargs):
    session = Session()
    try:
        state = session.query(BotState).filter_by(device_id=device_id).first()
        if state:
            for key, value in kwargs.items(): setattr(state, key, value)
            session.commit()
        else:
            new_state = BotState(device_id=device_id, **kwargs)
            session.add(new_state)
            session.commit()
    finally: session.close()

def get_logs(device_id):
    session = Session()
    try:
        logs = session.query(BotLog).filter_by(device_id=device_id).order_by(BotLog.timestamp.desc()).limit(100).all()
        return [f"[{log.timestamp.strftime('%H:%M:%S')}] {log.message}" for log in reversed(logs)]
    finally: session.close()

def main_app():
    st.title("KHOURYBOT - Automated Trading 🤖")

    if 'is_authenticated' not in st.session_state: st.session_state.is_authenticated = False
    if 'user_id' not in st.session_state: st.session_state.user_id = None
    if 'page' not in st.session_state: st.session_state.page = 'inputs'
    if 'user_id_checked' not in st.session_state:
        st.session_state.user_id, status = get_or_create_device_id()
        if st.session_state.user_id is None: st.error("Could not get device ID. Please check database connection."); st.session_state.user_id_checked = True
        else: st.session_state.user_id_checked = True

    if not st.session_state.is_authenticated:
        st.header("Log in to Your Account")
        if st.session_state.user_id and is_user_allowed(st.session_state.user_id):
            st.session_state.is_authenticated = True; st.success("Your device has been activated! Redirecting to settings..."); st.balloons(); st.rerun()
        else:
            st.warning("Your device has not been activated yet. To activate the bot, please send this ID to the bot administrator:"); st.code(st.session_state.user_id); st.info("After activation, simply refresh this page to continue.")

    else:
        bot_state = get_bot_state(st.session_state.user_id)
        if not bot_state: update_bot_state_from_ui(st.session_state.user_id)
        bot_state = get_bot_state(st.session_state.user_id)
        
        status_placeholder = st.empty(); timer_placeholder = st.empty()
        if bot_state and bot_state.is_running:
            if not bot_state.is_trade_open:
                status_placeholder.info("Analyzing...")
                now = datetime.now()
                last_action_time = bot_state.last_action_time if bot_state.last_action_time else now
                seconds_since_last_action = (now - last_action_time).total_seconds()
                seconds_left = max(0, 60 - seconds_since_last_action)
                timer_placeholder.metric("Next action in", f"{int(seconds_left)}s")
            else:
                status_placeholder.info("Waiting for trade result..."); timer_placeholder.empty()
        else: status_placeholder.empty(); timer_placeholder.empty()

        if st.session_state.page == 'inputs':
            st.header("1. Bot Settings")
            user_token = st.text_input("Enter your Deriv API token:", type="password", key="api_token_input", value=bot_state.user_token if bot_state and bot_state.user_token else "")
            base_amount = st.number_input("Base Amount ($)", min_value=0.5, step=0.5, value=bot_state.base_amount if bot_state else 0.5)
            tp_target = st.number_input("Take Profit Target ($)", min_value=1.0, step=1.0, value=bot_state.tp_target if bot_state and bot_state.tp_target else 1.0)
            max_consecutive_losses = st.number_input("Max Consecutive Losses", min_value=1, step=1, value=bot_state.max_consecutive_losses if bot_state else 5)
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Start Bot", type="primary"):
                    if not user_token: st.error("Please enter a valid API token before starting the bot.")
                    else: update_bot_state_from_ui(st.session_state.user_id, is_running=True, user_token=user_token, base_amount=base_amount, current_amount=base_amount, consecutive_losses=0, total_wins=0, total_losses=0, tp_target=tp_target, max_consecutive_losses=max_consecutive_losses); st.success("Bot started! You can close this tab."); st.rerun()
            with col2:
                if st.button("Stop Bot"): update_bot_state_from_ui(st.session_state.user_id, is_running=False); st.warning("Bot will stop soon. You can close this tab."); st.rerun()
        
        elif st.session_state.page == 'logs':
            st.header("2. Live Bot Logs")
            if bot_state: st.markdown(f"*Wins: {bot_state.total_wins}* | *Losses: {bot_state.total_losses}*")
            log_records = get_logs(st.session_state.user_id)
            with st.container(height=600):
                st.text_area("Logs", "\n".join(log_records), height=600, key="logs_textarea")
                components.html("""<script>var textarea = parent.document.querySelector('textarea[aria-label="Logs"]'); if(textarea) {textarea.scrollTop = textarea.scrollHeight;}</script>""", height=0, width=0)
        
        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Settings"): st.session_state.page = 'inputs'; st.rerun()
        with col2:
            if st.button("Logs"): st.session_state.page = 'logs'; st.rerun()
        
        if bot_state and bot_state.is_running: import time; time.sleep(1); st.rerun()


if __name__ == "__main__":
    main_app()
