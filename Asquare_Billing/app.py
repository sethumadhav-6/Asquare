from __future__ import annotations

import csv
import io
import os
import sqlite3
from datetime import date, datetime
from functools import wraps

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_from_directory,
    session,
    url_for,
)


APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "asquare_billing.sqlite3")

COMPANY = {
    "name": "A Square Interiors",
    "address": "Pishari kovil road, Eroor, Tripunithura - 682306",
    "phone": "8547579715",
    "gstin": "32BCYPA3806K1ZI",
    "bank": "ASQUARE INTERIORS, 43052790757, SBIN0071236",
    "tagline": "Reflecting your Dreams",
    "invoice_prefix": "B2C",
}

HSN_CODES = [
    ("94036000", "Wooden Furniture"),
    ("94035000", "Wooden Furniture of a kind used in the bedroom"),
    ("94032000", "Other metal furniture"),
    ("94038900", "Other furniture and parts thereof"),
    ("94016100", "Upholstered seats with wooden frames"),
    ("94013000", "Swivel seats with variable height adjustment"),
    ("94054000", "Other electric lamps and lighting fittings"),
    ("94060000", "Prefabricated buildings"),
]

UNITS = ["NOS", "SQF", "SQM", "RFT", "MTR", "KG", "SET", "PAIR", "LOT"]

app = Flask(__name__)
app.secret_key = os.environ.get("ASQUARE_SECRET_KEY", "change-this-local-dev-key")


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    con = db()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            address TEXT,
            phone TEXT,
            gstin TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_number TEXT NOT NULL UNIQUE,
            invoice_seq INTEGER NOT NULL,
            financial_year TEXT NOT NULL,
            invoice_date TEXT NOT NULL,
            customer_id INTEGER,
            customer_name TEXT NOT NULL,
            customer_address TEXT,
            customer_phone TEXT,
            customer_gstin TEXT,
            vehicle_number TEXT,
            place_of_supply TEXT DEFAULT 'THEVARA',
            subtotal REAL DEFAULT 0,
            discount REAL DEFAULT 0,
            taxable_value REAL DEFAULT 0,
            cgst_rate REAL DEFAULT 9,
            cgst_amount REAL DEFAULT 0,
            sgst_rate REAL DEFAULT 9,
            sgst_amount REAL DEFAULT 0,
            igst_rate REAL DEFAULT 0,
            igst_amount REAL DEFAULT 0,
            grand_total REAL DEFAULT 0,
            amount_in_words TEXT,
            notes TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS invoice_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            description TEXT NOT NULL,
            hsn_code TEXT,
            qty REAL DEFAULT 1,
            unit TEXT DEFAULT 'NOS',
            rate REAL DEFAULT 0,
            amount REAL DEFAULT 0,
            sort_order INTEGER DEFAULT 0,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS invoice_sequences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            financial_year TEXT NOT NULL UNIQUE,
            last_seq INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS hsn_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hsn_code TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL
        );
        """
    )
    con.executemany(
        "INSERT OR IGNORE INTO hsn_codes (hsn_code, description) VALUES (?, ?)",
        HSN_CODES,
    )
    con.commit()
    con.close()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def current_fy() -> str:
    today = date.today()
    return financial_year_for(today.isoformat())


def financial_year_for(value: str) -> str:
    dt = datetime.strptime(value, "%Y-%m-%d").date()
    start = dt.year if dt.month >= 4 else dt.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def peek_invoice_number(con: sqlite3.Connection, fy: str) -> str:
    row = con.execute(
        "SELECT last_seq FROM invoice_sequences WHERE financial_year = ?", (fy,)
    ).fetchone()
    next_seq = (row["last_seq"] if row else 0) + 1
    return f"{next_seq:02d}/{COMPANY['invoice_prefix']}/{fy}"


def next_invoice_number(con: sqlite3.Connection, fy: str) -> tuple[int, str]:
    row = con.execute(
        "SELECT last_seq FROM invoice_sequences WHERE financial_year = ?", (fy,)
    ).fetchone()
    if row:
        seq = row["last_seq"] + 1
        con.execute(
            "UPDATE invoice_sequences SET last_seq = ? WHERE financial_year = ?",
            (seq, fy),
        )
    else:
        seq = 1
        con.execute(
            "INSERT INTO invoice_sequences (financial_year, last_seq) VALUES (?, ?)",
            (fy, seq),
        )
    return seq, f"{seq:02d}/{COMPANY['invoice_prefix']}/{fy}"


def number_to_words(amount: float) -> str:
    amount = round(float(amount or 0), 2)
    rupees = int(amount)
    paise = int(round((amount - rupees) * 100))
    ones = [
        "",
        "ONE",
        "TWO",
        "THREE",
        "FOUR",
        "FIVE",
        "SIX",
        "SEVEN",
        "EIGHT",
        "NINE",
        "TEN",
        "ELEVEN",
        "TWELVE",
        "THIRTEEN",
        "FOURTEEN",
        "FIFTEEN",
        "SIXTEEN",
        "SEVENTEEN",
        "EIGHTEEN",
        "NINETEEN",
    ]
    tens = ["", "", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY", "EIGHTY", "NINETY"]

    def hundreds(n: int) -> str:
        out = ""
        if n >= 100:
            out += ones[n // 100] + " HUNDRED "
            n %= 100
        if n >= 20:
            out += tens[n // 10] + " "
            n %= 10
        if n:
            out += ones[n] + " "
        return out

    def indian(n: int) -> str:
        if n == 0:
            return "ZERO"
        out = ""
        for limit, label in [(10000000, "CRORE"), (100000, "LAKH"), (1000, "THOUSAND")]:
            if n >= limit:
                out += hundreds(n // limit) + label + " "
                n %= limit
        if n:
            out += hundreds(n)
        return out.strip()

    words = indian(rupees) + " RUPEES"
    if paise:
        words += " AND " + indian(paise) + " PAISE"
    return words + " ONLY"


def money(value) -> str:
    return f"{float(value or 0):,.2f}"


def qty(value) -> str:
    return f"{float(value or 0):.3f}".rstrip("0").rstrip(".")


app.jinja_env.globals.update(company=COMPANY, money=money, qty=qty, units=UNITS)

BASE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} - A Square Interiors</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:Segoe UI,Arial,sans-serif;background:#f0f2f8;color:#222;min-height:100vh}
a{color:inherit}.topnav{background:#1a2a5e;color:#fff;display:flex;align-items:center;gap:12px;height:58px;padding:0 20px;position:sticky;top:0;z-index:10;box-shadow:0 2px 8px rgba(0,0,0,.25)}
.brand{display:flex;align-items:center;gap:10px;text-decoration:none;font-weight:700;font-size:17px;flex:1}.brand img{height:40px;object-fit:contain}.brand span{display:block;font-size:11px;color:#b8d0ff;font-weight:400}
nav{display:flex;gap:4px;align-items:center}nav a{color:#c8d8ff;text-decoration:none;padding:8px 14px;border-radius:6px;font-size:13px;font-weight:500;white-space:nowrap}nav a:hover,nav a.active{background:rgba(255,255,255,.15);color:#fff}nav a.new{background:#b8860b;color:#fff;font-weight:700;margin-left:6px}
.logout{background:rgba(255,255,255,.1);color:#c8d8ff;border:1px solid rgba(255,255,255,.2);padding:6px 12px;border-radius:6px;font-size:12px;text-decoration:none}
.wrap{max-width:1200px;margin:0 auto;padding:24px 20px}.card{background:#fff;border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,.08);padding:24px;margin-bottom:20px}.card-header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:18px}.card-header h2{font-size:18px;color:#1a2a5e}
.btn{display:inline-block;padding:8px 18px;border-radius:6px;font-size:13px;font-weight:600;text-decoration:none;cursor:pointer;border:0;transition:.15s}.btn-primary{background:#1a2a5e;color:#fff}.btn-gold{background:#b8860b;color:#fff}.btn-outline{background:transparent;border:1.5px solid #1a2a5e;color:#1a2a5e}.btn-danger{background:#c0392b;color:#fff}.btn-sm{padding:5px 12px;font-size:12px}
.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px}th{background:#1a2a5e;color:#fff;padding:10px 12px;text-align:left}td{padding:9px 12px;border-bottom:1px solid #eee}tr:hover td{background:#f5f7ff}.right{text-align:right}.center{text-align:center}.badge{display:inline-block;padding:3px 9px;border-radius:12px;font-size:11px;font-weight:700}.badge-active{background:#d5f5e3;color:#1e8449}.badge-cancelled{background:#fadbd8;color:#922b21}
.alert{padding:12px 16px;border-radius:7px;margin-bottom:16px;font-size:13px}.alert-success{background:#d5f5e3;color:#1e8449;border:1px solid #a9dfbf}.alert-error{background:#fadbd8;color:#922b21;border:1px solid #f1948a}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:20px}.stat{background:#fff;border-radius:8px;padding:20px;box-shadow:0 2px 10px rgba(0,0,0,.07);text-align:center;border-top:4px solid #1a2a5e}.stat.gold{border-top-color:#b8860b}.stat .val{font-size:28px;font-weight:700;color:#1a2a5e}.stat.gold .val{color:#b8860b}.stat .lbl{font-size:12px;color:#666;margin-top:4px}
input,select,textarea{width:100%;padding:9px 12px;border:1.5px solid #d0d5e8;border-radius:6px;font-size:13px;outline:none;background:#fff}input:focus,select:focus,textarea:focus{border-color:#1a2a5e}label{display:block;font-size:12px;color:#555;font-weight:600;margin-bottom:4px}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}.fg{margin-bottom:14px}.section-title{font-size:15px;font-weight:700;color:#1a2a5e;margin:14px 0;padding-bottom:7px;border-bottom:2px solid #e8ecf8}
.filters{background:#fff;border-radius:8px;box-shadow:0 2px 10px rgba(0,0,0,.07);padding:16px 20px;margin-bottom:18px;display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end}.filters .fg{margin:0}.filters input,.filters select{width:auto;min-width:150px}
.autocomplete{position:relative}.autocomplete-list{position:absolute;z-index:20;background:#fff;border:1.5px solid #b0b8d8;border-radius:0 0 7px 7px;width:100%;max-height:180px;overflow:auto;box-shadow:0 4px 16px rgba(0,0,0,.12);display:none}.autocomplete-list div{padding:9px 12px;cursor:pointer;font-size:13px;border-bottom:1px solid #f0f0f0}.autocomplete-list div:hover{background:#f0f3ff}
.footer{text-align:center;padding:18px;font-size:11px;color:#999;border-top:1px solid #e0e4f0;margin-top:30px;background:#fff}
@media(max-width:760px){.topnav{height:auto;align-items:flex-start;flex-wrap:wrap;padding:12px}.brand{flex-basis:100%}nav{flex-wrap:wrap}.grid2{grid-template-columns:1fr}.card-header{align-items:flex-start;flex-direction:column}.wrap{padding:16px 10px}}
</style>
</head>
<body>
{% if session.get('user_id') %}
<div class="topnav">
  <a href="{{ url_for('dashboard') }}" class="brand">{% if logo_exists %}<img src="{{ url_for('logo') }}" alt="Logo">{% endif %}<div>A Square Interiors<span>Billing System</span></div></a>
  <nav>
    <a class="{{ 'active' if active=='dashboard' else '' }}" href="{{ url_for('dashboard') }}">Dashboard</a>
    <a class="{{ 'active' if active=='invoices' else '' }}" href="{{ url_for('invoices') }}">All Invoices</a>
    <a class="{{ 'active' if active=='customers' else '' }}" href="{{ url_for('customers') }}">Customers</a>
    <a class="new {{ 'active' if active=='new' else '' }}" href="{{ url_for('invoice_form') }}">+ New Invoice</a>
  </nav>
  <a href="{{ url_for('logout') }}" class="logout">Logout ({{ session.get('username') }})</a>
</div>
{% endif %}
<div class="wrap">
{% for category, message in get_flashed_messages(with_categories=true) %}
  <div class="alert alert-{{ category }}">{{ message }}</div>
{% endfor %}
{{ content|safe }}
</div>
{% if session.get('user_id') %}<div class="footer">&copy; {{ year }} A Square Interiors - Billing System - GSTIN: {{ company.gstin }}</div>{% endif %}
</body></html>
"""


def render_page(content: str, title: str, active: str = "", **context):
    body = render_template_string(content, **context)
    return render_template_string(
        BASE,
        content=body,
        title=title,
        active=active,
        year=date.today().year,
        logo_exists=os.path.exists(os.path.join(APP_DIR, "logo.png")),
    )


@app.route("/logo.png")
def logo():
    return send_from_directory(APP_DIR, "logo.png")


@app.route("/", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    error = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if username == "Arun" and password == "8547@Arun":
            session["user_id"] = 1
            session["username"] = "Arun"
            return redirect(url_for("dashboard"))
        error = "Invalid username or password."
    return render_template_string(
        """
        <!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <title>A Square Interiors - Login</title><style>
        *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}body{min-height:100vh;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#1a2a5e,#2e3f8a 60%,#b8860b);font-family:Segoe UI,Arial,sans-serif}.card{background:#fff;border-radius:12px;box-shadow:0 20px 60px rgba(0,0,0,.35);width:380px;max-width:92vw;padding:40px 36px 36px;text-align:center}.card img{max-height:100px;max-width:220px;object-fit:contain}h1{font-size:22px;color:#1a2a5e;margin:12px 0 4px}.tag{font-size:12px;color:#b8860b;font-style:italic;margin-bottom:28px}.fg{text-align:left;margin-bottom:18px}label{display:block;font-size:13px;color:#444;font-weight:600;margin-bottom:5px}input{width:100%;padding:10px 14px;border:1.5px solid #d0d5e8;border-radius:7px;font-size:14px}.btn{width:100%;padding:12px;background:#1a2a5e;color:#fff;border:0;border-radius:7px;font-size:15px;font-weight:700;cursor:pointer}.err{background:#fff0f0;border:1px solid #ffb3b3;color:#c0392b;border-radius:6px;padding:9px 12px;margin-bottom:16px;font-size:13px}.foot{margin-top:22px;font-size:11px;color:#aaa}
        </style></head><body><div class="card">
        {% if logo_exists %}<img src="{{ url_for('logo') }}" alt="A Square Interiors">{% endif %}
        <h1>A Square Interiors</h1><p class="tag">Reflecting your Dreams</p>
        {% if error %}<div class="err">{{ error }}</div>{% endif %}
        <form method="post" autocomplete="off"><div class="fg"><label>Username</label><input name="username" required autofocus value="{{ request.form.get('username','') }}"></div><div class="fg"><label>Password</label><input type="password" name="password" required></div><button class="btn">LOGIN</button></form>
        <p class="foot">Billing System v1.0 - Python App</p></div></body></html>
        """,
        error=error,
        logo_exists=os.path.exists(os.path.join(APP_DIR, "logo.png")),
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    con = db()
    fy = current_fy()
    stats = {
        "total_invoices": con.execute("SELECT COUNT(*) FROM invoices WHERE status='active'").fetchone()[0],
        "fy_count": con.execute("SELECT COUNT(*) FROM invoices WHERE financial_year=? AND status='active'", (fy,)).fetchone()[0],
        "total_revenue": con.execute("SELECT COALESCE(SUM(grand_total),0) FROM invoices WHERE status='active'").fetchone()[0],
        "fy_revenue": con.execute("SELECT COALESCE(SUM(grand_total),0) FROM invoices WHERE financial_year=? AND status='active'", (fy,)).fetchone()[0],
        "month_revenue": con.execute("SELECT COALESCE(SUM(grand_total),0) FROM invoices WHERE status='active' AND substr(invoice_date,1,7)=?", (date.today().strftime("%Y-%m"),)).fetchone()[0],
    }
    recent = con.execute("SELECT * FROM invoices ORDER BY id DESC LIMIT 10").fetchall()
    next_no = peek_invoice_number(con, fy)
    con.close()
    return render_page(
        """
        <div class="stats">
          <div class="stat"><div class="val">{{ stats.fy_count }}</div><div class="lbl">Invoices This FY ({{ fy }})</div></div>
          <div class="stat gold"><div class="val">Rs. {{ money(stats.fy_revenue).split('.')[0] }}</div><div class="lbl">Revenue This FY</div></div>
          <div class="stat"><div class="val">Rs. {{ money(stats.month_revenue).split('.')[0] }}</div><div class="lbl">This Month</div></div>
          <div class="stat gold"><div class="val">{{ stats.total_invoices }}</div><div class="lbl">Total Invoices</div></div>
        </div>
        <div class="card"><div class="card-header"><h2>Recent Invoices</h2><div><span style="font-size:13px;color:#666">Next: <b style="color:#1a2a5e">{{ next_no }}</b></span> <a class="btn btn-gold" href="{{ url_for('invoice_form') }}">+ New Invoice</a></div></div>
        {% if not recent %}<p style="text-align:center;color:#888;padding:30px 0">No invoices yet. <a href="{{ url_for('invoice_form') }}">Create your first invoice</a>.</p>{% else %}
        <div class="table-wrap"><table><thead><tr><th>#</th><th>Invoice No</th><th>Date</th><th>Customer</th><th class="right">Amount</th><th>Status</th><th>Actions</th></tr></thead><tbody>
        {% for inv in recent %}<tr><td>{{ loop.index }}</td><td><b>{{ inv.invoice_number }}</b></td><td>{{ inv.invoice_date }}</td><td>{{ inv.customer_name }}</td><td class="right">Rs. {{ money(inv.grand_total) }}</td><td><span class="badge badge-{{ inv.status }}">{{ inv.status.upper() }}</span></td><td><a class="btn btn-sm btn-outline" href="{{ url_for('view_invoice', invoice_id=inv.id) }}">View</a> <a class="btn btn-sm btn-primary" target="_blank" href="{{ url_for('print_invoice', invoice_id=inv.id) }}">Print</a></td></tr>{% endfor %}
        </tbody></table></div><div style="margin-top:14px;text-align:right"><a class="btn btn-outline btn-sm" href="{{ url_for('invoices') }}">View All Invoices</a></div>{% endif %}</div>
        """,
        "Dashboard",
        "dashboard",
        fy=fy,
        stats=stats,
        recent=recent,
        next_no=next_no,
    )


@app.route("/invoices")
@login_required
def invoices():
    search = request.args.get("search", "").strip()
    fy_filter = request.args.get("fy", "").strip()
    month = request.args.get("month", "").strip()
    status = request.args.get("status", "active").strip() or "active"
    where, params = ["1=1"], []
    if search:
        where.append("(invoice_number LIKE ? OR customer_name LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    if fy_filter:
        where.append("financial_year = ?")
        params.append(fy_filter)
    if month:
        where.append("substr(invoice_date,1,7) = ?")
        params.append(month)
    if status != "all":
        where.append("status = ?")
        params.append(status)
    con = db()
    rows = con.execute("SELECT * FROM invoices WHERE " + " AND ".join(where) + " ORDER BY id DESC", params).fetchall()
    fys = [r[0] for r in con.execute("SELECT DISTINCT financial_year FROM invoices ORDER BY financial_year DESC").fetchall()]
    con.close()
    if "export" in request.args:
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["Invoice No", "Date", "FY", "Customer", "Phone", "Place of Supply", "Subtotal", "Discount", "Taxable", "CGST", "SGST", "Grand Total", "Status"])
        for inv in rows:
            writer.writerow([inv["invoice_number"], inv["invoice_date"], inv["financial_year"], inv["customer_name"], inv["customer_phone"], inv["place_of_supply"], inv["subtotal"], inv["discount"], inv["taxable_value"], inv["cgst_amount"], inv["sgst_amount"], inv["grand_total"], inv["status"]])
        return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=asquare_invoices_{date.today():%Y%m%d}.csv"})
    totals = {k: sum(float(r[k] or 0) for r in rows) for k in ["taxable_value", "cgst_amount", "sgst_amount", "grand_total"]}
    return render_page(INVOICES_TEMPLATE, "All Invoices", "invoices", rows=rows, fys=fys, totals=totals, filters={"search": search, "fy": fy_filter, "month": month, "status": status})


INVOICES_TEMPLATE = """
<form method="get"><div class="filters">
  <div class="fg"><label>Search</label><input name="search" placeholder="Invoice No / Customer" value="{{ filters.search }}"></div>
  <div class="fg"><label>Financial Year</label><select name="fy"><option value="">All FY</option>{% for fy in fys %}<option value="{{ fy }}" {{ 'selected' if filters.fy==fy else '' }}>{{ fy }}</option>{% endfor %}</select></div>
  <div class="fg"><label>Month</label><input type="month" name="month" value="{{ filters.month }}"></div>
  <div class="fg"><label>Status</label><select name="status">{% for s in ['active','cancelled','all'] %}<option value="{{ s }}" {{ 'selected' if filters.status==s else '' }}>{{ s.title() }}</option>{% endfor %}</select></div>
  <button class="btn btn-primary btn-sm">Filter</button><a href="{{ url_for('invoices') }}" class="btn btn-outline btn-sm">Reset</a><a href="{{ request.full_path ~ '&export=1' if '?' in request.full_path else '?export=1' }}" class="btn btn-gold btn-sm" style="margin-left:auto">Export CSV</a>
</div></form>
<div class="card"><div class="card-header"><h2>Invoices - {{ rows|length }} records</h2><span style="font-size:14px;font-weight:700;color:#b8860b">Total: Rs. {{ money(totals.grand_total) }}</span></div>
{% if not rows %}<p style="text-align:center;color:#888;padding:30px">No invoices found. <a href="{{ url_for('invoice_form') }}">Create one</a>.</p>{% else %}
<div class="table-wrap"><table><thead><tr><th>Invoice No</th><th>Date</th><th>FY</th><th>Customer</th><th>Phone</th><th class="right">Taxable</th><th class="right">CGST</th><th class="right">SGST</th><th class="right">Total</th><th>Status</th><th>Actions</th></tr></thead><tbody>
{% for inv in rows %}<tr><td><b>{{ inv.invoice_number }}</b></td><td>{{ inv.invoice_date }}</td><td>{{ inv.financial_year }}</td><td>{{ inv.customer_name }}</td><td>{{ inv.customer_phone or '' }}</td><td class="right">Rs. {{ money(inv.taxable_value) }}</td><td class="right">Rs. {{ money(inv.cgst_amount) }}</td><td class="right">Rs. {{ money(inv.sgst_amount) }}</td><td class="right"><b>Rs. {{ money(inv.grand_total) }}</b></td><td><span class="badge badge-{{ inv.status }}">{{ inv.status.upper() }}</span></td><td><a class="btn btn-sm btn-outline" href="{{ url_for('view_invoice', invoice_id=inv.id) }}">View</a> <a class="btn btn-sm btn-primary" target="_blank" href="{{ url_for('print_invoice', invoice_id=inv.id) }}">Print</a></td></tr>{% endfor %}
</tbody><tfoot><tr style="background:#f0f2f8;font-weight:700"><td colspan="5">TOTALS</td><td class="right">Rs. {{ money(totals.taxable_value) }}</td><td class="right">Rs. {{ money(totals.cgst_amount) }}</td><td class="right">Rs. {{ money(totals.sgst_amount) }}</td><td class="right" style="color:#1a2a5e">Rs. {{ money(totals.grand_total) }}</td><td colspan="2"></td></tr></tfoot></table></div>{% endif %}</div>
"""


def load_invoice(invoice_id: int):
    con = db()
    inv = con.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    items = con.execute("SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY sort_order", (invoice_id,)).fetchall()
    con.close()
    return inv, items


@app.route("/invoice/new", methods=["GET", "POST"])
@app.route("/invoice/<int:invoice_id>/edit", methods=["GET", "POST"])
@login_required
def invoice_form(invoice_id: int | None = None):
    con = db()
    inv = items = None
    if invoice_id:
        inv = con.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        if not inv:
            con.close()
            return redirect(url_for("invoices"))
        items = con.execute("SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY sort_order", (invoice_id,)).fetchall()
    if request.method == "POST":
        result = save_invoice(con, invoice_id)
        if isinstance(result, str):
            flash(result, "error")
        else:
            con.close()
            if request.form.get("action") == "print":
                return redirect(url_for("print_invoice", invoice_id=result))
            return redirect(url_for("view_invoice", invoice_id=result, saved=1))
    hsn_rows = con.execute("SELECT hsn_code, description FROM hsn_codes ORDER BY description").fetchall()
    hsn_list = [dict(row) for row in hsn_rows]
    next_no = inv["invoice_number"] if inv else peek_invoice_number(con, current_fy())
    con.close()
    return render_page(INVOICE_FORM_TEMPLATE, "Edit Invoice" if inv else "New Invoice", "new", inv=inv, items=items or [], hsn_list=hsn_list, next_no=next_no, today=date.today().isoformat())


def save_invoice(con: sqlite3.Connection, invoice_id: int | None):
    f = request.form
    inv_date = f.get("invoice_date") or date.today().isoformat()
    fy = financial_year_for(inv_date)
    name = f.get("customer_name", "").strip()
    if not name:
        return "Customer name is required."
    descs = f.getlist("item_desc[]")
    hsns = f.getlist("item_hsn[]")
    qtys = f.getlist("item_qty[]")
    units = f.getlist("item_unit[]")
    rates = f.getlist("item_rate[]")
    items, subtotal = [], 0.0
    for i, desc in enumerate(descs):
        desc = desc.strip()
        if not desc:
            continue
        item_qty = float(qtys[i] or 0) if i < len(qtys) else 1
        rate = float(rates[i] or 0) if i < len(rates) else 0
        amount = round(item_qty * rate, 2)
        subtotal += amount
        items.append((desc, hsns[i] if i < len(hsns) else "", item_qty, units[i] if i < len(units) else "NOS", rate, amount, len(items)))
    if not items:
        return "At least one item is required."
    discount = float(f.get("discount") or 0)
    taxable = round(subtotal - discount, 2)
    cgst = round(taxable * 0.09, 2)
    sgst = cgst
    grand = round(taxable + cgst + sgst, 2)
    words = number_to_words(grand)
    address = f.get("customer_address", "").strip()
    phone = f.get("customer_phone", "").strip()
    gstin = f.get("customer_gstin", "").strip()
    vehicle = f.get("vehicle_number", "").strip()
    place = f.get("place_of_supply", "THEVARA").strip() or "THEVARA"
    con.execute("BEGIN")
    cust = con.execute("SELECT id FROM customers WHERE name=? LIMIT 1", (name,)).fetchone()
    if cust:
        customer_id = cust["id"]
        con.execute("UPDATE customers SET address=?, phone=?, gstin=? WHERE id=?", (address, phone, gstin, customer_id))
    else:
        cur = con.execute("INSERT INTO customers (name,address,phone,gstin) VALUES (?,?,?,?)", (name, address, phone, gstin))
        customer_id = cur.lastrowid
    if invoice_id:
        con.execute(
            """UPDATE invoices SET invoice_date=?, customer_id=?, customer_name=?, customer_address=?, customer_phone=?, customer_gstin=?, vehicle_number=?, place_of_supply=?, subtotal=?, discount=?, taxable_value=?, cgst_rate=9, cgst_amount=?, sgst_rate=9, sgst_amount=?, grand_total=?, amount_in_words=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (inv_date, customer_id, name, address, phone, gstin, vehicle, place, subtotal, discount, taxable, cgst, sgst, grand, words, invoice_id),
        )
        con.execute("DELETE FROM invoice_items WHERE invoice_id=?", (invoice_id,))
        save_id = invoice_id
    else:
        seq, number = next_invoice_number(con, fy)
        cur = con.execute(
            """INSERT INTO invoices (invoice_number,invoice_seq,financial_year,invoice_date,customer_id,customer_name,customer_address,customer_phone,customer_gstin,vehicle_number,place_of_supply,subtotal,discount,taxable_value,cgst_rate,cgst_amount,sgst_rate,sgst_amount,grand_total,amount_in_words) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (number, seq, fy, inv_date, customer_id, name, address, phone, gstin, vehicle, place, subtotal, discount, taxable, 9, cgst, 9, sgst, grand, words),
        )
        save_id = cur.lastrowid
    con.executemany("INSERT INTO invoice_items (invoice_id,description,hsn_code,qty,unit,rate,amount,sort_order) VALUES (?,?,?,?,?,?,?,?)", [(save_id, *it) for it in items])
    con.commit()
    return save_id


INVOICE_FORM_TEMPLATE = """
<div class="card"><div class="card-header"><h2>{{ 'Edit Invoice' if inv else 'Create New Invoice' }}</h2><b style="font-size:18px;color:#b8860b">{{ next_no }}</b></div>
<form method="post" id="inv-form">
<div class="section-title">Customer Details</div>
<div class="grid2"><div class="fg"><label>Customer Name *</label><div class="autocomplete"><input name="customer_name" id="customer_name" required autocomplete="off" value="{{ inv.customer_name if inv else '' }}"><div class="autocomplete-list" id="cust-dropdown"></div></div></div><div class="fg"><label>Phone</label><input name="customer_phone" id="customer_phone" value="{{ inv.customer_phone if inv else '' }}"></div></div>
<div class="fg"><label>Address</label><textarea name="customer_address" id="customer_address" rows="2">{{ inv.customer_address if inv else '' }}</textarea></div>
<div class="grid2"><div class="fg"><label>Customer GSTIN</label><input name="customer_gstin" id="customer_gstin" value="{{ inv.customer_gstin if inv else '' }}"></div><div class="fg"><label>Vehicle Number</label><input name="vehicle_number" value="{{ inv.vehicle_number if inv else '' }}"></div></div>
<div class="grid2"><div class="fg"><label>Place of Supply</label><input name="place_of_supply" value="{{ inv.place_of_supply if inv else 'THEVARA' }}"></div><div class="fg"><label>Invoice Date *</label><input type="date" name="invoice_date" id="invoice_date" required value="{{ inv.invoice_date if inv else today }}"></div></div>
<div class="section-title">Items / Description of Goods</div><div class="table-wrap"><table id="items-table"><thead><tr><th style="width:35%">Description</th><th>HSN</th><th>Qty</th><th>Unit</th><th>Rate</th><th>Amount</th><th></th></tr></thead><tbody id="items-body">
{% set form_items = items if items else [{'description':'','hsn_code':'94036000','qty':1,'unit':'NOS','rate':''}] %}
{% for it in form_items %}<tr class="item-row"><td><div class="autocomplete"><input name="item_desc[]" class="item-desc" autocomplete="off" value="{{ it.description }}"><div class="autocomplete-list item-desc-drop"></div></div></td><td><select name="item_hsn[]" class="item-hsn">{% for h in hsn_list %}<option value="{{ h.hsn_code }}" {{ 'selected' if it.hsn_code==h.hsn_code else '' }}>{{ h.hsn_code }}</option>{% endfor %}<option value="">Other</option></select></td><td><input type="number" step="0.001" min="0" name="item_qty[]" class="item-qty" value="{{ it.qty }}"></td><td><select name="item_unit[]">{% for u in units %}<option {{ 'selected' if it.unit==u else '' }}>{{ u }}</option>{% endfor %}</select></td><td><input type="number" step="0.01" min="0" name="item_rate[]" class="item-rate" value="{{ it.rate }}"></td><td><input class="item-amt" readonly style="background:#f5f7ff"></td><td><button type="button" class="btn btn-sm btn-danger del-row">X</button></td></tr>{% endfor %}
</tbody></table></div><button type="button" id="add-row" class="btn btn-outline btn-sm" style="margin-top:8px">+ Add Row</button>
<div style="display:flex;justify-content:flex-end;margin-top:16px"><table style="width:330px"><tr><td>Sub Total</td><td class="right">Rs. <span id="t-subtotal">0.00</span></td></tr><tr><td>Less Discount</td><td class="right"><input type="number" name="discount" id="discount" step="0.01" min="0" value="{{ inv.discount if inv else 0 }}" style="width:110px;text-align:right"></td></tr><tr><td>Taxable Value</td><td class="right">Rs. <span id="t-taxable">0.00</span></td></tr><tr><td>CGST @ 9%</td><td class="right">Rs. <span id="t-cgst">0.00</span></td></tr><tr><td>SGST @ 9%</td><td class="right">Rs. <span id="t-sgst">0.00</span></td></tr><tr style="background:#1a2a5e;color:#fff;font-weight:700"><td>GRAND TOTAL</td><td class="right">Rs. <span id="t-grand">0.00</span></td></tr></table></div><div id="words-display" style="font-size:12px;color:#555;font-style:italic;text-align:right;margin-top:10px"></div>
<div style="margin-top:24px;display:flex;gap:12px;justify-content:flex-end"><a href="{{ url_for('view_invoice', invoice_id=inv.id) if inv else url_for('dashboard') }}" class="btn btn-outline">Cancel</a><button name="action" value="save" class="btn btn-primary">Save Invoice</button><button name="action" value="print" class="btn btn-gold">Save & Print</button></div></form></div>
<script>
const hsnList={{ hsn_list|tojson }}; const units={{ units|tojson }};
function esc(s){return String(s||'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
function makeRow(){return `<tr class="item-row"><td><div class="autocomplete"><input name="item_desc[]" class="item-desc" autocomplete="off"><div class="autocomplete-list item-desc-drop"></div></div></td><td><select name="item_hsn[]">${hsnList.map(h=>`<option value="${h.hsn_code}">${h.hsn_code}</option>`).join('')}<option value="">Other</option></select></td><td><input type="number" step="0.001" min="0" name="item_qty[]" class="item-qty" value="1"></td><td><select name="item_unit[]">${units.map(u=>`<option>${u}</option>`).join('')}</select></td><td><input type="number" step="0.01" min="0" name="item_rate[]" class="item-rate"></td><td><input class="item-amt" readonly style="background:#f5f7ff"></td><td><button type="button" class="btn btn-sm btn-danger del-row">X</button></td></tr>`}
function recalc(){let subtotal=0;document.querySelectorAll('.item-row').forEach(r=>{let q=parseFloat(r.querySelector('.item-qty').value)||0, rate=parseFloat(r.querySelector('.item-rate').value)||0, amt=Math.round(q*rate*100)/100;r.querySelector('.item-amt').value=amt.toFixed(2);subtotal+=amt});let discount=parseFloat(document.getElementById('discount').value)||0,taxable=Math.round((subtotal-discount)*100)/100,cgst=Math.round(taxable*9)/100,grand=Math.round((taxable+cgst+cgst)*100)/100;['subtotal','taxable','cgst','sgst','grand'].forEach((k,i)=>document.getElementById('t-'+k).textContent=[subtotal,taxable,cgst,cgst,grand][i].toFixed(2));if(grand>0)fetch('{{ url_for("api_words") }}?amount='+grand).then(r=>r.json()).then(d=>document.getElementById('words-display').textContent=d.words||'')}
function bindRow(r){r.querySelectorAll('.item-qty,.item-rate').forEach(i=>i.addEventListener('input',recalc));let inp=r.querySelector('.item-desc'),drop=r.querySelector('.item-desc-drop');inp.addEventListener('input',()=>{let q=inp.value.trim();if(!q){drop.style.display='none';return}fetch('{{ url_for("api_item_search") }}?q='+encodeURIComponent(q)).then(r=>r.json()).then(items=>{drop.innerHTML=items.map(i=>`<div data-desc="${esc(i.description)}">${esc(i.description)}</div>`).join('');drop.style.display=items.length?'block':'none'})});drop.addEventListener('click',e=>{if(e.target.dataset.desc){inp.value=e.target.dataset.desc;drop.style.display='none'}})}
document.getElementById('add-row').addEventListener('click',()=>{document.getElementById('items-body').insertAdjacentHTML('beforeend',makeRow());bindRow(document.querySelector('#items-body tr:last-child'));recalc()});document.addEventListener('click',e=>{if(e.target.classList.contains('del-row')&&document.querySelectorAll('.item-row').length>1){e.target.closest('tr').remove();recalc()}if(!e.target.closest('.autocomplete'))document.querySelectorAll('.autocomplete-list').forEach(d=>d.style.display='none')});document.getElementById('discount').addEventListener('input',recalc);document.querySelectorAll('.item-row').forEach(bindRow);
let cust=document.getElementById('customer_name'),drop=document.getElementById('cust-dropdown');cust.addEventListener('input',()=>{let q=cust.value.trim();if(!q){drop.style.display='none';return}fetch('{{ url_for("api_customer_search") }}?q='+encodeURIComponent(q)).then(r=>r.json()).then(rows=>{drop.innerHTML=rows.map(c=>`<div data-name="${esc(c.name)}" data-address="${esc(c.address)}" data-phone="${esc(c.phone)}" data-gstin="${esc(c.gstin)}"><b>${esc(c.name)}</b>${c.phone?' - '+esc(c.phone):''}</div>`).join('');drop.style.display=rows.length?'block':'none'})});drop.addEventListener('click',e=>{let d=e.target.closest('[data-name]');if(d){cust.value=d.dataset.name;document.getElementById('customer_address').value=d.dataset.address;document.getElementById('customer_phone').value=d.dataset.phone;document.getElementById('customer_gstin').value=d.dataset.gstin;drop.style.display='none'}});recalc();
</script>
"""


@app.route("/invoice/<int:invoice_id>")
@login_required
def view_invoice(invoice_id: int):
    inv, items = load_invoice(invoice_id)
    if not inv:
        return redirect(url_for("invoices"))
    if request.args.get("saved"):
        flash("Invoice saved successfully!", "success")
    return render_page(VIEW_TEMPLATE, f"Invoice {inv['invoice_number']}", "invoices", inv=inv, items=items)


VIEW_TEMPLATE = """
<div class="card"><div class="card-header"><div><h2>Invoice {{ inv.invoice_number }}</h2><span class="badge badge-{{ inv.status }}">{{ inv.status.upper() }}</span></div><div>{% if inv.status=='active' %}<a class="btn btn-outline btn-sm" href="{{ url_for('invoice_form', invoice_id=inv.id) }}">Edit</a> <a class="btn btn-primary btn-sm" target="_blank" href="{{ url_for('print_invoice', invoice_id=inv.id) }}">Print</a> <button class="btn btn-danger btn-sm" onclick="cancelInvoice({{ inv.id }})">Cancel Invoice</button>{% endif %} <a class="btn btn-outline btn-sm" href="{{ url_for('invoices') }}">All Invoices</a></div></div>
<div class="grid2"><div style="background:#f8f9fc;border-radius:8px;padding:14px"><b>Invoice Details</b><p>Invoice No: <b>{{ inv.invoice_number }}</b></p><p>Date: <b>{{ inv.invoice_date }}</b></p><p>Financial Year: <b>{{ inv.financial_year }}</b></p><p>Place of Supply: <b>{{ inv.place_of_supply }}</b></p>{% if inv.vehicle_number %}<p>Vehicle No: <b>{{ inv.vehicle_number }}</b></p>{% endif %}</div><div style="background:#f8f9fc;border-radius:8px;padding:14px"><b>Customer Details</b><p>Name: <b>{{ inv.customer_name }}</b></p>{% if inv.customer_address %}<p style="white-space:pre-line">Address: <b>{{ inv.customer_address }}</b></p>{% endif %}{% if inv.customer_phone %}<p>Phone: <b>{{ inv.customer_phone }}</b></p>{% endif %}{% if inv.customer_gstin %}<p>GSTIN: <b>{{ inv.customer_gstin }}</b></p>{% endif %}</div></div>
<div class="table-wrap" style="margin-top:16px"><table><thead><tr><th>#</th><th>Description</th><th>HSN</th><th>Qty</th><th>Unit</th><th class="right">Rate</th><th class="right">Amount</th></tr></thead><tbody>{% for it in items %}<tr><td>{{ loop.index }}</td><td><b>{{ it.description }}</b></td><td>{{ it.hsn_code }}</td><td>{{ qty(it.qty) }}</td><td>{{ it.unit }}</td><td class="right">Rs. {{ money(it.rate) }}</td><td class="right">Rs. {{ money(it.amount) }}</td></tr>{% endfor %}</tbody></table></div>
<div style="display:flex;justify-content:flex-end;margin-top:16px"><table style="width:320px"><tr><td>Subtotal</td><td class="right">Rs. {{ money(inv.subtotal) }}</td></tr>{% if inv.discount>0 %}<tr><td>Less Discount</td><td class="right">- Rs. {{ money(inv.discount) }}</td></tr>{% endif %}<tr><td>Taxable Value</td><td class="right"><b>Rs. {{ money(inv.taxable_value) }}</b></td></tr><tr><td>CGST @ 9%</td><td class="right">Rs. {{ money(inv.cgst_amount) }}</td></tr><tr><td>SGST @ 9%</td><td class="right">Rs. {{ money(inv.sgst_amount) }}</td></tr><tr style="background:#1a2a5e;color:#fff;font-weight:700"><td>GRAND TOTAL</td><td class="right">Rs. {{ money(inv.grand_total) }}</td></tr></table></div><div style="margin-top:12px;font-size:12px;color:#555;font-style:italic;text-align:right">{{ inv.amount_in_words }}</div><div style="margin-top:16px;padding:10px 14px;background:#f8f9fc;border-radius:7px;font-size:13px"><b style="color:#1a2a5e">Account Details:</b> {{ company.bank }}</div></div>
<script>function cancelInvoice(id){if(!confirm('Cancel this invoice?'))return;fetch('{{ url_for("api_cancel_invoice") }}',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'id='+id}).then(r=>r.json()).then(d=>{if(d.success)location.reload();else alert('Error cancelling invoice')})}</script>
"""


@app.route("/invoice/<int:invoice_id>/print")
@login_required
def print_invoice(invoice_id: int):
    inv, items = load_invoice(invoice_id)
    if not inv:
        return "Invoice not found", 404
    return render_template_string(PRINT_TEMPLATE, inv=inv, items=items, copies=["Original", "Duplicate", "Triplicate"])


PRINT_TEMPLATE = """
<!doctype html><html><head><meta charset="utf-8"><title>Invoice {{ inv.invoice_number }}</title><style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}body{font-family:Arial,sans-serif;background:#e8e8e8;font-size:9.5pt;color:#000}.toolbar{padding:10px;text-align:center}.page{display:flex;flex-direction:column;align-items:center;gap:10px;padding:10px}.copy{width:148mm;min-height:197mm;background:#fff;border:1px solid #999;padding:5mm;position:relative}.copy-label{position:absolute;top:3mm;right:4mm;font-size:7pt;color:#666;border:1px solid #aaa;padding:1px 5px;border-radius:3px}.head{display:flex;gap:6px;border-bottom:1.5px solid #1a2a5e;padding-bottom:4px}.logo{width:22mm}.logo img{width:100%;max-height:18mm;object-fit:contain}.co{flex:1;text-align:center}.co h1{font-size:16pt;color:#1a2a5e}.co p{font-size:7.5pt}.row{display:flex;border:1px solid #1a2a5e;margin-top:3px}.cell{flex:1;padding:3px 5px;font-size:8pt;border-right:1px solid #1a2a5e}.cell:last-child{border-right:0}.lbl{font-size:7pt;color:#555;font-weight:700;text-transform:uppercase}.val{font-weight:700;margin-top:1px}.items{width:100%;border-collapse:collapse;border-left:1px solid #1a2a5e;border-right:1px solid #1a2a5e;font-size:8pt}.items th{background:#1a2a5e;color:#fff;padding:3px 4px}.items td{padding:3px 4px;border-bottom:1px solid #ddd;border-right:1px solid #ddd}.right{text-align:right}.center{text-align:center}.totals{display:flex;border:1px solid #1a2a5e;border-top:0}.words{flex:1;padding:3px 5px;font-size:7.5pt}.totals table{width:50mm;border-collapse:collapse;font-size:8pt}.totals td{padding:2px 5px}.grand td{background:#1a2a5e;color:#fff;font-weight:700}.bank{border:1px solid #1a2a5e;border-top:0;padding:3px 5px;font-size:7.5pt;background:#f8f9fc}.sig{text-align:right;margin-top:10mm;font-size:8pt}@media print{body{background:#fff}.toolbar{display:none}.page{padding:0;gap:0}.copy{width:100%;border:0;page-break-after:always;padding:4mm}@page{size:A5;margin:4mm}}
</style></head><body><div class="toolbar"><button onclick="window.print()">Print</button></div><div class="page">{% for copy in copies %}<div class="copy"><div class="copy-label">{{ copy }}</div><div class="head"><div class="logo">{% if true %}<img src="{{ url_for('logo') }}">{% endif %}</div><div class="co"><div style="font-size:8pt;color:#555;letter-spacing:1px">TAX INVOICE</div><h1>{{ company.name }}</h1><p>{{ company.address }}</p><p><b>GSTIN: {{ company.gstin }} | Phone: {{ company.phone }}</b></p></div></div>
<div class="row"><div class="cell"><div class="lbl">Bill To</div><div class="val">{{ inv.customer_name }}</div><div>{{ inv.customer_address or '' }}</div><div>{{ inv.customer_phone or '' }}</div><div>{{ inv.customer_gstin or '' }}</div></div><div class="cell"><div class="lbl">Invoice No</div><div class="val">{{ inv.invoice_number }}</div><div>Date: {{ inv.invoice_date }}</div><div>FY: {{ inv.financial_year }}</div></div><div class="cell"><div class="lbl">Supply</div><div class="val">{{ inv.place_of_supply }}</div><div>Vehicle: {{ inv.vehicle_number or '' }}</div></div></div>
<table class="items"><thead><tr><th>Description</th><th>HSN</th><th>Qty</th><th>Unit</th><th>Rate</th><th>Amount</th></tr></thead><tbody>{% for it in items %}<tr><td><b>{{ it.description }}</b></td><td class="center">{{ it.hsn_code }}</td><td class="center">{{ qty(it.qty) }}</td><td class="center">{{ it.unit }}</td><td class="right">{{ money(it.rate) }}</td><td class="right">{{ money(it.amount) }}</td></tr>{% endfor %}{% for i in range(8-items|length if items|length<8 else 0) %}<tr><td>&nbsp;</td><td></td><td></td><td></td><td></td><td></td></tr>{% endfor %}</tbody></table>
<div class="totals"><div class="words"><b>Amount in words:</b><br>{{ inv.amount_in_words }}<br><br><b>Declaration:</b> Goods once sold will not be taken back.</div><table><tr><td>Subtotal</td><td class="right">{{ money(inv.subtotal) }}</td></tr>{% if inv.discount>0 %}<tr><td>Discount</td><td class="right">-{{ money(inv.discount) }}</td></tr>{% endif %}<tr><td>Taxable</td><td class="right">{{ money(inv.taxable_value) }}</td></tr><tr><td>CGST</td><td class="right">{{ money(inv.cgst_amount) }}</td></tr><tr><td>SGST</td><td class="right">{{ money(inv.sgst_amount) }}</td></tr><tr class="grand"><td>Total</td><td class="right">{{ money(inv.grand_total) }}</td></tr></table></div><div class="bank"><b>Bank:</b> {{ company.bank }}</div><div class="sig">For <b>{{ company.name }}</b><br><br>Authorised Signatory</div></div>{% endfor %}</div></body></html>
"""


@app.route("/customers")
@login_required
def customers():
    search = request.args.get("search", "").strip()
    con = db()
    params = []
    where = ""
    if search:
        where = "WHERE c.name LIKE ? OR c.phone LIKE ?"
        params = [f"%{search}%", f"%{search}%"]
    rows = con.execute(
        f"""SELECT c.*, COUNT(i.id) AS invoice_count, COALESCE(SUM(i.grand_total),0) AS total_billed
            FROM customers c LEFT JOIN invoices i ON i.customer_id=c.id AND i.status='active'
            {where} GROUP BY c.id ORDER BY c.name""",
        params,
    ).fetchall()
    con.close()
    return render_page(
        """
        <div class="card"><div class="card-header"><h2>Customers ({{ rows|length }})</h2><form method="get" style="display:flex;gap:8px"><input name="search" placeholder="Search by name / phone" value="{{ search }}" style="width:220px"><button class="btn btn-primary btn-sm">Search</button>{% if search %}<a class="btn btn-outline btn-sm" href="{{ url_for('customers') }}">Reset</a>{% endif %}</form></div>
        {% if not rows %}<p style="text-align:center;color:#888;padding:30px">No customers yet. They are auto-added when you create invoices.</p>{% else %}<div class="table-wrap"><table><thead><tr><th>#</th><th>Name</th><th>Phone</th><th>Address</th><th>GSTIN</th><th class="center">Invoices</th><th class="right">Total Billed</th><th>Actions</th></tr></thead><tbody>{% for c in rows %}<tr><td>{{ loop.index }}</td><td><b>{{ c.name }}</b></td><td>{{ c.phone or '' }}</td><td style="max-width:180px;white-space:pre-line;font-size:12px">{{ c.address or '' }}</td><td>{{ c.gstin or '' }}</td><td class="center">{{ c.invoice_count }}</td><td class="right">Rs. {{ money(c.total_billed) }}</td><td><a class="btn btn-sm btn-outline" href="{{ url_for('invoices', search=c.name) }}">Invoices</a> <a class="btn btn-sm btn-gold" href="{{ url_for('invoice_form') }}">+ Invoice</a></td></tr>{% endfor %}</tbody></table></div>{% endif %}</div>
        """,
        "Customers",
        "customers",
        rows=rows,
        search=search,
    )


@app.route("/api/customer_search")
@login_required
def api_customer_search():
    q = f"%{request.args.get('q', '')}%"
    con = db()
    rows = con.execute("SELECT id,name,address,phone,gstin FROM customers WHERE name LIKE ? ORDER BY name LIMIT 10", (q,)).fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/item_search")
@login_required
def api_item_search():
    q = f"%{request.args.get('q', '')}%"
    con = db()
    rows = con.execute("SELECT DISTINCT description FROM invoice_items WHERE description LIKE ? ORDER BY description LIMIT 10", (q,)).fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/words")
@login_required
def api_words():
    return jsonify({"words": number_to_words(float(request.args.get("amount", 0) or 0))})


@app.route("/api/peek_invoice_no")
@login_required
def api_peek_invoice_no():
    invoice_date = request.args.get("date") or date.today().isoformat()
    fy = financial_year_for(invoice_date)
    con = db()
    number = peek_invoice_number(con, fy)
    con.close()
    return jsonify({"number": number, "fy": fy})


@app.route("/api/cancel_invoice", methods=["POST"])
@login_required
def api_cancel_invoice():
    invoice_id = int(request.form.get("id") or 0)
    con = db()
    con.execute("UPDATE invoices SET status='cancelled' WHERE id=?", (invoice_id,))
    con.commit()
    con.close()
    return jsonify({"success": True})


if __name__ == "__main__":
    init_db()
    print("A Square Interiors Python app running at http://0.0.0.0:8547")
    app.run(host="0.0.0.0", port=8547, debug=True)
