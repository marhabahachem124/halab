import streamlit as st
import websocket
import json
import pandas as pd
import ta
import time

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

# دالة تحليل البيانات
def analyse_data(data):
    try:
        if data.empty or len(data) < 30:
            st.error("خطأ: لا توجد بيانات كافية للتحليل. يرجى المحاولة مرة أخرى.")
            return None
        
        data = data.tail(30).copy()

        # إضافة المؤشرات باستخدام ta
        data['RSI'] = ta.momentum.RSIIndicator(data['Close']).rsi()
        data['MACD'] = ta.trend.MACD(data['Close']).macd()
        data['MACD_Signal'] = ta.trend.MACD(data['Close']).macd_signal()
        # تعديل استدعاء Awesome Oscillator لتجنب الخطأ السابق
        data['Awesome_Oscillator'] = ta.momentum.awesome_oscillator(data['High'], data['Low'])
        data['ROC'] = ta.momentum.ROCIndicator(data['Close']).roc()
        data['Stoch_K'] = ta.momentum.StochasticOscillator(data['High'], data['Low'], data['Close']).stoch()
        data['Bollinger_Bands_PctB'] = ta.volatility.BollingerBands(data['Close']).bollinger_pband()
        data['ADX'] = ta.trend.ADXIndicator(data['High'], data['Low'], data['Close']).adx()
        data['MFI'] = ta.volume.MFIIndicator(data['High'], data['Low'], data['Close'], data['Volume']).money_flow_index()
        
        # --- نظام النقاط الأقوى والأكثر تطوراً ---
        score = 0
        
        up_candles = sum(1 for i, row in data.iterrows() if row['Close'] > row['Open'])
        down_candles = sum(1 for i, row in data.iterrows() if row['Close'] < row['Open'])
        score += (up_candles - down_candles) * 5
        
        if len(data) >= 2:
            last_candle = data.iloc[-1]
            prev_candle = data.iloc[-2]
            
            if last_candle['Close'] > last_candle['Open'] and prev_candle['Close'] < prev_candle['Open']:
                if (last_candle['Close'] - last_candle['Open']) > abs(prev_candle['Close'] - prev_candle['Open']) * 1.5:
                    score += 20
            elif last_candle['Close'] < last_candle['Open'] and prev_candle['Close'] > prev_candle['Open']:
                if abs(last_candle['Close'] - last_candle['Open']) > abs(prev_candle['Close'] - prev_candle['Open']) * 1.5:
                    score -= 20
            
            body = abs(last_candle['Close'] - last_candle['Open'])
            total_range = last_candle['High'] - last_candle['Low']
            if total_range > 0 and body / total_range < 0.3:
                if last_candle['Close'] > last_candle['Open']:
                    score += 15
                else:
                    score -= 15

        if data['MACD'].iloc[-1] > data['MACD_Signal'].iloc[-1] and data['MACD'].iloc[-2] <= data['MACD_Signal'].iloc[-2]:
            score += 20
        elif data['MACD'].iloc[-1] < data['MACD_Signal'].iloc[-1] and data['MACD'].iloc[-2] >= data['MACD_Signal'].iloc[-2]:
            score -= 20
        
        if data['Awesome_Oscillator'].iloc[-1] > 0 and data['Awesome_Oscillator'].iloc[-2] <= 0:
            score += 15
        elif data['Awesome_Oscillator'].iloc[-1] < 0 and data['Awesome_Oscillator'].iloc[-2] >= 0:
            score -= 15

        if data['ROC'].iloc[-1] > 0:
            score += 10
        elif data['ROC'].iloc[-1] < 0:
            score -= 10
            
        if data['RSI'].iloc[-1] > 70:
            score -= 10
        elif data['RSI'].iloc[-1] < 30:
            score += 10
            
        if data['Stoch_K'].iloc[-1] > 80:
            score -= 10
        elif data['Stoch_K'].iloc[-1] < 20:
            score += 10
            
        if data['Bollinger_Bands_PctB'].iloc[-1] > 1.0:
            score -= 5
        elif data['Bollinger_Bands_PctB'].iloc[-1] < 0.0:
            score += 5
        
        if data['ADX'].iloc[-1] > 25:
            if data.iloc[-1]['Close'] > data.iloc[-1]['Open']:
                score += 5
            else:
                score -= 5
                
        if data['MFI'].iloc[-1] > 80:
            score -= 5
        elif data['MFI'].iloc[-1] < 20:
            score += 5

        final_decision = "⚠️ متعادل"
        total_strength = 50

        if score > 0:
            final_decision = "📈 صعود"
            total_strength = min(100, 50 + score)
        elif score < 0:
            final_decision = "📉 هبوط"
            total_strength = min(100, 50 + abs(score))
        
        last_candle_is_up = data.iloc[-1]['Close'] > data.iloc[-1]['Open']

        if (final_decision == "📈 صعود" and not last_candle_is_up):
            final_decision = "📉 هبوط"
            total_strength = min(100, 50 + abs(score))
        elif (final_decision == "📉 هبوط" and last_candle_is_up):
            final_decision = "📈 صعود"
            total_strength = min(100, 50 + abs(score))
            
        if final_decision == "⚠️ متعادل":
            if last_candle_is_up:
                final_decision = "📈 صعود"
                total_strength = 50
            else:
                final_decision = "📉 هبوط"
                total_strength = 50
        
        return {
            'final_decision': final_decision,
            'total_strength': total_strength
        }

    except Exception as e:
        st.error(f"حدث خطأ في التحليل: {e}")
        return None

# دالة الاتصال بـ WebSocket مع إعادة المحاولة
def fetch_data_from_websocket(symbol, max_retries=3):
    retries = 0
    while retries < max_retries:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
            request = {
                "ticks_history": symbol,
                "end": "latest",
                "count": 5000,
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

st.header("قم باختيار الزوج والفريم لتحليل السوق:")

col1, col2 = st.columns(2)

with col1:
    symbol_map = {
        'EUR/USD': 'frxEURUSD',
        'EUR/GBP': 'frxEURGBP',
        'EUR/JPY': 'frxEURJPY'
    }
    selected_pair_name = st.selectbox(
        'اختر زوج العملات:',
        options=list(symbol_map.keys())
    )
    selected_symbol = symbol_map[selected_pair_name]

with col2:
    timeframe_map = {
        '1 دقيقة': 60,
        '5 دقائق': 300,
        '15 دقيقة': 900
    }
    selected_timeframe_name = st.selectbox(
        'اختر الفريم الزمني:',
        options=list(timeframe_map.keys())
    )
    selected_timeframe = timeframe_map[selected_timeframe_name]

if st.button('احصل على الإشارة الآن'):
    with st.spinner('جاري جلب التيكات وتحويلها إلى شموع... يرجى الانتظار'):
        ticks_data = fetch_data_from_websocket(selected_symbol)
        
        if not ticks_data.empty:
            st.info("تم جلب التيكات بنجاح. جاري تحويلها وتحليلها...")
            
            candles_data = ticks_to_ohlc(ticks_data, selected_timeframe)
            
            if not candles_data.empty:
                results = analyse_data(candles_data)
                
                if results:
                    st.markdown("---")
                    st.header("نتائج التحليل والإشارة:")
                    st.success("🎉 تم التحليل بنجاح! إليك الإشارة:")
                    
                    st.markdown(f"## **الإشارة**: {results['final_decision']}")
                    st.markdown(f"## **قوتها**: {results['total_strength']}%")
                    
                    st.markdown("---")
                    st.info("💡 ملاحظة: التحليل يعتمد على بيانات السوق اللحظية. يرجى استخدامه كأداة مساعدة في اتخاذ القرار.")
            else:
                st.error("فشل في تحويل التيكات إلى شموع. يرجى المحاولة مرة أخرى.")
        else:
            st.error("فشل جلب التيكات. يرجى التحقق من اتصالك بالإنترنت والمحاولة لاحقًا.")
