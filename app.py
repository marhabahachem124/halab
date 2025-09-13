import streamlit as st
import time
import websocket
import json
import pandas as pd
from datetime import datetime
import psycopg2
import os
import threading

# --- Database Connection Details ---
DB_URI = os.environ.get("DATABASE_URL", "postgresql://charboul_user:Nri3ODg6M9mDFu1kK71ru69FiAmKSNtY@dpg-d32peaqdbo4c73alceog-a.oregon-postgres.render.com/charboul")

# --- Authentication Logic ---
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

# --- Database Functions ---
def get_db_connection():
    try:
        return psycopg2.connect(DB_URI)
    except Exception as e:
        st.error(f"❌ Error connecting to database: {e}")
        return None

def start_new_session_in_db(email, settings):
    # First, check if the user is authorized to use the bot
    if not is_user_active(email):
        st.error("❌ هذا البريد الإلكتروني غير مفعّل لاستخدام البوت.")
        return False
        
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS user_settings (
                        email VARCHAR(255) PRIMARY KEY,
                        user_token VARCHAR(255),
                        base_amount NUMERIC(10, 2),
                        tp_target NUMERIC(10, 2),
                        max_consecutive_losses INTEGER,
                        total_wins INTEGER,
                        total_losses INTEGER,
                        current_amount NUMERIC(10, 2),
                        consecutive_losses INTEGER,
                        initial_balance NUMERIC(10, 2),
                        contract_id VARCHAR(255)
                    );
                """)
                cur.execute("""
                    INSERT INTO user_settings (email, user_token, base_amount, tp_target, max_consecutive_losses,
                                               total_wins, total_losses, current_amount, consecutive_losses, initial_balance,
                                               contract_id)
                    VALUES (%s, %s, %s, %s, %s, 0, 0, %s, 0, 0, NULL)
                    ON CONFLICT (email) DO UPDATE SET
                    user_token = EXCLUDED.user_token,
                    base_amount = EXCLUDED.base_amount,
                    tp_target = EXCLUDED.tp_target,
                    max_consecutive_losses = EXCLUDED.max_consecutive_losses,
                    total_wins = 0,
                    total_losses = 0,
                    current_amount = EXCLUDED.base_amount,
                    consecutive_losses = 0,
                    initial_balance = 0,
                    contract_id = NULL
                """, (email, settings["user_token"], settings["base_amount"], settings["tp_target"], 
                      settings["max_consecutive_losses"], settings["base_amount"]))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                st.error(f"❌ Error saving settings to database: {e}")
                return False
    return False

def get_session_status_from_db(email):
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_token, base_amount, tp_target, max_consecutive_losses, total_wins, total_losses, current_amount, consecutive_losses, initial_balance, contract_id FROM user_settings WHERE email = %s", (email,))
            result = cur.fetchone()
            conn.close()
            if result:
                return {
                    "user_token": result[0],
                    "base_amount": float(result[1]),
                    "tp_target": float(result[2]),
                    "max_consecutive_losses": int(result[3]),
                    "total_wins": int(result[4]),
                    "total_losses": int(result[5]),
                    "current_amount": float(result[6]),
                    "consecutive_losses": int(result[7]),
                    "initial_balance": float(result[8]),
                    "contract_id": result[9]
                }
    return None

def get_all_active_sessions():
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT email, user_token, base_amount, tp_target, max_consecutive_losses, total_wins, total_losses, current_amount, consecutive_losses, initial_balance, contract_id FROM user_settings;")
                active_sessions = cur.fetchall()
                conn.close()
                return active_sessions
        except Exception as e:
            st.error(f"❌ An error occurred while fetching active sessions: {e}")
            return []
    return []

def update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=None, contract_id=None):
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
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
                conn.close()
        except Exception as e:
            st.error(f"❌ An error occurred while updating database for {email}: {e}")

def clear_session_data(email):
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_settings WHERE email = %s", (email,))
                conn.commit()
                conn.close()
        except Exception as e:
            st.error(f"❌ An error occurred while clearing session data for {email}: {e}")

# --- Trading Bot Logic ---
def get_balance_and_currency(ws):
    req = {"balance": 1, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        if response.get('msg_type') == 'balance':
            balance_info = response.get('balance', {})
            return balance_info.get('balance'), balance_info.get('currency')
        return None, None
    except Exception as e:
        st.error(f"❌ Error getting balance: {e}")
        return None, None
            
def analyse_data(df_ticks):
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

def place_order(ws, proposal_id, amount):
    req = {"buy": proposal_id, "price": round(max(0.5, amount), 2)}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv()) 
        return response
    except Exception as e:
        st.error(f"❌ Error placing order: {e}")
        return {"error": {"message": "Order placement failed."}}

def check_contract_status(ws, contract_id):
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv()) 
        return response.get('proposal_open_contract')
    except Exception as e:
        st.error(f"❌ Error checking contract status: {e}")
        return None

def run_trading_job_for_user(session_data):
    try:
        email, user_token, base_amount, tp_target, max_consecutive_losses, total_wins, total_losses, current_amount, consecutive_losses, initial_balance, contract_id = session_data

        ws = None
        try:
            ws = websocket.WebSocket()
            ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
            auth_req = {"authorize": user_token}
            ws.send(json.dumps(auth_req))
            auth_response = json.loads(ws.recv())
            if auth_response.get('error'):
                st.error(f"❌ Auth failed for {email}: {auth_response['error']['message']}")
                clear_session_data(email)
                return
        except Exception as e:
            st.error(f"❌ Connection or Authorization Failed for {email}: {e}")
            return

        balance, currency = get_balance_and_currency(ws)
        if initial_balance is None or initial_balance == 0:
            initial_balance = balance
            update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=contract_id)
        
        if contract_id:
            contract_info = check_contract_status(ws, contract_id)
            if contract_info and contract_info.get('is_sold'):
                profit = contract_info.get('profit', 0)
                if profit > 0:
                    consecutive_losses = 0
                    total_wins += 1
                    current_amount = base_amount
                else:
                    consecutive_losses += 1
                    total_losses += 1
                    next_bet = current_amount * 2.2
                    current_amount = max(base_amount, next_bet)
                contract_id = None
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=contract_id)

                if (balance - initial_balance) >= tp_target:
                    st.warning(f"🎉 Take Profit target (${tp_target}) reached. Stopping bot.")
                    clear_session_data(email)
                    st.session_state.is_bot_running = False
                    return
                
                if consecutive_losses >= max_consecutive_losses:
                    st.error(f"🔴 Maximum consecutive losses ({max_consecutive_losses}) reached. Stopping bot.")
                    clear_session_data(email)
                    st.session_state.is_bot_running = False
                    return
        
        if not contract_id:
            req = {"ticks_history": "R_100", "end": "latest", "count": 5, "style": "ticks"}
            ws.send(json.dumps(req))
            tick_data = None
            while not tick_data:
                response = json.loads(ws.recv())
                if response.get('msg_type') == 'history':
                    tick_data = response
            
            if 'history' in tick_data and 'prices' in tick_data['history']:
                ticks = tick_data['history']['prices']
                df_ticks = pd.DataFrame({'price': ticks})
                signal, _ = analyse_data(df_ticks)
                
                if signal in ['Buy', 'Sell']:
                    contract_type = "CALL" if signal == 'Buy' else "PUT"
                    proposal_req = {
                        "proposal": 1,
                        "amount": round(current_amount, 2),
                        "basis": "stake",
                        "contract_type": contract_type,
                        "currency": currency,
                        "duration": 15,
                        "duration_unit": "s",
                        "symbol": "R_100"
                    }
                    ws.send(json.dumps(proposal_req))
                    proposal_response = json.loads(ws.recv())
                    
                    if 'proposal' in proposal_response:
                        proposal_id = proposal_response['proposal']['id']
                        order_response = place_order(ws, proposal_id, current_amount)
                        if 'buy' in order_response and 'contract_id' in order_response['buy']:
                            contract_id = order_response['buy']['contract_id']
                            update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=contract_id)
                    else:
                        st.error(f"❌ Proposal failed. Response: {proposal_response}")
            else:
                st.error("❌ Failed to get ticks history or ticks data is empty.")
    except Exception as e:
        st.error(f"\n❌ An unhandled error occurred in trading job for {email}: {e}")
    finally:
        if ws and ws.connected:
            ws.close()

def bot_loop():
    while True:
        if st.session_state.is_bot_running:
            now = datetime.now()
            if now.second >= 58:
                active_sessions = get_all_active_sessions()
                if active_sessions:
                    for session in active_sessions:
                        run_trading_job_for_user(session)
                time.sleep(1)
            else:
                time.sleep(0.1)
        else:
            time.sleep(1)

# --- Streamlit App ---
st.set_page_config(page_title="Hadi Binary Bot", layout="wide")

st.title("Hadi Binary Bot Interface")

if "is_bot_running" not in st.session_state:
    st.session_state.is_bot_running = False
if "stats" not in st.session_state:
    st.session_state.stats = None

st.markdown("---")
st.subheader("إعدادات البوت")

with st.form("settings_form"):
    email = st.text_input("البريد الإلكتروني", "your_email@example.com")
    user_token = st.text_input("Deriv API Token", type="password")
    base_amount = st.number_input("مقدار الرهان الأساسي", min_value=0.5, value=0.5, step=0.1)
    tp_target = st.number_input("هدف الربح (Take Profit)", min_value=10.0, value=20.0, step=5.0)
    max_consecutive_losses = st.number_input("الخسائر المتتالية القصوى", min_value=1, value=5, step=1)
    
    col1, col2 = st.columns(2)
    with col1:
        start_button = st.form_submit_button("تشغيل البوت")
    with col2:
        stop_button = st.form_submit_button("إيقاف البوت")

if start_button:
    if not user_token:
        st.error("رجاءً أدخل الـ Deriv API Token لتشغيل البوت.")
    else:
        settings = {
            "user_token": user_token,
            "base_amount": base_amount,
            "tp_target": tp_target,
            "max_consecutive_losses": max_consecutive_losses
        }
        success = start_new_session_in_db(email, settings)
        if success:
            st.session_state.is_bot_running = True
            st.success("✅ تم تشغيل البوت بنجاح! يرجى الانتظار لتحديث الإحصائيات.")
        else:
            # The error message is handled inside start_new_session_in_db
            pass

if stop_button:
    clear_session_data(email)
    st.session_state.is_bot_running = False
    st.info("⏸️ تم إيقاف البوت.")
    st.session_state.stats = None

st.markdown("---")
st.subheader("الإحصائيات")

stats_placeholder = st.empty()

if st.session_state.is_bot_running:
    stats_data = get_session_status_from_db(email)
    if stats_data:
        st.session_state.stats = stats_data
        st.success("✅ البوت قيد التشغيل")
    else:
        st.warning("🔄 جارٍ الاتصال بالبوت... يرجى الانتظار.")
else:
    st.info("البوت متوقف حالياً.")

if st.session_state.stats:
    with stats_placeholder.container():
        stats = st.session_state.stats
        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            st.metric(label="إجمالي الأرباح", value=f"${stats['current_amount']:.2f}")
        with col2:
            st.metric(label="الربح المستهدف", value=f"${stats['tp_target']:.2f}")
        with col3:
            st.metric(label="إجمالي الصفقات الرابحة", value=stats['total_wins'])
        with col4:
            st.metric(label="إجمالي الصفقات الخاسرة", value=stats['total_losses'])
        with col5:
            st.metric(label="الخسائر المتتالية", value=stats['consecutive_losses'])
        
        if stats['contract_id']:
            st.warning("⚠️ هناك صفقة قيد الانتظار. سيتم تحديث الإحصائيات عند انتهائها.")

# Ensure the bot loop runs in a background thread
if "bot_thread_started" not in st.session_state:
    st.session_state.bot_thread_started = False

if not st.session_state.bot_thread_started:
    bot_thread = threading.Thread(target=bot_loop)
    bot_thread.daemon = True
    bot_thread.start()
    st.session_state.bot_thread_started = True
