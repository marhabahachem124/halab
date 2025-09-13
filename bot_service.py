import time
import websocket
import json
import pandas as pd
from datetime import datetime
import psycopg2
import sys
import os

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
        try:
            with conn.cursor() as cur:
                # Added COALESCE for initial_balance to handle potential NULLs gracefully
                cur.execute("SELECT email, user_token, base_amount, tp_target, max_consecutive_losses, total_wins, total_losses, current_amount, consecutive_losses, COALESCE(initial_balance, 0.0), contract_id FROM user_settings;")
                active_sessions = cur.fetchall()
                return active_sessions
        except Exception as e:
            print(f"❌ Error fetching active sessions: {e}")
            return []
        finally:
            conn.close()
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
        except Exception as e:
            print(f"❌ Error updating stats for {email}: {e}")
        finally:
            conn.close()

def clear_session_data(email):
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_settings WHERE email = %s", (email,))
                conn.commit()
                print(f"✅ Session data cleared for {email}.")
        except Exception as e:
            print(f"❌ Error clearing session data for {email}: {e}")
        finally:
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
    except Exception as e:
        print(f"❌ Error getting balance and currency: {e}")
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
        print(f"❌ Order placement failed: {e}")
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
        if initial_balance == 0: # Use 0 to check if it was ever set
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
                    print(f"🎉 Take Profit target (${tp_target}) reached. Stopping bot for {email}.")
                    clear_session_data(email)
                    return
                
                if consecutive_losses >= max_consecutive_losses:
                    print(f"🔴 Maximum consecutive losses ({max_consecutive_losses}) reached for {email}. Stopping bot.")
                    clear_session_data(email)
                    return
        
        if not contract_id:
            now = datetime.now()
            if now.second >= 58: # Check for trade placement in the last 2 seconds of the minute
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
                                print(f"✅ Placed a {contract_type} trade for ${current_amount:.2f} for {email}")
    except Exception as e:
        print(f"\n❌ An error occurred in trading job for {email}: {e}")
        # Attempt to clear session if connection issues persist, to prevent infinite loop of errors for this user
        if "connection refused" in str(e).lower() or "timed out" in str(e).lower():
            print(f"Attempting to clear session for {email} due to connection error.")
            clear_session_data(email)
    finally:
        if 'ws' in locals() and ws.connected:
            ws.close()

if __name__ == "__main__":
    print("🚀 Bot Service Started. Waiting for sessions to become active in the database...")
    while True:
        active_sessions = get_active_sessions()
        if active_sessions:
            for session in active_sessions:
                # Pass session data to the function
                run_trading_job_for_user(session)
        else:
            print("😴 No active sessions found. Sleeping for 10 seconds...")
        time.sleep(10) # Sleep to prevent excessive database calls
