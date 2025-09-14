import streamlit as st
import time
import websocket
import json
import os
import threading
import decimal
import sqlite3
import pandas as pd
from datetime import datetime

# --- SQLite Database Configuration ---
DB_FILE = "trading_data999.db"
trading_lock = threading.Lock()

# --- Database & Utility Functions ---
def create_connection():
    """Create a database connection to the SQLite database specified by DB_FILE"""
    try:
        conn = sqlite3.connect(DB_FILE)
        return conn
    except sqlite3.Error as e:
        st.error(f"❌ Database connection error: {e}")
        return None

def create_table_if_not_exists():
    """Create the sessions table if it does not exist and add new columns."""
    conn = create_connection()
    if conn:
        try:
            sql_create_sessions_table = """
            CREATE TABLE IF NOT EXISTS sessions (
                email TEXT PRIMARY KEY,
                user_token TEXT NOT NULL,
                base_amount REAL NOT NULL,
                tp_target REAL NOT NULL,
                max_consecutive_losses INTEGER NOT NULL,
                total_wins INTEGER DEFAULT 0,
                total_losses INTEGER DEFAULT 0,
                current_amount REAL NOT NULL,
                consecutive_losses INTEGER DEFAULT 0,
                initial_balance REAL DEFAULT 0.0,
                contract_id TEXT,
                trade_start_time REAL DEFAULT 0.0,
                is_running INTEGER DEFAULT 0
            );
            """
            conn.execute(sql_create_sessions_table)
            
            cursor = conn.execute("PRAGMA table_info(sessions)")
            columns = [col[1] for col in cursor.fetchall()]
            if 'is_running' not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN is_running INTEGER DEFAULT 0")
            
            conn.commit()
            print("✅ 'sessions' table checked/created/updated successfully.")
        except sqlite3.Error as e:
            st.error(f"❌ Error creating/updating table: {e}")
        finally:
            conn.close()

def is_user_active(email):
    """Checks if a user's email exists in the user_ids.txt file."""
    try:
        with open("user_ids.txt", "r") as file:
            active_users = [line.strip() for line in file.readlines()]
        return email in active_users
    except FileNotFoundError:
        st.error("❌ Error: 'user_ids.txt' file not found.")
        return False
    except Exception as e:
        st.error(f"❌ An error occurred while reading 'user_ids.txt': {e}")
        return False

def start_new_session_in_db(email, settings):
    """Saves or updates user settings and initializes session data in the database."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO sessions 
                    (email, user_token, base_amount, tp_target, max_consecutive_losses, current_amount, is_running)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                """, (email, settings["user_token"], settings["base_amount"], settings["tp_target"], settings["max_consecutive_losses"], settings["base_amount"]))
            print(f"✅ Session for {email} saved to database and bot status set to running.")
        except sqlite3.Error as e:
            st.error(f"❌ Error saving session to database: {e}")
        finally:
            conn.close()

def update_is_running_status(email, status):
    """Updates the is_running status for a specific user."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.execute("UPDATE sessions SET is_running = ? WHERE email = ?", (status, email))
            print(f"✅ Bot status for {email} updated to {'running' if status == 1 else 'stopped'}.")
        except sqlite3.Error as e:
            st.error(f"❌ Error updating bot status for {email}: {e}")
        finally:
            conn.close()

def clear_session_data(email):
    """Deletes a user's session data from the database."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.execute("DELETE FROM sessions WHERE email=?", (email,))
            print(f"✅ Session for {email} deleted successfully.")
        except sqlite3.Error as e:
            st.error(f"❌ Error deleting session from database: {e}")
        finally:
            conn.close()

def get_session_status_from_db(email):
    """Retrieves the current session status for a given email from the database."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM sessions WHERE email=?", (email,))
                row = cursor.fetchone()
                if row:
                    return dict(row)
                return None
        except sqlite3.Error as e:
            st.error(f"❌ Error fetching session from database: {e}")
            return None
        finally:
            conn.close()

def get_all_active_sessions():
    """Fetches all currently active trading sessions from the database."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM sessions WHERE is_running = 1")
                rows = cursor.fetchall()
                sessions = []
                for row in rows:
                    sessions.append(dict(row))
                return sessions
        except sqlite3.Error as e:
            st.error(f"❌ Error fetching all sessions from database: {e}")
            return []
        finally:
            conn.close()

def update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=None, contract_id=None, trade_start_time=None):
    """Updates trading statistics and trade information for a user in the database."""
    conn = create_connection()
    if conn:
        try:
            with conn:
                update_query = """
                UPDATE sessions SET 
                    total_wins = ?, total_losses = ?, current_amount = ?, consecutive_losses = ?, 
                    initial_balance = COALESCE(?, initial_balance), contract_id = ?, trade_start_time = COALESCE(?, trade_start_time)
                WHERE email = ?
                """
                conn.execute(update_query, (total_wins, total_losses, current_amount, consecutive_losses, initial_balance, contract_id, trade_start_time, email))
            print(f"✅ Stats for {email} updated successfully.")
        except sqlite3.Error as e:
            st.error(f"❌ Error updating session in database: {e}")
        finally:
            conn.close()

# --- WebSocket Helper Functions ---
def connect_websocket(user_token):
    """Establishes a WebSocket connection and authenticates the user."""
    ws = websocket.WebSocket()
    try:
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929")
        auth_req = {"authorize": user_token}
        ws.send(json.dumps(auth_req))
        auth_response = json.loads(ws.recv())
        if auth_response.get('error'):
            print(f"❌ Authentication failed: {auth_response['error']['message']}")
            ws.close()
            return None
        return ws
    except Exception as e:
        print(f"❌ WebSocket connection or authentication failed: {e}")
        return None

def get_balance_and_currency(user_token):
    """Fetches the user's current balance and currency using WebSocket."""
    ws = None
    try:
        ws = connect_websocket(user_token)
        if not ws:
            return None, None
        balance_req = {"balance": 1}
        ws.send(json.dumps(balance_req))
        balance_response = json.loads(ws.recv())
        if balance_response.get('msg_type') == 'balance':
            balance_info = balance_response.get('balance', {})
            return balance_info.get('balance'), balance_info.get('currency')
        return None, None
    except Exception as e:
        print(f"❌ Error fetching balance: {e}")
        return None, None
    finally:
        if ws and ws.connected:
            ws.close()
            
def check_contract_status(ws, contract_id):
    """Checks the status of an open contract."""
    if not ws or not ws.connected:
        return None
    req = {"proposal_open_contract": 1, "contract_id": contract_id}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        return response.get('proposal_open_contract')
    except Exception as e:
        print(f"❌ Error checking contract status: {e}")
        return None

def place_order(ws, proposal_id, amount):
    """Places a trade order on Deriv."""
    if not ws or not ws.connected:
        return {"error": {"message": "WebSocket not connected."}}
    amount_decimal = decimal.Decimal(str(amount)).quantize(decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_UP)
    req = {"buy": proposal_id, "price": float(amount_decimal)}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        return response
    except Exception as e:
        print(f"❌ Error placing order: {e}")
        return {"error": {"message": "Order placement failed."}}

# --- Trading Bot Logic ---
def analyse_data(df_ticks):
    """Analyzes tick data to generate a trading signal (Buy, Sell, Neutral)."""
    if len(df_ticks) < 5:
        return "Neutral", "Insufficient data."
    last_5_ticks = df_ticks.tail(5).copy()
    open_5_ticks = last_5_ticks['price'].iloc[0]
    close_5_ticks = last_5_ticks['price'].iloc[-1]
    if close_5_ticks > open_5_ticks:
        return "Buy", None
    elif close_5_ticks < open_5_ticks:
        return "Sell", None
    else:
        return "Neutral", "No clear signal."

def run_trading_job_for_user(session_data, check_only=False):
    """Executes the trading logic for a specific user's session."""
    email = session_data['email']
    user_token = session_data['user_token']
    base_amount = session_data['base_amount']
    tp_target = session_data['tp_target']
    max_consecutive_losses = session_data['max_consecutive_losses']
    total_wins = session_data['total_wins']
    total_losses = session_data['total_losses']
    current_amount = session_data['current_amount']
    consecutive_losses = session_data['consecutive_losses']
    initial_balance = session_data['initial_balance']
    contract_id = session_data['contract_id']
    
    if check_only:
        ws = None
        try:
            ws = connect_websocket(user_token)
            if not ws: return
            contract_info = check_contract_status(ws, contract_id)
            if contract_info and contract_info.get('is_sold'):
                profit = float(contract_info.get('profit', 0))
                
                if profit > 0:
                    print(f"🎉 User {email}: Trade won! Profit: ${profit:.2f}")
                    consecutive_losses = 0
                    total_wins += 1
                    current_amount = base_amount
                elif profit < 0:
                    print(f"🔴 User {email}: Trade lost. Loss: ${profit:.2f}")
                    consecutive_losses += 1
                    total_losses += 1
                    next_bet = float(current_amount) * 2.2 
                    current_amount = max(base_amount, next_bet)
                else: 
                    print(f"➖ User {email}: Trade was a tie. Amount remains ${current_amount:.2f}")
                    consecutive_losses = 0
                
                contract_id = None
                trade_start_time = 0.0
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=contract_id, trade_start_time=trade_start_time)

                new_balance, _ = get_balance_and_currency(user_token)
                if new_balance is not None and (float(new_balance) - float(initial_balance)) >= float(tp_target):
                    print(f"🎉 User {email}: TP target (${tp_target}) reached. Stopping the bot and clearing data.")
                    update_is_running_status(email, 0)
                    clear_session_data(email) # <-- ADDED
                    return
                
                if consecutive_losses >= max_consecutive_losses:
                    print(f"🔴 User {email}: Max consecutive losses ({max_consecutive_losses}) reached. Stopping the bot and clearing data.")
                    update_is_running_status(email, 0)
                    clear_session_data(email) # <-- ADDED
                    return
            else:
                print(f"User {email}: Contract {contract_id} is still pending. Retrying next cycle.")
        except Exception as e:
            print(f"\n❌ An unexpected error occurred while processing pending contract for user {email}: {e}")
        finally:
            if ws and ws.connected:
                ws.close()
    
    elif not check_only:
        with trading_lock:
            ws = None
            try:
                ws = connect_websocket(user_token)
                if not ws: return
                balance, currency = get_balance_and_currency(user_token)
                if balance is None:
                    print(f"❌ Failed to fetch balance for user {email}. Skipping trade job.")
                    return
                if initial_balance == 0:
                    initial_balance = float(balance)
                    update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=contract_id)
                req = {"ticks_history": "R_100", "end": "latest", "count": 5, "style": "ticks"}
                ws.send(json.dumps(req))
                tick_data = None
                while not tick_data:
                    response = json.loads(ws.recv())
                    if response.get('msg_type') == 'history':
                        tick_data = response; break
                if 'history' in tick_data and 'prices' in tick_data['history']:
                    ticks = tick_data['history']['prices']
                    df_ticks = pd.DataFrame({'price': ticks})
                    signal, _ = analyse_data(df_ticks)
                    if signal in ['Buy', 'Sell']:
                        contract_type = "CALL" if signal == 'Buy' else "PUT"
                        amount_rounded = round(float(current_amount), 2)
                        proposal_req = {
                            "proposal": 1, "amount": amount_rounded, "basis": "stake",
                            "contract_type": contract_type, "currency": currency,
                            "duration": 15, "duration_unit": "s", "symbol": "R_100"
                        }
                        ws.send(json.dumps(proposal_req))
                        proposal_response = json.loads(ws.recv())
                        if 'proposal' in proposal_response:
                            proposal_id = proposal_response['proposal']['id']
                            order_response = place_order(ws, proposal_id, float(current_amount))
                            if 'buy' in order_response and 'contract_id' in order_response['buy']:
                                contract_id = order_response['buy']['contract_id']
                                trade_start_time = time.time()
                                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=contract_id, trade_start_time=trade_start_time)
                                print(f"✅ User {email}: New trade placed successfully. Type: {contract_type}, Amount: {current_amount}")
                            else:
                                print(f"❌ User {email}: Failed to place order. Response: {order_response}")
                        else:
                            print(f"❌ User {email}: Failed to get proposal. Response: {proposal_response}")
                else:
                    print(f"❌ User {email}: Failed to get tick data.")
            except Exception as e:
                print(f"\n❌ An unexpected error occurred in the trading job for user {email}: {e}")
            finally:
                if ws and ws.connected:
                    ws.close()

def bot_loop():
    """Main loop that orchestrates trading jobs for all active sessions."""
    print("🤖 Starting main bot loop...")
    while True:
        try:
            now = datetime.now()
            # The bot will only fetch sessions where is_running = 1
            active_sessions = get_all_active_sessions()
            
            # This is the key logic: only proceed if there are active sessions
            if active_sessions:
                for session in active_sessions:
                    email = session['email']
                    
                    latest_session_data = get_session_status_from_db(email)
                    if not latest_session_data or latest_session_data.get('is_running') == 0:
                        continue
                    
                    contract_id = latest_session_data.get('contract_id')
                    trade_start_time = latest_session_data.get('trade_start_time')
                    
                    if contract_id:
                        if (time.time() - trade_start_time) >= 20: 
                            run_trading_job_for_user(latest_session_data, check_only=True)
                    
                    elif now.second == 58:
                        re_checked_session_data = get_session_status_from_db(email)
                        if re_checked_session_data and not re_checked_session_data.get('contract_id'):
                            run_trading_job_for_user(re_checked_session_data, check_only=False)
            
            # The bot will sleep regardless of active sessions, ensuring it doesn't overload
            time.sleep(1) 
        except Exception as e:
            print(f"❌ حدث خطأ غير متوقع في الحلقة الرئيسية: {e}")
            time.sleep(5)

# --- Streamlit App Configuration ---
st.set_page_config(page_title="Khoury Bot", layout="wide")
st.title("Khoury Bot")

# --- Initialize Session State ---
# This block must be at the top of the script
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user_email" not in st.session_state:
    st.session_state.user_email = ""
if "stats" not in st.session_state:
    st.session_state.stats = None
    
# Call this at the start to ensure the database is ready
create_table_if_not_exists()

# --- Start Background Bot Thread ---
# This part is still necessary to start the thread, but the logic inside the thread is what matters
if "bot_thread" not in st.session_state:
    bot_thread = threading.Thread(target=bot_loop, daemon=True)
    bot_thread.start()
    st.session_state.bot_thread = bot_thread
    print("Bot thread started.")

# --- Login Section ---
if not st.session_state.logged_in:
    st.markdown("---")
    st.subheader("Login")
    login_form = st.form("login_form")
    email_input = login_form.text_input("Email")
    submit_button = login_form.form_submit_button("Login")
    
    if submit_button:
        if is_user_active(email_input):
            st.session_state.logged_in = True
            st.session_state.user_email = email_input
            st.rerun()
        else:
            st.error("❌ This email is not active. Please contact the administrator.")

# --- Main Application Section (after login) ---
if st.session_state.logged_in:
    st.markdown("---")
    st.subheader(f"Welcome, {st.session_state.user_email}")
    
    stats_data = get_session_status_from_db(st.session_state.user_email)
    st.session_state.stats = stats_data
    
    is_user_bot_running = False
    if st.session_state.stats:
        is_user_bot_running = st.session_state.stats.get('is_running', 0) == 1
    
    with st.form("settings_and_control"):
        st.subheader("Bot Settings and Control")
        user_token_val = ""
        base_amount_val = 0.5
        tp_target_val = 20.0
        max_consecutive_losses_val = 5
        
        if st.session_state.stats:
            user_token_val = st.session_state.stats['user_token']
            base_amount_val = st.session_state.stats['base_amount']
            tp_target_val = st.session_state.stats['tp_target']
            max_consecutive_losses_val = st.session_state.stats['max_consecutive_losses']
        
        user_token = st.text_input("Deriv API Token", type="password", value=user_token_val, disabled=is_user_bot_running)
        base_amount = st.number_input("Base Bet Amount", min_value=0.5, value=base_amount_val, step=0.1, disabled=is_user_bot_running)
        tp_target = st.number_input("Take Profit Target", min_value=10.0, value=tp_target_val, step=5.0, disabled=is_user_bot_running)
        max_consecutive_losses = st.number_input("Max Consecutive Losses", min_value=1, value=max_consecutive_losses_val, step=1, disabled=is_user_bot_running)
        
        col_start, col_stop = st.columns(2)
        with col_start:
            start_button = st.form_submit_button("Start Bot", disabled=is_user_bot_running)
        with col_stop:
            stop_button = st.form_submit_button("Stop Bot", disabled=not is_user_bot_running)
    
    if start_button:
        if not user_token:
            st.error("Please enter a Deriv API Token to start the bot.")
        else:
            settings = {
                "user_token": user_token,
                "base_amount": base_amount,
                "tp_target": tp_target,
                "max_consecutive_losses": max_consecutive_losses
            }
            start_new_session_in_db(st.session_state.user_email, settings)
            st.success("✅ Bot started successfully! Please wait for the stats to update.")
            st.rerun()

    if stop_button:
        update_is_running_status(st.session_state.user_email, 0)
        st.info("⏸ The bot has been stopped.")
        st.session_state.stats = None
        st.rerun()

    st.markdown("---")
    st.subheader("Statistics")

    stats_placeholder = st.empty()
    
    if is_user_bot_running:
        st.success("🟢 Your bot is *RUNNING*.")
    else:
        st.error("🔴 Your bot is *STOPPED*.")

    if st.session_state.user_email:
        session_data = get_session_status_from_db(st.session_state.user_email)
        if session_data:
            user_token = session_data['user_token']
            balance, _ = get_balance_and_currency(user_token)
            if balance is not None:
                st.metric(label="Current Balance", value=f"${float(balance):.2f}")

    if st.session_state.stats:
        with stats_placeholder.container():
            stats = st.session_state.stats
            col1, col2, col3, col4, col5 = st.columns(5)
            with col1:
                st.metric(label="Current Amount", value=f"${stats['current_amount']:.2f}")
            with col2:
                st.metric(label="Profit Target", value=f"${stats['tp_target']:.2f}")
            with col3:
                st.metric(label="Total Wins", value=stats['total_wins'])
            with col4:
                st.metric(label="Total Losses", value=stats['total_losses'])
            with col5:
                st.metric(label="Consecutive Losses", value=stats['consecutive_losses'])
            
            if stats['contract_id']:
                st.warning("⚠ A trade is pending. Stats will be updated after it's completed.")
    else:
        with stats_placeholder.container():
            st.info("The bot is currently stopped.")
            
    time.sleep(1)
    st.rerun()
