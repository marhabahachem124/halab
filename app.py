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

def analyse_chart(img):
    h, w, _ = img.shape
    candles = 30
    startX = int(w * 0.7)
    candleWidth = max(1, int((w - startX) / candles))

    green_counts = []
    red_counts = []

    for c in range(candles):
        x = startX + c * candleWidth
        roi = img[:, x:x+candleWidth]
        
        green_mask = np.all(roi > [100, 150, 100], axis=2)
        red_mask = np.all(roi > [150, 100, 100], axis=2)
        
        green_counts.append(np.sum(green_mask))
        red_counts.append(np.sum(red_mask))

    # 1. الاتجاه العام
    total_green = sum(green_counts)
    total_red = sum(red_counts)
    trend = "📈 صاعد" if total_green > total_red else "📉 هابط"
    trend_strength = min(100, abs(total_green - total_red) / (total_green + total_red) * 100)

    # 2. RSI
    gains = [g - r if g > r else 0 for g, r in zip(green_counts, red_counts)]
    losses = [r - g if r > g else 0 for g, r in zip(green_counts, red_counts)]
    avg_gain = sum(gains) / candles
    avg_loss = sum(losses) / candles
    RS = avg_gain / avg_loss if avg_loss != 0 else 100
    RSI = 100 - (100 / (1 + RS))
    RSI_strength = 100 if RSI > 70 or RSI < 30 else abs(RSI - 50) * 2

    # 3. MACD
    values = [1 if g > r else -1 for g, r in zip(green_counts, red_counts)]
    ema_fast = EMA(values, 12)
    ema_slow = EMA(values, 26)
    macd = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal = EMA(macd, 9)
    macd_latest = macd[-1]
    signal_latest = signal[-1]
    macd_signal = "📈 Call" if macd_latest > signal_latest else "📉 Put"
    macd_strength = min(100, abs(macd_latest - signal_latest) * 100)

    # 4. Bollinger Bands
    mean = np.mean(green_counts)
    std_dev = np.std(green_counts)
    last_value = green_counts[-1]
    bb_signal = "📉 Put" if last_value > mean + std_dev else "📈 Call" if last_value < mean - std_dev else "⚪ Neutral"
    bb_strength = min(100, abs(last_value - mean) / std_dev * 50) if std_dev != 0 else 0

    # 5. آخر شمعة
    last_green = max(green_counts[-3:])
    last_red = max(red_counts[-3:])
    last_candle_strength = min(100, abs(last_green - last_red) / (last_green + last_red) * 100) if (last_green + last_red) != 0 else 0

    # 6. القوة النهائية
    total_strength = int(
        trend_strength * 0.2 +
        RSI_strength * 0.25 +
        macd_strength * 0.25 +
        last_candle_strength * 0.15 +
        bb_strength * 0.15
    )
    
    # 7. تحليل الذكاء الاصطناعي الخفيف
    ai_analysis = "نظرة الذكاء الاصطناعي: "
    if total_strength > 90:
        ai_analysis += "إشارة قوية جداً، ينصح باتخاذ الإشارة."
    elif total_strength > 75:
        ai_analysis += "إشارة قوية، يمكنك اتخاذ الإشارة مع الحذر."
    else:
        ai_analysis += "إشارة غير مؤكدة، يفضل عدم اتخاذ الإشارة."
    
    # 8. القرار النهائي
    final_decision = "⚠️ ضعيفة - لا تنفذ صفقة"
    if total_strength >= 70:
        if (last_green > last_red and macd_latest > signal_latest) or (bb_signal == "📈 Call" and RSI > 50):
            final_decision = "📈 صعود"
        else:
            final_decision = "📉 هبوط"

    return {
        'final_decision': final_decision,
        'total_strength': total_strength,
        'ai_analysis': ai_analysis
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
            st.markdown(f"## **قوتها**: {results['total_strength']}%")
            
            st.markdown("---")
            st.info(results['ai_analysis'])
            st.markdown("---")
            st.info("💡 ملاحظة: التحليل يعتمد على خوارزميات الذكاء الاصطناعي وقد لا يكون دقيقاً بنسبة 100%. الرجاء استخدامه كأداة مساعدة.")
else:
    st.info("👆 يرجى تحميل صورة شارت للبدء بالتحليل.")
