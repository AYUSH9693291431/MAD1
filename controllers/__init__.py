import re
from datetime import datetime
from functools import wraps

from flask import abort, redirect, url_for
from flask_login import current_user, login_required


def role_required(role):
    """Allow only logged-in users of the given role. Examiners must also be approved."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role != role:
                abort(403)
            if role == "examiner" and not current_user.examiner.is_approved:
                return redirect(url_for("auth.pending"))
            return view(*args, **kwargs)

        return wrapper

    return decorator


class FormErrors(list):
    """Collects validation messages while parsing a submitted form."""

    def text(self, form, field, label, required=True, max_len=255):
        value = form.get(field, "").strip()
        if required and not value:
            self.append(f"{label} is required.")
        elif len(value) > max_len:
            self.append(f"{label} must be at most {max_len} characters.")
        return value

    def number(self, form, field, label, minimum=None, maximum=None, integer=False):
        raw = form.get(field, "").strip()
        try:
            value = int(raw) if integer else float(raw)
        except ValueError:
            self.append(f"{label} must be a {'whole ' if integer else ''}number.")
            return None
        if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
            self.append(f"{label} must be between {minimum} and {maximum}.")
            return None
        return value

    def date(self, form, field, label):
        try:
            return datetime.strptime(form.get(field, ""), "%Y-%m-%d").date()
        except ValueError:
            self.append(f"{label} must be a valid date.")
            return None

    def time(self, form, field, label):
        try:
            return datetime.strptime(form.get(field, ""), "%H:%M").time()
        except ValueError:
            self.append(f"{label} must be a valid time.")
            return None

    def pattern(self, form, field, label, regex, hint, required=True):
        value = form.get(field, "").strip()
        if not value and not required:
            return value
        if not re.fullmatch(regex, value):
            self.append(f"{label} {hint}")
        return value
