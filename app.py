import streamlit as st
import cv2
import numpy as np

# إعدادات الصفحة والأيقونة
st.set_page_config(
    page_title="KHOURYBOT - بوت تحليل الشارت",
    page_icon="https://i.imgur.com/KHOURYBOT_Logo.png"
)

# دوال التحليل
def EMA(values, period):
    k = 2 / (period + 1)
    ema_arr = [values[0]]
    for i in range(1, len(values)):
        ema_arr.append(values[i] * k + ema_arr[-1] * (1 - k))
    return ema_arr

def SMA(values, period):
    return [np.mean(values[i-period:i]) for i in range(period, len(values) + 1)]

def analyse_chart(img):
    h, w, _ = img.shape
    candles = 30
    startX = int(w * 0.7)
    candleWidth = max(1, int((w - startX) / candles))

    green_counts = []
    red_counts = []
    
    indicator_signals = []

    for c in range(candles):
        x = startX + c * candleWidth
        roi = img[:, x:x+candleWidth]
        
        green_mask = np.all(roi > [100, 150, 100], axis=2)
        red_mask = np.all(roi > [150, 100, 100], axis=2)
        
        green_counts.append(np.sum(green_mask))
        red_counts.append(np.sum(red_mask))

    # --- حساب المؤشرات الـ 10 ---

    # 1. الاتجاه العام (Trend)
    total_green = sum(green_counts)
    total_red = sum(red_counts)
    if total_green > total_red: indicator_signals.append("up")
    else: indicator_signals.append("down")

    # 2. مؤشر RSI
    gains = [g - r if g > r else 0 for g, r in zip(green_counts, red_counts)]
    losses = [r - g if r > g else 0 for g, r in zip(green_counts, red_counts)]
    avg_gain = sum(gains) / candles
    avg_loss = sum(losses) / candles
    RS = avg_gain / avg_loss if avg_loss != 0 else 100
    RSI = 100 - (100 / (1 + RS))
    if RSI > 70: indicator_signals.append("down")
    elif RSI < 30: indicator_signals.append("up")
    else: indicator_signals.append("neutral")

    # 3. مؤشر MACD
    values = [1 if g > r else -1 for g, r in zip(green_counts, red_counts)]
    ema_fast = EMA(values, 12)
    ema_slow = EMA(values, 26)
    macd = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal = EMA(macd, 9)
    if macd[-1] > signal[-1]: indicator_signals.append("up")
    else: indicator_signals.append("down")

    # 4. مؤشر Bollinger Bands
    mean = np.mean(green_counts)
    std_dev = np.std(green_counts)
    last_value = green_counts[-1]
    if last_value > mean + std_dev: indicator_signals.append("down")
    elif last_value < mean - std_dev: indicator_signals.append("up")
    else: indicator_signals.append("neutral")

    # 5. مؤشر Moving Average (MA)
    ma_short = SMA(green_counts, 5)
    ma_long = SMA(green_counts, 20)
    if len(ma_short) > 0 and len(ma_long) > 0 and ma_short[-1] > ma_long[-1]: indicator_signals.append("up")
    else: indicator_signals.append("down")
    
    # 6. مؤشر Stochastic Oscillator
    highs = [max(g, r) for g,r in zip(green_counts, red_counts)]
    lows = [min(g,r) for g,r in zip(green_counts, red_counts)]
    K = 100 * ((green_counts[-1] - min(lows)) / (max(highs) - min(lows))) if (max(highs) - min(lows)) > 0 else 50
    if K > 80: indicator_signals.append("down")
    elif K < 20: indicator_signals.append("up")
    else: indicator_signals.append("neutral")
    
    # 7. مؤشر آخر شمعة
    last_green = max(green_counts[-3:])
    last_red = max(red_counts[-3:])
    if last_green > last_red: indicator_signals.append("up")
    else: indicator_signals.append("down")
    
    # 8. مؤشر On-Balance Volume (OBV)
    obv = []
    obv_val = 0
    for i in range(len(green_counts)):
        if green_counts[i] > red_counts[i]: obv_val += (green_counts[i] - red_counts[i])
        elif red_counts[i] > green_counts[i]: obv_val -= (red_counts[i] - green_counts[i])
        obv.append(obv_val)
    if len(obv) > 1 and obv[-1] > obv[-2]: indicator_signals.append("up")
    else: indicator_signals.append("down")
    
    # 9. مؤشر Ichimoku Cloud (تبسيط)
    tenkan_sen = EMA(green_counts, 9)
    kijun_sen = EMA(green_counts, 26)
    if len(tenkan_sen) > 0 and len(kijun_sen) > 0 and tenkan_sen[-1] > kijun_sen[-1]: indicator_signals.append("up")
    else: indicator_signals.append("down")
    
    # 10. مؤشر Average Directional Index (ADX) (تبسيط)
    pos_di = [g - r if g > r else 0 for g,r in zip(green_counts, red_counts)]
    neg_di = [r - g if r > g else 0 for g,r in zip(green_counts, red_counts)]
    if sum(pos_di) > sum(neg_di): indicator_signals.append("up")
    else: indicator_signals.append("down")

    # --- نظام التصويت: 6 مؤشرات أو أكثر + تأكيد آخر شمعة ---

    up_votes = indicator_signals.count("up")
    down_votes = indicator_signals.count("down")

    final_decision = "⚠️ لا توجد إشارة واضحة - عدد المؤشرات غير كافٍ"
    
    # تحديد اتجاه آخر شمعة
    last_candle_is_up = green_counts[-1] > red_counts[-1]

    if up_votes >= 6 and last_candle_is_up:
        final_decision = "📈 صعود"
    elif down_votes >= 6 and not last_candle_is_up:
        final_decision = "📉 هبوط"
        
    # حساب القوة الإجمالية
    total_strength = int((up_votes + down_votes) / 10 * 100)

    return {
        'final_decision': final_decision,
        'total_strength': total_strength,
        'up_votes': up_votes,
        'down_votes': down_votes
    }

# --- تصميم الواجهة باستخدام Streamlit ---

st.title("WELCOME WITH KHOURYBOT")
st.markdown("---")

st.header("قم بتحميل صورة الشارت هنا لتحليلها:")

uploaded_file = st.file_uploader("اختر صورة شارت", type=["jpg", "jpeg", "png"])

if uploaded_file is not None:
    st.image(uploaded_file, caption='الصورة التي تم تحميلها', use_column_width=True)
    file_bytes = np.asarray(bytearray(uploaded_file.read()), dtype=np.uint8)
    img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)

    if st.button('حلل الشارت الآن'):
        with st.spinner('جاري التحليل... يرجى الانتظار'):
            results = analyse_chart(img)
            
            st.markdown("---")
            st.header("نتائج التحليل والإشارة:")
            st.success("🎉 تم التحليل بنجاح! إليك الإشارة:")
            
            st.markdown(f"## **الإشارة**: {results['final_decision']}")
            st.markdown(f"## **عدد المؤشرات المتوافقة**: {results['up_votes'] if results['final_decision'] == '📈 صعود' else results['down_votes']} / 10")
            
            st.markdown("---")
            st.info(f"💡 قوة القرار: {results['total_strength']}%")
            st.markdown("---")
            st.info("💡 ملاحظة: التحليل يعتمد على خوارزميات الذكاء الاصطناعي وقد لا يكون دقيقاً بنسبة 100%. الرجاء استخدامه كأداة مساعدة.")
else:
    st.info("👆 يرجى تحميل صورة شارت للبدء بالتحليل.")
