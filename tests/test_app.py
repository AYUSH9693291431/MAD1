"""Smoke tests for the whole app, driven through HTTP like a real user.

Run from the project folder:  .venv/bin/python -m unittest -v
Each test gets a brand-new temporary SQLite database, so the real instance/emp.db is never touched.
"""

import os
import re
import tempfile
import unittest
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

# Point the module-level app at a throwaway database before app.py is imported.
_tmp = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}/import.db"

from app import create_app  # noqa: E402
from models import Booking, Slot, User, db  # noqa: E402

TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)


class LinkParser(HTMLParser):
    """Collects internal GET links (<a href="/...">) from a page."""

    def __init__(self):
        super().__init__()
        self.links = set()

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get("href", "")
        if tag == "a" and href.startswith("/") and not href.startswith("/logout"):
            self.links.add(href)


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.db_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_dir.name}/test.db"
        self.app = create_app()
        self.app.testing = True
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.db_dir.cleanup()

    # ---------- helpers ----------

    def get(self, url, status=200, contains=None):
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status, f"GET {url}: {resp.data[:300]!r}")
        if contains:
            self.assertIn(contains, resp.get_data(as_text=True), f"GET {url}")
        return resp

    def post(self, url, data=None, status=302, contains=None, follow=False):
        resp = self.client.post(url, data=data or {}, follow_redirects=follow)
        self.assertEqual(resp.status_code, status, f"POST {url}: {resp.data[:300]!r}")
        if contains:
            self.assertIn(contains, resp.get_data(as_text=True), f"POST {url}")
        return resp

    def login(self, username, password="secret1"):
        self.client.get("/logout")
        self.post("/login", {"username": username, "password": password}, status=302)

    def register(self, username, role, **extra):
        data = dict(
            username=username, name=username.title(), email=f"{username}@example.com", phone="9876543210",
            password="secret1", confirm_password="secret1", role=role,
        )
        data.update(extra)
        return self.client.post("/register", data=data)

    def build_scenario(self):
        """Admin + 2 approved examiners + 3 students + a course + an exam open for booking with 2 slots."""
        self.register("prof", "examiner", department="CSE")
        self.register("prof2", "examiner", department="EE")
        for name, roll in [("asha", "21F001"), ("ben", "21F002"), ("chen", "21F003")]:
            self.register(name, "student", roll_number=roll)
        self.login("admin", "admin123")
        self.post("/admin/examiners/1/status", {"status": "Approved"})
        self.post("/admin/examiners/2/status", {"status": "Approved"})
        self.post("/admin/courses/new", {"code": "CS2003", "name": "MAD I", "status": "Active"})
        self.post("/admin/exams/new", self.exam_form())
        self.post("/admin/exams/1/rubrics/new", {"criterion": "Code", "max_marks": "10", "weightage": "60"})
        self.post("/admin/exams/1/rubrics/new", {"criterion": "Viva", "max_marks": "20", "weightage": "40"})
        self.post("/admin/exams/1/examiners", {"examiner_ids": ["1", "2"]})
        self.post("/admin/exams/1/status", {"action": "open_slot_creation"})
        self.login("prof")
        self.post("/examiner/exams/1/slots/new", self.slot_form("10:00", capacity="1"))
        self.login("prof2")
        self.post("/examiner/exams/1/slots/new", self.slot_form("10:00", capacity="2"))
        self.login("admin", "admin123")
        self.post("/admin/exams/1/status", {"action": "open_booking"})

    @staticmethod
    def exam_form(**overrides):
        form = dict(
            course_id="1", name="Project Viva", exam_type="Viva", duration="15", max_marks="100",
            slot_creation_start=TODAY.isoformat(), slot_creation_end=TODAY.isoformat(),
            booking_start=TODAY.isoformat(), booking_end=TODAY.isoformat(),
        )
        form.update(overrides)
        return form

    @staticmethod
    def slot_form(start, capacity="1", day=TOMORROW):
        return {"date": day.isoformat(), "start_time": start, "capacity": capacity, "venue": "Lab 1"}

    def slot(self, slot_id):
        with self.app.app_context():
            return db.session.get(Slot, slot_id).status

    def booking(self, booking_id):
        with self.app.app_context():
            b = db.session.get(Booking, booking_id)
            return b.status, b.slot_id


class AuthTests(AppTestCase):
    def test_admin_exists_and_pages_render(self):
        self.get("/", status=302)
        self.get("/login", contains="Sign in")
        self.get("/register?role=student", contains="Register as student")
        self.get("/register?role=examiner", contains="Register as examiner")
        self.post("/login", {"username": "admin", "password": "wrong"}, status=200, contains="Invalid username")
        self.login("admin", "admin123")
        self.get("/admin/", contains="Admin Dashboard")

    def test_nobody_can_register_as_admin(self):
        self.register("sneaky", "admin", roll_number="21F777")
        with self.app.app_context():
            self.assertEqual(User.query.filter_by(username="sneaky").one().role, "student")
            self.assertEqual(User.query.filter_by(role="admin").count(), 1)

    def test_registration_validation(self):
        resp = self.register("ab", "student", roll_number="1", email="bad", password="123", confirm_password="123")
        text = resp.get_data(as_text=True)
        for message in ["Username must be", "valid email", "Roll number", "at least 6"]:
            self.assertIn(message, text)
        self.assertEqual(self.register("asha", "student", roll_number="21F001").status_code, 302)
        self.assertIn("already taken", self.register("asha", "student", roll_number="21F009").get_data(as_text=True))
        self.assertIn("already registered", self.register("asha2", "student", roll_number="21F001").get_data(as_text=True))
        mismatch = self.register("dev", "student", roll_number="21F005", confirm_password="other1")
        self.assertIn("do not match", mismatch.get_data(as_text=True))

    def test_examiner_needs_approval_and_can_be_deactivated(self):
        self.register("prof", "examiner", department="CSE")
        self.login("prof")
        self.client.get("/examiner/")
        self.get("/pending", contains="Waiting for admin approval")
        self.get("/examiner/", status=302)
        self.login("admin", "admin123")
        self.post("/admin/examiners/1/status", {"status": "Approved"})
        self.login("prof")
        self.get("/examiner/", contains="Welcome")
        self.login("admin", "admin123")
        self.post("/admin/examiners/1/status", {"status": "Deactivated"})
        self.login("prof")
        self.get("/examiner/", status=302)
        self.get("/pending", contains="deactivated")

    def test_profile_update(self):
        self.register("asha", "student", roll_number="21F001")
        self.login("asha")
        self.post("/profile", {"name": "Asha R", "email": "asha@new.com", "phone": "", "program": "BS"},
                  status=200, follow=True, contains="Profile updated")
        self.post("/profile", {"name": "Asha R", "email": "asha@new.com", "password": "newpass", "confirm_password": "newpass"})
        self.login("asha", "newpass")
        self.get("/student/", contains="Asha R")


class AccessControlTests(AppTestCase):
    ADMIN_URLS = ["/admin/", "/admin/courses", "/admin/exams", "/admin/examiners", "/admin/students", "/admin/slots",
                  "/admin/bookings", "/admin/search?q=a", "/admin/stats", "/api/students", "/api/examiners"]
    EXAMINER_URLS = ["/examiner/", "/examiner/slots"]
    STUDENT_URLS = ["/student/", "/student/exams", "/student/bookings", "/student/schedule", "/student/results"]

    def test_anonymous_is_sent_to_login(self):
        for url in self.ADMIN_URLS + self.EXAMINER_URLS + self.STUDENT_URLS + ["/profile", "/api/exams"]:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, url)
            self.assertIn("/login", resp.headers["Location"], url)

    def test_roles_cannot_cross_over(self):
        self.build_scenario()
        self.login("asha")
        for url in self.ADMIN_URLS + self.EXAMINER_URLS:
            self.get(url, status=403)
        self.login("prof")
        for url in self.ADMIN_URLS + self.STUDENT_URLS:
            self.get(url, status=403)
        self.login("admin", "admin123")
        for url in self.EXAMINER_URLS + self.STUDENT_URLS:
            self.get(url, status=403)

    def test_examiner_only_touches_own_slots(self):
        self.build_scenario()
        self.login("asha")
        self.post("/student/slots/2/book")          # asha books prof2's slot
        self.login("prof")
        self.get("/examiner/slots/2", status=403)
        self.post("/examiner/slots/2/delete", status=403)
        self.get("/examiner/bookings/1/evaluate", status=403)

    def test_student_only_touches_own_bookings(self):
        self.build_scenario()
        self.login("asha")
        self.post("/student/slots/2/book")
        self.login("ben")
        self.post("/student/bookings/1/cancel", status=403)
        self.get("/student/results/1", status=403)


class ExamLifecycleTests(AppTestCase):
    def test_full_lifecycle(self):
        self.build_scenario()
        # Booking rules: capacity, duplicates, auto-close, cancel and rebook.
        self.login("asha")
        self.get("/student/", contains="Available examination slots")
        self.assertIn("/student/slots/1/book", self.get("/student/").get_data(as_text=True))
        self.get("/student/exams?q=viva&open=1", contains="Project Viva")
        self.post("/student/slots/1/book")
        self.assertEqual(self.slot(1), "Full")
        self.post("/student/slots/2/book", follow=True, status=200, contains="already have a booking")
        self.login("ben")
        self.post("/student/slots/1/book", follow=True, status=200, contains="full")
        self.post("/student/slots/2/book")
        self.login("asha")
        self.post("/student/bookings/1/cancel", follow=True, status=200, contains="Booking cancelled")
        self.assertEqual(self.slot(1), "Available")
        self.post("/student/slots/1/book")
        self.get("/student/bookings", contains="Cancelled by student")

        # Admin: reschedule asha (booking 3, slot 1) into slot 2, then change slot 1's examiner.
        self.login("admin", "admin123")
        self.post("/admin/bookings/3/reschedule", {"slot_id": "2"})
        self.assertEqual(self.booking(3)[0], "Cancelled")
        self.assertEqual(self.booking(4), ("Booked", 2))
        self.assertEqual(self.slot(2), "Full")
        self.get("/admin/slots/1/examiner", contains="Change assigned examiner")
        self.post("/admin/exams/1/status", {"action": "close_booking"})

        # Evaluation: only booked students in the examiner's own slot; marks are range-checked.
        self.login("prof2")
        self.post("/examiner/bookings/2/evaluate", {"rubric_1": "11", "rubric_2": "5"}, status=200, contains="between")
        self.post("/examiner/bookings/2/evaluate", {"rubric_1": "8", "rubric_2": "15", "remarks": "Good"},
                  follow=True, status=200, contains="78 / 100")
        self.post("/examiner/bookings/4/evaluate", {"rubric_1": "10", "rubric_2": "20"})
        self.login("prof")
        self.get("/examiner/bookings/1/evaluate", status=302)   # cancelled booking can't be evaluated

        # Results stay hidden until published.
        self.login("ben")
        self.get("/student/results", contains="Not published yet")
        self.get("/student/results/2", status=404)
        self.login("admin", "admin123")
        self.post("/admin/exams/1/results", {"publish": "1"})
        self.post("/admin/exams/1/status", {"action": "complete"})
        self.assertEqual(self.slot(2), "Completed")
        self.login("prof2")
        self.get("/examiner/bookings/2/evaluate", status=302)   # locked after publishing
        self.login("ben")
        self.get("/student/", contains="78 / 100")
        self.get("/student/results/2", contains="Good")
        card = self.get("/student/results/2/scorecard", contains="Total: 78 / 100")
        self.assertIn("attachment", card.headers["Content-Disposition"])

    def test_status_transitions_are_guarded(self):
        self.login("admin", "admin123")
        self.post("/admin/courses/new", {"code": "CS1", "name": "C"})
        self.post("/admin/exams/new", self.exam_form())
        self.post("/admin/exams/1/status", {"action": "complete"}, follow=True, status=200, contains="isn&#39;t allowed")
        self.post("/admin/exams/1/status", {"action": "open_slot_creation"})
        self.post("/admin/exams/1/status", {"action": "open_booking"}, follow=True, status=200, contains="slots yet")
        self.post("/admin/exams/1/results", {"publish": "1"}, follow=True, status=200, contains="only after booking")


class RuleTests(AppTestCase):
    def test_slot_rules(self):
        self.build_scenario()
        self.login("admin", "admin123")
        self.post("/admin/exams/new", self.exam_form(name="Second"))
        self.post("/admin/exams/2/examiners", {"examiner_ids": ["1"]})
        self.login("prof")
        self.post("/examiner/exams/2/slots/new", self.slot_form("11:00"), follow=True, status=200,
                  contains="isn&#39;t open")                           # still Draft
        self.login("admin", "admin123")
        self.post("/admin/exams/2/status", {"action": "open_slot_creation"})
        self.login("prof")
        self.post("/examiner/exams/2/slots/new", self.slot_form("10:05"), status=200, contains="overlaps")
        self.post("/examiner/exams/2/slots/new", self.slot_form("11:00", day=TODAY - timedelta(days=1)),
                  status=200, contains="must be on or after")
        self.post("/examiner/exams/2/slots/new", self.slot_form("23:55"), status=200, contains="same day")
        self.post("/examiner/exams/2/slots/new", self.slot_form("11:00"))
        self.post("/examiner/slots/3/edit", self.slot_form("12:00", capacity="3"))
        self.post("/examiner/slots/1/edit", self.slot_form("13:00"), follow=True, status=200,
                  contains="only before booking opens")                 # exam 1 already open for booking
        self.post("/examiner/slots/3/delete", follow=True, status=200, contains="Slot removed")
        self.login("ben")                                                 # exam 2 not visible to students yet
        self.get("/student/exams/2", status=404)

    def test_student_clash_and_deadline(self):
        self.build_scenario()
        self.login("admin", "admin123")
        self.post("/admin/examiners/new", dict(username="prof3", name="Prof Three", email="p3@example.com",
                                               password="secret1", confirm_password="secret1", department="ME"))
        self.post("/admin/exams/new", self.exam_form(name="Clashing"))
        self.post("/admin/exams/2/rubrics/new", {"criterion": "All", "max_marks": "10", "weightage": "100"})
        self.post("/admin/exams/2/examiners", {"examiner_ids": ["1", "3"]})
        self.post("/admin/exams/2/status", {"action": "open_slot_creation"})
        self.login("prof")
        self.post("/examiner/exams/2/slots/new", self.slot_form("15:00"))                   # slot 3
        self.post("/examiner/slots/3/edit", self.slot_form("10:05"), status=200, contains="overlaps")
        self.login("prof3")
        self.post("/examiner/exams/2/slots/new", self.slot_form("10:05"))                   # slot 4
        self.login("admin", "admin123")
        self.post("/admin/exams/2/status", {"action": "open_booking"})
        self.login("asha")
        self.post("/student/slots/2/book")                                                   # 10:00-10:15
        self.post("/student/slots/4/book", follow=True, status=200, contains="clashes")
        self.post("/student/slots/3/book")
        self.login("admin", "admin123")
        self.post("/admin/exams/1/status", {"action": "close_booking"})
        self.login("asha")
        self.post("/student/bookings/1/cancel", follow=True, status=200, contains="deadline has passed")

    def test_started_slot_cannot_be_booked_or_cancelled(self):
        self.build_scenario()
        self.login("asha")
        self.post("/student/slots/2/book")
        with self.app.app_context():
            for slot_id in (1, 2):   # move both slots to "started a minute ago"
                slot = db.session.get(Slot, slot_id)
                started = datetime.now() - timedelta(minutes=1)
                slot.date, slot.start_time = started.date(), started.time().replace(microsecond=0)
            db.session.commit()
        self.post("/student/bookings/1/cancel", follow=True, status=200, contains="deadline has passed")
        self.login("ben")
        self.post("/student/slots/1/book", follow=True, status=200, contains="already started")

    def test_deactivated_examiner_does_not_block_assignment(self):
        self.build_scenario()
        self.login("admin", "admin123")
        self.post("/admin/examiners/1/status", {"status": "Deactivated"})   # prof's slot has no bookings
        self.post("/admin/exams/1/examiners", {"examiner_ids": ["2"]}, follow=True, status=200,
                  contains="Assigned examiners updated")
        self.post("/admin/exams/1/examiners", {"examiner_ids": []}, follow=True, status=200,
                  contains="already has slots")                              # prof2 still has a live slot

    def test_error_pages(self):
        self.get("/nope", status=404, contains="Page not found")
        self.register("asha", "student", roll_number="21F001")
        self.login("asha")
        self.get("/admin/", status=403, contains="Access denied")

    def test_admin_validation(self):
        self.login("admin", "admin123")
        self.post("/admin/courses/new", {"code": "cs2003", "name": "MAD I"})
        self.post("/admin/courses/new", {"code": "CS2003", "name": "Dup"}, status=200, contains="already exists")
        self.post("/admin/courses/new", {"code": "!", "name": ""}, status=200, contains="Course name is required")
        bad_dates = self.exam_form(booking_end=(TODAY - timedelta(days=2)).isoformat())
        self.post("/admin/exams/new", bad_dates, status=200, contains="Booking must end")
        self.post("/admin/exams/new", self.exam_form(duration="1", max_marks="x"), status=200, contains="Duration")
        self.post("/admin/exams/new", self.exam_form())
        self.post("/admin/exams/1/rubrics/new", {"criterion": "A", "max_marks": "10", "weightage": "70"})
        self.post("/admin/exams/1/rubrics/new", {"criterion": "B", "max_marks": "10", "weightage": "40"},
                  status=200, contains="can&#39;t exceed 100%")
        self.post("/admin/rubrics/1/edit", {"criterion": "A2", "max_marks": "5", "weightage": "100"})
        self.get("/admin/exams/1", contains="A2")
        self.post("/admin/rubrics/1/delete")
        self.post("/admin/courses/1/delete", follow=True, status=200, contains="Inactive instead")
        self.post("/admin/exams/1/delete")
        self.post("/admin/courses/1/delete", follow=True, status=200, contains="removed")

    def test_admin_adds_examiner_and_search(self):
        self.login("admin", "admin123")
        data = dict(username="newprof", name="New Prof", email="np@example.com", password="secret1",
                    confirm_password="secret1", department="Maths")
        self.post("/admin/examiners/new", data)
        self.get("/admin/examiners?status=Approved", contains="New Prof")
        self.login("newprof")
        self.get("/examiner/", contains="Welcome")
        self.login("admin", "admin123")
        self.get("/admin/search?q=new", contains="New Prof")
        self.get("/admin/search?q=zzz", contains="No examinations match")

    def test_inactive_course_hides_exam(self):
        self.build_scenario()
        self.login("admin", "admin123")
        self.post("/admin/courses/1/edit", {"code": "CS2003", "name": "MAD I", "status": "Inactive"})
        self.login("asha")
        self.get("/student/exams/1", status=404)
        self.get("/student/exams", contains="No examinations match")


class ApiTests(AppTestCase):
    def test_api_per_role(self):
        self.build_scenario()
        self.login("asha")
        self.post("/student/slots/1/book")
        self.assertEqual(len(self.client.get("/api/exams").get_json()), 1)
        self.assertEqual(self.client.get("/api/exams/1").get_json()["slots"][0]["status"], "Full")
        self.assertEqual(len(self.client.get("/api/bookings").get_json()), 1)
        self.login("ben")
        self.assertEqual(self.client.get("/api/bookings").get_json(), [])
        self.login("prof")
        self.assertEqual(len(self.client.get("/api/bookings").get_json()), 1)
        self.login("admin", "admin123")
        self.assertEqual(len(self.client.get("/api/students").get_json()), 3)
        self.assertEqual(len(self.client.get("/api/examiners").get_json()), 2)
        self.get("/api/exams/99", status=404)


class CrawlTests(AppTestCase):
    """Follow every internal link each role can see and make sure no page errors."""

    def crawl(self, start):
        seen, queue = set(), [start]
        while queue:
            url = queue.pop()
            if url in seen:
                continue
            seen.add(url)
            resp = self.client.get(url)
            self.assertIn(resp.status_code, (200, 302), f"{url} returned {resp.status_code}")
            if resp.status_code == 200 and resp.mimetype == "text/html":
                parser = LinkParser()
                parser.feed(resp.get_data(as_text=True))
                queue.extend(re.sub(r"#.*$", "", link) for link in parser.links - seen)
        return seen

    def test_every_link_works(self):
        self.build_scenario()
        self.login("asha")
        self.post("/student/slots/1/book")
        self.login("ben")
        self.post("/student/slots/2/book")
        self.login("admin", "admin123")
        self.post("/admin/exams/1/status", {"action": "close_booking"})
        self.login("prof")
        self.post("/examiner/bookings/1/evaluate", {"rubric_1": "7", "rubric_2": "12", "remarks": "Nice"})
        self.login("admin", "admin123")
        self.post("/admin/exams/1/results", {"publish": "1"})
        visited = {}
        for user, password, start in [("admin", "admin123", "/admin/"), ("prof", "secret1", "/examiner/"),
                                      ("asha", "secret1", "/student/")]:
            self.login(user, password)
            visited[user] = self.crawl(start)
        self.assertGreater(len(visited["admin"]), 25)
        self.assertGreater(len(visited["prof"]), 4)
        self.assertGreater(len(visited["asha"]), 8)


if __name__ == "__main__":
    unittest.main()
