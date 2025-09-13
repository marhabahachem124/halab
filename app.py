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
# استبدال رابط قاعدة البيانات القديم بالجديد
DB_URI = "postgresql://charboul_user:Nri3ODg6M9mDFu1kK71ru69FiAmKSNtY@dpg-d32peaqdbo4c73alceog-a.oregon-postgres.render.com/charboul"

# --- Authentication Logic ---
def is_user_active(email):
    """Checks if a user's email exists in the user_ids.txt file."""
    try:
        with open("user_ids.txt", "r") as file:
            active_users = [line.strip() for line in file.readlines()]
            return email in active_users
    except FileNotFoundError:
        st.error("❌ خطأ: ملف 'user_ids.txt' غير موجود. يرجى إنشائه.")
        return False
    except Exception as e:
        st.error(f"❌ حدث خطأ أثناء قراءة ملف 'user_ids.txt': {e}")
        return False

# --- Database Functions ---
def get_db_connection():
    try:
        return psycopg2.connect(DB_URI)
    except Exception as e:
        st.error(f"❌ خطأ في الاتصال بقاعدة البيانات: {e}")
        return None

def start_new_session_in_db(email, settings):
    # أولاً، تحقق مما إذا كان المستخدم مفعّلًا
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
                st.error(f"❌ خطأ في حفظ الإعدادات بقاعدة البيانات: {e}")
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
            st.error(f"❌ حدث خطأ أثناء جلب الجلسات النشطة: {e}")
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
            st.error(f"❌ حدث خطأ أثناء تحديث قاعدة البيانات للمستخدم {email}: {e}")

def clear_session_data(email):
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_settings WHERE email = %s", (email,))
                conn.commit()
                conn.close()
        except Exception as e:
            st.error(f"❌ حدث خطأ أثناء مسح بيانات الجلسة للمستخدم {email}: {e}")

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
        st.error(f"❌ خطأ في جلب الرصيد: {e}")
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
        st.error(f"❌ خطأ في وضع الطلب: {e}")
        return {"error": {"message": "Order placement failed."}}

def check_contract_status(ws, contract_id):
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv()) 
        return response.get('proposal_open_contract')
    except Exception as e:
        st.error(f"❌ خطأ في التحقق من حالة العقد: {e}")
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
                st.error(f"❌ فشل المصادقة للمستخدم {email}: {auth_response['error']['message']}")
                clear_session_data(email)
                return
        except Exception as e:
            st.error(f"❌ فشل الاتصال أو المصادقة للمستخدم {email}: {e}")
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
                    st.warning(f"🎉 تم الوصول إلى هدف الربح (${tp_target}). إيقاف البوت.")
                    clear_session_data(email)
                    st.session_state.is_bot_running = False # Stop bot from UI side
                    return
                
                if consecutive_losses >= max_consecutive_losses:
                    st.error(f"🔴 تم الوصول إلى أقصى عدد خسائر متتالية ({max_consecutive_losses}). إيقاف البوت.")
                    clear_session_data(email)
                    st.session_state.is_bot_running = False # Stop bot from UI side
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
                        st.error(f"❌ فشل الاقتراح. الرد: {proposal_response}")
            else:
                st.error("❌ فشل في الحصول على سجل التيكس أو البيانات فارغة.")
    except Exception as e:
        st.error(f"\n❌ حدث خطأ غير معالج في مهمة التداول للمستخدم {email}: {e}")
    finally:
        if ws and ws.connected:
            ws.close()

def bot_loop():
    while True:
        # Check if bot is supposed to be running from Streamlit UI state
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
            time.sleep(1) # Sleep if bot is not running to avoid busy-waiting

# --- Streamlit App ---
st.set_page_config(page_title="khourybot", layout="wide")

# --- Initialize Session State Variables ---
# Initialize these ONLY if they don't exist yet.
# This prevents resetting on every rerun.
if "is_bot_running" not in st.session_state:
    st.session_state.is_bot_running = False
if "user_email" not in st.session_state:
    st.session_state.user_email = ""
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "stats" not in st.session_state:
    st.session_state.stats = None
if "bot_thread_started" not in st.session_state:
    st.session_state.bot_thread_started = False

# --- Bot Thread Management ---
# Start the bot thread only once when the app first loads
if not st.session_state.bot_thread_started:
    bot_thread = threading.Thread(target=bot_loop)
    bot_thread.daemon = True # Allows the main program to exit even if thread is running
    bot_thread.start()
    st.session_state.bot_thread_started = True
    # Optional: You might want to add a small delay to ensure the thread is ready,
    # though the 'bot_loop' itself has sleeps.

# --- Main App Logic ---

if not st.session_state.logged_in:
    # --- Login Interface ---
    st.title("khourybot - تسجيل الدخول")
    st.markdown("---")
    st.subheader("تسجيل الدخول")

    with st.form("login_form"):
        login_email = st.text_input("البريد الإلكتروني", key="login_email_input")
        login_token = st.text_input("Deriv API Token", type="password", key="login_token_input")
        login_button = st.form_submit_button("تسجيل الدخول")

    if login_button:
        if not login_token:
            st.error("رجاءً أدخل الـ Deriv API Token.")
        elif not login_email:
            st.error("رجاءً أدخل البريد الإلكتروني.")
        else:
            # Check if the user is active using the file
            if is_user_active(login_email):
                # Temporarily store settings, will be saved to DB if all checks pass
                st.session_state.temp_email = login_email
                st.session_state.temp_token = login_token
                st.session_state.logged_in = True
                # Use rerun to clear the login form and show the settings page
                st.rerun()
            else:
                st.error("❌ هذا البريد الإلكتروني غير مفعّل لاستخدام البوت. يرجى التأكد من وجوده في ملف user_ids.txt.")

else:
    # --- Settings Interface (only shown after successful login) ---
    st.title("khourybot - الإعدادات")
    st.markdown("---")
    
    # Display the logged-in user's email (optional)
    st.write(f"مرحباً بك, **{st.session_state.user_email if st.session_state.user_email else st.session_state.temp_email}**")

    st.subheader("إعدادات البوت")

    with st.form("settings_form"):
        # Pre-fill inputs with existing settings if available
        current_email = st.session_state.user_email if st.session_state.user_email else st.session_state.temp_email
        
        # Retrieve existing settings from DB if user is already configured
        existing_settings = get_session_status_from_db(current_email)
        
        if existing_settings:
            # If user exists, pre-fill token, amounts, etc.
            token_value = existing_settings.get("user_token", "")
            base_amount_value = existing_settings.get("base_amount", 0.5)
            tp_target_value = existing_settings.get("tp_target", 20.0)
            max_consecutive_losses_value = existing_settings.get("max_consecutive_losses", 5)
        else:
            # If new user or no settings yet, use defaults from temp session and UI inputs
            token_value = st.session_state.temp_token if hasattr(st.session_state, 'temp_token') else ""
            base_amount_value = 0.5
            tp_target_value = 20.0
            max_consecutive_losses_value = 5
            
        # Use pre-filled values in the form
        user_token_setting = st.text_input("Deriv API Token", value=token_value, type="password", key="user_token_setting")
        base_amount_setting = st.number_input("مقدار الرهان الأساسي", min_value=0.5, value=float(base_amount_value), step=0.1, key="base_amount_setting")
        tp_target_setting = st.number_input("هدف الربح (Take Profit)", min_value=10.0, value=float(tp_target_value), step=5.0, key="tp_target_setting")
        max_consecutive_losses_setting = st.number_input("الخسائر المتتالية القصوى", min_value=1, value=int(max_consecutive_losses_value), step=1, key="max_consecutive_losses_setting")
        
        col1, col2 = st.columns(2)
        with col1:
            start_button = st.form_submit_button("تطبيق الإعدادات وتشغيل البوت")
        with col2:
            stop_button = st.form_submit_button("إيقاف البوت")

    if start_button:
        if not user_token_setting:
            st.error("رجاءً أدخل الـ Deriv API Token.")
        else:
            settings = {
                "user_token": user_token_setting,
                "base_amount": base_amount_setting,
                "tp_target": tp_target_setting,
                "max_consecutive_losses": max_consecutive_losses_setting
            }
            # Save settings to DB and activate bot
            success = start_new_session_in_db(current_email, settings)
            if success:
                st.session_state.is_bot_running = True
                st.session_state.user_email = current_email # Store email in session state after successful config
                st.success("✅ تم حفظ الإعدادات وتشغيل البوت بنجاح!")
                st.rerun() # Rerun to update UI (e.g., show stats)
            # If start_new_session_in_db returned False, an error is already shown.

    if stop_button:
        clear_session_data(current_email)
        st.session_state.is_bot_running = False
        st.session_state.user_email = "" # Clear user email on logout
        st.session_state.logged_in = False # Return to login page
        st.session_state.stats = None # Clear stats
        st.info("⏸️ تم إيقاف البوت وتسجيل الخروج.")
        st.rerun() # Rerun to show login page

    # --- Display Stats (only if logged in and bot is running) ---
    if st.session_state.logged_in and st.session_state.is_bot_running:
        st.markdown("---")
        st.subheader("الإحصائيات")
        
        stats_data = get_session_status_from_db(st.session_state.user_email)
        if stats_data:
            st.session_state.stats = stats_data
        
        if st.session_state.stats:
            with st.container():
                stats = st.session_state.stats
                col1, col2, col3, col4, col5 = st.columns(5)
                with col1:
                    st.metric(label="رصيد التداول الحالي", value=f"${stats['current_amount']:.2f}")
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
        else:
            st.warning("🔄 جارٍ جلب الإحصائيات... يرجى الانتظار.")
    elif st.session_state.logged_in and not st.session_state.is_bot_running:
        st.info("البوت متوقف حالياً. اضغط على 'تطبيق الإعدادات وتشغيل البوت' للبدء.")
    elif st.session_state.logged_in and not get_session_status_from_db(st.session_state.user_email):
        st.warning("لم يتم العثور على إعدادات للبوت. يرجى ضبط الإعدادات وتشغيل البوت.")
