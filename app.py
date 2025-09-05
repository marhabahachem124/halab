import streamlit as st
import multiprocessing as mp
import time
import os
import collections
import requests
from datetime import datetime, timedelta
import pandas as pd
import ta
import websocket
import json
import random
import numpy as np
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker, declarative_base
import streamlit.components.v1 as components

# --- Setup Paths and State ---
if 'user_data' not in st.session_state:
    st.session_state.user_data = {}
if 'processes' not in st.session_state:
    st.session_state.processes = {}
if 'page' not in st.session_state:
    st.session_state.page = 'inputs'
if 'all_users' not in st.session_state:
    st.session_state.all_users = []

# --- Database Setup (shared but accessed by each process) ---
DATABASE_URL = "postgresql://khourybot_db_user:wlVAwKwLhfzzH9HFsRMNo3IOo4dX6DYm@dpg-d2smi46r433s73frbbcg-a/khourybot_db"
engine = sa.create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base()

class Device(Base):
    __tablename__ = 'devices'
    id = sa.Column(sa.Integer, primary_key=True)
    device_id = sa.Column(sa.String, unique=True, nullable=False)

Base.metadata.create_all(engine)

# --- Licensing System ---
ALLOWED_USERS_FILE = 'user_ids.txt'

def get_or_create_device_id():
    session = Session()
    try:
        device = session.query(Device).first()
        if device:
            return device.device_id
        else:
            new_id = str(random.randint(1000000000000000, 9999999999999999))
            new_device = Device(device_id=new_id)
            session.add(new_device)
            session.commit()
            return new_id
    except Exception as e:
        session.rollback()
        return None
    finally:
        session.close()

def is_user_allowed(user_id):
    try:
        with open(ALLOWED_USERS_FILE, 'r') as f:
            allowed_ids = {line.strip() for line in f}
            if user_id in allowed_ids:
                return True
    except FileNotFoundError:
        st.error(f"Error: '{ALLOWED_USERS_FILE}' not found. Please create this file.")
        return False
    return False

# --- Bot Logic (to be run in a separate process) ---
def run_bot(user_id, api_token, log_queue, initial_balance, base_amount, tp_target, max_consecutive_losses):
    log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] 🟢 البوت شغال")

    # Initialization inside the process
    current_amount = base_amount
    consecutive_losses = 0
    is_trade_open = False
    trade_start_time = None
    contract_id = None
    total_wins = 0
    total_losses = 0

    def get_balance(ws):
        try:
            ws.send(json.dumps({"balance": 1}))
            response = json.loads(ws.recv())
            return response['balance']['balance']
        except Exception:
            return None

    def check_contract_status(ws, contract_id):
        try:
            ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}))
            response = ws.recv()
            response_data = json.loads(response)
            if response_data.get('msg_type') == 'proposal_open_contract':
                return response_data['proposal_open_contract']
            return None
        except Exception:
            return None

    def analyse_data(data):
        required_candles = 50
        if data.empty or len(data) < required_candles:
            return "Neutral", 0, 0, "Insufficient data"
        data = data.tail(required_candles).copy()
        signals = []
        def get_indicator_signal(indicator_func):
            try:
                result = indicator_func()
                if isinstance(result, pd.Series) and not result.empty:
                    return result.iloc[-1]
                return None
            except Exception:
                return None
        
        rsi_value = get_indicator_signal(lambda: ta.momentum.RSIIndicator(data['Close']).rsi())
        if rsi_value is not None:
            signals.append("Buy" if rsi_value >= 50 else "Sell")
        
        stoch_value = get_indicator_signal(lambda: ta.momentum.StochasticOscillator(data['High'], data['Low'], data['Close']).stoch())
        if stoch_value is not None:
            signals.append("Buy" if stoch_value >= 50 else "Sell")
        
        roc_value = get_indicator_signal(lambda: ta.momentum.ROCIndicator(data['Close']).roc())
        if roc_value is not None:
            signals.append("Buy" if roc_value >= 0 else "Sell")
        
        adx_indicator = ta.trend.ADXIndicator(data['High'], data['Low'], data['Close'])
        adx_pos_val = get_indicator_signal(lambda: adx_indicator.adx_pos())
        adx_neg_val = get_indicator_signal(lambda: adx_indicator.adx_neg())
        if adx_pos_val is not None and adx_neg_val is not None:
            signals.append("Buy" if adx_pos_val >= adx_neg_val else "Sell")
        
        macd_indicator = ta.trend.MACD(data['Close'])
        macd_val = get_indicator_signal(lambda: macd_indicator.macd())
        macd_signal_val = get_indicator_signal(lambda: macd_indicator.macd_signal())
        if macd_val is not None and macd_signal_val is not None:
            signals.append("Buy" if macd_val >= macd_signal_val else "Sell")
        
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

        if len(data) >= 20:
            ema10 = get_indicator_signal(lambda: ta.trend.EMAIndicator(data['Close'], window=10).ema_indicator())
            ema20 = get_indicator_signal(lambda: ta.trend.EMAIndicator(data['Close'], window=20).ema_indicator())
            if ema10 is not None and ema20 is not None:
                signals.append("Buy" if ema10 >= ema20 else "Sell")
        
        obv_series = ta.volume.OnBalanceVolumeIndicator(data['Close'], data['Volume']).on_balance_volume()
        if not obv_series.empty and len(obv_series) > 1:
            if obv_series.iloc[-1] > obv_series.iloc[-2]:
                signals.append("Buy")
            elif obv_series.iloc[-1] < obv_series.iloc[-2]:
                signals.append("Sell")
        
        cci_value = get_indicator_signal(lambda: ta.trend.CCIIndicator(data['High'], data['Low'], data['Close']).cci())
        if cci_value is not None:
            if cci_value > 0:
                signals.append("Buy")
            elif cci_value < 0:
                signals.append("Sell")
        
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
        return provisional_decision, buy_count, sell_count, None

    def ticks_to_ohlc_by_count(ticks_df, tick_count):
        if ticks_df.empty: return pd.DataFrame()
        ohlc_data = []
        prices = ticks_df['price'].values
        timestamps = ticks_df['timestamp'].values
        for i in range(0, len(prices), tick_count):
            chunk = prices[i:i + tick_count]
            if len(chunk) == tick_count:
                ohlc_data.append({
                    'timestamp': timestamps[i+tick_count-1],
                    'Open': chunk[0],
                    'High': np.max(chunk),
                    'Low': np.min(chunk),
                    'Close': chunk[-1],
                    'Volume': tick_count
                })
        ohlc_df = pd.DataFrame(ohlc_data)
        if not ohlc_df.empty:
            ohlc_df['timestamp'] = pd.to_datetime(ohlc_df['timestamp'], unit='s')
            ohlc_df.set_index('timestamp', inplace=True)
        return ohlc_df

    def place_order(ws, proposal_id, amount):
        valid_amount = round(max(0.5, amount), 2)
        req = {"buy": proposal_id, "price": valid_amount}
        try:
            ws.send(json.dumps(req))
            response = json.loads(ws.recv())
            return response
        except Exception:
            return {"error": {"message": "Failed to place order"}}

    last_action_time = datetime.min
    while True:
        try:
            now = datetime.now()
            seconds_in_minute = now.second
            
            if not is_trade_open and (now - last_action_time).total_seconds() >= 60 and seconds_in_minute >= 55:
                last_action_time = now
                ws = websocket.WebSocket()
                ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                ws.send(json.dumps({"authorize": api_token}))
                auth_response = json.loads(ws.recv())

                if auth_response.get('error'):
                    log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ فشل المصادقة: {auth_response['error']['message']}")
                    ws.close()
                    continue
                
                # Get initial balance on first run
                if initial_balance is None:
                    initial_balance = get_balance(ws)
                    if initial_balance is not None:
                        log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] 💰 الرصيد الحالي: {initial_balance:.2f}")

                ticks_to_request = 350
                ws.send(json.dumps({"ticks_history": "R_100", "end": "latest", "count": ticks_to_request, "style": "ticks"}))
                tick_data = json.loads(ws.recv())
                
                if 'history' in tick_data and tick_data['history']['prices']:
                    df_ticks = pd.DataFrame({'timestamp': tick_data['history']['times'], 'price': tick_data['history']['prices']})
                    candles_df = ticks_to_ohlc_by_count(df_ticks, 7)
                    provisional_decision, _, _, _ = analyse_data(candles_df)
                    
                    last_5_ticks = df_ticks.tail(5)
                    last_5_signal = "Neutral"
                    if len(last_5_ticks) == 5:
                        if last_5_ticks['price'].iloc[-1] > last_5_ticks['price'].iloc[0]:
                            last_5_signal = "Buy"
                        elif last_5_ticks['price'].iloc[-1] < last_5_ticks['price'].iloc[0]:
                            last_5_signal = "Sell"

                    last_60_ticks = df_ticks.tail(60)
                    last_60_signal = "Neutral"
                    if len(last_60_ticks) == 60:
                        if last_60_ticks['price'].iloc[-1] > last_60_ticks['price'].iloc[0]:
                            last_60_signal = "Buy"
                        elif last_60_ticks['price'].iloc[-1] < last_60_ticks['price'].iloc[0]:
                            last_60_signal = "Sell"

                    final_signal = "Neutral"
                    if provisional_decision == "Buy" and last_5_signal == "Buy" and last_60_signal == "Buy":
                        final_signal = "Sell"
                    elif provisional_decision == "Sell" and last_5_signal == "Sell" and last_60_signal == "Sell":
                        final_signal = "Buy"

                    if final_signal in ['Buy', 'Sell']:
                        proposal_req = {
                            "proposal": 1,
                            "amount": round(current_amount, 2),
                            "basis": "stake",
                            "contract_type": "CALL" if final_signal == 'Buy' else "PUT",
                            "currency": "USD",
                            "duration": 1,
                            "duration_unit": "m",
                            "symbol": "R_100",
                        }
                        ws.send(json.dumps(proposal_req))
                        proposal_response = json.loads(ws.recv())
                        
                        if 'proposal' in proposal_response:
                            proposal_id = proposal_response['proposal']['id']
                            order_response = place_order(ws, proposal_id, current_amount)
                            
                            if 'buy' in order_response:
                                is_trade_open = True
                                trade_start_time = datetime.now()
                                contract_id = order_response['buy']['contract_id']
                            elif 'error' in order_response:
                                log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ فشل الطلب: {order_response['error']['message']}")
                        else:
                            log_queue.put(f"[{datetime.now().strftime('%H:%H:%S')}] ❌ فشل الاقتراح: {proposal_response.get('error', {}).get('message', 'خطأ غير معروف')}")
                ws.close()
            
            if is_trade_open and (datetime.now() >= trade_start_time + timedelta(seconds=70)):
                ws = websocket.WebSocket()
                ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                ws.send(json.dumps({"authorize": api_token}))
                json.loads(ws.recv())
                
                contract_info = check_contract_status(ws, contract_id)
                if contract_info and contract_info.get('is_sold'):
                    profit = contract_info.get('profit', 0)
                    
                    log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] ⏳ انتظار النتيجة...")
                    
                    if profit > 0:
                        consecutive_losses = 0
                        total_wins += 1
                        current_amount = base_amount
                    elif profit < 0:
                        consecutive_losses += 1
                        total_losses += 1
                        current_amount = max(base_amount, current_amount * 2.2)
                    
                    is_trade_open = False
                    current_balance = get_balance(ws)
                    if current_balance is not None:
                        log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] 💰 الرصيد الحالي: {current_balance:.2f}")
                        log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ عدد الصفقات الرابحة: {total_wins}, 🔴 عدد الصفقات الخاسرة: {total_losses}")

                    if tp_target and (current_balance - initial_balance) >= tp_target:
                        log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] 🤑 تم الوصول لهدف جني الأرباح ({tp_target}$)! توقف البوت.")
                        break
                    if consecutive_losses >= max_consecutive_losses:
                        log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 تم الوصول لحد وقف الخسارة ({consecutive_losses} خسارة متتالية)! توقف البوت.")
                        break
                ws.close()
                
        except Exception as e:
            log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ حدث خطأ: {e}")
        time.sleep(1)

# --- Streamlit UI ---
st.title("KHOURYBOT - Automated Trading 🤖")

# --- UI for user login/access ---
user_id_from_db = get_or_create_device_id()
if user_id_from_db not in st.session_state.all_users:
    st.session_state.all_users.append(user_id_from_db)

if not is_user_allowed(user_id_from_db):
    st.warning("جهازك غير مفعّل. الرجاء إرسال هذا المعرّف للمسؤول لتفعيله:")
    st.code(user_id_from_db)
    st.stop()

st.header("1. إعدادات المستخدم")
user_id = st.text_input("معرّف المستخدم", user_id_from_db, disabled=True)
api_token = st.text_input("رمز API الخاص بك", type="password")
base_amount = st.number_input("مبلغ التداول الأساسي ($)", min_value=0.5, step=0.5, value=st.session_state.user_data.get(user_id, {}).get('base_amount', 0.5))
tp_target = st.number_input("هدف جني الأرباح ($)", min_value=1.0, step=1.0, value=st.session_state.user_data.get(user_id, {}).get('tp_target', 5.0))
max_consecutive_losses = st.number_input("الحد الأقصى للخسائر المتتالية", min_value=1, step=1, value=st.session_state.user_data.get(user_id, {}).get('max_consecutive_losses', 5))

col1, col2 = st.columns(2)
with col1:
    start_button = st.button("بدء البوت", type="primary")
with col2:
    stop_button = st.button("إيقاف البوت")

# --- Logic for starting/stopping bot process ---
if start_button:
    if not api_token:
        st.error("الرجاء إدخال رمز API.")
    else:
        if user_id in st.session_state.processes and st.session_state.processes[user_id].is_alive():
            st.warning(f"البوت قيد التشغيل بالفعل للمستخدم {user_id}.")
        else:
            log_queue = mp.Queue()
            process = mp.Process(target=run_bot, args=(user_id, api_token, log_queue, None, base_amount, tp_target, max_consecutive_losses))
            process.start()
            st.session_state.processes[user_id] = process
            st.session_state.user_data[user_id] = {
                'status': 'Running',
                'logs': [],
                'log_queue': log_queue,
                'base_amount': base_amount,
                'tp_target': tp_target,
                'max_consecutive_losses': max_consecutive_losses,
            }
            st.success(f"تم بدء البوت للمستخدم {user_id}.")

if stop_button:
    if user_id in st.session_state.processes and st.session_state.processes[user_id].is_alive():
        st.session_state.processes[user_id].terminate()
        st.session_state.processes[user_id].join()
        st.session_state.user_data[user_id]['status'] = 'Stopped'
        st.warning(f"تم إيقاف البوت للمستخدم {user_id}.")
    else:
        st.info(f"لا يوجد بوت قيد التشغيل للمستخدم {user_id}.")

# --- Display Logs ---
st.header("2. سجلات البوت")
if user_id in st.session_state.user_data:
    user_logs_data = st.session_state.user_data[user_id]
    if 'log_queue' in user_logs_data and user_logs_data['status'] == 'Running':
        while not user_logs_data['log_queue'].empty():
            user_logs_data['logs'].append(user_logs_data['log_queue'].get())
    
    st.markdown(f"**الحالة:** {user_logs_data['status']}")
    st.text_area(f"سجلات المستخدم {user_id}", "\n".join(user_logs_data['logs']), height=400)
