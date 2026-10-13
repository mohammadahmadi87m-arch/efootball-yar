import os
import secrets
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, abort
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

database_url = os.environ.get(
    "DATABASE_URL",
    "sqlite:///efootball_yar.db"
)

if database_url.startswith("postgres://"):
    database_url = database_url.replace(
        "postgres://", "postgresql://", 1
    )

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True
}

db = SQLAlchemy(app)


# -------------------------
# Models
# -------------------------

class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)


class Judge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    rubika_id = db.Column(db.String(120), nullable=False)
    active = db.Column(db.Boolean, default=True)


class QueueEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    rubika_id = db.Column(db.String(120), nullable=False)
    mode = db.Column(db.String(30), nullable=False)
    prize_level = db.Column(db.String(30), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Match(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    player1_name = db.Column(db.String(120), nullable=False)
    player1_rubika = db.Column(db.String(120), nullable=False)

    player2_name = db.Column(db.String(120), nullable=False)
    player2_rubika = db.Column(db.String(120), nullable=False)

    mode = db.Column(db.String(30), nullable=False)
    prize_level = db.Column(db.String(30), nullable=True)

    judge_id = db.Column(
        db.Integer,
        db.ForeignKey("judge.id"),
        nullable=True
    )

    # شناسه اختصاصی هر بازیکن در صف
    player1_queue_id = db.Column(
        db.Integer,
        db.ForeignKey("queue_entry.id"),
        nullable=True,
        index=True
    )

    player2_queue_id = db.Column(
        db.Integer,
        db.ForeignKey("queue_entry.id"),
        nullable=True,
        index=True
    )

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
    prize = db.Column(db.String(300), nullable=False)
    entry_fee = db.Column(db.String(100), nullable=False)
    organizer_rubika = db.Column(db.String(120), nullable=False)
    channel_rubika = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(30), default="open")


# -------------------------
# Helpers
# -------------------------

def admin_required():
    if not session.get("admin_id"):
        abort(403)


def get_active_match_for_queue(queue_id):
    """مسابقه‌ای را پیدا می‌کند که به شناسه صف بازیکن متصل است."""
    return Match.query.filter(
        Match.status.in_(["pending", "confirmed"]),
        or_(
            Match.player1_queue_id == queue_id,
            Match.player2_queue_id == queue_id
        )
    ).order_by(Match.id.desc()).first()


def set_match_session(match, queue_id):
    """شناسه مسابقه و جایگاه بازیکن را در نشست ذخیره می‌کند."""
    session.pop("queue_id", None)

    session["match_id"] = match.id

    if match.player1_queue_id == queue_id:
        session["player_slot"] = 1
    elif match.player2_queue_id == queue_id:
        session["player_slot"] = 2
    else:
        session["player_slot"] = 1


def create_match_for_entries(entry1, entry2):
    """دو بازیکن را به یک مسابقه متصل می‌کند."""
    judge = None

    if entry1.mode == "prize":
        judge = (
            Judge.query
            .filter_by(active=True)
            .order_by(Judge.id.asc())
            .first()
        )

    match = Match(
        player1_name=entry1.name,
        player1_rubika=entry1.rubika_id,
        player2_name=entry2.name,
        player2_rubika=entry2.rubika_id,
        mode=entry1.mode,
        prize_level=entry1.prize_level,
        judge_id=judge.id if judge else None,
        player1_queue_id=entry1.id,
        player2_queue_id=entry2.id,
        status="pending"
    )

    db.session.add(match)

    # رکوردهای صف حذف نمی‌شوند تا شناسه‌ها برای پیگیری
    # مسابقه باقی بمانند. رکوردها پس از پایان مسابقه
    # می‌توانند با یک فرایند پاک‌سازی حذف شوند.
    db.session.flush()

    return match


# -------------------------
# Main pages
# -------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/opponent")
def opponent():
    return render_template("opponent.html")


# -------------------------
# Queue and matching
# -------------------------

@app.route("/queue", methods=["POST"])
def queue():
    name = request.form.get("name", "").strip()
    rubika_id = request.form.get("rubika_id", "").strip()
    mode = request.form.get("mode", "").strip()
    prize_level = request.form.get("prize_level", "").strip() or None

    if not name or not rubika_id:
        return redirect(url_for("opponent"))

    if mode not in {"friendly", "prize"}:
        return redirect(url_for("opponent"))

    if mode == "friendly":
        prize_level = None

    if mode == "prize" and prize_level not in {
        "light", "ideal", "heavy"
    }:
        return redirect(url_for("opponent"))

    # اگر همین نشست از قبل در مسابقه است، همان مسابقه را باز کن.
    existing_match_id = session.get("match_id")

    if existing_match_id:
        existing_match = db.session.get(Match, existing_match_id)

        if existing_match and existing_match.status in {
            "pending", "confirmed"
        }:
            return redirect(url_for("match_view"))

        session.pop("match_id", None)
        session.pop("player_slot", None)

    # اگر همین نشست در صف است، دوباره رکورد نساز.
    existing_queue_id = session.get("queue_id")

    if existing_queue_id:
        existing_entry = db.session.get(
            QueueEntry, existing_queue_id
        )

        if existing_entry:
            return redirect(url_for("waiting"))

        session.pop("queue_id", None)

    try:
        # تراکنش کوتاه برای یافتن حریف و ایجاد مسابقه
        with db.session.begin_nested():
            candidate = (
                QueueEntry.query
                .filter_by(mode=mode, prize_level=prize_level)
                .order_by(
                    QueueEntry.created_at.asc(),
                    QueueEntry.id.asc()
                )
                .with_for_update()
                .first()
            )

            entry = QueueEntry(
                name=name,
                rubika_id=rubika_id,
                mode=mode,
                prize_level=prize_level
            )

            db.session.add(entry)
            db.session.flush()

            if candidate:
                # حریف باید یک رکورد صف متفاوت باشد.
                if candidate.id != entry.id:
                    match = create_match_for_entries(
                        candidate, entry
                    )

                    # فقط رکورد حریف از صف حذف می‌شود.
                    # شناسه او در Match ذخیره شده است.
                    db.session.delete(candidate)
                else:
                    match = None
            else:
                match = None

        db.session.commit()

    except Exception:
        db.session.rollback()
        app.logger.exception("Queue matching failed")
        return "خطا در ثبت جست‌وجوی حریف. دوباره تلاش کن.", 500

    if match:
        session.pop("queue_id", None)
        session["match_id"] = match.id
        session["player_slot"] = 2

        return redirect(url_for("match_view"))

    session["queue_id"] = entry.id
    session["queue_name"] = name
    session["queue_rubika"] = rubika_id
    session["queue_mode"] = mode
    session["queue_prize_level"] = prize_level

    session.pop("match_id", None)
    session.pop("player_slot", None)

    return redirect(url_for("waiting"))


@app.route("/waiting")
def waiting():
    if not session.get("queue_id"):
        if session.get("match_id"):
            return redirect(url_for("match_view"))

        return redirect(url_for("opponent"))

    return render_template("queue.html")


@app.route("/api/queue-status")
def queue_status():
    queue_id = session.get("queue_id")

    if not queue_id:
        match_id = session.get("match_id")

        if match_id:
            return jsonify({
                "matched": True,
                "redirect": url_for("match_view")
            })

        return jsonify({"matched": False})

    entry = db.session.get(QueueEntry, queue_id)

    # اگر رکورد صف حذف شده باشد، شناسه صف را در Match بررسی کن.
    match = get_active_match_for_queue(queue_id)

    if match:
        set_match_session(match, queue_id)

        return jsonify({
            "matched": True,
            "redirect": url_for("match_view")
        })

    if not entry:
        session.pop("queue_id", None)

        return jsonify({
            "matched": False,
            "error": "queue_entry_not_found"
        })

    try:
        with db.session.begin_nested():
            # اگر بازیکن دیگری هم‌سطح منتظر است، مسابقه بساز.
            candidate = (
                QueueEntry.query
                .filter(
                    QueueEntry.mode == entry.mode,
                    QueueEntry.prize_level == entry.prize_level,
                    QueueEntry.id != entry.id
                )
                .order_by(
                    QueueEntry.created_at.asc(),
                    QueueEntry.id.asc()
                )
                .with_for_update()
                .first()
            )

            if candidate:
                match = create_match_for_entries(entry, candidate)
                db.session.delete(candidate)
                db.session.delete(entry)
            else:
                match = None

        db.session.commit()

    except Exception:
        db.session.rollback()
        app.logger.exception("Queue status matching failed")

        return jsonify({
            "matched": False,
            "error": "matching_error"
        }), 500

    if match:
        set_match_session(match, queue_id)

        return jsonify({
            "matched": True,
            "redirect": url_for("match_view")
        })

    return jsonify({"matched": False})


@app.route("/cancel-queue", methods=["POST"])
def cancel_queue():
    queue_id = session.pop("queue_id", None)

    if queue_id:
        entry = db.session.get(QueueEntry, queue_id)

        if entry:
            db.session.delete(entry)
            db.session.commit()

    session.pop("queue_name", None)
    session.pop("queue_rubika", None)
    session.pop("queue_mode", None)
    session.pop("queue_prize_level", None)

    return redirect(url_for("index"))


# -------------------------
# Match pages and confirmation
# -------------------------

@app.route("/match")
def match_view():
    match_id = session.get("match_id")

    match = db.session.get(Match, match_id) if match_id else None

    if not match:
        session.pop("match_id", None)
        session.pop("player_slot", None)
        return redirect(url_for("opponent"))

    if match.status == "rejected":
        session.pop("match_id", None)
        session.pop("player_slot", None)
        return redirect(url_for("opponent"))

    slot = session.get("player_slot", 1)

    return render_template(
        "match.html",
        match=match,
        slot=slot
    )


@app.route("/api/match/<int:match_id>")
def match_status(match_id):
    match = db.session.get(Match, match_id)

    if not match or session.get("match_id") != match_id:
        return jsonify({"error": "not_found"}), 404

    return jsonify({
        "status": match.status,
        "player1_confirmed": match.player1_confirmed,
        "player2_confirmed": match.player2_confirmed
    })


@app.route("/match/<int:match_id>/confirm", methods=["POST"])
def confirm(match_id):
    match = db.session.get(Match, match_id)

    if not match or session.get("match_id") != match_id:
        abort(404)

    if match.status != "pending":
        return jsonify({
            "confirmed": match.status == "confirmed",
            "both": match.status == "confirmed"
        })

    slot = session.get("player_slot")

    if slot == 1:
        match.player1_confirmed = True
    elif slot == 2:
        match.player2_confirmed = True
    else:
        abort(403)

    if match.player1_confirmed and match.player2_confirmed:
        match.status = "confirmed"

    db.session.commit()

    return jsonify({
        "confirmed": True,
        "both": match.status == "confirmed"
    })


@app.route("/match/<int:match_id>/reject", methods=["POST"])
def reject(match_id):
    match = db.session.get(Match, match_id)

    if not match or session.get("match_id") != match_id:
        abort(404)

    match.status = "rejected"
    db.session.commit()

    session.pop("match_id", None)
    session.pop("player_slot", None)

    return redirect(url_for("index"))


# -------------------------
# Cups
# -------------------------

@app.route("/cups")
def cups():
    return render_template(
        "cups.html",
        cups=Cup.query.order_by(Cup.id.desc()).all()
    )


# -------------------------
# Admin login
# -------------------------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        admin = Admin.query.filter_by(username=username).first()

        if admin and check_password_hash(
            admin.password_hash, password
        ):
            session.clear()
            session["admin_id"] = admin.id

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
    admin_required()

    return render_template(
        "admin/dashboard.html",
        cups=Cup.query.order_by(Cup.id.desc()).all(),
        judges=Judge.query.order_by(Judge.id.desc()).all(),
        queue=QueueEntry.query.order_by(
            QueueEntry.created_at.desc()
        ).all()
    )


@app.route("/admin/cups/add", methods=["POST"])
def add_cup():
    admin_required()

    try:
        cup = Cup(
            name=request.form["name"],
            teams_count=int(request.form["teams_count"]),
            format_type=request.form["format_type"],
            prize=request.form["prize"],
            entry_fee=request.form["entry_fee"],
            organizer_rubika=request.form["organizer_rubika"],
            channel_rubika=request.form["channel_rubika"],
            status=request.form.get("status", "open")
        )

        db.session.add(cup)
        db.session.commit()

    except (ValueError, KeyError):
        db.session.rollback()
        return "اطلاعات کاپ نامعتبر است.", 400

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/cups/<int:cup_id>/delete", methods=["POST"])
def delete_cup(cup_id):
    admin_required()

    cup = db.session.get(Cup, cup_id)

    if cup:
        db.session.delete(cup)
        db.session.commit()

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/judges/add", methods=["POST"])
def add_judge():
    admin_required()

    name = request.form.get("name", "").strip()
    rubika_id = request.form.get("rubika_id", "").strip()

    if not name or not rubika_id:
        return redirect(url_for("admin_dashboard"))

    judge = Judge(
        name=name,
        rubika_id=rubika_id,
        active=True
    )

    db.session.add(judge)
    db.session.commit()

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/judges/<int:judge_id>/toggle", methods=["POST"])
def toggle_judge(judge_id):
    admin_required()

    judge = db.session.get(Judge, judge_id)

    if judge:
        judge.active = not judge.active
        db.session.commit()

    return redirect(url_for("admin_dashboard"))


# -------------------------
# Admin initialization
# -------------------------

def create_admin_from_env():
    if Admin.query.first():
        return

    username = os.environ.get("ADMIN_USERNAME")
    password = os.environ.get("ADMIN_PASSWORD")

    if not username or not password:
        app.logger.warning(
            "Admin was not created: set ADMIN_USERNAME and ADMIN_PASSWORD."
        )
        return

    db.session.add(
        Admin(
            username=username,
            password_hash=generate_password_hash(password)
        )
    )

    db.session.commit()


with app.app_context():
    db.create_all()
    create_admin_from_env()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        debug=False
    )        
