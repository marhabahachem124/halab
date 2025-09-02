import streamlit as st
import websocket
import json
import pandas as pd
import ta
import time
import numpy as np

# إعدادات الصفحة والأيقونة
st.set_page_config(
    page_title="KHOURYBOT - بوت تحليل الشارت",
    page_icon="https://i.imgur.com/KHOURYBOT_Logo.png"
)

# دالة التحويل من تيك إلى شموع OHLC
def ticks_to_ohlc(ticks_df, timeframe_seconds):
    if ticks_df.empty:
        return pd.DataFrame()
        
    ticks_df['timestamp'] = pd.to_datetime(ticks_df['timestamp'], unit='s')
    ticks_df.set_index('timestamp', inplace=True)
    
    ohlc_data = ticks_df['price'].resample(f'{timeframe_seconds}s').ohlc()
    ohlc_data['Volume'] = ticks_df['price'].resample(f'{timeframe_seconds}s').count()
    
    ohlc_data.dropna(inplace=True)
    ohlc_data.reset_index(inplace=True)
    ohlc_data.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'}, inplace=True)
    
    return ohlc_data

# دالة لتحديد مناطق الدعم والمقاومة
def find_support_resistance(data):
    highs = data['High'].iloc[-50:]
    lows = data['Low'].iloc[-50:]
    
    support = lows.min()
    resistance = highs.max()
    
    return support, resistance

# دالة لتحليل أنماط الشموع
def analyze_candlesticks(data):
    score = 0
    
    if len(data) >= 2:
        last = data.iloc[-1]
        prev = data.iloc[-2]
        
        # Bullish Engulfing
        if (last['Close'] > last['Open'] and prev['Close'] < prev['Open'] and
            last['High'] > prev['High'] and last['Low'] < prev['Low']):
            score += 30
            st.info("💡 تم رصد نمط شمعة **ابتلاعية صعودية** قوية.")
        
        # Bearish Engulfing
        if (last['Close'] < last['Open'] and prev['Close'] > prev['Open'] and
            last['High'] > prev['High'] and last['Low'] < prev['Low']):
            score -= 30
            st.info("💡 تم رصد نمط شمعة **ابتلاعية هبوطية** قوية.")

        # Hammer & Shooting Star
        body = abs(last['Close'] - last['Open'])
        lower_shadow = last['Open'] - last['Low'] if last['Open'] > last['Close'] else last['Close'] - last['Low']
        upper_shadow = last['High'] - last['Close'] if last['Open'] > last['Close'] else last['High'] - last['Open']
        
        if last['Close'] > last['Open'] and lower_shadow > body * 2 and upper_shadow < body:
            score += 20
            st.info("💡 تم رصد نمط شمعة **مطرقة** قوية.")
        
        if last['Close'] < last['Open'] and upper_shadow > body * 2 and lower_shadow < body:
            score -= 20
            st.info("💡 تم رصد نمط شمعة **نجم الرماية** قوية.")
            
    return score

# دالة تحليل البيانات
def analyse_data(data):
    try:
        if data.empty or len(data) < 50:
            return None, "خطأ: لا توجد بيانات كافية للتحليل (أقل من 50 شمعة)."

        data = data.tail(50).copy()

        # إضافة المؤشرات
        data['RSI'] = ta.momentum.RSIIndicator(data['Close']).rsi()
        data['MACD'] = ta.trend.MACD(data['Close']).macd()
        data['MACD_Signal'] = ta.trend.MACD(data['Close']).macd_signal()
        data['Awesome_Oscillator'] = ta.momentum.awesome_oscillator(data['High'], data['Low'])
        data['ROC'] = ta.momentum.ROCIndicator(data['Close']).roc()
        data['Stoch_K'] = ta.momentum.StochasticOscillator(data['High'], data['Low'], data['Close']).stoch()
        data['Bollinger_Bands_PctB'] = ta.volatility.BollingerBands(data['Close']).bollinger_pband()
        data['ADX'] = ta.trend.ADXIndicator(data['High'], data['Low'], data['Close']).adx()
        data['MFI'] = ta.volume.MFIIndicator(data['High'], data['Low'], data['Close'], data['Volume']).money_flow_index()
        data['Aroon_Up'] = ta.trend.AroonIndicator(data['Close']).aroon_up()
        data['Aroon_Down'] = ta.trend.AroonIndicator(data['Close']).aroon_down()
        data['Vortex_P'] = ta.trend.VortexIndicator(data['High'], data['Low'], data['Close']).vortex_indicator_pos()
        data['Vortex_N'] = ta.trend.VortexIndicator(data['High'], data['Low'], data['Close']).vortex_indicator_neg()
        data['SAR'] = ta.trend.PSARIndicator(data['High'], data['Low'], data['Close']).psar()
        
        # --- نظام النقاط الشامل والمتقدم ---
        score = 0
        last_close = data.iloc[-1]['Close']
        last_candle_is_up = last_close > data.iloc[-1]['Open']
        
        # 1. تحليل أنماط الشموع (وزن عالي جداً)
        candlestick_score = analyze_candlesticks(data)
        score += candlestick_score
        
        # 2. تحليل مناطق الدعم والمقاومة (وزن عالي)
        support, resistance = find_support_resistance(data)
        if last_close > resistance * 1.0001: score += 40
        elif last_close < support * 0.9999: score -= 40
        if last_close < resistance and last_close > resistance * 0.9999: score -= 25
        if last_close > support and last_close < support * 1.0001: score += 25
        
        # 3. تحليل الزخم والمؤشرات (وزن متوسط)
        if data['MACD'].iloc[-1] > data['MACD_Signal'].iloc[-1]: score += 15
        elif data['MACD'].iloc[-1] < data['MACD_Signal'].iloc[-1]: score -= 15
        
        if data['Awesome_Oscillator'].iloc[-1] > 0: score += 10
        elif data['Awesome_Oscillator'].iloc[-1] < 0: score -= 10
        
        if data['ROC'].iloc[-1] > 0: score += 10
        elif data['ROC'].iloc[-1] < 0: score -= 10
        
        # 4. المؤشرات الأخرى (وزن أقل)
        if data['RSI'].iloc[-1] > 70: score -= 10
        elif data['RSI'].iloc[-1] < 30: score += 10
        
        if data['Stoch_K'].iloc[-1] > 80: score -= 10
        elif data['Stoch_K'].iloc[-1] < 20: score += 10
        
        if data['Aroon_Up'].iloc[-1] > data['Aroon_Down'].iloc[-1] and data['Aroon_Up'].iloc[-1] > 50: score += 10
        elif data['Aroon_Down'].iloc[-1] > data['Aroon_Up'].iloc[-1] and data['Aroon_Down'].iloc[-1] > 50: score -= 10
        
        if data['Vortex_P'].iloc[-1] > data['Vortex_N'].iloc[-1]: score += 10
        elif data['Vortex_P'].iloc[-1] < data['Vortex_N'].iloc[-1]: score -= 10
        
        if data.iloc[-1]['Close'] > data['SAR'].iloc[-1]: score += 15
        elif data.iloc[-1]['Close'] < data['SAR'].iloc[-1]: score -= 15
        
        # --- التحقق النهائي من الإشارة مع شمعة الـ 5 دقائق الاصطناعية ---
        provisional_decision = "⚠️ متعادل"
        if score > 40: provisional_decision = "📈 صعود"
        elif score < -40: provisional_decision = "📉 هبوط"

        if len(data) >= 5:
            last_5_candles = data.tail(5)
            synthetic_open = last_5_candles.iloc[0]['Open']
            synthetic_close = last_5_candles.iloc[-1]['Close']
            
            # شرط التأكيد: يجب أن تكون الشمعة الاصطناعية في نفس اتجاه الإشارة
            if (provisional_decision == "📈 صعود" and synthetic_close > synthetic_open):
                return "📈 صعود", None
            elif (provisional_decision == "📉 هبوط" and synthetic_close < synthetic_open):
                return "📉 هبوط", None
            else:
                st.warning("⚠️ المؤشرات تعطي إشارة، لكن حركة آخر 5 دقائق لا تؤكدها.")
                return "⚠️ متعادل", None
        else:
            return provisional_decision, None
        
    except Exception as e:
        return None, f"حدث خطأ في التحليل: {e}"

# دالة الاتصال بـ WebSocket مع إعادة المحاولة
def fetch_data_from_websocket(symbol, count, max_retries=3):
    retries = 0
    while retries < max_retries:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
            request = {
                "ticks_history": symbol,
                "end": "latest",
                "count": count,
                "style": "ticks"
            }
            ws.send(json.dumps(request))
            response = json.loads(ws.recv())
            ws.close()
            
            if 'history' in response:
                ticks = response['history']['prices']
                timestamps = response['history']['times']
                df = pd.DataFrame({'timestamp': timestamps, 'price': ticks})
                return df
            else:
                retries += 1
                time.sleep(1)
                st.info(f"فشل في جلب التيكات. جاري إعادة المحاولة ({retries}/{max_retries})...")
        except Exception as e:
            retries += 1
            time.sleep(1)
            st.warning(f"خطأ في الاتصال. جاري إعادة المحاولة ({retries}/{max_retries})...")
    st.error("فشل جلب البيانات بعد عدة محاولات.")
    return pd.DataFrame()


# --- تصميم الواجهة باستخدام Streamlit ---
st.title("WELCOME WITH KHOURYBOT 🤖")
st.markdown("---")
st.header("تحليل فريم الدقيقة الواحدة:")

col1, col2 = st.columns(2)

with col1:
    symbol_map = {'EUR/USD': 'frxEURUSD', 'EUR/GBP': 'frxEURGBP', 'EUR/JPY': 'frxEURJPY'}
    selected_pair_name = st.selectbox('اختر زوج العملات:', options=list(symbol_map.keys()))
    selected_symbol = symbol_map[selected_pair_name]

if st.button('احصل على الإشارة الآن'):
    with st.spinner('جاري جلب البيانات وتحليلها على فريم 1 دقيقة...'):
        ticks_1min = fetch_data_from_websocket(selected_symbol, count=20000)
        if ticks_1min.empty:
            st.error("فشل في جلب بيانات فريم 1 دقيقة. يرجى المحاولة لاحقًا.")
        else:
            candles_1min = ticks_to_ohlc(ticks_1min, 60)
            entry_signal, error = analyse_data(candles_1min) 
            
            st.markdown("---")
            st.header("نتائج التحليل والإشارة:")

            if error:
                st.error(error)
            elif entry_signal != "⚠️ متعادل":
                st.success(f"🎉 الإشارة الأقوى هي: **{entry_signal}**.")
                st.info("💡 هذه الإشارة بناءً على تحليل فريم الدقيقة الواحدة.")
            else:
                st.warning("⚠️ لا توجد إشارة قوية حاليًا.")
                st.info("التحليل لم يجد إشارة واضحة للدخول في هذه اللحظة.")
