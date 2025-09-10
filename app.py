import streamlit as st
import json
import time
import websocket
import pandas as pd
from datetime import datetime, timedelta

# --- Helper Functions ---
def get_balance(ws):
    req = {"balance": 1, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        return response.get('balance', {}).get('balance')
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

# --- Display Status and Timer ---
st.header("KHOURYBOT - The Simple Trader 🤖")
status_placeholder = st.empty()
timer_placeholder = st.empty()

if st.session_state.bot_running:
    if not st.session_state.is_trade_open:
        status_placeholder.info("Analysing...")
        now = datetime.now()
        next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        seconds_left = max(0, (next_minute - now).total_seconds())
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
                        st.session_state.current_balance = current_balance
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💰 Initial Balance: {st.session_state.initial_balance}")
                    else:
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Failed to retrieve initial balance.")
                
                # Request 60 ticks
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
                            "duration": 30, # Duration in ticks
                            "duration_unit": "t", # Duration unit is ticks
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
        except websocket.WebSocketConnectionClosedException:
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%H:%S')}] ❌ WebSocket connection closed unexpectedly.")
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
    if (datetime.now() - st.session_state.trade_start_time).total_seconds() >= 40:
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
                    
                    if profit > 0:
                        st.session_state.consecutive_losses = 0
                        st.session_state.total_wins += 1
                        st.session_state.current_amount = st.session_state.base_amount
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🎉 Win! Profit: {profit:.2f}$")
                    elif profit < 0:
                        st.session_state.consecutive_losses += 1
                        st.session_state.total_losses += 1
                        next_bet = st.session_state.current_amount * 2.2
                        st.session_state.current_amount = max(st.session_state.base_amount, next_bet)
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💔 Loss! Loss: {profit:.2f}$")
                    else:
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚪ No change. Profit/Loss: 0$")
                        
                    st.session_state.is_trade_open = False
                    
                    current_balance = get_balance(ws)
                    if current_balance is not None:
                        st.session_state.current_balance = current_balance
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
            st.session_state.log_records = [f"[{datetime.now().strftime('%H:%M:%S')}] 🟢 Bot has been started."]
            st.rerun()
            
    if stop_button:
        st.session_state.bot_running = False
        st.session_state.is_trade_open = False
        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 Bot stopped by user.")
        st.rerun()
        
elif st.session_state.page == 'logs':
    st.header("2. Live Bot Logs")
    st.markdown(f"*Wins: {st.session_state.total_wins}* | *Losses: {st.session_state.total_losses}*")
    
    current_balance = st.session_state.current_balance
    if current_balance is not None and st.session_state.initial_balance is not None:
        balance_change = round(current_balance - st.session_state.initial_balance, 2)
        st.metric("Current Balance", f"{current_balance:.2f}$", delta=balance_change)
    else:
        st.info("Balance information not available yet.")

    with st.container(height=600):
        st.text_area("Logs", "\n".join(st.session_state.log_records), height=600)
    
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
if st.session_state.bot_running or st.session_state.is_trade_open:
    st.rerun()
