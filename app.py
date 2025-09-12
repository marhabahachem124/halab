import time
import websocket
import json
import pandas as pd
from datetime import datetime
import psycopg2
import os
import sys
from flask import Flask, render_template_string, request, redirect, url_for

app = Flask(__name__)
# لم نعد بحاجة إلى secret_key إلا إذا أردنا استخدام وظائف Flask الخاصة بالجلسات
# app.secret_key = 'your_super_secret_key' 

# --- Database Connection Details ---
DB_URI = "postgresql://bestan_user:gTJKgsCRwEu9ijNMD9d3IMxFcW5TAdE0@dpg-d329ao2dbo4c73a92kng-a.oregon-postgres.render.com/bestan" 

# --- HTML Templates as Strings ---
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Login to Khourybot</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #f0f2f5; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .container { background-color: #fff; padding: 30px 40px; border-radius: 10px; box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1); text-align: center; }
        h1 { color: #333; margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; color: #555; text-align: left; }
        input[type="email"] { width: 100%; padding: 10px; margin-bottom: 20px; border: 1px solid #ddd; border-radius: 5px; box-sizing: border-box; }
        input[type="submit"] { width: 100%; padding: 12px; background-color: #007bff; color: white; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; transition: background-color 0.3s ease; }
        input[type="submit"]:hover { background-color: #0056b3; }
        .error { color: red; margin-top: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📈 Welcome with KHOURYBOT Autotrading</h1>
        <form method="POST">
            <label for="email">Enter your registered email:</label><br>
            <input type="email" id="email" name="email" required><br>
            <input type="submit" value="Login">
        </form>
        {% if error_message %}
            <p class="error">{{ error_message }}</p>
        {% endif %}
    </div>
</body>
</html>
"""

SETTINGS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Bot Settings</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #f0f2f5; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .container { background-color: #fff; padding: 30px 40px; border-radius: 10px; box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1); width: 400px; text-align: center; }
        h1, h2 { color: #333; margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; color: #555; text-align: left; }
        input[type="text"], input[type="number"] { width: 100%; padding: 10px; margin-bottom: 20px; border: 1px solid #ddd; border-radius: 5px; box-sizing: border-box; }
        input[type="submit"] { width: 100%; padding: 12px; background-color: #28a745; color: white; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; transition: background-color 0.3s ease; }
        input[type="submit"]:hover { background-color: #218838; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📈 Welcome with KHOURYBOT Autotrading</h1>
        <h2>⚙️ Start a new session</h2>
        <form method="POST">
            <label for="user_token">Deriv API Token:</label>
            <input type="text" id="user_token" name="user_token" required><br>
            
            <label for="base_amount">Base Amount ($):</label>
            <input type="number" id="base_amount" name="base_amount" step="0.01" required><br>
            
            <label for="tp_target">Take Profit Target ($):</label>
            <input type="number" id="tp_target" name="tp_target" step="0.01" required><br>
            
            <label for="max_consecutive_losses">Max Consecutive Losses:</label>
            <input type="number" id="max_consecutive_losses" name="max_consecutive_losses" required><br>
            
            <input type="submit" value="Start Bot">
        </form>
    </div>
</body>
</html>
"""

STATS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Bot Stats</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #f0f2f5; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .container { background-color: #fff; padding: 30px 40px; border-radius: 10px; box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1); width: 400px; }
        h1, h2 { color: #333; text-align: center; }
        ul { list-style: none; padding: 0; text-align: left; }
        li { background-color: #f9f9f9; padding: 12px; margin-bottom: 8px; border-radius: 5px; border-left: 5px solid #007bff; }
        .profit { color: #28a745; font-weight: bold; }
        .loss { color: #dc3545; font-weight: bold; }
        a { color: #007bff; text-decoration: none; }
        a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📈 Welcome with KHOURYBOT Autotrading</h1>
        <h2>📊 Trading Stats:</h2>
        <ul>
            <li>Total Wins: {{ stats['total_wins'] }}</li>
            <li>Total Losses: {{ stats['total_losses'] }}</li>
            <li>Consecutive Losses: {{ stats['consecutive_losses'] }}</li>
            <li>Current Martingale Amount: ${{ "%.2f"|format(stats['current_amount']) }}</li>
            <li>Initial Balance: ${{ "%.2f"|format(stats['initial_balance']) }}</li>
        </ul>
        <p>Current P/L: <span class="{{ 'profit' if profit > 0 else 'loss' }}">${{ "%.2f"|format(profit) }}</span></p>
        <p><a href="{{ url_for('logout') }}">Logout and Stop Bot</a></p>
    </div>
</body>
</html>
"""

# --- Database Functions (Same as before) ---
def get_db_connection():
    try:
        conn = psycopg2.connect(DB_URI)
        return conn
    except Exception as e:
        print(f"❌ Failed to connect to the database: {e}")
        return None

def load_settings_from_db(email):
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_token, base_amount, tp_target, max_consecutive_losses FROM user_settings WHERE email = %s", (email,))
            result = cur.fetchone()
            cur.close()
            conn.close()
            if result:
                return {
                    "user_token": result[0],
                    "base_amount": result[1],
                    "tp_target": result[2],
                    "max_consecutive_losses": result[3]
                }
    return None

def load_stats_from_db(email):
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            cur.execute("SELECT total_wins, total_losses, current_amount, consecutive_losses, initial_balance FROM user_settings WHERE email = %s", (email,))
            result = cur.fetchone()
            cur.close()
            conn.close()
            if result:
                return {
                    "total_wins": result[0],
                    "total_losses": result[1],
                    "current_amount": result[2],
                    "consecutive_losses": result[3],
                    "initial_balance": result[4]
                }
    return None

def save_settings_and_start_session(email, settings):
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    INSERT INTO user_settings (email, user_token, base_amount, tp_target, max_consecutive_losses,
                                               total_wins, total_losses, current_amount, consecutive_losses, initial_balance)
                    VALUES (%s, %s, %s, %s, %s, 0, 0, %s, 0, 0)
                    ON CONFLICT (email) DO UPDATE SET
                    user_token = EXCLUDED.user_token,
                    base_amount = EXCLUDED.base_amount,
                    tp_target = EXCLUDED.tp_target,
                    max_consecutive_losses = EXCLUDED.max_consecutive_losses,
                    total_wins = 0,
                    total_losses = 0,
                    current_amount = EXCLUDED.base_amount,
                    consecutive_losses = 0,
                    initial_balance = 0
                """, (email, settings["user_token"], settings["base_amount"], settings["tp_target"], 
                      settings["max_consecutive_losses"], settings["base_amount"]))
                conn.commit()
            except Exception as e:
                print(f"❌ Error saving settings: {e}")
            finally:
                cur.close()
                conn.close()

def update_stats_in_db(email, total_wins, total_losses, current_amount, consecutive_losses, initial_balance=None):
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            try:
                query = """
                    UPDATE user_settings
                    SET total_wins = %s,
                        total_losses = %s,
                        current_amount = %s,
                        consecutive_losses = %s
                    WHERE email = %s
                """
                params = (total_wins, total_losses, current_amount, consecutive_losses, email)

                if initial_balance is not None:
                    query = query.replace("WHERE", ", initial_balance = %s WHERE")
                    params = (total_wins, total_losses, current_amount, consecutive_losses, initial_balance, email)
                
                cur.execute(query, params)
                conn.commit()
            except Exception as e:
                print(f"❌ Error updating stats in DB: {e}")
            finally:
                conn.close()

def clear_session_data(email):
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            try:
                cur.execute("DELETE FROM user_settings WHERE email = %s", (email,))
                conn.commit()
            except Exception as e:
                print(f"❌ Error clearing session data: {e}")
            finally:
                cur.close()
                conn.close()

# --- Helper Functions for Trading Logic (Placeholders) ---
# هذه الدوال تحتاج إلى إكمالها وربطها بعملية البوت الفعلية
# حالياً هي مجرد نماذج لتوضيح كيفية قراءة البيانات وعرضها
def get_balance_from_db(email):
    # في تطبيق حقيقي، ستجلب الرصيد الحالي للبوت من قاعدة البيانات
    # أو ستحتاج إلى آلية لتشغيل البوت في الخلفية وقراءة بياناته.
    # هنا سنفترض أن stats_from_db تحتوي على الرصيد الحالي.
    stats = load_stats_from_db(email)
    if stats:
        return stats.get('current_amount', 0)
    return 0

def get_initial_balance_from_db(email):
    stats = load_stats_from_db(email)
    if stats:
        return stats.get('initial_balance', 0)
    return 0

def get_current_balance_and_calculate_profit(email):
    # هذه دالة وهمية. في تطبيقك الحقيقي، البوت سيشغل في الخلفية
    # ويحدث بياناته في الـ DB. هنا، سنقرأ فقط آخر حالة محفوظة.
    stats = load_stats_from_db(email)
    if stats:
        initial_balance = stats.get('initial_balance', 0)
        current_amount = stats.get('current_amount', 0)
        profit = current_amount - initial_balance
        return current_amount, profit
    return 0, 0 # إذا لم يتم العثور على إحصائيات

# --- Flask Routes ---
@app.route('/')
def index():
    # إعادة التوجيه لصفحة تسجيل الدخول إذا لم يكن هناك بريد إلكتروني في الجلسة
    # (نحن لا نستخدم session الخاصة بـ Flask، بل سنحفظ البريد في ملف مؤقت أو DB بسيط إذا لزم الأمر)
    # الآن، سنعتمد على التحقق من البريد عند الوصول لـ /dashboard
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    error_message = None
    if request.method == 'POST':
        user_email = request.form['email'].lower()
        
        if not os.path.exists("user_ids.txt"):
            error_message = "❌ Error: 'user_ids.txt' file not found. Please contact support."
        else:
            with open("user_ids.txt", "r") as f:
                valid_emails = [line.strip().lower() for line in f.readlines()]
                if user_email not in valid_emails:
                    error_message = "❌ Sorry, you do not have access to this account."
                else:
                    # تم التحقق من البريد بنجاح، سنحفظه في ملف مؤقت أو قاعدة بيانات بسيطة لتتبع الجلسة
                    # هنا سنستخدم ملف بسيط لتجاوز الحاجة لـ Flask session
                    with open("current_user.txt", "w") as f:
                        f.write(user_email)
                    return redirect(url_for('dashboard'))
    
    return render_template_string(LOGIN_HTML, error_message=error_message)

@app.route('/dashboard')
def dashboard():
    # قراءة البريد الإلكتروني من الملف المؤقت
    user_email = None
    if os.path.exists("current_user.txt"):
        with open("current_user.txt", "r") as f:
            user_email = f.read().strip().lower()
    
    if not user_email:
        return redirect(url_for('login'))

    stats = load_stats_from_db(user_email)
    
    if not stats or stats["initial_balance"] == 0:
        # لا توجد جلسة مفتوحة، اطلب الإعدادات
        return redirect(url_for('settings'))
    
    # إذا كانت هناك جلسة، اعرض الإحصائيات
    current_balance, profit = get_current_balance_and_calculate_profit(user_email) # قراءة آخر حالة من DB
    return render_template_string(STATS_HTML, stats=stats, profit=profit)

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    user_email = None
    if os.path.exists("current_user.txt"):
        with open("current_user.txt", "r") as f:
            user_email = f.read().strip().lower()

    if not user_email:
        return redirect(url_for('login'))

    if request.method == 'POST':
        user_token = request.form['user_token']
        base_amount = float(request.form['base_amount'])
        tp_target = float(request.form['tp_target'])
        max_consecutive_losses = int(request.form['max_consecutive_losses'])
        
        settings = {
            "user_token": user_token,
            "base_amount": base_amount,
            "tp_target": tp_target,
            "max_consecutive_losses": max_consecutive_losses
        }
        save_settings_and_start_session(user_email, settings)
        
        # بعد حفظ الإعدادات، ابدأ البوت (في تطبيق حقيقي، هذا سيبدأ عملية في الخلفية)
        # هنا سنعيد التوجيه لصفحة الإحصائيات لعرض الحالة الأولية
        return redirect(url_for('dashboard'))

    return render_template_string(SETTINGS_HTML)

@app.route('/logout')
def logout():
    user_email = None
    if os.path.exists("current_user.txt"):
        with open("current_user.txt", "r") as f:
            user_email = f.read().strip().lower()
    
    if user_email:
        clear_session_data(user_email) # امسح بيانات الجلسة من قاعدة البيانات
        # امسح الملف المؤقت الذي يحفظ البريد
        if os.path.exists("current_user.txt"):
            os.remove("current_user.txt")
            
    # إعادة التوجيه لصفحة تسجيل الدخول
    return redirect(url_for('login'))

# --- Main Execution ---
if __name__ == "__main__":
    # تأكد من وجود ملف user_ids.txt
    if not os.path.exists("user_ids.txt"):
        print("❌ Error: 'user_ids.txt' file not found. Please create it and add authorized emails.")
        sys.exit(1)
        
    # تأكد من أن قاعدة البيانات جاهزة
    try:
        conn = get_db_connection()
        if conn:
            conn.close()
        else:
            print("❌ Database connection failed. Ensure DB_URI is correct and the database is running.")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Error during database check: {e}")
        sys.exit(1)

    # تشغيل التطبيق
    # Host='0.0.0.0' يجعل التطبيق متاحاً على شبكتك الداخلية
    # Port=5000 هو المنفذ الافتراضي لـ Flask
    # debug=True يسهل التطوير بإعادة تحميل التطبيق تلقائياً عند التغيير
    # لكن يجب إيقافه في بيئة الإنتاج (مثل Render)
    app.run(host='0.0.0.0', port=5000, debug=True)
