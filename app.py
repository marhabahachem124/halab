import streamlit as st
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.engine.url import URL
import time
import websocket
import json
import os
from datetime import datetime

# --- PostgreSQL Database Configuration ---
# رابط قاعدة البيانات
DATABASE_URL = "postgresql://hesba_user:EAMYdltUnfFJTz46ccq9ZoCgIU4k1Jib@dpg-d33e7mumcj7s73aail50-a/hesba"

if not DATABASE_URL:
    st.error("❌ DATABASE_URL is not configured. Please set it in the code.")
    st.stop()

# Reformat the URL for SQLAlchemy
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://")

# Create a connection engine
try:
    engine = create_engine(DATABASE_URL)
except Exception as e:
    st.error(f"❌ Error connecting to the database: {e}")
    st.stop()

# --- Database & Utility Functions ---
def create_table_if_not_exists():
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("""
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
            """))
            conn.commit()
        print("✅ 'sessions' table checked/created/updated successfully.")
    except Exception as e:
        st.error(f"❌ Error creating/updating table: {e}")

def is_user_active(email):
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
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("""
                INSERT INTO sessions 
                (email, user_token, base_amount, tp_target, max_consecutive_losses, current_amount, is_running)
                VALUES (:email, :user_token, :base_amount, :tp_target, :max_consecutive_losses, :current_amount, 1)
                ON CONFLICT(email) DO UPDATE SET
                user_token = excluded.user_token,
                base_amount = excluded.base_amount,
                tp_target = excluded.tp_target,
                max_consecutive_losses = excluded.max_consecutive_losses,
                current_amount = excluded.current_amount,
                is_running = 1
            """),
            {"email": email, "user_token": settings["user_token"], "base_amount": settings["base_amount"], "tp_target": settings["tp_target"], "max_consecutive_losses": settings["max_consecutive_losses"], "current_amount": settings["base_amount"]})
            conn.commit()
        print(f"✅ Session for {email} saved to database and bot status set to running.")
    except Exception as e:
        st.error(f"❌ Error saving session to database: {e}")

def update_is_running_status(email, status):
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("UPDATE sessions SET is_running = :status WHERE email = :email"),
            {"status": status, "email": email})
            conn.commit()
        print(f"✅ Bot status for {email} updated to {'running' if status == 1 else 'stopped'}.")
    except Exception as e:
        st.error(f"❌ Error updating bot status for {email}: {e}")

def get_session_status_from_db(email):
    try:
        with engine.connect() as conn:
            result = conn.execute(sa.text("SELECT * FROM sessions WHERE email=:email"), {"email": email}).fetchone()
            if result:
                return result._asdict()
            return None
    except Exception as e:
        st.error(f"❌ Error fetching session from database: {e}")
        return None

def get_balance_and_currency_streamlit(user_token):
    ws = None
    try:
        ws = websocket.WebSocket()
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929")
        auth_req = {"authorize": user_token}
        ws.send(json.dumps(auth_req))
        auth_response = json.loads(ws.recv())
        if auth_response.get('error'):
            return None, None
        balance_req = {"balance": 1}
        ws.send(json.dumps(balance_req))
        balance_response = json.loads(ws.recv())
        if balance_response.get('msg_type') == 'balance':
            balance_info = balance_response.get('balance', {})
            return balance_info.get('balance'), balance_info.get('currency')
        return None, None
    except Exception as e:
        st.error(f"❌ Error fetching balance: {e}")
        return None, None
    finally:
        if ws and ws.connected:
            ws.close()

# --- Streamlit App Configuration ---
st.set_page_config(page_title="Khoury Bot", layout="wide")
st.title("Khoury Bot")

# --- Initialize Session State ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user_email" not in st.session_state:
    st.session_state.user_email = ""
if "stats" not in st.session_state:
    st.session_state.stats = None
    
create_table_if_not_exists()

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
        st.info("⏸️ The bot has been stopped.")
        st.session_state.stats = None
        st.rerun()

    st.markdown("---")
    st.subheader("Statistics")

    stats_placeholder = st.empty()
    
    if is_user_bot_running:
        st.success("🟢 Your bot is **RUNNING**.")
    else:
        st.error("🔴 Your bot is **STOPPED**.")

    if st.session_state.user_email:
        session_data = get_session_status_from_db(st.session_state.user_email)
        if session_data:
            user_token = session_data['user_token']
            balance, _ = get_balance_and_currency_streamlit(user_token) 
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
                st.warning("⚠️ A trade is pending. Stats will be updated after it's completed.")
    else:
        with stats_placeholder.container():
            st.info("The bot is currently stopped.")
            
    time.sleep(1)
    st.rerun()
