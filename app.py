import time
import websocket
import json
import pandas as pd
from datetime import datetime
import psycopg2
import os
import sys
from flask import Flask, request, render_template_string, redirect, url_for
import threading

# --- Database Connection Details ---
# قم باستبدال هذا الرابط برابط الاتصال الخاص بك
DB_URI = "postgresql://bestan_user:gTJKgsCRwEu9ijNMD9d3IMxFcW5TAdE0@dpg-d329ao2dbo4c73a92kng-a.oregon-postgres.render.com/bestan" 

# --- Flask App Setup ---
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'a_very_secret_key_for_flask_development') # استخدم مفتاح سري آمن

# --- Database Functions ---
def get_db_connection():
    try:
        return psycopg2.connect(DB_URI)
    except Exception as e:
        print(f"❌ Failed to connect to the database: {e}")
        return None

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
                # Save settings and reset stats for the new session
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
                print("✅ Session data cleared from the database.")
            except Exception as e:
                print(f"❌ Error clearing session data: {e}")
            finally:
                conn.close()

# --- Helper Functions for Trading Logic ---
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

# --- Trading Bot Core Logic (Runs in a separate thread) ---
def run_trading_bot(user_email, settings):
    user_token = settings["user_token"]
    base_amount = settings["base_amount"]
    tp_target = settings["tp_target"]
    max_consecutive_losses = settings["max_consecutive_losses"]

    stats = load_stats_from_db(user_email)
    if not stats or stats["initial_balance"] == 0:
        print("\n⚙️ Bot: Starting a new session. Initializing stats.")
        # These will be loaded again to ensure we have the latest for the new session
        total_wins = 0
        total_losses = 0
        current_amount = base_amount
        consecutive_losses = 0
        initial_balance = 0 # Will be set from live balance
    else:
        print("\n✅ Bot: Resuming an existing session.")
        total_wins = stats["total_wins"]
        total_losses = stats["total_losses"]
        current_amount = stats["current_amount"]
        consecutive_losses = stats["consecutive_losses"]
        initial_balance = stats["initial_balance"]

    is_trade_open = False
    contract_id = None
    
    print(f"🚀 Trading bot for {user_email} is starting...")

    try:
        ws = websocket.WebSocket()
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        auth_req = {"authorize": user_token}
        ws.send(json.dumps(auth_req))
        auth_response = json.loads(ws.recv())

        if auth_response.get('error'):
            print(f"❌ Bot Auth failed: {auth_response['error']['message']}")
            return # Stop bot thread

        current_balance = get_balance(ws)
        if initial_balance == 0 or initial_balance is None:
            initial_balance = current_balance
            update_stats_in_db(user_email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance)
        
        while True: # This loop runs continuously while the bot thread is alive
            now = datetime.now()
            seconds_to_wait = 60 - now.second
            
            if seconds_to_wait <= 2:
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
                            "currency": "USD",
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
                                print(f"✅ Bot: Placed a {contract_type} trade for ${current_amount:.2f}")
                                contract_id = order_response['buy']['contract_id']
            
            elif is_trade_open:
                time.sleep(20)
                contract_info = check_contract_status(ws, contract_id)

                if contract_info and contract_info.get('is_sold'):
                    profit = contract_info.get('profit', 0)
                    
                    if profit > 0:
                        consecutive_losses = 0
                        total_wins += 1
                        current_amount = base_amount
                        print(f"🎉 Bot: WIN! Profit: ${profit:.2f}. Total wins: {total_wins}")
                    elif profit < 0:
                        consecutive_losses += 1
                        total_losses += 1
                        next_bet = current_amount * 2.2
                        current_amount = max(base_amount, next_bet)
                        print(f"🔻 Bot: LOSS! New amount: ${current_amount:.2f}. Consecutive losses: {consecutive_losses}. Total losses: {total_losses}")
                    else:
                        print("Bot: Trade ended with no profit/loss.")
                        
                    is_trade_open = False
                    
                    current_balance = get_balance(ws)
                    update_stats_in_db(user_email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance)

                    # Check for TP/SL conditions and if bot should stop
                    if (current_balance - initial_balance) >= tp_target:
                        print(f"🎉 Bot: Take Profit target (${tp_target}) reached. Stopping bot and clearing session data.")
                        clear_session_data(user_email)
                        ws.close() # Close websocket
                        return # Stop the bot thread
                    
                    elif consecutive_losses >= max_consecutive_losses:
                        print(f"🔴 Bot: Maximum consecutive losses ({max_consecutive_losses}) reached. Stopping bot and clearing session data.")
                        clear_session_data(user_email)
                        ws.close() # Close websocket
                        return # Stop the bot thread
            
            time.sleep(1) # Prevent busy-waiting

    except Exception as e:
        print(f"❌ Bot Error: {e}")
        if 'ws' in locals() and ws.connected:
            ws.close()
    finally:
        # Ensure cleanup if thread exits unexpectedly
        if 'ws' in locals() and ws.connected:
            ws.close()

# --- Flask Routes ---
@app.route('/')
def index():
    # Check if user_ids.txt exists
    if not os.path.exists("user_ids.txt"):
        return "<h1>Error: user_ids.txt file not found. Please create it.</h1>", 500

    # Read valid emails
    with open("user_ids.txt", "r") as f:
        valid_emails = [line.strip().lower() for line in f.readlines()]
    
    return render_template_string("""
        <!doctype html>
        <html>
        <head>
            <title>KhouryBot Autotrading Setup</title>
            <style>
                body { font-family: sans-serif; background-color: #f4f4f4; color: #333; margin: 20px; }
                .container { max-width: 600px; margin: auto; background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }
                h1 { color: #0056b3; text-align: center; }
                label { display: block; margin-bottom: 5px; font-weight: bold; }
                input[type="text"], input[type="password"], input[type="number"] {
                    width: calc(100% - 22px); padding: 10px; margin-bottom: 15px; border: 1px solid #ccc; border-radius: 4px;
                }
                button {
                    background-color: #28a745; color: white; padding: 10px 15px; border: none; border-radius: 4px; cursor: pointer; font-size: 16px;
                }
                button:hover { background-color: #218838; }
                .error { color: red; margin-top: 10px; }
                .welcome { text-align: center; font-size: 24px; margin-bottom: 20px; color: #007bff; font-weight: bold;}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="welcome">Welcome with KHOURYBOT Autotrading</div>
                <h1>Bot Setup</h1>
                <form action="{{ url_for('setup_bot') }}" method="post">
                    {% if error %}
                        <p class="error">{{ error }}</p>
                    {% endif %}
                    <label for="email">Email:</label>
                    <input type="text" id="email" name="email" required>
                    
                    <label for="user_token">Deriv API Token:</label>
                    <input type="password" id="user_token" name="user_token" required>
                    
                    <label for="base_amount">Base Amount ($):</label>
                    <input type="number" step="0.01" id="base_amount" name="base_amount" required>
                    
                    <label for="tp_target">Take Profit Target ($):</label>
                    <input type="number" step="0.01" id="tp_target" name="tp_target" required>
                    
                    <label for="max_consecutive_losses">Max Consecutive Losses:</label>
                    <input type="number" id="max_consecutive_losses" name="max_consecutive_losses" required>
                    
                    <button type="submit">Start Bot</button>
                </form>
            </div>
        </body>
        </html>
    """, valid_emails=valid_emails)

@app.route('/setup_bot', methods=['POST'])
def setup_bot():
    email = request.form['email'].lower()
    user_token = request.form['user_token']
    base_amount = float(request.form['base_amount'])
    tp_target = float(request.form['tp_target'])
    max_consecutive_losses = int(request.form['max_consecutive_losses'])

    # Validate email
    if not os.path.exists("user_ids.txt"):
        return render_template_string("<h1>Error: user_ids.txt file not found.</h1>", error="user_ids.txt not found.")
    
    with open("user_ids.txt", "r") as f:
        valid_emails = [line.strip().lower() for line in f.readlines()]
    
    if email not in valid_emails:
        return render_template_string("""
            <!doctype html>
            <html><body><h1>Error: Your email is not activated. Please contact support.</h1><a href="/">Go back</a></body></html>
        """, error="Your email is not activated.")

    # Check if bot is already running for this email
    # This is a basic check, a more robust solution might involve a separate status tracking mechanism
    if any(thread.is_alive() for thread in threading.enumerate() if f"Bot:{email}" in thread.name):
        return render_template_string("""
            <!doctype html>
            <html><body><h1>Bot is already running for this email.</h1><a href="/">Go back</a></body></html>
        """)

    settings = {
        "user_token": user_token,
        "base_amount": base_amount,
        "tp_target": tp_target,
        "max_consecutive_losses": max_consecutive_losses
    }
    
    save_settings_and_start_session(email, settings)
    
    # Start the bot in a separate thread
    bot_thread = threading.Thread(target=run_trading_bot, args=(email, settings), name=f"Bot:{email}")
    bot_thread.daemon = True # Allows the main Flask app to exit even if this thread is running
    bot_thread.start()
    
    return render_template_string("""
        <!doctype html>
        <html>
        <head>
            <title>KhouryBot - Bot Running</title>
            <style>
                body { font-family: sans-serif; background-color: #f4f4f4; color: #333; margin: 20px; }
                .container { max-width: 600px; margin: auto; background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1); text-align: center;}
                h1 { color: #28a745; }
                .welcome { font-size: 24px; margin-bottom: 20px; color: #007bff; font-weight: bold;}
                .status { font-size: 18px; margin-bottom: 20px; }
                a { color: #007bff; text-decoration: none; }
                a:hover { text-decoration: underline; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="welcome">Welcome with KHOURYBOT Autotrading</div>
                <h1>Bot Setup Successful!</h1>
                <p class="status">Your bot has started for email: <strong>{{ email }}</strong>.</p>
                <p class="status">You can close this tab or window. The bot will continue running in the background.</p>
                <p><a href="/">Go back to setup</a></p>
            </div>
        </body>
        </html>
    """, email=email)

# --- Entry Point ---
if __name__ == '__main__':
    # Check if running in a non-interactive environment (like Render)
    if not sys.stdin.isatty(): # If not a TTY, assume it's a server environment
        # Load base amount from DB for the default email if possible, for a quick start
        # Or require a default email/token via env vars if no user interaction is allowed at all.
        # For simplicity, we will just rely on the user to set up the first time via Flask UI.
        print("Running in server mode. Flask app will start.")
        # The bot logic itself is now in the 'run_trading_bot' function which is called by Flask.
        # The main Flask app runs and waits for requests.
        # We don't need a separate 'run_bot_engine_cli()' call here for server deployments.
        # You might want to configure Flask to listen on 0.0.0.0 for external access.
        # For Render, it often sets PORT automatically.
        port = int(os.environ.get("PORT", 8080)) # Use Render's PORT or default to 8080
        app.run(host='0.0.0.0', port=port) 
    else:
        # This part is for local development if you want to run it as a command-line script
        # Currently not the primary use case for server deployment.
        print("Running in interactive mode (e.g., local development).")
        print("Consider using Flask app.run() for server deployments.")
        # You would need to adapt this part if you want to keep the CLI option for local testing.
        # For now, we focus on the server deployment with Flask.
        pass # Or implement a CLI runner if needed for local tests
