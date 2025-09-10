import streamlit as st
import json
import time
import websocket
import pandas as pd
from datetime import datetime
from threading import Thread
import os

# --- Trading Logic Functions ---
def analyse_data(df_ticks):
    if len(df_ticks) < 60:
        return "Neutral", "Insufficient data"
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
        response = json.loads(ws.recv())
        return response
    except Exception:
        return {"error": {"message": "Order placement failed."}}

def check_contract_status(ws, contract_id):
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = ws.recv()
        return json.loads(response)['proposal_open_contract']
    except Exception:
        return None

def get_balance(ws):
    req = {"balance": 1, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        return response.get('balance', {}).get('balance')
    except Exception:
        return None

def main_trading_loop():
    state = st.session_state.get('bot_state', {})
    
    ws = None
    try:
        ws = websocket.WebSocket()
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)

        auth_req = {"authorize": state.get('api_token')}
        ws.send(json.dumps(auth_req))
        auth_response = json.loads(ws.recv())
        if auth_response.get('error'):
            state['status'] = f"Auth failed: {auth_response['error']['message']}"
            st.session_state['bot_state'] = state
            return

        while st.session_state.get('bot_is_running'):
            state = st.session_state.get('bot_state', {})
            
            if not state.get('is_trade_open'):
                now = datetime.now()
                seconds_to_wait = 60 - now.second
                state['status'] = f"Analysing... Waiting for the next minute ({seconds_to_wait}s)"
                st.session_state['bot_state'] = state
                
                if now.second >= 55:
                    if state.get('initial_balance') is None:
                        current_balance = get_balance(ws)
                        if current_balance is not None:
                            state['initial_balance'] = current_balance
                            state['current_balance'] = current_balance
                        else:
                            state['status'] = "Failed to get balance. Retrying..."
                            st.session_state['bot_state'] = state
                            time.sleep(5)
                            continue

                    req = {"ticks_history": "R_100", "end": "latest", "count": 60, "style": "ticks"}
                    ws.send(json.dumps(req))
                    tick_data = json.loads(ws.recv())
                    if 'history' in tick_data and tick_data['history']['prices']:
                        df_ticks = pd.DataFrame({'price': tick_data['history']['prices']})
                        signal, error = analyse_data(df_ticks)
                        
                        if signal in ['Buy', 'Sell']:
                            state['status'] = f"Entering {signal.upper()} trade with {state['current_amount']:.2f}$"
                            proposal_req = {"proposal": 1, "amount": round(state['current_amount'], 2), "basis": "stake", "contract_type": "CALL" if signal == 'Buy' else "PUT", "currency": "USD", "duration": 30, "duration_unit": "s", "symbol": "R_100"}
                            ws.send(json.dumps(proposal_req))
                            proposal_response = json.loads(ws.recv())

                            if 'proposal' in proposal_response:
                                order_response = place_order(ws, proposal_response['proposal']['id'], state['current_amount'])
                                if 'buy' in order_response and order_response['buy'].get('contract_id'):
                                    state['is_trade_open'] = True
                                    state['contract_id'] = order_response['buy']['contract_id']
                                    state['status'] = f"Waiting for trade result... (Contract ID: {state['contract_id']})"
                                    st.session_state['bot_state'] = state
                                else:
                                    error_msg = order_response.get('error', {}).get('message', 'Unknown order placement error')
                                    state['status'] = f"❌ Order failed: {error_msg}"
                                    st.session_state['bot_state'] = state
                            else:
                                error_msg = proposal_response.get('error', {}).get('message', 'Unknown proposal error')
                                state['status'] = f"❌ Proposal failed: {error_msg}"
                                st.session_state['bot_state'] = state
                    else:
                        state['status'] = "No clear signal. Waiting."
                        st.session_state['bot_state'] = state
            
            elif state.get('is_trade_open') and state.get('contract_id'):
                contract_info = check_contract_status(ws, state['contract_id'])
                if contract_info and contract_info.get('is_sold'):
                    profit = contract_info.get('profit', 0)
                    
                    if profit > 0:
                        state['consecutive_losses'] = 0
                        state['current_amount'] = state['base_amount']
                        state['total_wins'] += 1
                        state['status'] = f"🎉 Win! Profit: {profit:.2f}$"
                    else:
                        state['consecutive_losses'] += 1
                        state['current_amount'] = max(state['base_amount'], state['current_amount'] * 2.2)
                        state['total_losses'] += 1
                        state['status'] = f"💔 Loss! Loss: {profit:.2f}$"
                    
                    state['is_trade_open'] = False
                    state['contract_id'] = None
                    current_balance = get_balance(ws)
                    if current_balance is not None:
                        state['current_balance'] = current_balance
                        
                    if state.get('tp_target') and (current_balance - state.get('initial_balance', 0)) >= state['tp_target']:
                        state['status'] = "🤑 Take Profit reached! Bot stopped."
                        st.session_state['bot_is_running'] = False
                        st.session_state['bot_state'] = state
                        return
                        
                    if state['consecutive_losses'] >= state['max_consecutive_losses']:
                        state['status'] = "🛑 Stop Loss hit! Bot stopped."
                        st.session_state['bot_is_running'] = False
                        st.session_state['bot_state'] = state
                        return
                    
                    st.session_state['bot_state'] = state
            
            time.sleep(1)
            
    finally:
        if ws:
            ws.close()

def start_bot_thread():
    st.session_state.bot_thread = Thread(target=main_trading_loop)
    st.session_state.bot_thread.daemon = True
    st.session_state.bot_thread.start()

# --- Streamlit UI ---
st.title("KHOURYBOT - The Simple Trader 🤖")

if 'bot_is_running' not in st.session_state:
    st.session_state.bot_is_running = False
if 'bot_state' not in st.session_state:
    st.session_state.bot_state = {
        'api_token': '',
        'base_amount': 0.5,
        'tp_target': 10.0,
        'max_consecutive_losses': 5,
        'current_amount': 0.5,
        'consecutive_losses': 0,
        'total_wins': 0,
        'total_losses': 0,
        'is_trade_open': False,
        'initial_balance': None,
        'current_balance': None,
        'contract_id': None,
        'status': "Ready to start"
    }
if 'bot_thread' not in st.session_state:
    st.session_state.bot_thread = None

with st.expander("Bot Settings", expanded=True):
    st.text_input("Deriv API Token:", type="password", key='api_token_input', 
                  value=st.session_state.bot_state['api_token'])
    st.number_input("Base Amount ($):", min_value=0.5, step=0.5, key='base_amount_input', 
                    value=st.session_state.bot_state['base_amount'])
    st.number_input("Take Profit Target ($):", min_value=1.0, step=1.0, key='tp_target_input', 
                    value=st.session_state.bot_state['tp_target'])
    st.number_input("Max Consecutive Losses:", min_value=1, step=1, key='max_losses_input', 
                    value=st.session_state.bot_state['max_consecutive_losses'])
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Start Bot", type="primary", disabled=st.session_state.bot_is_running):
            st.session_state.bot_is_running = True
            st.session_state.bot_state = {
                'api_token': st.session_state.api_token_input,
                'base_amount': st.session_state.base_amount_input,
                'tp_target': st.session_state.tp_target_input,
                'max_consecutive_losses': st.session_state.max_losses_input,
                'current_amount': st.session_state.base_amount_input,
                'consecutive_losses': 0,
                'total_wins': 0,
                'total_losses': 0,
                'is_trade_open': False,
                'initial_balance': None,
                'current_balance': None,
                'contract_id': None,
                'status': "Starting..."
            }
            start_bot_thread()
            st.success("Bot started!")
            st.rerun()
            
    with col2:
        if st.button("Stop Bot", disabled=not st.session_state.bot_is_running):
            st.session_state.bot_is_running = False
            st.session_state.bot_state['status'] = "Bot stopped by user."
            st.warning("Bot is stopping...")
            st.rerun()

st.markdown("---")

st.header("Live Bot Status")
status_placeholder = st.empty()
wins_losses_placeholder = st.empty()
balance_placeholder = st.empty()

while st.session_state.bot_is_running:
    state = st.session_state.bot_state
    
    status_placeholder.info(f"**Bot Status:** {state.get('status', 'Stopped')}")
    wins_losses_placeholder.write(f"**Wins:** {state.get('total_wins', 0)} | **Losses:** {state.get('total_losses', 0)}")
    
    current_balance = state.get('current_balance')
    if current_balance is not None:
        balance_placeholder.metric("Current Balance", f"{current_balance:.2f}$", 
                                  delta=round(current_balance - state.get('initial_balance', current_balance), 2), 
                                  delta_color="normal")
    else:
        balance_placeholder.info("Fetching balance...")
        
    time.sleep(1)
    st.rerun()

state = st.session_state.bot_state
status_placeholder.info(f"**Bot Status:** {state.get('status', 'Stopped')}")
wins_losses_placeholder.write(f"**Wins:** {state.get('total_wins', 0)} | **Losses:** {state.get('total_losses', 0)}")

if state.get('current_balance') is not None:
    balance_placeholder.metric("Current Balance", f"{state['current_balance']:.2f}$", 
                              delta=round(state['current_balance'] - state.get('initial_balance', state['current_balance']), 2), 
                              delta_color="normal")
else:
    balance_placeholder.info("Balance not available.")
