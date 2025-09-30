from flask import Flask, render_template, request, redirect, session
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone as pytz_timezone
import sqlite3
import smtplib
from email.mime.text import MIMEText
import os
import requests
import re
from functools import wraps
from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf


app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Replace with a secure key

# Enable CSRF protection
csrf = CSRFProtect(app)

# Custom CSRF error handler
@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    return render_template('csrf_error.html', reason=e.description), 400

# Initialize database
def init_db():
    conn = sqlite3.connect('licenses.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS licenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        expiry_date TEXT,
        email TEXT,
        owner_name TEXT,
        last_updated_by TEXT,
        last_updated_on TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT
    )''')
    conn.commit()
    conn.close()

def is_valid_username(username):
    # Accepts email-style usernames
    return re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", username) is not None

def is_valid_password(password):
    # Minimum 6 characters
    return password and len(password) >= 6

def is_valid_date(date_str):
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


# Input validation helpers
def is_valid_email(email):
    return re.match(r"[^@]+@[^@]+\.[^@]+", email)

    return username and len(username) >= 3 and re.match(r"^\w+$", username)

def is_valid_password(password):
    return password and len(password) >= 6

def is_valid_date(date_str):
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False

# Email reminders
def send_email(to, subject, body):
    sender = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_PASS")
    print(f"EMAIL_USER: {sender}")
    print(f"EMAIL_PASS: {'SET' if password else 'NOT SET'}")
    print(f"Attempting to send email to {to} with subject: {subject}")
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = to
    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(sender, password)
            server.send_message(msg)
        print(f"Email successfully sent to {to}")
    except Exception as e:
        print(f"Email error: {e}")

def send_teams_message(name, expiry, days_left, owner):
    webhook_url = os.getenv("TEAMS_WEBHOOK")
    message = (
        f"🔔 **License Alert**\n"
        f"📄 `{name}` expires in **{days_left} days**\n"
        f"📅 Expiry Date: `{expiry}`\n"
        f"👤 Owner: @`{owner}`\n"
        f"📬 Please renew ASAP."
    )
    payload = {"text": message}
    try:
        response = requests.post(webhook_url, json=payload)
        print(f"Teams response: {response.status_code}")
    except Exception as e:
        print(f"Teams error: {e}")

# Admin-only route 
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for('auth'))
        if session.get('role') != 'admin':
            flash("Admin access required.", "danger")
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def check_expirations():
    print(f"[{datetime.now()}] Running expiration check...")
    today = datetime.today().date()
    conn = sqlite3.connect('licenses.db')
    c = conn.cursor()
    c.execute("SELECT name, expiry_date, email, owner_name FROM licenses")
    for name, expiry_str, email, owner in c.fetchall():
        try:
            expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            days_left = (expiry - today).days
            print(f"Evaluating: {name} — {expiry} — {days_left} days left")
            if days_left in [45, 30, 15, 7, 1]:
                # Email (if SMTP works)
                send_email(
                    email,
                    f"License '{name}' expires in {days_left} days",
                    f"Your license '{name}' expires on {expiry}. Please renew."
                )

                # Teams alert
                send_teams_message(
                    name=name,
                    expiry=expiry,
                    days_left=days_left,
                    owner=owner or "Unknown"
                )
        except Exception as e:
            print(f"Reminder error: {e}")
    conn.close()

# Scheduler setup for 10:15 AM Bangladesh time
bd_tz = pytz_timezone('Asia/Dhaka')
scheduler = BackgroundScheduler(timezone=bd_tz)
trigger = CronTrigger(hour=10, minute=15, timezone=bd_tz)
scheduler.add_job(check_expirations, trigger)
scheduler.start()

@app.route('/')
def home():
    return redirect('/auth')


@app.route('/auth', methods=['GET', 'POST'])
def auth():
    if request.method == 'POST':
        action = request.form['action']
        username = request.form['username'].strip()
        password = request.form['password'].strip()

        if not is_valid_username(username):
            return render_template('auth.html', error="Invalid username", source=action, csrf_token=generate_csrf())
        if not is_valid_password(password):
            return render_template('auth.html', error="Invalid password", source=action, csrf_token=generate_csrf())

        conn = sqlite3.connect('licenses.db')
        c = conn.cursor()

        if action == 'signup':
            c.execute("SELECT COUNT(*) FROM users")
            count = c.fetchone()[0]
            role = 'admin' if count == 0 else 'general'
            try:
                c.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", (username, password, role))
                conn.commit()
                conn.close()
                return redirect('/auth')
            except sqlite3.IntegrityError:
                conn.close()
                return render_template('auth.html', error="Username already exists", source="signup", csrf_token=generate_csrf())

        elif action == 'login':
            # Normalize username for case-insensitive match
            c.execute("SELECT username, password, role FROM users WHERE LOWER(username) = LOWER(?)", (username,))
            user_record = c.fetchone()
            conn.close()

            if user_record:
                db_username, db_password, db_role = user_record
                if password == db_password:
                    session['user'] = db_username  # use stored username
                    session['role'] = db_role
                    return redirect('/dashboard')
                else:
                    return render_template('auth.html', error="Incorrect password", source="login", csrf_token=generate_csrf())
            else:
                return render_template('auth.html', error="Username not found", source="login", csrf_token=generate_csrf())

    return render_template('auth.html', csrf_token=generate_csrf())

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/auth')

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect('/auth')

    query = request.args.get('query', '').strip().lower()
    conn = sqlite3.connect('licenses.db')
    c = conn.cursor()

    if query:
        c.execute("SELECT * FROM licenses WHERE LOWER(name) LIKE ? OR LOWER(owner_name) LIKE ?", (f'%{query}%', f'%{query}%'))
    else:
        c.execute("SELECT * FROM licenses")
    licenses = c.fetchall()

    today = datetime.today().date()
    expiring_soon = sum(
        1 for lic in licenses
        if lic[2] and (datetime.strptime(lic[2], "%Y-%m-%d").date() - today).days <= 30
    )

    c.execute("SELECT username, role FROM users WHERE username != ?", (session['user'],))
    users = c.fetchall()

    c.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
    admin_count = c.fetchone()[0]

    conn.close()
    return render_template('dashboard.html',
                           licenses=licenses,
                           users=users,
                           role=session['role'],
                           user=session['user'],
                           query=query,
                           expiring_soon=expiring_soon,
                           total_users=len(users) + 1,
                           admin_count=admin_count,
                           csrf_token=generate_csrf())  # ✅ Inject CSRF token

@app.route('/add', methods=['POST'])
def add():
    if 'user' not in session:
        return redirect('/auth')

    name = request.form['license_name'].strip()
    expiry = request.form['expiry_date'].strip()
    email = request.form['owner_email'].strip()
    owner_name = request.form['owner_name'].strip()

    if not is_valid_email(email):
        return "Invalid email format", 400
    if not is_valid_date(expiry):
        return "Invalid date format", 400

    conn = sqlite3.connect('licenses.db')
    c = conn.cursor()
    c.execute("INSERT INTO licenses (name, expiry_date, email, owner_name) VALUES (?, ?, ?, ?)", (name, expiry, email, owner_name))
    conn.commit()
    conn.close()
    return redirect('/dashboard')


@app.route('/update/<int:license_id>', methods=['POST'])
def update(license_id):
    if 'user' not in session:
        return redirect('/auth')

    new_expiry = request.form['new_expiry'].strip()
    if not is_valid_date(new_expiry):
        return "Invalid date format", 400

    conn = sqlite3.connect('licenses.db')
    c = conn.cursor()

    c.execute("SELECT id FROM licenses WHERE id = ?", (license_id,))
    if not c.fetchone():
        conn.close()
        return "License not found", 404

    user = session['user']
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    c.execute("""
        UPDATE licenses
        SET expiry_date = ?, last_updated_by = ?, last_updated_on = ?
        WHERE id = ?
    """, (new_expiry, user, now, license_id))

    conn.commit()
    conn.close()
    return redirect('/dashboard')

@app.route('/delete/<int:license_id>', methods=['POST'])
@admin_required
def delete(license_id):
    if 'user' not in session:
        return redirect('/auth')

    conn = sqlite3.connect('licenses.db')
    c = conn.cursor()

    c.execute("SELECT id FROM licenses WHERE id = ?", (license_id,))
    if not c.fetchone():
        conn.close()
        return "License not found", 404

    c.execute("DELETE FROM licenses WHERE id = ?", (license_id,))
    conn.commit()
    conn.close()
    return redirect('/dashboard')

@app.route('/promote/<username>', methods=['POST'])
@admin_required
def promote(username):
    if 'user' not in session:
        return redirect('/auth')

    username = username.strip()
    if not is_valid_username(username):
        return "Invalid username", 400

    conn = sqlite3.connect('licenses.db')
    c = conn.cursor()

    c.execute("SELECT username FROM users WHERE username = ?", (username,))
    if not c.fetchone():
        conn.close()
        return "User not found", 404

    c.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
    admin_count = c.fetchone()[0]
    if admin_count >= 2:
        conn.close()
        return "Maximum number of admins reached", 403

    c.execute("UPDATE users SET role = 'admin' WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    return redirect('/dashboard')


@app.route('/transfer_admin/<username>', methods=['POST'])
@admin_required
def transfer_admin(username):
    if 'user' not in session:
        return redirect('/auth')

    username = username.strip()
    if not is_valid_username(username):
        return "Invalid username", 400

    conn = sqlite3.connect('licenses.db')
    c = conn.cursor()

    c.execute("SELECT username FROM users WHERE username = ?", (username,))
    if not c.fetchone():
        conn.close()
        return "User not found", 404

    c.execute("UPDATE users SET role = 'admin' WHERE username = ?", (username,))
    c.execute("UPDATE users SET role = 'general' WHERE username = ?", (session['user'],))
    conn.commit()
    conn.close()

    session['role'] = 'general'
    return redirect('/dashboard')

@app.route('/delete_user/<username>', methods=['POST'])
@admin_required
def delete_user(username):
    if 'user' not in session:
        return redirect('/auth')

    username = username.strip()
    if username == session['user']:
        return "You can't delete yourself", 403
    if not is_valid_username(username):
        return "Invalid username", 400

    conn = sqlite3.connect('licenses.db')
    c = conn.cursor()

    c.execute("SELECT username FROM users WHERE username = ?", (username,))
    if not c.fetchone():
        conn.close()
        return "User not found", 404

    c.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    return redirect('/dashboard')


# Initialize DB and run the app
init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
