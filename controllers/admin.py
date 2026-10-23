from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import func, or_

from controllers import FormErrors, role_required
from controllers.auth import read_account_fields
from models import (
    BOOKING_OPEN,
    CLOSED,
    COMPLETED,
    DRAFT,
    EXAM_STATUSES,
    EXAM_TYPES,
    SLOT_CREATION,
    Booking,
    Course,
    Evaluation,
    Exam,
    Examiner,
    Rubric,
    Slot,
    Student,
    User,
    db,
)

admin_bp = Blueprint("admin", __name__)

# action -> (statuses it can be applied from, status it moves the exam to)
EXAM_TRANSITIONS = {
    "open_slot_creation": ([DRAFT], SLOT_CREATION),
    "close_slot_creation": ([SLOT_CREATION], DRAFT),
    "open_booking": ([SLOT_CREATION, CLOSED], BOOKING_OPEN),
    "close_booking": ([BOOKING_OPEN], CLOSED),
    "complete": ([CLOSED], COMPLETED),
}


@admin_bp.route("/")
@role_required("admin")
def dashboard():
    counts = {
        "Courses": Course.query.count(),
        "Examinations": Exam.query.count(),
        "Examiners": Examiner.query.count(),
        "Students": Student.query.count(),
        "Slots": Slot.query.count(),
        "Bookings": Booking.query.count(),
    }
    pending_examiners = Examiner.query.filter_by(status="Pending").all()
    exams_by_status = dict(db.session.query(Exam.status, func.count(Exam.id)).group_by(Exam.status).all())
    recent_bookings = Booking.query.order_by(Booking.booked_at.desc()).limit(8).all()
    return render_template(
        "admin/dashboard.html",
        counts=counts,
        pending_examiners=pending_examiners,
        exams_by_status=exams_by_status,
        statuses=EXAM_STATUSES,
        recent_bookings=recent_bookings,
    )


# ---------- Courses ----------


@admin_bp.route("/courses")
@role_required("admin")
def courses():
    return render_template("admin/courses.html", courses=Course.query.order_by(Course.code).all())


@admin_bp.route("/courses/new", methods=["GET", "POST"])
@admin_bp.route("/courses/<int:course_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def course_form(course_id=None):
    course = db.get_or_404(Course, course_id) if course_id else Course()
    if request.method == "POST":
        errors = FormErrors()
        form = request.form
        code = errors.pattern(form, "code", "Course code", r"[A-Za-z0-9-]{2,20}", "must be 2-20 letters, digits or '-'.")
        code = code.upper()
        if Course.query.filter(Course.code == code, Course.id != course.id).first():
            errors.append(f"Course code {code} already exists.")
        name = errors.text(form, "name", "Course name", max_len=120)
        description = errors.text(form, "description", "Description", required=False, max_len=2000)
        credits = errors.number(form, "credits", "Credits", 0, 20, integer=True) if form.get("credits") else None
        status = form.get("status") if form.get("status") in ("Active", "Inactive") else "Active"
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("admin/course_form.html", course=course, form=form)
        course.code, course.name, course.description, course.credits, course.status = (
            code, name, description, credits, status
        )
        if not course.id:
            db.session.add(course)
        db.session.commit()
        flash(f"Course {course.code} saved.", "success")
        return redirect(url_for("admin.courses"))
    return render_template("admin/course_form.html", course=course, form=course_to_form(course))


@admin_bp.route("/courses/<int:course_id>/delete", methods=["POST"])
@role_required("admin")
def delete_course(course_id):
    course = db.get_or_404(Course, course_id)
    if course.exams:
        flash("This course has examinations, so it can't be removed. Mark it Inactive instead.", "danger")
    else:
        db.session.delete(course)
        db.session.commit()
        flash(f"Course {course.code} removed.", "success")
    return redirect(url_for("admin.courses"))


# ---------- Examinations ----------


@admin_bp.route("/exams")
@role_required("admin")
def exams():
    query = Exam.query
    status = request.args.get("status", "")
    course_id = request.args.get("course_id", type=int)
    if status in EXAM_STATUSES:
        query = query.filter_by(status=status)
    if course_id:
        query = query.filter_by(course_id=course_id)
    return render_template(
        "admin/exams.html",
        exams=query.order_by(Exam.booking_start.desc()).all(),
        courses=Course.query.order_by(Course.code).all(),
        statuses=EXAM_STATUSES,
        status=status,
        course_id=course_id,
    )


@admin_bp.route("/exams/new", methods=["GET", "POST"])
@admin_bp.route("/exams/<int:exam_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def exam_form(exam_id=None):
    exam = db.get_or_404(Exam, exam_id) if exam_id else Exam()
    courses = Course.query.order_by(Course.code).all()
    if request.method == "POST":
        errors = FormErrors()
        form = request.form
        course = db.session.get(Course, form.get("course_id", type=int) or 0)
        if not course:
            errors.append("Choose a course.")
        name = errors.text(form, "name", "Examination name", max_len=120)
        exam_type = form.get("exam_type")
        if exam_type not in EXAM_TYPES:
            errors.append("Choose an examination type.")
        description = errors.text(form, "description", "Description", required=False, max_len=2000)
        duration = errors.number(form, "duration", "Duration (minutes)", 5, 600, integer=True)
        max_marks = errors.number(form, "max_marks", "Maximum marks", 1, 1000)
        dates = {
            field: errors.date(form, field, label)
            for field, label in [
                ("slot_creation_start", "Slot creation start"),
                ("slot_creation_end", "Slot creation end"),
                ("booking_start", "Booking start"),
                ("booking_end", "Booking end"),
            ]
        }
        if all(dates.values()):
            if dates["slot_creation_start"] > dates["slot_creation_end"]:
                errors.append("Slot creation must end on or after it starts.")
            if dates["booking_start"] > dates["booking_end"]:
                errors.append("Booking must end on or after it starts.")
            if dates["slot_creation_end"] > dates["booking_start"]:
                errors.append("Slot creation must end on or before booking starts.")
        if exam.id and exam.slots and duration != exam.duration:
            errors.append("Duration can't change once slots exist, because slot timings depend on it.")
        if exam.id and max_marks != exam.max_marks and any(b.evaluation for b in exam.active_bookings):
            errors.append("Maximum marks can't change once students have been evaluated.")
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("admin/exam_form.html", exam=exam, form=form, courses=courses, types=EXAM_TYPES)
        exam.course, exam.name, exam.exam_type, exam.description = course, name, exam_type, description
        exam.duration, exam.max_marks = duration, max_marks
        for field, value in dates.items():
            setattr(exam, field, value)
        if not exam.id:
            db.session.add(exam)
        db.session.commit()
        flash(f"Examination '{exam.name}' saved.", "success")
        return redirect(url_for("admin.exam_detail", exam_id=exam.id))
    return render_template(
        "admin/exam_form.html", exam=exam, form=exam_to_form(exam), courses=courses, types=EXAM_TYPES
    )


@admin_bp.route("/exams/<int:exam_id>")
@role_required("admin")
def exam_detail(exam_id):
    exam = db.get_or_404(Exam, exam_id)
    actions = [action for action, (sources, _) in EXAM_TRANSITIONS.items() if exam.status in sources]
    examiners = Examiner.query.filter_by(status="Approved").all()
    return render_template("admin/exam_detail.html", exam=exam, actions=actions, examiners=examiners)


@admin_bp.route("/exams/<int:exam_id>/delete", methods=["POST"])
@role_required("admin")
def delete_exam(exam_id):
    exam = db.get_or_404(Exam, exam_id)
    if exam.slots:
        flash("This examination already has slots, so it can't be removed (its history must be kept).", "danger")
        return redirect(url_for("admin.exam_detail", exam_id=exam.id))
    db.session.delete(exam)
    db.session.commit()
    flash(f"Examination '{exam.name}' removed.", "success")
    return redirect(url_for("admin.exams"))


@admin_bp.route("/exams/<int:exam_id>/status", methods=["POST"])
@role_required("admin")
def exam_status(exam_id):
    exam = db.get_or_404(Exam, exam_id)
    action = request.form.get("action")
    if action not in EXAM_TRANSITIONS or exam.status not in EXAM_TRANSITIONS[action][0]:
        flash("That change isn't allowed from the current status.", "danger")
        return redirect(url_for("admin.exam_detail", exam_id=exam.id))
    if action == "open_booking":
        if not any(s.status != "Cancelled" for s in exam.slots):
            flash("Examiners haven't created any slots yet, so booking can't open.", "danger")
            return redirect(url_for("admin.exam_detail", exam_id=exam.id))
        if not exam.rubric_ready:
            flash("Set up the rubric (weightages adding to 100%) before opening booking.", "danger")
            return redirect(url_for("admin.exam_detail", exam_id=exam.id))
    if action == "complete":
        for slot in exam.slots:
            if slot.status != "Cancelled":
                slot.status = "Completed"
    exam.status = EXAM_TRANSITIONS[action][1]
    db.session.commit()
    flash(f"Examination status changed to {exam.status}.", "success")
    if action == "open_slot_creation" and not exam.slot_creation_open:
        flash("Note: today is outside the slot creation dates, so examiners can't create slots yet.", "warning")
    if action == "open_booking" and not exam.booking_open:
        flash("Note: students can't book until today is within the booking dates and the course is Active.", "warning")
    return redirect(url_for("admin.exam_detail", exam_id=exam.id))


@admin_bp.route("/exams/<int:exam_id>/results", methods=["POST"])
@role_required("admin")
def publish_results(exam_id):
    exam = db.get_or_404(Exam, exam_id)
    if exam.status not in (CLOSED, COMPLETED):
        flash("Results can be published only after booking is closed.", "danger")
    else:
        exam.results_published = request.form.get("publish") == "1"
        db.session.commit()
        flash("Results published to students." if exam.results_published else "Results hidden from students.", "success")
    return redirect(url_for("admin.exam_detail", exam_id=exam.id))


@admin_bp.route("/exams/<int:exam_id>/examiners", methods=["POST"])
@role_required("admin")
def assign_examiners(exam_id):
    exam = db.get_or_404(Exam, exam_id)
    chosen = {int(i) for i in request.form.getlist("examiner_ids") if i.isdigit()}
    # Only approved examiners appear as checkboxes, so only they can be added or removed here.
    with_slots = {s.examiner_id for s in exam.slots if s.status != "Cancelled" and s.examiner.is_approved}
    missing = with_slots - chosen
    if missing:
        names = ", ".join(db.session.get(Examiner, i).user.name for i in missing)
        flash(f"{names} already has slots for this exam. Change those slots' examiner first.", "danger")
        return redirect(url_for("admin.exam_detail", exam_id=exam.id))
    others = [e for e in exam.examiners if not e.is_approved]
    exam.examiners = others + Examiner.query.filter(Examiner.id.in_(chosen), Examiner.status == "Approved").all()
    db.session.commit()
    flash("Assigned examiners updated.", "success")
    return redirect(url_for("admin.exam_detail", exam_id=exam.id))


# ---------- Rubrics ----------


@admin_bp.route("/exams/<int:exam_id>/rubrics/new", methods=["GET", "POST"])
@admin_bp.route("/rubrics/<int:rubric_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def rubric_form(exam_id=None, rubric_id=None):
    if rubric_id:
        rubric = db.get_or_404(Rubric, rubric_id)
        exam = rubric.exam
    else:
        exam = db.get_or_404(Exam, exam_id)
        rubric = Rubric()
    if rubric.id and rubric.scores:
        flash("Students have already been marked on this criterion, so it can't be edited.", "danger")
        return redirect(url_for("admin.exam_detail", exam_id=exam.id))
    if request.method == "POST":
        errors = FormErrors()
        form = request.form
        criterion = errors.text(form, "criterion", "Criterion name", max_len=120)
        max_marks = errors.number(form, "max_marks", "Maximum marks", 1, 1000)
        weightage = errors.number(form, "weightage", "Weightage", 1, 100)
        description = errors.text(form, "description", "Description", required=False, max_len=1000)
        others = sum(r.weightage for r in exam.rubrics if r.id != rubric.id)
        if weightage is not None and others + weightage > 100.001:
            errors.append(f"Total weightage can't exceed 100%. {100 - others:g}% is left for this criterion.")
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("admin/rubric_form.html", exam=exam, rubric=rubric, form=form)
        rubric.criterion, rubric.max_marks, rubric.weightage, rubric.description = (
            criterion, max_marks, weightage, description
        )
        if not rubric.id:
            rubric.exam = exam
            db.session.add(rubric)
        db.session.commit()
        flash(f"Criterion '{rubric.criterion}' saved.", "success")
        return redirect(url_for("admin.exam_detail", exam_id=exam.id))
    form = {
        "criterion": rubric.criterion,
        "max_marks": rubric.max_marks,
        "weightage": rubric.weightage,
        "description": rubric.description,
    }
    return render_template("admin/rubric_form.html", exam=exam, rubric=rubric, form=form)


@admin_bp.route("/rubrics/<int:rubric_id>/delete", methods=["POST"])
@role_required("admin")
def delete_rubric(rubric_id):
    rubric = db.get_or_404(Rubric, rubric_id)
    exam_id = rubric.exam_id
    if rubric.scores:
        flash("Students have already been marked on this criterion, so it can't be removed.", "danger")
    else:
        db.session.delete(rubric)
        db.session.commit()
        flash("Criterion removed.", "success")
    return redirect(url_for("admin.exam_detail", exam_id=exam_id))


# ---------- Examiners and students ----------


@admin_bp.route("/examiners")
@role_required("admin")
def examiners():
    status = request.args.get("status", "")
    query = Examiner.query.join(User)
    if status in ("Pending", "Approved", "Deactivated"):
        query = query.filter(Examiner.status == status)
    return render_template("admin/examiners.html", examiners=query.order_by(User.name).all(), status=status)


@admin_bp.route("/examiners/new", methods=["GET", "POST"])
@role_required("admin")
def new_examiner():
    form = request.form
    if request.method == "POST":
        errors = FormErrors()
        user = User(role="examiner")
        read_account_fields(form, errors, user, new=True)
        examiner = Examiner(user=user, status="Approved")
        examiner.department = errors.text(form, "department", "Department", max_len=100)
        examiner.designation = errors.text(form, "designation", "Designation", required=False, max_len=100)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("admin/examiner_form.html", form=form)
        db.session.add(user)
        db.session.commit()
        flash(f"Examiner {user.name} added and approved.", "success")
        return redirect(url_for("admin.examiners"))
    return render_template("admin/examiner_form.html", form=form)


@admin_bp.route("/examiners/<int:examiner_id>/status", methods=["POST"])
@role_required("admin")
def examiner_status(examiner_id):
    examiner = db.get_or_404(Examiner, examiner_id)
    new_status = request.form.get("status")
    if new_status not in ("Approved", "Deactivated"):
        flash("Unknown status.", "danger")
    elif new_status == "Deactivated" and any(
        s.status in ("Available", "Full") and s.active_bookings for s in examiner.slots
    ):
        flash(f"{examiner.user.name} has upcoming slots with bookings. Change those slots' examiner first.", "danger")
    else:
        examiner.status = new_status
        db.session.commit()
        flash(f"{examiner.user.name} is now {new_status.lower()}.", "success")
    return redirect(request.referrer or url_for("admin.examiners"))


@admin_bp.route("/students")
@role_required("admin")
def students():
    return render_template("admin/students.html", students=Student.query.join(User).order_by(User.name).all())


@admin_bp.route("/students/<int:student_id>")
@role_required("admin")
def student_detail(student_id):
    return render_template("admin/student_detail.html", student=db.get_or_404(Student, student_id))


# ---------- Slots and bookings ----------


@admin_bp.route("/slots")
@role_required("admin")
def slots():
    exam_id = request.args.get("exam_id", type=int)
    query = Slot.query
    if exam_id:
        query = query.filter_by(exam_id=exam_id)
    return render_template(
        "admin/slots.html",
        slots=query.order_by(Slot.date.desc(), Slot.start_time).all(),
        exams=Exam.query.order_by(Exam.name).all(),
        exam_id=exam_id,
    )


@admin_bp.route("/slots/<int:slot_id>/examiner", methods=["GET", "POST"])
@role_required("admin")
def change_examiner(slot_id):
    slot = db.get_or_404(Slot, slot_id)
    candidates = [
        e for e in Examiner.query.filter_by(status="Approved").all()
        if e.id != slot.examiner_id
        and not any(s.overlaps(slot) and s.status != "Cancelled" for s in e.slots)
    ]
    if slot.status in ("Cancelled", "Completed"):
        flash("The examiner can't be changed for a cancelled or completed slot.", "danger")
        return redirect(url_for("admin.slots", exam_id=slot.exam_id))
    if request.method == "POST":
        new = next((e for e in candidates if e.id == request.form.get("examiner_id", type=int)), None)
        if not new:
            flash("Choose an approved examiner who is free at this time.", "danger")
            return render_template("admin/change_examiner.html", slot=slot, candidates=candidates)
        old_name = slot.examiner.user.name
        slot.examiner = new
        if new not in slot.exam.examiners:
            slot.exam.examiners.append(new)
        db.session.commit()
        flash(f"Slot #{slot.id} moved from {old_name} to {new.user.name}.", "success")
        return redirect(url_for("admin.slots", exam_id=slot.exam_id))
    return render_template("admin/change_examiner.html", slot=slot, candidates=candidates)


@admin_bp.route("/bookings")
@role_required("admin")
def bookings():
    status = request.args.get("status", "")
    exam_id = request.args.get("exam_id", type=int)
    query = Booking.query.join(Slot)
    if status in ("Booked", "Cancelled", "Completed"):
        query = query.filter(Booking.status == status)
    if exam_id:
        query = query.filter(Slot.exam_id == exam_id)
    return render_template(
        "admin/bookings.html",
        bookings=query.order_by(Booking.booked_at.desc()).all(),
        exams=Exam.query.order_by(Exam.name).all(),
        status=status,
        exam_id=exam_id,
    )


@admin_bp.route("/bookings/<int:booking_id>/reschedule", methods=["GET", "POST"])
@role_required("admin")
def reschedule(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    if booking.status != "Booked":
        flash("Only active (Booked) bookings can be rescheduled.", "danger")
        return redirect(url_for("admin.bookings"))
    student_slots = [b.slot for b in booking.student.bookings if b.status == "Booked" and b.id != booking.id]
    options = [
        s for s in booking.slot.exam.slots
        if s.id != booking.slot_id
        and s.status == "Available"
        and s.available_seats > 0
        and not s.has_started
        and not any(s.overlaps(other) for other in student_slots)
    ]
    if request.method == "POST":
        target = next((s for s in options if s.id == request.form.get("slot_id", type=int)), None)
        if not target:
            flash("Choose a slot that still has free seats.", "danger")
            return render_template("admin/reschedule.html", booking=booking, options=options)
        old_slot = booking.slot
        booking.status = "Cancelled"
        booking.cancelled_at = datetime.now()
        booking.note = f"Rescheduled by admin to slot #{target.id}"
        new_booking = Booking(student=booking.student, slot=target, note=f"Rescheduled by admin from slot #{old_slot.id}")
        db.session.add(new_booking)
        old_slot.refresh_status()
        target.refresh_status()
        db.session.commit()
        flash(f"Booking moved to slot #{target.id} on {target.date:%d %b %Y} at {target.start_time:%H:%M}.", "success")
        return redirect(url_for("admin.bookings"))
    return render_template("admin/reschedule.html", booking=booking, options=options)


# ---------- Search ----------


@admin_bp.route("/search")
@role_required("admin")
def search():
    q = request.args.get("q", "").strip()
    results = {"students": [], "examiners": [], "exams": [], "bookings": []}
    if q:
        like = f"%{q}%"
        num = int(q) if q.isdigit() else -1
        results["students"] = (
            Student.query.join(User)
            .filter(or_(User.name.ilike(like), Student.roll_number.ilike(like), User.username.ilike(like), Student.id == num))
            .all()
        )
        results["examiners"] = (
            Examiner.query.join(User)
            .filter(or_(User.name.ilike(like), Examiner.department.ilike(like), Examiner.id == num))
            .all()
        )
        results["exams"] = (
            Exam.query.join(Course)
            .filter(or_(Exam.name.ilike(like), Course.code.ilike(like), Course.name.ilike(like), Exam.id == num))
            .all()
        )
        results["bookings"] = (
            Booking.query.join(Student).join(User)
            .filter(or_(Booking.id == num, User.name.ilike(like), Student.roll_number.ilike(like)))
            .order_by(Booking.booked_at.desc())
            .all()
        )
    return render_template("admin/search.html", q=q, results=results)


# ---------- Statistics ----------


@admin_bp.route("/stats")
@role_required("admin")
def stats():
    exams = Exam.query.order_by(Exam.id).all()
    examiners = Examiner.query.filter_by(status="Approved").all()
    chart = {
        "exams": [[e.course.code, e.name] for e in exams],  # two-line axis labels
        "booked": [len(e.active_bookings) for e in exams],
        "evaluated": [sum(1 for b in e.active_bookings if b.status == "Completed") for e in exams],
        "examiners": [e.user.name for e in examiners],
        "workload": [sum(len(s.active_bookings) for s in e.slots) for e in examiners],
        "status_labels": EXAM_STATUSES,
        "status_counts": [sum(1 for e in exams if e.status == s) for s in EXAM_STATUSES],
    }
    avg_marks = db.session.query(func.avg(Evaluation.total_marks)).scalar()
    return render_template("admin/stats.html", chart=chart, avg_marks=avg_marks)


def course_to_form(course):
    return {
        "code": course.code,
        "name": course.name,
        "description": course.description,
        "credits": course.credits,
        "status": course.status or "Active",
    }


def exam_to_form(exam):
    form = {
        "course_id": exam.course_id,
        "name": exam.name,
        "exam_type": exam.exam_type,
        "description": exam.description,
        "duration": exam.duration,
        "max_marks": exam.max_marks,
    }
    for field in ("slot_creation_start", "slot_creation_end", "booking_start", "booking_end"):
        value = getattr(exam, field)
        form[field] = value.isoformat() if value else ""
    return form
