# QR Attendance – Django backend

REST API for the QR Attendance React app (Django + Django REST Framework + JWT).
It lives in its own folder; the React frontend (separate folder) talks to it through axios.

## 1. Setup (run in a terminal inside this folder)

```bash
# create and activate a virtual environment
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # Mac / Linux

pip install -r requirements.txt

# settings file
copy .env.example .env           # Windows
# cp .env.example .env           # Mac / Linux

# database
python manage.py migrate

# demo users (development only) ...
python manage.py seed_demo
# ... or create your own admin instead
# python manage.py createsuperuser

python manage.py runserver       # http://127.0.0.1:8000
```

Check it is up: open <http://127.0.0.1:8000/api/health/> – you should see `{"status": "ok", ...}`.

### Demo logins (after `seed_demo`)

| Role | Employee ID | Password |
|---|---|---|
| Admin | `ADM001` | `Demo@12345` |
| Manager | `MGR001` | `Demo@12345` |
| Employee | `EMP001`, `EMP002` | `Demo@12345` |

People log in with their **Employee ID** (not the username). If you use `createsuperuser`,
the admin's Employee ID is generated automatically (`ADM001`, `ADM002`, …) – see it in the
Django admin at <http://127.0.0.1:8000/admin/>.

Run the React app in a second terminal (`npm run dev`); its Vite proxy forwards `/api` to this server.

## 2. API (all under `/api/`)

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `health/` | anyone | connectivity check |
| POST | `auth/login/` | anyone | `{employee_id, password}` → `{access, refresh, user}` |
| POST | `auth/qr-login/` | anyone | `{qr_token}` → `{access, user, active_attendance}` (short-lived, limited token, not for admins) |
| POST | `auth/token/refresh/` | anyone | `{refresh}` → `{access}` |
| POST | `auth/register/` | admin | create employee/manager; blank username / employee_id are auto-generated |
| GET | `dashboard/` | any | counts + staff lists, depends on role |
| POST | `attendance/mark/` | employee, manager | `{token?}`: none/personal QR = check in; session QR token = check in to that session |
| POST | `attendance/checkout/` | employee, manager | `{record?}` |
| GET | `attendance/history/` | any | `?period=all\|daily\|monthly&role=&employee=`; employees see own, managers see employees + own, admins see all |
| GET / POST | `notices/` | GET any, POST admin | admin sees sent notices, others see received |
| GET / POST | `sessions/` | admin, manager | create a shared QR (`{title, expires_in_minutes?}`); response has `qr_url` |
| GET | `reports/export/` | admin, manager | CSV download |

Send the JWT as `Authorization: Bearer <access>`.

## 3. How QR attendance works

- **Personal QR** – every employee/manager has a secret `qr_token`; the React app draws it as a QR
  that opens `<frontend>/?quick=<token>`. Scanning it signs the owner in (30 min token, can only mark
  attendance and read the owner's own data) and checks them in, or offers Check Out if already in.
  If a QR is lost: Django admin → Users → select user → action *Regenerate QR token*.
- **Session QR** (meeting, shift, event) – an admin/manager creates a session with `POST /api/sessions/`
  (or in the Django admin → Attendance sessions). Turn the returned `qr_url`
  (`<frontend>/?attendance=<token>`) into a QR code. People scan it, log in if needed, and are checked in
  once per session. Sessions can expire.
- One open check-in per person is enforced by a database constraint, so double scans can't create duplicates.

## 4. Settings (`.env`)

| Variable | Meaning |
|---|---|
| `DJANGO_DEBUG` | `True` for development, `False` in production |
| `DJANGO_SECRET_KEY` | required when `DJANGO_DEBUG=False` |
| `DJANGO_ALLOWED_HOSTS` | add your LAN IP if a phone calls Django directly |
| `CORS_ALLOWED_ORIGINS` | only needed when React calls Django directly instead of using the Vite proxy |
| `FRONTEND_URL` | used to build session-QR links; use your LAN IP for phone scans |
| `DJANGO_TIME_ZONE` | default `Asia/Kolkata`; decides what "today" and monthly totals mean |
| `JWT_ACCESS_HOURS`, `QR_LOGIN_MINUTES` | token lifetimes |

## 5. Tests

```bash
python manage.py test
```

## 6. Moving to production

- `DJANGO_DEBUG=False`, a real `DJANGO_SECRET_KEY`, correct `DJANGO_ALLOWED_HOSTS` and `CORS_ALLOWED_ORIGINS`.
- Serve over HTTPS (the QR links and JWTs should never travel over plain HTTP).
- Use PostgreSQL: `pip install psycopg[binary]` and replace the `DATABASES` block in `config/settings.py`.
- Run with gunicorn/uvicorn behind nginx and run `python manage.py collectstatic`.
