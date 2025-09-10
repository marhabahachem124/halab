import streamlit as st
import json
import time
import websocket
import pandas as pd
from datetime import datetime, timedelta
import threading

# --- Helper Functions ---
def get_balance(ws):
    req = {"balance": 1, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        if response.get('msg_type') == 'balance':
            return response.get('balance', {}).get('balance')
        return None
    except Exception:
        return None

def analyse_data(df_ticks):
    if len(df_ticks) < 60:
        return "Neutral", "Insufficient data: Less than 60 ticks available."
    
    last_60_ticks = df_ticks.tail(60).copy()
    first_30 = last_60_ticks.iloc[:30]
    last_30 = last_60_ticks.iloc[30:]
    
    avg_first_30 = first_30['price'].mean()
    avg_last_30 = last_30['price'].mean()
    
    if avg_last_30 > avg_first_30:
        return "Buy", None
    elif avg_last_30 < avg_first_30:
        return "Sell", None
    else:
        return "Neutral", "No clear trend in the last 60 ticks."

def place_order(ws, proposal_id, amount):
    req = {"buy": proposal_id, "price": round(max(0.5, amount), 2)}
    try:
        ws.send(json.dumps(req))
        while True:
            response = json.loads(ws.recv())
            if response.get('msg_type') == 'buy':
                return response
            elif response.get('msg_type') == 'balance':
                st.session_state.current_balance = response.get('balance', {}).get('balance')
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💰 Balance updated: {st.session_state.current_balance:.2f}")
            else:
                pass
    except Exception:
        return {"error": {"message": "Order placement failed."}}

def check_contract_status(ws, contract_id):
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        while True:
            response = json.loads(ws.recv())
            if response.get('msg_type') == 'proposal_open_contract':
                return response.get('proposal_open_contract')
            elif response.get('msg_type') == 'balance':
                st.session_state.current_balance = response.get('balance', {}).get('balance')
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💰 Balance updated: {st.session_state.current_balance:.2f}")
            else:
                pass
    except Exception:
        return None

# --- Initial Setup ---
if "user_token" not in st.session_state:
    st.session_state.user_token = ""
if "bot_running" not in st.session_state:
    st.session_state.bot_running = False
if "is_trade_open" not in st.session_state:
    st.session_state.is_trade_open = False
if "last_action_time" not in st.session_state:
    st.session_state.last_action_time = datetime.now()
if "initial_balance" not in st.session_state:
    st.session_state.initial_balance = None
if "log_records" not in st.session_state:
    st.session_state.log_records = []
if "base_amount" not in st.session_state:
    st.session_state.base_amount = 0.5
if "current_amount" not in st.session_state:
    st.session_state.current_amount = 0.5
if "consecutive_losses" not in st.session_state:
    st.session_state.consecutive_losses = 0
if "total_wins" not in st.session_state:
    st.session_state.total_wins = 0
if "total_losses" not in st.session_state:
    st.session_state.total_losses = 0
if "tp_target" not in st.session_state:
    st.session_state.tp_target = 10.0
if "max_consecutive_losses" not in st.session_state:
    st.session_state.max_consecutive_losses = 5
if "page" not in st.session_state:
    st.session_state.page = 'inputs'
if "trade_start_time" not in st.session_state:
    st.session_state.trade_start_time = None
if "contract_id" not in st.session_state:
    st.session_state.contract_id = None
if "current_balance" not in st.session_state:
    st.session_state.current_balance = None
if "balance_check_needed" not in st.session_state:
    st.session_state.balance_check_needed = True

# --- Display UI and handle user input ---
st.header("KHOURYBOT - The Simple Trader 🤖")

with st.expander("Bot Settings", expanded=True):
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
            st.session_state.log_records = [f"[{datetime.now().strftime('%H:%M:%S')}] 🟢 Bot has been started."]
            st.session_state.balance_check_needed = True
            st.rerun()
            
    if stop_button:
        st.session_state.bot_running = False
        st.session_state.is_trade_open = False
        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 Bot stopped by user.")
        st.rerun()

st.markdown("---")
st.header("Live Bot Status")

# --- Display dynamic status ---
status_placeholder = st.empty()
wins_losses_placeholder = st.empty()
balance_placeholder = st.empty()
timer_placeholder = st.empty()

state = st.session_state

# --- Get and display balance immediately if token is present ---
if state.user_token and state.balance_check_needed:
    ws = None
    try:
        ws = websocket.WebSocket()
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        auth_req = {"authorize": state.user_token}
        ws.send(json.dumps(auth_req))
        auth_response = json.loads(ws.recv())
        if auth_response.get('error'):
            state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Auth failed: {auth_response['error']['message']}")
            state.bot_running = False
        else:
            balance = get_balance(ws)
            if balance is not None:
                state.initial_balance = balance
                state.current_balance = balance
                state.balance_check_needed = False
            else:
                state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Failed to get initial balance.")
    except Exception as e:
        state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Connection error: {e}")
    finally:
        if ws and ws.connected:
            ws.close()
    
# --- Update UI based on current state ---
if state.bot_running:
    if state.current_balance is not None:
        balance_placeholder.metric("Current Balance", f"{state.current_balance:.2f}$", 
                                  delta=round(state.current_balance - state.initial_balance, 2), 
                                  delta_color="normal")
    else:
        balance_placeholder.info("Fetching balance...")
        st.rerun()

    wins_losses_placeholder.write(f"**Wins:** {state.total_wins} | **Losses:** {state.total_losses}")
    
    # --- Main Trading Logic ---
    ws = None
    try:
        ws = websocket.WebSocket()
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        auth_req = {"authorize": state.user_token}
        ws.send(json.dumps(auth_req))
        auth_response = json.loads(ws.recv())
        if auth_response.get('error'):
            state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Auth failed: {auth_response['error']['message']}")
            state.bot_running = False
            st.rerun()

        if not state.is_trade_open:
            now = datetime.now()
            seconds_to_wait = 60 - now.second
            status_placeholder.info(f"**Bot Status:** Analysing... Waiting for the next minute")
            timer_placeholder.metric("Time until next analysis", f"{seconds_to_wait}s")
            
            if now.second >= 55:
                req = {"ticks_history": "R_100", "end": "latest", "count": 60, "style": "ticks"}
                ws.send(json.dumps(req))
                tick_data = json.loads(ws.recv())
                
                if 'history' in tick_data and tick_data['history']['prices']:
                    ticks = tick_data['history']['prices']
                    df_ticks = pd.DataFrame({'price': ticks})
                    
                    signal, error_msg = analyse_data(df_ticks)
                    
                    if error_msg:
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Analysis Error: {error_msg}")
                    
                    if signal in ['Buy', 'Sell']:
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ➡ Entering a {signal.upper()} trade with {round(st.session_state.current_amount, 2)}$")
                        
                        proposal_req = {
                            "proposal": 1,
                            "amount": round(st.session_state.current_amount, 2),
                            "basis": "stake",
                            "contract_type": "CALL" if signal == 'Buy' else "PUT",
                            "currency": "USD",
                            "duration": 30,  # Changed from 30 t to 30 s
                            "duration_unit": "s",
                            "symbol": "R_100"
                        }
                        ws.send(json.dumps(proposal_req))
                        proposal_response = json.loads(ws.recv())
                        
                        if 'proposal' in proposal_response:
                            proposal_id = proposal_response['proposal']['id']
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
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚪ No clear signal. Waiting for the next analysis cycle.")

                else:
                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Error: Could not get tick history data or data is empty.")
        
        elif state.is_trade_open:
            status_placeholder.info(f"**Bot Status:** Waiting for trade result...")
            if (datetime.now() - state.trade_start_time).total_seconds() >= 40:
                contract_info = check_contract_status(ws, state.contract_id)
                if contract_info and contract_info.get('is_sold'):
                    profit = contract_info.get('profit', 0)
                    
                    if profit > 0:
                        state.consecutive_losses = 0
                        state.total_wins += 1
                        state.current_amount = state.base_amount
                        state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🎉 Win! Profit: {profit:.2f}$")
                    elif profit < 0:
                        state.consecutive_losses += 1
                        state.total_losses += 1
                        next_bet = state.current_amount * 2.2
                        state.current_amount = max(state.base_amount, next_bet)
                        state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💔 Loss! Loss: {profit:.2f}$")
                    else:
                        state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚪ No change. Profit/Loss: 0$")
                        
                    state.is_trade_open = False
                    
                    current_balance = get_balance(ws)
                    if current_balance is not None:
                        state.current_balance = current_balance
                        state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💰 Current Balance: {current_balance:.2f}")
                        
                        if state.tp_target and (current_balance - state.initial_balance) >= state.tp_target:
                            state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🤑 Take Profit target ({state.tp_target}$) reached! Bot stopped.")
                            state.bot_running = False
                    else:
                        state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠ Could not retrieve balance after trade.")
                        
                    if state.consecutive_losses >= state.max_consecutive_losses:
                        state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 Stop Loss hit ({state.consecutive_losses} consecutive losses)! Bot stopped.")
                        state.bot_running = False
                elif contract_info and not contract_info.get('is_sold'):
                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠ Contract {st.session_state.contract_id} is not yet sold/closed.")
                else:
                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠ Could not get contract info for ID: {st.session_state.contract_id}. Contract might have been cancelled or failed.")
                    state.is_trade_open = False
        
    finally:
        if ws and ws.connected:
            ws.close()
    
    time.sleep(1)
    st.rerun()

else:
    status_placeholder.info(f"**Bot Status:** {state.get('status', 'Stopped')}")
    if state.current_balance is not None:
        balance_placeholder.metric("Current Balance", f"{state.current_balance:.2f}$", 
                                  delta=round(state.current_balance - (state.initial_balance if state.initial_balance is not None else state.current_balance), 2), 
                                  delta_color="normal")
    else:
        balance_placeholder.info("Enter API token to get balance.")
    wins_losses_placeholder.write(f"**Wins:** {state.total_wins} | **Losses:** {state.total_losses}")

st.markdown("---")
if st.button("Logs"):
    st.session_state.page = 'logs'

if st.session_state.page == 'logs':
    st.header("Live Bot Logs")
    with st.container(height=600):
        st.text_area("Logs", "\n".join(st.session_state.log_records), height=600)
