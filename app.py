import os
import secrets
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

database_url = os.environ.get("DATABASE_URL", "sqlite:///efootball_yar.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

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
    mode = db.Column(db.String(30), nullable=False)  # friendly / prize
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
    judge_id = db.Column(db.Integer, db.ForeignKey("judge.id"), nullable=True)
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

def admin_required():
    if not session.get("admin_id"):
        abort(403)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/opponent")
def opponent():
    return render_template("opponent.html")

@app.route("/queue", methods=["POST"])
def queue():
    name = request.form.get("name", "").strip()
    rubika_id = request.form.get("rubika_id", "").strip()
    mode = request.form.get("mode", "").strip()
    prize_level = request.form.get("prize_level", "").strip() or None
    if not name or not rubika_id or mode not in {"friendly", "prize"}:
        return redirect(url_for("opponent"))
    if mode == "prize" and prize_level not in {"light", "ideal", "heavy"}:
        return redirect(url_for("opponent"))

    candidate = QueueEntry.query.filter_by(mode=mode, prize_level=prize_level).order_by(QueueEntry.created_at.asc()).first()
    if candidate:
        judge = Judge.query.filter_by(active=True).order_by(Judge.id.asc()).first() if mode == "prize" else None
        match = Match(
            player1_name=candidate.name, player1_rubika=candidate.rubika_id,
            player2_name=name, player2_rubika=rubika_id,
            mode=mode, prize_level=prize_level,
            judge_id=judge.id if judge else None
        )
        db.session.delete(candidate)
        db.session.add(match)
        db.session.commit()
        session["match_id"] = match.id
        session["player_slot"] = 2
        return redirect(url_for("match_view"))
    entry = QueueEntry(name=name, rubika_id=rubika_id, mode=mode, prize_level=prize_level)
    db.session.add(entry)
    db.session.commit()
    session["queue_id"] = entry.id
    return redirect(url_for("waiting"))

@app.route("/waiting")
def waiting():
    if not session.get("queue_id"):
        return redirect(url_for("opponent"))
    return render_template("queue.html")

@app.route("/api/queue-status")
def queue_status():
    qid = session.get("queue_id")
    if not qid:
        return jsonify({"matched": False})
    entry = db.session.get(QueueEntry, qid)
    if not entry:
        return jsonify({"matched": False})
    candidate = QueueEntry.query.filter(
        QueueEntry.mode == entry.mode,
        QueueEntry.prize_level == entry.prize_level,
        QueueEntry.id != entry.id
    ).order_by(QueueEntry.created_at.asc()).first()
    if candidate:
        judge = Judge.query.filter_by(active=True).order_by(Judge.id.asc()).first() if entry.mode == "prize" else None
        match = Match(
            player1_name=entry.name, player1_rubika=entry.rubika_id,
            player2_name=candidate.name, player2_rubika=candidate.rubika_id,
            mode=entry.mode, prize_level=entry.prize_level,
            judge_id=judge.id if judge else None
        )
        db.session.delete(entry); db.session.delete(candidate); db.session.add(match); db.session.commit()
        session.pop("queue_id", None); session["match_id"] = match.id
        session["player_slot"] = 1
        return jsonify({"matched": True, "redirect": url_for("match_view")})
    return jsonify({"matched": False})

@app.route("/cancel-queue", methods=["POST"])
def cancel_queue():
    qid = session.pop("queue_id", None)
    if qid:
        entry = db.session.get(QueueEntry, qid)
        if entry:
            db.session.delete(entry); db.session.commit()
    return redirect(url_for("index"))

@app.route("/match")
def match_view():
    mid = session.get("match_id")
    match = db.session.get(Match, mid) if mid else None
    if not match: return redirect(url_for("opponent"))
    slot = session.get("player_slot", 1)
    return render_template("match.html", match=match, slot=slot)

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
    slot = session.get("player_slot")
    if slot == 1: match.player1_confirmed = True
    elif slot == 2: match.player2_confirmed = True
    else: abort(403)
    if match.player1_confirmed and match.player2_confirmed:
        match.status = "confirmed"
    db.session.commit()
    return jsonify({"confirmed": True, "both": match.status == "confirmed"})

@app.route("/match/<int:match_id>/reject", methods=["POST"])
def reject(match_id):
    match = db.session.get(Match, match_id)
    if not match or session.get("match_id") != match_id:
        abort(404)
    match.status = "rejected"
    db.session.commit()
    session.pop("match_id", None); session.pop("player_slot", None)
    return redirect(url_for("index"))

@app.route("/cups")
def cups():
    return render_template("cups.html", cups=Cup.query.order_by(Cup.id.desc()).all())

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        admin = Admin.query.filter_by(username=username).first()
        if admin and check_password_hash(admin.password_hash, password):
            session["admin_id"] = admin.id
            return redirect(url_for("admin_dashboard"))
        return render_template("admin/login.html", error="نام کاربری یا رمز عبور اشتباه است.")
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
        queue=QueueEntry.query.order_by(QueueEntry.created_at.desc()).all()
    )

@app.route("/admin/cups/add", methods=["POST"])
def add_cup():
    admin_required()
    cup = Cup(
        name=request.form["name"], teams_count=int(request.form["teams_count"]),
        format_type=request.form["format_type"], prize=request.form["prize"],
        entry_fee=request.form["entry_fee"], organizer_rubika=request.form["organizer_rubika"],
        channel_rubika=request.form["channel_rubika"], status=request.form["status"]
    )
    db.session.add(cup); db.session.commit()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/cups/<int:cup_id>/delete", methods=["POST"])
def delete_cup(cup_id):
    admin_required()
    cup = db.session.get(Cup, cup_id)
    if cup: db.session.delete(cup); db.session.commit()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/judges/add", methods=["POST"])
def add_judge():
    admin_required()
    db.session.add(Judge(name=request.form["name"], rubika_id=request.form["rubika_id"], active=True))
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

@app.route("/admin/setup")
def admin_setup():
    if Admin.query.first():
        return "مدیر قبلاً ساخته شده است.", 403
    username = request.args.get("username", "admin")
    password = request.args.get("password")
    if not password:
        return "برای ساخت مدیر، ?password=YOUR_PASSWORD را اضافه کنید.", 400
    db.session.add(Admin(username=username, password_hash=generate_password_hash(password)))
    db.session.commit()
    return "مدیر ساخته شد. این آدرس را دیگر استفاده نکنید و سپس /admin بروید."

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=False)
