import streamlit as st
import psycopg2
import time

# --- Database Connection Details ---
DB_URI = "postgresql://bestan_user:gTJKgsCRwEu9ijNMD9d3IMxFcW5TAdE0@dpg-d329ao2dbo4c73a92kng-a.oregon-postgres.render.com/bestan" 

# --- Session State ---
if "user_email" not in st.session_state:
    st.session_state.user_email = ""
if "is_logged_in" not in st.session_state:
    st.session_state.is_logged_in = False

# --- Database Functions ---
def get_db_connection():
    try:
        return psycopg2.connect(DB_URI)
    except Exception as e:
        st.error(f"❌ Error connecting to database: {e}")
        return None

def create_table_if_not_exists():
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
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
            conn.commit()
            conn.close()

def save_settings(email, settings):
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
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

def get_session_status(email):
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            cur.execute("SELECT total_wins, total_losses, current_amount, consecutive_losses, initial_balance FROM user_settings WHERE email = %s", (email,))
            result = cur.fetchone()
            conn.close()
            if result:
                return {
                    "total_wins": result[0],
                    "total_losses": result[1],
                    "current_amount": float(result[2]),
                    "consecutive_losses": result[3],
                    "initial_balance": float(result[4])
                }
    return None

def stop_session(email):
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_settings WHERE email = %s", (email,))
            conn.commit()
            conn.close()

# --- UI Functions ---
def show_login_page():
    st.title("KHOURYBOT - Login 🤖")
    email = st.text_input("Enter your registered email:")
    if st.button("Login"):
        if not email:
            st.error("Please enter your email.")
        else:
            try:
                with open("user_ids.txt", "r") as f:
                    valid_emails = [line.strip().lower() for line in f.readlines()]
                if email.lower() in valid_emails:
                    st.session_state.user_email = email.lower()
                    st.session_state.is_logged_in = True
                    st.rerun()
                else:
                    st.error("Sorry, you do not have access to this account.")
            except FileNotFoundError:
                st.error("❌ 'user_ids.txt' file not found.")

def show_bot_settings():
    st.title("KHOURYBOT - Bot Settings")
    st.write(f"Welcome, {st.session_state.user_email}!")
    user_token = st.text_input("Enter your Deriv API token:", type="password")
    base_amount = st.number_input("Base Amount ($)", min_value=0.5, step=0.5, value=0.5)
    tp_target = st.number_input("Take Profit Target ($)", min_value=1.0, step=1.0, value=10.0)
    max_consecutive_losses = st.number_input("Max Consecutive Losses", min_value=1, step=1, value=5)

    if st.button("Start Bot", type="primary"):
        if not user_token:
            st.error("Please enter a valid API token.")
        else:
            settings = {
                "user_token": user_token,
                "base_amount": base_amount,
                "tp_target": tp_target,
                "max_consecutive_losses": max_consecutive_losses
            }
            save_settings(st.session_state.user_email, settings)
            st.success("Bot settings saved! The bot will now start working in the background.")
            st.info("You can close this tab, the bot will continue to run.")
            st.rerun()

def show_bot_stats(stats):
    st.title("KHOURYBOT - Live Status")
    st.write(f"Welcome back, {st.session_state.user_email}!")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Stop Bot", type="secondary"):
            stop_session(st.session_state.user_email)
            st.success("Bot has been stopped.")
            st.rerun()
    with col2:
        if st.button("Refresh Status"):
            st.rerun()

    st.markdown("---")
    
    current_balance = stats.get('current_balance', 0)
    initial_balance = stats.get('initial_balance', 0)
    profit = current_balance - initial_balance if initial_balance else 0

    st.markdown(f"**Wins:** {stats['total_wins']} | **Losses:** {stats['total_losses']} | **Consecutive Losses:** {stats['consecutive_losses']}")
    st.metric(
        "Current Balance",
        f"{current_balance:.2f} USD",
        delta=round(profit, 2),
        delta_color="normal"
    )

# --- Main App Logic ---
create_table_if_not_exists()
if not st.session_state.is_logged_in:
    show_login_page()
else:
    stats = get_session_status(st.session_state.user_email)
    if stats:
        show_bot_stats(stats)
    else:
        show_bot_settings()
