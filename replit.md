# Python Flask App

## Overview
A Python Flask web application with PostgreSQL database connectivity.

## Stack
- **Language**: Python 3.11
- **Framework**: Flask
- **Database**: PostgreSQL (Replit built-in) via SQLAlchemy
- **ORM**: Flask-SQLAlchemy

## File Structure
```
├── main.py              # Entry point — starts the Flask dev server on port 5000
├── requirements.txt     # Python dependencies
├── app/
│   ├── __init__.py      # App factory (create_app)
│   ├── database.py      # SQLAlchemy db instance
│   ├── models.py        # Database models
│   └── routes.py        # Route handlers / blueprints
├── templates/
│   └── index.html       # HTML templates
└── static/
    └── style.css        # Static assets
```

## Environment Variables
Set automatically by Replit's database provisioning:
- `DATABASE_URL` — PostgreSQL connection string
- `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`

## Running
Workflow: `python main.py` on port 5000
