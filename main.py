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
import subprocess
import webbrowser
import sys
import multiprocessing
import signal

# --- SQLite Database Configuration ---
DB_FILE = "trading_data.db"
trading_lock = threading.Lock()

# --- Database & Utility Functions ---
def create_connection():
    try:
        conn = sqlite3.connect(DB_FILE)
        return conn
    except sqlite3.Error as e:
        print(f"❌ Database connection error: {e}", file=sys.stderr)
        return None

def create_table_if_not_exists():
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
            print(f"❌ Error creating/updating table: {e}", file=sys.stderr)
        finally:
            conn.close()

def is_user_active(email):
    try:
        with open("user_ids.txt", "r") as file:
            active_users = [line.strip() for line in file.readlines()]
        return email in active_users
    except FileNotFoundError:
        print("❌ Error: 'user_ids.txt' file not found.", file=sys.stderr)
        return False
    except Exception as e:
        print(f"❌ An error occurred while reading 'user_ids.txt': {e}", file=sys.stderr)
        return False

def start_new_session_in_db(email, settings):
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
            print(f"❌ Error saving session to database: {e}", file=sys.stderr)
        finally:
            conn.close()

def update_is_running_status(email, status):
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.execute("UPDATE sessions SET is_running = ? WHERE email = ?", (status, email))
            print(f"✅ Bot status for {email} updated to {'running' if status == 1 else 'stopped'}.")
        except sqlite3.Error as e:
            print(f"❌ Error updating bot status for {email}: {e}", file=sys.stderr)
        finally:
            conn.close()

def clear_session_data(email):
    conn = create_connection()
    if conn:
        try:
            with conn:
                conn.execute("DELETE FROM sessions WHERE email=?", (email,))
            print(f"✅ Session for {email} deleted successfully.")
        except sqlite3.Error as e:
            print(f"❌ Error deleting session from database: {e}", file=sys.stderr)
        finally:
            conn.close()

def get_session_status_from_db(email):
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
            print(f"❌ Error fetching session from database: {e}", file=sys.stderr)
            return None
        finally:
            conn.close()

def get_all_active_sessions():
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
            print(f"❌ Error fetching all sessions from database: {e}", file=sys.stderr)
            return []
        finally:
            conn.close()

def update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=None, contract_id=None, trade_start_time=None):
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
            print(f"❌ Error updating session in database: {e}", file=sys.stderr)
        finally:
            conn.close()

# --- WebSocket Helper Functions ---
def connect_websocket(user_token):
    ws = websocket.WebSocket()
    try:
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929")
        auth_req = {"authorize": user_token}
        ws.send(json.dumps(auth_req))
        auth_response = json.loads(ws.recv())
        if auth_response.get('error'):
            print(f"❌ Authentication failed: {auth_response['error']['message']}", file=sys.stderr)
            ws.close()
            return None
        return ws
    except Exception as e:
        print(f"❌ WebSocket connection or authentication failed: {e}", file=sys.stderr)
        return None

def get_balance_and_currency(user_token):
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
        print(f"❌ Error fetching balance: {e}", file=sys.stderr)
        return None, None
    finally:
        if ws and ws.connected:
            ws.close()
            
def check_contract_status(ws, contract_id):
    if not ws or not ws.connected:
        return None
    req = {"proposal_open_contract": 1, "contract_id": contract_id}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        return response.get('proposal_open_contract')
    except Exception as e:
        print(f"❌ Error checking contract status: {e}", file=sys.stderr)
        return None

def place_order(ws, proposal_id, amount):
    if not ws or not ws.connected:
        return {"error": {"message": "WebSocket not connected."}}
    amount_decimal = decimal.Decimal(str(amount)).quantize(decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_UP)
    req = {"buy": proposal_id, "price": float(amount_decimal)}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        return response
    except Exception as e:
        print(f"❌ Error placing order: {e}", file=sys.stderr)
        return {"error": {"message": "Order placement failed."}}

# --- Trading Bot Logic ---
def analyse_data(df_ticks):
    if len(df_ticks) < 30:
        return "Neutral", "Insufficient data. Need at least 30 ticks."

    last_30_ticks = df_ticks.tail(30).copy()
    
    if last_30_ticks.iloc[-1]['price'] > last_30_ticks.iloc[0]['price']:
        return "Buy", "Detected a 30-tick uptrend."
    elif last_30_ticks.iloc[-1]['price'] < last_30_ticks.iloc[0]['price']:
        return "Sell", "Detected a 30-tick downtrend."
    else:
        return "Neutral", "No clear 30-tick trend detected."

def run_trading_job_for_user(session_data, check_only=False):
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
                    clear_session_data(email) 
                    return
                
                if consecutive_losses >= max_consecutive_losses:
                    print(f"🔴 User {email}: Max consecutive losses ({max_consecutive_losses}) reached. Stopping the bot and clearing data.")
                    update_is_running_status(email, 0)
                    clear_session_data(email)
                    return
            else:
                print(f"User {email}: Contract {contract_id} is still pending. Retrying next cycle.")
        except Exception as e:
            print(f"\n❌ An unexpected error occurred while processing pending contract for user {email}: {e}", file=sys.stderr)
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
                    print(f"❌ Failed to fetch balance for user {email}. Skipping trade job.", file=sys.stderr)
                    return
                if initial_balance == 0:
                    initial_balance = float(balance)
                    update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=contract_id)
                
                req = {"ticks_history": "R_100", "end": "latest", "count": 30, "style": "ticks"}
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
                            "duration": 30, "duration_unit": "s", "symbol": "R_100"
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
                                print(f"❌ User {email}: Failed to place order. Response: {order_response}", file=sys.stderr)
                        else:
                            print(f"❌ User {email}: Failed to get proposal. Response: {proposal_response}", file=sys.stderr)
                else:
                    print(f"❌ User {email}: Failed to get tick data.", file=sys.stderr)
            except Exception as e:
                print(f"\n❌ An unexpected error occurred in the trading job for user {email}: {e}", file=sys.stderr)
            finally:
                if ws and ws.connected:
                    ws.close()

def bot_loop():
    print("🤖 Starting main bot loop...")
    while True:
        try:
            now = datetime.now()
            active_sessions = get_all_active_sessions()
            
            if active_sessions:
                for session in active_sessions:
                    email = session['email']
                    
                    latest_session_data = get_session_status_from_db(email)
                    if not latest_session_data or latest_session_data.get('is_running') == 0:
                        continue
                    
                    contract_id = latest_session_data.get('contract_id')
                    trade_start_time = latest_session_data.get('trade_start_time')
                    
                    if contract_id:
                        if (time.time() - trade_start_time) >= 40: 
                            run_trading_job_for_user(latest_session_data, check_only=True)
                    
                    elif now.second == 58:
                        re_checked_session_data = get_session_status_from_db(email)
                        if re_checked_session_data and not re_checked_session_data.get('contract_id'):
                            run_trading_job_for_user(re_checked_session_data, check_only=False)
            
            time.sleep(1) 
        except Exception as e:
            print(f"❌ حدث خطأ غير متوقع في الحلقة الرئيسية: {e}", file=sys.stderr)
            time.sleep(5)

# --- Streamlit App UI and logic ---
def run_streamlit_app():
    st.set_page_config(page_title="Khoury Bot", layout="wide")
    st.title("Khoury Bot")

    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    if "user_email" not in st.session_state:
        st.session_state.user_email = ""
    if "stats" not in st.session_state:
        st.session_state.stats = None
        
    create_table_if_not_exists()

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
            clear_session_data(st.session_state.user_email)
            st.info("⏸️ The bot has been stopped. Session data has been cleared.")
            st.session_state.logged_in = False
            st.session_state.user_email = ""
            st.rerun()

        st.markdown("---")
        st.subheader("Statistics")

        stats_placeholder = st.empty()
        
        if is_user_bot_running:
            st.success("🟢 Your bot is **RUNNING**.")
            st.info("The bot is already active in the background. You can close this tab and monitor it from the stats page.")
        else:
            st.error("🔴 Your bot is **STOPPED**.")

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
                    st.warning("⚠️ A trade is pending. Stats will be updated after it's completed.")
        else:
            with stats_placeholder.container():
                st.info("The bot is currently stopped.")
                
        time.sleep(1)
        st.rerun()

# --- Stats Server Logic (Flask) ---
def run_stats_server():
    from flask import Flask, jsonify, render_template

    app = Flask(__name__)
    STATS_HTML = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Khoury Bot Stats</title>
        <style>
            body { font-family: Arial, sans-serif; background-color: #f0f2f5; color: #333; margin: 20px; }
            .container { max-width: 900px; margin: auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
            .header { text-align: center; color: #007bff; margin-bottom: 30px; }
            .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; }
            .stats-card { background-color: #e9ecef; padding: 15px; border-radius: 6px; }
            .stats-card h3 { margin-top: 0; color: #495057; }
            .stats-card p { margin: 5px 0; font-size: 1.1em; font-weight: bold; }
            .status-badge { display: inline-block; padding: 4px 10px; border-radius: 12px; font-weight: bold; color: white; margin-top: 10px; }
            .status-running { background-color: #28a745; }
            .status-stopped { background-color: #dc3545; }
            .status-no-data { background-color: #6c757d; }
            .status-pending { background-color: #ffc107; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1 class="header">Khoury Bot - Live Statistics</h1>
            <div id="stats-container">
                <p style="text-align: center;">Loading statistics...</p>
            </div>
        </div>
        
        <script>
            function fetchStats() {
                fetch('/api/stats')
                    .then(response => response.json())
                    .then(data => {
                        const statsContainer = document.getElementById('stats-container');
                        statsContainer.innerHTML = ''; 
                        
                        if (Object.keys(data).length === 0) {
                            statsContainer.innerHTML = '<p style="text-align: center;">No active bot sessions found.</p>';
                            return;
                        }
                        
                        const statsGrid = document.createElement('div');
                        statsGrid.className = 'stats-grid';

                        for (const email in data) {
                            const stats = data[email];
                            const card = document.createElement('div');
                            card.className = 'stats-card';

                            const statusClass = stats.is_running === 1 ? 'status-running' : 'status-stopped';
                            const statusText = stats.is_running === 1 ? 'RUNNING' : 'STOPPED';

                            card.innerHTML = `
                                <h3>User: ${email}</h3>
                                <p>Status: <span class="status-badge ${statusClass}">${statusText}</span></p>
                                <p>Current Amount: $${stats.current_amount.toFixed(2)}</p>
                                <p>Total Wins: ${stats.total_wins}</p>
                                <p>Total Losses: ${stats.total_losses}</p>
                                <p>Consecutive Losses: ${stats.consecutive_losses}</p>
                                <p>Profit Target: $${stats.tp_target.toFixed(2)}</p>
                            `;
                            statsGrid.appendChild(card);
                        }
                        statsContainer.appendChild(statsGrid);
                    })
                    .catch(error => {
                        console.error('Error fetching stats:', error);
                        document.getElementById('stats-container').innerHTML = '<p style="text-align: center; color: red;">Failed to load statistics.</p>';
                    });
            }

            setInterval(fetchStats, 5000);
            fetchStats();
        </script>
    </body>
    </html>
    """
    
    with open("stats_temp.html", "w") as f:
        f.write(STATS_HTML)
    
    @app.route('/')
    def serve_stats_page():
        return render_template("stats_temp.html")

    @app.route('/api/stats')
    def get_stats_api():
        stats = {}
        try:
            conn = sqlite3.connect(DB_FILE)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sessions")
            rows = cursor.fetchall()
            if rows:
                for row in rows:
                    stats[row['email']] = dict(row)
        except sqlite3.Error as e:
            print(f"❌ Database error in Flask app: {e}", file=sys.stderr)
        finally:
            if conn:
                conn.close()
        return jsonify(stats)

    app.run(host='0.0.0.0', port=5001, debug=False, use_reloader=False)

# --- Main Router Logic (Flask) ---
def run_main_router():
    from flask import Flask, render_template_string
    
    app = Flask(__name__)
    
    MAIN_PAGE_HTML = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Khoury Bot</title>
        <style>
            body { font-family: Arial, sans-serif; display: flex; flex-direction: column; justify-content: center; align-items: center; height: 100vh; background-color: #f0f2f5; }
            .container { text-align: center; background: white; padding: 40px; border-radius: 12px; box-shadow: 0 6px 10px rgba(0,0,0,0.1); }
            h1 { color: #007bff; margin-bottom: 20px; }
            .button-group { display: flex; gap: 20px; }
            .button-group a { text-decoration: none; }
            .button { background-color: #007bff; color: white; padding: 15px 30px; border: none; border-radius: 8px; font-size: 1.2em; cursor: pointer; transition: background-color 0.3s ease; }
            .button:hover { background-color: #0056b3; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Khoury Bot - Welcome</h1>
            <div class="button-group">
                <a href="http://localhost:8501" class="button">Log In / Settings</a>
                <a href="http://localhost:5001" class="button">View Statistics</a>
            </div>
        </div>
    </body>
    </html>
    """

    @app.route('/')
    def index():
        return render_template_string(MAIN_PAGE_HTML)

    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

# --- Signal Handler to Terminate Processes Cleanly ---
def shutdown_handler(signum, frame):
    print("\nReceived shutdown signal. Terminating all processes...")
    try:
        bot_process.terminate()
        streamlit_process.terminate()
        stats_process.terminate()
        main_router_process.terminate()
        bot_process.join()
        streamlit_process.join()
        stats_process.join()
        main_router_process.join()
        print("All processes terminated successfully.")
    except NameError:
        pass  # Processes were not yet defined
    sys.exit(0)

# --- Main Orchestrator ---
if __name__ == '__main__':
    # Set up the signal handler for clean shutdown
    signal.signal(signal.SIGINT, shutdown_handler)

    try:
        import flask
        import streamlit
    except ImportError as e:
        print(f"❌ Missing required library: {e}. Please run 'pip install Flask streamlit websocket-client pandas'.", file=sys.stderr)
        sys.exit(1)

    print("Starting all services... This may take a few moments.")
    
    # Start the bot loop in a separate process
    bot_process = multiprocessing.Process(target=bot_loop)
    bot_process.start()

    # Start the Streamlit app in a separate process
    streamlit_process = multiprocessing.Process(target=lambda: subprocess.run(["streamlit", "run", __file__], env={**os.environ, "STREAMLIT_SERVER_PORT": "8501"}))
    streamlit_process.start()

    # Start the stats server (Flask) in a separate process
    stats_process = multiprocessing.Process(target=run_stats_server)
    stats_process.start()
    
    # Start the main router (Flask) in a separate process
    main_router_process = multiprocessing.Process(target=run_main_router)
    main_router_process.start()

    # Wait for the main router to start and then open the browser
    time.sleep(3) 
    webbrowser.open("http://localhost:5000")
    
    # Keep the main script alive to manage child processes
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown_handler(None, None)
