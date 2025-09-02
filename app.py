import streamlit as st
import websocket
import json
import time

# إعدادات الصفحة
st.set_page_config(
    page_title="اختبار اتصال Deriv",
    page_icon="🤖"
)

st.title("اختبار اتصال Deriv WebSocket 🌐")
st.markdown("---")

if st.button('اختبر الاتصال الآن'):
    with st.spinner('جاري محاولة الاتصال...'):
        try:
            # حاول إنشاء اتصال WebSocket
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929", timeout=10)
            st.success("🎉 نجح الاتصال بـ Deriv WebSocket!")
            ws.close()
        except Exception as e:
            st.error(f"❌ فشل الاتصال. الخطأ هو: {e}")
