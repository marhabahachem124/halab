import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey
from datetime import datetime
import json
import uuid
import time
import os
import websocket
import pandas as pd

# --- Database Setup ---
# using the provided database URL directly in the code
DATABASE_URL = "postgresql://bibokh_user:Ric9h1SaTADxdkV0LgNmF8c0RPWhWYzy@dpg-d30mrpogjchc73f1tiag-a.oregon-postgres.render.com/bibokh"
engine = sa.create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base()

class BotSession(Base):
    __tablename__ = 'bot_sessions'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    session_id = Column(String, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    api_token = Column(String, nullable=True)
    base_amount = Column(Float, default=0.5)
    tp_target = Column(Float, nullable=True)
    max_consecutive_losses = Column(Integer, default=5)
    current_amount = Column(Float, default=0.5)
    consecutive_losses = Column(Integer, default=0)
    total_wins = Column(Integer, default=0)
    total_losses = Column(Integer, default=0)
    is_running = Column(Boolean, default=False)
    is_trade_open = Column(Boolean, default=False)
    initial_balance = Column(Float, nullable=True)
    logs = Column(String, default="[]")

# --- Create tables if they don't exist ---
# This is crucial for the first deployment of the bot
Base.metadata.create_all(engine)


# --- Trading Logic Functions ---
def analyse_data(df_ticks):
    if len(df_ticks) < 60:
        return "Neutral", "Insufficient data"
    last_60_ticks = df_ticks.tail(60).copy()
    first_30 = last_60_ticks.iloc[:30]
    last_30 = last_60_ticks.iloc[30:]
    avg_first_30 = first_30['price'].mean()
    avg_last_30 = last_30['price'].mean()
    if avg_last_30 > avg_first_30:
        return "Buy", None
    elif avg_last_30 < avg_first_30:
        return "Sell", None
    else:
        return "Neutral", "No clear trend in the last 60 ticks."

def place_order(ws, proposal_id, amount):
    req = {"buy": proposal_id, "price": round(max(0.5, amount), 2)}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        return response
    except Exception:
        return {"error": {"message": "Order placement failed."}}

def check_contract_status(ws, contract_id):
    # This function needs to subscribe to the contract and wait for a response.
    # For simplicity, we'll assume the response structure for now.
    # In a real implementation, you'd need to manage subscription IDs and wait for specific responses.
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        # In a real scenario, you might need to filter responses by subscription ID
        response = ws.recv() # This might block or require careful handling in a real-time scenario
        return json.loads(response).get('proposal_open_contract')
    except Exception as e:
        print(f"Error checking contract status: {e}")
        return None

def get_balance(ws):
    req = {"balance": 1, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        return response.get('balance', {}).get('balance')
    except Exception as e:
        print(f"Error getting balance: {e}")
        return None

def main_trading_loop(bot_session_id):
    state = {}
    ws = None
    try:
        ws = websocket.WebSocket()
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        
        while True:
            s = Session()
            try:
                bot_session = s.query(BotSession).filter_by(session_id=bot_session_id).first()
                if not bot_session or not bot_session.is_running:
                    print(f"Bot for session {bot_session_id} is stopped or not found. Exiting loop.")
                    break
                
                # Load state from DB
                state = {
                    'api_token': bot_session.api_token,
                    'base_amount': bot_session.base_amount,
                    'tp_target': bot_session.tp_target,
                    'max_consecutive_losses': bot_session.max_consecutive_losses,
                    'current_amount': bot_session.current_amount,
                    'consecutive_losses': bot_session.consecutive_losses,
                    'total_wins': bot_session.total_wins,
                    'total_losses': bot_session.total_losses,
                    'is_running': bot_session.is_running,
                    'is_trade_open': bot_session.is_trade_open,
                    'initial_balance': bot_session.initial_balance,
                    'logs': json.loads(bot_session.logs),
                }

                if not state.get('api_token'):
                    state['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ API Token not set. Please set it in the dashboard.")
                    s.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(state['logs'])})
                    s.commit()
                    time.sleep(10)
                    continue

                # Authorize with the API token
                auth_req = {"authorize": state['api_token']}
                ws.send(json.dumps(auth_req))
                auth_response = json.loads(ws.recv())
                if auth_response.get('error'):
                    state['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Auth failed: {auth_response['error']['message']}")
                    s.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(state['logs'])})
                    s.commit()
                    time.sleep(5)
                    continue

                if not state.get('is_trade_open'):
                    now = datetime.now()
                    # Check if it's time to potentially place a trade (e.g., near the end of a 30-sec interval)
                    if now.second >= 55: # Check last 5 seconds of the minute for ticks history
                        if state['initial_balance'] is None:
                            current_balance = get_balance(ws)
                            if current_balance is not None:
                                state['initial_balance'] = current_balance
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 💰 Initial Balance: {state['initial_balance']:.2f}")
                            else:
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ❌ Failed to get balance.")
                                s.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(state['logs'])})
                                s.commit()
                                time.sleep(5)
                                continue

                        # Fetch historical ticks data
                        req = {"ticks_history": "R_100", "end": "latest", "count": 60, "style": "ticks"}
                        ws.send(json.dumps(req))
                        tick_data = json.loads(ws.recv())
                        
                        if 'history' in tick_data and tick_data['history']['prices']:
                            df_ticks = pd.DataFrame({'price': tick_data['history']['prices']})
                            signal, error_msg = analyse_data(df_ticks)
                            
                            if signal in ['Buy', 'Sell']:
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ➡ Entering a {signal.upper()} trade with {state['current_amount']:.2f}$")
                                contract_type = "CALL" if signal == 'Buy' else "PUT"
                                
                                # Get proposal for the trade
                                proposal_req = {"proposal": 1, "amount": round(state['current_amount'], 2), "basis": "stake", "contract_type": contract_type, "currency": "USD", "duration": 30, "duration_unit": "s", "symbol": "R_100"}
                                ws.send(json.dumps(proposal_req))
                                proposal_response = json.loads(ws.recv())
                                
                                if 'error' in proposal_response:
                                    state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ❌ Proposal failed: {proposal_response['error']['message']}")
                                    s.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(state['logs'])})
                                    s.commit()
                                    time.sleep(5)
                                    continue

                                if 'proposal' in proposal_response and 'id' in proposal_response['proposal']:
                                    proposal_id = proposal_response['proposal']['id']
                                    # Place the order
                                    order_response = place_order(ws, proposal_id, state['current_amount'])
                                    
                                    if 'buy' in order_response and 'contract_id' in order_response['buy']:
                                        state['is_trade_open'] = True
                                        state['contract_id'] = order_response['buy']['contract_id'] # Store contract ID
                                        state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ✅ Order placed. Contract ID: {state['contract_id']}")
                                        s.query(BotSession).filter_by(session_id=bot_session_id).update({"is_trade_open": True, "contract_id": state['contract_id'], "logs": json.dumps(state['logs'])})
                                        s.commit()
                                    else:
                                        state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ❌ Order failed: {order_response.get('error', {}).get('message', 'Unknown error')}")
                                        s.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(state['logs'])})
                                        s.commit()
                                else:
                                    state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ❌ Proposal response missing ID.")
                                    s.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(state['logs'])})
                                    s.commit()
                        else:
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ⚪ No clear signal or insufficient data. Waiting.")
                            s.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(state['logs'])})
                            s.commit()

                elif state.get('is_trade_open'): # If a trade is currently open, check its status
                    now = datetime.now()
                    contract_info = check_contract_status(ws, state.get('contract_id')) # Use stored contract_id

                    if contract_info and contract_info.get('is_sold'): # Trade has ended
                        profit = contract_info.get('profit', 0)
                        
                        # Update win/loss stats and reset for next trade
                        if profit > 0:
                            state['consecutive_losses'] = 0
                            state['current_amount'] = state['base_amount'] # Reset to base amount on win
                            state['total_wins'] += 1
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 🎉 Win! Profit: {profit:.2f}$")
                        else:
                            state['consecutive_losses'] += 1
                            # Martingale logic: double the amount on loss, up to a limit or reasonable factor
                            state['current_amount'] = max(state['base_amount'], state['current_amount'] * 1.5) # Example: 1.5x increase
                            state['total_losses'] += 1
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 💔 Loss! Loss: {profit:.2f}$")
                        
                        state['is_trade_open'] = False
                        state['contract_id'] = None # Clear contract ID

                        # Check for TP/SL conditions
                        current_balance = get_balance(ws)
                        if current_balance is not None:
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 💰 Current Balance: {current_balance:.2f}")
                            # Check Take Profit
                            if state['tp_target'] and (current_balance - state['initial_balance']) >= state['tp_target']:
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 🤑 Take Profit reached! Bot stopped.")
                                state['is_running'] = False
                        
                        # Check Stop Loss
                        if state['consecutive_losses'] >= state['max_consecutive_losses']:
                            state['logs'].append(f"[{now.now().strftime('%H:%M:%S')}] 🛑 Stop Loss hit! Bot stopped.")
                            state['is_running'] = False

                    # Save updated state to DB
                    s.query(BotSession).filter_by(session_id=bot_session_id).update({
                        'current_amount': state['current_amount'],
                        'consecutive_losses': state['consecutive_losses'],
                        'total_wins': state['total_wins'],
                        'total_losses': state['total_losses'],
                        'is_running': state['is_running'],
                        'is_trade_open': state['is_trade_open'],
                        'initial_balance': state['initial_balance'],
                        'logs': json.dumps(state['logs']),
                        'contract_id': state.get('contract_id') # Save contract ID
                    })
                    s.commit()
            
            except Exception as e:
                print(f"Error in main loop for session {bot_session_id}: {e}")
                # Attempt to log the error if possible
                try:
                    s_err = Session()
                    bot_session_err = s_err.query(BotSession).filter_by(session_id=bot_session_id).first()
                    if bot_session_err:
                        err_logs = json.loads(bot_session_err.logs)
                        err_logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💥 Internal Bot Error: {e}")
                        s_err.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(err_logs)})
                        s_err.commit()
                except Exception as log_e:
                    print(f"Failed to log internal error: {log_e}")
                finally:
                    s_err.close()
            finally:
                s.close()
            
            time.sleep(1) # Small delay to prevent excessive polling

    finally:
        if ws:
            ws.close()

if __name__ == "__main__":
    # This section will be executed when the script is run directly
    # It checks for any active sessions and starts their trading loops
    print("Bot script started. Checking for active sessions...")
    while True: # Loop to continuously check for active sessions
        s = Session()
        try:
            active_sessions = s.query(BotSession).filter_by(is_running=True).all()
            if not active_sessions:
                print("No active bots found. Waiting for commands...")
                time.sleep(10) # Wait before checking again
                continue # Go back to the start of the while loop

            print(f"Found {len(active_sessions)} active session(s).")
            for session in active_sessions:
                # Ensure the session is still running before starting the loop
                # (Another instance might have stopped it while we queried)
                session_check = s.query(BotSession).filter_by(session_id=session.session_id).first()
                if session_check and session_check.is_running:
                    print(f"Starting trading loop for session: {session.session_id}")
                    main_trading_loop(session.session_id) # This call blocks until the session stops or error occurs
                else:
                    print(f"Session {session.session_id} was no longer running. Skipping.")
            
            # If main_trading_loop finishes for all active sessions, it means they stopped.
            # The outer loop will then wait and check again.
            print("All active trading loops finished. Waiting for new commands...")
            time.sleep(10) # Wait before checking for new active sessions
        
        except Exception as e:
            print(f"Error in main bot loop startup: {e}")
            time.sleep(10) # Wait before retrying
        finally:
            s.close()
