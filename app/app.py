from flask import Flask, render_template, request, redirect, session, flash, url_for, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone as pytz_timezone
import sqlite3
import smtplib
from email.mime.text import MIMEText
import os
import csv
import io
import json
import secrets
import requests
import re
from functools import wraps
from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf


app = Flask(__name__)
# Secret key comes from the environment (see .env); fallback only for quick local testing
app.secret_key = os.getenv("SECRET_KEY", "dev-only-insecure-secret")

# Enable CSRF protection
csrf = CSRFProtect(app)

# Custom CSRF error handler
@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    return render_template('csrf_error.html', reason=e.description), 400

# All database access happens from the directory containing this file
# (overridable with DB_PATH), so it stays consistent inside the container
DB_PATH = os.getenv("DB_PATH") or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'licenses.db')

# Configuration file for email/Teams settings (admin-editable via dashboard)
# Local dev: [repo_root]/config/settings.json
# Inside Docker: /config/settings.json (mounted from host ./config)
def _config_file_path():
    candidate = os.environ.get('CONFIG_FILE')
    if candidate:
        return os.path.normpath(candidate)
    return os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        '..',
        'config',
        'settings.json'
    ))

def load_config():
    """
    Load email/Teams settings from config/settings.json (or environment).
    Returns a dict with smtp_host, smtp_port, smtp_tls, email_user,
    email_pass, teams_webhook.
    """
    defaults = {
        'smtp_host': 'smtp.gmail.com',
        'smtp_port': 587,
        'smtp_tls': True,
        'email_user': '',
        'email_pass': '',
        'teams_webhook': ''
    }
    path = os.environ.get('CONFIG_FILE') or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        '..',
        'config',
        'settings.json'
    )
    path = os.path.normpath(path)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key, val in defaults.items():
                if key not in data:
                    data[key] = val
            return data
    except Exception:
        pass
    return dict(defaults)


# Initialize database
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS licenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        expiry_date TEXT,
        email TEXT,
        owner_name TEXT,
        owner_name_2 TEXT,
        email_2 TEXT,
        last_updated_by TEXT,
        last_updated_on TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT
    )''')
    # One-time migration: older databases only have the single owner columns
    for col in ('owner_name_2', 'email_2'):
        try:
            c.execute(f"ALTER TABLE licenses ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
    # One-time migration: license/seat count (defaults to 1 for existing rows)
    try:
        c.execute("ALTER TABLE licenses ADD COLUMN license_count INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass  # column already exists
    c.execute("UPDATE licenses SET license_count = 1 WHERE license_count IS NULL")
    # One-time migration: why the license was purchased (shown to all members)
    try:
        c.execute("ALTER TABLE licenses ADD COLUMN purpose TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    # One-time migration: password-reset tokens
    for col in ('reset_token', 'reset_expires'):
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
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
    return re.match(r"[^@]+@[^@]+\.[^@]+", email) is not None

# Email reminders
def send_email(to, subject, body):
    cfg = load_config()
    sender = cfg.get('email_user', '')
    password = cfg.get('email_pass', '')
    host = cfg.get('smtp_host', 'smtp.gmail.com')
    port = int(cfg.get('smtp_port', 587))
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = to
    try:
        with smtplib.SMTP(host, port) as server:
            if cfg.get('smtp_tls', True):
                server.starttls()
            server.login(sender, password)
            server.send_message(msg)
        print(f"Email successfully sent to {to}")
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

def send_teams_message(name, expiry, days_left, owner):
    cfg = load_config()
    webhook_url = cfg.get('teams_webhook', '')
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
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name, expiry_date, email, email_2, owner_name, owner_name_2 FROM licenses")
    for name, expiry_str, email, email_2, owner, owner_2 in c.fetchall():
        try:
            expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            days_left = (expiry - today).days
            print(f"Evaluating: {name} — {expiry} — {days_left} days left")
            # Reminder tiers: at 40 and 30 days out, then every day from
            # 14 days out through expiry day (0). Scheduler runs once a
            # day, so each tier fires exactly once per day.
            if days_left == 40 or days_left == 30 or (0 <= days_left <= 14):
                # Email every owner who has an address on file
                recipients = [e for e in (email, email_2) if e]
                for recipient in recipients:
                    send_email(
                        recipient,
                        f"License '{name}' expires in {days_left} days",
                        f"Your license '{name}' expires on {expiry}. Please renew."
                    )

                # Teams alert
                send_teams_message(
                    name=name,
                    expiry=expiry,
                    days_left=days_left,
                    owner=owner or "Unknown",
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
        confirm_password = request.form.get('confirm_password', '').strip()

        if not is_valid_username(username):
            return render_template('auth.html', error="Invalid username — please use your email address", source=action, csrf_token=generate_csrf())
        if not is_valid_password(password):
            return render_template('auth.html', error="Invalid password — must be at least 6 characters", source=action, csrf_token=generate_csrf())
        if action == 'signup' and password != confirm_password:
            return render_template('auth.html', error="Passwords do not match", source="signup", csrf_token=generate_csrf())

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        if action == 'signup':
            c.execute("SELECT COUNT(*) FROM users")
            count = c.fetchone()[0]
            role = 'admin' if count == 0 else 'general'
            try:
                c.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", (username, generate_password_hash(password), role))
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
                # check_password_hash handles hashed passwords; the fallback
                # supports accounts created before hashing was introduced
                if check_password_hash(db_password, password) or password == db_password:
                    session['user'] = db_username  # use stored username
                    session['role'] = db_role
                    return redirect('/dashboard')
                else:
                    return render_template('auth.html', error="Incorrect password", source="login", csrf_token=generate_csrf())
            else:
                return render_template('auth.html', error="Username not found", source="login", csrf_token=generate_csrf())

    return render_template('auth.html', csrf_token=generate_csrf())

@app.route('/forgot', methods=['GET', 'POST'])
def forgot_password():
    """Step 1: user asks for a reset link. Step 2 handled by reset_password()."""
    if request.method == 'GET':
        return render_template('forgot.html', csrf_token=generate_csrf())

    username = request.form.get('username', '').strip()
    if not is_valid_username(username):
        return render_template('forgot.html',
                               error="Please enter the email address you signed up with",
                               csrf_token=generate_csrf())

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE username = ?", (username,))
    found = c.fetchone()
    if found:
        token = secrets.token_urlsafe(32)
        expires = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("UPDATE users SET reset_token = ?, reset_expires = ? WHERE username = ?",
                  (token, expires, username))
        conn.commit()
    conn.close()

    # Never reveal whether the account exists (stops username enumeration)
    message = ("If an account exists for that address, a reset link has been "
               "emailed to it. The link expires in 1 hour.")

    reset_link = None
    if found:
        reset_url = request.host_url.rstrip('/') + url_for('reset_password', token=token)
        emailed = send_email(username,
                             "Reset your License Tracker password",
                             f"You asked to reset your License Tracker password.\n\n"
                             f"Click this link (valid for 1 hour):\n{reset_url}\n\n"
                             f"If you didn't ask for this, just ignore this email.")
        if not emailed:
            # Email not configured/failed — show the link on-screen so
            # testing and unblocking remain possible
            reset_link = reset_url

    return render_template('forgot.html', message=message,
                           reset_link=reset_link, csrf_token=generate_csrf())

@app.route('/reset/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Step 2: user sets a new password via the emailed token link."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, reset_expires FROM users WHERE reset_token = ?", (token,))
    row = c.fetchone()

    valid = False
    if row:
        try:
            valid = datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S") > datetime.now()
        except (ValueError, TypeError):
            valid = False

    if not valid:
        conn.close()
        return render_template('reset.html',
                               error="This reset link is invalid or has expired. "
                                     "Please request a new one.",
                               csrf_token=generate_csrf())

    if request.method == 'GET':
        conn.close()
        return render_template('reset.html', csrf_token=generate_csrf())

    password = request.form.get('password', '').strip()
    confirm_password = request.form.get('confirm_password', '').strip()
    if not is_valid_password(password):
        conn.close()
        return render_template('reset.html',
                               error="Password must be at least 6 characters",
                               csrf_token=generate_csrf())
    if password != confirm_password:
        conn.close()
        return render_template('reset.html',
                               error="Passwords do not match",
                               csrf_token=generate_csrf())

    c.execute("UPDATE users SET password = ?, reset_token = NULL, reset_expires = NULL "
              "WHERE username = ?",
              (generate_password_hash(password), row[0]))
    conn.commit()
    conn.close()
    flash("Password updated — please log in.", "success")
    return redirect('/auth')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/auth')

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect('/auth')

    query = request.args.get('query', '').strip().lower()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Pagination: 10 licenses per page, soonest expiry first
    per_page = 10
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    offset = (page - 1) * per_page

    where_clause = ""
    params = []
    if query:
        where_clause = ("WHERE LOWER(name) LIKE ? OR LOWER(owner_name) LIKE ? "
                        "OR LOWER(owner_name_2) LIKE ? OR LOWER(purpose) LIKE ?")
        params = [f'%{query}%', f'%{query}%', f'%{query}%', f'%{query}%']

    # Total count (for summary tiles + page links)
    c.execute(f"SELECT COUNT(*) FROM licenses {where_clause}", params)
    total_licenses = c.fetchone()[0]
    total_pages = max(1, (total_licenses + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    c.execute(
        f"SELECT * FROM licenses {where_clause} ORDER BY expiry_date ASC LIMIT ? OFFSET ?",
        params + [per_page, offset])
    licenses = c.fetchall()

    today = datetime.today().date()
    expiring_soon = sum(
        1 for lic in (c.execute(f"SELECT expiry_date FROM licenses {where_clause}", params).fetchall())
        if lic[0] and (datetime.strptime(lic[0], "%Y-%m-%d").date() - today).days <= 30
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
                           total_licenses=total_licenses,
                           page=page,
                           total_pages=total_pages,
                           admin_count=admin_count,
                           csrf_token=generate_csrf())  # ✅ Inject CSRF token

@app.route('/export')
def export_csv():
    """Download the current (search-filtered) license list as CSV."""
    if 'user' not in session:
        return redirect('/auth')

    query = request.args.get('query', '').strip().lower()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    params = []
    where_clause = ""
    if query:
        where_clause = ("WHERE LOWER(name) LIKE ? OR LOWER(owner_name) LIKE ? "
                        "OR LOWER(owner_name_2) LIKE ? OR LOWER(purpose) LIKE ?")
        params = [f'%{query}%', f'%{query}%', f'%{query}%', f'%{query}%']

    c.execute(f"SELECT * FROM licenses {where_clause} ORDER BY expiry_date ASC", params)
    rows = c.fetchall()
    conn.close()

    today = datetime.today().date()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['License name', 'Purpose', 'Expiry date', 'Days left',
                     'License count', 'Primary owner', 'Primary email',
                     'Secondary owner', 'Secondary email',
                     'Last updated by', 'Last updated on'])
    for lic in rows:
        days_left = ''
        if len(lic) > 2 and lic[2]:
            try:
                days_left = (datetime.strptime(lic[2], "%Y-%m-%d").date() - today).days
            except ValueError:
                days_left = ''
        writer.writerow([
            lic[1] if len(lic) > 1 else '',
            lic[10] if len(lic) > 10 and lic[10] else '',
            lic[2] if len(lic) > 2 else '',
            days_left,
            lic[9] if len(lic) > 9 and lic[9] is not None else 1,
            lic[4] if len(lic) > 4 else '',
            lic[3] if len(lic) > 3 else '',
            lic[5] if len(lic) > 5 else '',
            lic[6] if len(lic) > 6 else '',
            lic[7] if len(lic) > 7 else '',
            lic[8] if len(lic) > 8 else '',
        ])

    response = make_response('\ufeff' + output.getvalue())  # BOM so Excel opens UTF-8 correctly
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename=licenses.csv'
    return response

@app.route('/add', methods=['POST'])
def add():
    if 'user' not in session:
        return redirect('/auth')

    name = request.form['license_name'].strip()
    expiry = request.form['expiry_date'].strip()
    email = request.form['owner_email'].strip()
    owner_name = request.form['owner_name'].strip()
    owner_name_2 = request.form.get('owner_name_2', '').strip()
    email_2 = request.form.get('owner_email_2', '').strip()
    purpose = request.form.get('purpose', '').strip()[:300]
    try:
        license_count = int(request.form.get('license_count', '1'))
    except ValueError:
        license_count = 0
    if license_count < 1:
        return "License count must be at least 1", 400

    if not is_valid_email(email):
        return "Invalid email format", 400
    if email_2 and not is_valid_email(email_2):
        return "Invalid secondary email format", 400
    if not is_valid_date(expiry):
        return "Invalid date format", 400

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO licenses (name, expiry_date, email, owner_name, owner_name_2, email_2, license_count, purpose) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name, expiry, email, owner_name, owner_name_2, email_2, license_count, purpose))
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

    conn = sqlite3.connect(DB_PATH)
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

    conn = sqlite3.connect(DB_PATH)
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

    conn = sqlite3.connect(DB_PATH)
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

    conn = sqlite3.connect(DB_PATH)
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

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT username FROM users WHERE username = ?", (username,))
    if not c.fetchone():
        conn.close()
        return "User not found", 404

    c.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    return redirect('/dashboard')


# Admin Settings Page

@app.route('/admin/settings', methods=['GET', 'POST'])
def admin_settings():
    if 'user' not in session:
        return redirect('/auth')
    if session.get('role') != 'admin':
        return redirect('/dashboard')
    
    cfg = load_config()
    return render_template('admin_settings.html', 
                         cfg=cfg, 
                         email_user=cfg.get('email_user', ''),
                         email_pass=cfg.get('email_pass', ''),
                         smtp_host=cfg.get('smtp_host', 'smtp.gmail.com'),
                         smtp_port=cfg.get('smtp_port', 587),
                         teams_webhook=cfg.get('teams_webhook', ''))

# Initialize DB and run the app
init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
