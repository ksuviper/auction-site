# ASQ Daylily Auctions

A Django-based online auction platform for the Above Status Quo Daylily Auction Group.
Sellers are featured on a rotating weekly schedule; bidders browse by category, place bids,
and receive automated invoices when auctions close.

---

## Features

- Weekly auction rotation with bulk listing upload
- Automatic auction closing, winner assignment, and invoice generation (APScheduler)
- Email notifications to winners and sellers on auction close
- Invoice management dashboard with CSV export
- Admin reporting with Chart.js monthly revenue chart
- Social login (Google, Facebook, Microsoft) via django-allauth
- MFA support via allauth
- Rate-limited login (5/min per IP) and bid submission (10/min per user)
- Mobile-first Bootstrap 5 UI with offcanvas category sidebar

---

## Local Setup

### 1. Clone the repository

```bash
git clone <repo-url>
cd auction-site
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```
SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_urlsafe(50))">
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
```

### 5. Apply database migrations

```bash
cd auction_site
python manage.py migrate
```

### 6. Create a superuser

```bash
python manage.py createsuperuser
```

### 7. Run the development server

```bash
python manage.py runserver
```

Visit `http://localhost:8000`. Log in at `/admin/` with your superuser credentials.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SECRET_KEY` | Yes | insecure default | Django secret key — generate a new one for production |
| `DEBUG` | Yes | `False` | Set `True` for local development only |
| `ALLOWED_HOSTS` | Yes | `localhost,127.0.0.1` | Comma-separated allowed hostnames |
| `DATABASE_ENGINE` | No | SQLite | e.g. `django.db.backends.postgresql` |
| `DATABASE_NAME` | No | — | PostgreSQL database name |
| `DATABASE_USER` | No | — | PostgreSQL user |
| `DATABASE_PASSWORD` | No | — | PostgreSQL password |
| `DATABASE_HOST` | No | — | PostgreSQL host |
| `DATABASE_PORT` | No | — | PostgreSQL port |
| `EMAIL_BACKEND` | No | console | Set to `django.core.mail.backends.smtp.EmailBackend` in production |
| `EMAIL_HOST` | No | `smtp.gmail.com` | SMTP server hostname |
| `EMAIL_PORT` | No | `587` | SMTP port |
| `EMAIL_USE_TLS` | No | `True` | Enable STARTTLS |
| `EMAIL_HOST_USER` | No | — | SMTP username |
| `EMAIL_HOST_PASSWORD` | No | — | SMTP password or app-specific password |
| `DEFAULT_FROM_EMAIL` | No | — | From address for outgoing mail |
| `ADMIN_EMAIL` | No | — | Receives auction-close summaries and no-bid notifications |
| `GOOGLE_CLIENT_ID` | No | — | Google OAuth2 client ID |
| `GOOGLE_CLIENT_SECRET` | No | — | Google OAuth2 client secret |
| `FACEBOOK_APP_ID` | No | — | Facebook Login app ID |
| `FACEBOOK_APP_SECRET` | No | — | Facebook Login app secret |
| `MICROSOFT_CLIENT_ID` | No | — | Microsoft Azure app client ID |
| `MICROSOFT_CLIENT_SECRET` | No | — | Microsoft Azure app client secret |
| `SECURE_SSL_REDIRECT` | No | `False` | Set `True` in production behind HTTPS |
| `SECURE_HSTS_SECONDS` | No | `0` | Set `31536000` after HTTPS is confirmed working |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | No | `False` | Extend HSTS to subdomains |
| `SECURE_HSTS_PRELOAD` | No | `False` | Enable HSTS preload list eligibility |

---

## Background Scheduler

The APScheduler runs automatically when the development server starts. It runs two jobs:

| Job | Schedule | Description |
|---|---|---|
| `close_ended_auctions` | Every 5 minutes | Closes ended auctions, assigns winners, creates invoices, sends emails |
| `deactivate_old_listings` | Monday 03:00 | Sets `is_active=False` on listings ended more than 7 days ago |

Run either job manually at any time:

```bash
python manage.py close_ended_auctions
python manage.py close_ended_auctions --dry-run   # preview only

python manage.py deactivate_old_listings
python manage.py deactivate_old_listings --dry-run
```

In production, the scheduler still starts automatically inside Gunicorn workers. If you
prefer an external cron, disable the scheduler and add cron entries instead:

```cron
*/5 * * * * /path/to/venv/bin/python /path/to/auction_site/manage.py close_ended_auctions
0   3 * * 1 /path/to/venv/bin/python /path/to/auction_site/manage.py deactivate_old_listings
```

---

## Deployment

### Collect static files

```bash
python manage.py collectstatic
```

### Gunicorn

```bash
pip install gunicorn
gunicorn auction_site.wsgi:application --workers 3 --bind 127.0.0.1:8000
```

### nginx

Proxy requests to Gunicorn and serve static and media files directly:

```nginx
server {
    listen 80;
    server_name yourdomain.com;

    location /static/ { alias /path/to/auction_site/staticfiles/; }
    location /media/  { alias /path/to/auction_site/media/; }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Then obtain a TLS certificate with Certbot — see [SECURITY.md](SECURITY.md) for the
full HTTPS setup guide.

### Production environment variables (minimum)

```
SECRET_KEY=<long random string>
DEBUG=False
ALLOWED_HOSTS=yourdomain.com
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST_USER=...
EMAIL_HOST_PASSWORD=...
ADMIN_EMAIL=...
```

### Run the deployment checklist

```bash
python manage.py check --deploy
```

Fix any warnings before going live. See [SECURITY.md](SECURITY.md) for details.

---

## Weekly Workflow

See [WEEKLY_WORKFLOW.md](WEEKLY_WORKFLOW.md) for the step-by-step guide to running
each week's auction cycle.
