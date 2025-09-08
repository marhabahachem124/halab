import websocket
import json
import pandas as pd
import numpy as np
import time
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, event
from datetime import datetime, timedelta
import os

# --- Database Setup ---
DATABASE_URL = "postgresql://khourybotes_db_user:HeAQEQ68txKKjTVQkDva3yaMx3npqTuw@dpg-d2uvmvogjchc73ao6060-a/khourybotes_db"
engine = sa.create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base()

# --- Database Models (Copied from app.py to ensure consistency) ---
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    sessions = relationship("BotSession", back_populates="user", cascade="all, delete-orphan")

class BotSession(Base):
    __tablename__ = 'bot_sessions'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    session_id = Column(String, unique=True, nullable=False)
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
    user = relationship("User", back_populates="sessions")

# Create tables if they don't exist
Base.metadata.create_all(engine)

# --- Trading Logic Functions ---
def log_session(session_obj, message):
    try:
        logs_list = json.loads(session_obj.logs)
        logs_list.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
        session_obj.logs = json.dumps(logs_list)
    except json.JSONDecodeError:
        session_obj.logs = json.dumps([f"[{datetime.now().strftime('%H:%M:%S')}] {message}"])

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
    valid_amount = round(max(0.5, amount), 2)
    req = {"buy": proposal_id, "price": valid_amount}
    try:
        ws.send(json.dumps(req))
        response = json.loads(ws.recv())
        if response.get('error'):
            return {"error": response['error']}
        return response
    except Exception as e:
        return {"error": {"message": str(e)}}

def check_contract_status(ws, contract_id):
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    try:
        ws.send(json.dumps(req))
        response = ws.recv() 
        response_data = json.loads(response)
        if response_data.get('msg_type') == 'proposal_open_contract':
            return response_data['proposal_open_contract']
        else:
            return None
    except websocket.WebSocketTimeoutException:
        return None
    except Exception as e:
        return None

def get_balance(ws):
    req = {"balance": 1, "subscribe": 1}
    try:
        ws.send(json.json.loads(ws.recv())
        if 'balance' in response:
            return response['balance']['balance']
        elif 'error' in response:
            return None
        return None
    except Exception as e:
        return None

def main():
    log_file_path = 'trading_bot_log.txt'
    with open(log_file_path, 'w') as f:
        f.write(f"[{datetime.now().strftime('%H:%M:%S')}] Trading bot service started.\n")

    while True:
        s = Session()
        try:
            active_sessions = s.query(BotSession).filter_by(is_running=True).all()
            
            if not active_sessions:
                time.sleep(10)
                continue
                
            for bot_session in active_sessions:
                if not bot_session.api_token:
                    log_session(bot_session, "❌ API Token not found in session. Stopping bot.")
                    bot_session.is_running = False
                    s.commit()
                    continue

                api_token = bot_session.api_token
                
                # Check for active trade
                if bot_session.is_trade_open:
                    if datetime.now() >= bot_session.trade_start_time + timedelta(seconds=40): 
                        ws = None
                        try:
                            ws = websocket.WebSocket()
                            ws.connect(f"wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
                            auth_req = {"authorize": api_token}
                            ws.send(json.dumps(auth_req))
                            auth_response = json.loads(ws.recv())
                            
                            if auth_response.get('error'):
                                log_session(bot_session, "❌ Reconnection failed for result check. Authentication error.")
                                bot_session.is_trade_open = False
                            else:
                                contract_info = check_contract_status(ws, bot_session.contract_id)
                                if contract_info and contract_info.get('is_sold'):
                                    profit = contract_info.get('profit', 0)
                                    
                                    if profit > 0:
                                        bot_session.consecutive_losses = 0
                                        bot_session.current_amount = bot_session.base_amount
                                        bot_session.total_wins += 1
                                        log_session(bot_session, f"🎉 Win! Profit: {profit:.2f}$")
                                    elif profit < 0:
                                        bot_session.consecutive_losses += 1
                                        bot_session.total_losses += 1
                                        next_bet = bot_session.current_amount * 2.2
                                        bot_session.current_amount = max(bot_session.base_amount, next_bet)
                                        log_session(bot_session, f"💔 Loss! Loss: {profit:.2f}$")
                                    else:
                                        log_session(bot_session, f"⚪ No change. Profit/Loss: 0$")
                                        
                                    bot_session.is_trade_open = False
                                    
                                    current_balance = get_balance(ws)
                                    if current_balance is not None:
                                        log_session(bot_session, f"💰 Current Balance: {current_balance:.2f}")
                                        if bot_session.tp_target and (current_balance - bot_session.initial_balance) >= bot_session.tp_target:
                                            log_session(bot_session, f"🤑 Take Profit target ({bot_session.tp_target}$) reached! Bot stopped.")
                                            bot_session.is_running = False
                                            s.delete(bot_session)
                                            s.commit()
                                            continue
                                    else:
                                        log_session(bot_session, "⚠ Could not retrieve balance after trade.")
                                        
                                    if bot_session.consecutive_losses >= bot_session.max_consecutive_losses:
                                        log_session(bot_session, f"🛑 Stop Loss hit ({bot_session.consecutive_losses} consecutive losses)! Bot stopped.")
                                        bot_session.is_running = False
                                        s.delete(bot_session)
                                        s.commit()
                                        continue

                                elif contract_info and not contract_info.get('is_sold'):
                                    log_session(bot_session, f"⚠ Contract {bot_session.contract_id} is not yet sold/closed.")
                                else:
                                    log_session(bot_session, f"⚠ Could not get contract info for ID: {bot_session.contract_id}.")
                                    bot_session.is_trade_open = False
                        except websocket.WebSocketConnectionClosedException:
                            log_session(bot_session, "❌ WebSocket connection closed unexpectedly during result check.")
                            bot_session.is_trade_open = False
                        except websocket.WebSocketTimeoutException:
                            log_session(bot_session, "❌ WebSocket connection timed out during result check.")
                            bot_session.is_trade_open = False
                        except Exception as e:
                            log_session(bot_session, f"❌ An error occurred getting the trade result: {e}")
                        finally:
                            if ws and ws.connected:
                                ws.close()
                
                # Check for new trade
                elif not bot_session.is_trade_open:
                    now = datetime.now()
                    if now.second >= 55:
                        ws = None
                        try:
                            ws = websocket.WebSocket()
                            ws.connect(f"wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10) 
                            
                            auth_req = {"authorize": api_token}
                            ws.send(json.dumps(auth_req))
                            auth_response = json.loads(ws.recv()) 
                            
                            if auth_response.get('error'):
                                log_session(bot_session, f"❌ Authentication failed: {auth_response['error']['message']}")
                                continue
                            else:
                                if bot_session.initial_balance is None:
                                    current_balance = get_balance(ws)
                                    if current_balance is not None:
                                        bot_session.initial_balance = current_balance
                                        log_session(bot_session, f"💰 Initial Balance: {bot_session.initial_balance}")
                                    else:
                                        log_session(bot_session, "❌ Failed to retrieve initial balance.")
                                        continue
                                
                                ticks_to_request = 60 
                                req = {"ticks_history": "R_100", "end": "latest", "count": ticks_to_request, "style": "ticks"}
                                ws.send(json.dumps(req))
                                tick_data = json.loads(ws.recv())
                                
                                if 'history' in tick_data and tick_data['history']['prices']:
                                    ticks = tick_data['history']['prices']
                                    timestamps = tick_data['history']['times']
                                    df_ticks = pd.DataFrame({'timestamp': timestamps, 'price': ticks})
                                    
                                    final_signal, error_msg = analyse_data(df_ticks)
                                    
                                    if error_msg:
                                        log_session(bot_session, f"⚠ Analysis Error: {error_msg}")
                                        continue
                                    
                                    if final_signal in ['Buy', 'Sell']:
                                        log_session(bot_session, f"➡ Entering a {final_signal.upper()} trade with {round(bot_session.current_amount, 2)}$")
                                        
                                        proposal_req = {
                                            "proposal": 1,
                                            "amount": round(bot_session.current_amount, 2),
                                            "basis": "stake",
                                            "contract_type": "CALL" if final_signal == 'Buy' else "PUT",
                                            "currency": "USD",
                                            "duration": 30,
                                            "duration_unit": "s",
                                            "symbol": "R_100",
                                            "passthrough": {"action": final_signal}
                                        }
                                        ws.send(json.dumps(proposal_req))
                                        proposal_response = json.loads(ws.recv())
                                        
                                        if 'proposal' in proposal_response:
                                            proposal_id = proposal_response['proposal']['id']
                                            order_response = place_order(ws, proposal_id, bot_session.current_amount)
                                            if 'buy' in order_response and 'contract_id' in order_response['buy']:
                                                bot_session.is_trade_open = True
                                                bot_session.trade_start_time = datetime.now()
                                                bot_session.contract_id = order_response['buy']['contract_id']
                                                log_session(bot_session, "✅ Order placed.")
                                            elif 'error' in order_response:
                                                log_session(bot_session, f"❌ Order failed: {order_response['error']['message']}")
                                            else:
                                                log_session(bot_session, f"❌ Unexpected order response: {order_response}")
                                        else:
                                            log_session(bot_session, f"❌ Proposal failed: {proposal_response.get('error', {}).get('message', 'Unknown error')}")
                                else:
                                    log_session(bot_session, "⚪ No clear signal. Waiting for next interval.")
                            else:
                                log_session(bot_session, "❌ Error: Could not get tick history data or data is empty.")
                        except websocket.WebSocketConnectionClosedException:
                            log_session(bot_session, "❌ WebSocket connection closed unexpectedly.")
                        except websocket.WebSocketTimeoutException:
                            log_session(bot_session, "❌ WebSocket connection timed out.")
                        except Exception as e:
                            log_session(bot_session, f"❌ An error occurred during the trading cycle: {e}")
                        finally:
                            if ws and ws.connected:
                                ws.close()
                s.commit()
        except Exception as e:
            with open(log_file_path, 'a') as f:
                f.write(f"[{datetime.now().strftime('%H:%M:%S')}] Main loop error: {e}\n")
        finally:
            s.close()
        
        time.sleep(1)

if __name__ == "__main__":
    main()
