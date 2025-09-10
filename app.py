import streamlit as st # لا يُستخدم فعلياً في هذا الكود الخاص بالبوت، لكن أبقيه إذا كنت تخطط لدمجه لاحقاً
from sqlalchemy.orm import sessionmaker, declarative_base # تم تعديل الاستيراد هنا
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, ForeignKey
from datetime import datetime
import json
import uuid
import time
import os
import websocket
import pandas as pd
from flask import Flask
from threading import Thread

# --- Database Setup ---
DATABASE_URL = "postgresql://bibokh_user:Ric9h1SaTADxdkV0LgNmF8c0RPWhWYzy@dpg-d30mrpogjchc73f1tiag-a.oregon-postgres.render.com/bibokh"
engine = sa.create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base() # الآن تعمل بشكل صحيح

# --- Model Definitions ---
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)

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
    contract_id = Column(String, nullable=True)
    logs = Column(String, default="[]")

# --- File-Based Authentication ---
# NOTE: For deployment, ensure this file is created or its content is part of env variables if needed.
# For local testing, create a file named user_ids.txt and add emails line by line.
ALLOWED_EMAILS_FILE = 'user_ids.txt'

def is_email_allowed(email):
    try:
        # On Render, we might not have direct file access, so this part might need
        # to be handled via environment variables or other means if the file isn't deployed.
        # For now, assuming the file will be present.
        if os.path.exists(ALLOWED_EMAILS_FILE):
            with open(ALLOWED_EMAILS_FILE, 'r') as f:
                allowed_emails = {line.strip() for line in f if line.strip()}
                return email.strip() in allowed_emails
        return False
    except Exception as e:
        print(f"Error checking email allowed: {e}")
        return False

# --- Database Session Management ---
def get_db_session():
    """Returns a new database session."""
    return Session()

def get_or_create_user(email):
    s = get_db_session()
    try:
        user = s.query(User).filter_by(email=email.strip()).first()
        if not user:
            user = User(email=email.strip())
            s.add(user)
            s.commit()
            s.refresh(user)
        return user
    finally:
        s.close()

def get_or_create_bot_session(user_id):
    s = get_db_session()
    try:
        bot_session = s.query(BotSession).filter_by(user_id=user_id).first()
        if not bot_session:
            bot_session = BotSession(user_id=user_id)
            s.add(bot_session)
            s.commit()
            s.refresh(bot_session)
        return bot_session
    finally:
        s.close()

def load_bot_state(session_id):
    s = get_db_session()
    try:
        bot_session = s.query(BotSession).filter_by(session_id=session_id).first()
        if bot_session:
            return {
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
                'contract_id': bot_session.contract_id,
                'logs': json.loads(bot_session.logs) if bot_session.logs else [],
            }
        return {}
    finally:
        s.close()

def update_bot_settings(session_id, new_settings):
    s = get_db_session()
    try:
        bot_session = s.query(BotSession).filter_by(session_id=session_id).first()
        if bot_session:
            for key, value in new_settings.items():
                if hasattr(bot_session, key):
                    setattr(bot_session, key, value)
            s.commit()
    finally:
        s.close()

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
    except Exception as e:
        print(f"Error placing order: {e}")
        return {"error": {"message": "Order placement failed."}}

def check_contract_status(ws, contract_id):
    if not contract_id:
        return None
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = ws.recv() 
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
    ws = None
    try:
        ws = websocket.WebSocket()
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        print(f"Connected to Deriv WebSocket for session {bot_session_id}")

        while True:
            bot_session = None
            s = get_db_session()
            try:
                bot_session = s.query(BotSession).filter_by(session_id=bot_session_id).first()
                if not bot_session or not bot_session.is_running:
                    print(f"Bot for session {bot_session_id} is stopped or not found. Exiting loop.")
                    break
                
                # Fetch current state from DB
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
                    'contract_id': bot_session.contract_id,
                    'logs': json.loads(bot_session.logs) if bot_session.logs else [],
                }

                if not state['api_token']:
                    # print("API token not set, waiting...")
                    time.sleep(5)
                    continue

                # Authorize
                auth_req = {"authorize": state['api_token']}
                ws.send(json.dumps(auth_req))
                auth_response = json.loads(ws.recv())
                if auth_response.get('error'):
                    state['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Auth failed: {auth_response['error']['message']}")
                    update_bot_settings(bot_session_id, {'logs': json.dumps(state['logs'])})
                    time.sleep(5)
                    continue

                # Handle trade execution logic
                if not state.get('is_trade_open'):
                    now = datetime.now()
                    if now.second >= 55: # Trigger in the last 5 seconds of every minute
                        if state['initial_balance'] is None:
                            current_balance = get_balance(ws)
                            if current_balance is not None:
                                state['initial_balance'] = current_balance
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 💰 Initial Balance: {state['initial_balance']:.2f}")
                                update_bot_settings(bot_session_id, {'initial_balance': state['initial_balance'], 'logs': json.dumps(state['logs'])})
                            else:
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ❌ Failed to get balance.")
                                update_bot_settings(bot_session_id, {'logs': json.dumps(state['logs'])})
                                time.sleep(5)
                                continue

                        # Request ticks history for analysis
                        req = {"ticks_history": "R_100", "end": "latest", "count": 60, "style": "ticks"}
                        ws.send(json.dumps(req))
                        tick_data = json.loads(ws.recv())
                        
                        if 'history' in tick_data and tick_data['history']['prices']:
                            df_ticks = pd.DataFrame({'price': tick_data['history']['prices']})
                            signal, error = analyse_data(df_ticks)
                            
                            if signal in ['Buy', 'Sell']:
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ➡ Entering a {signal.upper()} trade with {state['current_amount']:.2f}$")
                                proposal_req = {"proposal": 1, "amount": round(state['current_amount'], 2), "basis": "stake", "contract_type": "CALL" if signal == 'Buy' else "PUT", "currency": "USD", "duration": 30, "duration_unit": "s", "symbol": "R_100"}
                                ws.send(json.dumps(proposal_req))
                                proposal_response = json.loads(ws.recv())
                                
                                if 'proposal' in proposal_response:
                                    order_response = place_order(ws, proposal_response['proposal']['id'], state['current_amount'])
                                    if 'buy' in order_response:
                                        state['is_trade_open'] = True
                                        state['contract_id'] = order_response['buy']['contract_id']
                                        state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ✅ Order placed. Contract ID: {state['contract_id']}")
                                        update_bot_settings(bot_session_id, {"is_trade_open": True, "contract_id": state['contract_id'], "logs": json.dumps(state['logs'])})
                                    else:
                                        state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ❌ Order failed: {order_response.get('error', {}).get('message', 'Unknown error')}")
                                        update_bot_settings(bot_session_id, {'logs': json.dumps(state['logs'])})
                                else:
                                    state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ❌ Proposal failed: {proposal_response.get('error', {}).get('message', 'Unknown error')}")
                                    update_bot_settings(bot_session_id, {'logs': json.dumps(state['logs'])})
                            else:
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ⚪ No clear signal. Waiting.")
                                update_bot_settings(bot_session_id, {'logs': json.dumps(state['logs'])})
                        else:
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ⚪ No tick data received.")
                            update_bot_settings(bot_session_id, {'logs': json.dumps(state['logs'])})

                elif state.get('is_trade_open') and state.get('contract_id'):
                    now = datetime.now()
                    contract_info = check_contract_status(ws, state['contract_id'])
                    if contract_info and contract_info.get('is_sold'):
                        profit = contract_info.get('profit', 0)
                        if profit > 0:
                            state['consecutive_losses'] = 0
                            state['current_amount'] = state['base_amount']
                            state['total_wins'] += 1
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 🎉 Win! Profit: {profit:.2f}$")
                        else:
                            state['consecutive_losses'] += 1
                            state['current_amount'] = max(state['base_amount'], state['current_amount'] * 2.2) # Martingale logic
                            state['total_losses'] += 1
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 💔 Loss! Loss: {profit:.2f}$")
                        
                        state['is_trade_open'] = False
                        state['contract_id'] = None # Clear contract ID after closing
                        
                        current_balance = get_balance(ws)
                        if current_balance is not None:
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 💰 Current Balance: {current_balance:.2f}")
                            if state['tp_target'] and (current_balance - state['initial_balance']) >= state['tp_target']:
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 🤑 Take Profit reached! Bot stopped.")
                                state['is_running'] = False
                        if state['consecutive_losses'] >= state['max_consecutive_losses']:
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 🛑 Stop Loss hit! Bot stopped.")
                            state['is_running'] = False
                        
                        update_bot_settings(bot_session_id, {
                            'current_amount': state['current_amount'],
                            'consecutive_losses': state['consecutive_losses'],
                            'total_wins': state['total_wins'],
                            'total_losses': state['total_losses'],
                            'is_running': state['is_running'],
                            'is_trade_open': state['is_trade_open'],
                            'initial_balance': state['initial_balance'],
                            'contract_id': state['contract_id'],
                            'logs': json.dumps(state['logs'])
                        })
            except Exception as e:
                print(f"Error in main_trading_loop for session {bot_session_id}: {e}")
                # Log the error and attempt to update DB if possible
                try:
                    current_logs = json.loads(bot_session.logs) if bot_session and bot_session.logs else []
                    current_logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💥 CRITICAL ERROR IN LOOP: {str(e)}")
                    update_bot_settings(bot_session_id, {'logs': json.dumps(current_logs)})
                except: # If DB connection fails here, just print to console
                    print(f"Failed to log critical error to DB for session {bot_session_id}")
            finally:
                s.close()
            time.sleep(1) # Small delay to avoid overwhelming the system
    finally:
        if ws:
            ws.close()
            print(f"WebSocket connection closed for session {bot_session_id}")

# --- Flask App for Web Service ---
app = Flask(__name__)

@app.route('/')
def home():
    return "KHOURYBOT Trading Bot Service is Running and Ready!"

# --- Background Task Runner ---
def start_all_bots_in_background():
    """Initializes DB and starts all active bots."""
    try:
        Base.metadata.create_all(engine)
        print("Database tables checked/created successfully.")
        
        print("Starting bot management loop.")
        while True:
            s = get_db_session()
            try:
                active_sessions = s.query(BotSession).filter_by(is_running=True).all()
                if not active_sessions:
                    # print("No active bots found. Waiting for commands...") # Uncomment if you want frequent logging
                    time.sleep(10)
                    continue
                
                for session in active_sessions:
                    # Check if bot is already running in a thread or needs to start
                    # This is a simplified approach, a more robust solution would manage threads.
                    # For now, we rely on the main_trading_loop checking session.is_running.
                    print(f"Ensuring bot is running for session: {session.session_id}")
                    main_trading_loop(session.session_id) # This will run until session.is_running becomes False
                
                time.sleep(10) # Check for active sessions every 10 seconds
            except Exception as e:
                print(f"Error in start_all_bots_in_background: {e}")
            finally:
                s.close()
    except Exception as e:
        print(f"FATAL ERROR in background bot management: {e}")

# --- Main Execution Block ---
if __name__ == "__main__":
    # Start the bot logic in a separate thread
    bot_management_thread = Thread(target=start_all_bots_in_background)
    bot_management_thread.daemon = True # Allows the main program to exit even if this thread is running
    bot_management_thread.start()
    
    # Get port from environment variable or default to 5000
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Flask web server on port {port}...")
    
    # Start the Flask web server to keep the service alive
    # Gunicorn will manage this when deployed. For local running, app.run() works.
    # If running locally and want to test gunicorn behavior, use:
    # import subprocess
    # subprocess.run(["gunicorn", "--bind", f"0.0.0.0:{port}", "trading_bot:app"])
    
    # For direct execution (e.g., python trading_bot.py)
    app.run(host="0.0.0.0", port=port)
