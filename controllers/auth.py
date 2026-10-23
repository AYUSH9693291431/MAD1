from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from controllers import FormErrors
from models import Examiner, Student, User, db

auth_bp = Blueprint("auth", __name__)

USERNAME_RE = r"[A-Za-z0-9_.]{3,30}"
EMAIL_RE = r"[^@\s]+@[^@\s]+\.[^@\s]+"
PHONE_RE = r"\d{10}"


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(request.form.get("password", "")):
            login_user(user)
            flash(f"Welcome, {user.name}!", "success")
            return redirect(url_for("index"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    form = request.form
    role = request.values.get("role", "student")
    if role not in ("student", "examiner"):
        role = "student"
    if request.method == "POST":
        errors = FormErrors()
        user = User(role=role)
        read_account_fields(form, errors, user, new=True)
        if role == "student":
            profile = Student(user=user)
            profile.roll_number = errors.pattern(
                form, "roll_number", "Roll number", r"[A-Za-z0-9]{4,20}", "must be 4-20 letters or digits."
            ).upper()
            profile.program = errors.text(form, "program", "Program", required=False, max_len=100)
            if Student.query.filter_by(roll_number=profile.roll_number).first():
                errors.append("That roll number is already registered.")
        else:
            profile = Examiner(user=user)
            profile.department = errors.text(form, "department", "Department", max_len=100)
            profile.designation = errors.text(form, "designation", "Designation", required=False, max_len=100)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("register.html", form=form, role=role)
        db.session.add(user)
        db.session.commit()
        if role == "examiner":
            flash("Registration successful. You can log in, but the admin must approve your account first.", "success")
        else:
            flash("Registration successful. Please log in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("register.html", form=form, role=role)


@auth_bp.route("/pending")
@login_required
def pending():
    if current_user.role != "examiner" or current_user.examiner.is_approved:
        return redirect(url_for("index"))
    return render_template("pending.html", status=current_user.examiner.status)


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if current_user.role == "admin":
        return redirect(url_for("admin.dashboard"))
    if request.method == "POST":
        errors = FormErrors()
        read_account_fields(request.form, errors, current_user, new=False)
        if current_user.role == "student":
            current_user.student.program = errors.text(request.form, "program", "Program", required=False, max_len=100)
        else:
            current_user.examiner.department = errors.text(request.form, "department", "Department", max_len=100)
            current_user.examiner.designation = errors.text(
                request.form, "designation", "Designation", required=False, max_len=100
            )
        if errors:
            db.session.rollback()
            for e in errors:
                flash(e, "danger")
            return render_template("profile.html", form=request.form)
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("auth.profile"))
    form = {"name": current_user.name, "email": current_user.email, "phone": current_user.phone}
    if current_user.role == "student":
        form["program"] = current_user.student.program
    else:
        form.update(department=current_user.examiner.department, designation=current_user.examiner.designation)
    return render_template("profile.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


def read_account_fields(form, errors, user, new):
    """Validate the fields shared by every account and copy them onto user."""
    if new:
        user.username = errors.pattern(
            form, "username", "Username", USERNAME_RE, "must be 3-30 letters, digits, '_' or '.'."
        )
        if User.query.filter_by(username=user.username).first():
            errors.append("That username is already taken.")
    user.name = errors.text(form, "name", "Name", max_len=120)
    email = errors.pattern(form, "email", "Email", EMAIL_RE, "must be a valid email address.").lower()
    if User.query.filter(User.email == email, User.id != user.id).first():
        errors.append("That email is already registered.")
    user.email = email
    user.phone = errors.pattern(form, "phone", "Phone", PHONE_RE, "must be 10 digits.", required=False)
    password = form.get("password", "")
    if new or password:
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        elif password != form.get("confirm_password", ""):
            errors.append("Passwords do not match.")
        else:
            user.set_password(password)
