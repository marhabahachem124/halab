# --- Imports ---
import streamlit as st
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker, declarative_base # Import declarative_base here
from datetime import datetime
import json
import uuid
import os
import time
import streamlit.components.v1 as components
from threading import Thread # Needed for running bot in background

# --- Database Setup ---
# IMPORTANT: Use environment variables for production, but for Render's service deploy,
# putting it directly might be necessary if you can't set it up as a secret.
# In a real-world scenario, ALWAYS use environment variables for sensitive data.
DATABASE_URL = "postgresql://bibokh_user:Ric9h1SaTADxdkV0LgNmF8c0RPWhWYzy@dpg-d30mrpogjchc73f1tiag-a.oregon-postgres.render.com/bibokh"
engine = sa.create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base() # Use the imported declarative_base

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
    contract_id = Column(String, nullable=True) # Added to track contracts
    logs = Column(String, default="[]")

# --- File-Based Authentication ---
ALLOWED_EMAILS_FILE = 'user_ids.txt' # Ensure this file exists in your project

def is_email_allowed(email):
    try:
        if os.path.exists(ALLOWED_EMAILS_FILE):
            with open(ALLOWED_EMAILS_FILE, 'r') as f:
                allowed_emails = {line.strip() for line in f}
                return email in allowed_emails
        return False
    except Exception:
        return False

# --- Database Session Management ---
def get_or_create_user(email):
    s = Session()
    try:
        user = s.query(User).filter_by(email=email).first()
        if not user:
            user = User(email=email)
            s.add(user)
            s.commit()
            s.refresh(user)
        return user
    finally:
        s.close()

def get_or_create_bot_session(user):
    s = Session()
    try:
        bot_session = s.query(BotSession).filter_by(user_id=user.id).first()
        if not bot_session:
            bot_session = BotSession(user_id=user.id)
            s.add(bot_session)
            s.commit()
            s.refresh(bot_session)
        return bot_session
    finally:
        s.close()

def load_bot_state(session_id):
    s = Session()
    try:
        bot_session = s.query(BotSession).filter_by(session_id=session_id).first()
        if bot_session:
            # Ensure logs is a list, even if empty or null
            logs_list = json.loads(bot_session.logs) if bot_session.logs else []
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
                'logs': logs_list,
            }
        return {}
    finally:
        s.close()

def update_bot_settings(session_id, new_settings):
    s = Session()
    try:
        bot_session = s.query(BotSession).filter_by(session_id=session_id).first()
        if bot_session:
            for key, value in new_settings.items():
                if hasattr(bot_session, key):
                    setattr(bot_session, key, value)
            s.commit()
    finally:
        s.close()

# --- Trading Bot Logic (extracted to a separate function to be run in a thread) ---
def trading_bot_process():
    s = Session()
    try:
        # Ensure tables are created when the bot process starts
        Base.metadata.create_all(engine)
        print("Database tables checked/created successfully.")
        
        print("Starting the main bot process...")
        while True:
            active_sessions = s.query(BotSession).filter_by(is_running=True).all()
            if not active_sessions:
                print("No active bots found. Waiting for commands...")
                time.sleep(10)
                continue
            
            for session in active_sessions:
                print(f"Processing bot session: {session.session_id}")
                # Call the actual trading loop function
                main_trading_loop(session.session_id)
            
            time.sleep(10) # Wait before checking for active sessions again
    except Exception as e:
        print(f"Error in main bot process loop: {e}")
    finally:
        s.close()

# --- Actual Trading Loop Logic ---
def main_trading_loop(bot_session_id):
    state = {}
    ws = None
    try:
        ws = websocket.WebSocket()
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        
        # Keep re-fetching session state to reflect UI changes
        s_loop = Session()
        bot_session = s_loop.query(BotSession).filter_by(session_id=bot_session_id).first()
        if not bot_session or not bot_session.is_running:
            print(f"Bot for session {bot_session_id} is stopped. Exiting trading loop.")
            return # Exit this specific trading loop
        
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
        s_loop.close() # Close the session used for fetching state

        # Authenticate
        auth_req = {"authorize": state['api_token']}
        ws.send(json.dumps(auth_req))
        auth_response = json.loads(ws.recv())
        if auth_response.get('error'):
            state['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Auth failed: {auth_response['error']['message']}")
            update_bot_settings(bot_session_id, {'logs': json.dumps(state['logs'])})
            time.sleep(5)
            return # Exit if auth fails

        # Trading logic
        if not state.get('is_trade_open'):
            now = datetime.now()
            if now.second >= 55: # Trigger analysis in the last 5 seconds of each minute
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
                        return # Exit if balance fetching fails

                # Request historical ticks
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
                    state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ⚪ No tick data received. Retrying...")
                    update_bot_settings(bot_session_id, {'logs': json.dumps(state['logs'])})
            else:
                # If not in the last 5 seconds, check if we need to update status
                # This part might be simplified or removed if not critical for real-time updates here
                pass
        
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
                    state['current_amount'] = max(state['base_amount'], state['current_amount'] * 2.2)
                    state['total_losses'] += 1
                    state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 💔 Loss! Loss: {profit:.2f}$")
                
                state['is_trade_open'] = False
                state['contract_id'] = None # Reset contract ID
                
                current_balance = get_balance(ws)
                if current_balance is not None:
                    state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 💰 Current Balance: {current_balance:.2f}")
                    # Check for Take Profit
                    if state['tp_target'] and (current_balance - state['initial_balance']) >= state['tp_target']:
                        state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 🤑 Take Profit reached! Bot stopping.")
                        state['is_running'] = False
                
                # Check for Stop Loss
                if state['consecutive_losses'] >= state['max_consecutive_losses']:
                    state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 🛑 Stop Loss hit! Bot stopping.")
                    state['is_running'] = False

            # Update database with latest state after trade resolution
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

        s_loop_update = Session() # Need a new session to query and update
        bot_session_to_update = s_loop_update.query(BotSession).filter_by(session_id=bot_session_id).first()
        if bot_session_to_update:
             # Update any fields that might have changed in the UI but not directly by the bot loop
            bot_session_to_update.is_running = state.get('is_running', False)
            bot_session_to_update.api_token = state.get('api_token') # Re-sync token if changed
            s_loop_update.commit()
        s_loop_update.close()


    except Exception as e:
        print(f"Error in main_trading_loop for session {bot_session_id}: {e}")
        # Attempt to log this error to the database if possible
        try:
            s_err = Session()
            bot_session_err = s_err.query(BotSession).filter_by(session_id=bot_session_id).first()
            if bot_session_err:
                err_logs = json.loads(bot_session_err.logs) if bot_session_err.logs else []
                err_logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🔥 CRITICAL ERROR IN TRADING LOOP: {e}")
                s_err.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(err_logs), 'is_running': False})
                s_err.commit()
        except Exception as log_e:
            print(f"Failed to log critical error to DB: {log_e}")
        finally:
            if 's_err' in locals() and s_err: s_err.close()
    finally:
        if ws:
            ws.close()
        # Ensure the bot stops if it was running but encountered a fatal error
        try:
            s_final = Session()
            bot_session_final = s_final.query(BotSession).filter_by(session_id=bot_session_id).first()
            if bot_session_final and bot_session_final.is_running:
                 # Log that the loop exited unexpectedly
                final_logs = json.loads(bot_session_final.logs) if bot_session_final.logs else []
                final_logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Trading loop for {bot_session_id} exited unexpectedly.")
                s_final.query(BotSession).filter_by(session_id=bot_session_id).update({'is_running': False, 'logs': json.dumps(final_logs)})
                s_final.commit()
            s_final.close()
        except Exception as final_err:
            print(f"Error during final cleanup for session {bot_session_id}: {final_err}")


# --- Flask App Setup ---
from flask import Flask
app = Flask(__name__)

@app.route('/')
def home():
    return "KhouryBot is running and ready!"

# --- Combined Startup Function ---
def run_all_services():
    # Start the trading bot process in a separate thread
    # This thread will manage multiple bot sessions if needed
    bot_thread = Thread(target=trading_bot_process)
    bot_thread.daemon = True # Allows the main program to exit even if this thread is running
    bot_thread.start()

    # Start the Flask web server to keep the Render service alive
    # Render sets the PORT environment variable
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Flask web server on port {port}")
    app.run(host="0.0.0.0", port=port)

# --- Main Execution Block ---
if __name__ == "__main__":
    run_all_services()
