from datetime import datetime

from flask import Blueprint, abort, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_

from controllers import role_required
from models import BOOKING_OPEN, CLOSED, COMPLETED, EXAM_TYPES, Booking, Course, Exam, Slot, db

student_bp = Blueprint("student", __name__)

VISIBLE_STATUSES = [BOOKING_OPEN, CLOSED, COMPLETED]  # students don't see exams still being set up


def me():
    return current_user.student


@student_bp.route("/")
@role_required("student")
def dashboard():
    student = me()
    open_exams = [
        e for e in Exam.query.filter_by(status=BOOKING_OPEN).all()
        if e.booking_open and not student.active_booking_for(e)
    ]
    upcoming = sorted(
        (b for b in student.bookings if b.status == "Booked" and b.slot.end >= datetime.now()),
        key=lambda b: b.slot.start,
    )
    open_slots = sorted(
        (s for e in open_exams for s in e.slots if s.status == "Available" and not s.has_started),
        key=lambda s: s.start,
    )
    results = [b for b in student.bookings if b.status == "Completed" and b.slot.exam.results_published]
    return render_template(
        "student/dashboard.html", open_exams=open_exams, open_slots=open_slots, upcoming=upcoming, results=results
    )


@student_bp.route("/exams")
@role_required("student")
def exams():
    q = request.args.get("q", "").strip()
    course_id = request.args.get("course_id", type=int)
    exam_type = request.args.get("type", "")
    only_open = request.args.get("open") == "1"
    query = Exam.query.join(Course).filter(Exam.status.in_(VISIBLE_STATUSES), Course.status == "Active")
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Exam.name.ilike(like), Course.code.ilike(like), Course.name.ilike(like)))
    if course_id:
        query = query.filter(Exam.course_id == course_id)
    if exam_type in EXAM_TYPES:
        query = query.filter(Exam.exam_type == exam_type)
    exams = query.order_by(Exam.booking_end).all()
    if only_open:
        exams = [e for e in exams if e.booking_open]
    return render_template(
        "student/exams.html",
        exams=exams,
        courses=Course.query.filter_by(status="Active").order_by(Course.code).all(),
        types=EXAM_TYPES,
        q=q,
        course_id=course_id,
        exam_type=exam_type,
        only_open=only_open,
    )


@student_bp.route("/exams/<int:exam_id>")
@role_required("student")
def exam_detail(exam_id):
    exam = db.get_or_404(Exam, exam_id)
    if exam.status not in VISIBLE_STATUSES or exam.course.status != "Active":
        abort(404)
    return render_template("student/exam_detail.html", exam=exam, booking=me().active_booking_for(exam))


@student_bp.route("/slots/<int:slot_id>/book", methods=["POST"])
@role_required("student")
def book(slot_id):
    slot = db.get_or_404(Slot, slot_id)
    exam = slot.exam
    student = me()
    back = redirect(url_for("student.exam_detail", exam_id=exam.id))
    if not exam.booking_open:
        flash("Booking isn't open for this examination.", "danger")
        return back
    if student.active_booking_for(exam):
        flash("You already have a booking for this examination.", "danger")
        return back
    if slot.status != "Available" or slot.available_seats <= 0:
        flash("That slot is full. Please pick another one.", "danger")
        return back
    if slot.has_started:
        flash("That slot has already started. Please pick a later one.", "danger")
        return back
    clash = next((b for b in student.bookings if b.status == "Booked" and b.slot.overlaps(slot)), None)
    if clash:
        flash(f"This clashes with your booking for {clash.slot.exam.name} at {clash.slot.start_time:%H:%M}.", "danger")
        return back
    booking = Booking(student=student, slot=slot)
    db.session.add(booking)
    slot.refresh_status()
    db.session.commit()
    flash(f"Booked! {exam.name} on {slot.date:%d %b %Y} at {slot.start_time:%H:%M}.", "success")
    return redirect(url_for("student.bookings"))


@student_bp.route("/bookings/<int:booking_id>/cancel", methods=["POST"])
@role_required("student")
def cancel(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    if booking.student_id != me().id:
        abort(403)
    if booking.status != "Booked":
        flash("Only active bookings can be cancelled.", "danger")
    elif not booking.slot.exam.booking_open or booking.slot.has_started:
        flash("The booking deadline has passed, so this booking can't be cancelled.", "danger")
    else:
        booking.status = "Cancelled"
        booking.cancelled_at = datetime.now()
        booking.note = "Cancelled by student"
        booking.slot.refresh_status()
        db.session.commit()
        flash("Booking cancelled.", "success")
    return redirect(url_for("student.bookings"))


@student_bp.route("/bookings")
@role_required("student")
def bookings():
    return render_template("student/bookings.html", bookings=me().bookings)


@student_bp.route("/schedule")
@role_required("student")
def schedule():
    booked = sorted((b for b in me().bookings if b.status == "Booked"), key=lambda b: b.slot.start)
    return render_template("student/schedule.html", bookings=booked, now=datetime.now())


@student_bp.route("/results")
@role_required("student")
def results():
    done = [b for b in me().bookings if b.status == "Completed"]
    return render_template("student/results.html", bookings=done)


@student_bp.route("/results/<int:booking_id>")
@role_required("student")
def result_detail(booking_id):
    booking = published_result(booking_id)
    return render_template("student/result_detail.html", b=booking)


@student_bp.route("/results/<int:booking_id>/scorecard")
@role_required("student")
def scorecard(booking_id):
    booking = published_result(booking_id)
    html = render_template("student/scorecard.html", b=booking, generated=datetime.now())
    response = make_response(html)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    filename = f"scorecard_{booking.student.roll_number}_{booking.slot.exam.id}.html"
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


def published_result(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    if booking.student_id != me().id:
        abort(403)
    if booking.status != "Completed" or not booking.slot.exam.results_published:
        abort(404)
    return booking
