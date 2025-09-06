import streamlit as st
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
import os
import uuid
import streamlit.components.v1 as components

# --- Database Setup (WARNING: HARDCODED URL) ---
DATABASE_URL = "postgresql://khourybot_db_user:wlVAwKwLhfzzH9HFsRMNo3IOo4dX6DYm@dpg-d2smi46r433s73frbbcg-a/khourybot_db"
engine = sa.create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base()

class BotState(Base):
    __tablename__ = 'bot_state'
    id = sa.Column(sa.Integer, primary_key=True)
    device_id = sa.Column(sa.String, unique=True, nullable=False)
    is_running = sa.Column(sa.Boolean, default=False)
    user_token = sa.Column(sa.String, nullable=True)
    current_amount = sa.Column(sa.Float, default=0.5)
    base_amount = sa.Column(sa.Float, default=0.5)
    consecutive_losses = sa.Column(sa.Integer, default=0)
    is_trade_open = sa.Column(sa.Boolean, default=False)
    trade_start_time = sa.Column(sa.DateTime, nullable=True)
    contract_id = sa.Column(sa.String, nullable=True)
    last_action_time = sa.Column(sa.DateTime, nullable=True)
    total_wins = sa.Column(sa.Integer, default=0)
    total_losses = sa.Column(sa.Integer, default=0)
    initial_balance = sa.Column(sa.Float, nullable=True)
    tp_target = sa.Column(sa.Float, nullable=True)
    max_consecutive_losses = sa.Column(sa.Integer, default=5)

class BotLog(Base):
    __tablename__ = 'bot_logs'
    id = sa.Column(sa.Integer, primary_key=True)
    device_id = sa.Column(sa.String, nullable=False)
    timestamp = sa.Column(sa.DateTime, default=datetime.utcnow)
    message = sa.Column(sa.String, nullable=False)

class Device(Base):
    __tablename__ = 'devices'
    id = sa.Column(sa.Integer, primary_key=True)
    device_id = sa.Column(sa.String, unique=True, nullable=False)

Base.metadata.create_all(engine)

# --- License Check and Device ID Generation ---
ALLOWED_USERS_FILE = 'user_ids.txt'
def get_or_create_device_id():
    if 'device_id' not in st.session_state:
        st.session_state.device_id = str(uuid.uuid4())
    device_id = st.session_state.device_id
    session = Session()
    try:
        device = session.query(Device).filter_by(device_id=device_id).first()
        if device:
            return device.device_id, "retrieved"
        else:
            new_device = Device(device_id=device_id)
            session.add(new_device)
            session.commit()
            return device_id, "created"
    except Exception as e:
        session.rollback()
        return None, f"error: {e}"
    finally:
        session.close()

def is_user_allowed(user_id):
    try:
        with open(ALLOWED_USERS_FILE, 'r') as f:
            allowed_ids = {line.strip() for line in f}
            if user_id in allowed_ids: return True
    except FileNotFoundError: st.error(f"Error: '{ALLOWED_USERS_FILE}' not found. Please create this file with a list of allowed user IDs."); return False
    except Exception as e: st.error(f"Error reading '{ALLOWED_USERS_FILE}': {e}"); return False
    return False

# --- UI Functions ---
def get_bot_state(device_id):
    session = Session()
    try:
        return session.query(BotState).filter_by(device_id=device_id).first()
    finally: session.close()

def update_bot_state_from_ui(device_id, **kwargs):
    session = Session()
    try:
        state = session.query(BotState).filter_by(device_id=device_id).first()
        if state:
            for key, value in kwargs.items(): setattr(state, key, value)
            session.commit()
        else:
            new_state = BotState(device_id=device_id, **kwargs)
            session.add(new_state)
            session.commit()
    finally: session.close()

def get_logs(device_id):
    session = Session()
    try:
        logs = session.query(BotLog).filter_by(device_id=device_id).order_by(BotLog.timestamp.desc()).limit(100).all()
        return [f"[{log.timestamp.strftime('%H:%M:%S')}] {log.message}" for log in reversed(logs)]
    finally: session.close()

# --- Streamlit App ---
st.title("KHOURYBOT - Automated Trading 🤖")

if 'is_authenticated' not in st.session_state: st.session_state.is_authenticated = False
if 'user_id' not in st.session_state: st.session_state.user_id = None
if 'page' not in st.session_state: st.session_state.page = 'inputs'
if 'user_id_checked' not in st.session_state:
    st.session_state.user_id, status = get_or_create_device_id()
    if st.session_state.user_id is None: st.error("Could not get device ID. Please check database connection."); st.session_state.user_id_checked = True
    else: st.session_state.user_id_checked = True

if not st.session_state.is_authenticated:
    st.header("Log in to Your Account")
    if st.session_state.user_id and is_user_allowed(st.session_state.user_id):
        st.session_state.is_authenticated = True; st.success("Your device has been activated! Redirecting to settings..."); st.balloons(); st.rerun()
    else:
        st.warning("Your device has not been activated yet. To activate the bot, please send this ID to the bot administrator:"); st.code(st.session_state.user_id); st.info("After activation, simply refresh this page to continue.")

else:
    bot_state = get_bot_state(st.session_state.user_id)
    if not bot_state: update_bot_state_from_ui(st.session_state.user_id)
    bot_state = get_bot_state(st.session_state.user_id)
    
    status_placeholder = st.empty(); timer_placeholder = st.empty()
    if bot_state and bot_state.is_running:
        if not bot_state.is_trade_open:
            status_placeholder.info("Analyzing...")
            now = datetime.now()
            last_action_time = bot_state.last_action_time if bot_state.last_action_time else now
            seconds_since_last_action = (now - last_action_time).total_seconds()
            seconds_left = max(0, 60 - seconds_since_last_action)
            timer_placeholder.metric("Next action in", f"{int(seconds_left)}s")
        else:
            status_placeholder.info("Waiting for trade result..."); timer_placeholder.empty()
    else: status_placeholder.empty(); timer_placeholder.empty()

    if st.session_state.page == 'inputs':
        st.header("1. Bot Settings")
        user_token = st.text_input("Enter your Deriv API token:", type="password", key="api_token_input", value=bot_state.user_token if bot_state and bot_state.user_token else "")
        base_amount = st.number_input("Base Amount ($)", min_value=0.5, step=0.5, value=bot_state.base_amount if bot_state else 0.5)
        tp_target = st.number_input("Take Profit Target ($)", min_value=1.0, step=1.0, value=bot_state.tp_target if bot_state and bot_state.tp_target else 1.0)
        max_consecutive_losses = st.number_input("Max Consecutive Losses", min_value=1, step=1, value=bot_state.max_consecutive_losses if bot_state else 5)
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Start Bot", type="primary"):
                if not user_token: st.error("Please enter a valid API token before starting the bot.")
                else: update_bot_state_from_ui(st.session_state.user_id, is_running=True, user_token=user_token, base_amount=base_amount, current_amount=base_amount, consecutive_losses=0, total_wins=0, total_losses=0, tp_target=tp_target, max_consecutive_losses=max_consecutive_losses); st.success("Bot started! You can close this tab."); st.rerun()
        with col2:
            if st.button("Stop Bot"): update_bot_state_from_ui(st.session_state.user_id, is_running=False); st.warning("Bot will stop soon. You can close this tab."); st.rerun()
    
    elif st.session_state.page == 'logs':
        st.header("2. Live Bot Logs")
        if bot_state: st.markdown(f"*Wins: {bot_state.total_wins}* | *Losses: {bot_state.total_losses}*")
        log_records = get_logs(st.session_state.user_id)
        with st.container(height=600):
            st.text_area("Logs", "\n".join(log_records), height=600, key="logs_textarea")
            components.html("""<script>var textarea = parent.document.querySelector('textarea[aria-label="Logs"]'); if(textarea) {textarea.scrollTop = textarea.scrollHeight;}</script>""", height=0, width=0)
    
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Settings"): st.session_state.page = 'inputs'; st.rerun()
    with col2:
        if st.button("Logs"): st.session_state.page = 'logs'; st.rerun()
    
    if bot_state and bot_state.is_running: import time; time.sleep(1); st.rerun()
