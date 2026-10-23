from flask import Blueprint, abort, jsonify
from flask_login import current_user, login_required

from controllers import role_required
from controllers.student import VISIBLE_STATUSES
from models import Booking, Exam, Examiner, Student, db

api_bp = Blueprint("api", __name__)


@api_bp.route("/exams")
@login_required
def exams():
    query = Exam.query
    if current_user.role == "student":
        query = query.filter(Exam.status.in_(VISIBLE_STATUSES))
    return jsonify([e.to_dict() for e in query.order_by(Exam.id)])


@api_bp.route("/exams/<int:exam_id>")
@login_required
def exam(exam_id):
    exam = db.get_or_404(Exam, exam_id)
    data = exam.to_dict()
    data["rubrics"] = [
        {"criterion": r.criterion, "max_marks": r.max_marks, "weightage": r.weightage} for r in exam.rubrics
    ]
    data["slots"] = [s.to_dict() for s in exam.slots if s.status != "Cancelled"]
    return jsonify(data)


@api_bp.route("/examiners")
@role_required("admin")
def examiners():
    return jsonify(
        [
            {
                "id": e.id,
                "name": e.user.name,
                "email": e.user.email,
                "department": e.department,
                "status": e.status,
                "assigned_exams": [x.id for x in e.exams],
            }
            for e in Examiner.query.order_by(Examiner.id)
        ]
    )


@api_bp.route("/students")
@role_required("admin")
def students():
    return jsonify(
        [
            {"id": s.id, "roll_number": s.roll_number, "name": s.user.name, "email": s.user.email, "program": s.program}
            for s in Student.query.order_by(Student.id)
        ]
    )


@api_bp.route("/bookings")
@login_required
def bookings():
    """Admin sees every booking, examiners see their slots' bookings, students see their own."""
    if current_user.role == "admin":
        items = Booking.query.order_by(Booking.booked_at.desc()).all()
    elif current_user.role == "student":
        items = current_user.student.bookings
    elif current_user.examiner.is_approved:
        items = [b for s in current_user.examiner.slots for b in s.bookings]
    else:
        abort(403)
    return jsonify([b.to_dict() for b in items])
