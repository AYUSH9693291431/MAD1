from datetime import date, datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

EXAM_TYPES = ["Viva", "Practical", "Project Demo", "Assessment"]

# Exam lifecycle, in order. Admin moves an exam along it from the exam page.
DRAFT, SLOT_CREATION, BOOKING_OPEN, CLOSED, COMPLETED = "Draft", "Slot Creation", "Booking Open", "Closed", "Completed"
EXAM_STATUSES = [DRAFT, SLOT_CREATION, BOOKING_OPEN, CLOSED, COMPLETED]

exam_examiners = db.Table(
    "exam_examiners",
    db.Column("exam_id", db.Integer, db.ForeignKey("exams.id"), primary_key=True),
    db.Column("examiner_id", db.Integer, db.ForeignKey("examiners.id"), primary_key=True),
)


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(15))
    role = db.Column(db.String(10), nullable=False)  # admin / examiner / student
    created_at = db.Column(db.DateTime, default=datetime.now)

    examiner = db.relationship("Examiner", back_populates="user", uselist=False, cascade="all, delete-orphan")
    student = db.relationship("Student", back_populates="user", uselist=False, cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Examiner(db.Model):
    """Examiner profile. Status is Pending until the admin approves it."""

    __tablename__ = "examiners"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    department = db.Column(db.String(100), nullable=False)
    designation = db.Column(db.String(100))
    status = db.Column(db.String(12), default="Pending", nullable=False)  # Pending / Approved / Deactivated

    user = db.relationship("User", back_populates="examiner")
    exams = db.relationship("Exam", secondary=exam_examiners, back_populates="examiners")
    slots = db.relationship("Slot", back_populates="examiner")

    @property
    def is_approved(self):
        return self.status == "Approved"


class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    roll_number = db.Column(db.String(20), unique=True, nullable=False)
    program = db.Column(db.String(100))

    user = db.relationship("User", back_populates="student")
    bookings = db.relationship("Booking", back_populates="student", order_by="Booking.booked_at.desc()")

    def active_booking_for(self, exam):
        """The student's Booked/Completed booking for an exam, if any (one per exam is allowed)."""
        return next(
            (b for b in self.bookings if b.status != "Cancelled" and b.slot.exam_id == exam.id),
            None,
        )


class Course(db.Model):
    __tablename__ = "courses"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    credits = db.Column(db.Integer)
    status = db.Column(db.String(10), default="Active", nullable=False)  # Active / Inactive

    exams = db.relationship("Exam", back_populates="course")


class Exam(db.Model):
    __tablename__ = "exams"

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    exam_type = db.Column(db.String(20), nullable=False)
    description = db.Column(db.Text)
    duration = db.Column(db.Integer, nullable=False)  # minutes
    max_marks = db.Column(db.Float, nullable=False)
    slot_creation_start = db.Column(db.Date, nullable=False)
    slot_creation_end = db.Column(db.Date, nullable=False)
    booking_start = db.Column(db.Date, nullable=False)
    booking_end = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(15), default=DRAFT, nullable=False)
    results_published = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

    course = db.relationship("Course", back_populates="exams")
    rubrics = db.relationship("Rubric", back_populates="exam", cascade="all, delete-orphan", order_by="Rubric.id")
    slots = db.relationship("Slot", back_populates="exam", order_by="[Slot.date, Slot.start_time]")
    examiners = db.relationship("Examiner", secondary=exam_examiners, back_populates="exams")

    @property
    def slot_creation_open(self):
        return self.status == SLOT_CREATION and self.slot_creation_start <= date.today() <= self.slot_creation_end

    @property
    def booking_open(self):
        return (
            self.status == BOOKING_OPEN
            and self.course.status == "Active"
            and self.booking_start <= date.today() <= self.booking_end
        )

    @property
    def total_weightage(self):
        return sum(r.weightage for r in self.rubrics)

    @property
    def rubric_ready(self):
        return bool(self.rubrics) and abs(self.total_weightage - 100) < 0.01

    @property
    def active_bookings(self):
        return [b for s in self.slots for b in s.bookings if b.status != "Cancelled"]

    def to_dict(self):
        return {
            "id": self.id,
            "course": self.course.code,
            "name": self.name,
            "type": self.exam_type,
            "duration_minutes": self.duration,
            "max_marks": self.max_marks,
            "slot_creation": [self.slot_creation_start.isoformat(), self.slot_creation_end.isoformat()],
            "booking": [self.booking_start.isoformat(), self.booking_end.isoformat()],
            "status": self.status,
            "results_published": self.results_published,
        }


class Rubric(db.Model):
    __tablename__ = "rubrics"

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id"), nullable=False)
    criterion = db.Column(db.String(120), nullable=False)
    max_marks = db.Column(db.Float, nullable=False)
    weightage = db.Column(db.Float, nullable=False)  # percent of the exam's max marks
    description = db.Column(db.Text)

    exam = db.relationship("Exam", back_populates="rubrics")
    scores = db.relationship("RubricScore", back_populates="rubric")


class Slot(db.Model):
    __tablename__ = "slots"

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id"), nullable=False)
    examiner_id = db.Column(db.Integer, db.ForeignKey("examiners.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    capacity = db.Column(db.Integer, nullable=False)
    venue = db.Column(db.String(120))
    status = db.Column(db.String(10), default="Available", nullable=False)  # Available / Full / Cancelled / Completed

    exam = db.relationship("Exam", back_populates="slots")
    examiner = db.relationship("Examiner", back_populates="slots")
    bookings = db.relationship("Booking", back_populates="slot", order_by="Booking.booked_at")

    @property
    def start(self):
        return datetime.combine(self.date, self.start_time)

    @property
    def end(self):
        return datetime.combine(self.date, self.end_time)

    @property
    def has_started(self):
        return datetime.now() >= self.start

    @property
    def active_bookings(self):
        return [b for b in self.bookings if b.status != "Cancelled"]

    @property
    def available_seats(self):
        return self.capacity - len(self.active_bookings)

    def refresh_status(self):
        """Close the slot when it is full, reopen it when a seat frees up."""
        if self.status in ("Available", "Full"):
            self.status = "Full" if self.available_seats <= 0 else "Available"

    def overlaps(self, other):
        return self.date == other.date and self.start_time < other.end_time and other.start_time < self.end_time

    def to_dict(self):
        return {
            "id": self.id,
            "exam_id": self.exam_id,
            "examiner": self.examiner.user.name,
            "date": self.date.isoformat(),
            "start_time": self.start_time.strftime("%H:%M"),
            "end_time": self.end_time.strftime("%H:%M"),
            "capacity": self.capacity,
            "available_seats": self.available_seats,
            "status": self.status,
        }


class Booking(db.Model):
    __tablename__ = "bookings"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    slot_id = db.Column(db.Integer, db.ForeignKey("slots.id"), nullable=False)
    booked_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    status = db.Column(db.String(10), default="Booked", nullable=False)  # Booked / Cancelled / Completed
    cancelled_at = db.Column(db.DateTime)
    note = db.Column(db.String(255))  # e.g. why it was cancelled or rescheduled

    student = db.relationship("Student", back_populates="bookings")
    slot = db.relationship("Slot", back_populates="bookings")
    evaluation = db.relationship("Evaluation", back_populates="booking", uselist=False)

    @property
    def can_be_evaluated(self):
        """Evaluation opens once the slot has started, or once the admin closes booking."""
        return self.status == "Booked" and (
            datetime.now() >= self.slot.start or self.slot.exam.status in (CLOSED, COMPLETED)
        )

    def to_dict(self):
        return {
            "id": self.id,
            "student": self.student.roll_number,
            "exam": self.slot.exam.name,
            "slot": self.slot.to_dict(),
            "booked_at": self.booked_at.isoformat(timespec="seconds"),
            "status": self.status,
            "note": self.note,
        }


class Evaluation(db.Model):
    __tablename__ = "evaluations"

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"), unique=True, nullable=False)
    examiner_id = db.Column(db.Integer, db.ForeignKey("examiners.id"), nullable=False)
    total_marks = db.Column(db.Float, nullable=False)
    remarks = db.Column(db.Text)
    evaluated_at = db.Column(db.DateTime, default=datetime.now)

    booking = db.relationship("Booking", back_populates="evaluation")
    examiner = db.relationship("Examiner")
    scores = db.relationship("RubricScore", back_populates="evaluation", cascade="all, delete-orphan")


class RubricScore(db.Model):
    """Marks given for one rubric criterion in one evaluation."""

    __tablename__ = "rubric_scores"

    id = db.Column(db.Integer, primary_key=True)
    evaluation_id = db.Column(db.Integer, db.ForeignKey("evaluations.id"), nullable=False)
    rubric_id = db.Column(db.Integer, db.ForeignKey("rubrics.id"), nullable=False)
    marks = db.Column(db.Float, nullable=False)

    evaluation = db.relationship("Evaluation", back_populates="scores")
    rubric = db.relationship("Rubric", back_populates="scores")
