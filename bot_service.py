import time
import websocket
import json
import pandas as pd
from datetime import datetime
import psycopg2
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- Database Connection Details ---
DB_URI = "postgresql://ihom_user:M0AybLPpyZl4a4QDdAEHB7dsrXZ9GEUq@dpg-d32mngqdbo4c73aiu4v0-a.oregon-postgres.render.com/ihom"

# --- Database Functions ---
def get_db_connection():
    try:
        return psycopg2.connect(DB_URI)
    except Exception as e:
        print(f"❌ Error connecting to database: {e}")
        return None

def get_active_sessions():
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            cur.execute("SELECT email, user_token, base_amount, tp_target, max_consecutive_losses, total_wins, total_losses, current_amount, consecutive_losses, initial_balance, contract_id FROM user_settings;")
            active_sessions = cur.fetchall()
            conn.close()
            return active_sessions
    return []

def update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=None, contract_id=None):
    conn = get_db_connection()
    if conn:
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

def clear_session_data(email):
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_settings WHERE email = %s", (email,))
            conn.commit()
            conn.close()

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
    except Exception:
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

def run_trading_job_for_user(session_data):
    try:
        email, user_token, base_amount, tp_target, max_consecutive_losses, total_wins, total_losses, current_amount, consecutive_losses, initial_balance, contract_id = session_data

        ws = websocket.WebSocket()
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        auth_req = {"authorize": user_token}
        ws.send(json.dumps(auth_req))
        auth_response = json.loads(ws.recv())

        if auth_response.get('error'):
            print(f"❌ Auth failed for {email}: {auth_response['error']['message']}")
            clear_session_data(email)
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
                    print(f"🎉 WIN! Profit: ${profit:.2f}. Total wins: {total_wins}")
                else:
                    consecutive_losses += 1
                    total_losses += 1
                    next_bet = current_amount * 2.2
                    current_amount = max(base_amount, next_bet)
                    print(f"🔻 LOSS! New amount: ${current_amount:.2f}. Consecutive losses: {consecutive_losses}. Total losses: {total_losses}")
                contract_id = None
                update_stats_and_trade_info_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance, contract_id=contract_id)

                if (balance - initial_balance) >= tp_target:
                    print(f"🎉 Take Profit target (${tp_target}) reached. Stopping bot.")
                    clear_session_data(email)
                    return
                
                if consecutive_losses >= max_consecutive_losses:
                    print(f"🔴 Maximum consecutive losses ({max_consecutive_losses}) reached. Stopping bot.")
                    clear_session_data(email)
                    return
        
        if not contract_id:
            # --- هنا تتم عملية جلب البيانات والتحليل ---
            # سنقوم بجلب البيانات فقط إذا كانت الثانية 58 أو 59
            now = datetime.now()
            if now.second >= 58: 
                req = {"ticks_history": "R_100", "end": "latest", "count": 5, "style": "ticks"}
                ws.send(json.dumps(req))
                tick_data = json.loads(ws.recv())
                if 'history' in tick_data and tick_data['history']['prices']:
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
                            "duration": 15, # مدة الصفقة
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
                                print(f"✅ Placed a {contract_type} trade for ${current_amount:.2f}")
    except Exception as e:
        print(f"\n❌ An error occurred in trading job for {email}: {e}")
    finally:
        if 'ws' in locals() and ws.connected:
            ws.close()

def bot_loop():
    while True:
        now = datetime.now()
        # تحقق بشكل مستمر، وعندما تصل الثانية إلى 58 أو 59، قم بجلب البيانات والتحليل
        if now.second >= 58: 
            active_sessions = get_active_sessions()
            if active_sessions:
                for session in active_sessions:
                    run_trading_job_for_user(session)
            # يمكن أن نترك هنا sleep صغير جداً لتجنب استهلاك المعالج بشكل كامل
            time.sleep(0.1) 
        else:
            # إذا لم تكن الثانية 58، ننام لفترة قصيرة جداً لعدم استهلاك المعالج
            time.sleep(0.1)

class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is running...")

def run_http_server():
    server_address = ('', 8080) # الاستماع على جميع الواجهات على المنفذ 8080
    httpd = HTTPServer(server_address, RequestHandler)
    print("Serving HTTP on port 8080...")
    httpd.serve_forever()

if __name__ == "__main__":
    # تشغيل حلقة البوت في Thread منفصل
    bot_thread = threading.Thread(target=bot_loop)
    bot_thread.daemon = True # السماح للبرنامج بالخروج إذا انتهت الـ main thread
    bot_thread.start()

    # تشغيل خادم الويب HTTP لإبقاء الخدمة نشطة (مهم لـ Render ولـ Uptime Robot)
    run_http_server()
