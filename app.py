import streamlit as st
import requests
import time
import os

# --- Configuration (URL of the bot service) ---
# Change this to your Render URL when deployed
BOT_SERVICE_URL = "http://localhost:8080" 

# --- Session State ---
if "user_email" not in st.session_state:
    st.session_state.user_email = ""
if "is_logged_in" not in st.session_state:
    st.session_state.is_logged_in = False

# --- UI Functions ---
def check_bot_status():
    try:
        response = requests.post(f"{BOT_SERVICE_URL}/get_stats", json={"email": st.session_state.user_email})
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False

def get_stats():
    try:
        response = requests.post(f"{BOT_SERVICE_URL}/get_stats", json={"email": st.session_state.user_email})
        if response.status_code == 200:
            return response.json()['stats']
        return None
    except requests.exceptions.RequestException:
        st.error("❌ Failed to connect to bot service.")
        return None

def show_login_page():
    st.title("KHOURYBOT - Login 🤖")
    email = st.text_input("Enter your registered email:")
    if st.button("Login"):
        if not email:
            st.error("Please enter your email.")
        else:
            try:
                if not os.path.exists("user_ids.txt"):
                    st.error("❌ Error: 'user_ids.txt' file not found.")
                    return
                with open("user_ids.txt", "r") as f:
                    valid_emails = [line.strip().lower() for line in f.readlines()]
                if email.lower() in valid_emails:
                    st.session_state.user_email = email.lower()
                    st.session_state.is_logged_in = True
                    st.rerun()
                else:
                    st.error("Sorry, you do not have access to this account.")
            except FileNotFoundError:
                st.error("❌ 'user_ids.txt' file not found.")

def show_bot_settings():
    st.title("KHOURYBOT - Bot Settings")
    st.write(f"Welcome, {st.session_state.user_email}!")
    st.write("Please enter your bot settings to start a new session.")
    user_token = st.text_input("Enter your Deriv API token:", type="password")
    base_amount = st.number_input("Base Amount ($)", min_value=0.5, step=0.5, value=0.5)
    tp_target = st.number_input("Take Profit Target ($)", min_value=1.0, step=1.0, value=10.0)
    max_consecutive_losses = st.number_input("Max Consecutive Losses", min_value=1, step=1, value=5)

    if st.button("Start Bot", type="primary"):
        if not user_token:
            st.error("Please enter a valid API token.")
        else:
            settings = {
                "user_token": user_token,
                "base_amount": base_amount,
                "tp_target": tp_target,
                "max_consecutive_losses": max_consecutive_losses
            }
            try:
                response = requests.post(f"{BOT_SERVICE_URL}/start_bot", json={"email": st.session_state.user_email, "settings": settings})
                if response.status_code == 200:
                    st.success("Bot settings sent! The bot will now start working.")
                    time.sleep(2)
                    st.rerun()
                else:
                    st.error(f"❌ Failed to start bot: {response.json().get('message')}")
            except requests.exceptions.RequestException:
                st.error("❌ Could not connect to the bot service.")

def show_bot_stats(stats):
    st.title("KHOURYBOT - Live Status")
    st.write(f"Welcome back, {st.session_state.user_email}!")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Stop Bot", type="secondary"):
            try:
                response = requests.post(f"{BOT_SERVICE_URL}/stop_bot", json={"email": st.session_state.user_email})
                if response.status_code == 200:
                    st.success("Bot has been stopped.")
                    time.sleep(2)
                    st.rerun()
                else:
                    st.error("❌ Failed to stop bot.")
            except requests.exceptions.RequestException:
                st.error("❌ Could not connect to the bot service.")
    with col2:
        if st.button("Refresh Now"):
            st.rerun()
    
    st.markdown("---")
    
    if stats:
        current_amount = stats.get('current_amount', 0)
        initial_balance = stats.get('initial_balance', 0)
        profit = current_amount - initial_balance
        
        st.metric(
            "Current Profit/Loss",
            f"{profit:.2f} USD",
            delta=round(profit, 2),
            delta_color="normal"
        )
        
        st.markdown(f"**Wins:** {stats.get('total_wins', 0)} | **Losses:** {stats.get('total_losses', 0)} | **Consecutive Losses:** {stats.get('consecutive_losses', 0)}")
    else:
        st.info("No active session or failed to retrieve stats.")
    
    # Auto-refresh logic every 1 second
    time.sleep(1)
    st.rerun()

# --- Main App Logic ---
if not st.session_state.is_logged_in:
    show_login_page()
else:
    stats = get_stats()
    if stats:
        show_bot_stats(stats)
    else:
        show_bot_settings()
