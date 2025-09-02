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

# دالة تحليل البيانات من API
def analyse_data(symbol, timeframe):
    try:
        data = yf.download(symbol, period='1mo', interval=timeframe)
        if data.empty or len(data) < 20:
            st.error("خطأ: لا توجد بيانات كافية لرمز الزوج المحدد.")
            return None

        data = data.tail(20)

        # إضافة المؤشرات الجديدة
        data['RSI'] = ta.momentum.RSIIndicator(data['Close']).rsi()
        data['MACD'] = ta.trend.MACD(data['Close']).macd()
        data['MACD_Signal'] = ta.trend.MACD(data['Close']).macd_signal()

        # --- نظام النقاط الأكثر تطوراً ---
        score = 0
        
        # 1. نقاط الاتجاه العام
        up_candles = sum(1 for i, row in data.iterrows() if row['Close'] > row['Open'])
        down_candles = sum(1 for i, row in data.iterrows() if row['Close'] < row['Open'])
        score += (up_candles - down_candles) * 2
        
        # 2. نقاط الزخم (للشموع الخمس الأخيرة)
        recent_data = data.tail(5)
        recent_up = sum(1 for i, row in recent_data.iterrows() if row['Close'] > row['Open'])
        recent_down = sum(1 for i, row in recent_data.iterrows() if row['Close'] < row['Open'])
        score += (recent_up - recent_down) * 3
        
        # 3. نقاط أنماط الشموع
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

        # 4. نقاط مؤشر RSI
        if data['RSI'].iloc[-1] > 70:
            score -= 10 # ذروة شراء، قد يحدث انعكاس هبوطي
        elif data['RSI'].iloc[-1] < 30:
            score += 10 # ذروة بيع، قد يحدث انعكاس صعودي

        # 5. نقاط مؤشر MACD
        if data['MACD'].iloc[-1] > data['MACD_Signal'].iloc[-1] and data['MACD'].iloc[-2] <= data['MACD_Signal'].iloc[-2]:
            score += 20 # تقاطع صعودي قوي
        elif data['MACD'].iloc[-1] < data['MACD_Signal'].iloc[-1] and data['MACD'].iloc[-2] >= data['MACD_Signal'].iloc[-2]:
            score -= 20 # تقاطع هبوطي قوي

        # 6. تحديد الإشارة النهائية
        final_decision = "⚠️ متعادل"
        total_strength = 50

        if score > 0:
            final_decision = "📈 صعود"
            total_strength = min(100, 50 + score)
        elif score < 0:
            final_decision = "📉 هبوط"
            total_strength = min(100, 50 + abs(score))
        
        last_candle_is_up = data.iloc[-1]['Close'] > data.iloc[-1]['Open']
        if (final_decision == "📈 صعود" and not last_candle_is_up) or \
           (final_decision == "📉 هبوط" and last_candle_is_up):
            final_decision = "⚠️ الإشارة غير مؤكدة"
            total_strength = 0

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
