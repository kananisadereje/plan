# ITPMS (Flask + PostgreSQL)

pip install -r requirements.txt
export DATABASE_URL=postgresql://user:pass@host/itpms SECRET_KEY=<random>
flask --app app init-db # creates tables, views, admin@itpms.local / Admin@123
flask --app app run # dev | production: gunicorn app:app (behind HTTPS, e.g. Render/Railway/VPS)

After updating an existing installation, run `flask --app app init-db` again to apply new idempotent schema changes.
