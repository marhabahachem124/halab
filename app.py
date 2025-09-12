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
    if len(df_ticks) < 15:
        return "Neutral", "Insufficient data: Less than 15 ticks available."
    
    last_15_ticks = df_ticks.tail(15).copy()
    
    # Check for direction based on first vs last tick (Trend Strategy)
    open_15_ticks = last_15_ticks['price'].iloc[0]
    close_15_ticks = last_15_ticks['price'].iloc[-1]
    
    # Check for a BUY signal (Upward trend)
    if close_15_ticks > open_15_ticks:
        return "Buy", None
    
    # Check for a SELL signal (Downward trend)
    elif close_15_ticks < open_15_ticks:
        return "Sell", None
    
    else:
        return "Neutral", "No clear signal."

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
if "trade_start_time" not in st.session_state:
    st.session_state.trade_start_time = None
if "contract_id" not in st.session_state:
    st.session_state.contract_id = None
if "current_balance" not in st.session_state:
    st.session_state.current_balance = None
if "balance_check_needed" not in st.session_state:
    st.session_state.balance_check_needed = True
if "account_currency" not in st.session_state:
    st.session_state.account_currency = "USD"

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
            st.session_state.balance_check_needed = True
            st.rerun()
            
    if stop_button:
        st.session_state.bot_running = False
        st.session_state.is_trade_open = False
        st.rerun()

st.markdown("---")
st.header("Live Bot Status")

# --- Display dynamic status placeholders ---
status_placeholder = st.empty()
wins_losses_placeholder = st.empty()
balance_placeholder = st.empty()
timer_placeholder = st.empty()

state = st.session_state

# --- Main Bot Logic Loop ---
if state.bot_running:
    try:
        # Get and display balance
        ws = websocket.WebSocket()
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        auth_req = {"authorize": state.user_token}
        ws.send(json.dumps(auth_req))
        auth_response = json.loads(ws.recv())

        if auth_response.get('error'):
            status_placeholder.error(f"❌ Auth failed: {auth_response['error']['message']}")
            state.bot_running = False
            st.rerun()
        else:
            state.account_currency = auth_response.get('authorize', {}).get('currency')
            
            if state.initial_balance is None:
                balance = get_balance(ws)
                if balance is not None:
                    state.initial_balance = balance
                    state.current_balance = balance
            
            # Update UI
            wins_losses_placeholder.write(f"**Wins:** {state.total_wins} | **Losses:** {state.total_losses}")
            if state.current_balance is not None:
                balance_placeholder.metric(f"Current Balance ({state.account_currency})", 
                                          f"{state.current_balance:.2f}{state.account_currency}", 
                                          delta=round(state.current_balance - state.initial_balance, 2), 
                                          delta_color="normal")
            
            # Trading Logic
            if not state.is_trade_open:
                now = datetime.now()
                seconds_to_wait = 60 - now.second
                status_placeholder.info(f"**Bot Status:** Analysing... Waiting for the next minute")
                timer_placeholder.metric("Time until next analysis", f"{seconds_to_wait}s")
                
                if seconds_to_wait <= 2:
                    req = {"ticks_history": "R_100", "end": "latest", "count": 15, "style": "ticks"}
                    ws.send(json.dumps(req))
                    tick_data = json.loads(ws.recv())
                    
                    if 'history' in tick_data and tick_data['history']['prices']:
                        ticks = tick_data['history']['prices']
                        df_ticks = pd.DataFrame({'price': ticks})
                        signal, error_msg = analyse_data(df_ticks)
                        
                        if signal in ['Buy', 'Sell']:
                            contract_type = "CALL" if signal == 'Buy' else "PUT"
                            
                            proposal_req = {
                                "proposal": 1,
                                "amount": round(st.session_state.current_amount, 2),
                                "basis": "stake",
                                "contract_type": contract_type,
                                "currency": state.account_currency,
                                "duration": 15,  # MODIFIED: Duration in ticks
                                "duration_unit": "s",  # MODIFIED: Duration unit is ticks
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
            
            elif state.is_trade_open:
                status_placeholder.info(f"**Bot Status:** Waiting for trade result...")
                timer_placeholder.empty()
                if (datetime.now() - state.trade_start_time).total_seconds() >= 20: # MODIFIED: Wait 20 seconds for result
                    contract_info = check_contract_status(ws, state.contract_id)
                    if contract_info and contract_info.get('is_sold'):
                        profit = contract_info.get('profit', 0)
                        
                        if profit > 0:
                            state.consecutive_losses = 0
                            state.total_wins += 1
                            state.current_amount = state.base_amount
                        elif profit < 0:
                            state.consecutive_losses += 1
                            state.total_losses += 1
                            next_bet = state.current_amount * 2.2
                            state.current_amount = max(state.base_amount, next_bet)
                        else:
                            pass
                            
                        state.is_trade_open = False
                        
                        current_balance = get_balance(ws)
                        if current_balance is not None:
                            state.current_balance = current_balance
                            if state.tp_target and (current_balance - state.initial_balance) >= state.tp_target:
                                state.bot_running = False
                                st.rerun()
                            if state.consecutive_losses >= state.max_consecutive_losses:
                                state.bot_running = False
                                st.rerun()
    except Exception as e:
        status_placeholder.error(f"❌ Connection lost. Reconnecting... Error: {e}")
        time.sleep(5)
        st.rerun()
    finally:
        if 'ws' in locals() and ws.connected:
            ws.close()
    
    time.sleep(1)
    st.rerun()

else:
    status_placeholder.info(f"**Bot Status:** {'Stopped'}")
    if state.current_balance is not None:
        balance_placeholder.metric(f"Current Balance ({state.account_currency})", 
                                  f"{state.current_balance:.2f}{state.account_currency}", 
                                  delta=round(state.current_balance - (state.initial_balance if state.initial_balance is not None else state.current_balance), 2), 
                                  delta_color="normal")
    else:
        balance_placeholder.info("Enter API token to get balance.")
    wins_losses_placeholder.write(f"**Wins:** {state.total_wins} | **Losses:** {state.total_losses}")
