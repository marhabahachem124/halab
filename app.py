import streamlit as st
import time
import websocket
import json
import pandas as pd
from datetime import datetime, timedelta
import psycopg2
import os
import threading
import decimal # Import decimal for type hinting if needed, though we'll convert to float

# --- Database Connection Details ---
# Using environment variable for security and flexibility
DB_URI = os.environ.get("DATABASE_URL", "postgresql://charboul_user:Nri3ODg6M9mDFu1kK71ru69FiAmKSNtY@dpg-d32pealkbo4c73alceog-a.oregon-postgres.render.com/charboul")

# --- Authentication Logic ---
def is_user_active(email):
    """Checks if a user's email exists in the user_ids.txt file."""
    try:
        with open("user_ids.txt", "r") as file:
            active_users = [line.strip() for line in file.readlines()]
            return email in active_users
    except FileNotFoundError:
        st.error("❌ Error: 'user_ids.txt' file not found. Please create it and add allowed user emails.")
        return False
    except Exception as e:
        st.error(f"❌ An error occurred while reading 'user_ids.txt': {e}")
        return False

# --- Database Functions ---
def get_db_connection():
    """Establishes a connection to the PostgreSQL database."""
    try:
        conn = psycopg2.connect(DB_URI)
        return conn
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        return None

def start_new_session_in_db(email, settings):
    """Saves or updates user settings and initializes session data in the database."""
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            try:
                # Ensure the table exists with the correct types
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS user_settings (
                        email VARCHAR(255) PRIMARY KEY,
                        user_token VARCHAR(255),
                        base_amount NUMERIC(10, 2),
                        tp_target NUMERIC(10, 2),
                        max_consecutive_losses INTEGER,
                        total_wins INTEGER DEFAULT 0,
                        total_losses INTEGER DEFAULT 0,
                        current_amount NUMERIC(10, 2),
                        consecutive_losses INTEGER DEFAULT 0,
                        initial_balance NUMERIC(10, 2) DEFAULT 0,
                        contract_id VARCHAR(255) DEFAULT NULL
                    );
                """)
                # Insert or update user settings, initializing stats and current amount
                cur.execute("""
                    INSERT INTO user_settings (email, user_token, base_amount, tp_target, max_consecutive_losses,
                                               current_amount, total_wins, total_losses, consecutive_losses, initial_balance, contract_id)
                    VALUES (%s, %s, %s, %s, %s, %s, 0, 0, 0, 0, NULL)
                    ON CONFLICT (email) DO UPDATE SET
                    user_token = EXCLUDED.user_token,
                    base_amount = EXCLUDED.base_amount,
                    tp_target = EXCLUDED.tp_target,
                    max_consecutive_losses = EXCLUDED.max_consecutive_losses,
                    current_amount = EXCLUDED.base_amount, -- Reset current amount to base amount on new start
                    total_wins = 0,
                    total_losses = 0,
                    consecutive_losses = 0,
                    initial_balance = 0,
                    contract_id = NULL
                """, (email, settings["user_token"], settings["base_amount"], settings["tp_target"],
                      settings["max_consecutive_losses"], settings["base_amount"]))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                print(f"❌ Error saving settings to database: {e}")
                conn.rollback() # Rollback in case of error
                conn.close()
                return False
    return False

def get_session_status_from_db(email):
    """Retrieves the current session status for a given email from the database."""
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT user_token, base_amount, tp_target, max_consecutive_losses, total_wins, total_losses, current_amount, consecutive_losses, initial_balance, contract_id FROM user_settings WHERE email = %s", (email,))
                result = cur.fetchone()
                conn.close()
                if result:
                    # --- IMPORTANT: Convert NUMERIC types from DB to float ---
                    return {
                        "user_token": result[0],
                        "base_amount": float(result[1]),
                        "tp_target": float(result[2]),
                        "max_consecutive_losses": int(result[3]),
                        "total_wins": int(result[4]),
                        "total_losses": int(result[5]),
                        "current_amount": float(result[6]), # Convert to float
                        "consecutive_losses": int(result[7]),
                        "initial_balance": float(result[8]), # Convert to float
                        "contract_id": result[9]
                    }
            except Exception as e:
                print(f"❌ Error fetching session status for {email}: {e}")
                if conn: conn.close()
    return None

def get_all_active_sessions():
    """Fetches all currently active trading sessions from the database."""
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT email, user_token, base_amount, tp_target, max_consecutive_losses, total_wins, total_losses, current_amount, consecutive_losses, initial_balance, contract_id FROM user_settings;")
                active_sessions = cur.fetchall()
                conn.close()
                # Convert NUMERIC to float for all retrieved records
                processed_sessions = []
                for session in active_sessions:
                    processed_session = list(session)
                    processed_session[1] = float(session[1]) if session[1] is not None else 0.0 # base_amount
                    processed_session[2] = float(session[2]) if session[2] is not None else 0.0 # tp_target
                    processed_session[6] = float(session[6]) if session[6] is not None else 0.0 # current_amount
                    processed_session[8] = float(session[8]) if session[8] is not None else 0.0 # initial_balance
                    processed_sessions.append(tuple(processed_session))
                return processed_sessions
        except Exception as e:
            print(f"❌ An error occurred while fetching active sessions: {e}")
            if conn: conn.close()
            return []
    return []

def update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=None, contract_id=None):
    """Updates trading statistics and trade information for a user in the database."""
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                # Ensure current_amount and initial_balance are treated as NUMERIC
                cur.execute("""
                    UPDATE user_settings
                    SET total_wins = %s,
                        total_losses = %s,
                        current_amount = %s,
                        consecutive_losses = %s,
                        initial_balance = COALESCE(%s, initial_balance),
                        contract_id = %s
                    WHERE email = %s
                """, (total_wins, total_losses, current_amount, consecutive_losses, initial_balance, contract_id, email))
                conn.commit()
        except Exception as e:
            print(f"❌ An error occurred while updating the database for user {email}: {e}")
            conn.rollback()
        finally:
            conn.close()

def clear_session_data(email):
    """Deletes a user's session data from the database."""
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_settings WHERE email = %s", (email,))
                conn.commit()
        except Exception as e:
            print(f"❌ An error occurred while stopping the session for user {email}: {e}")
            conn.rollback()
        finally:
            conn.close()

# --- WebSocket Helper Functions ---
def connect_websocket(user_token):
    """Establishes a WebSocket connection and authenticates the user."""
    ws = websocket.WebSocket()
    try:
        # Use a timeout for connection to prevent hanging
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        auth_req = {"authorize": user_token}
        ws.send(json.dumps(auth_req))
        # Add a timeout for receiving the auth response
        auth_response = json.loads(ws.recv_data(timeout=10))
        
        if auth_response.get('error'):
            print(f"❌ Authentication failed: {auth_response['error']['message']}")
            ws.close()
            return None
        print("✅ WebSocket connection successful and authenticated.")
        return ws
    except websocket.WebSocketTimeoutException:
        print("❌ WebSocket connection or authentication timed out.")
        if ws and ws.connected: ws.close()
        return None
    except Exception as e:
        print(f"❌ WebSocket connection or authentication failed: {e}")
        if ws and ws.connected: ws.close()
        return None

def get_balance(ws):
    """Fetches the user's current balance using WebSocket."""
    if not ws or not ws.connected:
        print("❌ Cannot get balance: WebSocket not connected.")
        return None
    
    req = {"balance": 1, "subscribe": 1} # subscribe can be useful for real-time updates, but we'll just fetch once here
    try:
        ws.send(json.dumps(req))
        # Use timeout for receiving data
        response = json.loads(ws.recv_data(timeout=10))
        
        if response.get('msg_type') == 'balance':
            balance_info = response.get('balance', {})
            # Return balance as float
            return float(balance_info.get('balance', 0.0))
        elif response.get('error'):
            print(f"❌ Error getting balance: {response['error']['message']}")
            return None
        return None # Should not happen if msg_type is balance
    except websocket.WebSocketTimeoutException:
        print("❌ Timeout waiting for balance information.")
        return None
    except Exception as e:
        print(f"❌ Error fetching balance: {e}")
        return None

def check_contract_status(ws, contract_id):
    """Checks the status of an open contract."""
    if not ws or not ws.connected:
        print("❌ Cannot check contract status: WebSocket not connected.")
        return None
        
    req = {"proposal_open_contract": 1, "contract_id": str(contract_id)} # Ensure contract_id is string
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv_data(timeout=10)) # Use timeout
        
        if response.get('msg_type') == 'proposal_open_contract':
            return response['proposal_open_contract']
        elif response.get('error'):
            print(f"❌ Error checking contract status for {contract_id}: {response['error']['message']}")
            return None
        else:
            print(f"⚠ Unexpected response type for contract status check: {response.get('msg_type')}. Response: {response}")
            return None
    except websocket.WebSocketTimeoutException:
        print(f"❌ Timeout waiting for contract info for ID {contract_id}.")
        return None
    except Exception as e:
        print(f"❌ Error checking contract status for ID {contract_id}: {e}")
        return None

def place_order(ws, proposal_id, amount):
    """Places a trade order on Deriv."""
    if not ws or not ws.connected:
        print("❌ Cannot place order: WebSocket not connected.")
        return {"error": {"message": "WebSocket not connected."}}
    
    # Use Decimal for precise rounding before converting to float for the API
    # Ensure amount is at least the minimum allowed (e.g., 0.5)
    amount_decimal = decimal.Decimal(str(amount)).quantize(decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_UP)
    valid_amount = max(decimal.Decimal('0.5'), amount_decimal)
    
    req = {"buy": proposal_id, "price": float(valid_amount)} # API expects float
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv_data(timeout=10)) # Use timeout
        return response
    except websocket.WebSocketTimeoutException:
        print("❌ Timeout waiting to place order.")
        return {"error": {"message": "Order placement timed out."}}
    except Exception as e:
        print(f"❌ Error placing order: {e}")
        return {"error": {"message": str(e)}}

# --- Trading Bot Logic ---
def analyse_data(df_ticks):
    """Analyzes tick data to generate a trading signal (Buy, Sell, Neutral)."""
    if len(df_ticks) < 5: # Needs at least a few ticks to compare
        return "Neutral", 0, 0, "Insufficient data for analysis."
    
    # Simplified analysis: compare the last tick to the first tick of the sample
    # This is a placeholder; more complex indicator logic would go here.
    # For now, we'll rely on the logic implemented in the main bot loop
    # that checks recent tick direction as well.
    
    return "Neutral", 0, 0, None # Default to neutral if no specific logic defined here

def run_trading_job_for_user(email, session_data):
    """Executes the trading logic for a specific user's session."""
    user_token = session_data["user_token"]
    base_amount = session_data["base_amount"]
    tp_target = session_data["tp_target"]
    max_consecutive_losses = session_data["max_consecutive_losses"]
    total_wins = session_data["total_wins"]
    total_losses = session_data["total_losses"]
    current_amount = session_data["current_amount"]
    consecutive_losses = session_data["consecutive_losses"]
    initial_balance = session_data["initial_balance"]
    contract_id = session_data["contract_id"]

    ws = None
    try:
        ws = connect_websocket(user_token)
        if not ws:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Failed to connect WebSocket for user {email}. Skipping job.")
            return

        # --- 1. Process Existing Contract First ---
        if contract_id:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] User {email}: Checking status of existing contract {contract_id}...")
            contract_info = check_contract_status(ws, contract_id)

            if contract_info and contract_info.get('is_sold'):
                profit = float(contract_info.get('profit', 0)) # Ensure profit is float
                
                # Update stats based on win/loss
                if profit > 0:
                    print(f"🎉 User {email}: Trade won! Profit: ${profit:.2f}")
                    consecutive_losses = 0
                    total_wins += 1
                    current_amount = base_amount # Reset bet amount after a win
                elif profit < 0:
                    print(f"🔴 User {email}: Trade lost. Loss: ${profit:.2f}")
                    consecutive_losses += 1
                    total_losses += 1
                    # --- FIX APPLIED HERE: Convert current_amount to float before multiplication ---
                    next_bet = float(current_amount) * 2.2
                    current_amount = max(base_amount, next_bet) # Ensure bet amount doesn't drop below base
                else: # Profit is 0
                    print(f"⚪ User {email}: Trade resulted in zero profit/loss.")
                    # No change in consecutive losses, amount reset, or stats
                
                # Update DB with new stats and clear contract_id
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=None)
                
                # Check for TP/SL conditions after processing the trade
                current_balance = get_balance(ws)
                if current_balance is not None:
                    if tp_target and (current_balance - initial_balance) >= tp_target:
                        print(f"🎉 User {email}: TP target (${tp_target}) reached! Stopping bot.")
                        clear_session_data(email) # Stop the bot by clearing data
                        return
                    if consecutive_losses >= max_consecutive_losses:
                        print(f"🔴 User {email}: Max consecutive losses ({max_consecutive_losses}) reached! Stopping bot.")
                        clear_session_data(email) # Stop the bot
                        return
                else:
                    print(f"❌ User {email}: Failed to get current balance after trade. Cannot check TP/SL. Continuing...")

            elif contract_info and not contract_info.get('is_sold'):
                print(f"User {email}: Contract {contract_id} is still pending or not yet sold. Waiting for next cycle.")
            else:
                print(f"❌ User {email}: Could not retrieve status for contract {contract_id} or contract not found/cancelled. Clearing contract ID.")
                # If status is unclear or not found, assume it's over and clear the ID
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=None)

        # --- 2. Place New Trade if No Contract is Pending ---
        elif not contract_id:
            # Fetch balance and set initial balance if not set
            if initial_balance == 0 or initial_balance is None:
                balance = get_balance(ws)
                if balance is not None:
                    initial_balance = float(balance) # Store as float
                    update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=None)
                    print(f"User {email}: Initial balance set to ${initial_balance:.2f}")
                else:
                    print(f"❌ User {email}: Failed to fetch balance to set initial balance. Skipping trade placement.")
                    return # Skip this cycle if balance cannot be fetched

            # Request recent tick data (e.g., 50 candles * 7 ticks/candle = 350 ticks)
            ticks_to_request = 350
            req_ticks = {"ticks_history": "R_100", "end": "latest", "count": ticks_to_request, "style": "ticks"}
            ws.send(json.dumps(req_ticks))
            tick_data_response = json.loads(ws.recv()) # No timeout here, assuming connection is stable

            if 'history' in tick_data_response and tick_data_response['history']['prices']:
                ticks = tick_data_response['history']['prices']
                timestamps = tick_data_response['history']['times']
                df_ticks = pd.DataFrame({'timestamp': timestamps, 'price': ticks})
                
                # --- Determine Trading Signal ---
                # Basic logic: check direction of last few ticks
                signal = "Neutral"
                if len(df_ticks) >= 5:
                    # Compare last tick price with first tick price in the history
                    if df_ticks['price'].iloc[-1] > df_ticks['price'].iloc[0]:
                        signal = "Buy"
                    elif df_ticks['price'].iloc[-1] < df_ticks['price'].iloc[0]:
                        signal = "Sell"
                
                if signal in ["Buy", "Sell"]:
                    print(f"User {email}: Signal detected: {signal}. Attempting to place trade with amount ${current_amount:.2f}...")
                    
                    # First, request a proposal to get the proposal ID.
                    proposal_req = {
                        "proposal": 1,
                        "amount": float(current_amount), # API expects float
                        "basis": "stake",
                        "contract_type": "CALL" if signal == 'Buy' else "PUT",
                        "currency": "USD", # Assuming USD, adjust if needed
                        "duration": 1, # 1 minute duration
                        "duration_unit": "m",
                        "symbol": "R_100", # Trading Symbol
                        "passthrough": {"action": signal} # Can be used for debugging
                    }
                    ws.send(json.dumps(proposal_req))
                    proposal_response = json.loads(ws.recv())
                    
                    if 'proposal' in proposal_response and 'id' in proposal_response['proposal']:
                        proposal_id = proposal_response['proposal']['id']
                        
                        # Now, place the order using the proposal ID.
                        order_response = place_order(ws, proposal_id, current_amount)
                        
                        if 'buy' in order_response and 'contract_id' in order_response['buy']:
                            new_contract_id = order_response['buy']['contract_id']
                            # Update DB with the new contract ID to track this trade
                            update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=new_contract_id)
                            print(f"✅ User {email}: New trade placed successfully. Contract ID: {new_contract_id}")
                        elif 'error' in order_response:
                            print(f"❌ User {email}: Failed to place order. Error: {order_response['error']['message']}")
                        else:
                            print(f"❌ User {email}: Unexpected response when placing order: {order_response}")
                    else:
                        error_msg = proposal_response.get('error', {}).get('message', 'Unknown proposal error')
                        print(f"❌ User {email}: Failed to get proposal. Error: {error_msg}")
            else:
                print(f"❌ User {email}: Failed to get tick history data or data is empty.")

    except websocket.WebSocketConnectionClosedException:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ WebSocket connection closed unexpectedly for user {email}.")
    except websocket.WebSocketTimeoutException:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ WebSocket connection timed out for user {email}.")
    except Exception as e:
        print(f"\n❌ An unexpected error occurred in the trading job for user {email}: {e}")
    finally:
        if ws and ws.connected:
            ws.close()
            print(f"🔗 WebSocket connection closed for user {email}.")

# --- Main Bot Loop ---
def bot_loop():
    """Main loop that orchestrates trading jobs for all active sessions."""
    print("🤖 Starting main bot loop...")
    while True:
        try:
            now = datetime.now()
            # Check if it's time to run the job (e.g., every minute, around the 58th second)
            # This ensures jobs don't overlap and run roughly once per minute.
            if now.second == 58:
                active_sessions = get_all_active_sessions()
                if active_sessions:
                    for session in active_sessions:
                        # session is a tuple, unpack it into a dictionary-like structure for clarity
                        session_dict = {
                            "email": session[0],
                            "user_token": session[1],
                            "base_amount": session[2],
                            "tp_target": session[3],
                            "max_consecutive_losses": session[4],
                            "total_wins": session[5],
                            "total_losses": session[6],
                            "current_amount": session[7],
                            "consecutive_losses": session[8],
                            "initial_balance": session[9],
                            "contract_id": session[10]
                        }
                        run_trading_job_for_user(session_dict["email"], session_dict)
                time.sleep(2) # Sleep a bit longer to avoid rapid re-checks
            else:
                time.sleep(0.5) # Shorter sleep for finer timing
        except Exception as e:
            print(f"❌ An error occurred in the main bot loop: {e}")
            time.sleep(5) # Wait before retrying in case of a loop error

# --- Streamlit App Configuration ---
st.set_page_config(page_title="Khoury Bot", layout="wide")
st.title("Khoury Bot - Automated Trading 🤖")

# --- Initialize Session State ---
# Authentication and License
if 'user_id_checked' not in st.session_state:
    st.session_state.user_id = None
    st.session_state.is_authenticated = False
    st.session_state.user_id_checked = False # Flag to ensure device ID check runs only once

# Bot Configuration
if 'bot_running' not in st.session_state:
    st.session_state.bot_running = False
if 'user_token' not in st.session_state:
    st.session_state.user_token = None
if 'base_amount' not in st.session_state:
    st.session_state.base_amount = 0.5 # Default minimum
if 'current_amount' not in st.session_state:
    st.session_state.current_amount = 0.5
if 'tp_target' not in st.session_state:
    st.session_state.tp_target = 20.0 # Default TP
if 'max_consecutive_losses' not in st.session_state:
    st.session_state.max_consecutive_losses = 5 # Default SL

# Trading State
if 'is_trade_open' not in st.session_state:
    st.session_state.is_trade_open = False
if 'trade_start_time' not in st.session_state:
    st.session_state.trade_start_time = None
if 'contract_id' not in st.session_state:
    st.session_state.contract_id = None
if 'initial_balance' not in st.session_state:
    st.session_state.initial_balance = 0.0 # Use float for consistency
if 'consecutive_losses' not in st.session_state:
    st.session_state.consecutive_losses = 0
if 'total_wins' not in st.session_state:
    st.session_state.total_wins = 0
if 'total_losses' not in st.session_state:
    st.session_state.total_losses = 0

# UI State
if 'page' not in st.session_state:
    st.session_state.page = 'inputs' # Default to settings page

# Background Bot Thread
if 'bot_thread_started' not in st.session_state:
    bot_thread = threading.Thread(target=bot_loop)
    bot_thread.daemon = True
    bot_thread.start()
    st.session_state.bot_thread_started = True
    print("Bot thread started.")

# --- License Check ---
if not st.session_state.user_id_checked:
    st.session_state.user_id, status = get_or_create_device_id() # Attempt to get/create device ID
    if st.session_state.user_id is None:
        st.error("❌ Could not retrieve or create a device ID. Please check database connection and logs.")
    st.session_state.user_id_checked = True # Mark as checked

if st.session_state.user_id and not is_user_active(st.session_state.user_id):
    st.warning(f"Your device ID is: **{st.session_state.user_id}**. It is not yet activated.")
    st.info("Please send this ID to the administrator for activation. The bot cannot run without activation.")
    st.stop() # Stop the app execution until activated

# --- Main App Logic (After successful authentication/activation) ---
st.session_state.is_authenticated = True # Assume authenticated if we passed the checks

# --- Display Status and Timer ---
status_placeholder = st.empty()
timer_placeholder = st.empty()

if st.session_state.bot_running:
    if not st.session_state.is_trade_open:
        status_placeholder.info("Bot is running. Analyzing market...")
        # Calculate time until the next minute starts (plus a small buffer)
        now = datetime.now()
        next_minute_start = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        seconds_until_next_minute = max(0, (next_minute_start - now).total_seconds() - 5) # 5 sec buffer
        timer_placeholder.metric("Next action in", f"{int(seconds_until_next_minute)}s")
    else:
        status_placeholder.info("Trade is open. Waiting for result...")
        timer_placeholder.empty() # Hide timer when a trade is active
else:
    status_placeholder.warning("Bot is stopped. Configure settings and press 'Start Bot'.")
    timer_placeholder.empty()

# --- UI Navigation and Controls ---
st.markdown("---")
col1, col2 = st.columns(2)

with col1:
    if st.button("⚙️ Settings", type="secondary", use_container_width=True):
        st.session_state.page = 'inputs'
with col2:
    if st.button("📜 Logs", type="secondary", use_container_width=True):
        st.session_state.page = 'logs'

# --- Settings Page ---
if st.session_state.page == 'inputs':
    st.header("Bot Configuration")
    
    # Retrieve current settings from DB if available
    user_settings = get_session_status_from_db(st.session_state.user_id)
    if user_settings:
        st.session_state.user_token = user_settings.get('user_token', st.session_state.user_token)
        st.session_state.base_amount = user_settings.get('base_amount', st.session_state.base_amount)
        st.session_state.tp_target = user_settings.get('tp_target', st.session_state.tp_target)
        st.session_state.max_consecutive_losses = user_settings.get('max_consecutive_losses', st.session_state.max_consecutive_losses)
        # Update current_amount based on DB if bot is not running
        if not st.session_state.bot_running:
            st.session_state.current_amount = st.session_state.base_amount


    st.session_state.user_token = st.text_input("Deriv API Token", type="password", value=st.session_state.user_token, key="api_token_input")
    st.session_state.base_amount = st.number_input("Base Bet Amount ($)", min_value=0.5, value=st.session_state.base_amount, step=0.1)
    st.session_state.tp_target = st.number_input("Take Profit Target ($)", min_value=1.0, value=st.session_state.tp_target, step=1.0)
    st.session_state.max_consecutive_losses = st.number_input("Max Consecutive Losses", min_value=1, value=st.session_state.max_consecutive_losses, step=1)
    
    col_start, col_stop = st.columns(2)
    with col_start:
        if st.button("▶️ Start Bot", type="primary", use_container_width=True):
            if not st.session_state.user_token:
                st.error("Please enter a valid Deriv API token to start the bot.")
            else:
                settings = {
                    "user_token": st.session_state.user_token,
                    "base_amount": st.session_state.base_amount,
                    "tp_target": st.session_state.tp_target,
                    "max_consecutive_losses": st.session_state.max_consecutive_losses
                }
                # Store settings in DB and reset stats for a fresh start
                if start_new_session_in_db(st.session_state.user_id, settings):
                    st.session_state.bot_running = True
                    st.session_state.current_amount = st.session_state.base_amount # Ensure current amount is reset
                    st.session_state.consecutive_losses = 0
                    st.session_state.total_wins = 0
                    st.session_state.total_losses = 0
                    st.session_state.initial_balance = 0.0 # Reset initial balance on start
                    st.session_state.contract_id = None
                    st.session_state.is_trade_open = False
                    st.success("Bot started successfully! Settings saved.")
                    st.rerun()
                else:
                    st.error("Failed to start the bot. Please check database connection and logs.")
    with col_stop:
        if st.button("⏹️ Stop Bot", type="secondary", use_container_width=True):
            st.session_state.bot_running = False
            st.session_state.is_trade_open = False # Ensure any open trade state is cleared
            clear_session_data(st.session_state.user_id) # Remove from DB to stop background jobs
            st.info("Bot stopped. Session data cleared.")
            st.rerun()

# --- Logs Page ---
if st.session_state.page == 'logs':
    st.header("Live Bot Logs")
    
    # Display current stats
    col_wins, col_losses, col_balance = st.columns(3)
    with col_wins:
        st.metric("Total Wins", st.session_state.total_wins)
    with col_losses:
        st.metric("Total Losses", st.session_state.total_losses)
    with col_balance:
        # Fetch live balance if bot is running and authenticated
        live_balance = "N/A"
        if st.session_state.bot_running and st.session_state.user_token:
            try:
                ws_balance = connect_websocket(st.session_state.user_token)
                if ws_balance:
                    balance_val = get_balance(ws_balance)
                    if balance_val is not None:
                        live_balance = f"${balance_val:.2f}"
                    ws_balance.close()
            except Exception as e:
                print(f"Error fetching live balance for display: {e}")
        elif st.session_state.initial_balance > 0: # Show initial balance if bot stopped but has one
            live_balance = f"${st.session_state.initial_balance:.2f} (Initial)"
            
        st.metric("Current Balance", live_balance)
        
    st.markdown("---")
    # Display logs in a scrollable text area
    log_display = st.text_area("Logs", "\n".join(st.session_state.log_records), height=500, key="logs_textarea")
    
    # Auto-scroll JavaScript
    components.html(
        """
        <script>
            var textarea = parent.document.querySelector('textarea[aria-label="Logs"]');
            if(textarea) {
                textarea.scrollTop = textarea.scrollHeight;
            }
        </script>
        """,
        height=0, width=0
    )

# Refresh the page every second to update status and logs
time.sleep(1)
st.rerun()
