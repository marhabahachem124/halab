import time
import websocket
import json
import pandas as pd
from datetime import datetime
import psycopg2
import os
import threading
from flask import Flask, request, jsonify

# --- Database Connection Details ---
DB_URI = os.environ.get("DATABASE_URL", "postgresql://ihom_user:M0AybLPpyZl4a4QDdAEHB7dsrXZ9GEUq@dpg-d32mngqdbo4c73aiu4v0-a.oregon-postgres.render.com/ihom")

# --- Flask App Setup ---
app = Flask(__name__)

# --- Database Functions (No changes here) ---
def get_db_connection():
    try:
        return psycopg2.connect(DB_URI)
    except Exception as e:
        print(f"❌ Error connecting to database: {e}")
        return None

def start_new_session_in_db(email, settings):
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
                print(f"❌ Error saving settings to database: {e}")
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
            print(f"❌ An error occurred while fetching active sessions: {e}")
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
            print(f"❌ An error occurred while updating database for {email}: {e}")

def clear_session_data(email):
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_settings WHERE email = %s", (email,))
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"❌ An error occurred while clearing session data for {email}: {e}")

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
        print(f"❌ Error getting balance: {e}")
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
        print(f"❌ Error placing order: {e}")
        return {"error": {"message": "Order placement failed."}}

def check_contract_status(ws, contract_id):
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv()) 
        return response.get('proposal_open_contract')
    except Exception as e:
        print(f"❌ Error checking contract status: {e}")
        return None

def run_trading_job_for_user(session_data):
    try:
        email, user_token, base_amount, tp_target, max_consecutive_losses, total_wins, total_losses, current_amount, consecutive_losses, initial_balance, contract_id = session_data

        print(f"🔄 **STARTING JOB FOR** {email}...")

        ws = None
        try:
            print("🚀 Attempting to connect to Deriv...")
            ws = websocket.WebSocket()
            ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
            auth_req = {"authorize": user_token}
            ws.send(json.dumps(auth_req))
            auth_response = json.loads(ws.recv())
            print(f"✅ Authorization response for {email}: {auth_response.get('msg_type')}")

            if auth_response.get('error'):
                print(f"❌ Auth failed for {email}: {auth_response['error']['message']}")
                clear_session_data(email)
                return
        except Exception as e:
            print(f"❌ Connection or Authorization Failed for {email}: {e}")
            return # Stop the job for this user if connection fails

        balance, currency = get_balance_and_currency(ws)
        if initial_balance is None or initial_balance == 0:
            initial_balance = balance
            update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=contract_id)
        
        print(f"📊 Current balance for {email}: {balance} {currency}")
        
        if contract_id:
            print(f"🔎 Checking status for open contract: {contract_id}")
            contract_info = check_contract_status(ws, contract_id)
            if contract_info and contract_info.get('is_sold'):
                profit = contract_info.get('profit', 0)
                if profit > 0:
                    consecutive_losses = 0
                    total_wins += 1
                    current_amount = base_amount
                    print(f"🎉 WIN! Profit: ${profit:.2f}. Total wins: {total_wins}")
                else:
                    consecutive_losses += 1
                    total_losses += 1
                    next_bet = current_amount * 2.2
                    current_amount = max(base_amount, next_bet)
                    print(f"🔻 LOSS! New amount: ${current_amount:.2f}. Consecutive losses: {consecutive_losses}. Total losses: {total_losses}")
                contract_id = None
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=contract_id)
                print("✅ Contract closed. Updated stats and reset contract_id.")

                if (balance - initial_balance) >= tp_target:
                    print(f"🎉 Take Profit target (${tp_target}) reached. Stopping bot.")
                    clear_session_data(email)
                    return
                
                if consecutive_losses >= max_consecutive_losses:
                    print(f"🔴 Maximum consecutive losses ({max_consecutive_losses}) reached. Stopping bot.")
                    clear_session_data(email)
                    return
        
        if not contract_id:
            print("📈 No open contract found. Attempting to get ticks and analyze...")
            req = {"ticks_history": "R_100", "end": "latest", "count": 5, "style": "ticks"}
            ws.send(json.dumps(req))
            
            tick_data = None
            while not tick_data:
                response = json.loads(ws.recv())
                if response.get('msg_type') == 'history':
                    tick_data = response
                elif response.get('msg_type') != 'tick':
                    print(f"ℹ️ Received non-history message: {response.get('msg_type')}. Waiting for 'history'...")
            
            print(f"📈 Raw ticks data received. Content size: {len(str(tick_data))}")
            
            if 'history' in tick_data and 'prices' in tick_data['history']:
                ticks = tick_data['history']['prices']
                df_ticks = pd.DataFrame({'price': ticks})
                signal, _ = analyse_data(df_ticks)
                
                print(f"📊 Analysis complete. Signal: {signal}")
                
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
                    print(f"💰 Sending proposal request for {contract_type} trade...")
                    ws.send(json.dumps(proposal_req))
                    proposal_response = json.loads(ws.recv())
                    
                    if 'proposal' in proposal_response:
                        proposal_id = proposal_response['proposal']['id']
                        print(f"✅ Proposal received. Placing order...")
                        order_response = place_order(ws, proposal_id, current_amount)
                        if 'buy' in order_response and 'contract_id' in order_response['buy']:
                            contract_id = order_response['buy']['contract_id']
                            update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=contract_id)
                            print(f"✅ Placed a {contract_type} trade for ${current_amount:.2f}")
                        else:
                            print(f"❌ Failed to place order. Response: {order_response}")
                    else:
                        print(f"❌ Proposal failed. Response: {proposal_response}")
            else:
                print("❌ Failed to get ticks history or ticks data is empty.")
    except Exception as e:
        print(f"\n❌ An unhandled error occurred in trading job for {email}: {e}")
    finally:
        if ws and ws.connected:
            ws.close()
            print(f"🔗 Websocket connection closed for {email}.")

def bot_loop():
    while True:
        try:
            now = datetime.now()
            if now.second >= 58:
                print(f"⏰ It's {now.strftime('%H:%M:%S')}, checking for active sessions...")
                active_sessions = get_all_active_sessions()
                print(f"📋 Found {len(active_sessions)} active session(s).")
                if active_sessions:
                    for session in active_sessions:
                        run_trading_job_for_user(session)
                else:
                    print("😴 No active sessions found. Sleeping for 1 second...")
                time.sleep(1)
            else:
                time.sleep(0.1)
        except Exception as e:
            print(f"❌ An error occurred in the main bot loop: {e}")
            # This is a critical failure. Wait and then try to continue.
            time.sleep(5)

# --- Flask Endpoints for Streamlit communication ---
@app.route('/start_bot', methods=['POST'])
def start_bot():
    data = request.json
    email = data.get('email')
    settings = data.get('settings')
    if not email or not settings:
        return jsonify({"status": "error", "message": "Invalid data."}), 400
    
    success = start_new_session_in_db(email, settings)
    if success:
        return jsonify({"status": "success", "message": "Bot session started."}), 200
    else:
        return jsonify({"status": "error", "message": "Failed to start bot session in database."}), 500

@app.route('/stop_bot', methods=['POST'])
def stop_bot():
    data = request.json
    email = data.get('email')
    if not email:
        return jsonify({"status": "error", "message": "Invalid data."}), 400
    
    clear_session_data(email)
    return jsonify({"status": "success", "message": "Bot session stopped."}), 200

@app.route('/get_stats', methods=['POST'])
def get_stats():
    data = request.json
    email = data.get('email')
    if not email:
        return jsonify({"status": "error", "message": "Invalid data."}), 400
    
    stats = get_session_status_from_db(email)
    if stats:
        return jsonify({"status": "success", "stats": stats}), 200
    else:
        return jsonify({"status": "error", "message": "No active session."}), 404

# --- Main execution block ---
if __name__ == "__main__":
    bot_thread = threading.Thread(target=bot_loop)
    bot_thread.daemon = True
    bot_thread.start()
    app.run(host='0.0.0.0', port=os.environ.get('PORT', 8080))
