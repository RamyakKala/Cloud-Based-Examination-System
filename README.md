# Cloud-Based-Examination-System (AetherExam)

**Assess with confidence.**

A calm, focused examination platform built with Python Flask, SQLAlchemy, Jinja2, HTML, CSS, JavaScript and a cloud-ready relational database.

## Main Features

- Student registration/login
- Teacher registration/login
- Teacher creates exams
- Teacher adds MCQ questions
- Student takes online exam
- Automatic evaluation
- Result dashboard
- Teacher result view
- Password hashing
- Cloud-ready database using `DATABASE_URL`
- SaaS-style multi-user web application

## Included Features

This project now includes:
- exam duration timer and automatic submission
- in-exam question navigator with live answered/unanswered indicators and jump-to-question controls
- scheduled exams with timezone handling in `Asia/Kolkata`
- CSV and XLSX MCQ import with validation and preview
- per-attempt answer persistence and server-side scoring
- teacher reset of one or all attempts
- student result history and answer review
- validation of exam ownership and schedule restrictions
- student and teacher dashboard overview cards, search, and status filters
- in-app reminders for exams starting within 24 hours
- visible answer-save state and submit confirmation
- per-exam participation, average score, and question performance analytics
- account name/password settings
- automated route and workflow tests
- configurable application logging and SQLite/PostgreSQL backup script
- branch-and-division exam targeting with server-side access enforcement
- per-attempt question shuffling with a saved order for exam resumption

## Product interface

The interface uses a restrained neutral design for student and teacher workspaces. The active examination screen has a separate warm, low-glare palette to support focus during longer exams.

## Cloud Computing Concepts Demonstrated

### 1. SaaS
The application is delivered through a web browser. Students and teachers do not need to install the examination software.

### 2. Cloud Database
The project uses SQLAlchemy. By default it uses SQLite locally. For deployment, set `DATABASE_URL` to a managed PostgreSQL database.

### 3. Authentication
Flask sessions are used for login state and Werkzeug securely hashes passwords.

### 4. Cloud Deployment
The included `Procfile` allows deployment on services such as Render or similar Python cloud platforms. The same application can also be deployed on Google Cloud using an appropriate Python hosting service.

## Run Locally

### Windows

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open:

`http://127.0.0.1:5000`

### Local demo accounts (not created in production)

Teacher:
- Email: `teacher@example.com`
- Password: `teacher123`

Student:
- Email: `student@example.com`
- Password: `student123`

## Cloud Database and Migration Safety

For production, set:

```text
DATABASE_URL=postgresql://user:password@host:5432/database
SECRET_KEY=your-long-random-secret
```

The app preserves the SQLite fallback and supports PostgreSQL via `DATABASE_URL`. Set `APP_ENV=production` when deploying; production startup requires `SECRET_KEY`. Session cookies use `HttpOnly`, `SameSite=Lax`, and `Secure` settings in production. Configure secrets through the hosting provider's environment settings, not in source control. `LOG_LEVEL` controls console log verbosity.

### Class-targeted exams

Branch dropdown values are based on the programs/departments listed by [VIT Pune](https://www.vit.edu/) and its [Undergraduate - SEDA admissions page](https://www.vit.edu/undergraduate-dse/). Student registration, student profiles, and teacher exam assignment use the same branch list. Division options are A through H and SEDA.

Students select their branch and division when registering and can update them in account settings. Teachers assign each new exam to a branch and division; values are validated against the shared dropdown list. The dashboard hides exams for other classes, and the server independently checks class membership before allowing an exam attempt or answer submission. Existing exams created before this feature have no class assignment and remain available to all students until assigned from the exam management page, provided no attempt has started. Previously saved class values not present in the official list remain selectable as legacy values so existing profiles and exams can continue to be edited; new values must use the official dropdown.

Each student's attempt receives the same complete exam question set in a shuffled order. The order is saved with the attempt, so refreshing or resuming does not reshuffle it. After the first attempt begins, question creation/imports and changing the assigned class are blocked so later students cannot receive a different question set or eligibility group. Student-provided class information is self-reported; production deployments that require verified enrollment should add an institution-controlled roster or enrollment approval flow.

### Tests

Run the automated tests from this directory:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

### Database backups

Set `DATABASE_URL` to the database to back up, then run:

```powershell
.\venv\Scripts\python.exe backup_database.py
```

Backups are written to `backups/` by default; set `BACKUP_DIR` to use a different persistent location. SQLite uses its online backup API. PostgreSQL uses `pg_dump` and requires the PostgreSQL client tools to be installed. For cloud deployment, store backups in persistent/object storage and schedule this command using a protected job or hosting scheduler. PostgreSQL custom-format files can be restored with `pg_restore`; SQLite `.db` backups can replace a stopped application's database after making a separate safety copy.

Migration procedure:
1. Back up the database with `.\venv\Scripts\python.exe backup_database.py` (PostgreSQL requires `pg_dump`).
2. Deploy the updated app code.
3. On startup, the application checks for missing columns and adds them safely without deleting data.
4. Review existing results after deployment; reset policy overwrites previous attempts for the same exam/student combination.

## Deployment Notes

This project is intended for Render or similar Python hosting and keeps the existing `Procfile` and Gunicorn settings.

## Reset Policy

Resetting an attempt replaces the previous score for that same exam. Older results are not retained as historical records for the same student/exam pair.

## Suggested Cloud Architecture

Browser
   |
   v
Flask Web Application
   |
   +---- Authentication / Sessions
   |
   +---- Exam & Evaluation Logic
   |
   v
Managed Cloud Database

## Suggested Project Presentation

1. Problem statement
2. Objectives
3. System architecture
4. Modules
5. Cloud concepts
6. Database design
7. Authentication
8. Auto evaluation workflow
9. Screenshots/demo
10. Cloud deployment
11. Future scope

## Important Production Improvements

For a real production system, also add:
- HTTPS
- Admin role
- Randomized questions/options
- Multiple attempts policy
- Email notifications
- Rate limiting
- Audit logs
- Persistent cloud storage for uploaded files and backups
- A managed log aggregation and alerting service

This project is designed as an academic/demo system, not as a production examination platform.
