import os
import html
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

os.environ["APP_ENV"] = "testing"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import (  # noqa: E402
    Attempt,
    Exam,
    Question,
    StudentAnswer,
    User,
    VIT_BRANCHES,
    DIVISIONS,
    app,
    db,
    generate_password_hash,
    get_now_utc,
)
from backup_database import backup_database  # noqa: E402
from werkzeug.security import check_password_hash


class ExaminationAppTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            db.drop_all()
            db.create_all()
            self.teacher = User(
                name="Test Teacher",
                email="teacher@test.example",
                password_hash=generate_password_hash("old-password"),
                role="teacher",
            )
            self.student = User(
                name="Test Student",
                email="student@test.example",
                password_hash=generate_password_hash("student-password"),
                role="student",
                branch="Computer Engineering",
                division="A",
            )
            db.session.add_all([self.teacher, self.student])
            db.session.commit()
            self.teacher_id = self.teacher.id
            self.student_id = self.student.id
            now = get_now_utc()
            self.upcoming_exam = Exam(
                title="Cloud Foundations",
                teacher_id=self.teacher_id,
                duration_minutes=30,
                start_at=now + timedelta(hours=2),
            )
            self.available_exam = Exam(
                title="Database Basics",
                teacher_id=self.teacher_id,
                duration_minutes=30,
            )
            self.targeted_exam = Exam(
                title="CS Division A Quiz",
                teacher_id=self.teacher_id,
                duration_minutes=30,
                target_branch="Computer Engineering",
                target_division="A",
            )
            db.session.add_all([self.upcoming_exam, self.available_exam, self.targeted_exam])
            db.session.commit()
            self.upcoming_exam_id = self.upcoming_exam.id
            self.available_exam_id = self.available_exam.id
            self.targeted_exam_id = self.targeted_exam.id
        self.client = app.test_client()

    def log_in(self, user_id, role, name):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["name"] = name

    def test_student_dashboard_filters_reminders_and_overview(self):
        self.log_in(self.student_id, "student", "Test Student")
        response = self.client.get("/student/dashboard?q=Cloud&status=upcoming")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Cloud Foundations", response.data)
        self.assertNotIn(b"Database Basics", response.data)
        self.assertIn(b"Starting within 24 hours", response.data)
        self.assertIn(b"Pending results", response.data)

    def test_teacher_dashboard_search_and_status_filter(self):
        self.log_in(self.teacher_id, "teacher", "Test Teacher")
        response = self.client.get("/teacher/dashboard?status=available")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Database Basics", response.data)
        self.assertNotIn(b"Cloud Foundations", response.data)
        self.assertIn(b"Exams in progress", response.data)

    def test_account_settings_updates_name_and_password(self):
        self.log_in(self.student_id, "student", "Test Student")
        response = self.client.post(
            "/account",
            data={
                "name": "Updated Student",
                "branch": "computer engineering",
                "division": "a",
                "current_password": "student-password",
                "new_password": "new-password-123",
                "confirm_password": "new-password-123",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Account settings updated", response.data)
        with app.app_context():
            user = db.session.get(User, self.student_id)
            self.assertEqual(user.name, "Updated Student")
            self.assertEqual(user.branch, "Computer Engineering")
            self.assertEqual(user.division, "A")
            self.assertTrue(check_password_hash(user.password_hash, "new-password-123"))
        with self.client.session_transaction() as session:
            self.assertEqual(session["name"], "Updated Student")

    def test_student_registration_requires_and_saves_class(self):
        response = self.client.post(
            "/register",
            data={
                "name": "New Student",
                "email": "new@student.example",
                "password": "student-password",
                "role": "student",
                "branch": "information technology",
                "division": "b",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        with app.app_context():
            student = User.query.filter_by(email="new@student.example").one()
            self.assertEqual(student.branch, "Information Technology")
            self.assertEqual(student.division, "B")

    def test_registration_dropdowns_include_official_branches_and_divisions(self):
        response = self.client.get("/register")
        self.assertEqual(response.status_code, 200)
        for branch in VIT_BRANCHES:
            self.assertIn(
                f'<option value="{html.escape(branch, quote=True)}">'.encode(),
                response.data,
            )
        for division in DIVISIONS:
            self.assertIn(f'<option value="{division}">'.encode(), response.data)
        self.assertIn(b'value="SEDA"', response.data)
        self.assertIn(b'value="H"', response.data)

    def test_account_and_teacher_forms_share_official_class_dropdowns(self):
        self.log_in(self.student_id, "student", "Test Student")
        account_response = self.client.get("/account")
        self.assertEqual(account_response.status_code, 200)
        self.assertIn(b'<option value="Computer Engineering" selected>', account_response.data)
        self.assertIn(b'value="SEDA"', account_response.data)

        self.log_in(self.teacher_id, "teacher", "Test Teacher")
        create_response = self.client.get("/teacher/exam/create")
        self.assertEqual(create_response.status_code, 200)
        self.assertIn(b'value="Computer Engineering"', create_response.data)
        self.assertIn(b'value="SEDA"', create_response.data)

    def test_teacher_cannot_create_exam_with_unlisted_class(self):
        self.log_in(self.teacher_id, "teacher", "Test Teacher")
        response = self.client.post(
            "/teacher/exam/create",
            data={
                "title": "Invalid class exam",
                "description": "",
                "target_branch": "Mathematics",
                "target_division": "I",
                "duration_minutes": "30",
                "start_at": "",
                "end_at": "",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Select a valid assigned branch and division", response.data)
        with app.app_context():
            self.assertIsNone(Exam.query.filter_by(title="Invalid class exam").first())

    def test_branch_values_are_whitelisted_and_long_names_fit(self):
        invalid_response = self.client.post(
            "/register",
            data={
                "name": "Invalid Branch",
                "email": "invalid@student.example",
                "password": "student-password",
                "role": "student",
                "branch": "Mathematics",
                "division": "I",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Select a valid branch and division", invalid_response.data)
        branch = "Computer Science & Engineering (IoT and Cyber Security Including Blockchain Technology)"
        self.assertGreater(len(branch), 80)
        valid_response = self.client.post(
            "/register",
            data={
                "name": "Long Branch Student",
                "email": "long-branch@student.example",
                "password": "student-password",
                "role": "student",
                "branch": branch,
                "division": "SEDA",
            },
        )
        self.assertEqual(valid_response.status_code, 302)
        with app.app_context():
            student = User.query.filter_by(email="long-branch@student.example").one()
            self.assertEqual(student.branch, branch)
            self.assertEqual(student.division, "SEDA")

    def test_teacher_creates_exam_for_a_branch_and_division(self):
        self.log_in(self.teacher_id, "teacher", "Test Teacher")
        response = self.client.post(
            "/teacher/exam/create",
            data={
                "title": "Targeted exam",
                "description": "",
                "target_branch": "computer engineering",
                "target_division": "a",
                "duration_minutes": "30",
                "start_at": "",
                "end_at": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            exam = Exam.query.filter_by(title="Targeted exam").one()
            self.assertEqual(exam.target_branch, "Computer Engineering")
            self.assertEqual(exam.target_division, "A")

    def test_questions_cannot_change_after_exam_attempt_begins(self):
        with app.app_context():
            existing_question = Question(
                exam_id=self.targeted_exam_id,
                text="Existing question",
                option_a="A",
                option_b="B",
                option_c="C",
                option_d="D",
                correct_option="A",
            )
            db.session.add(existing_question)
            db.session.commit()
        self.log_in(self.student_id, "student", "Test Student")
        self.client.get(f"/exam/{self.targeted_exam_id}")
        self.log_in(self.teacher_id, "teacher", "Test Teacher")
        response = self.client.post(
            f"/teacher/exam/{self.targeted_exam_id}/question",
            data={
                "text": "Late added question",
                "option_a": "A",
                "option_b": "B",
                "option_c": "C",
                "option_d": "D",
                "correct_option": "A",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Questions cannot be changed", response.data)
        with app.app_context():
            self.assertEqual(
                Question.query.filter_by(exam_id=self.targeted_exam_id).count(),
                1,
            )

    def test_exam_class_cannot_change_after_attempt_begins(self):
        with app.app_context():
            exam = db.session.get(Exam, self.targeted_exam_id)
            exam.questions.append(
                Question(
                    text="Existing question",
                    option_a="A",
                    option_b="B",
                    option_c="C",
                    option_d="D",
                    correct_option="A",
                )
            )
            db.session.commit()
        self.log_in(self.student_id, "student", "Test Student")
        self.client.get(f"/exam/{self.targeted_exam_id}")
        self.log_in(self.teacher_id, "teacher", "Test Teacher")
        response = self.client.post(
            f"/teacher/exam/{self.targeted_exam_id}/schedule",
            data={
                "target_branch": "Chemical Engineering",
                "target_division": "B",
                "duration_minutes": "30",
                "start_at": "",
                "end_at": "",
            },
            follow_redirects=True,
        )
        self.assertIn(b"The assigned class cannot be changed", response.data)
        with app.app_context():
            exam = db.session.get(Exam, self.targeted_exam_id)
            self.assertEqual(exam.target_branch, "Computer Engineering")
            self.assertEqual(exam.target_division, "A")

    def test_teacher_results_show_score_and_question_analytics(self):
        with app.app_context():
            question = Question(
                exam_id=self.available_exam_id,
                text="What is SQL?",
                option_a="Database language",
                option_b="Cloud",
                option_c="Operating system",
                option_d="Browser",
                correct_option="A",
            )
            db.session.add(question)
            db.session.flush()
            attempt = Attempt(
                exam_id=self.available_exam_id,
                student_id=self.student_id,
                score=1,
                total=1,
                status="submitted",
                started_at=get_now_utc() - timedelta(minutes=5),
                submitted_at=get_now_utc(),
            )
            db.session.add(attempt)
            db.session.flush()
            db.session.add(
                StudentAnswer(
                    attempt_id=attempt.id,
                    question_id=question.id,
                    selected_option="A",
                    is_correct=True,
                )
            )
            db.session.commit()
            exam_id = self.available_exam_id

        self.log_in(self.teacher_id, "teacher", "Test Teacher")
        response = self.client.get(f"/teacher/results/{exam_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Participation", response.data)
        self.assertIn(b"Average score", response.data)
        self.assertIn(b"Question performance", response.data)
        self.assertIn(b"100%", response.data)

    def test_exam_page_exposes_timer_and_autosave_status(self):
        with app.app_context():
            question = Question(
                exam_id=self.available_exam_id,
                text="Choose A",
                option_a="A",
                option_b="B",
                option_c="C",
                option_d="D",
                correct_option="A",
            )
            db.session.add(question)
            db.session.commit()
        self.log_in(self.student_id, "student", "Test Student")
        response = self.client.get(f"/exam/{self.available_exam_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Time Remaining", response.data)
        self.assertIn(b"All changes saved", response.data)

    def test_exam_page_question_navigator_shows_total_and_answer_status(self):
        with app.app_context():
            questions = [
                Question(
                    exam_id=self.available_exam_id,
                    text=f"Question {index}",
                    option_a="A",
                    option_b="B",
                    option_c="C",
                    option_d="D",
                    correct_option="A",
                )
                for index in range(1, 3)
            ]
            db.session.add_all(questions)
            db.session.commit()
            question_ids = [question.id for question in questions]

        self.log_in(self.student_id, "student", "Test Student")
        self.client.get(f"/exam/{self.available_exam_id}")
        with app.app_context():
            attempt = Attempt.query.filter_by(
                exam_id=self.available_exam_id,
                student_id=self.student_id,
            ).one()
            db.session.add(
                StudentAnswer(
                    attempt_id=attempt.id,
                    question_id=question_ids[0],
                    selected_option="A",
                    is_correct=True,
                )
            )
            db.session.commit()

        response = self.client.get(f"/exam/{self.available_exam_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"2 questions", response.data)
        self.assertIn(b'id="answered-count">1</span> of 2 answered', response.data)
        self.assertIn(b'class="question-nav-button answered current"', response.data)
        self.assertIn(b'class="question-nav-button unanswered"', response.data)
        self.assertIn(b'id="current-question-number">1</strong> of 2', response.data)

    def test_submitting_exam_saves_form_answers_without_waiting_for_autosave(self):
        with app.app_context():
            question = Question(
                exam_id=self.available_exam_id,
                text="Choose A",
                option_a="A",
                option_b="B",
                option_c="C",
                option_d="D",
                correct_option="A",
            )
            db.session.add(question)
            db.session.commit()
            question_id = question.id

        self.log_in(self.student_id, "student", "Test Student")
        page = self.client.get(f"/exam/{self.available_exam_id}")
        self.assertEqual(page.status_code, 200)

        response = self.client.post(
            f"/exam/{self.available_exam_id}/submit",
            data={f"question_{question_id}": "A"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Your answers were evaluated", response.data)
        with app.app_context():
            attempt = Attempt.query.filter_by(
                exam_id=self.available_exam_id,
                student_id=self.student_id,
            ).one()
            answer = StudentAnswer.query.filter_by(
                attempt_id=attempt.id,
                question_id=question_id,
            ).one()
            self.assertEqual(attempt.status, "submitted")
            self.assertEqual(attempt.score, 1)
            self.assertEqual(answer.selected_option, "A")

    def test_only_matching_branch_and_division_can_view_or_start_exam(self):
        with app.app_context():
            question = Question(
                exam_id=self.targeted_exam_id,
                text="Restricted question",
                option_a="A",
                option_b="B",
                option_c="C",
                option_d="D",
                correct_option="A",
            )
            other_student = User(
                name="Other Student",
                email="other@test.example",
                password_hash=generate_password_hash("student-password"),
                role="student",
                branch="Computer Engineering",
                division="B",
            )
            db.session.add_all([question, other_student])
            db.session.commit()
            other_student_id = other_student.id
            restricted_question_id = question.id
            db.session.add(
                Attempt(
                    exam_id=self.targeted_exam_id,
                    student_id=other_student_id,
                    status="in_progress",
                    started_at=get_now_utc(),
                    question_order=json.dumps([question.id]),
                )
            )
            db.session.commit()

        self.log_in(self.student_id, "student", "Test Student")
        allowed = self.client.get(f"/exam/{self.targeted_exam_id}")
        self.assertEqual(allowed.status_code, 200)

        other_client = app.test_client()
        with other_client.session_transaction() as session:
            session["user_id"] = other_student_id
            session["role"] = "student"
            session["name"] = "Other Student"
        denied = other_client.get(f"/exam/{self.targeted_exam_id}")
        self.assertEqual(denied.status_code, 302)
        denied_save = other_client.post(
            f"/exam/{self.targeted_exam_id}/save-answer",
            data={"question_id": restricted_question_id, "selected_option": "A"},
        )
        self.assertEqual(denied_save.status_code, 403)
        dashboard = other_client.get("/student/dashboard")
        self.assertNotIn(b"CS Division A Quiz", dashboard.data)

    def test_each_attempt_gets_a_shuffled_question_order_that_survives_resume(self):
        with app.app_context():
            questions = [
                Question(
                    exam_id=self.targeted_exam_id,
                    text=f"Question {index}",
                    option_a="A",
                    option_b="B",
                    option_c="C",
                    option_d="D",
                    correct_option="A",
                )
                for index in range(1, 4)
            ]
            db.session.add_all(questions)
            db.session.commit()
            question_ids = [question.id for question in questions]
            second_student = User(
                name="Second Student",
                email="second@test.example",
                password_hash=generate_password_hash("student-password"),
                role="student",
                branch="Computer Engineering",
                division="A",
            )
            db.session.add(second_student)
            db.session.commit()
            second_student_id = second_student.id

        second_client = app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = self.student_id
            session["role"] = "student"
            session["name"] = "Test Student"
        with second_client.session_transaction() as session:
            session["user_id"] = second_student_id
            session["role"] = "student"
            session["name"] = "Second Student"

        with patch("app.random.SystemRandom") as system_random:
            shuffle_effects = [
                lambda values: values.reverse(),
                lambda values: values.sort(),
            ]

            def shuffle(values):
                shuffle_effects.pop(0)(values)

            system_random.return_value.shuffle.side_effect = shuffle
            first_response = self.client.get(f"/exam/{self.targeted_exam_id}")
            second_response = second_client.get(f"/exam/{self.targeted_exam_id}")
            self.assertEqual(first_response.status_code, 200)
            self.assertEqual(second_response.status_code, 200)

        with app.app_context():
            first_attempt = Attempt.query.filter_by(
                exam_id=self.targeted_exam_id, student_id=self.student_id
            ).one()
            second_attempt = Attempt.query.filter_by(
                exam_id=self.targeted_exam_id, student_id=second_student_id
            ).one()
            first_order = json.loads(first_attempt.question_order)
            second_order = json.loads(second_attempt.question_order)
            self.assertEqual(set(first_order), set(question_ids))
            self.assertEqual(set(second_order), set(question_ids))
            self.assertNotEqual(first_order, second_order)

        resumed_response = self.client.get(f"/exam/{self.targeted_exam_id}")
        self.assertEqual(resumed_response.status_code, 200)
        self.assertEqual(system_random.return_value.shuffle.call_count, 2)

    def test_sqlite_backup_creates_a_copy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.db"
            destination = Path(temp_dir) / "backups"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("CREATE TABLE sample (value TEXT)")
                connection.execute("INSERT INTO sample VALUES ('preserved')")
                connection.commit()

            old_database_url = os.environ.get("DATABASE_URL")
            old_backup_dir = os.environ.get("BACKUP_DIR")
            try:
                os.environ["DATABASE_URL"] = f"sqlite:///{source.as_posix()}"
                os.environ["BACKUP_DIR"] = str(destination)
                backup = backup_database()
            finally:
                if old_database_url is None:
                    os.environ.pop("DATABASE_URL", None)
                else:
                    os.environ["DATABASE_URL"] = old_database_url
                if old_backup_dir is None:
                    os.environ.pop("BACKUP_DIR", None)
                else:
                    os.environ["BACKUP_DIR"] = old_backup_dir

            self.assertTrue(backup.is_file())
            with closing(sqlite3.connect(backup)) as connection:
                value = connection.execute("SELECT value FROM sample").fetchone()[0]
            self.assertEqual(value, "preserved")


if __name__ == "__main__":
    unittest.main()
