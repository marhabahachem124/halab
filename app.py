import streamlit as st
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
import uuid
import streamlit.components.v1 as components
import websocket
import json
import pandas as pd
import ta
import time
import numpy as np
import threading
import os

# --- إعداد قاعدة البيانات ---
DATABASE_URL = "postgresql://khourybot_db_user:wlVAwKwLhfzzH9HFsRMNo3IOo4dX6DYm@dpg-d2smi46r433s73frbbcg-a/khourybot_db"
engine = sa.create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base()

class BotState(Base):
    __tablename__ = 'bot_state'
    id = sa.Column(sa.Integer, primary_key=True)
    device_id = sa.Column(sa.String, unique=True, nullable=False)
    is_running = sa.Column(sa.Boolean, default=False)
    user_token = sa.Column(sa.String, nullable=True)
    current_amount = sa.Column(sa.Float, default=0.5)
    base_amount = sa.Column(sa.Float, default=0.5)
    consecutive_losses = sa.Column(sa.Integer, default=0)
    is_trade_open = sa.Column(sa.Boolean, default=False)
    trade_start_time = sa.Column(sa.DateTime, nullable=True)
    contract_id = sa.Column(sa.String, nullable=True)
    last_action_time = sa.Column(sa.DateTime, nullable=True)
    total_wins = sa.Column(sa.Integer, default=0)
    total_losses = sa.Column(sa.Integer, default=0)
    initial_balance = sa.Column(sa.Float, nullable=True)
    tp_target = sa.Column(sa.Float, nullable=True)
    max_consecutive_losses = sa.Column(sa.Integer, default=5)

class BotLog(Base):
    __tablename__ = 'bot_logs'
    id = sa.Column(sa.Integer, primary_key=True)
    device_id = sa.Column(sa.String, nullable=False)
    timestamp = sa.Column(sa.DateTime, default=datetime.utcnow)
    message = sa.Column(sa.String, nullable=False)

class Device(Base):
    __tablename__ = 'devices'
    id = sa.Column(sa.Integer, primary_key=True)
    device_id = sa.Column(sa.String, unique=True, nullable=False)
    is_allowed = sa.Column(sa.Boolean, default=False)

# This line ensures tables are created only if they don't exist
try:
    Base.metadata.create_all(engine)
except Exception as e:
    st.error(f"Database connection error: {e}")

def sync_allowed_users_from_file():
    """Reads device IDs from user_ids.txt and updates the database."""
    allowed_ids = set()
    try:
        if os.path.exists("user_ids.txt"):
            with open("user_ids.txt", "r") as f:
                allowed_ids = {line.strip() for line in f if line.strip()}
    except Exception as e:
        st.error(f"Error reading user_ids.txt: {e}")
        return

    session = Session()
    try:
        devices_to_activate = session.query(Device).filter(
            Device.device_id.in_(allowed_ids),
            Device.is_allowed == False
        ).all()
        
        for device in devices_to_activate:
            device.is_allowed = True
            log_message(device.device_id, "تم تفعيل الجهاز تلقائيا من ملف user_ids.txt")
        
        session.commit()
    except Exception as e:
        st.error(f"Database error during sync: {e}")
        session.rollback()
    finally:
        session.close()

def is_user_allowed(device_id):
    session = Session()
    try:
        device = session.query(Device).filter_by(device_id=device_id).first()
        if device:
            return device.is_allowed
        return False
    finally:
        session.close()

def log_message(device_id, message):
    session = Session()
    try:
        new_log = BotLog(device_id=device_id, message=message)
        session.add(new_log)
        session.commit()
    except Exception as e:
        print(f"Error logging to DB for {device_id}: {e}")
        session.rollback()
    finally:
        session.close()

def get_bot_state(device_id):
    session = Session()
    try:
        state = session.query(BotState).filter_by(device_id=device_id).first()
        if not state:
            state = BotState(device_id=device_id)
            session.add(state)
            session.commit()
        return state
    finally:
        session.close()

def update_bot_state(device_id, **kwargs):
    session = Session()
    try:
        state = session.query(BotState).filter_by(device_id=device_id).first()
        if state:
            for key, value in kwargs.items():
                setattr(state, key, value)
            session.commit()
    finally:
        session.close()

def ticks_to_ohlc_by_count(ticks_df, tick_count):
    if ticks_df.empty: return pd.DataFrame()
    ohlc_data = []
    prices = ticks_df['price'].values
    timestamps = ticks_df['timestamp'].values
    for i in range(0, len(prices), tick_count):
        chunk = prices[i:i + tick_count]
        if len(chunk) == tick_count:
            open_price = chunk[0]; high_price = np.max(chunk); low_price = np.min(chunk); close_price = chunk[-1]
            ohlc_data.append({'timestamp': timestamps[i+tick_count-1], 'Open': open_price, 'High': high_price, 'Low': low_price, 'Close': close_price, 'Volume': tick_count})
    ohlc_df = pd.DataFrame(ohlc_data)
    if not ohlc_df.empty:
        ohlc_df['timestamp'] = pd.to_datetime(ohlc_df['timestamp'], unit='s')
        ohlc_df.set_index('timestamp', inplace=True)
    return ohlc_df

def analyse_data(data, device_id):
    try:
        required_candles = 50
        if data.empty or len(data) < required_candles: return "Neutral", 0, 0, "Insufficient data"
        data = data.tail(required_candles).copy()
        signals = []
        def get_indicator_signal(indicator_func, default_signal="Neutral"):
            try:
                result = indicator_func()
                if isinstance(result, pd.Series) and not result.empty: return result.iloc[-1]
                elif isinstance(result, tuple) and len(result) > 0 and isinstance(result[0], pd.Series) and not result[0].empty: return result[0].iloc[-1]
                return None
            except Exception as e: return None
        rsi_value = get_indicator_signal(lambda: ta.momentum.RSIIndicator(data['Close']).rsi())
        if rsi_value is not None: signals.append("Buy" if rsi_value >= 50 else "Sell")
        stoch_value = get_indicator_signal(lambda: ta.momentum.StochasticOscillator(data['High'], data['Low'], data['Close']).stoch())
        if stoch_value is not None: signals.append("Buy" if stoch_value >= 50 else "Sell")
        roc_value = get_indicator_signal(lambda: ta.momentum.ROCIndicator(data['Close']).roc())
        if roc_value is not None: signals.append("Buy" if roc_value >= 0 else "Sell")
        adx_indicator = ta.trend.ADXIndicator(data['High'], data['Low'], data['Close']); adx_pos_val = get_indicator_signal(lambda: adx_indicator.adx_pos()); adx_neg_val = get_indicator_signal(lambda: adx_indicator.adx_neg())
        if adx_pos_val is not None and adx_neg_val is not None: signals.append("Buy" if adx_pos_val >= adx_neg_val else "Sell")
        macd_indicator = ta.trend.MACD(data['Close']); macd_val = get_indicator_signal(lambda: macd_indicator.macd()); macd_signal_val = get_indicator_signal(lambda: macd_indicator.macd_signal())
        if macd_val is not None and macd_signal_val is not None: signals.append("Buy" if macd_val >= macd_signal_val else "Sell")
        ichimoku_indicator = ta.trend.IchimokuIndicator(data['High'], data['Low']); ichimoku_a_val = get_indicator_signal(lambda: ichimoku_indicator.ichimoku_a()); ichimoku_b_val = get_indicator_signal(lambda: ichimoku_indicator.ichimoku_b()); last_close_ichimoku = data.iloc[-1]['Close']
        if ichimoku_a_val is not None and ichimoku_b_val is not None:
            if last_close_ichimoku > max(ichimoku_a_val, ichimoku_b_val): signals.append("Buy")
            elif last_close_ichimoku < min(ichimoku_a_val, ichimoku_b_val): signals.append("Sell")
            else:
                tenkan_sen = (data['High'].rolling(window=9).max() + data['Low'].rolling(window=9).min()) / 2
                tenkan_sen_val = get_indicator_signal(lambda: tenkan_sen)
                if tenkan_sen_val is not None: signals.append("Buy" if last_close_ichimoku > tenkan_sen_val else "Sell")
        if len(data) >= 20:
            ema10 = get_indicator_signal(lambda: ta.trend.EMAIndicator(data['Close'], window=10).ema_indicator())
            ema20 = get_indicator_signal(lambda: ta.trend.EMAIndicator(data['Close'], window=20).ema_indicator())
            if ema10 is not None and ema20 is not None: signals.append("Buy" if ema10 >= ema20 else "Sell")
        obv_series = ta.volume.OnBalanceVolumeIndicator(data['Close'], data['Volume']).on_balance_volume()
        if not obv_series.empty and len(obv_series) > 1:
            if obv_series.iloc[-1] > obv_series.iloc[-2]: signals.append("Buy")
            elif obv_series.iloc[-1] < obv_series.iloc[-2]: signals.append("Sell")
        cci_value = get_indicator_signal(lambda: ta.trend.CCIIndicator(data['High'], data['Low'], data['Close']).cci())
        if cci_value is not None:
            if cci_value > 0: signals.append("Buy")
            elif cci_value < 0: signals.append("Sell")
        ao_value = get_indicator_signal(lambda: ta.momentum.AwesomeOscillatorIndicator(data['High'], data['Low']).awesome_oscillator())
        if ao_value is not None:
            if ao_value > 0: signals.append("Buy")
            elif ao_value < 0: signals.append("Sell")
        buy_count = signals.count("Buy"); sell_count = signals.count("Sell"); total_indicators = len(signals); provisional_decision = "Neutral"
        if total_indicators > 0:
            buy_percentage = (buy_count / total_indicators) * 100; sell_percentage = (sell_count / total_indicators) * 100
            if buy_percentage >= 70: provisional_decision = "Buy"
            elif sell_percentage >= 70: provisional_decision = "Sell"
        return provisional_decision, buy_count, sell_count, None
    except Exception as e: log_message(device_id, f"❌ Error in analyse_data: {e}"); return None, 0, 0, f"An error occurred during analysis: {e}"

def place_order(ws, proposal_id, amount, device_id):
    valid_amount = round(max(0.5, amount), 2); req = {"buy": proposal_id, "price": valid_amount}
    try:
        ws.send(json.dumps(req)); response = json.loads(ws.recv())
        if response.get('error'):
            log_message(device_id, f"❌ Order Error: {response['error']['message']}")
            return {"error": response['error']}
        return response
    except Exception as e:
        log_message(device_id, f"❌ Exception in place_order: {e}")
        return {"error": {"message": str(e)}}

def check_contract_status(ws, contract_id, device_id):
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    try:
        ws.send(json.dumps(req)); response = ws.recv(); response_data = json.loads(response)
        if response_data.get('msg_type') == 'proposal_open_contract': return response_data['proposal_open_contract']
        else:
            log_message(device_id, f"⚠️ Unexpected response type for contract status: {response_data.get('msg_type')}.")
            return None
    except websocket.WebSocketTimeoutException:
        log_message(device_id, f"⚠️ Timeout waiting for contract info for ID {contract_id}.")
        return None
    except Exception as e:
        log_message(device_id, f"❌ Error checking contract status for ID {contract_id}: {e}")
        return None

def get_balance(ws, device_id):
    req = {"balance": 1, "subscribe": 1}
    try:
        ws.send(json.dumps(req)); response = json.loads(ws.recv())
        if 'balance' in response: return response['balance']['balance']
        elif 'error' in response: log_message(device_id, f"❌ Error getting balance: {response['error']['message']}"); return None
        return None
    except Exception as e: log_message(device_id, f"❌ Exception in get_balance: {e}"); return None

def run_bot_for_user(device_id):
    log_message(device_id, "🟢 بدأ تشغيل الروبوت لهذا المستخدم.")
    while True:
        state = get_bot_state(device_id)
        if not state or not state.is_running:
            log_message(device_id, "🛑 حالة الروبوت متوقفة. سيتم إيقاف عمل الروبوت.")
            break
        
        if state.is_trade_open:
            if state.trade_start_time and (datetime.now() - state.trade_start_time).total_seconds() >= 70:
                ws = None
                try:
                    ws = websocket.WebSocket(); ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10); auth_req = {"authorize": state.user_token}; ws.send(json.dumps(auth_req)); auth_response = json.loads(ws.recv())
                    if auth_response.get('error'):
                        log_message(device_id, "❌ فشل المصادقة أثناء التحقق من النتيجة."); update_bot_state(device_id, is_running=False, is_trade_open=False); continue
                    contract_info = check_contract_status(ws, state.contract_id, device_id)
                    if contract_info and contract_info.get('is_sold'):
                        profit = contract_info.get('profit', 0); wins = state.total_wins; losses = state.total_losses; consecutive = state.consecutive_losses; current_amount = state.current_amount
                        if profit > 0: consecutive = 0; wins += 1; current_amount = state.base_amount
                        elif profit < 0: consecutive += 1; losses += 1; next_bet = state.current_amount * 2.2; current_amount = max(state.base_amount, next_bet)
                        update_bot_state(device_id, is_trade_open=False, trade_start_time=None, contract_id=None, consecutive_losses=consecutive, total_wins=wins, total_losses=losses, current_amount=current_amount)
                        current_balance = get_balance(ws, device_id)
                        if current_balance is not None:
                            log_message(device_id, f"💰 الرصيد الحالي: {current_balance:.2f}")
                            if state.tp_target and state.initial_balance and (current_balance - state.initial_balance) >= state.tp_target:
                                log_message(device_id, f"🤑 تم الوصول إلى هدف الربح ({state.tp_target}$)! سيتم إيقاف الروبوت."); update_bot_state(device_id, is_running=False)
                        if consecutive >= state.max_consecutive_losses:
                            log_message(device_id, f"🛑 تم الوصول إلى الحد الأقصى للخسائر ({consecutive} خسارة متتالية)! سيتم إيقاف الروبوت."); update_bot_state(device_id, is_running=False)
                    else:
                        log_message(device_id, f"⚠ تعذر الحصول على معلومات العقد للمعرف: {state.contract_id}."); update_bot_state(device_id, is_trade_open=False, trade_start_time=None, contract_id=None)
                except Exception as e: log_message(device_id, f"❌ حدث خطأ أثناء الحصول على نتيجة الصفقة: {e}"); update_bot_state(device_id, is_trade_open=False)
                finally:
                    if ws and ws.connected: ws.close()
            time.sleep(1); continue
        
        now = datetime.now()
        if state.last_action_time and (now - state.last_action_time).total_seconds() < 60:
            time.sleep(1); continue
        update_bot_state(device_id, last_action_time=now)
        
        ws = None
        try:
            ws = websocket.WebSocket(); ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10); auth_req = {"authorize": state.user_token}; ws.send(json.dumps(auth_req)); auth_response = json.loads(ws.recv())
            if auth_response.get('error'): log_message(device_id, f"❌ فشل المصادقة: {auth_response['error']['message']}"); update_bot_state(device_id, is_running=False); continue
            if state.initial_balance is None:
                current_balance = get_balance(ws, device_id)
                if current_balance is not None:
                    update_bot_state(device_id, initial_balance=current_balance); log_message(device_id, f"💰 الرصيد الأولي: {current_balance}")
                else: log_message(device_id, "❌ فشل استرداد الرصيد الأولي.")
            
            ticks_to_request = 350; req = {"ticks_history": "R_100", "end": "latest", "count": ticks_to_request, "style": "ticks"}; ws.send(json.dumps(req)); tick_data = json.loads(ws.recv())
            if 'history' in tick_data and tick_data['history']['prices']:
                ticks = tick_data['history']['prices']; timestamps = tick_data['history']['times']; df_ticks = pd.DataFrame({'timestamp': timestamps, 'price': ticks})
                ticks_per_candle = 7; candles_df = ticks_to_ohlc_by_count(df_ticks, ticks_per_candle)
                provisional_decision, _, _, error_msg = analyse_data(candles_df, device_id)
                if error_msg: log_message(device_id, f"❌ خطأ في التحليل: {error_msg}"); continue
                
                final_signal = "Neutral"
                if provisional_decision == "Buy": final_signal = "Buy"
                elif provisional_decision == "Sell": final_signal = "Sell"

                if final_signal in ['Buy', 'Sell']:
                    proposal_req = {"proposal": 1, "amount": round(state.current_amount, 2), "basis": "stake", "contract_type": "CALL" if final_signal == 'Buy' else "PUT", "currency": "USD", "duration": 1, "duration_unit": "m", "symbol": "R_100", "passthrough": {"action": final_signal}}
                    ws.send(json.dumps(proposal_req)); proposal_response = json.loads(ws.recv())
                    if 'proposal' in proposal_response:
                        proposal_id = proposal_response['proposal']['id']; order_response = place_order(ws, proposal_id, state.current_amount, device_id)
                        if 'buy' in order_response and 'contract_id' in order_response['buy']:
                            update_bot_state(device_id, is_trade_open=True, trade_start_time=datetime.now(), contract_id=order_response['buy']['contract_id'])
                        elif 'error' in order_response: log_message(device_id, f"❌ فشلت الصفقة: {order_response['error']['message']}")
                        else: log_message(device_id, f"❌ استجابة غير متوقعة للصفقة: {order_response}")
                    else: log_message(device_id, f"❌ فشل الاقتراح: {proposal_response.get('error', {}).get('message', 'خطأ غير معروف')}")
            else: log_message(device_id, "❌ خطأ: تعذر الحصول على بيانات السعر أو أن البيانات فارغة.")
        except Exception as e: log_message(device_id, f"❌ حدث خطأ أثناء دورة التداول: {e}")
        finally:
            if ws and ws.connected: ws.close()

def update_bot_state_from_ui(device_id, **kwargs):
    session = Session()
    try:
        state = session.query(BotState).filter_by(device_id=device_id).first()
        if state:
            for key, value in kwargs.items(): setattr(state, key, value)
            session.commit()
        else:
            new_state = BotState(device_id=device_id, **kwargs)
            session.add(new_state)
            session.commit()
    finally: session.close()

def get_logs(device_id):
    session = Session()
    try:
        logs = session.query(BotLog).filter_by(device_id=device_id).order_by(BotLog.timestamp.desc()).limit(100).all()
        return [f"[{log.timestamp.strftime('%H:%M:%S')}] {log.message}" for log in reversed(logs)]
    finally: session.close()

def main():
    st.title("KHOURYBOT - روبوت التداول الآلي 🤖")
    
    sync_allowed_users_from_file()
    
    # 🆕 تغيير الكود هنا:
    # استخدام UUID لإنشاء معرف فريد للجلسة الحالية
    if "device_id" not in st.session_state:
        st.session_state.device_id = str(uuid.uuid4())
    
    device_id = st.session_state.device_id
    
    # Check if this device ID exists in the database. If not, add it.
    session = Session()
    try:
        device = session.query(Device).filter_by(device_id=device_id).first()
        if not device:
            new_device = Device(device_id=device_id)
            session.add(new_device)
            session.commit()
            log_message(device_id, "تم تسجيل معرف جهاز جديد في قاعدة البيانات.")
    except Exception as e:
        st.error(f"Database error while checking/adding device: {e}")
    finally:
        session.close()

    st.header(f"معرف جهازك:")
    st.code(device_id)
    
    if not is_user_allowed(device_id):
        st.info("⚠️ لم يتم تفعيل معرف جهازك بعد. يرجى إرسال المعرف للمسؤول لتفعيله.")
        if st.button("التحقق من حالة التفعيل"):
            sync_allowed_users_from_file()
            if is_user_allowed(device_id):
                st.session_state.is_authenticated = True
                st.success("تم تفعيل معرف جهازك! يمكنك الآن استخدام التطبيق.")
                st.rerun()
            else:
                st.warning("لم يتم تفعيل المعرف بعد. يرجى المحاولة مرة أخرى لاحقاً.")
        return
    
    st.session_state.is_authenticated = True
    bot_state = get_bot_state(device_id)
    if not bot_state: update_bot_state_from_ui(device_id)
    bot_state = get_bot_state(device_id)
    
    if 'bot_thread' not in st.session_state: st.session_state.bot_thread = None
    
    status_placeholder = st.empty()
    timer_placeholder = st.empty()
    if bot_state and bot_state.is_running:
        if not st.session_state.bot_thread or not st.session_state.bot_thread.is_alive():
            st.session_state.bot_thread = threading.Thread(target=run_bot_for_user, args=(device_id,), daemon=True)
            st.session_state.bot_thread.start()
        
        if not bot_state.is_trade_open:
            status_placeholder.info("جاري التحليل...")
            now = datetime.now()
            last_action_time = bot_state.last_action_time if bot_state.last_action_time else now
            seconds_since_last_action = (now - last_action_time).total_seconds()
            seconds_left = max(0, 60 - seconds_since_last_action)
            timer_placeholder.metric("الخطوة التالية خلال", f"{int(seconds_left)}s")
        else:
            status_placeholder.info("في انتظار نتيجة الصفقة...")
            timer_placeholder.empty()
    else:
        if st.session_state.bot_thread and st.session_state.bot_thread.is_alive():
            status_placeholder.warning("جاري إيقاف الروبوت...")
        else:
            status_placeholder.empty()
            timer_placeholder.empty()
    
    st.header("1. إعدادات الروبوت")
    user_token = st.text_input("أدخل رمز Deriv API الخاص بك:", type="password", key="api_token_input", value=bot_state.user_token if bot_state and bot_state.user_token else "")
    base_amount = st.number_input("المبلغ الأساسي ($)", min_value=0.5, step=0.5, value=bot_state.base_amount if bot_state else 0.5)
    tp_target = st.number_input("هدف الربح ($)", min_value=1.0, step=1.0, value=bot_state.tp_target if bot_state and bot_state.tp_target else 1.0)
    max_consecutive_losses = st.number_input("الحد الأقصى للخسائر المتتالية", min_value=1, step=1, value=bot_state.max_consecutive_losses if bot_state else 5)
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("بدء الروبوت", type="primary"):
            if not user_token: st.error("يرجى إدخال رمز API صحيح قبل بدء الروبوت.")
            else: update_bot_state_from_ui(device_id, is_running=True, user_token=user_token, base_amount=base_amount, current_amount=base_amount, consecutive_losses=0, total_wins=0, total_losses=0, tp_target=tp_target, max_consecutive_losses=max_consecutive_losses); st.success("تم بدء الروبوت!"); st.rerun()
    with col2:
        if st.button("إيقاف الروبوت"): update_bot_state_from_ui(device_id, is_running=False); st.warning("سيتوقف الروبوت قريباً."); st.rerun()

    st.markdown("---")
    st.header("2. سجلات الروبوت المباشرة")
    if bot_state: st.markdown(f"*انتصارات: {bot_state.total_wins}* | *خسائر: {bot_state.total_losses}*")
    log_records = get_logs(device_id)
    with st.container(height=600):
        st.text_area("السجلات", "\n".join(log_records), height=600, key="logs_textarea")
        
    if bot_state and bot_state.is_running:
         time.sleep(1)
         st.experimental_rerun()

if __name__ == "__main__":
    main()
