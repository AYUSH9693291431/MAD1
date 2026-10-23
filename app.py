import os

from flask import Flask, redirect, render_template, url_for
from flask_login import LoginManager, current_user

from models import User, db

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    # Default file is instance/emp.db; DATABASE_URL lets a separate demo/test database be used.
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///emp.db")

    db.init_app(app)

    login_manager = LoginManager(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from controllers.admin import admin_bp
    from controllers.api import api_bp
    from controllers.auth import auth_bp
    from controllers.examiner import examiner_bp
    from controllers.student import student_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(examiner_bp, url_prefix="/examiner")
    app.register_blueprint(student_bp, url_prefix="/student")
    app.register_blueprint(api_bp, url_prefix="/api")

    error_pages = {
        403: ("Access denied", "Your account doesn't have permission to open this page."),
        404: ("Page not found", "The page you're looking for doesn't exist or was removed."),
        500: ("Something went wrong", "An unexpected error occurred. Please try again."),
    }

    def render_error(error):
        code = getattr(error, "code", 500)
        title, message = error_pages[code]
        return render_template("error.html", code=code, title=title, message=message), code

    for code in error_pages:
        app.register_error_handler(code, render_error)

    @app.route("/")
    def index():
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        return redirect(url_for(f"{current_user.role}.dashboard"))

    with app.app_context():
        db.create_all()
        if not User.query.filter_by(role="admin").first():
            admin = User(username=ADMIN_USERNAME, email="admin@emp.local", name="Administrator", role="admin")
            admin.set_password(ADMIN_PASSWORD)
            db.session.add(admin)
            db.session.commit()

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
