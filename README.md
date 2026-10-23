# Examination Management Portal (EMP)

MAD I project (Sep 2026 term): a Flask web app where the Admin, Examiners and Students manage
examinations, examiner slots, bookings and rubric-based evaluation.

**Stack:** Flask, Flask-SQLAlchemy (SQLite), Flask-Login, Jinja2, Bootstrap 5.
Every core feature is server-side Flask + HTML forms; JavaScript is used only for the optional
Chart.js statistics page and Bootstrap's navbar/alert widgets.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Open http://127.0.0.1:5000. On first start the app creates `instance/emp.db` from the models and
adds the admin account: **`admin` / `admin123`**. Examiners and students register from the login page.

To use a different database file (e.g. a throwaway demo copy), set `DATABASE_URL`, for example
`DATABASE_URL=sqlite:////tmp/demo.db .venv/bin/python app.py`.

## Demo flow

1. **Admin** creates a course, then an examination with its timeline dates.
2. Admin adds rubric criteria (weightages must total 100%), assigns examiners, clicks **Open slot creation**.
3. **Examiner** registers, gets approved by the admin, then creates slots for the assigned exam.
4. Admin clicks **Open student booking**. **Students** book a slot (one per exam, capacity enforced,
   slot auto-closes when full) and can cancel until the booking end date.
5. Admin can **reschedule** a booking or **change a slot's examiner**, then **Close booking**.
6. Examiner evaluates each booked student per rubric criterion and adds remarks.
7. Admin **publishes results** and marks the exam **Completed**. Students see marks, feedback and
   can download a scorecard.

## Exam lifecycle and rules

`Draft → Slot Creation → Booking Open → Closed → Completed`

- Examiners create slots only while the status is *Slot Creation* **and** today is inside the slot creation dates.
- Students book/cancel only while the status is *Booking Open* **and** today is inside the booking dates.
- A slot must be on or after the booking end date; its end time = start + exam duration.
- Examiners can edit/remove slots only before booking opens, and can evaluate only their own slots' students.
- Evaluation opens once the slot has started or the admin has closed booking.
- Total marks = Σ (criterion marks ÷ criterion max × weightage) × exam max marks ÷ 100.
- Cancelled and rescheduled bookings are kept (status *Cancelled* with a note) so history is complete.

## Tests

```bash
.venv/bin/python -m unittest -v
```

`tests/test_app.py` drives the app through HTTP the way a user would, on a fresh temporary database each
time (your `instance/emp.db` is never touched). It covers login/registration, examiner approval, role-based
access for every page, the full exam lifecycle (slots, booking, capacity, duplicates, cancellation,
rescheduling, evaluation, publishing, scorecard), the timeline and clash rules, validation messages,
the JSON API, and a crawl that opens every link each role can see.

## Structure

```
app.py                 app factory, DB creation, admin seeding
models.py              User, Examiner, Student, Course, Exam, Rubric, Slot, Booking, Evaluation, RubricScore
controllers/
  __init__.py          role_required decorator, form validation helper
  auth.py              login, register, approval-pending page, profile
  admin.py             courses, exams, rubrics, examiners, students, slots, bookings, search, stats
  examiner.py          dashboard, slots, evaluation
  student.py           dashboard, exam search, booking, history, schedule, results, scorecard
  api.py               JSON API
tests/test_app.py      smoke tests (unittest)
templates/             Jinja2 templates (admin/, examiner/, student/)
static/style.css       glassmorphism theme
```

## API (JSON, login required)

| Endpoint | Access | Returns |
|---|---|---|
| `GET /api/exams` | any user (students: published exams only) | examinations |
| `GET /api/exams/<id>` | any user | one exam with rubric and slots |
| `GET /api/examiners` | admin | examiners and their assignments |
| `GET /api/students` | admin | students |
| `GET /api/bookings` | admin: all · examiner: own slots · student: own | bookings |
