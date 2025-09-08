import streamlit as st
import time
import os
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, event
from datetime import datetime
import json
import uuid

# --- Database Setup ---
DATABASE_URL = "postgresql://khourybotes_db_user:HeAQEQ68txKKjTVQkDva3yaMx3npqTuw@dpg-d2uvmvogjchc73ao6060-a/khourybotes_db"
engine = sa.create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base()

# --- Database Models ---
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    sessions = relationship("BotSession", back_populates="user", cascade="all, delete-orphan")

class BotSession(Base):
    __tablename__ = 'bot_sessions'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    session_id = Column(String, unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    api_token = Column(String, nullable=True)
    base_amount = Column(Float, default=0.5)
    tp_target = Column(Float, nullable=True)
    max_consecutive_losses = Column(Integer, default=5)
    current_amount = Column(Float, default=0.5)
    consecutive_losses = Column(Integer, default=0)
    total_wins = Column(Integer, default=0)
    total_losses = Column(Integer, default=0)
    is_running = Column(Boolean, default=False)
    is_trade_open = Column(Boolean, default=False)
    initial_balance = Column(Float, nullable=True)
    logs = Column(String, default="[]")
    user = relationship("User", back_populates="sessions")

# Create tables
Base.metadata.create_all(engine)

# --- File-Based Licensing System ---
ALLOWED_USERS_FILE = 'user_ids.txt'

# --- Session State Management ---
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'user_email' not in st.session_state:
    st.session_state.user_email = None
if 'session_id' not in st.session_state:
    st.session_state.session_id = None
if 'session_data' not in st.session_state:
    st.session_state.session_data = {}

# --- Helper Functions ---
def is_email_allowed(email):
    if os.path.exists(ALLOWED_USERS_FILE):
        with open(ALLOWED_USERS_FILE, 'r') as f:
            allowed_emails = {line.strip() for line in f}
            return email in allowed_emails
    return False

def get_user_session(email):
    session = Session()
    try:
        user = session.query(User).filter_by(email=email).first()
        if not user:
            return None, None
        
        bot_session = session.query(BotSession).filter_by(user_id=user.id).first()
        return user, bot_session
    finally:
        session.close()

def create_user_session(email):
    session = Session()
    try:
        user = session.query(User).filter_by(email=email).first()
        if not user:
            user = User(email=email)
            session.add(user)
            session.commit()
            
        bot_session = BotSession(user_id=user.id)
        session.add(bot_session)
        session.commit()
        return bot_session
    finally:
        session.close()

def save_bot_state(session_id, state):
    s = Session()
    try:
        bot_session = s.query(BotSession).filter_by(session_id=session_id).first()
        if bot_session:
            for key, value in state.items():
                if hasattr(bot_session, key):
                    setattr(bot_session, key, value)
            s.commit()
    finally:
        s.close()

def reset_bot_session(session_id):
    s = Session()
    try:
        bot_session = s.query(BotSession).filter_by(session_id=session_id).first()
        if bot_session:
            s.delete(bot_session)
            s.commit()
            st.session_state.session_data = {}
            st.session_state.session_id = None
    finally:
        s.close()

def load_bot_state(session_id):
    s = Session()
    try:
        bot_session = s.query(BotSession).filter_by(session_id=session_id).first()
        if bot_session:
            return {
                'api_token': bot_session.api_token,
                'base_amount': bot_session.base_amount,
                'tp_target': bot_session.tp_target,
                'max_consecutive_losses': bot_session.max_consecutive_losses,
                'current_amount': bot_session.current_amount,
                'consecutive_losses': bot_session.consecutive_losses,
                'total_wins': bot_session.total_wins,
                'total_losses': bot_session.total_losses,
                'is_running': bot_session.is_running,
                'is_trade_open': bot_session.is_trade_open,
                'initial_balance': bot_session.initial_balance,
                'logs': json.loads(bot_session.logs)
            }
        return {}
    finally:
        s.close()

# --- Login Logic ---
if not st.session_state.logged_in:
    st.header("Login")
    email = st.text_input("Enter your email address:")
    if st.button("Login", type="primary"):
        if email:
            if is_email_allowed(email): # Check if email is in the allowed list
                user, bot_session = get_user_session(email)
                if not user:
                    new_bot_session = create_user_session(email)
                    st.session_state.user_email = email
                    st.session_state.session_id = new_bot_session.session_id
                    st.session_state.session_data = load_bot_state(new_bot_session.session_id)
                    st.session_state.logged_in = True
                    st.success("Welcome! A new session has been created for you.")
                elif not bot_session:
                    new_bot_session = create_user_session(email)
                    st.session_state.user_email = email
                    st.session_state.session_id = new_bot_session.session_id
                    st.session_state.session_data = load_bot_state(new_bot_session.session_id)
                    st.session_state.logged_in = True
                    st.warning("You are an existing user but no active session was found. A new one has been started.")
                else:
                    st.session_state.user_email = email
                    st.session_state.session_id = bot_session.session_id
                    st.session_state.session_data = load_bot_state(bot_session.session_id)
                    st.session_state.logged_in = True
                    st.success("Login successful. Resuming your session.")
                st.rerun()
            else:
                st.error("This email address is not authorized. Please contact the administrator.")
        else:
            st.warning("Please enter your email address.")
else:
    st.title("KHOURYBOT - Automated Trading 🤖")
    st.write(f"Logged in as: **{st.session_state.user_email}**")

    # --- Bot Controls & Settings ---
    st.header("1. Bot Control")
    current_status = "Running" if st.session_state.session_data.get('is_running') else "Stopped"
    
    # Check if session needs a reset or if API token is missing
    is_session_active = st.session_state.session_data.get('api_token') is not None
    if not is_session_active:
        st.warning("Session has ended. Please enter new settings to start a new one.")
        api_token = st.text_input("Enter your Deriv API token:", type="password", key="api_token_input")
        base_amount = st.number_input("Base Amount ($)", min_value=0.5, step=0.5, value=0.5, key="base_amount_input")
        tp_target = st.number_input("Take Profit Target ($)", min_value=1.0, step=1.0, value=1.0, key="tp_target_input")
        max_losses = st.number_input("Max Consecutive Losses", min_value=1, step=1, value=5, key="max_losses_input")
    else:
        api_token = st.session_state.session_data.get('api_token')
        base_amount = st.session_state.session_data.get('base_amount')
        tp_target = st.session_state.session_data.get('tp_target')
        max_losses = st.session_state.session_data.get('max_consecutive_losses')
        
        st.write(f"**API Token:** {'********'}")
        st.write(f"**Base Amount:** {base_amount}$")
        st.write(f"**TP Target:** {tp_target}$")
        st.write(f"**Max Losses:** {max_losses}")

    col1, col2 = st.columns(2)
    with col1:
        start_button = st.button("Start Bot", type="primary", disabled=(current_status == 'Running' or not api_token))
    with col2:
        stop_button = st.button("Stop Bot", disabled=(current_status == 'Stopped'))

    if start_button:
        st.session_state.session_data = {
            'is_running': True,
            'api_token': api_token,
            'base_amount': base_amount,
            'tp_target': tp_target,
            'max_consecutive_losses': max_losses,
            'current_amount': base_amount,
            'consecutive_losses': 0,
            'total_wins': 0,
            'total_losses': 0,
            'logs': [f"[{datetime.now().strftime('%H:%M:%S')}] 🟢 Bot has been started."]
        }
        save_bot_state(st.session_state.session_id, st.session_state.session_data)
        st.success("Bot has been started.")
        time.sleep(1)
        st.rerun()
        
    if stop_button:
        st.session_state.session_data['is_running'] = False
        st.session_state.session_data['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] 🛑 Bot stopped by user.")
        save_bot_state(st.session_state.session_id, st.session_state.session_data)
        reset_bot_session(st.session_state.session_id)
        st.warning("Bot has been stopped. The session has been reset.")
        time.sleep(1)
        st.rerun()

    if current_status == 'Running':
        st.info("Bot Status: **Running** ✅")
    else:
        st.info("Bot Status: **Stopped** 🛑")
        
    st.markdown("---")
    st.header("2. Live Bot Logs")
    
    logs = st.session_state.session_data.get('logs', [])
    with st.container(height=600):
        st.text_area("Logs", "\n".join(logs), height=600, key="logs_textarea")
        components.html(
            """
            <script>
                var textarea = parent.document.querySelector('textarea[aria-label="Logs"]');
                if(textarea) {
                    textarea.scrollTop = textarea.scrollHeight;
                }
            </script>
            """,
            height=0,
            width=0
        )
        
    time.sleep(5)
    st.rerun()
