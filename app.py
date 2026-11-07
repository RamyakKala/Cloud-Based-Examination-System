import csv
import io
import json
import logging.config
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from openpyxl import Workbook, load_workbook
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app_environment = os.environ.get("APP_ENV", "development").strip().lower()
secret_key = os.environ.get("SECRET_KEY")
if not secret_key and app_environment == "production":
    raise RuntimeError("SECRET_KEY must be configured when APP_ENV=production.")
app.config["SECRET_KEY"] = secret_key or os.urandom(32)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
app.config["WTF_CSRF_TIME_LIMIT"] = None
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=app_environment == "production",
)

VIT_BRANCHES = (
    "Artificial Intelligence & Data Science",
    "Chemical Engineering",
    "Civil Engineering",
    "Computer Engineering",
    "Computer Engineering (Software Engineering)",
    "Computer Sciences & Engineering (AI)",
    "Computer Science and Engineering (AI & ML)",
    "Computer Science and Engineering (Data Science)",
    "Computer Science & Engineering (IoT and Cyber Security Including Blockchain Technology)",
    "Electronics and Telecommunication Engineering",
    "Engineering, Science & Humanities",
    "Information Technology",
    "Instrumentation Engineering",
    "Mechanical Engineering",
)
DIVISIONS = ("A", "B", "C", "D", "E", "F", "G", "H", "SEDA")
BRANCH_LOOKUP = {branch.casefold(): branch for branch in VIT_BRANCHES}
DIVISION_LOOKUP = {division.casefold(): division for division in DIVISIONS}

logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "formatter": "standard",
            }
        },
        "root": {
            "handlers": ["console"],
            "level": os.environ.get("LOG_LEVEL", "INFO").upper(),
        },
    }
)

@app.context_processor
def inject_common():
    return {
        "IST": IST,
        "VIT_BRANCHES": VIT_BRANCHES,
        "DIVISIONS": DIVISIONS,
    }

# Cloud-ready database:
# Local: SQLite database file
# Cloud: set DATABASE_URL to PostgreSQL/MySQL connection URL.
database_url = os.environ.get("DATABASE_URL", "sqlite:///exam_system.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

csrf = CSRFProtect(app)
db = SQLAlchemy(app)
IST = ZoneInfo("Asia/Kolkata")
IMPORT_REQUIRED_COLUMNS = [
    "question",
    "option_a",
    "option_b",
    "option_c",
    "option_d",
    "correct_option",
]
MAX_UPLOAD_BYTES = 2 * 1024 * 1024


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # student / teacher
    branch = db.Column(db.String(150), nullable=True)
    division = db.Column(db.String(80), nullable=True)

    attempts = db.relationship("Attempt", backref="student", cascade="all, delete-orphan")


class Exam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    start_at = db.Column(db.DateTime, nullable=True)
    end_at = db.Column(db.DateTime, nullable=True)
    duration_minutes = db.Column(db.Integer, nullable=False, default=30)
    target_branch = db.Column(db.String(150), nullable=True)
    target_division = db.Column(db.String(80), nullable=True)
    questions = db.relationship("Question", backref="exam", cascade="all, delete-orphan")
    attempts = db.relationship("Attempt", backref="exam", cascade="all, delete-orphan")


class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exam.id"), nullable=False)
    text = db.Column(db.Text, nullable=False)
    option_a = db.Column(db.String(500), nullable=False)
    option_b = db.Column(db.String(500), nullable=False)
    option_c = db.Column(db.String(500), nullable=False)
    option_d = db.Column(db.String(500), nullable=False)
    correct_option = db.Column(db.String(1), nullable=False)

    student_answers = db.relationship("StudentAnswer", backref="question", cascade="all, delete-orphan")


class Attempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exam.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    score = db.Column(db.Integer, nullable=False, default=0)
    total = db.Column(db.Integer, nullable=False, default=0)
    started_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="in_progress")  # in_progress, submitted, expired
    deadline_at = db.Column(db.DateTime, nullable=True)
    submitted_at = db.Column(db.DateTime, nullable=True)
    question_order = db.Column(db.Text, nullable=True)

    answers = db.relationship("StudentAnswer", backref="attempt", cascade="all, delete-orphan")


class StudentAnswer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey("attempt.id"), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey("question.id"), nullable=False)
    selected_option = db.Column(db.String(1), nullable=True)
    is_correct = db.Column(db.Boolean, nullable=False, default=False)
    __table_args__ = (
        db.UniqueConstraint("attempt_id", "question_id", name="uq_student_answer_attempt_question"),
    )


def add_missing_columns():
    inspector = db.inspect(db.engine)
    if not inspector.has_table("exam"):
        return
    exam_columns = {column["name"] for column in inspector.get_columns("exam")}
    if "start_at" not in exam_columns:
        db.session.execute(text('ALTER TABLE "exam" ADD COLUMN start_at TIMESTAMP'))
    if "end_at" not in exam_columns:
        db.session.execute(text('ALTER TABLE "exam" ADD COLUMN end_at TIMESTAMP'))
    if "duration_minutes" not in exam_columns:
        db.session.execute(text('ALTER TABLE "exam" ADD COLUMN duration_minutes INTEGER DEFAULT 30 NOT NULL'))
    if "target_branch" not in exam_columns:
        db.session.execute(text('ALTER TABLE "exam" ADD COLUMN target_branch VARCHAR(150)'))
    elif db.engine.dialect.name == "postgresql":
        target_branch_column = next(
            column for column in inspector.get_columns("exam")
            if column["name"] == "target_branch"
        )
        if getattr(target_branch_column["type"], "length", None) != 150:
            db.session.execute(text('ALTER TABLE "exam" ALTER COLUMN target_branch TYPE VARCHAR(150)'))
    if "target_division" not in exam_columns:
        db.session.execute(text('ALTER TABLE "exam" ADD COLUMN target_division VARCHAR(80)'))

    if inspector.has_table("user"):
        user_columns = {column["name"] for column in inspector.get_columns("user")}
        if "branch" not in user_columns:
            db.session.execute(text('ALTER TABLE "user" ADD COLUMN branch VARCHAR(150)'))
        elif db.engine.dialect.name == "postgresql":
            branch_column = next(
                column for column in inspector.get_columns("user")
                if column["name"] == "branch"
            )
            if getattr(branch_column["type"], "length", None) != 150:
                db.session.execute(text('ALTER TABLE "user" ALTER COLUMN branch TYPE VARCHAR(150)'))
        if "division" not in user_columns:
            db.session.execute(text('ALTER TABLE "user" ADD COLUMN division VARCHAR(80)'))

    if inspector.has_table("attempt"):
        attempt_columns = {column["name"] for column in inspector.get_columns("attempt")}
        if "started_at" not in attempt_columns:
            db.session.execute(text('ALTER TABLE "attempt" ADD COLUMN started_at TIMESTAMP'))
        if "status" not in attempt_columns:
            db.session.execute(text("ALTER TABLE \"attempt\" ADD COLUMN status VARCHAR(20) DEFAULT 'in_progress' NOT NULL"))
        if "deadline_at" not in attempt_columns:
            db.session.execute(text('ALTER TABLE "attempt" ADD COLUMN deadline_at TIMESTAMP'))
        if "submitted_at" not in attempt_columns:
            db.session.execute(text('ALTER TABLE "attempt" ADD COLUMN submitted_at TIMESTAMP'))
        if "question_order" not in attempt_columns:
            db.session.execute(text('ALTER TABLE "attempt" ADD COLUMN question_order TEXT'))

    if not inspector.has_table("student_answer"):
        StudentAnswer.__table__.create(bind=db.engine)
    db.session.commit()


def get_now_utc():
    return datetime.now(timezone.utc)


def as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_aware_datetime(raw_value, assume_timezone=IST):
    if raw_value in (None, ""):
        return None
    if isinstance(raw_value, datetime):
        dt = raw_value
    else:
        dt = datetime.fromisoformat(raw_value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=assume_timezone)
    return dt.astimezone(timezone.utc)


def format_ist(value):
    if value is None:
        return "Not scheduled"
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime("%d %b %Y, %H:%M IST")


def exam_status(exam, now=None):
    now = now or get_now_utc()
    if exam.start_at and now < as_utc(exam.start_at):
        return "upcoming"
    if exam.end_at and now >= as_utc(exam.end_at):
        return "closed"
    return "available"


def get_effective_deadline(exam, attempt=None, now=None):
    now = now or get_now_utc()
    deadline_candidates = []
    if exam.end_at:
        deadline_candidates.append(as_utc(exam.end_at))

    if attempt and attempt.started_at:
        duration_delta = timedelta(minutes=max(int(exam.duration_minutes or 0), 0))
        deadline_candidates.append(as_utc(attempt.started_at) + duration_delta)
    elif exam.duration_minutes:
        deadline_candidates.append(now + timedelta(minutes=max(int(exam.duration_minutes), 0)))

    return min(deadline_candidates) if deadline_candidates else None


def get_student_attempt(exam_id, student_id, lock=False):
    query = Attempt.query.filter_by(exam_id=exam_id, student_id=student_id)
    if lock:
        query = query.with_for_update()
    return (
        query.order_by(Attempt.id.desc()).first()
    )


def answer_map_for_attempt(attempt_id):
    answers = StudentAnswer.query.filter_by(attempt_id=attempt_id).all()
    return {answer.question_id: answer for answer in answers}


def normalize_class_value(value):
    return " ".join((value or "").split()).casefold()


def canonical_branch(value):
    return BRANCH_LOOKUP.get(normalize_class_value(value))


def canonical_division(value):
    return DIVISION_LOOKUP.get(normalize_class_value(value))


def exam_matches_student(exam, student):
    if exam.target_branch is None and exam.target_division is None:
        return True
    return (
        normalize_class_value(exam.target_branch)
        == normalize_class_value(student.branch)
        and normalize_class_value(exam.target_division)
        == normalize_class_value(student.division)
        and bool(normalize_class_value(student.branch))
        and bool(normalize_class_value(student.division))
    )


def get_attempt_questions(attempt, exam):
    questions_by_id = {question.id: question for question in exam.questions}
    try:
        stored_order = json.loads(attempt.question_order) if attempt.question_order else None
    except (TypeError, json.JSONDecodeError):
        stored_order = None

    if isinstance(stored_order, list):
        ordered_ids = []
        seen_ids = set()
        for question_id in stored_order:
            if (
                isinstance(question_id, int)
                and question_id in questions_by_id
                and question_id not in seen_ids
            ):
                ordered_ids.append(question_id)
                seen_ids.add(question_id)
    else:
        ordered_ids = []

    if not ordered_ids and questions_by_id and not attempt.question_order:
        ordered_ids = list(questions_by_id)
        random.SystemRandom().shuffle(ordered_ids)
        attempt.question_order = json.dumps(ordered_ids)
        db.session.commit()
    elif attempt.question_order is not None and ordered_ids != stored_order:
        attempt.question_order = json.dumps(ordered_ids)
        db.session.commit()
    elif attempt.question_order is None and not ordered_ids:
        attempt.question_order = "[]"
        db.session.commit()

    return [questions_by_id[question_id] for question_id in ordered_ids]


def student_is_eligible_for_exam(exam, student_id):
    student = db.session.get(User, student_id)
    if student is None or student.role != "student" or not exam_matches_student(exam, student):
        return False, "This exam is assigned to a different branch and division."
    return True, None


def student_can_access_exam(exam, student_id=None):
    now = get_now_utc()
    if student_id is not None:
        eligible, reason = student_is_eligible_for_exam(exam, student_id)
        if not eligible:
            return False, reason
    if exam.start_at and now < as_utc(exam.start_at):
        return False, "This exam has not opened yet."
    if exam.end_at and now >= as_utc(exam.end_at):
        return False, "This exam is closed."
    return True, None


def persist_student_answer(attempt, question_id, selected_option, commit=True):
    question = db.session.get(Question, question_id)
    if not question or question.exam_id != attempt.exam_id:
        return False
    exam = db.session.get(Exam, attempt.exam_id)
    if not exam or question_id not in {item.id for item in get_attempt_questions(attempt, exam)}:
        return False

    normalized = (selected_option or "").strip().upper()
    if normalized not in {"A", "B", "C", "D"}:
        return False

    if attempt.status != "in_progress":
        return False

    existing = StudentAnswer.query.filter_by(attempt_id=attempt.id, question_id=question.id).first()
    if existing is None:
        db.session.add(
            StudentAnswer(
                attempt_id=attempt.id,
                question_id=question.id,
                selected_option=normalized,
                is_correct=(normalized == question.correct_option),
            )
        )
    else:
        existing.selected_option = normalized
        existing.is_correct = normalized == question.correct_option
    if commit:
        db.session.commit()
    return True


def save_selected_answers_from_form(attempt, form_data):
    for key, value in form_data.items():
        if not key.startswith("question_"):
            continue
        try:
            question_id = int(key.split("_", 1)[1])
        except (TypeError, ValueError):
            continue
        if value not in (None, ""):
            persist_student_answer(attempt, question_id, value, commit=False)


def finalize_attempt(attempt, exam, submitted_answers=None, now=None):
    """Persist permitted final answers and score the saved attempt atomically."""
    if attempt.status == "submitted":
        return False

    now = now or get_now_utc()
    deadline = get_effective_deadline(exam, attempt=attempt)
    timed_out = deadline is not None and now >= deadline

    # Late requests may finalize an attempt, but cannot change its saved answers.
    if not timed_out and submitted_answers is not None:
        save_selected_answers_from_form(attempt, submitted_answers)

    answer_rows = StudentAnswer.query.filter_by(attempt_id=attempt.id).all()
    ordered_questions = get_attempt_questions(attempt, exam)
    questions = {question.id: question for question in ordered_questions}
    score = 0
    for answer in answer_rows:
        question = questions.get(answer.question_id)
        if question is None:
            continue
        answer.is_correct = (
            answer.selected_option is not None
            and answer.selected_option == question.correct_option
        )
        score += int(answer.is_correct)

    attempt.score = score
    attempt.total = len(ordered_questions)
    attempt.status = "submitted"
    attempt.submitted_at = now
    db.session.commit()
    return timed_out


def render_attempt_result(attempt, exam, timed_out=False):
    return render_template(
        "result.html",
        exam=exam,
        score=attempt.score,
        total=attempt.total,
        timed_out=timed_out,
        submitted_at=attempt.submitted_at,
    )


def attempt_expired(exam, attempt):
    deadline = get_effective_deadline(exam, attempt=attempt)
    return deadline is not None and as_utc(attempt.submitted_at) >= deadline if attempt.submitted_at else False


def delete_attempt_records(attempts):
    for attempt in attempts:
        StudentAnswer.query.filter_by(attempt_id=attempt.id).delete()
        db.session.delete(attempt)
    db.session.commit()


def login_required(role=None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in first.", "warning")
                return redirect(url_for("login"))
            if role and session.get("role") != role:
                flash("You are not authorized to access this page.", "danger")
                return redirect(url_for("index"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator


@app.route("/")
def index():
    if session.get("role") == "teacher":
        return redirect(url_for("teacher_dashboard"))
    if session.get("role") == "student":
        return redirect(url_for("student_dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        role = request.form["role"]
        raw_branch = request.form.get("branch", "")
        raw_division = request.form.get("division", "")
        branch = canonical_branch(raw_branch)
        division = canonical_division(raw_division)

        if role not in ("student", "teacher"):
            flash("Invalid role.", "danger")
            return redirect(url_for("register"))
        if role == "student" and (branch is None or division is None):
            flash("Select a valid branch and division from the available lists.", "danger")
            return redirect(url_for("register"))

        if User.query.filter_by(email=email).first():
            flash("Email is already registered.", "danger")
            return redirect(url_for("register"))

        user = User(
            name=name,
            email=email,
            password_hash=generate_password_hash(password),
            role=role,
            branch=branch if role == "student" else None,
            division=division if role == "student" else None,
        )
        db.session.add(user)
        db.session.commit()
        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password_hash, password):
            session.clear()
            session["user_id"] = user.id
            session["name"] = user.name
            session["role"] = user.role
            return redirect(url_for("index"))

        flash("Invalid email or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("index"))


@app.route("/account", methods=["GET", "POST"])
@login_required()
def account_settings():
    user = db.session.get(User, session["user_id"])
    if user is None:
        session.clear()
        flash("Your account could not be found. Please log in again.", "danger")
        return redirect(url_for("login"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        raw_branch = request.form.get("branch", "")
        raw_division = request.form.get("division", "")
        branch = canonical_branch(raw_branch)
        division = canonical_division(raw_division)
        if branch is None and normalize_class_value(raw_branch) == normalize_class_value(user.branch):
            branch = user.branch
        if division is None and normalize_class_value(raw_division) == normalize_class_value(user.division):
            division = user.division
        if not name:
            flash("Name is required.", "danger")
            return redirect(url_for("account_settings"))
        if len(name) > 120:
            flash("Name must be 120 characters or fewer.", "danger")
            return redirect(url_for("account_settings"))
        if user.role == "student" and (branch is None or division is None):
            flash("Select a valid branch and division from the available lists.", "danger")
            return redirect(url_for("account_settings"))
        if new_password:
            if not check_password_hash(user.password_hash, current_password):
                flash("Enter your current password to change your password.", "danger")
                return redirect(url_for("account_settings"))
            if len(new_password) < 8:
                flash("New password must be at least 8 characters.", "danger")
                return redirect(url_for("account_settings"))
            if new_password != confirm_password:
                flash("New password and confirmation do not match.", "danger")
                return redirect(url_for("account_settings"))
            user.password_hash = generate_password_hash(new_password)

        user.name = name
        if user.role == "student":
            user.branch = branch
            user.division = division
        db.session.commit()
        session["name"] = user.name
        flash("Account settings updated.", "success")
        return redirect(url_for("account_settings"))

    return render_template("account_settings.html", user=user)


@app.route("/student/dashboard")
@login_required("student")
def student_dashboard():
    student = db.session.get(User, session["user_id"])
    if student is None:
        session.clear()
        flash("Your account could not be found. Please log in again.", "danger")
        return redirect(url_for("login"))
    all_exams = [
        exam for exam in Exam.query.order_by(Exam.id.desc()).all()
        if exam_matches_student(exam, student)
    ]
    attempts = [
        attempt for attempt in (
            Attempt.query.filter_by(student_id=session["user_id"])
            .filter(Attempt.status == "submitted")
            .order_by(Attempt.submitted_at.desc(), Attempt.id.desc())
            .all()
        )
        if exam_matches_student(attempt.exam, student)
    ]
    matching_active_attempts = [
        attempt for attempt in (
            Attempt.query.filter_by(student_id=session["user_id"], status="in_progress")
            .order_by(Attempt.started_at.desc(), Attempt.id.desc())
            .all()
        )
        if exam_matches_student(attempt.exam, student)
    ]
    now = get_now_utc()
    exam_status_map = {exam.id: exam_status(exam, now) for exam in all_exams}
    attempt_exam_ids = {attempt.exam_id for attempt in attempts}
    active_attempts = [
        attempt for attempt in matching_active_attempts
        if get_effective_deadline(attempt.exam, attempt=attempt) is None
        or now < get_effective_deadline(attempt.exam, attempt=attempt)
    ]
    pending_result_attempts = [
        attempt for attempt in matching_active_attempts
        if get_effective_deadline(attempt.exam, attempt=attempt) is not None
        and now >= get_effective_deadline(attempt.exam, attempt=attempt)
    ]
    active_attempt_exam_ids = {attempt.exam_id for attempt in active_attempts}
    search = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "all")
    valid_statuses = {"all", "upcoming", "available", "in_progress", "completed", "closed"}
    if status_filter not in valid_statuses:
        status_filter = "all"
    exams = [
        exam for exam in all_exams
        if (not search or search.casefold() in exam.title.casefold())
        and (
            status_filter == "all"
            or (
                status_filter == "in_progress"
                and exam.id in active_attempt_exam_ids
            )
            or (
                status_filter == "completed"
                and exam.id in attempt_exam_ids
            )
            or (
                status_filter in {"upcoming", "available", "closed"}
                and exam_status_map.get(exam.id) == status_filter
                and exam.id not in attempt_exam_ids
                and exam.id not in active_attempt_exam_ids
            )
        )
    ]
    reminders = [
        exam for exam in all_exams
        if exam.start_at
        and now <= as_utc(exam.start_at) <= now + timedelta(hours=24)
        and exam_status_map.get(exam.id) == "upcoming"
        and exam.id not in attempt_exam_ids
        and exam.id not in active_attempt_exam_ids
    ]
    return render_template(
        "student_dashboard.html",
        student=student,
        exams=exams,
        all_exams=all_exams,
        attempts=attempts,
        active_attempts=active_attempts,
        pending_result_attempts=pending_result_attempts,
        attempt_exam_ids=attempt_exam_ids,
        active_attempt_exam_ids=active_attempt_exam_ids,
        exam_status_map=exam_status_map,
        reminders=reminders,
        search=search,
        status_filter=status_filter,
        dashboard_counts={
            "upcoming": sum(
                value == "upcoming"
                and exam_id not in attempt_exam_ids
                and exam_id not in active_attempt_exam_ids
                for exam_id, value in exam_status_map.items()
            ),
            "in_progress": len(active_attempts),
            "completed": len(attempt_exam_ids),
            "pending_results": len(pending_result_attempts),
        },
    )


@app.route("/student/result/<int:attempt_id>")
@login_required("student")
def student_result(attempt_id):
    attempt = db.session.get(Attempt, attempt_id)
    if not attempt or attempt.student_id != session["user_id"]:
        flash("Result not found.", "danger")
        return redirect(url_for("student_dashboard"))
    exam = db.session.get(Exam, attempt.exam_id)
    allowed, reason = student_is_eligible_for_exam(exam, session["user_id"])
    if not allowed:
        flash(reason, "danger")
        return redirect(url_for("student_dashboard"))
    if attempt.status != "submitted":
        deadline = get_effective_deadline(exam, attempt=attempt)
        if deadline is None or get_now_utc() < deadline:
            flash("This attempt is still in progress.", "warning")
            return redirect(url_for("take_exam", exam_id=exam.id))
        try:
            finalize_attempt(attempt, exam, now=get_now_utc())
        except SQLAlchemyError:
            db.session.rollback()
            app.logger.exception("Could not finalize expired attempt %s", attempt.id)
            return "Your saved answers are safe, but the result could not be finalized. Please retry.", 503

    answer_map = answer_map_for_attempt(attempt.id)
    ordered_questions = get_attempt_questions(attempt, exam)
    return render_template(
        "student_result.html",
        attempt=attempt,
        exam=exam,
        answer_map=answer_map,
        ordered_questions=ordered_questions,
    )


@app.route("/exam/<int:exam_id>")
@login_required("student")
def take_exam(exam_id):
    exam = db.session.get(Exam, exam_id)
    if not exam:
        flash("Exam not found.", "danger")
        return redirect(url_for("student_dashboard"))

    allowed, reason = student_is_eligible_for_exam(exam, session["user_id"])
    if not allowed:
        flash(reason, "danger")
        return redirect(url_for("student_dashboard"))

    attempt = get_student_attempt(exam_id, session["user_id"])
    if attempt and attempt.status == "submitted":
        return render_attempt_result(attempt, exam, timed_out=attempt_expired(exam, attempt))

    if attempt is not None:
        deadline = get_effective_deadline(exam, attempt=attempt)
        if deadline is not None and get_now_utc() >= deadline:
            try:
                timed_out = finalize_attempt(attempt, exam, now=get_now_utc())
            except SQLAlchemyError:
                db.session.rollback()
                app.logger.exception("Could not finalize expired attempt %s", attempt.id)
                return "Your saved answers are safe, but the result could not be finalized. Please refresh to retry.", 503
            return render_attempt_result(attempt, exam, timed_out=timed_out)
    else:
        allowed, reason = student_can_access_exam(exam, session["user_id"])
        if not allowed:
            flash(reason, "warning")
            return redirect(url_for("student_dashboard"))
        if not exam.questions:
            flash("This exam is not ready yet. Please check back after questions are added.", "warning")
            return redirect(url_for("student_dashboard"))

    if attempt is None:
        ordered_questions = list(exam.questions)
        question_ids = [question.id for question in ordered_questions]
        random.SystemRandom().shuffle(question_ids)
        attempt = Attempt(
            exam_id=exam.id,
            student_id=session["user_id"],
            score=0,
            total=len(question_ids),
            started_at=get_now_utc(),
            status="in_progress",
            question_order=json.dumps(question_ids),
        )
        attempt.deadline_at = get_effective_deadline(exam, attempt=attempt)
        db.session.add(attempt)
        db.session.commit()
    elif attempt.status == "in_progress" and not attempt.started_at:
        attempt.started_at = get_now_utc()
        attempt.deadline_at = get_effective_deadline(exam, attempt=attempt)
        db.session.commit()

    deadline = get_effective_deadline(exam, attempt=attempt)

    remaining_seconds = int(max((deadline - get_now_utc()).total_seconds(), 0)) if deadline else 0
    answer_map = answer_map_for_attempt(attempt.id)
    ordered_questions = get_attempt_questions(attempt, exam)
    return render_template(
        "take_exam.html",
        exam=exam,
        ordered_questions=ordered_questions,
        attempt=attempt,
        answer_map=answer_map,
        deadline=deadline,
        remaining_seconds=remaining_seconds,
    )


@app.route("/exam/<int:exam_id>/save-answer", methods=["POST"])
@login_required("student")
def save_answer(exam_id):
    exam = db.session.get(Exam, exam_id)
    if not exam:
        return jsonify({"error": "Exam not found"}), 404

    attempt = get_student_attempt(exam_id, session["user_id"], lock=True)
    if not attempt or attempt.status != "in_progress":
        return jsonify({"error": "No active exam attempt."}), 409
    allowed, reason = student_is_eligible_for_exam(exam, session["user_id"])
    if not allowed:
        return jsonify({"error": reason}), 403

    deadline = get_effective_deadline(exam, attempt=attempt)
    if deadline is not None and get_now_utc() >= deadline:
        try:
            finalize_attempt(attempt, exam, now=get_now_utc())
        except SQLAlchemyError:
            db.session.rollback()
            app.logger.exception("Could not finalize expired attempt %s after answer save", attempt.id)
            return jsonify({"error": "The answer was not changed; result finalization will retry."}), 503
        return jsonify({"status": "submitted", "timed_out": True}), 409

    question_id = request.form.get("question_id")
    selected_option = request.form.get("selected_option")
    if question_id is None or selected_option is None:
        return jsonify({"error": "Missing answer payload."}), 400

    try:
        question_id = int(question_id)
    except ValueError:
        return jsonify({"error": "Invalid question id."}), 400

    if not persist_student_answer(attempt, question_id, selected_option):
        return jsonify({"error": "Answer could not be saved."}), 400

    return jsonify({"status": "saved"})


@app.route("/exam/<int:exam_id>/submit", methods=["POST"])
@login_required("student")
def submit_exam(exam_id):
    exam = db.session.get(Exam, exam_id)
    if not exam:
        flash("Exam not found.", "danger")
        return redirect(url_for("student_dashboard"))

    allowed, reason = student_is_eligible_for_exam(exam, session["user_id"])
    if not allowed:
        flash(reason, "danger")
        return redirect(url_for("student_dashboard"))

    attempt = get_student_attempt(exam_id, session["user_id"], lock=True)
    if not attempt:
        flash("No active attempt was found for this exam.", "warning")
        return redirect(url_for("student_dashboard"))

    try:
        if attempt.status == "submitted":
            return render_attempt_result(attempt, exam, timed_out=attempt_expired(exam, attempt))

        timed_out = finalize_attempt(
            attempt,
            exam,
            submitted_answers=request.form,
            now=get_now_utc(),
        )
        return render_attempt_result(attempt, exam, timed_out=timed_out)
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Could not submit attempt %s", attempt.id)
        flash("Submission could not be finalized. Your saved answers remain intact; retry now.", "danger")
        return redirect(url_for("take_exam", exam_id=exam.id))


@app.route("/teacher/dashboard")
@login_required("teacher")
def teacher_dashboard():
    all_exams = Exam.query.filter_by(teacher_id=session["user_id"]).order_by(Exam.id.desc()).all()
    now = get_now_utc()
    exam_status_map = {exam.id: exam_status(exam, now) for exam in all_exams}
    search = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "all")
    valid_statuses = {"all", "upcoming", "available", "closed", "unscheduled"}
    if status_filter not in valid_statuses:
        status_filter = "all"
    exams = [
        exam for exam in all_exams
        if (not search or search.casefold() in exam.title.casefold())
        and (
            status_filter == "all"
            or (
                status_filter == "unscheduled"
                and not exam.start_at and not exam.end_at
            )
            or exam_status_map.get(exam.id) == status_filter
        )
    ]
    active_attempts = Attempt.query.join(Exam).filter(
        Exam.teacher_id == session["user_id"], Attempt.status == "in_progress"
    ).all()
    pending_results = [
        attempt for attempt in active_attempts
        if get_effective_deadline(attempt.exam, attempt=attempt) is not None
        and now >= get_effective_deadline(attempt.exam, attempt=attempt)
    ]
    return render_template(
        "teacher_dashboard.html",
        exams=exams,
        all_exams=all_exams,
        exam_status_map=exam_status_map,
        search=search,
        status_filter=status_filter,
        dashboard_counts={
            "upcoming": sum(value == "upcoming" for value in exam_status_map.values()),
            "in_progress": sum(value == "available" for value in exam_status_map.values()),
            "completed": sum(value == "closed" for value in exam_status_map.values()),
            "pending_results": len(pending_results),
        },
    )


@app.route("/teacher/exam/create", methods=["GET", "POST"])
@login_required("teacher")
def create_exam():
    if request.method == "POST":
        title = request.form["title"].strip()
        description = request.form["description"].strip()
        duration_minutes = request.form.get("duration_minutes", "0")
        start_raw = request.form.get("start_at")
        end_raw = request.form.get("end_at")
        target_branch = canonical_branch(request.form.get("target_branch", ""))
        target_division = canonical_division(request.form.get("target_division", ""))

        if not title:
            flash("Exam title is required.", "danger")
            return redirect(url_for("create_exam"))
        if target_branch is None or target_division is None:
            flash("Select a valid assigned branch and division from the available lists.", "danger")
            return redirect(url_for("create_exam"))

        duration_minutes = int(duration_minutes or 0)
        start_at = parse_aware_datetime(start_raw) if start_raw else None
        end_at = parse_aware_datetime(end_raw) if end_raw else None
        if start_at and end_at and end_at <= start_at:
            flash("Exam end time must be later than the start time.", "danger")
            return redirect(url_for("create_exam"))
        if duration_minutes < 0:
            flash("Duration must be a positive number.", "danger")
            return redirect(url_for("create_exam"))

        exam = Exam(
            title=title,
            description=description,
            teacher_id=session["user_id"],
            duration_minutes=duration_minutes,
            start_at=start_at,
            end_at=end_at,
            target_branch=target_branch,
            target_division=target_division,
        )
        db.session.add(exam)
        db.session.commit()
        flash("Exam created. Add questions below.", "success")
        return redirect(url_for("manage_exam", exam_id=exam.id))

    return render_template("create_exam.html")


@app.route("/teacher/exam/<int:exam_id>")
@login_required("teacher")
def manage_exam(exam_id):
    exam = db.session.get(Exam, exam_id)
    if not exam or exam.teacher_id != session["user_id"]:
        flash("Exam not found.", "danger")
        return redirect(url_for("teacher_dashboard"))
    preview = session.get("import_preview") if session.get("import_preview") and session["import_preview"].get("exam_id") == exam.id else None
    return render_template("manage_exam.html", exam=exam, preview=preview)


@app.route("/teacher/exam/<int:exam_id>/schedule", methods=["POST"])
@login_required("teacher")
def update_exam_schedule(exam_id):
    exam = db.session.get(Exam, exam_id)
    if not exam or exam.teacher_id != session["user_id"]:
        flash("Exam not found.", "danger")
        return redirect(url_for("teacher_dashboard"))

    start_raw = request.form.get("start_at")
    end_raw = request.form.get("end_at")
    duration_value = request.form.get("duration_minutes", "0")
    raw_target_branch = request.form.get("target_branch", "")
    raw_target_division = request.form.get("target_division", "")
    target_branch = canonical_branch(raw_target_branch)
    target_division = canonical_division(raw_target_division)
    if target_branch is None and normalize_class_value(raw_target_branch) == normalize_class_value(exam.target_branch):
        target_branch = exam.target_branch
    if target_division is None and normalize_class_value(raw_target_division) == normalize_class_value(exam.target_division):
        target_division = exam.target_division
    try:
        duration_minutes = int(duration_value or 0)
    except ValueError:
        flash("Duration must be a number of minutes.", "danger")
        return redirect(url_for("manage_exam", exam_id=exam_id))

    start_at = parse_aware_datetime(start_raw) if start_raw else None
    end_at = parse_aware_datetime(end_raw) if end_raw else None
    if start_at and end_at and end_at <= start_at:
        flash("Exam end time must be later than the start time.", "danger")
        return redirect(url_for("manage_exam", exam_id=exam_id))
    if target_branch is None or target_division is None:
        flash("Select a valid assigned branch and division from the available lists.", "danger")
        return redirect(url_for("manage_exam", exam_id=exam_id))
    if Attempt.query.filter_by(exam_id=exam.id).first() and (
        normalize_class_value(target_branch) != normalize_class_value(exam.target_branch)
        or normalize_class_value(target_division) != normalize_class_value(exam.target_division)
    ):
        flash("The assigned class cannot be changed after a student has started this exam.", "danger")
        return redirect(url_for("manage_exam", exam_id=exam_id))
    exam.start_at = start_at
    exam.end_at = end_at
    exam.duration_minutes = max(duration_minutes, 0)
    exam.target_branch = target_branch
    exam.target_division = target_division
    db.session.commit()
    flash("Exam schedule updated.", "success")
    return redirect(url_for("manage_exam", exam_id=exam_id))


@app.route("/teacher/exam/<int:exam_id>/question", methods=["POST"])
@login_required("teacher")
def add_question(exam_id):
    exam = db.session.get(Exam, exam_id)
    if not exam or exam.teacher_id != session["user_id"]:
        flash("Exam not found.", "danger")
        return redirect(url_for("teacher_dashboard"))
    if Attempt.query.filter_by(exam_id=exam.id).first():
        flash("Questions cannot be changed after a student has started this exam.", "danger")
        return redirect(url_for("manage_exam", exam_id=exam_id))

    correct = (request.form.get("correct_option") or "").upper()
    if correct not in ("A", "B", "C", "D"):
        flash("Correct option must be A, B, C or D.", "danger")
        return redirect(url_for("manage_exam", exam_id=exam_id))

    text = request.form.get("text", "").strip()
    option_a = request.form.get("option_a", "").strip()
    option_b = request.form.get("option_b", "").strip()
    option_c = request.form.get("option_c", "").strip()
    option_d = request.form.get("option_d", "").strip()

    if not all([text, option_a, option_b, option_c, option_d]):
        flash("All question fields are required.", "danger")
        return redirect(url_for("manage_exam", exam_id=exam_id))

    q = Question(
        exam_id=exam_id,
        text=text,
        option_a=option_a,
        option_b=option_b,
        option_c=option_c,
        option_d=option_d,
        correct_option=correct,
    )
    db.session.add(q)
    db.session.commit()
    flash("Question added.", "success")
    return redirect(url_for("manage_exam", exam_id=exam_id))


@app.route("/teacher/exam/<int:exam_id>/delete", methods=["POST"])
@login_required("teacher")
def delete_exam(exam_id):
    exam = db.session.get(Exam, exam_id)
    if not exam or exam.teacher_id != session["user_id"]:
        flash("Exam not found.", "danger")
        return redirect(url_for("teacher_dashboard"))

    db.session.delete(exam)
    db.session.commit()
    flash("Exam deleted.", "success")
    return redirect(url_for("teacher_dashboard"))


@app.route("/teacher/results/<int:exam_id>")
@login_required("teacher")
def exam_results(exam_id):
    exam = db.session.get(Exam, exam_id)
    if not exam or exam.teacher_id != session["user_id"]:
        flash("Exam not found.", "danger")
        return redirect(url_for("teacher_dashboard"))

    attempts = Attempt.query.filter_by(exam_id=exam_id).order_by(Attempt.submitted_at.desc(), Attempt.id.desc()).all()
    students = {u.id: u for u in User.query.filter_by(role="student").all()}
    submitted_attempts = [attempt for attempt in attempts if attempt.status == "submitted"]
    answers = StudentAnswer.query.join(Attempt).filter(
        Attempt.exam_id == exam_id,
        Attempt.status == "submitted",
    ).all()
    question_performance = {}
    for question in exam.questions:
        question_answers = [answer for answer in answers if answer.question_id == question.id]
        question_performance[question.id] = {
            "answered": len(question_answers),
            "correct": sum(answer.is_correct for answer in question_answers),
            "rate": (
                round(sum(answer.is_correct for answer in question_answers) * 100 / len(question_answers))
                if question_answers else 0
            ),
        }
    average_score = (
        round(sum(attempt.score * 100 / attempt.total for attempt in submitted_attempts if attempt.total)
              / sum(bool(attempt.total) for attempt in submitted_attempts))
        if any(attempt.total for attempt in submitted_attempts) else 0
    )
    eligible_students = [
        student for student in User.query.filter_by(role="student").all()
        if exam_matches_student(exam, student)
    ]
    student_count = len(eligible_students)
    participant_ids = {
        attempt.student_id for attempt in attempts
    }
    participation_rate = round(len(participant_ids) * 100 / student_count) if student_count else 0
    return render_template(
        "results.html",
        exam=exam,
        attempts=attempts,
        students=students,
        submitted_attempts=submitted_attempts,
        question_performance=question_performance,
        average_score=average_score,
        participant_count=len(participant_ids),
        student_count=student_count,
        participation_rate=participation_rate,
    )


@app.route("/teacher/exam/<int:exam_id>/reset", methods=["POST"])
@login_required("teacher")
def reset_attempt_for_exam(exam_id):
    exam = db.session.get(Exam, exam_id)
    if not exam or exam.teacher_id != session["user_id"]:
        flash("Exam not found.", "danger")
        return redirect(url_for("teacher_dashboard"))

    student_id = request.form.get("student_id")
    if student_id:
        try:
            student_id = int(student_id)
        except ValueError:
            flash("Invalid student selection.", "danger")
            return redirect(url_for("exam_results", exam_id=exam.id))
        attempts = Attempt.query.filter_by(exam_id=exam.id, student_id=student_id).all()
        label = "This student's attempt has been reset."
    else:
        attempts = Attempt.query.filter_by(exam_id=exam.id).all()
        label = "All attempts for this exam have been reset."

    delete_attempt_records(attempts)
    flash(label, "success")
    return redirect(url_for("exam_results", exam_id=exam.id))


@app.route("/teacher/exam/<int:exam_id>/attempt/<int:attempt_id>")
@login_required("teacher")
def teacher_attempt_review(exam_id, attempt_id):
    exam = db.session.get(Exam, exam_id)
    if not exam or exam.teacher_id != session["user_id"]:
        flash("Exam not found.", "danger")
        return redirect(url_for("teacher_dashboard"))

    attempt = db.session.get(Attempt, attempt_id)
    if not attempt or attempt.exam_id != exam.id:
        flash("Attempt not found.", "danger")
        return redirect(url_for("exam_results", exam_id=exam.id))

    student = db.session.get(User, attempt.student_id)
    answer_map = answer_map_for_attempt(attempt.id)
    ordered_questions = get_attempt_questions(attempt, exam)
    return render_template(
        "teacher_attempt_review.html",
        exam=exam,
        attempt=attempt,
        student=student,
        answer_map=answer_map,
        ordered_questions=ordered_questions,
    )


@app.route("/teacher/exam/<int:exam_id>/import", methods=["POST"])
@login_required("teacher")
def import_questions(exam_id):
    exam = db.session.get(Exam, exam_id)
    if not exam or exam.teacher_id != session["user_id"]:
        flash("Exam not found.", "danger")
        return redirect(url_for("teacher_dashboard"))
    if Attempt.query.filter_by(exam_id=exam.id).first():
        flash("Questions cannot be changed after a student has started this exam.", "danger")
        return redirect(url_for("manage_exam", exam_id=exam_id))

    file = request.files.get("question_file")
    if not file or not file.filename:
        flash("Please choose a CSV or Excel file to import.", "danger")
        return redirect(url_for("manage_exam", exam_id=exam_id))

    filename = secure_filename(file.filename)
    extension = os.path.splitext(filename)[1].lower()
    if extension not in {".csv", ".xlsx"}:
        flash("Only CSV and XLSX files are supported.", "danger")
        return redirect(url_for("manage_exam", exam_id=exam_id))

    if file.content_length and file.content_length > MAX_UPLOAD_BYTES:
        flash("Uploaded file is too large. Maximum size is 2 MB.", "danger")
        return redirect(url_for("manage_exam", exam_id=exam_id))

    try:
        rows = parse_import_file(file, filename)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("manage_exam", exam_id=exam_id))

    preview = {"exam_id": exam.id, "filename": filename, "rows": rows}
    session["import_preview"] = preview
    flash("Imported questions were validated successfully. Review the preview and confirm to save.", "success")
    return redirect(url_for("manage_exam", exam_id=exam_id))


@app.route("/teacher/exam/<int:exam_id>/confirm-import", methods=["POST"])
@login_required("teacher")
def confirm_import(exam_id):
    exam = db.session.get(Exam, exam_id)
    if not exam or exam.teacher_id != session["user_id"]:
        flash("Exam not found.", "danger")
        return redirect(url_for("teacher_dashboard"))
    if Attempt.query.filter_by(exam_id=exam.id).first():
        flash("Questions cannot be changed after a student has started this exam.", "danger")
        return redirect(url_for("manage_exam", exam_id=exam_id))

    preview = session.get("import_preview")
    if not preview or preview.get("exam_id") != exam_id:
        flash("No valid import preview is available.", "danger")
        return redirect(url_for("manage_exam", exam_id=exam_id))

    imported_count = 0
    for row in preview.get("rows", []):
        text_value = (row["question"] or "").strip()
        option_a = (row["option_a"] or "").strip()
        option_b = (row["option_b"] or "").strip()
        option_c = (row["option_c"] or "").strip()
        option_d = (row["option_d"] or "").strip()
        correct_option = (row["correct_option"] or "").strip().upper()

        if not all([text_value, option_a, option_b, option_c, option_d]):
            continue

        duplicate = any(
            q.text.strip().lower() == text_value.lower() and q.option_a.strip() == option_a and q.option_b.strip() == option_b and q.option_c.strip() == option_c and q.option_d.strip() == option_d
            for q in exam.questions
        )
        if duplicate:
            continue

        db.session.add(
            Question(
                exam_id=exam.id,
                text=text_value,
                option_a=option_a,
                option_b=option_b,
                option_c=option_c,
                option_d=option_d,
                correct_option=correct_option,
            )
        )
        imported_count += 1

    db.session.commit()
    session.pop("import_preview", None)
    flash(f"{imported_count} question(s) were imported successfully.", "success")
    return redirect(url_for("manage_exam", exam_id=exam_id))


@app.route("/teacher/exam/<int:exam_id>/template/<string:file_type>")
@login_required("teacher")
def exam_import_template(exam_id, file_type):
    exam = db.session.get(Exam, exam_id)
    if not exam or exam.teacher_id != session["user_id"]:
        flash("Exam not found.", "danger")
        return redirect(url_for("teacher_dashboard"))

    file_type = file_type.lower()
    if file_type not in {"csv", "xlsx"}:
        abort(404)

    if file_type == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(IMPORT_REQUIRED_COLUMNS)
        writer.writerow(["Which cloud model provides complete software applications to users?", "IaaS", "PaaS", "SaaS", "DaaS", "C"])
        return send_file(
            io.BytesIO(buffer.getvalue().encode("utf-8")),
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"{secure_filename(exam.title)}_template.csv",
        )

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Questions"
    sheet.append(IMPORT_REQUIRED_COLUMNS)
    sheet.append(["Which cloud model provides complete software applications to users?", "IaaS", "PaaS", "SaaS", "DaaS", "C"])
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return send_file(output, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True, download_name=f"{secure_filename(exam.title)}_template.xlsx")


def parse_import_file(file, filename):
    extension = os.path.splitext(filename)[1].lower()
    file.stream.seek(0)
    if extension == ".csv":
        data = file.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(data))
        rows = []
        if reader.fieldnames is None:
            raise ValueError("The CSV file is empty or missing a header row.")
        normalized_headers = {header.strip().lower(): header for header in reader.fieldnames}
        missing = [column for column in IMPORT_REQUIRED_COLUMNS if column not in normalized_headers]
        if missing:
            raise ValueError(f"Missing required columns: {', '.join(missing)}")
        for row_number, row in enumerate(reader, start=2):
            cleaned = {key: (value.strip() if isinstance(value, str) else value) for key, value in row.items()}
            normalized_row = {}
            for expected in IMPORT_REQUIRED_COLUMNS:
                original_name = normalized_headers[expected]
                normalized_row[expected] = cleaned.get(original_name, "")
            rows.append(normalized_row)
        validate_import_rows(rows)
        return rows

    workbook = load_workbook(file, read_only=True, data_only=True)
    if not workbook.sheetnames:
        raise ValueError("The Excel file does not contain any sheets.")

    sheet = workbook[workbook.sheetnames[0]]
    rows = []
    for row_index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
        if row_index == 1:
            headers = [str(cell).strip().lower() if cell is not None else "" for cell in row]
            missing = [column for column in IMPORT_REQUIRED_COLUMNS if column not in headers]
            if missing:
                raise ValueError(f"Missing required columns: {', '.join(missing)}")
            continue
        if not any(cell is not None and str(cell).strip() for cell in row):
            continue
        record = {}
        for header_index, header in enumerate(headers):
            if header in IMPORT_REQUIRED_COLUMNS:
                record[header] = "" if header_index >= len(row) or row[header_index] is None else str(row[header_index]).strip()
        rows.append(record)

    validate_import_rows(rows)
    return rows


def validate_import_rows(rows):
    for row_number, row in enumerate(rows, start=2):
        normalized = {key: (value or "").strip() for key, value in row.items()}
        missing_fields = [field for field in IMPORT_REQUIRED_COLUMNS if not normalized.get(field, "")]
        if missing_fields:
            raise ValueError(f"Row {row_number}: missing required fields ({', '.join(missing_fields)}).")

        correct_option = normalized["correct_option"].upper()
        if correct_option not in {"A", "B", "C", "D"}:
            raise ValueError(f"Row {row_number}: correct_option must be A, B, C, or D.")

        for field in ["question", "option_a", "option_b", "option_c", "option_d"]:
            if not normalized.get(field):
                raise ValueError(f"Row {row_number}: {field} cannot be empty.")


def seed_demo_data():
    """Creates demo accounts and a demo exam only when the database is empty."""
    if app_environment == "production" or User.query.count() > 0:
        return

    teacher = User(
        name="Demo Teacher",
        email="teacher@example.com",
        password_hash=generate_password_hash("teacher123"),
        role="teacher",
    )
    student = User(
        name="Demo Student",
        email="student@example.com",
        password_hash=generate_password_hash("student123"),
        role="student",
        branch="Computer Engineering",
        division="A",
    )
    db.session.add_all([teacher, student])
    db.session.commit()

    exam = Exam(
        title="Cloud Computing Basics",
        description="Demo MCQ examination covering basic cloud concepts.",
        teacher_id=teacher.id,
        duration_minutes=15,
    )
    db.session.add(exam)
    db.session.commit()

    questions = [
        Question(
            exam_id=exam.id,
            text="Which cloud model provides complete software applications to users?",
            option_a="IaaS",
            option_b="PaaS",
            option_c="SaaS",
            option_d="DaaS",
            correct_option="C",
        ),
        Question(
            exam_id=exam.id,
            text="Which service is commonly used for storing structured relational data?",
            option_a="Cloud SQL",
            option_b="Cloud DNS",
            option_c="Cloud CDN",
            option_d="Cloud Load Balancing",
            correct_option="A",
        ),
        Question(
            exam_id=exam.id,
            text="What is the main purpose of authentication?",
            option_a="Compress data",
            option_b="Verify user identity",
            option_c="Increase storage",
            option_d="Create backups",
            correct_option="B",
        ),
        Question(
            exam_id=exam.id,
            text="Which deployment model is dedicated to a single organization?",
            option_a="Public cloud",
            option_b="Private cloud",
            option_c="Community-free cloud",
            option_d="Open cloud",
            correct_option="B",
        ),
    ]
    db.session.add_all(questions)
    db.session.commit()


with app.app_context():
    db.create_all()
    add_missing_columns()
    seed_demo_data()


if __name__ == "__main__":
    debug_enabled = os.environ.get("FLASK_DEBUG", "0").lower() in {"1", "true", "yes"}
    app.run(debug=debug_enabled and app_environment != "production")