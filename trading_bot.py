import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, ForeignKey
from datetime import datetime
import json
import uuid
import time
import os
import websocket
import pandas as pd

# --- Database Setup ---
DATABASE_URL = "postgresql://khourybotes_db_user:HeAQEQ68txKKjTVQkDva3yaMx3npqTuw@dpg-d2uvmvogjchc73ao6060-a/khourybotes_db"
engine = sa.create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

# Fix: declarative_base is now in sqlalchemy.orm
from sqlalchemy.orm import declarative_base
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
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = ws.recv() 
        return json.loads(response)['proposal_open_contract']
    except Exception:
        return None

def get_balance(ws):
    req = {"balance": 1, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        return response.get('balance', {}).get('balance')
    except Exception:
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
                    print(f"Bot for session {bot_session_id} is stopped. Exiting loop.")
                    break
                
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
                    time.sleep(5)
                    continue

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
                    if now.second >= 55:
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
                                        state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ✅ Order placed.")
                                        s.query(BotSession).filter_by(session_id=bot_session_id).update({"is_trade_open": True, "logs": json.dumps(state['logs'])})
                                        s.commit()
                                    else:
                                        state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ❌ Order failed: {order_response.get('error', {}).get('message', 'Unknown error')}")
                                        s.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(state['logs'])})
                                        s.commit()
                                else:
                                    state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ❌ Proposal failed: {proposal_response.get('error', {}).get('message', 'Unknown error')}")
                                    s.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(state['logs'])})
                                    s.commit()
                        else:
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] ⚪ No clear signal. Waiting.")
                            s.query(BotSession).filter_by(session_id=bot_session_id).update({'logs': json.dumps(state['logs'])})
                            s.commit()

                elif state.get('is_trade_open'):
                    now = datetime.now()
                    contract_info = check_contract_status(ws, state.get('contract_id'))
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
                        current_balance = get_balance(ws)
                        if current_balance is not None:
                            state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 💰 Current Balance: {current_balance:.2f}")
                            if state['tp_target'] and (current_balance - state['initial_balance']) >= state['tp_target']:
                                state['logs'].append(f"[{now.strftime('%H:%M:%S')}] 🤑 Take Profit reached! Bot stopped.")
                                state['is_running'] = False
                        if state['consecutive_losses'] >= state['max_consecutive_losses']:
                            state['logs'].append(f"[{now.now().strftime('%H:%M:%S')}] 🛑 Stop Loss hit! Bot stopped.")
                            state['is_running'] = False

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

            except Exception as e:
                print(f"Error for session {bot_session_id}: {e}")
            finally:
                s.close()
            time.sleep(1)
    finally:
        if ws:
            ws.close()

if __name__ == "__main__":
    # Check for active sessions when the bot starts
    s = Session()
    try:
        active_sessions = s.query(BotSession).filter_by(is_running=True).all()
        if not active_sessions:
            print("No active bots found. The bot will wait for new commands...")
            # Keep the bot running to check for new commands periodically
            while True:
                time.sleep(10) # Wait and check again
                s_wait = Session()
                try:
                    new_active_sessions = s_wait.query(BotSession).filter_by(is_running=True).all()
                    if new_active_sessions:
                        print("Found new active bots. Starting them now.")
                        for session in new_active_sessions:
                            # In a production scenario, you might want to manage these loops more robustly (e.g., using a thread pool or process pool)
                            # For simplicity, we'll just call main_trading_loop which will run until it's stopped.
                            # If multiple bots are started, they will run sequentially here. Consider threading if concurrent execution is needed.
                            main_trading_loop(session.session_id)
                        break # Exit the waiting loop once bots are started
                finally:
                    s_wait.close()
        else:
            for session in active_sessions:
                main_trading_loop(session.session_id)
    except Exception as e:
        print(f"Error in main bot loop: {e}")
    finally:
        s.close()
