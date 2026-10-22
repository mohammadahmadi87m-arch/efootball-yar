import os
import secrets
from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

database_url = os.environ.get("DATABASE_URL", "sqlite:///efootball_yar.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

db = SQLAlchemy(app)

ONLINE_SECONDS = 90
CODE_LENGTH = 8
CODE_LIFETIME_MINUTES = 5
ALLOWED_INTERNET_TYPES = {"همراه اول", "ایرانسل", "رایتل", "وای‌فای"}

RUBIKA_CHANNEL_URL = "https://rubika.ir/efcup27"
RUBIKA_ADMIN_URL = "https://rubika.ir/Mamadb87"
TELEGRAM_ADMIN_URL = "https://t.me/Mamad13287"


# -------------------------
# Database models
# -------------------------

class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)


class Judge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    rubika_id = db.Column(db.String(120), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)


class QueueEntry(db.Model):
    # Kept for compatibility with existing databases and old records.
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, default="")
    rubika_id = db.Column(db.String(120), nullable=False, default="")
    mode = db.Column(db.String(30), nullable=False, default="friendly")
    prize_level = db.Column(db.String(30), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Match(db.Model):
    # Kept for compatibility with existing databases and old records.
    id = db.Column(db.Integer, primary_key=True)
    player1_name = db.Column(db.String(120), nullable=False, default="")
    player1_rubika = db.Column(db.String(120), nullable=False, default="")
    player2_name = db.Column(db.String(120), nullable=False, default="")
    player2_rubika = db.Column(db.String(120), nullable=False, default="")
    mode = db.Column(db.String(30), nullable=False, default="friendly")
    prize_level = db.Column(db.String(30), nullable=True)
    judge_id = db.Column(db.Integer, db.ForeignKey("judge.id"), nullable=True)
    player1_queue_id = db.Column(db.Integer, db.ForeignKey("queue_entry.id"), nullable=True, index=True)
    player2_queue_id = db.Column(db.Integer, db.ForeignKey("queue_entry.id"), nullable=True, index=True)
    player1_confirmed = db.Column(db.Boolean, default=False)
    player2_confirmed = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(30), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    judge = db.relationship("Judge")


class Cup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    teams_count = db.Column(db.Integer, nullable=False)
    format_type = db.Column(db.String(160), nullable=False)
    prize = db.Column(db.String(300), nullable=False, default="")
    entry_fee = db.Column(db.String(100), nullable=False, default="")
    # Existing column names retained for compatibility.
    organizer_rubika = db.Column(db.String(200), nullable=False, default="")
    channel_rubika = db.Column(db.String(500), nullable=False, default="")
    status = db.Column(db.String(30), default="open")
    contact_platform = db.Column(db.String(20), nullable=False, default="rubika")


class FriendlyCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(8), nullable=False, index=True)
    owner_token = db.Column(db.String(64), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    # Internet type added without replacing existing records.
    internet_type = db.Column(db.String(30), nullable=False, default="وای‌فای")


class SiteStats(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    total_visits = db.Column(db.Integer, nullable=False, default=0)


class OnlineSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_key = db.Column(db.String(64), unique=True, nullable=False, index=True)
    last_seen = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)


# -------------------------
# Database compatibility / maintenance
# -------------------------

def ensure_column(table_name, column_name, column_sql):
    inspector = inspect(db.engine)
    if table_name not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name not in existing:
        with db.engine.begin() as connection:
            connection.execute(text(
                f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {column_sql}'
            ))


def remove_expired_codes():
    """Remove only codes whose own five-minute lifetime has elapsed."""
    cutoff = datetime.utcnow() - timedelta(minutes=CODE_LIFETIME_MINUTES)
    FriendlyCode.query.filter(
        FriendlyCode.created_at <= cutoff
    ).delete(synchronize_session=False)
    db.session.commit()


def initialize_database():
    db.create_all()

    # Add columns to existing databases without dropping any data.
    ensure_column("cup", "contact_platform", "VARCHAR(20) NOT NULL DEFAULT 'rubika'")
    ensure_column("match", "player1_queue_id", "INTEGER")
    ensure_column("match", "player2_queue_id", "INTEGER")
    ensure_column(
        "friendly_code",
        "internet_type",
        "VARCHAR(30) NOT NULL DEFAULT 'وای‌فای'"
    )

    if not SiteStats.query.first():
        db.session.add(SiteStats(total_visits=0))
        db.session.commit()

    cutoff = datetime.utcnow() - timedelta(seconds=ONLINE_SECONDS)
    OnlineSession.query.filter(
        OnlineSession.last_seen < cutoff
    ).delete(synchronize_session=False)
    remove_expired_codes()
    db.session.commit()


def create_admin_from_env():
    # Keep an existing admin account unchanged.
    if Admin.query.first():
        return

    username = os.environ.get("ADMIN_USERNAME")
    password = os.environ.get("ADMIN_PASSWORD")
    if not username or not password:
        app.logger.warning(
            "Admin was not created: set ADMIN_USERNAME and ADMIN_PASSWORD."
        )
        return

    db.session.add(Admin(
        username=username,
        password_hash=generate_password_hash(password)
    ))
    db.session.commit()


# -------------------------
# Request tracking / counters
# -------------------------

@app.before_request
def track_visitor():
    if request.endpoint == "static":
        return

    # Expired codes are cleaned during normal site traffic.
    remove_expired_codes()

    visitor_token = session.get("visitor_token")
    if not visitor_token:
        visitor_token = secrets.token_hex(24)
        session["visitor_token"] = visitor_token

    now = datetime.utcnow()
    online = OnlineSession.query.filter_by(session_key=visitor_token).first()
    if online:
        online.last_seen = now
    else:
        db.session.add(OnlineSession(
            session_key=visitor_token,
            last_seen=now
        ))

    # Count regular page visits, not API heartbeats or health checks.
    if request.method == "GET" and not (
        request.path.startswith("/api/") or request.path == "/health"
    ):
        stats = SiteStats.query.first()
        if not stats:
            stats = SiteStats(total_visits=0)
            db.session.add(stats)
        stats.total_visits += 1

    cutoff = now - timedelta(seconds=ONLINE_SECONDS)
    OnlineSession.query.filter(
        OnlineSession.last_seen < cutoff
    ).delete(synchronize_session=False)
    db.session.commit()


@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    token = session.get("visitor_token")
    if not token:
        token = secrets.token_hex(24)
        session["visitor_token"] = token

    now = datetime.utcnow()
    online = OnlineSession.query.filter_by(session_key=token).first()
    if online:
        online.last_seen = now
    else:
        db.session.add(OnlineSession(
            session_key=token,
            last_seen=now
        ))

    cutoff = now - timedelta(seconds=ONLINE_SECONDS)
    OnlineSession.query.filter(
        OnlineSession.last_seen < cutoff
    ).delete(synchronize_session=False)
    db.session.commit()

    count = OnlineSession.query.filter(
        OnlineSession.last_seen >= cutoff
    ).count()
    return jsonify({"online": count})


@app.context_processor
def inject_site_stats():
    try:
        stats = SiteStats.query.first()
        total_visits = stats.total_visits if stats else 0
        cutoff = datetime.utcnow() - timedelta(seconds=ONLINE_SECONDS)
        online_count = OnlineSession.query.filter(
            OnlineSession.last_seen >= cutoff
        ).count()
    except Exception:
        total_visits = 0
        online_count = 0

    return {
        "total_visits": total_visits,
        "online_count": online_count,
        "rubika_channel_url": RUBIKA_CHANNEL_URL,
        "rubika_admin_url": RUBIKA_ADMIN_URL,
        "telegram_admin_url": TELEGRAM_ADMIN_URL
    }


# -------------------------
# Public pages
# -------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/opponent")
def opponent():
    return render_template("opponent.html")


@app.route("/opponent/code/register", methods=["GET", "POST"])
def register_code():
    if request.method == "POST":
        code = "".join(
            ch for ch in request.form.get("code", "")
            if ch.isdigit()
        )
        internet_type = request.form.get("internet_type", "").strip()

        if len(code) != CODE_LENGTH:
            return render_template(
                "register_code.html",
                error="کد بازی باید دقیقاً ۸ رقم باشد.",
                code_value=code,
                selected_internet=internet_type
            )

        if internet_type not in ALLOWED_INTERNET_TYPES:
            return render_template(
                "register_code.html",
                error="لطفاً نوع اینترنت را انتخاب کن.",
                code_value=code,
                selected_internet=""
            )

        # Every submission creates a separate record. A new code does not
        # delete an older code, even if both were submitted by the same visitor.
        owner_token = secrets.token_hex(24)
        new_code = FriendlyCode(
            code=code,
            owner_token=owner_token,
            internet_type=internet_type,
            created_at=datetime.utcnow()
        )
        db.session.add(new_code)
        db.session.commit()

        # Keep compatibility with any old templates, but do not use this
        # session data to delete codes when a visitor leaves a page.
        session["owned_code_id"] = new_code.id
        session["owned_code_token"] = owner_token

        return render_template(
            "register_code.html",
            success="کد بازی با موفقیت ثبت شد و تا ۵ دقیقه در فهرست می‌ماند.",
            registered_code=code,
            registered_internet=internet_type
        )

    return render_template(
        "register_code.html",
        code_value="",
        selected_internet=""
    )


@app.route("/opponent/codes")
def ready_codes():
    remove_expired_codes()
    codes = FriendlyCode.query.order_by(
        FriendlyCode.created_at.desc(),
        FriendlyCode.id.desc()
    ).all()

    return render_template("ready_codes.html", codes=codes)


# Backward-compatible endpoint: page exit does not delete a code.
# Codes remain available until their own five-minute expiry.
@app.route("/api/codes/leave", methods=["POST"])
def delete_owned_code_on_leave():
    return jsonify({
        "deleted": False,
        "message": "کد تا پایان زمان ۵ دقیقه‌ای خود باقی می‌ماند."
    })


@app.route("/api/codes")
def codes_api():
    remove_expired_codes()
    codes = FriendlyCode.query.order_by(
        FriendlyCode.created_at.desc(),
        FriendlyCode.id.desc()
    ).all()

    return jsonify([
        {
            "id": item.id,
            "code": item.code,
            "internet_type": item.internet_type,
            "created_at": item.created_at.isoformat()
        }
        for item in codes
    ])


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# -------------------------
# Cups
# -------------------------

@app.route("/cups")
def cups():
    all_cups = Cup.query.order_by(Cup.id.desc()).all()
    return render_template("cups.html", cups=all_cups)


# -------------------------
# Admin
# -------------------------

def admin_required():
    if not session.get("admin_id"):
        return redirect(url_for("admin_login"))
    return None


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        admin = Admin.query.filter_by(username=username).first()

        if admin and check_password_hash(admin.password_hash, password):
            session["admin_id"] = admin.id
            session.permanent = True
            return redirect(url_for("admin_dashboard"))

        return render_template(
            "admin/login.html",
            error="نام کاربری یا رمز عبور اشتباه است."
        )

    return render_template("admin/login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_id", None)
    return redirect(url_for("index"))


@app.route("/admin")
def admin_dashboard():
    # Admin pages remain protected even if their links are hidden publicly.
    if not session.get("admin_id"):
        return redirect(url_for("admin_login"))

    return render_template(
        "admin/dashboard.html",
        cups=Cup.query.order_by(Cup.id.desc()).all(),
        judges=Judge.query.order_by(Judge.id.desc()).all(),
        queue=QueueEntry.query.order_by(QueueEntry.created_at.desc()).all()
    )


@app.route("/admin/cups/add", methods=["POST"])
def add_cup():
    if not session.get("admin_id"):
        return redirect(url_for("admin_login"))

    try:
        platform = request.form.get(
            "contact_platform", "rubika"
        ).strip().lower()
        if platform not in {"rubika", "telegram"}:
            platform = "rubika"

        organizer = (
            request.form.get("organizer_contact", "").strip()
            or request.form.get("organizer_rubika", "").strip()
        )
        channel = (
            request.form.get("channel_link", "").strip()
            or request.form.get("channel_rubika", "").strip()
        )

        cup = Cup(
            name=request.form["name"].strip(),
            teams_count=int(request.form["teams_count"]),
            format_type=request.form["format_type"].strip(),
            prize=request.form.get("prize", "").strip(),
            entry_fee=request.form.get("entry_fee", "").strip(),
            organizer_rubika=organizer,
            channel_rubika=channel,
            contact_platform=platform,
            status=request.form.get("status", "open").strip()
        )
        db.session.add(cup)
        db.session.commit()
    except (ValueError, KeyError):
        db.session.rollback()
        return "اطلاعات کاپ نامعتبر است؛ فیلدها را بررسی کن.", 400

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/cups/<int:cup_id>/delete", methods=["POST"])
def delete_cup(cup_id):
    if not session.get("admin_id"):
        return redirect(url_for("admin_login"))

    cup = db.session.get(Cup, cup_id)
    if cup:
        db.session.delete(cup)
        db.session.commit()

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/judges/add", methods=["POST"])
def add_judge():
    if not session.get("admin_id"):
        return redirect(url_for("admin_login"))

    name = request.form.get("name", "").strip()
    rubika_id = request.form.get("rubika_id", "").strip()
    if name and rubika_id:
        db.session.add(Judge(
            name=name,
            rubika_id=rubika_id,
            active=True
        ))
        db.session.commit()

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/judges/<int:judge_id>/toggle", methods=["POST"])
def toggle_judge(judge_id):
    if not session.get("admin_id"):
        return redirect(url_for("admin_login"))

    judge = db.session.get(Judge, judge_id)
    if judge:
        judge.active = not judge.active
        db.session.commit()

    return redirect(url_for("admin_dashboard"))


with app.app_context():
    initialize_database()
    create_admin_from_env()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        debug=False
    )
