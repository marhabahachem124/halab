import time
import websocket
import json
import pandas as pd
from datetime import datetime
import psycopg2
import os
import sys
from flask import Flask, render_template_string, request, redirect, url_for, session
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

app = Flask(__name__)
app.secret_key = 'your_super_secret_key'  # قم بتغيير هذا إلى مفتاح سري خاص بك

# --- Database Connection Details ---
DB_URI = "postgresql://bestan_user:gTJKgsCRwEu9ijNMD9d3IMxFcW5TAdE0@dpg-d329ao2dbo4c73a92kng-a.oregon-postgres.render.com/bestan" 

# --- HTML Templates as Strings ---
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Login to Khourybot</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #f0f2f5; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .container { background-color: #fff; padding: 30px 40px; border-radius: 10px; box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1); text-align: center; }
        h1 { color: #333; margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; color: #555; text-align: left; }
        input[type="email"] { width: 100%; padding: 10px; margin-bottom: 20px; border: 1px solid #ddd; border-radius: 5px; box-sizing: border-box; }
        input[type="submit"] { width: 100%; padding: 12px; background-color: #007bff; color: white; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; transition: background-color 0.3s ease; }
        input[type="submit"]:hover { background-color: #0056b3; }
        .error { color: red; margin-top: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📈 Welcome with KHOURYBOT Autotrading</h1>
        <form method="POST">
            <label for="email">Enter your registered email:</label><br>
            <input type="email" id="email" name="email" required><br>
            <input type="submit" value="Login">
        </form>
        {% if error_message %}
            <p class="error">{{ error_message }}</p>
        {% endif %}
    </div>
</body>
</html>
"""

SETTINGS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Bot Settings</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #f0f2f5; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .container { background-color: #fff; padding: 30px 40px; border-radius: 10px; box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1); width: 400px; text-align: center; }
        h1, h2 { color: #333; margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; color: #555; text-align: left; }
        input[type="text"], input[type="number"] { width: 100%; padding: 10px; margin-bottom: 20px; border: 1px solid #ddd; border-radius: 5px; box-sizing: border-box; }
        input[type="submit"] { width: 100%; padding: 12px; background-color: #28a745; color: white; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; transition: background-color 0.3s ease; }
        input[type="submit"]:hover { background-color: #218838; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📈 Welcome with KHOURYBOT Autotrading</h1>
        <h2>⚙️ Start a new session</h2>
        <form method="POST">
            <label for="user_token">Deriv API Token:</label>
            <input type="text" id="user_token" name="user_token" required><br>
            
            <label for="base_amount">Base Amount ($):</label>
            <input type="number" id="base_amount" name="base_amount" step="0.01" required><br>
            
            <label for="tp_target">Take Profit Target ($):</label>
            <input type="number" id="tp_target" name="tp_target" step="0.01" required><br>
            
            <label for="max_consecutive_losses">Max Consecutive Losses:</label>
            <input type="number" id="max_consecutive_losses" name="max_consecutive_losses" required><br>
            
            <input type="submit" value="Start Bot">
        </form>
    </div>
</body>
</html>
"""

STATS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Bot Stats</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #f0f2f5; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .container { background-color: #fff; padding: 30px 40px; border-radius: 10px; box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1); width: 400px; }
        h1, h2 { color: #333; text-align: center; }
        ul { list-style: none; padding: 0; text-align: left; }
        li { background-color: #f9f9f9; padding: 12px; margin-bottom: 8px; border-radius: 5px; border-left: 5px solid #007bff; }
        .profit { color: #28a745; font-weight: bold; }
        .loss { color: #dc3545; font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📈 Welcome with KHOURYBOT Autotrading</h1>
        <h2>📊 Trading Stats:</h2>
        <ul>
            <li>Total Wins: {{ stats['total_wins'] }}</li>
            <li>Total Losses: {{ stats['total_losses'] }}</li>
            <li>Consecutive Losses: {{ stats['consecutive_losses'] }}</li>
            <li>Current Martingale Amount: ${{ stats['current_amount'] }}</li>
            <li>Initial Balance: ${{ "%.2f"|format(stats['initial_balance']) }}</li>
        </ul>
        <p>Current P/L: <span class="{{ 'profit' if profit > 0 else 'loss' }}">${{ "%.2f"|format(profit) }}</span></p>
    </div>
</body>
</html>
"""

# --- Database Functions ---
def get_db_connection():
    try:
        return psycopg2.connect(DB_URI)
    except Exception as e:
        return None

def create_table_if_not_exists():
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
                        initial_balance NUMERIC(10, 2)
                    );
                """)
                conn.commit()
                print("✅ Table 'user_settings' created successfully or already exists.")
            except Exception as e:
                print(f"❌ Error creating table: {e}")
            finally:
                if conn:
                    conn.close()

def load_settings_from_db(email):
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_token, base_amount, tp_target, max_consecutive_losses FROM user_settings WHERE email = %s", (email,))
            result = cur.fetchone()
            conn.close()
            if result:
                return {
                    "user_token": result[0],
                    "base_amount": result[1],
                    "tp_target": result[2],
                    "max_consecutive_losses": result[3]
                }
    return None

def load_stats_from_db(email):
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
                    "current_amount": result[2],
                    "consecutive_losses": result[3],
                    "initial_balance": result[4]
                }
    return None

def save_settings_and_start_session(email, settings):
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    INSERT INTO user_settings (email, user_token, base_amount, tp_target, max_consecutive_losses,
                                               total_wins, total_losses, current_amount, consecutive_losses, initial_balance)
                    VALUES (%s, %s, %s, %s, %s, 0, 0, %s, 0, 0)
                    ON CONFLICT (email) DO UPDATE SET
                    user_token = EXCLUDED.user_token,
                    base_amount = EXCLUDED.base_amount,
                    tp_target = EXCLUDED.tp_target,
                    max_consecutive_losses = EXCLUDED.max_consecutive_losses,
                    total_wins = 0,
                    total_losses = 0,
                    current_amount = EXCLUDED.base_amount,
                    consecutive_losses = 0,
                    initial_balance = 0
                """, (email, settings["user_token"], settings["base_amount"], settings["tp_target"], 
                      settings["max_consecutive_losses"], settings["base_amount"]))
                conn.commit()
                print("✅ New session started and settings saved to database.")
            except Exception as e:
                print(f"❌ Error saving settings: {e}")
            finally:
                cur.close()
                conn.close()

def update_stats_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=None):
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            try:
                query = """
                    UPDATE user_settings
                    SET total_wins = %s,
                        total_losses = %s,
                        current_amount = %s,
                        consecutive_losses = %s
                    WHERE email = %s
                """
                params = (total_wins, total_losses, current_amount, consecutive_losses, email)

                if initial_balance is not None:
                    query = query.replace("WHERE", ", initial_balance = %s WHERE")
                    params = (total_wins, total_losses, current_amount, consecutive_losses, initial_balance, email)
                
                cur.execute(query, params)
                conn.commit()
            except Exception as e:
                print(f"❌ Error updating stats in DB: {e}")
            finally:
                conn.close()

def clear_session_data(email):
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            try:
                cur.execute("DELETE FROM user_settings WHERE email = %s", (email,))
                conn.commit()
            except Exception as e:
                print(f"❌ Error clearing session data: {e}")
            finally:
                conn.close()

# --- Trading Bot Logic ---
is_trade_open = False
contract_id = None
user_email_for_bot = None

def get_balance(ws):
    req = {"balance": 1, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        if response.get('msg_type') == 'balance':
            return response.get('balance', {}).get('balance')
        return None
    except Exception:
        return None

def analyse_data(df_ticks):
    if len(df_ticks) < 5:
        return "Neutral", "Insufficient data: Less than 5 ticks available."
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
    except Exception:
        return {"error": {"message": "Order placement failed."}}

def check_contract_status(ws, contract_id):
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        return response.get('proposal_open_contract')
    except Exception:
        return None

# --- Background Trading Bot Job ---
def run_trading_job():
    global is_trade_open, contract_id, user_email_for_bot
    
    if user_email_for_bot is None:
        print("Bot is inactive. Awaiting a user login to start trading.")
        return
    
    stats = load_stats_from_db(user_email_for_bot)
    settings = load_settings_from_db(user_email_for_bot)

    if not stats or not settings:
        print(f"❌ No active session found for email: {user_email_for_bot}. Trading job stopped.")
        # Clear the global state to stop the bot from trying to run
        user_email_for_bot = None
        return

    total_wins = stats["total_wins"]
    total_losses = stats["total_losses"]
    current_amount = stats["current_amount"]
    consecutive_losses = stats["consecutive_losses"]
    initial_balance = stats["initial_balance"]
    
    user_token = settings["user_token"]
    base_amount = settings["base_amount"]
    tp_target = settings["tp_target"]
    max_consecutive_losses = settings["max_consecutive_losses"]
    
    try:
        ws = websocket.WebSocket()
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        auth_req = {"authorize": user_token}
        ws.send(json.dumps(auth_req))
        auth_response = json.loads(ws.recv())

        if auth_response.get('error'):
            print(f"❌ Auth failed for {user_email_for_bot}: {auth_response['error']['message']}")
            return
        
        # --- Get Account Balance and Currency ---
        balance_req = {"balance": 1}
        ws.send(json.dumps(balance_req))
        balance_response = json.loads(ws.recv())
        currency = balance_response.get('balance', {}).get('currency', 'USD')
        
        current_balance = balance_response.get('balance', {}).get('balance')

        if initial_balance == 0 or initial_balance is None:
            initial_balance = current_balance
            update_stats_in_db(user_email_for_bot, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance)
        
        # --- Trading Logic ---
        if not is_trade_open:
            req = {"ticks_history": "R_100", "end": "latest", "count": 5, "style": "ticks"}
            ws.send(json.dumps(req))
            tick_data = json.loads(ws.recv())
            
            if 'history' in tick_data and tick_data['history']['prices']:
                ticks = tick_data['history']['prices']
                df_ticks = pd.DataFrame({'price': ticks})
                signal, error_msg = analyse_data(df_ticks)
                
                if signal in ['Buy', 'Sell']:
                    contract_type = "CALL" if signal == 'Buy' else "PUT"
                    proposal_req = {
                        "proposal": 1,
                        "amount": round(current_amount, 2),
                        "basis": "stake",
                        "contract_type": contract_type,
                        "currency": currency, # Use the detected currency
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
                            is_trade_open = True
                            print(f"✅ Placed a {contract_type} trade for ${current_amount:.2f}")
                            contract_id = order_response['buy']['contract_id']
        else: # If a trade is open, check its status
            print("⏳ Waiting for trade result...")
            time.sleep(20) # Wait for 20 seconds to be sure the 15-second trade is done
            contract_info = check_contract_status(ws, contract_id)

            if contract_info and contract_info.get('is_sold'):
                profit = contract_info.get('profit', 0)
                
                if profit > 0:
                    consecutive_losses = 0
                    total_wins += 1
                    current_amount = base_amount
                    print(f"🎉 WIN! Profit: ${profit:.2f}. Total wins: {total_wins}")
                elif profit < 0:
                    consecutive_losses += 1
                    total_losses += 1
                    next_bet = current_amount * 2.2
                    current_amount = max(base_amount, next_bet)
                    print(f"🔻 LOSS! New amount: ${current_amount:.2f}. Consecutive losses: {consecutive_losses}. Total losses: {total_losses}")
                else:
                    print("Trade ended with no profit/loss.")
                    
                is_trade_open = False
                
                current_balance = get_balance(ws)
                update_stats_in_db(user_email_for_bot, total_wins, total_losses, current_amount, consecutive_losses, initial_balance)

                if (current_balance - initial_balance) >= tp_target:
                    print(f"🎉 Take Profit target (${tp_target}) reached. Stopping bot.")
                    clear_session_data(user_email_for_bot)
                    scheduler.shutdown(wait=False)
                    return
                
                if consecutive_losses >= max_consecutive_losses:
                    print(f"🔴 Maximum consecutive losses ({max_consecutive_losses}) reached. Stopping bot.")
                    clear_session_data(user_email_for_bot)
                    scheduler.shutdown(wait=False)
                    return
    except Exception as e:
        print(f"\n❌ An error occurred in trading job: {e}")
    finally:
        if 'ws' in locals() and ws.connected:
            ws.close()

# --- Flask Routes ---
@app.route('/', methods=['GET', 'POST'])
def login():
    global user_email_for_bot
    error_message = None
    if request.method == 'POST':
        user_email = request.form['email'].lower()
        
        if not os.path.exists("user_ids.txt"):
            return render_template_string(LOGIN_HTML, error_message="❌ Error: 'user_ids.txt' file not found.")
        
        with open("user_ids.txt", "r") as f:
            valid_emails = [line.strip().lower() for line in f.readlines()]
            if user_email not in valid_emails:
                error_message = "❌ Sorry, you do not have access to this account."
            else:
                session['user_email'] = user_email
                user_email_for_bot = user_email
                print(f"User {user_email_for_bot} logged in and trading job scheduled.")
                return redirect(url_for('dashboard'))
    
    return render_template_string(LOGIN_HTML, error_message=error_message)

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if 'user_email' not in session:
        return redirect(url_for('login'))
    
    user_email = session['user_email']
    stats = load_stats_from_db(user_email)

    if not stats or stats["initial_balance"] == 0:
        if request.method == 'POST':
            user_token = request.form['user_token']
            base_amount = float(request.form['base_amount'])
            tp_target = float(request.form['tp_target'])
            max_consecutive_losses = int(request.form['max_consecutive_losses'])
            
            settings = {
                "user_token": user_token,
                "base_amount": base_amount,
                "tp_target": tp_target,
                "max_consecutive_losses": max_consecutive_losses
            }
            save_settings_and_start_session(user_email, settings)
            stats = load_stats_from_db(user_email)
            profit = 0
            return render_template_string(STATS_HTML, stats=stats, profit=profit)
        return render_template_string(SETTINGS_HTML)
    
    profit = stats['current_amount'] - stats['initial_balance'] if stats['initial_balance'] is not None else 0
    return render_template_string(STATS_HTML, stats=stats, profit=profit)

# --- Scheduler Setup ---
scheduler = BackgroundScheduler()
# Schedule the job to run at the 58th second of every minute
scheduler.add_job(func=run_trading_job, trigger='cron', second=58)

# The scheduler will run when the app starts
if __name__ == "__main__":
    create_table_if_not_exists()
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())
    app.run(host='0.0.0.0', port=5000, debug=True)
