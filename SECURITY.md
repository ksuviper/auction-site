# Security Guide — ASQ Daylily Auctions

## Secret key management

`SECRET_KEY` must be a long, random string unique to each environment.
**Never commit a real secret key to version control.**

Generate one:
```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

Set it in the environment or `.env` file:
```
SECRET_KEY=<output from above>
```

The `.env` file is in `.gitignore` and must never be committed.

---

## Required environment variables

| Variable | Required in production | Description |
|---|---|---|
| `SECRET_KEY` | Yes | Django secret key — must be unique and secret |
| `DEBUG` | Yes — set `False` | Never run `DEBUG=True` in production |
| `ALLOWED_HOSTS` | Yes | Comma-separated list of your domain(s), e.g. `asqdaylily.com` |
| `DATABASE_*` | If using PostgreSQL | See `.env.example` for full list |
| `EMAIL_BACKEND` | Yes | Set to `django.core.mail.backends.smtp.EmailBackend` in production |
| `EMAIL_HOST_USER` | Yes | SMTP username |
| `EMAIL_HOST_PASSWORD` | Yes | SMTP password or app-specific password |
| `DEFAULT_FROM_EMAIL` | Yes | From address for outgoing email |
| `ADMIN_EMAIL` | Yes | Receives auction-close summaries and seller notifications |

### Optional HTTPS hardening (enable after TLS is confirmed working)

| Variable | Value | Description |
|---|---|---|
| `SECURE_SSL_REDIRECT` | `True` | Redirect all HTTP to HTTPS |
| `SECURE_HSTS_SECONDS` | `31536000` | 1-year HSTS header (set last, after confirming HTTPS works) |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | `True` | Extend HSTS to subdomains |
| `SECURE_HSTS_PRELOAD` | `True` | Eligible for browser HSTS preload lists |

**Warning:** Set HSTS only after verifying HTTPS works end-to-end. A misconfigured
HSTS header can lock users out of your site for up to a year.

---

## Enabling HTTPS with nginx + Certbot

### 1. Install nginx and Certbot

```bash
sudo apt install nginx certbot python3-certbot-nginx
```

### 2. Basic nginx config (`/etc/nginx/sites-available/asqdaylily`)

```nginx
server {
    listen 80;
    server_name asqdaylily.com www.asqdaylily.com;

    location /static/ {
        alias /path/to/auction_site/staticfiles/;
    }

    location /media/ {
        alias /path/to/auction_site/media/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable it:
```bash
sudo ln -s /etc/nginx/sites-available/asqdaylily /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 3. Obtain a TLS certificate

```bash
sudo certbot --nginx -d asqdaylily.com -d www.asqdaylily.com
```

Certbot modifies your nginx config to add HTTPS and sets up automatic renewal.

### 4. Tell Django it's behind HTTPS

Add to your `.env`:
```
SECURE_SSL_REDIRECT=True
```

Also add to nginx config (inside the `location /` block):
```nginx
proxy_set_header X-Forwarded-Proto $scheme;
```

And in Django settings add (already present):
```python
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
```

### 5. Enable HSTS (after confirming HTTPS works)

```
SECURE_HSTS_SECONDS=31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS=True
```

Test renewal:
```bash
sudo certbot renew --dry-run
```

---

## Rate limiting

- **Login:** 5 POST attempts per minute per IP. Returns HTTP 429 when exceeded.
- **Bid submission:** 10 bids per minute per authenticated user. Shows an error message when exceeded.

Rate limits are enforced via `django-ratelimit` using Django's default cache backend.
For multi-worker deployments, configure a shared cache (Redis recommended):

```python
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': 'redis://127.0.0.1:6379/1',
    }
}
```

---

## Django security checklist

Run Django's built-in deployment checklist before going live:

```bash
python manage.py check --deploy
```

Fix any warnings it reports before exposing the site to the public.
