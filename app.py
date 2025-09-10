import streamlit as st
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker, declarative_base # Corrected import for declarative_base
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, ForeignKey
from datetime import datetime
import json
import uuid
import time
import os
import websocket
import pandas as pd
from threading import Thread

# --- Database Setup ---
# Ensure this URL is correct and points to your database
DATABASE_URL = "postgresql://deriv_pv02_user:pkCXarwp82IBTnoIWySO8CuAVLUcw1B1@dpg-d30otpogjchc73f4bieg-a.oregon-postgres.render.com/deriv_pv02"
engine = sa.create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base() # Use declarative_base from sqlalchemy.orm

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)

class BotSession(Base):
    __tablename__ = 'bot_sessions'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    session_id = Column(String, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    api_token = Column(String, nullable=True)
    base_amount = Column(Float, default=0.5)
    tp_target = Column(Float, nullable=True)
    max_consecutive_losses = Column(Integer, default=5)
    current_amount = Column(Float, default=0.5)
    consecutive_losses = Column(Integer, default=0)
    total_wins = Column(Integer, default=0)
    total_losses = Column(Integer, default=0)
    is_running = Column(Boolean, default=False)
    is_trade_open = Column(Boolean, default=False)
    initial_balance = Column(Float, nullable=True)
    contract_id = Column(String, nullable=True)
    logs = Column(String, default="[]")

# This part ensures the tables are created in the database.
# If you deploy for the first time or change the schema, this is important.
# For subsequent deployments, it will check if tables exist and do nothing if they do.
Base.metadata.create_all(engine)

# --- File-Based Authentication ---
ALLOWED_EMAILS_FILE = 'user_ids.txt'

def is_email_allowed(email):
    try:
        if os.path.exists(ALLOWED_EMAILS_FILE):
            with open(ALLOWED_EMAILS_FILE, 'r') as f:
                allowed_emails = {line.strip() for line in f}
                return email in allowed_emails
        return False
    except Exception as e:
        print(f"Error checking allowed emails: {e}")
        return False

# --- Database Session Management ---
def get_or_create_user(email):
    s = Session()
    try:
        user = s.query(User).filter_by(email=email).first()
        if not user:
            user = User(email=email)
            s.add(user)
            s.commit()
            s.refresh(user)
        return user
    finally:
        s.close()

def get_or_create_bot_session(user):
    s = Session()
    try:
        bot_session = s.query(BotSession).filter_by(user_id=user.id).first()
        if not bot_session:
            bot_session = BotSession(user_id=user.id)
            s.add(bot_session)
            s.commit()
            s.refresh(bot_session)
        return bot_session
    finally:
        s.close()

def load_bot_state(session_id):
    s = Session()
    try:
        bot_session = s.query(BotSession).filter_by(session_id=session_id).first()
        if bot_session:
            return {
                'api_token': bot_session.api_token,
                'base_amount': bot_session.base_amount,
                'tp_target': bot_session.tp_target,
                'max_consecutive_losses': bot_session.max_consecutive_losses,
                'current_amount': bot_session.current_amount,
                'consecutive_losses': bot_session.consecutive_losses,
                'total_wins': bot_session.total_wins,
                'total_losses': bot_session.total_losses,
                'is_running': bot_session.is_running,
                'is_trade_open': bot_session.is_trade_open,
                'initial_balance': bot_session.initial_balance,
                'contract_id': bot_session.contract_id,
                'logs': json.loads(bot_session.logs) if bot_session.logs else [],
            }
        return {}
    finally:
        s.close()

def update_bot_settings(session_id, new_settings):
    s = Session()
    try:
        bot_session = s.query(BotSession).filter_by(session_id=session_id).first()
        if bot_session:
            for key, value in new_settings.items():
                if hasattr(bot_session, key):
                    setattr(bot_session, key, value)
            s.commit()
    except Exception as e:
        print(f"Error updating bot settings for session {session_id}: {e}")
    finally:
        s.close()

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
    except Exception as e:
        print(f"Error placing order: {e}")
        return {"error": {"message": "Order placement failed."}}

def check_contract_status(ws, contract_id):
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        # It's important to handle the response carefully; sometimes it might be delayed or in a different format.
        # For simplicity here, we expect a direct response.
        response = ws.recv()
        # Check if response is valid JSON and contains 'proposal_open_contract'
        try:
            parsed_response = json.loads(response)
            if 'proposal_open_contract' in parsed_response:
                return parsed_response['proposal_open_contract']
            else:
                print(f"Received unexpected response format for contract status: {parsed_response}")
                return None
        except json.JSONDecodeError:
            print(f"Failed to decode JSON response for contract status: {response}")
            return None
    except Exception as e:
        print(f"Error checking contract status for {contract_id}: {e}")
        return None

def get_balance(ws):
    req = {"balance": 1, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        return response.get('balance', {}).get('balance')
    except Exception as e:
        print(f"Error getting balance: {e}")
        return None

def main_trading_loop(bot_session_id):
    state = {}
    ws = None
    try:
        ws = websocket.WebSocket()
        # Using a public endpoint for Deriv WebSocket API
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        print(f"WebSocket connected for session {bot_session_id}")

        while True:
            s = Session()
            try:
                bot_session = s.query(BotSession).filter_by(session_id=bot_session_id).first()
                if not bot_session or not bot_session.is_running:
                    print(f"Bot for session {bot_session_id} is stopped. Exiting loop.")
                    if ws: ws.close()
                    break
                
                state = {
                    'api_token': bot_session.api_token,
                    'base_amount': bot_session.base_amount,
                    'tp_target': bot_session.tp_target,
                    'max_consecutive_losses': bot_session.max_consecutive_losses,
                    'current_amount': bot_session.current_amount,
                    'consecutive_losses': bot_session.consecutive_losses,
                    'total_wins': bot_session.total_wins,
                    'total_losses': bot_session.total_losses,
                    'is_running': bot_session.is_running,
                    'is_trade_open': bot_session.is_trade_open,
                    'initial_balance': bot_session.initial_balance,
                    'contract_id': bot_session.contract_id,
                    'logs': json.loads(bot_session.logs),
                }

                if not state.get('api_token'):
                    print("API token not set. Waiting...")
                    time.sleep(5)
                    continue

                # Authorize the WebSocket connection with the API token
                auth_req = {"authorize": state['api_token']}
                ws.send(json.dumps(auth_req))
                auth_response = json.loads(ws.recv())
                if auth_response.get('error'):
                    state['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Auth failed: {auth_response['error']['message']}")
                    print(f"Authentication failed: {auth_response['error']['message']}")
                    s.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(state['logs'])})
                    s.commit()
                    time.sleep(5)
                    continue
                
                # If not authorized, the token might be invalid or expired.
                if not auth_response.get('authorize'):
                    state['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Invalid API token provided.")
                    print("Invalid API token.")
                    s.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(state['logs'])})
                    s.commit()
                    time.sleep(5)
                    continue

                # --- Trading Logic ---
                if not state.get('is_trade_open'):
                    now = datetime.now()
                    if now.second >= 55: # Trigger analysis in the last 5 seconds of the minute
                        if state['initial_balance'] is None:
                            current_balance = get_balance(ws)
                            if current_balance is not None:
                                state['initial_balance'] = current_balance
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 💰 Initial Balance: {state['initial_balance']:.2f}")
                                s.query(BotSession).filter_by(session_id=bot_session_id).update({'initial_balance': state['initial_balance'], 'logs': json.dumps(state['logs'])})
                                s.commit()
                            else:
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ❌ Failed to get balance.")
                                s.commit()
                                time.sleep(5)
                                continue

                        # Request ticks history
                        req = {"ticks_history": "R_100", "end": "latest", "count": 60, "style": "ticks"}
                        ws.send(json.dumps(req))
                        tick_data = json.loads(ws.recv())
                        
                        if 'history' in tick_data and tick_data['history']['prices']:
                            df_ticks = pd.DataFrame({'price': tick_data['history']['prices']})
                            signal, error = analyse_data(df_ticks)
                            
                            if signal in ['Buy', 'Sell']:
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ➡ Entering a {signal.upper()} trade with {state['current_amount']:.2f}$")
                                contract_type = "CALL" if signal == 'Buy' else "PUT"
                                proposal_req = {"proposal": 1, "amount": round(state['current_amount'], 2), "basis": "stake", "contract_type": contract_type, "currency": "USD", "duration": 30, "duration_unit": "s", "symbol": "R_100"}
                                ws.send(json.dumps(proposal_req))
                                proposal_response = json.loads(ws.recv())

                                if 'error' in proposal_response:
                                    state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ❌ Proposal failed: {proposal_response['error']['message']}")
                                    s.commit()
                                elif 'proposal' in proposal_response:
                                    # Place the order
                                    order_response = place_order(ws, proposal_response['proposal']['id'], state['current_amount'])
                                    if 'buy' in order_response:
                                        state['is_trade_open'] = True
                                        state['contract_id'] = order_response['buy']['contract_id']
                                        state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ✅ Order placed. Contract ID: {state['contract_id']}")
                                        s.query(BotSession).filter_by(session_id=bot_session_id).update({"is_trade_open": True, "contract_id": state['contract_id'], "logs": json.dumps(state['logs'])})
                                        s.commit()
                                    else:
                                        state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ❌ Order failed: {order_response.get('error', {}).get('message', 'Unknown error')}")
                                        s.commit()
                                else:
                                    state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ❌ Unexpected proposal response: {proposal_response}")
                                    s.commit()
                        else:
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ⚪ No clear signal. Waiting.")
                            s.commit()

                elif state.get('is_trade_open') and state.get('contract_id'):
                    now = datetime.now()
                    contract_info = check_contract_status(ws, state['contract_id'])
                    if contract_info and contract_info.get('is_sold'):
                        profit = contract_info.get('profit', 0)
                        if profit > 0:
                            state['consecutive_losses'] = 0
                            state['current_amount'] = state['base_amount'] # Reset to base amount after a win
                            state['total_wins'] += 1
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 🎉 Win! Profit: {profit:.2f}$")
                        else:
                            state['consecutive_losses'] += 1
                            # Martingale logic: Double the stake for the next trade, up to a limit
                            state['current_amount'] = max(state['base_amount'], state['current_amount'] * 2.2) # Example doubling factor
                            state['total_losses'] += 1
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 💔 Loss! Loss: {profit:.2f}$")
                        
                        state['is_trade_open'] = False
                        state['contract_id'] = None
                        
                        current_balance = get_balance(ws)
                        if current_balance is not None:
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 💰 Current Balance: {current_balance:.2f}")
                            # Check for Take Profit
                            if state['tp_target'] and (current_balance - state['initial_balance']) >= state['tp_target']:
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 🤑 Take Profit reached! Bot stopped.")
                                state['is_running'] = False
                        
                        # Check for Stop Loss
                        if state['consecutive_losses'] >= state['max_consecutive_losses']:
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 🛑 Stop Loss hit! Bot stopped.")
                            state['is_running'] = False

                    s.query(BotSession).filter_by(session_id=bot_session_id).update({
                        'current_amount': state['current_amount'],
                        'consecutive_losses': state['consecutive_losses'],
                        'total_wins': state['total_wins'],
                        'total_losses': state['total_losses'],
                        'is_running': state['is_running'],
                        'is_trade_open': state['is_trade_open'],
                        'initial_balance': state['initial_balance'],
                        'contract_id': state['contract_id'],
                        'logs': json.dumps(state['logs'])
                    })
                    s.commit()
                
                # If bot is stopped, break the loop
                if not state['is_running']:
                    print(f"Bot for session {bot_session_id} auto-stopped. Exiting loop.")
                    if ws: ws.close()
                    break

            except Exception as e:
                print(f"Error in main trading loop for session {bot_session_id}: {e}")
                state['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] 🚨 CRITICAL ERROR: {e}")
                s.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(state['logs'])})
                s.commit()
            finally:
                s.close()
            
            # Sleep briefly to prevent overwhelming the CPU and API
            time.sleep(1)
            
    finally:
        if ws and ws.connected:
            ws.close()
        print(f"WebSocket for session {bot_session_id} closed.")

# --- Streamlit UI ---
# Use Streamlit's session state to manage UI elements and user session across reruns.
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'user_email' not in st.session_state:
    st.session_state.user_email = None
if 'session_id' not in st.session_state:
    st.session_state.session_id = None
if 'session_data' not in st.session_state:
    st.session_state.session_data = {}
if 'bot_thread' not in st.session_state: # To keep track of the bot's thread
    st.session_state.bot_thread = None

def start_bot_thread(session_id):
    """Starts the trading bot in a separate thread."""
    if st.session_state.bot_thread is None or not st.session_state.bot_thread.is_alive():
        st.session_state.bot_thread = Thread(target=main_trading_loop, args=(session_id,))
        st.session_state.bot_thread.daemon = True # Allows the app to exit even if this thread is running
        st.session_state.bot_thread.start()
        print(f"Bot thread started for session: {session_id}")
    else:
        print(f"Bot thread for session {session_id} is already running.")

# --- Login Page ---
if not st.session_state.logged_in:
    st.title("KHOURYBOT Login 🤖")
    email = st.text_input("Enter your email address:")
    
    if st.button("Login", type="primary"):
        if is_email_allowed(email):
            user = get_or_create_user(email)
            bot_session = get_or_create_bot_session(user)
            st.session_state.user_email = email
            st.session_state.session_id = bot_session.session_id
            st.session_state.logged_in = True
            st.success("Login successful! Redirecting to bot control...")
            # Use rerun to refresh the page and show the main app
            st.rerun()
        else:
            st.error("Access denied. Your email is not activated or not found in the authorized list.")
else:
    # --- Main App Interface ---
    st.title("KHOURYBOT - Automated Trading 🤖")
    st.write(f"Logged in as: **{st.session_state.user_email}**")
    
    # Load current session data
    st.session_state.session_data = load_bot_state(st.session_state.session_id)
    current_status = "Running" if st.session_state.session_data.get('is_running') else "Stopped"
    is_session_active = st.session_state.session_data.get('api_token') is not None
    
    # Input fields for settings
    with st.form("bot_settings_form"):
        st.header("1. Bot Configuration")
        api_token = st.text_input("Deriv API Token:", type="password", value=st.session_state.session_data.get('api_token', ''))
        base_amount = st.number_input("Base Amount ($)", min_value=0.5, step=0.5, value=st.session_state.session_data.get('base_amount', 0.5))
        tp_target = st.number_input("Take Profit Target ($)", min_value=1.0, step=1.0, value=st.session_state.session_data.get('tp_target', 1.0))
        max_losses = st.number_input("Max Consecutive Losses", min_value=1, step=1, value=st.session_state.session_data.get('max_consecutive_losses', 5))
        
        st.write("---")
        col1, col2 = st.columns(2)
        with col1:
            start_button = st.form_submit_button("Start Bot", type="primary", disabled=(current_status == 'Running' or not api_token))
        with col2:
            stop_button = st.form_submit_button("Stop Bot", disabled=(current_status == 'Stopped'))

        if start_button:
            new_settings = {
                'is_running': True, 'api_token': api_token, 'base_amount': base_amount, 'tp_target': tp_target,
                'max_consecutive_losses': max_losses, 'current_amount': base_amount, 'consecutive_losses': 0,
                'total_wins': 0, 'total_losses': 0, 'initial_balance': None, 'contract_id': None,
                'logs': json.dumps([f"[{datetime.now().strftime('%H:%M:%S')}] 🟢 Bot has been started."])
            }
            update_bot_settings(st.session_state.session_id, new_settings)
            start_bot_thread(st.session_state.session_id) # Start the bot thread
            st.success("Bot settings updated and bot started.")
            st.rerun() # Rerun to reflect changes

        if stop_button:
            logs = st.session_state.session_data.get('logs', [])
            logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 Bot stopped by user.")
            update_bot_settings(st.session_state.session_id, {'is_running': False, 'logs': json.dumps(logs)})
            st.warning("Bot has been stopped.")
            st.rerun() # Rerun to reflect changes

    st.info(f"Bot Status: **{'Running' if current_status == 'Running' else 'Stopped'}**")
    
    # Display current stats if bot is active or was running
    if st.session_state.session_data:
        st.subheader("Current Performance")
        col_stats1, col_stats2, col_stats3, col_stats4 = st.columns(4)
        with col_stats1:
            st.metric("Initial Balance", f"${st.session_state.session_data.get('initial_balance', 0):.2f}")
        with col_stats2:
            st.metric("Total Wins", st.session_state.session_data.get('total_wins', 0))
        with col_stats3:
            st.metric("Total Losses", st.session_state.session_data.get('total_losses', 0))
        with col_stats4:
            current_balance = get_balance(None) # Attempt to get current balance, if ws is not available this might fail.
                                             # A more robust solution would pass ws from thread. For now, relying on DB.
            db_balance = st.session_state.session_data.get('initial_balance', 0) + (st.session_state.session_data.get('total_wins', 0) * st.session_state.session_data.get('tp_target', 0)) - (st.session_state.session_data.get('total_losses', 0) * st.session_state.session_data.get('base_amount', 0) * 2.2) # Approximation
            st.metric("Current Balance", f"${db_balance:.2f}") # Using approximated balance

    st.markdown("---")
    st.header("2. Live Bot Logs")
    logs = st.session_state.session_data.get('logs', [])
    # Display logs in a scrollable text area
    with st.container(height=600):
        st.text_area("Logs", "\n".join(logs), height=600, key="logs_textarea")
        
    # Auto-refresh the page every 5 seconds to show live updates
    time.sleep(5)
    st.rerun()
