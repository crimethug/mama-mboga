from datetime import datetime, timedelta
from functools import wraps
import logging
import math
import os
import re
import secrets
import sqlite3

from flask import Flask, abort, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mama_mboga")

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_HTTPS") == "1",
)
DB = os.path.join(app.root_path, "mama_mboga.db")
SCHEMA_VERSION = 3
DEMO_ITEMS = [
    ("Tomatoes", "Nyanya", "Vegetables", 40, 50, 42, 15),
    ("Sukuma Wiki", "Sukuma Wiki", "Herbs", 20, 25, 8, 10),
    ("Onions", "Vitunguu", "Vegetables", 45, 55, 30, 16),
    ("Sugar", "Sukari", "Grains", 150, 170, 12, 5),
    ("Cooking Oil", "Mafuta", "Dairy", 280, 310, 6, 3),
    ("Bananas", "Ndizi", "Fruits", 30, 40, 18, 12),
    ("Sweet Potatoes", "Viazi Vitamu", "Tubers", 45, 60, 25, 18),
]


# ---------- DB helpers ----------
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def column_exists(cursor, table, column):
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def demo_items_with_purchase_date():
    purchase_date = datetime.now().date().isoformat()
    return [item + (purchase_date,) for item in DEMO_ITEMS]


def init_db():
    """Idempotent: safe to call on every startup."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            name_sw TEXT DEFAULT '',
            category TEXT DEFAULT 'Vegetables',
            price REAL NOT NULL DEFAULT 0,
            sell_price REAL NOT NULL DEFAULT 0,
            quantity INTEGER NOT NULL DEFAULT 0,
            low_threshold INTEGER DEFAULT 5,
            purchase_date TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Migrate older schema gracefully
    for col, ddl in [
        ("name_sw", "ALTER TABLE items ADD COLUMN name_sw TEXT DEFAULT ''"),
        ("category", "ALTER TABLE items ADD COLUMN category TEXT DEFAULT 'Vegetables'"),
        ("sell_price", "ALTER TABLE items ADD COLUMN sell_price REAL NOT NULL DEFAULT 0"),
        ("purchase_date", "ALTER TABLE items ADD COLUMN purchase_date TEXT NOT NULL DEFAULT ''"),
    ]:
        if not column_exists(cursor, "items", col):
            cursor.execute(ddl)
    cursor.execute(
        """UPDATE items
           SET purchase_date = COALESCE(date(created_at), date('now'))
           WHERE purchase_date = '' OR purchase_date IS NULL"""
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            quantity INTEGER DEFAULT 1,
            unit_price REAL DEFAULT 0,
            total REAL DEFAULT 0,
            sold_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES items (id)
        )
    """)
    for col, ddl in [
        ("unit_price", "ALTER TABLE sales ADD COLUMN unit_price REAL DEFAULT 0"),
        ("total", "ALTER TABLE sales ADD COLUMN total REAL DEFAULT 0"),
    ]:
        if not column_exists(cursor, "sales", col):
            cursor.execute(ddl)

    cursor.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    cursor.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                   (str(SCHEMA_VERSION),))

    cursor.execute("SELECT COUNT(*) FROM items")
    if cursor.fetchone()[0] == 0:
        cursor.executemany(
            """INSERT INTO items
               (name, name_sw, category, price, sell_price, quantity, low_threshold, purchase_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            demo_items_with_purchase_date(),
        )

    conn.commit()
    conn.close()
    log.info("Database ready (schema v%s)", SCHEMA_VERSION)


# Run at import so `flask run` and WSGI work too
init_db()


# ---------- Auth ----------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Please log in first."}), 401
        return view(*args, **kwargs)
    return wrapped


def ensure_csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def csrf_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        token = request.headers.get("X-CSRF-Token")
        if not token or not secrets.compare_digest(token, session.get("csrf", "")):
            return jsonify({"error": "Invalid or missing CSRF token."}), 403
        return view(*args, **kwargs)
    return wrapped


@app.get("/api/csrf")
def csrf_token():
    return jsonify({"csrf": ensure_csrf()})


@app.post("/api/auth/register")
@csrf_required
def register():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip().lower()
    password = data.get("password", "")
    if not re.fullmatch(r"[a-z0-9_]{3,32}", username):
        return jsonify({"error": "Username must be 3–32 characters (letters, numbers, underscore)."}), 400
    if not isinstance(password, str) or len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, generate_password_hash(password)),
        )
        conn.commit()
        user_id = cur.lastrowid
    except sqlite3.IntegrityError:
        return jsonify({"error": "That username is already in use."}), 409
    finally:
        conn.close()

    session.clear()
    session["user_id"] = user_id
    session["username"] = username
    ensure_csrf()
    return jsonify({"username": username, "csrf": session["csrf"]}), 201


@app.post("/api/auth/login")
@csrf_required
def login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip().lower()
    password = data.get("password", "")
    if not isinstance(password, str):
        return jsonify({"error": "Invalid username or password."}), 401

    conn = get_db()
    user = conn.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    conn.close()
    if user is None or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid username or password."}), 401

    session.clear()
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    ensure_csrf()
    return jsonify({"username": user["username"], "csrf": session["csrf"]})


@app.get("/api/auth/session")
def auth_session():
    if "user_id" not in session:
        return jsonify({"authenticated": False})
    return jsonify({
        "authenticated": True,
        "username": session["username"],
        "csrf": ensure_csrf(),
    })


@app.post("/api/auth/logout")
@csrf_required
def logout():
    session.clear()
    return jsonify({"status": "logged_out"})


# ---------- Items ----------
def _validate_item(data):
    errors = []
    name = str(data.get("name", "")).strip()
    if not name:
        errors.append("Product name is required.")
    purchase_date = str(data.get("purchase_date", datetime.now().date().isoformat())).strip()
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", purchase_date):
            raise ValueError
        datetime.strptime(purchase_date, "%Y-%m-%d")
    except ValueError:
        errors.append("Enter a valid stock purchase date.")
    try:
        price = float(data.get("price", 0))
        sell_price = float(data.get("sell_price", price))
        quantity = int(data.get("quantity", 0))
        low_threshold = int(data.get("low_threshold", 5))
    except (TypeError, ValueError):
        errors.append("Price, quantity, and reorder level must be numbers.")
        return None, errors
    if not all(math.isfinite(value) for value in (price, sell_price)):
        errors.append("Prices must be valid numbers.")
    if sell_price == 0 and price > 0:
        sell_price = price
    if price < 0 or sell_price < 0 or quantity < 0 or low_threshold < 0:
        errors.append("Values cannot be negative.")
    if errors:
        return None, errors
    return {
        "name": name,
        "name_sw": str(data.get("name_sw", "")).strip(),
        "category": str(data.get("category", "Vegetables")).strip() or "Vegetables",
        "price": price,
        "sell_price": sell_price,
        "quantity": quantity,
        "low_threshold": low_threshold,
        "purchase_date": purchase_date,
    }, []


@app.get("/api/items")
@login_required
def get_items():
    conn = get_db()
    items = conn.execute("SELECT * FROM items ORDER BY name").fetchall()
    conn.close()
    return jsonify([dict(i) for i in items])


@app.post("/api/items")
@login_required
@csrf_required
def add_item():
    payload, errors = _validate_item(request.get_json(silent=True) or {})
    if errors:
        return jsonify({"error": " ".join(errors)}), 400
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO items
           (name, name_sw, category, price, sell_price, quantity, low_threshold, purchase_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (payload["name"], payload["name_sw"], payload["category"], payload["price"],
         payload["sell_price"], payload["quantity"], payload["low_threshold"],
         payload["purchase_date"]),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return jsonify({"status": "ok", "id": new_id}), 201


@app.put("/api/items/<int:item_id>")
@login_required
@csrf_required
def update_item(item_id):
    payload, errors = _validate_item(request.get_json(silent=True) or {})
    if errors:
        return jsonify({"error": " ".join(errors)}), 400
    conn = get_db()
    cur = conn.execute(
        """UPDATE items
           SET name=?, name_sw=?, category=?, price=?, sell_price=?, quantity=?, low_threshold=?,
               purchase_date=?
           WHERE id=?""",
        (payload["name"], payload["name_sw"], payload["category"], payload["price"],
         payload["sell_price"], payload["quantity"], payload["low_threshold"],
         payload["purchase_date"], item_id),
    )
    conn.commit()
    updated = cur.rowcount
    conn.close()
    if not updated:
        return jsonify({"error": "Item not found."}), 404
    return jsonify({"status": "ok"})


@app.delete("/api/items/<int:item_id>")
@login_required
@csrf_required
def delete_item(item_id):
    conn = get_db()
    cur = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    if not deleted:
        return jsonify({"error": "Item not found."}), 404
    return jsonify({"status": "ok"})


@app.post("/api/reset")
@login_required
@csrf_required
def reset_demo_data():
    conn = get_db()
    try:
        with conn:
            conn.execute("DELETE FROM sales")
            conn.execute("DELETE FROM items")
            conn.executemany(
                """INSERT INTO items
                   (name, name_sw, category, price, sell_price, quantity, low_threshold, purchase_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                demo_items_with_purchase_date(),
            )
    finally:
        conn.close()
    return jsonify({"status": "ok"})


# ---------- Sales ----------
@app.post("/api/sell")
@login_required
@csrf_required
def sell_item():
    data = request.get_json(silent=True) or {}
    try:
        item_id = int(data["item_id"])
        quantity = int(data.get("quantity", 1))
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "A valid item and quantity are required."}), 400
    if quantity <= 0:
        return jsonify({"error": "Quantity must be greater than zero."}), 400

    conn = get_db()
    item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        conn.close()
        return jsonify({"error": "Item not found."}), 404
    if item["quantity"] < quantity:
        conn.close()
        return jsonify({"error": f"Only {item['quantity']} units of {item['name']} in stock."}), 400

    unit_price = item["sell_price"] or item["price"]
    total = unit_price * quantity
    new_quantity = item["quantity"] - quantity
    conn.execute("UPDATE items SET quantity = ? WHERE id = ?", (new_quantity, item_id))
    conn.execute(
        "INSERT INTO sales (item_id, quantity, unit_price, total) VALUES (?, ?, ?, ?)",
        (item_id, quantity, unit_price, total),
    )
    conn.commit()
    conn.close()

    if new_quantity <= item["low_threshold"]:
        send_sms_alert(item["name"], new_quantity)

    return jsonify({
        "status": "sold",
        "remaining": new_quantity,
        "total": total,
        "receipt": {
            "item": item["name"],
            "quantity": quantity,
            "unit_price": unit_price,
            "total": total,
            "sold_at": datetime.now().isoformat(timespec="seconds"),
        },
    })


@app.get("/api/sales")
@login_required
def recent_sales():
    limit = min(int(request.args.get("limit", 20)), 200)
    conn = get_db()
    rows = conn.execute(
        """SELECT s.id, i.name AS item, s.quantity, s.unit_price, s.total, s.sold_at
           FROM sales s JOIN items i ON s.item_id = i.id
           ORDER BY s.sold_at DESC, s.id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ---------- Reports ----------
@app.get("/api/summary/daily")
@login_required
def daily_summary():
    conn = get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT i.name, SUM(s.quantity) AS sold,
                  SUM(s.quantity * s.unit_price) AS revenue
           FROM sales s JOIN items i ON s.item_id = i.id
           WHERE date(s.sold_at) = ?
           GROUP BY i.id ORDER BY sold DESC""",
        (today,),
    ).fetchall()
    total = sum(r["revenue"] or 0 for r in rows)
    conn.close()
    return jsonify({"date": today, "items": [dict(r) for r in rows], "total_revenue": total})


@app.get("/api/report/weekly")
@login_required
def weekly_report():
    conn = get_db()
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT i.name, SUM(s.quantity) AS sold,
                  SUM(s.quantity * s.unit_price) AS revenue
           FROM sales s JOIN items i ON s.item_id = i.id
           WHERE date(s.sold_at) >= ?
           GROUP BY i.id ORDER BY sold DESC LIMIT 10""",
        (week_ago,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "schema": SCHEMA_VERSION})


# ---------- SMS ----------
def send_sms_alert(item_name, remaining):
    message = f"Mwangis Shop Inventory System Alert: {item_name} is almost finished ({remaining} left). Restock today!"
    log.info("SMS_ALERT %s", message)
    # TODO: integrate Africa's Talking SDK with credentials from env


# ---------- Static files ----------
@app.route("/")
def home():
    return send_from_directory(app.root_path, "index.html")


@app.get("/inventory")
def inventory_page():
    return send_from_directory(app.root_path, "index.html")


@app.get("/assets/<path:filename>")
def assets(filename):
    if filename not in {"script.js", "styles.css"}:
        abort(404)
    return send_from_directory(app.root_path, filename)


@app.get("/styles.css")
def styles():
    return send_from_directory(app.root_path, "styles.css")


@app.get("/script.js")
def script():
    return send_from_directory(app.root_path, "script.js")


@app.get("/manifest.json")
def manifest():
    return send_from_directory(app.root_path, "manifest.json")


@app.get("/service-worker.js")
def service_worker():
    return send_from_directory(app.root_path, "service-worker.js")


@app.get("/icon.svg")
def app_icon():
    return send_from_directory(app.root_path, "icon.svg")


@app.get("/icons/<path:filename>")
def app_png_icon(filename):
    if filename not in {"icon-192.png", "icon-512.png"}:
        abort(404)
    return send_from_directory(os.path.join(app.root_path, "icons"), filename)


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)