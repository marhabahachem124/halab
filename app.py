import time
import websocket
import json
import pandas as pd
from datetime import datetime
import psycopg2
import os
import sys
from flask import Flask, render_template_string, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = 'your_super_secret_key'  # قم بتغيير هذا إلى مفتاح سري خاص بك

# --- Database Connection Details ---
# قم باستبدال هذا الرابط برابط الاتصال الخاص بك
DB_URI = "postgresql://user:password@host:port/dbname" 

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
            <li>Initial Balance: ${{ stats['initial_balance'] }}</li>
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

def get_current_balance_and_calculate_profit(email, initial_balance):
    try:
        ws = websocket.WebSocket()
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=5)
        
        settings = load_settings_from_db(email)
        if not settings: return 0
        
        auth_req = {"authorize": settings['user_token']}
        ws.send(json.dumps(auth_req))
        auth_response = json.loads(ws.recv())
        if auth_response.get('error'): return 0

        current_balance = get_balance(ws)
        if current_balance is not None:
            return current_balance - initial_balance
    except Exception as e:
        return 0
    finally:
        if 'ws' in locals() and ws.connected:
            ws.close()
    return 0

# --- Flask Routes ---
@app.route('/', methods=['GET', 'POST'])
def login():
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
            profit = stats['initial_balance'] - stats['initial_balance']
            return render_template_string(STATS_HTML, stats=stats, profit=profit)

        return render_template_string(SETTINGS_HTML)
    
    profit = get_current_balance_and_calculate_profit(user_email, stats['initial_balance'])
    return render_template_string(STATS_HTML, stats=stats, profit=profit)

if __name__ == "__main__":
    create_table_if_not_exists()
    app.run(host='0.0.0.0', port=5000, debug=True)
