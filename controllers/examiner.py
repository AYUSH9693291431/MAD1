from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from controllers import FormErrors, role_required
from models import SLOT_CREATION, Booking, Evaluation, Exam, RubricScore, Slot, db

examiner_bp = Blueprint("examiner", __name__)


def me():
    return current_user.examiner


@examiner_bp.route("/")
@role_required("examiner")
def dashboard():
    examiner = me()
    live_slots = [s for s in examiner.slots if s.status != "Cancelled"]
    booked = [b for s in live_slots for b in s.active_bookings]
    pending = sorted((b for b in booked if b.status == "Booked"), key=lambda b: b.slot.start)
    upcoming = sorted((s for s in live_slots if s.end >= datetime.now()), key=lambda s: s.start)
    stats = {
        "Assigned exams": len(examiner.exams),
        "Slots created": len(live_slots),
        "Students booked": len(booked),
        "Pending evaluations": len(pending),
    }
    return render_template(
        "examiner/dashboard.html", exams=examiner.exams, upcoming=upcoming[:6], pending=pending, stats=stats
    )


@examiner_bp.route("/slots")
@role_required("examiner")
def slots():
    my_slots = sorted(me().slots, key=lambda s: s.start, reverse=True)
    return render_template("examiner/slots.html", slots=my_slots)


@examiner_bp.route("/exams/<int:exam_id>/slots/new", methods=["GET", "POST"])
@examiner_bp.route("/slots/<int:slot_id>/edit", methods=["GET", "POST"])
@role_required("examiner")
def slot_form(exam_id=None, slot_id=None):
    if slot_id:
        slot = own_slot(slot_id)
        exam = slot.exam
        if exam.status != SLOT_CREATION:
            flash("Slots can be changed only before booking opens.", "danger")
            return redirect(url_for("examiner.slots"))
    else:
        exam = db.get_or_404(Exam, exam_id)
        if exam not in me().exams:
            abort(403)
        if not exam.slot_creation_open:
            flash("Slot creation isn't open for this examination right now.", "danger")
            return redirect(url_for("examiner.dashboard"))
        slot = Slot()
    if request.method == "POST":
        errors = FormErrors()
        form = request.form
        slot_date = errors.date(form, "date", "Date")
        start_time = errors.time(form, "start_time", "Start time")
        capacity = errors.number(form, "capacity", "Capacity", 1, 200, integer=True)
        venue = errors.text(form, "venue", "Venue / link", required=False, max_len=120)
        end_time = None
        if slot_date and start_time:
            start = datetime.combine(slot_date, start_time)
            end = start + timedelta(minutes=exam.duration)
            end_time = end.time()
            if slot_date < max(exam.booking_end, date.today()):
                errors.append(f"The slot must be on or after {max(exam.booking_end, date.today()):%d %b %Y} (after booking closes).")
            elif start < datetime.now():
                errors.append("The slot can't start in the past.")
            if end.date() != slot_date:
                errors.append("The slot must finish on the same day.")
        if not errors:
            candidate = Slot(date=slot_date, start_time=start_time, end_time=end_time)
            clash = next(
                (s for s in me().slots if s.id != slot.id and s.status != "Cancelled" and s.overlaps(candidate)), None
            )
            if clash:
                errors.append(f"This overlaps your slot #{clash.id} ({clash.start_time:%H:%M}-{clash.end_time:%H:%M}).")
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("examiner/slot_form.html", exam=exam, slot=slot, form=form)
        slot.date, slot.start_time, slot.end_time, slot.capacity, slot.venue = (
            slot_date, start_time, end_time, capacity, venue
        )
        if not slot.id:
            slot.exam, slot.examiner = exam, me()
            db.session.add(slot)
        db.session.commit()
        flash(f"Slot saved: {slot.date:%d %b %Y}, {slot.start_time:%H:%M}-{slot.end_time:%H:%M}.", "success")
        return redirect(url_for("examiner.slots"))
    form = {
        "date": slot.date.isoformat() if slot.date else "",
        "start_time": slot.start_time.strftime("%H:%M") if slot.start_time else "",
        "capacity": slot.capacity or 1,
        "venue": slot.venue,
    }
    return render_template("examiner/slot_form.html", exam=exam, slot=slot, form=form)


@examiner_bp.route("/slots/<int:slot_id>/delete", methods=["POST"])
@role_required("examiner")
def delete_slot(slot_id):
    slot = own_slot(slot_id)
    if slot.exam.status != SLOT_CREATION or slot.bookings:
        flash("Slots can be removed only before booking opens.", "danger")
    else:
        db.session.delete(slot)
        db.session.commit()
        flash("Slot removed.", "success")
    return redirect(url_for("examiner.slots"))


@examiner_bp.route("/slots/<int:slot_id>")
@role_required("examiner")
def slot_detail(slot_id):
    return render_template("examiner/slot_detail.html", slot=own_slot(slot_id))


@examiner_bp.route("/bookings/<int:booking_id>/evaluate", methods=["GET", "POST"])
@role_required("examiner")
def evaluate(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    # Examiners may only evaluate students booked into their own slots.
    if booking.slot.examiner_id != me().id:
        abort(403)
    exam = booking.slot.exam
    editing = booking.status == "Completed" and booking.evaluation is not None
    if editing and exam.results_published:
        flash("Results are published, so this evaluation is locked.", "warning")
        return redirect(url_for("examiner.slot_detail", slot_id=booking.slot_id))
    if not editing and not booking.can_be_evaluated:
        flash("This booking can't be evaluated yet (the slot hasn't started, or it was cancelled).", "danger")
        return redirect(url_for("examiner.slot_detail", slot_id=booking.slot_id))
    if not exam.rubric_ready:
        flash("The admin hasn't finished this examination's rubric yet.", "danger")
        return redirect(url_for("examiner.slot_detail", slot_id=booking.slot_id))

    if request.method == "POST":
        errors = FormErrors()
        marks = {r.id: errors.number(request.form, f"rubric_{r.id}", r.criterion, 0, r.max_marks) for r in exam.rubrics}
        remarks = errors.text(request.form, "remarks", "Remarks", required=False, max_len=2000)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("examiner/evaluate.html", booking=booking, exam=exam, form=request.form)
        evaluation = booking.evaluation or Evaluation(booking=booking, examiner=me())
        evaluation.scores = [RubricScore(rubric_id=rid, marks=m) for rid, m in marks.items()]
        evaluation.total_marks = weighted_total(exam, marks)
        evaluation.remarks = remarks
        evaluation.evaluated_at = datetime.now()
        booking.status = "Completed"
        db.session.add(evaluation)
        db.session.commit()
        flash(
            f"Marks saved for {booking.student.user.name}: {evaluation.total_marks:g} / {exam.max_marks:g}.",
            "success",
        )
        return redirect(url_for("examiner.slot_detail", slot_id=booking.slot_id))

    form = {"remarks": booking.evaluation.remarks if editing else ""}
    if editing:
        form.update({f"rubric_{s.rubric_id}": s.marks for s in booking.evaluation.scores})
    return render_template("examiner/evaluate.html", booking=booking, exam=exam, form=form)


def own_slot(slot_id):
    slot = db.get_or_404(Slot, slot_id)
    if slot.examiner_id != me().id:
        abort(403)
    return slot


def weighted_total(exam, marks):
    """Each criterion contributes (marks / criterion max) of its weightage share of the exam's max marks."""
    total = sum(marks[r.id] / r.max_marks * r.weightage for r in exam.rubrics) * exam.max_marks / 100
    return round(total, 2)
