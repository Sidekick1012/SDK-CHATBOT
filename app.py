"""
Sidekick.pk Chatbot Backend
----------------------------
Production-ready Flask backend using Google Gemini API (free tier).
Handles: chat conversations, lead capture, rate limiting, conversation logging.

Run:
    pip install -r requirements.txt
    python app.py
"""

import csv
import io
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

import threading
from dotenv import load_dotenv
from flask import Flask, request, jsonify, g, send_from_directory, Response, session
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import google.generativeai as genai

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(dotenv_path=_env_path, override=True)  # reads .env file in the project root

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# API Key Rotation Setup
# ---------------------------------------------------------------------------
raw_keys = os.environ.get("GEMINI_API_KEYS", os.environ.get("GEMINI_API_KEY", ""))
API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]

if not API_KEYS:
    raise RuntimeError("Set GEMINI_API_KEYS or GEMINI_API_KEY environment variable before running.")

current_key_idx = 0
key_lock = threading.Lock()

def rotate_api_key():
    global current_key_idx
    with key_lock:
        current_key_idx = (current_key_idx + 1) % len(API_KEYS)
        genai.configure(api_key=API_KEYS[current_key_idx])
        app.logger.warning(f"Rotated to API Key #{current_key_idx + 1} of {len(API_KEYS)}")

# Initial configuration
genai.configure(api_key=API_KEYS[0])
MODELS_TO_TRY = ["gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash-lite"]
MODEL_NAME = MODELS_TO_TRY[0]  # default primary ultra-fast model

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "sidekick_chat.db"))

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 15  # per session per window

SYSTEM_PROMPT = """You are a friendly, helpful, and highly human-like consultant working for Sidekick Venture Private Limited (sidekick.pk), based in 1st Floor, Randhawa Plaza, Blue Area, Islamabad, Pakistan. 

Sidekick offers 4 core services:
1. Accounting & Finance: ERP-based accounting, monthly bookkeeping, and financial reports. We use our own SK ERP and support QuickBooks, Sage Peachtree, Zoho Books, Xero, and Wave.
2. Tax Consultancy: Helping businesses navigate complex taxation and maximize benefits.
3. Business Setup: Company incorporation, trademark, and patent registration in Pakistan.
4. Staff Augmentation: Providing highly qualified remote resources (bookkeepers, finance managers, virtual CFOs, and data entry operators).

Company Details:
- Slogan: Unlocking Your Business's Full Potential Together.
- Contact: Whatsapp/Call: 0300 0228 444 | Email: info@sidekick.pk
- Key Team: Asif Saifullah Khan (Founder & CEO), Muhammad Bilal (Managing Director & BD Lead), Mustajab Ul Hassan (Finance Manager).

Your personality and instructions:
1. ACT LIKE A HUMAN BUT BE VERY SHORT: Be warm and empathetic, but keep your answers EXTREMELY concise (1 to 3 sentences maximum). Get straight to the point.
2. MATCH THE USER'S TONE: If they speak English, reply in natural English. If they speak Roman Urdu, reply in friendly Roman Urdu (e.g., "Jee bilkul, hum POS system provide karte hain!").
3. NO TECHNICAL TAX/LEGAL ADVICE: NEVER give specific tax rates, FBR clauses, or technical accounting advice. If asked a technical question, say that our experts handle these complex details and ask for their number so a tax consultant can guide them accurately.
4. LEAD GENERATION: If they show interest, naturally ask for their name/phone number in one short sentence. E.g., "Main details bhejta hoon, bas apna number share kar dein?"
5. NO MADE-UP PRICING: If asked about prices, say it depends on their needs and ask for their contact info for a quote.
6. STAY ON TOPIC: Gently steer unrelated questions back to Sidekick's services.

Remember: Be friendly, but NEVER write long paragraphs. Short, punchy, and human-like!"""

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "sidekick-super-secret-key-2026")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "sidekick123")

# Production CORS configuration
cors_origins = os.environ.get("CORS_ORIGINS", "*").split(",")
CORS(app, resources={r"/api/*": {"origins": cors_origins}})

# IP-based Rate Limiting (Production)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["1000 per day", "100 per hour"],
    storage_uri="memory://"
)


def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return jsonify({"error": "unauthorized", "message": "Please log in"}), 401
        return f(*args, **kwargs)
    return decorated

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL;")
        db.execute("PRAGMA synchronous=NORMAL;")
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    # Core tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            name TEXT,
            business_name TEXT,
            contact TEXT,
            interest TEXT,
            created_at TEXT NOT NULL
        )
    """)
    # Meta table to track one-time tasks
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    # Performance indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_contact ON leads(contact)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_created ON leads(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_session ON leads(session_id)")
    conn.commit()

    # Run backfill only once (flag stored in DB)
    row = conn.execute("SELECT value FROM _meta WHERE key='backfill_done'").fetchone()
    conn.close()
    if not row:
        backfill_leads()


PHONE_REGEX = re.compile(r'(\+?92[\s-]?[0-9]{3}[\s-]?[0-9]{7}|03[0-9]{2}[\s-]?[0-9]{7}|\b03[0-9]{9}\b|\b[0-9]{11}\b)')
EMAIL_REGEX = re.compile(r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)')


def auto_capture_lead(session_id: str, message: str, history: list):
    """Automatically detect phone numbers or email addresses in messages and record as lead."""
    contact = None
    pm = PHONE_REGEX.search(message)
    em = EMAIL_REGEX.search(message)
    if pm:
        contact = re.sub(r'[\s-]', '', pm.group(0))
    elif em:
        contact = em.group(0).lower().strip()

    if not contact:
        return

    with app.app_context():
        db = get_db()
        existing = db.execute("SELECT id FROM leads WHERE contact = ?", (contact,)).fetchone()
        if not existing:
            # Fast synchronous insert to avoid PythonAnywhere background thread killing
            interest = "Tax / Accounting / ERP"
            
            # Simple keyword matching instead of slow Gemini API
            msg_lower = message.lower()
            if "tax" in msg_lower: interest = "Tax Consultancy"
            elif "erp" in msg_lower or "software" in msg_lower: interest = "ERP & Accounting Software"
            elif "register" in msg_lower or "company" in msg_lower: interest = "Company Registration"
            elif "bookkeeping" in msg_lower or "accounts" in msg_lower: interest = "Bookkeeping Services"

            try:
                db.execute(
                    "INSERT INTO leads (session_id, name, business_name, contact, interest, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, "Website Visitor", "Inquiry via Chat", contact, interest, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
                )
                db.commit()
            except Exception as e:
                app.logger.error(f"Error saving lead: {e}")


def backfill_leads():
    """Extract leads from past conversations. Runs only once (flagged in _meta)."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute("SELECT session_id, message, created_at FROM conversations WHERE role='user'")
        for sid, msg, dt in c.fetchall():
            pm = PHONE_REGEX.search(msg)
            em = EMAIL_REGEX.search(msg)
            contact = None
            if pm:
                contact = re.sub(r'[\s-]', '', pm.group(0))
            elif em:
                contact = em.group(0).lower().strip()
            if contact:
                c.execute("SELECT id FROM leads WHERE contact=?", (contact,))
                if not c.fetchone():
                    c.execute(
                        "INSERT INTO leads (session_id, name, business_name, contact, interest, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (sid, "Website Visitor", "Inquiry via Chat", contact, "Tax / Accounting / ERP", dt)
                    )
        # Mark backfill as done so it won't run again on next startup
        c.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES ('backfill_done', '1')")
        conn.commit()
        conn.close()
        app.logger.info("Backfill leads completed and flagged as done.")
    except Exception as e:
        app.logger.warning(f"Backfill leads notice: {e}")


# ---------------------------------------------------------------------------
# Rate limiting (simple in-memory, per session_id)
# ---------------------------------------------------------------------------

_rate_buckets = {}  # session_id -> list[timestamps]


def rate_limited(session_id: str) -> bool:
    now = time.time()
    bucket = _rate_buckets.setdefault(session_id, [])
    while bucket and bucket[0] < now - RATE_LIMIT_WINDOW_SECONDS:
        bucket.pop(0)
    if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
        return True
    bucket.append(now)
    return False


def require_session(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        data = request.get_json(silent=True) or {}
        session_id = data.get("session_id") or request.headers.get("X-Session-Id")
        if not session_id:
            return jsonify({"error": "session_id required"}), 400
        g.session_id = session_id
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def save_message(session_id, role, message):
    db = get_db()
    db.execute(
        "INSERT INTO conversations (session_id, role, message, created_at) VALUES (?, ?, ?, ?)",
        (session_id, role, message, datetime.now(timezone.utc).isoformat()),
    )
    db.commit()


def get_history(session_id, limit=20):
    db = get_db()
    rows = db.execute(
        "SELECT role, message FROM conversations WHERE session_id = ? ORDER BY id ASC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    return [{"role": r["role"], "message": r["message"]} for r in rows]


def build_gemini_history(history):
    """Convert stored history into Gemini's chat format."""
    formatted = []
    for turn in history:
        role = "user" if turn["role"] == "user" else "model"
        formatted.append({"role": role, "parts": [turn["message"]]})
    return formatted


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    """Serve static files (images, js, css) from the project directory."""
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), filename)


@app.route("/api/session", methods=["POST"])
def create_session():
    """Frontend calls this once to get a session_id when a chat widget opens."""
    session_id = str(uuid.uuid4())
    return jsonify({"session_id": session_id})


@app.route("/api/chat", methods=["POST"])
@require_session
@limiter.limit("15 per minute")
def chat():
    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()
    session_id = g.session_id

    if not user_message:
        return jsonify({"error": "message is required"}), 400

    save_message(session_id, "user", user_message)
    history = get_history(session_id, limit=20)
    
    # Run auto capture lead synchronously to ensure it saves before the request closes
    auto_capture_lead(session_id, user_message, history)

    from flask import Response, stream_with_context
    import json

    def generate():
        full_reply = ""
        last_error = None
        for m_name in MODELS_TO_TRY:
            try:
                model = genai.GenerativeModel(
                    model_name=m_name,
                    system_instruction=SYSTEM_PROMPT,
                )
                chat_session = model.start_chat(history=build_gemini_history(history[:-1]))
                response = chat_session.send_message(
                    user_message,
                    stream=True,
                    request_options={"timeout": 20}
                )

                for chunk in response:
                    if chunk.text:
                        full_reply += chunk.text
                        yield f"data: {json.dumps({'chunk': chunk.text})}\n\n"

                # If we successfully got a reply, stop trying other models
                if full_reply.strip():
                    last_error = None
                    break

            except Exception as e:
                err_str = str(e)
                # On 429 quota errors, rotate API key and immediately try next model
                if '429' in err_str or 'quota' in err_str.lower() or 'rate' in err_str.lower():
                    app.logger.warning(f"Model {m_name} quota hit. Rotating API key and switching fallback.")
                    rotate_api_key()
                else:
                    app.logger.warning(f"Model {m_name} failed: {e}. Trying fallback if available...")
                last_error = e
                full_reply = ""
                continue

        if last_error and not full_reply.strip():
            app.logger.error(f"All Gemini models failed: {last_error}")
            err_msg = "Abhi system thora busy hai! Apna number ya email share karein — hamara consultant 5 minute mein contact karega. \U0001F4DE"
            yield f"data: {json.dumps({'chunk': err_msg})}\n\n"

        # Save assistant message to DB at the end
        if full_reply.strip():
            save_message(session_id, "assistant", full_reply.strip())

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/lead", methods=["POST"])
@require_session
def capture_lead():
    """Call this when the bot (or frontend logic) determines a lead is ready to be saved."""
    data = request.get_json(silent=True) or {}
    db = get_db()
    db.execute(
        "INSERT INTO leads (session_id, name, business_name, contact, interest, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            g.session_id,
            data.get("name"),
            data.get("business_name"),
            data.get("contact"),
            data.get("interest"),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    db.commit()
    return jsonify({"status": "saved"})


# ---------------------------------------------------------------------------
# Admin Dashboard & Authentication Endpoints
# ---------------------------------------------------------------------------

@app.route("/admin")
def admin_page():
    """Serve the Admin Leads Portal."""
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "admin.html")


@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    """Authenticate admin user and start session."""
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        session["is_admin"] = True
        return jsonify({"status": "ok", "message": "Logged in successfully"})
    return jsonify({"error": "Invalid username or password"}), 401


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    """Log out admin user and destroy session."""
    session.pop("is_admin", None)
    return jsonify({"status": "ok", "message": "Logged out"})


@app.route("/api/admin/check-auth", methods=["GET"])
def admin_check_auth():
    """Check if the current session is authenticated as admin."""
    return jsonify({"authenticated": bool(session.get("is_admin"))})


@app.route("/api/admin/stats", methods=["GET"])
@require_admin
def admin_stats():
    """Return key metrics: leads count."""
    db = get_db()
    total_leads = db.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    return jsonify({
        "total_leads": total_leads
    })


@app.route("/api/admin/leads", methods=["GET"])
@require_admin
def admin_leads():
    """Return leads ordered by latest first, with optional server-side date filtering."""
    db = get_db()
    date_from = request.args.get("from")   # YYYY-MM-DD
    date_to   = request.args.get("to")     # YYYY-MM-DD

    query = "SELECT * FROM leads"
    params = []
    conditions = []

    if date_from:
        conditions.append("DATE(created_at) >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("DATE(created_at) <= ?")
        params.append(date_to)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id DESC"

    rows = db.execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/conversation/<session_id>", methods=["GET"])
@require_admin
def admin_conversation_detail(session_id):
    """Return full message transcript for a specific lead session."""
    db = get_db()
    rows = db.execute(
        "SELECT id, session_id, role, message, created_at FROM conversations WHERE session_id = ? ORDER BY id ASC",
        (session_id,)
    ).fetchall()
    lead = db.execute("SELECT * FROM leads WHERE session_id = ?", (session_id,)).fetchone()
    return jsonify({
        "messages": [dict(r) for r in rows],
        "lead": dict(lead) if lead else None
    })


@app.route("/api/admin/export-csv", methods=["GET"])
@require_admin
def admin_export_csv():
    """Export all captured leads to a downloadable CSV file."""
    db = get_db()
    rows = db.execute("SELECT id, name, contact, business_name, interest, created_at, session_id FROM leads ORDER BY id DESC").fetchall()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Lead ID", "Contact Name", "Phone / Email", "Business Name", "Service Interest", "Date Captured", "Session ID"])
    for r in rows:
        writer.writerow([r["id"], r["name"], r["contact"], r["business_name"], r["interest"], r["created_at"], r["session_id"]])
    
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=sidekick_leads_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"}
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": datetime.now(timezone.utc).isoformat()})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# Always initialize the database (works in both WSGI and direct run modes)
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # Use Waitress for production-ready WSGI serving
    from waitress import serve
    print(f"Starting Waitress production server on port {port}...")
    serve(app, host="0.0.0.0", port=port, threads=8)