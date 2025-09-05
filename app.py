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
if 'processes' in st.session_state:
    # Check for orphaned processes and terminate them on app rerun
    for pid in list(st.session_state.processes.keys()):
        p = st.session_state.processes[pid]
        if not p.is_alive():
            p.join()
            del st.session_state.processes[pid]
else:
    st.session_state.processes = {}

if 'page' not in st.session_state:
    st.session_state.page = 'inputs'
if 'all_users' not in st.session_state:
    st.session_state.all_users = []

# --- Database Setup (shared but accessed by each process) ---
# NOTE: Replace with your actual PostgreSQL connection string
DATABASE_URL = "postgresql://khourybot_db_user:wlVAwKwLhfzzH9HFsRMNo3IOo4dX6DYm@dpg-d2smi46r433s73frbbcg-a/khourybot_db"
try:
    engine = sa.create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    Base = declarative_base()

    class Device(Base):
        __tablename__ = 'devices'
        id = sa.Column(sa.Integer, primary_key=True)
        device_id = sa.Column(sa.String, unique=True, nullable=False)

    Base.metadata.create_all(engine)
except Exception as e:
    st.error(f"Failed to connect to the database. Please check your DATABASE_URL. Error: {e}")
    st.stop()


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
        if not os.path.exists(ALLOWED_USERS_FILE):
            st.error(f"Error: '{ALLOWED_USERS_FILE}' not found. Please create this file and add your user ID.")
            return False
        with open(ALLOWED_USERS_FILE, 'r') as f:
            allowed_ids = {line.strip() for line in f}
            return user_id in allowed_ids
    except Exception:
        return False

# --- Bot Logic (to be run in a separate process) ---
def run_bot(user_id, api_token, log_queue, initial_balance, base_amount, tp_target, max_consecutive_losses):
    
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
    
    # New logic: Analyse by comparing direction of first vs second half of ticks
    def analyse_data(ticks_df):
        if len(ticks_df) < 120:
            return "Neutral", "Insufficient data (less than 120 ticks)"

        first_half = ticks_df.iloc[:60]
        second_half = ticks_df.iloc[60:]

        first_half_direction = "Neutral"
        if first_half['price'].iloc[-1] > first_half['price'].iloc[0]:
            first_half_direction = "Up"
        elif first_half['price'].iloc[-1] < first_half['price'].iloc[0]:
            first_half_direction = "Down"

        second_half_direction = "Neutral"
        if second_half['price'].iloc[-1] > second_half['price'].iloc[0]:
            second_half_direction = "Up"
        elif second_half['price'].iloc[-1] < second_half['price'].iloc[0]:
            second_half_direction = "Down"

        if first_half_direction == "Up" and second_half_direction == "Down":
            return "Sell", "Reversal: Up trend followed by a Down trend"
        elif first_half_direction == "Down" and second_half_direction == "Up":
            return "Buy", "Reversal: Down trend followed by an Up trend"
        else:
            return "Neutral", "No clear reversal pattern found"

    def place_order(ws, proposal_id, amount):
        valid_amount = round(amount, 2)
        req = {"buy": proposal_id, "price": valid_amount}
        try:
            ws.send(json.dumps(req))
            response = json.loads(ws.recv())
            return response
        except Exception:
            return {"error": {"message": "Failed to place order"}}

    last_action_time = datetime.min
    
    log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] 🟢 Bot is running.")
    
    ws = websocket.WebSocket()
    ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
    ws.send(json.dumps({"authorize": api_token}))
    auth_response = json.loads(ws.recv())
    initial_balance = get_balance(ws)
    if initial_balance is not None:
        log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] 💰 Initial Balance: {initial_balance:.2f} USD")
    ws.close()
    
    while True:
        try:
            now = datetime.now()
            
            if not is_trade_open:
                # Countdown logic
                remaining_seconds = 60 - now.second
                log_queue.put(f"[{now.strftime('%H:%M:%S')}] ⏳ Waiting... ({remaining_seconds}s remaining)")

                if now.second >= 55:
                    last_action_time = now
                    ws = websocket.WebSocket()
                    ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                    ws.send(json.dumps({"authorize": api_token}))
                    auth_response = json.loads(ws.recv())

                    if auth_response.get('error'):
                        log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Authentication failed.")
                        ws.close()
                        continue
                    
                    ticks_to_request = 120
                    ws.send(json.dumps({"ticks_history": "R_100", "end": "latest", "count": ticks_to_request, "style": "ticks"}))
                    tick_data = json.loads(ws.recv())
                    
                    if 'history' in tick_data and tick_data['history']['prices'] and len(tick_data['history']['prices']) >= 120:
                        df_ticks = pd.DataFrame({'timestamp': tick_data['history']['times'], 'price': tick_data['history']['prices']})
                        provisional_decision, analysis_reason = analyse_data(df_ticks)
                        
                        if provisional_decision == "Neutral":
                            log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] 🚫 No trade signal. Waiting.")

                        final_signal = "Neutral"
                        if provisional_decision == "Buy":
                            final_signal = "Call"
                        elif provisional_decision == "Sell":
                            final_signal = "Put"

                        if final_signal in ['Call', 'Put']:
                            proposal_req = {
                                "proposal": 1,
                                "amount": round(current_amount, 2),
                                "basis": "stake",
                                "contract_type": final_signal,
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
                                else:
                                    log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Order failed.")
                            else:
                                log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Proposal failed.")
                    else:
                         log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Insufficient tick data.")
                    ws.close()
            
            if is_trade_open and (datetime.now() >= trade_start_time + timedelta(seconds=70)):
                ws = websocket.WebSocket()
                ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                ws.send(json.dumps({"authorize": api_token}))
                json.loads(ws.recv())
                
                contract_info = check_contract_status(ws, contract_id)
                if contract_info and contract_info.get('is_sold'):
                    profit = contract_info.get('profit', 0)
                    
                    if profit > 0:
                        consecutive_losses = 0
                        total_wins += 1
                        current_amount = base_amount
                    elif profit < 0:
                        consecutive_losses += 1
                        total_losses += 1
                        current_amount = max(base_amount, current_amount * 2.2)
                    
                    log_queue.put(("stats", total_wins, total_losses))
                    
                    is_trade_open = False
                    current_balance = get_balance(ws)
                    if current_balance is not None:
                        log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] 💰 Current Balance: {current_balance:.2f} USD")

                    if tp_target and (current_balance - initial_balance) >= tp_target:
                        log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] 🤑 TP hit! Bot is stopping.")
                        break
                    if consecutive_losses >= max_consecutive_losses:
                        log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 SL hit! Bot is stopping.")
                        break
                ws.close()
                
        except Exception as e:
            log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ An error occurred: {e}")
        time.sleep(1)

# --- Streamlit UI ---
st.title("KHOURYBOT - Automated Trading 🤖")

# --- UI for user login/access ---
user_id_from_db = get_or_create_device_id()
if user_id_from_db not in st.session_state.all_users:
    st.session_state.all_users.append(user_id_from_db)

if not is_user_allowed(user_id_from_db):
    st.warning("Your device is not activated. Please send this ID to the admin to activate it:")
    st.code(user_id_from_db)
    st.stop()

st.header("1. User Settings")
user_id = st.text_input("User ID", user_id_from_db, disabled=True)
api_token = st.text_input("Your API Token", type="password")
base_amount = st.number_input("Base Trading Amount ($)", min_value=0.5, step=0.5, value=st.session_state.user_data.get(user_id, {}).get('base_amount', 0.5))
tp_target = st.number_input("Take Profit Target ($)", min_value=1.0, step=1.0, value=st.session_state.user_data.get(user_id, {}).get('tp_target', 5.0))
max_consecutive_losses = st.number_input("Max Consecutive Losses", min_value=1, step=1, value=st.session_state.user_data.get(user_id, {}).get('max_consecutive_losses', 5))

col1, col2 = st.columns(2)
with col1:
    start_button = st.button("Start Bot", type="primary")
with col2:
    stop_button = st.button("Stop Bot")

# --- Logic for starting/stopping bot process ---
if start_button:
    if not api_token:
        st.error("Please enter your API token.")
    else:
        if user_id in st.session_state.processes and st.session_state.processes[user_id].is_alive():
            st.warning(f"Bot is already running for user {user_id}.")
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
                'wins': 0,
                'losses': 0
            }
            st.success(f"Bot started for user {user_id}.")

if stop_button:
    if user_id in st.session_state.processes and st.session_state.processes[user_id].is_alive():
        st.session_state.processes[user_id].terminate()
        st.session_state.processes[user_id].join()
        st.session_state.user_data[user_id]['status'] = 'Stopped'
        st.session_state.user_data[user_id]['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 Bot stopped.")
        st.warning(f"Bot stopped for user {user_id}.")
    else:
        st.info(f"No bot is running for user {user_id}.")

# --- Display Logs and Stats ---
st.header("2. Bot Logs")
if user_id in st.session_state.user_data:
    user_logs_data = st.session_state.user_data[user_id]
    
    # Update stats and logs from the queue
    if 'log_queue' in user_logs_data and user_logs_data['status'] == 'Running':
        while not user_logs_data['log_queue'].empty():
            log_item = user_logs_data['log_queue'].get()
            if isinstance(log_item, tuple) and log_item[0] == "stats":
                user_logs_data['wins'] = log_item[1]
                user_logs_data['losses'] = log_item[2]
            else:
                user_logs_data['logs'].append(log_item)
    
    # Display stats at the top of the logs section
    st.subheader("Trading Statistics")
    col_wins, col_losses = st.columns(2)
    with col_wins:
        st.metric(label="✅ Wins", value=user_logs_data.get('wins', 0))
    with col_losses:
        st.metric(label="🔴 Losses", value=user_logs_data.get('losses', 0))
    
    st.text_area(f"Logs for user {user_id}", "\n".join(user_logs_data['logs']), height=400)
