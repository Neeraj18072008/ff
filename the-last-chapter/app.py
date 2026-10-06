"""
THE LAST CHAPTER — farewell night · invitation + pass system.

public site  : hero → gathering → what awaits → the night → same people
               → venue → dress code → countdown → ready → booking
booking      : single / couple · cash / UPI (QR per pass type) · 4-digit code
digital pass : /pass/<code> — poster-styled, screenshot/print friendly
control room : /organizer — login, stats, verify pass, allow entry
               (duplicate-code protected), bookings, activity, settings

stack: flask + sqlite (see store.py) · mobile-first black/crimson theme
"""
import io
import os
import re
import secrets
import time
from datetime import date, datetime
from functools import wraps
from urllib.parse import quote

import qrcode
import qrcode.image.svg
import click
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from itsdangerous import BadSignature, URLSafeSerializer
from werkzeug.security import check_password_hash, generate_password_hash

import store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.environ.get("LC_INSTANCE", os.path.join(BASE_DIR, "instance"))

EVENT = {
    "date_long": "3 NOVEMBER 2026",
    "date_iso": "2026-11-03T19:00:00+05:30",   # countdown target — 7:00 PM IST
    "date_dot": "03.11.2026",
    "time_range": "7:00 PM — 12:00 AM",
    "time_short": "7 — 12 PM",
    "venue": "PITCHERS",
    "venue_sub": "DB CITY MALL, BHOPAL",
    "maps": "https://www.google.com/maps/search/?api=1&query=Pitchers+DB+City+Mall+Bhopal",
}

# placeholder running order — swap in the real schedule when it's final
TIMELINE = [
    ("07:00 PM", "arrival & welcome"),
    ("07:15 PM", "welcome drink"),
    ("07:30 PM", "opening"),
    ("07:45 PM", "starters"),
    ("08:15 PM", "main course"),
    ("08:45 PM", "dance & celebration"),
    ("09:30 PM", "photography"),
    ("10:00 PM", "party"),
    ("11:00 PM", "final celebration"),
    ("12:00 AM", "the chapter closes"),
]

AWAITS = [
    ("drink",   "welcome drink", "walk in, pick one up"),
    ("starter", "starters",      "hot plates to open the night"),
    ("main",    "main course",   "the heavy lifting, done right"),
    ("dessert", "dessert",       "a sweet closing note"),
    ("dance",   "dance",         "the floor is yours"),
    ("camera",  "photography",   "frames from the whole night"),
    ("party",   "party",         "till the chapter closes"),
]

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'\-]{1,59}$")
PHONE_RE = re.compile(r"^[6-9]\d{9}$")
CODE_RE = re.compile(r"^\d{4}$")
UPI_RE = re.compile(r"^[\w.\-@*]{3,64}$")

PAY_LABEL = {"pending": "PENDING", "submitted": "VERIFYING", "paid": "PAID", "received": "RECEIVED", "rejected": "REJECTED"}

_login_fails = {}  # ip -> {"count": int, "until": float}

app = Flask(__name__)
app.secret_key = store.load_or_create_secret(INSTANCE_DIR)
store.init(INSTANCE_DIR)
_serializer = URLSafeSerializer(app.secret_key, salt="booking")


@app.cli.command("reset-booking-data")
@click.option("--yes", is_flag=True, help="Required acknowledgement; this removes all bookings.")
def reset_booking_data(yes):
    """Safely prepare the existing printed-code inventory for a fresh launch."""
    if not yes:
        raise click.UsageError("Refusing to reset data without --yes.")
    conn = store.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE pass_code_inventory SET status='available', booking_id=NULL, assigned_at=NULL, used_at=NULL")
        conn.execute("DELETE FROM booking")
        conn.execute("DELETE FROM activity_log")
        store.log(conn, None, "reset", "booking data reset; printed codes returned to available")
        conn.commit()
    finally:
        conn.close()
    click.echo("Bookings cleared; all printed pass codes are available.")


# ------------------------------------------------------------------ plumbing

def db():
    if "db" not in g:
        g.db = store.connect()
    return g.db


@app.teardown_appcontext
def _close_db(exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@app.template_filter("time_of")
def _time_of(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%I:%M %p")
    except Exception:
        return iso or ""


@app.template_filter("day_time")
def _day_time(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d %b · %I:%M %p")
    except Exception:
        return iso or ""


def csrf_token():
    tok = session.get("csrf")
    if not tok:
        tok = secrets.token_hex(16)
        session["csrf"] = tok
    return tok


@app.before_request
def _csrf_protect():
    if request.method == "POST":
        tok = session.get("csrf")
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        if not tok or not sent or not secrets.compare_digest(tok, sent):
            return jsonify(error="session expired — reload the page and try again"), 400


@app.context_processor
def _inject():
    return {
        "csrf_token": csrf_token,
        "EVENT": EVENT,
        "TIMELINE": TIMELINE,
        "AWAITS": AWAITS,
        "settings": store.get_settings(db()),
        "org_name": session.get("org_name"),
    }


def make_qr_svg(data: str) -> str:
    """inline, crisp, responsive SVG QR (renders on the white card)."""
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=4
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode()
    # the factory emits a fixed mm-sized <svg>; keep its coordinate system
    # (viewBox) but make it fluid + screen-friendly
    m = re.search(r'viewBox="([^"]+)"', svg)
    viewBox = m.group(1) if m else f"0 0 {qr.modules_count + 8} {qr.modules_count + 8}"
    return re.sub(
        r"<svg[^>]*>",
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewBox}" '
        f'shape-rendering="crispEdges" role="img" aria-label="UPI QR code">',
        svg,
        count=1,
    )


def upi_uri(b, s) -> str:
    pa = s["upi_id_single"] if b["pass_type"] == "single" else s["upi_id_couple"]
    return (
        f"upi://pay?pa={quote(pa)}"
        f"&pn={quote(s['payee_name'])}"
        f"&am={b['amount']}"
        f"&cu=INR"
        f"&tn={quote('The Last Chapter ' + b['pass_type'] + ' pass')}"
    )


def bview(b):
    """row → template/api-safe dict."""
    if b is None:
        return None
    booking_status = b["booking_status"] if "booking_status" in b.keys() else "active"
    return {
        "id": b["id"],
        "code": b["pass_code"],
        "type": b["pass_type"],
        "p1": b["person_1_name"],
        "p1_dob": b["person_1_dob"],
        "p2": b["person_2_name"],
        "p2_dob": b["person_2_dob"],
        "contact": b["contact"],
        "method": b["payment_method"],
        "pay_status": b["payment_status"],
        "pay_label": PAY_LABEL.get(b["payment_status"], b["payment_status"].upper()),
        "amount": b["amount"],
        "created": b["created_at"],
        "entry": b["entry_status"],
        "entry_time": b["entry_time"],
        "verified_by": b["verified_by"],
        "booking_status": booking_status,
        "cancelled_at": b["cancelled_at"] if "cancelled_at" in b.keys() else None,
        "cancelled_by": b["cancelled_by"] if "cancelled_by" in b.keys() else None,
    }


def _booking_from_token(token: str):
    try:
        bid = _serializer.loads(token)
    except BadSignature:
        abort(404)
    b = db().execute("SELECT * FROM booking WHERE id=?", (bid,)).fetchone()
    if b is None:
        abort(404)
    return b


# ------------------------------------------------------------------ public site

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    return {"ok": True}


# ------------------------------------------------------------------ booking

def _valid_dob(s: str) -> bool:
    try:
        d = date.fromisoformat(s)
    except ValueError:
        return False
    return date(1985, 1, 1) <= d <= date(2016, 12, 31)


def _validate_booking(f):
    errors, v = {}, {}
    v["pass_type"] = f.get("pass_type", "")
    v["payment"] = f.get("payment", "")
    v["p1_name"] = (f.get("p1_name") or "").strip()
    v["p1_dob"] = (f.get("p1_dob") or "").strip()
    v["p2_name"] = (f.get("p2_name") or "").strip()
    v["p2_dob"] = (f.get("p2_dob") or "").strip()
    v["contact"] = re.sub(r"\D", "", f.get("contact") or "")
    if v["pass_type"] not in ("single", "couple"):
        errors["pass_type"] = "choose a pass type"
    if not NAME_RE.fullmatch(v["p1_name"]):
        errors["p1_name"] = "enter a valid name (letters, spaces, . ' -)"
    if not _valid_dob(v["p1_dob"]):
        errors["p1_dob"] = "enter a valid date of birth"
    if v["pass_type"] == "couple":
        if not NAME_RE.fullmatch(v["p2_name"]):
            errors["p2_name"] = "enter a valid name (letters, spaces, . ' -)"
        if not _valid_dob(v["p2_dob"]):
            errors["p2_dob"] = "enter a valid date of birth"
    if not PHONE_RE.fullmatch(v["contact"]):
        errors["contact"] = "enter a valid 10-digit mobile number"
    if v["payment"] not in ("cash", "upi"):
        errors["payment"] = "choose a payment method"
    return errors, v


@app.route("/book", methods=["GET", "POST"])
def book_page():
    if request.method == "GET":
        pre = request.args.get("type")
        if pre not in ("single", "couple"):
            pre = None
        return render_template("book.html", pre=pre, errors={}, v={})

    errors, v = _validate_booking(request.form)
    if errors:
        return render_template("book.html", errors=errors, v=v, pre=None), 400

    s = store.get_settings(db())
    amount = int(s["price_couple"] if v["pass_type"] == "couple" else s["price_single"])
    v["amount"] = amount
    v["p2_name"] = v["p2_name"] if v["pass_type"] == "couple" else None
    v["p2_dob"] = v["p2_dob"] if v["pass_type"] == "couple" else None
    bid = store.create_booking_with_code(db(), v)
    if bid is None:
        flash("passes are currently sold out — please contact an organizer.", "error")
        return render_template("book.html", errors={}, v=v, pre=None), 409

    token = _serializer.dumps(bid)
    if v["payment"] == "cash":
        return redirect(url_for("confirmed", token=token))
    return redirect(url_for("pay", token=token))


@app.route("/pay/<token>")
def pay(token):
    b = _booking_from_token(token)
    if b["payment_method"] != "upi" or b["payment_status"] != "pending":
        return redirect(url_for("confirmed", token=token))
    s = store.get_settings(db())
    upi_id = s["upi_id_single"] if b["pass_type"] == "single" else s["upi_id_couple"]
    return render_template(
        "pay.html",
        p=bview(b),
        token=token,
        qr=make_qr_svg(upi_uri(b, s)),
        upi_id=upi_id,
        uri=upi_uri(b, s),
    )


@app.post("/pay/<token>/done")
def pay_done(token):
    b = _booking_from_token(token)
    if b["payment_method"] == "upi" and b["payment_status"] == "pending":
        db().execute(
            "UPDATE booking SET payment_status='submitted' WHERE id=?", (b["id"],)
        )
        store.log(db(), b["id"], "upi", f"UPI payment submitted — {b['pass_code']}")
        db().commit()
    return redirect(url_for("confirmed", token=token))


@app.route("/confirmed/<token>")
def confirmed(token):
    b = _booking_from_token(token)
    return render_template(
        "pass.html",
        p=bview(b),
        fresh=True,
        share_url=request.host_url.rstrip("/") + url_for("pass_page", code=b["pass_code"]),
    )


@app.route("/pass/<code>")
def pass_page(code):
    if not CODE_RE.fullmatch(code or ""):
        abort(404)
    b = db().execute(
        "SELECT * FROM booking WHERE pass_code=?", (code,)
    ).fetchone()
    if b is None:
        abort(404)
    return render_template(
        "pass.html",
        p=bview(b),
        fresh=False,
        share_url=request.host_url.rstrip("/") + url_for("pass_page", code=code),
    )


# ------------------------------------------------------------------ organizer

def org_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("org_id"):
            if request.method == "POST":
                return jsonify(error="organizer login required"), 401
            return redirect(url_for("org_login", next=request.path))
        return fn(*a, **kw)
    return wrapper


@app.route("/organizer/login", methods=["GET", "POST"])
def org_login():
    if session.get("org_id"):
        return redirect(url_for("organizer"))
    error = None
    if request.method == "POST":
        u = (request.form.get("username") or "").strip()
        p = request.form.get("password") or ""
        ip = request.remote_addr or "?"
        st = _login_fails.get(ip)
        now = time.time()
        if st and st["until"] > now:
            error = f"too many attempts — locked for {int(st['until'] - now) + 1}s"
        else:
            row = db().execute(
                "SELECT * FROM organizer WHERE username=?", (u,)
            ).fetchone()
            if row and check_password_hash(row["password_hash"], p):
                old_csrf = session.get("csrf")  # keep csrf stable across login
                session.clear()
                if old_csrf:
                    session["csrf"] = old_csrf
                session["org_id"] = row["id"]
                session["org_name"] = row["username"]
                _login_fails.pop(ip, None)
                return redirect(url_for("organizer"))
            st = _login_fails.setdefault(ip, {"count": 0, "until": 0})
            st["count"] += 1
            if st["count"] >= 5:
                st["until"] = now + 300
                st["count"] = 0
            error = "wrong username or password"
    return render_template("login.html", error=error)


@app.post("/organizer/logout")
def org_logout():
    session.clear()
    return redirect(url_for("index"))


FILTER_CHIPS = [
    (None, "all"),
    ("single", "single"), ("couple", "couple"),
    ("cash", "cash"), ("upi", "upi"),
    ("paid", "paid"), ("pending", "pending"),
    ("active", "active"), ("cancelled", "cancelled"),
    ("used", "used"), ("unused", "unused"),
]


@app.route("/organizer")
@org_required
def organizer():
    tab = request.args.get("tab", "overview")
    if tab not in ("overview", "verify", "bookings", "codes", "activity", "settings"):
        tab = "overview"
    ctx = {"tab": tab}
    if tab == "overview":
        ctx["stats"] = store.stats(db())
    elif tab == "bookings":
        q = request.args.get("q", "").strip()
        f = request.args.get("f", "")
        if f not in store.FILTER_SQL:
            f = ""
        ctx["rows"] = store.list_bookings(db(), q, f)
        ctx["q"], ctx["f"] = q, f
        chips = []
        for key, label in FILTER_CHIPS:
            args = {"tab": "bookings"}
            if q:
                args["q"] = q
            if key:
                args["f"] = key
            chips.append({
                "label": label,
                "href": url_for("organizer", **args),
                "active": (key or "") == f,
            })
        ctx["chips"] = chips
    elif tab == "activity":
        ctx["logs"] = store.recent_activity(db())
    elif tab == "codes":
        ctx["inventory"] = store.inventory_stats(db())
        ctx["codes"] = store.list_inventory(db())
    elif tab == "settings":
        ctx["pw_default"] = store.get_setting(db(), "pw_default") == "1"
    return render_template("organizer.html", **ctx)


@app.post("/organizer/codes")
@org_required
def org_codes():
    raw = request.form.get("codes", "")
    values = [line.strip() for line in re.split(r"[\s,]+", raw) if line.strip()]
    invalid = [value for value in values if not CODE_RE.fullmatch(value)]
    if invalid:
        flash("every pass code must be exactly four digits; nothing was imported.", "error")
    elif not values:
        flash("paste one or more printed pass codes.", "error")
    else:
        inserted, rejected = store.add_inventory_codes(db(), values)
        store.log(db(), None, "pass codes", f"{inserted} printed pass code(s) added by {session.get('org_name')}")
        db().commit()
        message = f"{inserted} pass code(s) added."
        if rejected:
            message += f" {len(rejected)} duplicate code(s) skipped."
        flash(message, "ok")
    return redirect(url_for("organizer", tab="codes"))


@app.post("/organizer/codes/remove")
@org_required
def org_codes_remove():
    data = request.get_json(silent=True) or request.form
    code = re.sub(r"\D", "", data.get("code") or "")
    if not CODE_RE.fullmatch(code):
        return jsonify(error="enter the 4-digit pass code"), 400
    result = store.remove_inventory_code(db(), code, session.get("org_name") or "organizer", data.get("reason") or "removed by organizer")
    if result["ok"]:
        store.log(db(), None, "pass removed", f"removed available pass code {code} by {session.get('org_name')}")
        db().commit()
        return jsonify(**result)
    return jsonify(**result), 409


@app.post("/organizer/codes/release")
@org_required
def org_codes_release():
    data = request.get_json(silent=True) or request.form
    code = re.sub(r"\D", "", data.get("code") or "")
    if not CODE_RE.fullmatch(code):
        return jsonify(error="enter the 4-digit pass code"), 400
    result = store.release_pass_code(db(), code, session.get("org_name") or "organizer", data.get("reason") or "released by organizer")
    if result["ok"]:
        store.log(db(), None, "pass released", f"released pass code {code} by {session.get('org_name')}")
        db().commit()
        return jsonify(**result)
    return jsonify(**result), 409


@app.post("/organizer/booking/<int:booking_id>/cancel")
@org_required
def org_booking_cancel(booking_id):
    b = db().execute("SELECT * FROM booking WHERE id=?", (booking_id,)).fetchone()
    if b is None:
        return jsonify(status="not_found"), 404
    if b["booking_status"] == "cancelled":
        return jsonify(status="already_cancelled", code=b["pass_code"])
    store.cancel_booking(db(), booking_id, session.get("org_name") or "organizer", "cancelled by organizer")
    store.log(db(), booking_id, "cancel", f"booking #{booking_id} cancelled by {session.get('org_name')}")
    db().commit()
    return jsonify(status="cancelled", code=b["pass_code"])


@app.get("/organizer/summary")
@org_required
def org_summary():
    return jsonify(store.stats(db()))


@app.post("/organizer/verify")
@org_required
def org_verify():
    data = request.get_json(silent=True) or {}
    code = re.sub(r"\D", "", data.get("code") or "")
    if not CODE_RE.fullmatch(code):
        return jsonify(error="enter the 4-digit pass code"), 400
    b = db().execute(
        "SELECT * FROM booking WHERE pass_code=?", (code,)
    ).fetchone()
    if b is None:
        return jsonify(status="not_found"), 404
    if b["booking_status"] == "cancelled":
        store.log(db(), b["id"], "verification", f"cancelled pass {code} rejected by {session.get('org_name')}")
        db().commit()
        return jsonify(status="cancelled", code=code, message="INVALID / CANCELLED PASS", **bview(b)), 409
    if b["entry_status"] == "used":
        store.log(db(), b["id"], "duplicate entry", f"duplicate entry attempt for pass {code} by {session.get('org_name')}")
        db().commit()
        payload = bview(b)
        payload["status"] = "already_used"
        payload["entry"] = "used"
        return jsonify(payload)
    store.log(db(), b["id"], "verify", f"pass {code} verified by {session.get('org_name')}")
    db().commit()
    return jsonify(status="found", **bview(b))


@app.post("/organizer/payment")
@org_required
def org_payment():
    data = request.get_json(silent=True) or request.form
    code = re.sub(r"\D", "", data.get("code") or "")
    b = db().execute(
        "SELECT * FROM booking WHERE pass_code=?", (code,)
    ).fetchone()
    if b is None:
        return jsonify(status="not_found"), 404
    if b["booking_status"] == "cancelled":
        return jsonify(status="cancelled", code=code), 409
    if b["payment_status"] in ("paid", "received"):
        return jsonify(status="already_paid", code=code)
    db().execute("BEGIN IMMEDIATE")
    cur = db().execute(
        "UPDATE booking SET payment_status='paid', updated_at=? WHERE id=? AND payment_status NOT IN ('paid','received')",
        (store.now_iso(), b["id"]),
    )
    if cur.rowcount:
        store.log(
            db(), b["id"], "payment",
            f"payment confirmed — {b['payment_method']} ₹{b['amount']} · {code}",
        )
        db().commit()
    return jsonify(status="ok", code=code)


@app.post("/organizer/entry")
@org_required
def org_entry():
    data = request.get_json(silent=True) or {}
    code = re.sub(r"\D", "", data.get("code") or "")
    if not CODE_RE.fullmatch(code):
        return jsonify(error="enter the 4-digit pass code"), 400
    b = db().execute(
        "SELECT * FROM booking WHERE pass_code=?", (code,)
    ).fetchone()
    if b is None:
        return jsonify(status="not_found"), 404
    if b["booking_status"] == "cancelled":
        return jsonify(status="cancelled", code=code, message="INVALID / CANCELLED PASS"), 409
    if b["entry_status"] == "used":
        store.log(db(), b["id"], "duplicate entry", f"duplicate entry attempt for pass {code} by {session.get('org_name')}")
        db().commit()
        return jsonify(
            status="already_used", code=code,
            entry_time=b["entry_time"], verified_by=b["verified_by"],
        )
    if b["payment_status"] not in ("paid", "received"):
        return jsonify(status="payment_due", **bview(b)), 409
    now = store.now_iso()
    org = session.get("org_name", "organizer")
    db().execute("BEGIN IMMEDIATE")
    cur = db().execute(
        """UPDATE booking SET entry_status='used', entry_time=?, verified_by=?, updated_at=?
           WHERE id=? AND entry_status='unused'""",
        (now, org, now, b["id"]),
    )
    if not cur.rowcount:  # race: marked used a moment ago
        fresh = db().execute(
            "SELECT * FROM booking WHERE id=?", (b["id"],)
        ).fetchone()
        store.log(db(), b["id"], "duplicate entry", f"duplicate entry attempt for pass {code} by {org}")
        db().commit()
        return jsonify(
            status="already_used", code=code,
            entry_time=fresh["entry_time"], verified_by=fresh["verified_by"],
        )
    store.mark_code_used(db(), b["id"], now)
    store.log(db(), b["id"], "entry", f"pass {code} entered by {org}")
    db().commit()
    return jsonify(
        status="approved", code=code, time=now, organizer=org,
        name=b["person_1_name"],
    )


@app.post("/organizer/settings")
@org_required
def org_settings():
    f = request.form
    errors = []

    def _price(name, label):
        raw = (f.get(name) or "").strip()
        try:
            v = int(raw)
            if not (0 <= v <= 100000):
                raise ValueError
        except ValueError:
            errors.append(f"{label} must be a number")
            return None
        return v

    ps = _price("price_single", "single pass price")
    pc = _price("price_couple", "couple pass price")
    u1 = (f.get("upi_id_single") or "").strip()
    u2 = (f.get("upi_id_couple") or "").strip()
    payee = (f.get("payee_name") or "").strip()[:40] or "THE LAST CHAPTER"
    if not UPI_RE.fullmatch(u1):
        errors.append("single-pass UPI id looks invalid")
    if not UPI_RE.fullmatch(u2):
        errors.append("couple-pass UPI id looks invalid")

    if errors:
        for e in errors:
            flash(e, "error")
    else:
        store.set_settings(db(), {
            "price_single": str(ps), "price_couple": str(pc),
            "upi_id_single": u1, "upi_id_couple": u2, "payee_name": payee,
        })
        store.log(db(), None, "settings", "prices / UPI settings updated")
        db().commit()
        flash("settings saved", "ok")
    return redirect(url_for("organizer", tab="settings"))


@app.post("/organizer/password")
@org_required
def org_password():
    cur_pw = request.form.get("current") or ""
    new_pw = request.form.get("new") or ""
    row = db().execute(
        "SELECT * FROM organizer WHERE id=?", (session["org_id"],)
    ).fetchone()
    if not check_password_hash(row["password_hash"], cur_pw):
        flash("current password is wrong", "error")
    elif len(new_pw) < 8:
        flash("new password must be at least 8 characters", "error")
    else:
        db().execute(
            "UPDATE organizer SET password_hash=? WHERE id=?",
            (generate_password_hash(new_pw), session["org_id"]),
        )
        store.set_settings(db(), {"pw_default": "0"})
        store.log(db(), None, "password", "organizer password changed")
        db().commit()
        flash("password updated", "ok")
    return redirect(url_for("organizer", tab="settings"))


# ------------------------------------------------------------------ errors

@app.errorhandler(404)
def _nf(e):
    return render_template(
        "error.html", code="404", title="not found",
        msg="this page doesn't exist — check the link and try again.",
    ), 404


@app.errorhandler(400)
def _br(e):
    return render_template(
        "error.html", code="400", title="bad request",
        msg="that request couldn't be processed — go back and try again.",
    ), 400


@app.errorhandler(500)
def _ise(e):
    return render_template(
        "error.html", code="500", title="server error",
        msg="something broke on our side — try again in a moment.",
    ), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)
