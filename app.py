import streamlit as st
import websocket
import json
import pandas as pd
import ta
import time
import numpy as np
import requests
from datetime import datetime, timedelta
import os
import collections
import random
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

# --- نظام الترخيص القائم على الملفات ---
# هذا الملف يحتوي على الأرقام التعريفية للمستخدمين المسموح لهم بتشغيل البوت.
ALLOWED_USERS_FILE = 'user_ids.txt'

# --- إعداد قاعدة البيانات ---
DATABASE_URL = "postgresql://khourybot_db_user:wlVAwKwLhfzzH9HFsRMNo3IOo4dX6DYm@dpg-d2smi46r433s73frbbcg-a/khourybot_db"
engine = sa.create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base()

class Device(Base):
    __tablename__ = 'devices'
    id = sa.Column(sa.Integer, primary_key=True)
    device_id = sa.Column(sa.String, unique=True, nullable=False)

Base.metadata.create_all(engine)

# --- متغيرات التهيئة وحالة التطبيق ---
if 'is_authenticated' not in st.session_state:
    st.session_state.is_authenticated = False
if 'user_id' not in st.session_state:
    st.session_state.user_id = None
if 'bot_running' in st.session_state and st.session_state.bot_running:
    if 'current_amount' not in st.session_state:
        st.session_state.current_amount = st.session_state.base_amount
    if 'consecutive_losses' not in st.session_state:
        st.session_state.consecutive_losses = 0
else:
    st.session_state.bot_running = False

if 'is_trade_open' not in st.session_state:
    st.session_state.is_trade_open = False
if 'trade_start_time' not in st.session_state:
    st.session_state.trade_start_time = None
if 'contract_id' not in st.session_state:
    st.session_state.contract_id = None
if 'log_records' not in st.session_state:
    st.session_state.log_records = []
if 'user_token' not in st.session_state:
    st.session_state.user_token = None
if 'tick_history' not in st.session_state:
    st.session_state.tick_history = collections.deque(maxlen=200)
if 'current_amount' not in st.session_state:
    st.session_state.current_amount = 0.5
if 'base_amount' not in st.session_state:
    st.session_state.base_amount = 0.5
if 'consecutive_losses' not in st.session_state:
    st.session_state.consecutive_losses = 0
if 'initial_balance' not in st.session_state:
    st.session_state.initial_balance = None
if 'tp_target' not in st.session_state:
    st.session_state.tp_target = None
if 'max_consecutive_losses' not in st.session_state:
    st.session_state.max_consecutive_losses = 5
if 'last_action_time' not in st.session_state:
    st.session_state.last_action_time = datetime.min
if 'page' not in st.session_state:
    st.session_state.page = 'inputs'
if 'is_analysing' not in st.session_state:
    st.session_state.is_analysing = False

# --- فحص الترخيص وتوليد الرقم التعريفي للمستخدم ---
def get_or_create_device_id():
    """
    يسترجع الرقم التعريفي للجهاز من قاعدة البيانات أو ينشئ واحدًا جديدًا ويحفظه.
    """
    session = Session()
    try:
        device = session.query(Device).first()
        if device:
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ تم استرجاع الرقم التعريفي من قاعدة البيانات.")
            return device.device_id
        else:
            new_id = str(random.randint(1000000000000000, 9999999999999999))
            new_device = Device(device_id=new_id)
            session.add(new_device)
            session.commit()
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✨ تم إنشاء رقم تعريفي جديد وحفظه في قاعدة البيانات.")
            return new_id
    except Exception as e:
        session.rollback()
        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ فشل الاتصال بقاعدة البيانات أو حدث خطأ: {e}")
        return None
    finally:
        session.close()

def is_user_allowed(user_id):
    """يتحقق مما إذا كان الرقم التعريفي للمستخدم موجودًا في القائمة المسموح بها."""
    try:
        with open(ALLOWED_USERS_FILE, 'r') as f:
            allowed_ids = {line.strip() for line in f}
            if user_id in allowed_ids:
                return True
    except FileNotFoundError:
        st.error(f"خطأ: لم يتم العثور على '{ALLOWED_USERS_FILE}'. يرجى إنشاء هذا الملف بقائمة من الأرقام التعريفية المسموح بها للمستخدمين.")
        return False
    except Exception as e:
        st.error(f"خطأ أثناء قراءة '{ALLOWED_USERS_FILE}': {e}")
        return False
    return False

# --- الدوال من الكود الخاص بك ---
def ticks_to_ohlc_by_count(ticks_df, tick_count):
    if ticks_df.empty:
        return pd.DataFrame()

    ohlc_data = []
    prices = ticks_df['price'].values
    timestamps = ticks_df['timestamp'].values
    
    for i in range(0, len(prices), tick_count):
        chunk = prices[i:i + tick_count]
        if len(chunk) == tick_count:
            open_price = chunk[0]
            high_price = np.max(chunk)
            low_price = np.min(chunk)
            close_price = chunk[-1]
            ohlc_data.append({
                'timestamp': timestamps[i+tick_count-1],
                'Open': open_price,
                'High': high_price,
                'Low': low_price,
                'Close': close_price,
                'Volume': tick_count
            })
    ohlc_df = pd.DataFrame(ohlc_data)
    if not ohlc_df.empty:
        ohlc_df['timestamp'] = pd.to_datetime(ohlc_df['timestamp'], unit='s')
        ohlc_df.set_index('timestamp', inplace=True)
    return ohlc_df

def find_support_resistance(data):
    supports = []
    resistances = []
    for i in range(1, len(data) - 1):
        prev_candle = data.iloc[i-1]
        current_candle = data.iloc[i]
        next_candle = data.iloc[i+1]
        
        if prev_candle['Close'] < prev_candle['Open'] and next_candle['Close'] > next_candle['Open']:
            supports.append(current_candle['Low'])
            
        if prev_candle['Close'] > prev_candle['Open'] and next_candle['Close'] < next_candle['Open']:
            resistances.append(current_candle['High'])
            
    supports = sorted(list(set(supports)), reverse=True)[:5]
    resistances = sorted(list(set(resistances)))[:5]
    return supports, resistances

def analyze_candlesticks(data):
    signal = "Neutral"
    if len(data) >= 2:
        last = data.iloc[-1]
        prev = data.iloc[-2]
        
        # Bullish Engulfing
        if (last['Close'] > last['Open'] and prev['Close'] < prev['Open'] and last['High'] > prev['High'] and last['Low'] < prev['Low']):
            signal = "Buy"
        
        # Bearish Engulfing
        if (last['Close'] < last['Open'] and prev['Close'] > prev['Open'] and last['High'] > prev['High'] and last['Low'] < prev['Low']):
            signal = "Sell"

        # Hammer (Bullish)
        body = abs(last['Close'] - last['Open'])
        lower_shadow = last['Open'] - last['Low'] if last['Open'] > last['Close'] else last['Close'] - last['Low']
        upper_shadow = last['High'] - last['Close'] if last['Open'] > last['Close'] else last['High'] - last['Open']
        
        if last['Close'] > last['Open'] and lower_shadow > body * 2 and upper_shadow < body:
            signal = "Buy"
        
        # Inverted Hammer (Bearish)
        if last['Close'] < last['Open'] and upper_shadow > body * 2 and lower_shadow < body:
            signal = "Sell"
    return signal

def analyse_data(data):
    try:
        if data.empty or len(data) < 50:
            return None, "Error: Insufficient data for analysis (less than 50 candles)."

        data = data.tail(50).copy()

        signals = []

        # RSI Signal
        data['RSI'] = ta.momentum.RSIIndicator(data['Close']).rsi()
        if data['RSI'].iloc[-1] < 30: signals.append("Buy")
        elif data['RSI'].iloc[-1] > 70: signals.append("Sell")

        # Stoch Signal
        data['Stoch_K'] = ta.momentum.StochasticOscillator(data['High'], data['Low'], data['Close']).stoch()
        if data['Stoch_K'].iloc[-1] < 20: signals.append("Buy")
        elif data['Stoch_K'].iloc[-1] > 80: signals.append("Sell")

        # ROC Signal
        data['ROC'] = ta.momentum.ROCIndicator(data['Close']).roc()
        if data['ROC'].iloc[-1] > 0: signals.append("Buy")
        elif data['ROC'].iloc[-1] < 0: signals.append("Sell")
        
        # ADX Signal
        adx_indicator = ta.trend.ADXIndicator(data['High'], data['Low'], data['Close'])
        data['ADX'] = adx_indicator.adx()
        data['ADX_pos'] = adx_indicator.adx_pos()
        data['ADX_neg'] = adx_indicator.adx_neg()
        if data['ADX'].iloc[-1] > 25:
            if data['ADX_pos'].iloc[-1] > data['ADX_neg'].iloc[-1]: signals.append("Buy")
            elif data['ADX_neg'].iloc[-1] > data['ADX_pos'].iloc[-1]: signals.append("Sell")

        # MACD Signal
        macd_indicator = ta.trend.MACD(data['Close'])
        data['MACD'] = macd_indicator.macd()
        data['MACD_signal'] = macd_indicator.macd_signal()
        if data['MACD'].iloc[-1] > data['MACD_signal'].iloc[-1]: signals.append("Buy")
        elif data['MACD'].iloc[-1] < data['MACD_signal'].iloc[-1]: signals.append("Sell")

        # Ichimoku Signal
        ichimoku_indicator = ta.trend.IchimokuIndicator(data['High'], data['Low'])
        data['ichimoku_a'] = ichimoku_indicator.ichimoku_a()
        data['ichimoku_b'] = ichimoku_indicator.ichimoku_b()
        last_close_ichimoku = data.iloc[-1]['Close']
        cloud_a = data.iloc[-1]['ichimoku_a']
        cloud_b = data.iloc[-1]['ichimoku_b']
        if last_close_ichimoku > max(cloud_a, cloud_b): signals.append("Buy")
        elif last_close_ichimoku < min(cloud_a, cloud_b): signals.append("Sell")

        # EMA Signal
        if len(data) >= 20:
            data['ema10'] = ta.trend.EMAIndicator(data['Close'], window=10).ema_indicator()
            data['ema20'] = ta.trend.EMAIndicator(data['Close'], window=20).ema_indicator()
            if data['ema10'].iloc[-1] > data['ema20'].iloc[-1]: signals.append("Buy")
            elif data['ema10'].iloc[-1] < data['ema20'].iloc[-1]: signals.append("Sell")
            
            last_close = data.iloc[-1]['Close']
            if last_close > data['ema20'].iloc[-1] and last_close > data['ema10'].iloc[-1]: signals.append("Buy")
            elif last_close < data['ema20'].iloc[-1] and last_close < data['ema10'].iloc[-1]: signals.append("Sell")
        
        # Candlestick Signal
        candlestick_signal = analyze_candlesticks(data)
        if candlestick_signal != "Neutral":
            signals.append(candlestick_signal)

        # Support & Resistance Signal
        supports, resistances = find_support_resistance(data)
        last_close = data.iloc[-1]['Close']
        for support in supports:
            if abs(last_close - support) / support < 0.0001:  
                signals.append("Buy")
            elif last_close < support:
                signals.append("Sell")
        
        for resistance in resistances:
            if abs(last_close - resistance) / resistance < 0.0001:
                signals.append("Sell")
            elif last_close > resistance:
                signals.append("Buy")

        # Determine majority signal
        buy_count = signals.count("Buy")
        sell_count = signals.count("Sell")
        
        final_decision = "Neutral"
        if buy_count > sell_count:
            final_decision = "Buy"
        elif sell_count > buy_count:
            final_decision = "Sell"
        
        return final_decision, buy_count, sell_count, None
            
    except Exception as e:
        return None, 0, 0, f"An error occurred during analysis: {e}"

def place_order(ws, api_token, symbol, action, amount):
    req = {
        "buy": 1,
        "price": amount,
        "type": "CALL" if action == 'buy' else "PUT",
        "duration": 1,
        "duration_unit": "m",
        "symbol": symbol
    }
    ws.send(json.dumps(req))
    response = json.loads(ws.recv())
    return response

def check_contract_status(ws, contract_id):
    req = {"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}
    ws.send(json.dumps(req))
    while True:
        try:
            response = json.loads(ws.recv())
            if response.get('msg_type') == 'proposal_open_contract':
                is_sold = response['proposal_open_contract']['is_sold']
                if is_sold:
                    return response['proposal_open_contract']
        except websocket.WebSocketTimeoutException:
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ انتهت المهلة في انتظار معلومات العقد. جارٍ إعادة التحقق...")
            time.sleep(5)
        except Exception as e:
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ حدث خطأ أثناء التحقق من حالة العقد: {e}")
            return None

def get_balance(ws):
    req = {"balance": 1, "subscribe": 1}
    ws.send(json.dumps(req))
    response = json.loads(ws.recv())
    return response.get('balance', {}).get('balance')

# --- منطق التطبيق الرئيسي وواجهة المستخدم ---
st.title("KHOURYBOT - التداول الآلي 🤖")

# --- قسم المصادقة (Authentication) ---
st.session_state.user_id = get_or_create_device_id()

if st.session_state.user_id is None:
    st.error("تعذر الحصول على الرقم التعريفي للجهاز. يرجى التحقق من اتصال قاعدة البيانات.")
elif not st.session_state.is_authenticated:
    st.header("تسجيل الدخول إلى حسابك")
    if is_user_allowed(st.session_state.user_id):
        st.session_state.is_authenticated = True
        st.success("تم تنشيط جهازك! جارٍ إعادة التوجيه إلى الإعدادات...")
        st.balloons()
        st.rerun()
    else:
        st.warning("لم يتم تنشيط جهازك بعد. لتفعيل البوت، يرجى إرسال هذا الرقم التعريفي إلى مسؤول البوت:")
        st.code(st.session_state.user_id)
        st.info("بعد التفعيل، ما عليك سوى تحديث هذه الصفحة للمتابعة.")

else:
    # --- عرض الحالة والمؤقت ---
    status_placeholder = st.empty()
    timer_placeholder = st.empty()

    if st.session_state.bot_running:
        if not st.session_state.is_trade_open:
            status_placeholder.info("جارٍ التحليل...")
            now = datetime.now()
            next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
            seconds_left = max(0, (next_minute - now).seconds - 5)
            timer_placeholder.metric("الإجراء التالي خلال", f"{seconds_left}s")
        else:
            status_placeholder.info("في انتظار نتيجة الصفقة...")
            timer_placeholder.empty()
    else:
        status_placeholder.empty()
        timer_placeholder.empty()

    # --- التحقق من نتيجة الصفقة المعلقة ---
    if st.session_state.is_trade_open and st.session_state.trade_start_time:
        if datetime.now() >= st.session_state.trade_start_time + timedelta(seconds=70):
            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⏱️ جارٍ التحقق من نتيجة الصفقة...")
            
            ws = None
            try:
                ws = websocket.WebSocket()
                ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929")
                
                auth_req = {"authorize": st.session_state.user_token}
                ws.send(json.dumps(auth_req))
                auth_response = json.loads(ws.recv())
                
                if auth_response.get('error'):
                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ فشل إعادة الاتصال. خطأ في المصادقة.")
                    st.session_state.bot_running = False
                    st.session_state.is_trade_open = False
                else:
                    contract_info = check_contract_status(ws, st.session_state.contract_id)
                    
                    if contract_info:
                        profit = contract_info.get('profit', 0)
                        is_win = profit > 0
                        
                        if is_win:
                            st.session_state.consecutive_losses = 0
                            st.session_state.current_amount = st.session_state.base_amount
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🎉 فوز! الربح: {profit}")
                        else:
                            st.session_state.consecutive_losses += 1
                            st.session_state.current_amount *= 2.2
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💔 خسارة! الخسارة: {profit}")
                        
                        st.session_state.is_trade_open = False
                        
                        current_balance = get_balance(ws)
                        if current_balance is not None:
                            if st.session_state.initial_balance is None:
                                st.session_state.initial_balance = current_balance
                            
                            if st.session_state.tp_target and current_balance - st.session_state.initial_balance >= st.session_state.tp_target:
                                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🤑 تم الوصول إلى TP! تم إيقاف البوت.")
                                st.session_state.bot_running = False
                            
                        if st.session_state.consecutive_losses >= st.session_state.max_consecutive_losses:
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 تم الوصول إلى SL ({st.session_state.max_consecutive_losses} خسائر متتالية)! تم إيقاف البوت.")
                            st.session_state.bot_running = False
                            
                    else:
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ تعذر الحصول على معلومات العقد.")
                        st.session_state.is_trade_open = False
                        
            except Exception as e:
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ حدث خطأ في الحصول على النتيجة: {e}")
            finally:
                if ws:
                    ws.close()
            
            st.session_state.trade_start_time = None
            st.session_state.contract_id = None
            st.rerun()

    # --- منطق البوت الرئيسي (يعمل مرة واحدة كل دقيقة) ---
    if st.session_state.bot_running and not st.session_state.is_trade_open:
        now = datetime.now()
        seconds_in_minute = now.second
        
        # التحقق مما إذا كانت 60 ثانية قد مرت منذ آخر إجراء
        if (now - st.session_state.last_action_time).seconds >= 60:
            st.session_state.last_action_time = now
            if seconds_in_minute >= 55:
                
                ws = None
                try:
                    ws = websocket.WebSocket()
                    ws.connect("wss://blue.derivws.com/websockets/v3?app_id=16929")
                    
                    auth_req = {"authorize": st.session_state.user_token}
                    ws.send(json.dumps(auth_req))
                    auth_response = json.loads(ws.recv())
                    
                    if auth_response.get('error'):
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ فشلت المصادقة: {auth_response['error']['message']}")
                    else:
                        if st.session_state.initial_balance is None:
                            st.session_state.initial_balance = get_balance(ws)
                            st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 💰 الرصيد الأولي: {st.session_state.initial_balance}")
                            
                        req = {"ticks_history": "R_100", "end": "latest", "count": 70, "style": "ticks"}
                        ws.send(json.dumps(req))
                        tick_data = json.loads(ws.recv())
                        
                        if 'history' in tick_data:
                            ticks = tick_data['history']['prices']
                            timestamps = tick_data['history']['times']
                            df_ticks = pd.DataFrame({'timestamp': timestamps, 'price': ticks})
                            
                            if len(df_ticks) >= 70:
                                candles_5ticks = ticks_to_ohlc_by_count(df_ticks.tail(70), 5)

                                provisional_decision, buy_count, sell_count, error_msg = analyse_data(candles_5ticks)
                                
                                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 📈 إشارات الشراء: {buy_count}، إشارات البيع: {sell_count}")

                                last_5_ticks = df_ticks.tail(5)
                                last_5_signal = "Neutral"
                                if last_5_ticks['price'].iloc[-1] > last_5_ticks['price'].iloc[0]:
                                    last_5_signal = "Buy"
                                elif last_5_ticks['price'].iloc[-1] < last_5_ticks['price'].iloc[0]:
                                    last_5_signal = "Sell"

                                final_signal = "Neutral"
                                if provisional_decision == "Buy" and last_5_signal == "Buy":
                                    final_signal = "Buy"
                                elif provisional_decision == "Sell" and last_5_signal == "Sell":
                                    final_signal = "Sell"
                                
                                if final_signal is not None and final_signal in ['Buy', 'Sell']:
                                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 الإشارة المؤقتة: {provisional_decision}، إشارة آخر 5 تيكات: {last_5_signal}")
                                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ الإشارة النهائية: {final_signal.upper()}")
                                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ➡️ وضع أمر {final_signal.upper()} بـ {st.session_state.current_amount}$")
                                    order_response = place_order(ws, st.session_state.user_token, "R_100", final_signal, st.session_state.current_amount)
                                    
                                    if 'buy' in order_response:
                                        st.session_state.is_trade_open = True
                                        st.session_state.trade_start_time = datetime.now()
                                        st.session_state.contract_id = order_response.get('buy', {}).get('contract_id')
                                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ تم وضع الأمر. الرقم التعريفي: {st.session_state.contract_id}")
                                    else:
                                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ فشل الأمر: {order_response}")
                                else:
                                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ لم يتم العثور على إشارة قوية. لم يتم وضع أي صفقة.")


                    except Exception as e:
                        st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ حدث خطأ أثناء دورة التداول: {e}")
                    finally:
                        if ws:
                            ws.close()
                    
                    st.rerun()

        # --- عرض الصفحات بناءً على الحالة ---
        if st.session_state.page == 'inputs':
            st.header("1. إعدادات البوت")
            
            # حقل إدخال رمز API
            st.session_state.user_token = st.text_input("أدخل رمز Deriv API الخاص بك:", type="password", key="api_token_input")
            
            st.session_state.base_amount = st.number_input("المبلغ الأساسي", min_value=0.5, step=0.5, value=st.session_state.base_amount)
            st.session_state.tp_target = st.number_input("الهدف (Take Profit)", min_value=1.0, step=1.0, value=st.session_state.tp_target)
            
            start_button = st.button("بدء البوت")
            stop_button = st.button("إيقاف البوت")

            if start_button:
                if not st.session_state.user_token:
                    st.error("يرجى إدخال رمز API صالح قبل بدء البوت.")
                else:
                    st.session_state.bot_running = True
                    st.session_state.current_amount = st.session_state.base_amount
                    st.session_state.consecutive_losses = 0
                    st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🟢 تم تشغيل البوت.")
                    st.rerun()
            
            if stop_button:
                st.session_state.bot_running = False
                st.session_state.log_records.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 تم إيقاف البوت من قبل المستخدم.")
                st.rerun()
                
        elif st.session_state.page == 'logs':
            st.header("2. سجلات البوت المباشرة")
            with st.container(height=600):
                st.text_area("السجلات", "\n".join(st.session_state.log_records), height=600)

        # --- تذييل مع أزرار التنقل ---
        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("الإعدادات"):
                st.session_state.page = 'inputs'
                st.rerun()
        with col2:
            if st.button("السجلات"):
                st.session_state.page = 'logs'
                st.rerun()
                
        # إعادة تشغيل السكريبت بشكل دوري للتحقق من الوقت وتفعيل الدورة التالية
        time.sleep(1)
        st.rerun()
