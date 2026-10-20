# CLAUDE.md — ASQ Daylily Auction

A Django auction site for a daylily club: auctions plus Buy It Now, PayPal
memberships, admin-run weekly listings, combined invoicing, and an installable
PWA with optional push notifications. `HANDOFF_FROM_CODE.md` has the full
history, audit status and hook points; `README.md` has the long-form docs.

## Stack

- **Python 3.13** (local), **Django 6.0.8**. The project root is `auction_site/` (it contains `manage.py`); the app is `auctions/`.
- **django-unfold 0.92.0** (admin), **django-allauth 65.14.3** (email login, Google/Facebook, MFA: TOTP plus recovery codes), **django-ratelimit 4.1.0**.
- **APScheduler 3.11.2 / django-apscheduler 0.7.0**: in-process jobs started from `AuctionsConfig.ready()`.
- **Pillow 12.3.0** (uploads and generated PWA icons), **pywebpush 2.5.0** (Web Push), **paypalrestsdk** (webhook verification only; other PayPal calls go through `auctions/paypal.py`).
- **Databases:** SQLite by default. **Production is PostgreSQL**, and `psycopg2` is *not* in `requirements.txt`.
- **Front end:** Bootstrap 5 and Font Awesome from CDNs; small vanilla JS files in `auction_site/static/js/`.
- **Services:** media on Cloudflare R2 when the `CF_R2_*` vars are set; email via Resend SMTP.

## Conventions

- **Admin:** every admin class extends `unfold.admin.ModelAdmin` (inlines use `unfold.admin.StackedInline`/`TabularInline`). Sidebar entries are added by hand in `settings.UNFOLD['SIDEBAR']`.
- **Staff-only pages** outside the admin use `auctions.mixins.StaffRequiredMixin` (returns 403, not a redirect). Member pages use `LoginRequiredMixin`.
- **Email:** always send through `auctions.utils._safe_send(subject, body, recipients)`. It logs and never raises. Bodies are plain-text f-strings, signed `-- ASQ Daylily Auction Group` (members) or `-- ASQ Daylily Auction System` (seller/admin notices).
- **Push:** `auctions.utils.send_push_to_user(user, title, body, url, tag=None)`. It never raises and is a no-op without keys or devices. Always send it **after** the email, never instead of it. Do not reuse `_safe_send` for push.
- **Time:** `TIME_ZONE = 'America/Chicago'`, `USE_TZ = True`. Use `django.utils.timezone.now()` and never `datetime.now()`. Scheduler cron times are Chicago time.
- **Secrets:** read with `os.getenv`. Never hard-code or log them. `TURNSTILE_SECRET` and `VAPID_PRIVATE_KEY` are read at call time, not stored in settings. Commit only `.env.example`, with blank secret values.
- **Sellers:** a seller is a `User` whose `profile.is_seller` is True. **There is no Seller model.** Name a seller with `profile.display_name` or `utils.seller_display_name(user)`. Look one up with `profile__is_seller=True` in the query, as `get_seller_or_404` does.
- **Profiles:** every `User` has a `UserProfile` (created by a signal, `related_name='profile'`). A profile cannot be added or deleted on its own. An account with bids, invoices etc. cannot be deleted (`user_activity()`).
- **Listings:** sellers do not create listings; admins do, via *Add Listing* (URL names are still `weekly_setup*`). Copy with `services.copy_listing()`, and add new descriptive fields to `COPIED_LISTING_FIELDS`. "For sale now" has one definition: `queries.active_listings()`.
- **Invoicing:** buyers get **no** email at auction close or purchase. The combined invoice an admin sends is their only bill. Do not add a per-sale buyer email.
- **Branding:** site name, slogan, banner and icon come from `SiteSettings.load()` (singleton, pk=1), not templates.
- **Templates:** `{# … #}` is **single-line only**. A multi-line one renders as visible page text, so use `{% comment %}…{% endcomment %}`. `test_template_hygiene.py` enforces this.
- **Commits:** explanatory commit messages saying what changed and why. Do not put model identifiers in commits or code.

## Running tests

```bash
cd auction_site
python manage.py test auctions.tests --parallel 4      # 803 tests, 5 skipped on SQLite
python manage.py makemigrations --check --dry-run
python manage.py check --deploy                         # with DEBUG=False and a long SECRET_KEY
```

**Run the suite against PostgreSQL before any change to a migration, a row
lock or a raw query.** SQLite ignores `select_for_update` and deferred
foreign-key checks, and has hidden production-breaking bugs twice. Set the
`DATABASE_ENGINE=django.db.backends.postgresql` and `DATABASE_*` variables,
`pip install psycopg2-binary`, then run the same command. On PostgreSQL
nothing is skipped.

External services are mocked in tests (PayPal, Turnstile, `pywebpush.webpush`).
Login in tests is two steps because of the emailed code: use
`auctions.tests.utils.sign_in`, or `client.force_login`. Create sellers with
`auctions.tests.utils.make_seller`.

Browser checks: Playwright at `/opt/node22/lib/node_modules/playwright` with
`executablePath: '/opt/pw-browsers/chromium'`. Password login needs the emailed
code, so inject a session cookie instead.

## Deploy

1. `git pull` on the server.
2. `pip install -r requirements.txt` (plus `psycopg2-binary` for PostgreSQL).
3. `python manage.py migrate`. Rehearse on a PostgreSQL copy first when there are new migrations.
4. `python manage.py collectstatic --noinput`.
5. Restart gunicorn (`gunicorn auction_site.wsgi:application --workers 3 --bind 127.0.0.1:8000`, behind nginx, which sets `X-Forwarded-Proto`).
6. `python manage.py check --deploy`.

**One-time setup:**

- `python manage.py create_paypal_plans`.
- `python manage.py generate_vapid_keys`: paste the output into `.env` **once** and never regenerate it.
- Set `SECRET_KEY`, `TURNSTILE_*`, email and PayPal env vars (names in `.env.example`).

## Gotchas learned so far

- **Bump `SERVICE_WORKER_VERSION`** in `auction_site/pwa.py` whenever `templates/pwa/sw.js` changes (currently `v2`), or installed phones keep the old worker. The worker caches **only** `/offline/`; do not add page or asset caching.
- **PWA install and push require HTTPS** (localhost excepted). On plain HTTP the browser ignores the manifest and the service worker, and no code change fixes that.
- **The manifest and icons are dynamic** (`/manifest.webmanifest`, `/pwa/icon-<size>.png`), generated from the branding row. There are no static icon files. Only sizes 192 and 512 exist.
- **iOS:** there is no programmatic install, and push works only inside the Home Screen app (iOS 16.4+). UI must explain Share → Add to Home Screen rather than show a dead button.
- **Row locks with `select_related` across a reverse one-to-one** need `select_for_update(of=('self',))`. PostgreSQL refuses to lock the nullable side of an outer join.
- **A data migration that deletes rows and then alters a table** needs `connection.check_constraints()` in between (FKs are `DEFERRABLE INITIALLY DEFERRED`).
- **Stock** is decremented with one conditional `UPDATE … WHERE quantity_remaining >= n`, never read-check-write.
- **Network calls inside an atomic block that holds a lock** go in `transaction.on_commit` (see `_push_outbid`).
- **The scheduler starts in every gunicorn worker.** Jobs must be safe to run concurrently; the close job relies on a row lock.
- **Email is load-bearing:** verification, login codes and approvals all depend on it. Broken SMTP locks out new users. `manage.py unlock_account` is the escape hatch, and `/admin/login/` always works for staff.
- **PayPal price changes** reach existing subscribers from their next billing date. The admin refuses to save a price PayPal rejected.
- **Push endpoints** are only accepted on the hosts in `WEBPUSH_ALLOWED_HOST_SUFFIXES` (SSRF guard).
- **Admin-authored `SitePage`/`FAQItem` bodies render as HTML** (`|safe`, an accepted risk). Public email addresses go through `{% protected_email %}` or `|protect_emails`; never write a raw `mailto:` into a template.
- **In this sandbox**, kill the dev server by PID; `pkill -f` kills the calling shell.
