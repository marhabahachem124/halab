import time
import websocket
import json
import pandas as pd
from datetime import datetime
import psycopg2
import os
import sys

# --- Database Connection Details ---
# قم باستبدال هذا الرابط برابط الاتصال الخاص بك
DB_URI = "postgresql://user:password@host:port/dbname" 

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

# --- Main Logic ---
def run_bot_engine_cli():
    # 1. Ask for user email and authenticate
    user_email = input("👋 Enter your registered email to continue: ").lower()

    if not os.path.exists("user_ids.txt"):
        print("❌ Error: 'user_ids.txt' file not found. Please create it and add your email.")
        return

    with open("user_ids.txt", "r") as f:
        valid_emails = [line.strip().lower() for line in f.readlines()]
        if user_email not in valid_emails:
            print("❌ Your email is not activated. Please contact support.")
            return

    # 2. Check for existing session or start a new one
    stats = load_stats_from_db(user_email)
    
    if not stats or stats["initial_balance"] == 0:
        print("\n⚙️ Starting a new session. Please configure the bot:")
        user_token = input("Enter your Deriv API token: ")
        base_amount = float(input("Enter Base Amount ($): "))
        tp_target = float(input("Enter Take Profit Target ($): "))
        max_consecutive_losses = int(input("Enter Max Consecutive Losses: "))
        
        settings = {
            "user_token": user_token,
            "base_amount": base_amount,
            "tp_target": tp_target,
            "max_consecutive_losses": max_consecutive_losses
        }
        save_settings_and_start_session(user_email, settings)
        stats = load_stats_from_db(user_email)
    else:
        print("\n✅ Resuming an existing session.")
        settings = load_settings_from_db(user_email)
        
    # 3. Load settings and stats to start the bot
    user_token = settings["user_token"]
    base_amount = settings["base_amount"]
    tp_target = settings["tp_target"]
    max_consecutive_losses = settings["max_consecutive_losses"]

    total_wins = stats["total_wins"]
    total_losses = stats["total_losses"]
    current_amount = stats["current_amount"]
    consecutive_losses = stats["consecutive_losses"]
    initial_balance = stats["initial_balance"]

    is_trade_open = False
    contract_id = None
    
    print("\n🚀 Bot is starting. Press Ctrl+C to stop.")

    try:
        ws = websocket.WebSocket()
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        auth_req = {"authorize": user_token}
        ws.send(json.dumps(auth_req))
        auth_response = json.loads(ws.recv())

        if auth_response.get('error'):
            print(f"❌ Auth failed: {auth_response['error']['message']}")
            return

        current_balance = get_balance(ws)
        if initial_balance == 0 or initial_balance is None:
            initial_balance = current_balance
            update_stats_in_db(user_email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=initial_balance)
        
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            print("📈 Welcome with KHOURYBOT Autotrading")
            print("-" * 40)
            print("📊 Trading Stats:")
            print(f"  - Total Wins: {total_wins}")
            print(f"  - Total Losses: {total_losses}")
            print(f"  - Consecutive Losses: {consecutive_losses}")
            print(f"  - Current Martingale Amount: ${current_amount:.2f}")
            print(f"  - Current Balance: ${current_balance:.2f}")
            profit = current_balance - initial_balance
            print(f"  - Total P/L: ${profit:.2f}")
            print("-" * 40)

            if not is_trade_open:
                now = datetime.now()
                seconds_to_wait = 60 - now.second
                print(f"🔄 Bot Status: Analysing... Waiting for the next minute ({seconds_to_wait}s)")

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
                                    print(f"✅ Placed a {contract_type} trade for ${current_amount:.2f}")
                                    contract_id = order_response['buy']['contract_id']
            
            elif is_trade_open:
                print("⏳ Waiting for trade result...")
                time.sleep(20)
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
                    update_stats_in_db(user_email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance)

                    if (current_balance - initial_balance) >= tp_target:
                        print(f"🎉 Take Profit target (${tp_target}) reached. Stopping bot.")
                        clear_session_data(user_email)
                        break
                    
                    if consecutive_losses >= max_consecutive_losses:
                        print(f"🔴 Maximum consecutive losses ({max_consecutive_losses}) reached. Stopping bot.")
                        clear_session_data(user_email)
                        break
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user.")
        clear_session_data(user_email)
    except Exception as e:
        print(f"\n❌ An error occurred: {e}")
    finally:
        if 'ws' in locals() and ws.connected:
            ws.close()

if __name__ == "__main__":
    run_bot_engine_cli()
