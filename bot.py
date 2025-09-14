import time
import websocket
import json
import decimal
import sqlalchemy as sa
from sqlalchemy import create_engine
from datetime import datetime
import threading
import os
import pandas as pd
from flask import Flask, request

# --- Flask server to keep the bot alive for Uptimerobot ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Trading bot is running!", 200

# --- PostgreSQL Database Configuration ---
# رابط قاعدة البيانات
DATABASE_URL = "postgresql://hesba_user:EAMYdltUnfFJTz46ccq9ZoCgIU4k1Jib@dpg-d33e7mumcj7s73aail50-a/hesba"

if not DATABASE_URL:
    print("❌ DATABASE_URL is not configured. Exiting.")
    exit()

# Reformat the URL for SQLAlchemy
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://")

# Create a connection engine
try:
    engine = create_engine(DATABASE_URL)
except Exception as e:
    print(f"❌ Error connecting to the database: {e}")
    exit()

trading_lock = threading.Lock()

# --- Database & Utility Functions ---
def update_is_running_status(email, status):
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("UPDATE sessions SET is_running = :status WHERE email = :email"),
            {"status": status, "email": email})
            conn.commit()
        print(f"✅ Bot status for {email} updated to {'running' if status == 1 else 'stopped'}.")
    except Exception as e:
        print(f"❌ Error updating bot status for {email}: {e}")

def clear_session_data(email):
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("DELETE FROM sessions WHERE email=:email"),
            {"email": email})
            conn.commit()
        print(f"✅ Session for {email} deleted successfully.")
    except Exception as e:
        print(f"❌ Error deleting session from database: {e}")

def get_session_status_from_db(email):
    try:
        with engine.connect() as conn:
            result = conn.execute(sa.text("SELECT * FROM sessions WHERE email=:email"),
            {"email": email}).fetchone()
            if result:
                return result._asdict()
            return None
    except Exception as e:
        print(f"❌ Error fetching session from database: {e}")
        return None

def get_all_active_sessions():
    sessions = []
    try:
        with engine.connect() as conn:
            result = conn.execute(sa.text("SELECT * FROM sessions WHERE is_running = 1"))
            for row in result:
                sessions.append(row._asdict())
    except Exception as e:
        print(f"❌ Error fetching all sessions from database: {e}")
    return sessions

def update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=None, contract_id=None, trade_start_time=None):
    try:
        with engine.connect() as conn:
            update_query = sa.text("""
            UPDATE sessions SET 
                total_wins = :total_wins, total_losses = :total_losses, current_amount = :current_amount, consecutive_losses = :consecutive_losses, 
                initial_balance = COALESCE(:initial_balance, initial_balance), contract_id = :contract_id, trade_start_time = COALESCE(:trade_start_time, trade_start_time)
            WHERE email = :email
            """)
            conn.execute(update_query,
                {"total_wins": total_wins, "total_losses": total_losses, "current_amount": current_amount, "consecutive_losses": consecutive_losses, "initial_balance": initial_balance, "contract_id": contract_id, "trade_start_time": trade_start_time, "email": email})
            conn.commit()
        print(f"✅ Stats for {email} updated successfully.")
    except Exception as e:
        print(f"❌ Error updating session in database: {e}")

# --- WebSocket Helper Functions ---
def connect_websocket(user_token):
    ws = websocket.WebSocket()
    try:
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929")
        auth_req = {"authorize": user_token}
        ws.send(json.dumps(auth_req))
        auth_response = json.loads(ws.recv())
        if auth_response.get('error'):
            print(f"❌ Authentication failed: {auth_response['error']['message']}")
            ws.close()
            return None
        return ws
    except Exception as e:
        print(f"❌ WebSocket connection or authentication failed: {e}")
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
        print(f"❌ Error fetching balance: {e}")
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
        print(f"❌ Error checking contract status: {e}")
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
        print(f"❌ Error placing order: {e}")
        return {"error": {"message": "Order placement failed."}}

# --- Trading Bot Logic ---
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
            print(f"\n❌ An unexpected error occurred while processing pending contract for user {email}: {e}")
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
                    print(f"❌ Failed to fetch balance for user {email}. Skipping trade job.")
                    return
                if initial_balance == 0:
                    initial_balance = float(balance)
                    update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=contract_id)
                req = {"ticks_history": "R_100", "end": "latest", "count": 5, "style": "ticks"}
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
                            "duration": 15, "duration_unit": "s", "symbol": "R_100"
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
                                print(f"❌ User {email}: Failed to place order. Response: {order_response}")
                        else:
                            print(f"❌ User {email}: Failed to get proposal. Response: {proposal_response}")
                else:
                    print(f"❌ User {email}: Failed to get tick data.")
            except Exception as e:
                print(f"\n❌ An unexpected error occurred in the trading job for user {email}: {e}")
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
                        if (time.time() - trade_start_time) >= 20: 
                            run_trading_job_for_user(latest_session_data, check_only=True)
                    
                    elif now.second == 58:
                        re_checked_session_data = get_session_status_from_db(email)
                        if re_checked_session_data and not re_checked_session_data.get('contract_id'):
                            run_trading_job_for_user(re_checked_session_data, check_only=False)
            
            time.sleep(1) 
        except Exception as e:
            print(f"❌ حدث خطأ غير متوقع في الحلقة الرئيسية: {e}")
            time.sleep(5)

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    bot_loop()
