import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker, declarative_base # Import declarative_base correctly
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, ForeignKey
from datetime import datetime
import json
import uuid
import time
import os
import websocket
import pandas as pd
import logging # Added for better logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Database Setup ---
# Directly embedding the DATABASE_URL as requested
DATABASE_URL = "postgresql://bibokh_user:Ric9h1SaTADxdkV0LgNmF8c0RPWhWYzy@dpg-d30mrpogjchc73f1tiag-a.oregon-postgres.render.com/bibokh"

try:
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    Base = declarative_base() # Use the imported declarative_base

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

    # Create tables if they don't exist (important for first deploy)
    Base.metadata.create_all(engine)
    logging.info("Database tables checked/created successfully.")

except Exception as e:
    logging.error(f"Database setup failed: {e}")
    # Exit if database connection fails on startup
    exit()


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
        logging.error(f"Error placing order: {e}")
        return {"error": {"message": "Order placement failed."}}

def check_contract_status(ws, contract_id):
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        # It's better to handle responses in a loop or with a timeout for robustness
        # For simplicity here, we assume a response comes back quickly.
        # In a real-world app, you might need a more sophisticated approach to handle WebSocket messages.
        response = ws.recv() 
        return json.loads(response)['proposal_open_contract']
    except Exception as e:
        logging.error(f"Error checking contract status: {e}")
        return None

def get_balance(ws):
    req = {"balance": 1, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        return response.get('balance', {}).get('balance')
    except Exception as e:
        logging.error(f"Error getting balance: {e}")
        return None

def main_trading_loop(bot_session_id):
    state = {}
    ws = None
    try:
        # WebSocket connection setup
        ws = websocket.WebSocket()
        ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
        logging.info(f"WebSocket connected for session: {bot_session_id}")

        while True:
            s = Session()
            try:
                bot_session = s.query(BotSession).filter_by(session_id=bot_session_id).first()
                
                # Check if bot is still running or session exists
                if not bot_session or not bot_session.is_running:
                    logging.info(f"Bot for session {bot_session_id} is stopped or not found. Exiting loop.")
                    break
                
                # Load current state from DB
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
                    'logs': json.loads(bot_session.logs) if bot_session.logs else [],
                }

                # Ensure API token is present
                if not state.get('api_token'):
                    logging.warning(f"API token missing for session {bot_session_id}. Waiting.")
                    time.sleep(5)
                    continue

                # Authorize with API token
                auth_req = {"authorize": state['api_token']}
                ws.send(json.dumps(auth_req))
                auth_response = json.loads(ws.recv())
                if auth_response.get('error'):
                    error_msg = auth_response['error']['message']
                    logging.error(f"Authentication failed for session {bot_session_id}: {error_msg}")
                    state['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Auth failed: {error_msg}")
                    s.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(state['logs'])})
                    s.commit()
                    time.sleep(5)
                    continue
                
                # --- Trading Logic ---
                if not state.get('is_trade_open'):
                    now = datetime.now()
                    # Only attempt to trade at specific seconds to align with 30s contracts
                    if now.second >= 55 or now.second < 5: # Checking a window around the minute change
                        if state['initial_balance'] is None: # First run, get balance
                            current_balance = get_balance(ws)
                            if current_balance is not None:
                                state['initial_balance'] = current_balance
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 💰 Initial Balance: {state['initial_balance']:.2f}")
                            else:
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ❌ Failed to get balance.")
                        
                        # Fetch ticks for analysis
                        req = {"ticks_history": "R_100", "end": "latest", "count": 60, "style": "ticks"}
                        ws.send(json.dumps(req))
                        tick_data = json.loads(ws.recv())
                        
                        if 'history' in tick_data and tick_data['history']['prices']:
                            df_ticks = pd.DataFrame({'price': tick_data['history']['prices']})
                            signal, error = analyse_data(df_ticks)
                            
                            if signal in ['Buy', 'Sell']:
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ➡ Entering a {signal.upper()} trade with {state['current_amount']:.2f}$")
                                contract_type = "CALL" if signal == 'Buy' else "PUT"
                                # Proposal request for the trade
                                proposal_req = {"proposal": 1, "amount": round(state['current_amount'], 2), "basis": "stake", "contract_type": contract_type, "currency": "USD", "duration": 30, "duration_unit": "s", "symbol": "R_100"}
                                ws.send(json.dumps(proposal_req))
                                proposal_response = json.loads(ws.recv())
                                
                                if 'proposal' in proposal_response:
                                    order_response = place_order(ws, proposal_response['proposal']['id'], state['current_amount'])
                                    if 'buy' in order_response:
                                        state['is_trade_open'] = True
                                        state['contract_id'] = order_response['buy']['contract_id'] # Store contract ID
                                        state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ✅ Order placed successfully. Contract ID: {state['contract_id']}")
                                        s.query(BotSession).filter_by(session_id=bot_session_id).update({"is_trade_open": True, "contract_id": state['contract_id'], "logs": json.dumps(state['logs'])})
                                        s.commit()
                                    else:
                                        error_msg = order_response.get('error', {}).get('message', 'Unknown error')
                                        state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ❌ Order failed: {error_msg}")
                                        s.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(state['logs'])})
                                        s.commit()
                                else:
                                    error_msg = proposal_response.get('error', {}).get('message', 'Unknown error')
                                    state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ❌ Proposal failed: {error_msg}")
                                    s.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(state['logs'])})
                                    s.commit()
                            else:
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ⚪ No clear signal. Waiting.")
                        else:
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ⚪ No tick data received or error. Waiting.")
                        
                        # Update DB with latest logs and status if no trade was opened
                        if not state.get('is_trade_open'):
                            s.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(state['logs'])})
                            s.commit()

                elif state.get('is_trade_open'): # If a trade is currently open
                    now = datetime.now()
                    contract_info = check_contract_status(ws, state.get('contract_id'))
                    
                    if contract_info and contract_info.get('is_sold'):
                        profit = contract_info.get('profit', 0)
                        
                        # Update win/loss counts and amounts
                        if profit > 0:
                            state['consecutive_losses'] = 0
                            state['current_amount'] = state['base_amount'] # Reset to base amount on win
                            state['total_wins'] += 1
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 🎉 Win! Profit: {profit:.2f}$")
                        else:
                            state['consecutive_losses'] += 1
                            # Martingale logic: double the stake after a loss (adjust multiplier as needed)
                            state['current_amount'] = max(state['base_amount'], state['current_amount'] * 2.2) 
                            state['total_losses'] += 1
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 💔 Loss! Loss: {profit:.2f}$")
                        
                        state['is_trade_open'] = False
                        
                        # Check for Take Profit or Stop Loss
                        current_balance = get_balance(ws)
                        if current_balance is not None:
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 💰 Current Balance: {current_balance:.2f}")
                            if state['tp_target'] and (current_balance - state['initial_balance']) >= state['tp_target']:
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 🤑 Take Profit reached! Bot stopped.")
                                state['is_running'] = False
                        
                        if state['consecutive_losses'] >= state['max_consecutive_losses']:
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 🛑 Stop Loss hit! Bot stopped.")
                            state['is_running'] = False

                    # Update DB with latest state and logs
                    s.query(BotSession).filter_by(session_id=bot_session_id).update({
                        'current_amount': state['current_amount'],
                        'consecutive_losses': state['consecutive_losses'],
                        'total_wins': state['total_wins'],
                        'total_losses': state['total_losses'],
                        'is_running': state['is_running'],
                        'is_trade_open': state['is_trade_open'],
                        'initial_balance': state['initial_balance'],
                        'logs': json.dumps(state['logs'])
                    })
                    s.commit()
                    
                    # If bot was stopped due to TP/SL, break the inner loop to re-evaluate state
                    if not state['is_running']:
                        break 

            except Exception as e:
                logging.error(f"Error in trading loop for session {bot_session_id}: {e}")
                # Attempt to log the error to DB if session is valid
                if 's' in locals() and bot_session:
                    try:
                        # Append error to logs if possible
                        if 'logs' in state:
                            state['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] 💥 RuntimeError: {e}")
                            s.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(state['logs'])})
                            s.commit()
                    except Exception as db_err:
                        logging.error(f"Failed to log error to DB: {db_err}")
            finally:
                s.close() # Ensure session is closed
            
            # Sleep for a short duration before next iteration, unless a trade is open and needs fast checking
            if state.get('is_trade_open'):
                time.sleep(0.5) # Shorter sleep if waiting for contract to close
            else:
                time.sleep(1) # Normal sleep when waiting for signal
    
    except websocket.WebSocketConnectionClosedException as e:
        logging.warning(f"WebSocket connection closed for session {bot_session_id}: {e}. Attempting to reconnect.")
        # Add retry logic here if needed, or let the outer loop handle it if the bot is still running.
        time.sleep(5)
    except Exception as e:
        logging.error(f"An unexpected error occurred in main_trading_loop for session {bot_session_id}: {e}")
    finally:
        if ws:
            ws.close()
            logging.info(f"WebSocket closed for session {bot_session_id}.")


if __name__ == "__main__":
    logging.info("Starting the main bot process.")
    
    # This main loop will continuously check for active sessions and run them.
    # It's designed to run indefinitely as a Web Service.
    while True:
        s = Session()
        try:
            # Query for all sessions marked as running
            active_sessions = s.query(BotSession).filter_by(is_running=True).all()
            
            if not active_sessions:
                logging.info("No active bots found. Waiting for commands...")
                time.sleep(10) # Wait longer if no bots are running
                continue
            
            # For each active session, start a main_trading_loop
            # Note: In a true multi-user scenario, you might want to run these in threads or processes.
            # For now, it processes them sequentially in the main loop.
            for session in active_sessions:
                logging.info(f"Found active bot session: {session.session_id}. Starting its loop.")
                main_trading_loop(session.session_id)
                # After a loop finishes (e.g., bot stopped or error), 
                # it will re-query for active sessions in the next iteration.

        except Exception as e:
            logging.error(f"Error in the main bot process loop: {e}")
        finally:
            s.close() # Ensure the session is closed
        
        # Add a small delay to prevent excessive CPU usage if errors keep occurring
        time.sleep(5)
