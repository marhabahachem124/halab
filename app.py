import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import ta
import time

# إعدادات الصفحة والأيقونة
st.set_page_config(
    page_title="KHOURYBOT - بوت تحليل الشارت",
    page_icon="https://i.imgur.com/KHOURYBOT_Logo.png"
)

# دالة تحليل البيانات
def analyse_data(symbol, timeframe):
    try:
        data = yf.download(symbol, period='5d', interval=timeframe) 
        
        if data.empty or len(data) < 30:
            st.error("خطأ: لا توجد بيانات كافية لرمز الزوج المحدد.")
            return None

        data = data.tail(30)

        # إضافة المؤشرات
        data['RSI'] = ta.momentum.RSIIndicator(data['Close']).rsi()
        data['MACD'] = ta.trend.MACD(data['Close']).macd()
        data['MACD_Signal'] = ta.trend.MACD(data['Close']).macd_signal()
        data['Awesome_Oscillator'] = ta.momentum.AwesomeOscillator(data['High'], data['Low']).awesome_oscillator()
        data['ROC'] = ta.momentum.ROCIndicator(data['Close']).roc()
        data['Stoch_K'] = ta.momentum.StochasticOscillator(data['High'], data['Low'], data['Close']).stoch()
        data['Bollinger_Bands_PctB'] = ta.volatility.BollingerBands(data['Close']).bollinger_pband()
        data['ADX'] = ta.trend.ADXIndicator(data['High'], data['Low'], data['Close']).adx()
        data['MFI'] = ta.volume.MFIIndicator(data['High'], data['Low'], data['Close'], data['Volume']).money_flow_index()

        # --- نظام النقاط الأقوى والأكثر تطوراً ---
        score = 0
        
        # 1. نقاط الاتجاه العام (الوزن: 10%)
        up_candles = sum(1 for i, row in data.iterrows() if row['Close'] > row['Open'])
        down_candles = sum(1 for i, row in data.iterrows() if row['Close'] < row['Open'])
        score += (up_candles - down_candles) * 5
        
        # 2. نقاط أنماط الشموع (الوزن: 15%)
        if len(data) >= 2:
            last_candle = data.iloc[-1]
            prev_candle = data.iloc[-2]
            
            # نمط الابتلاع الصعودي/الهبوطي
            if last_candle['Close'] > last_candle['Open'] and prev_candle['Close'] < prev_candle['Open']:
                if (last_candle['Close'] - last_candle['Open']) > abs(prev_candle['Close'] - prev_candle['Open']) * 1.5:
                    score += 20
            elif last_candle['Close'] < last_candle['Open'] and prev_candle['Close'] > prev_candle['Open']:
                if abs(last_candle['Close'] - last_candle['Open']) > abs(prev_candle['Close'] - prev_candle['Open']) * 1.5:
                    score -= 20
            
            # نمط المطرقة والشهاب
            body = abs(last_candle['Close'] - last_candle['Open'])
            total_range = last_candle['High'] - last_candle['Low']
            if total_range > 0 and body / total_range < 0.3:
                if last_candle['Close'] > last_candle['Open']:
                    score += 15
                else:
                    score -= 15

        # 3. نقاط مؤشر MACD (الوزن: 15%)
        if data['MACD'].iloc[-1] > data['MACD_Signal'].iloc[-1] and data['MACD'].iloc[-2] <= data['MACD_Signal'].iloc[-2]:
            score += 20
        elif data['MACD'].iloc[-1] < data['MACD_Signal'].iloc[-1] and data['MACD'].iloc[-2] >= data['MACD_Signal'].iloc[-2]:
            score -= 20
        
        # 4. نقاط مؤشر Awesome Oscillator (الوزن: 10%)
        if data['Awesome_Oscillator'].iloc[-1] > 0 and data['Awesome_Oscillator'].iloc[-2] <= 0:
            score += 15
        elif data['Awesome_Oscillator'].iloc[-1] < 0 and data['Awesome_Oscillator'].iloc[-2] >= 0:
            score -= 15

        # 5. نقاط مؤشر ROC (الوزن: 10%)
        if data['ROC'].iloc[-1] > 0:
            score += 10
        elif data['ROC'].iloc[-1] < 0:
            score -= 10
            
        # 6. نقاط مؤشر RSI (الوزن: 10%)
        if data['RSI'].iloc[-1] > 70:
            score -= 10
        elif data['RSI'].iloc[-1] < 30:
            score += 10
            
        # 7. نقاط مؤشر Stochastic (الوزن: 10%)
        if data['Stoch_K'].iloc[-1] > 80:
            score -= 10
        elif data['Stoch_K'].iloc[-1] < 20:
            score += 10
            
        # 8. نقاط Bollinger Bands (%B) (الوزن: 5%)
        if data['Bollinger_Bands_PctB'].iloc[-1] > 1.0:
            score -= 5
        elif data['Bollinger_Bands_PctB'].iloc[-1] < 0.0:
            score += 5
        
        # 9. نقاط مؤشر ADX (الوزن: 5%)
        if data['ADX'].iloc[-1] > 25:
            if data.iloc[-1]['Close'] > data.iloc[-1]['Open']:
                score += 5
            else:
                score -= 5
                
        # 10. نقاط مؤشر MFI (الوزن: 5%)
        if data['MFI'].iloc[-1] > 80:
            score -= 5
        elif data['MFI'].iloc[-1] < 20:
            score += 5

        # 11. تحديد الإشارة النهائية بناءً على الأغلبية
        final_decision = "⚠️ متعادل"
        total_strength = 50

        if score > 0:
            final_decision = "📈 صعود"
            total_strength = min(100, 50 + score)
        elif score < 0:
            final_decision = "📉 هبوط"
            total_strength = min(100, 50 + abs(score))
        
        last_candle_is_up = data.iloc[-1]['Close'] > data.iloc[-1]['Open']

        # دائماً يعطي إشارة وتكون نفس آخر شمعة
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
        st.error(f"حدث خطأ: {e}")
        return None

# --- تصميم الواجهة باستخدام Streamlit ---

st.title("WELCOME WITH KHOURYBOT 🤖")
st.markdown("---")

st.header("قم باختيار الزوج والفريم لتحليل السوق:")

col1, col2 = st.columns(2)

with col1:
    symbol_map = {
        'EUR/USD': 'EURUSD=X',
        'EUR/GBP': 'EURGBP=X',
        'EUR/JPY': 'EURJPY=X'
    }
    selected_pair_name = st.selectbox(
        'اختر زوج العملات:',
        options=list(symbol_map.keys())
    )
    selected_symbol = symbol_map[selected_pair_name]

with col2:
    timeframe_map = {
        '1 دقيقة': '1m',
        '5 دقائق': '5m'
    }
    selected_timeframe_name = st.selectbox(
        'اختر الفريم الزمني:',
        options=list(timeframe_map.keys())
    )
    selected_timeframe = timeframe_map[selected_timeframe_name]

if st.button('احصل على الإشارة الآن'):
    with st.spinner('جاري التحليل... يرجى الانتظار'):
        time.sleep(1) 
        results = analyse_data(selected_symbol, selected_timeframe)
        
        if results:
            st.markdown("---")
            st.header("نتائج التحليل والإشارة:")
            st.success("🎉 تم التحليل بنجاح! إليك الإشارة:")
            
            st.markdown(f"## **الإشارة**: {results['final_decision']}")
            st.markdown(f"## **قوتها**: {results['total_strength']}%")
            
            st.markdown("---")
            st.info("💡 ملاحظة: التحليل يعتمد على بيانات السوق اللحظية. يرجى استخدامه كأداة مساعدة في اتخاذ القرار.")
