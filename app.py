import streamlit as st
import cv2
import numpy as np

# إعدادات الصفحة والأيقونة
st.set_page_config(
    page_title="KHOURYBOT - بوت تحليل الشارت",
    page_icon="https://i.imgur.com/KHOURYBOT_Logo.png"
)

# دالة لتحليل شمعة واحدة وتحديد نوعها
def get_candle_info(roi):
    h, w, _ = roi.shape
    green_mask = np.all(roi > [100, 150, 100], axis=2)
    red_mask = np.all(roi > [150, 100, 100], axis=2)
    
    green_pixels = np.sum(green_mask)
    red_pixels = np.sum(red_mask)

    candle_color = "green" if green_pixels > red_pixels else "red"
    body_pixels = max(green_pixels, red_pixels)
    total_pixels = np.sum(green_mask | red_mask)
    
    # نسبة جسم الشمعة إلى الذيل
    body_ratio = body_pixels / total_pixels if total_pixels > 0 else 0

    return {
        'color': candle_color,
        'body_ratio': body_ratio,
        'body_pixels': body_pixels
    }

# دالة تحليل الشارت
def analyse_chart(img):
    h, w, _ = img.shape
    
    # التحليل يركز على آخر 7 شموع فقط
    candles = 7
    startX = int(w * 0.7)
    candleWidth = max(1, int((w - startX) / candles))

    candle_data = []
    for c in range(candles):
        x = startX + c * candleWidth
        roi = img[:, x:x+candleWidth]
        candle_data.append(get_candle_info(roi))
    
    # --- منطق التحليل الأكثر قوة ---

    # 1. البحث عن أنماط الشموع (الأولوية القصوى)
    final_decision = "⚠️ متعادل - لا توجد إشارة"
    total_strength = 0
    
    # نمط الابتلاع الصعودي (Bullish Engulfing)
    if candles >= 2 and candle_data[-1]['color'] == 'green' and candle_data[-2]['color'] == 'red':
        if candle_data[-1]['body_pixels'] > candle_data[-2]['body_pixels'] * 1.5:
            final_decision = "📈 صعود"
            total_strength = 90
            
    # نمط الابتلاع الهبوطي (Bearish Engulfing)
    elif candles >= 2 and candle_data[-1]['color'] == 'red' and candle_data[-2]['color'] == 'green':
        if candle_data[-1]['body_pixels'] > candle_data[-2]['body_pixels'] * 1.5:
            final_decision = "📉 هبوط"
            total_strength = 90
    
    # نمط المطرقة (Hammer) أو الشهاب (Shooting Star)
    elif candle_data[-1]['body_ratio'] < 0.3 and candle_data[-1]['body_pixels'] > 100: # شمعة بجسم صغير وذيل طويل
        if candle_data[-1]['color'] == 'green': # المطرقة
            final_decision = "📈 صعود"
            total_strength = 80
        else: # الشهاب
            final_decision = "📉 هبوط"
            total_strength = 80
    
    # 2. منطق الزخم (في حال عدم وجود أنماط واضحة)
    if total_strength == 0:
        up_votes = sum(1 for c in candle_data if c['color'] == 'green')
        down_votes = sum(1 for c in candle_data if c['color'] == 'red')

        if up_votes > down_votes:
            final_decision = "📈 صعود"
            total_strength = int((up_votes / candles) * 100)
        elif down_votes > up_votes:
            final_decision = "📉 هبوط"
            total_strength = int((down_votes / candles) * 100)
        else:
            final_decision = "⚠️ متعادل - لا توجد إشارة"
            total_strength = 50

    # 3. شرط التأكيد النهائي
    # الإشارة يجب أن تتوافق مع لون الشمعة الأخيرة
    if (final_decision == "📈 صعود" and candle_data[-1]['color'] != 'green') or \
       (final_decision == "📉 هبوط" and candle_data[-1]['color'] != 'red'):
        final_decision = "⚠️ لا توجد إشارة واضحة - لا يوجد تأكيد"
        total_strength = 0

    return {
        'final_decision': final_decision,
        'total_strength': total_strength
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
            st.info("💡 ملاحظة: التحليل يعتمد على خوارزميات الذكاء الاصطناعي وقد لا يكون دقيقاً بنسبة 100%. الرجاء استخدامه كأداة مساعدة.")
else:
    st.info("👆 يرجى تحميل صورة شارت للبدء بالتحليل.")
