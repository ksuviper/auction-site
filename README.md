# ASQ Daylily Auctions

A Django-based online auction platform for the Above Status Quo Daylily Auction Group.
Sellers are featured on a rotating weekly schedule; bidders browse by category, place bids,
and receive automated invoices when auctions close.

---

## Features

- Weekly auction rotation with bulk listing upload
- Automatic auction closing, winner assignment, and invoice generation (APScheduler)
- Paid memberships via PayPal subscriptions, gating bidding and purchasing (US residents only)
- Buy It Now listings alongside standard auctions, with per-listing stock so
  several buyers can each take part of it, and a seller-chosen shipping mode
- Proxy (automatic) bidding up to a bidder's maximum
- Listing questions & comments with admin moderation and threaded replies
- Email notifications to winners and sellers on auction close
- Invoice management dashboard with CSV export
- Admin reporting with Chart.js monthly revenue chart
- Social login (Google, Facebook) via django-allauth
- Two-factor authentication: emailed login code by default for everyone
  (mandatory for staff), optional authenticator app (TOTP) with recovery codes
- Rate-limited login (5/min per IP), signup (5/hour per IP), and bid submission (10/min per user)
- Mandatory email verification plus Cloudflare Turnstile on email/password signup
- Manual admin approval before a new account can log in, with approval/revocation
  actions in the admin (staff and superusers exempt)
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

With `DEBUG=True` the default console email backend prints signup verification
links straight to the terminal, and Cloudflare Turnstile is skipped when
`TURNSTILE_SECRET` is unset — so registration works out of the box locally.
Accounts you register locally still need approving before they can log in: tick
**Is approved** for them in the admin under **User profiles**. See
[Registration Security](#registration-security) before deploying.

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
| `ADMIN_EMAIL` | No | — | Receives auction-close summaries, no-bid notifications, and new-account approval requests |
| `GOOGLE_CLIENT_ID` | No | — | Google OAuth2 client ID |
| `GOOGLE_CLIENT_SECRET` | No | — | Google OAuth2 client secret |
| `FACEBOOK_APP_ID` | No | — | Facebook Login app ID |
| `FACEBOOK_APP_SECRET` | No | — | Facebook Login app secret |
| `PAYPAL_CLIENT_ID` | For memberships | — | PayPal REST app client ID |
| `PAYPAL_CLIENT_SECRET` | For memberships | — | PayPal REST app secret |
| `PAYPAL_MODE` | No | `sandbox` | `sandbox` or `live` |
| `PAYPAL_MONTHLY_PLAN_ID` | For memberships | — | Plan ID from `create_paypal_plans` |
| `PAYPAL_YEARLY_PLAN_ID` | For memberships | — | Plan ID from `create_paypal_plans` |
| `PAYPAL_MONTHLY_PRICE` | No | `9.99` | Monthly membership price (USD) |
| `PAYPAL_YEARLY_PRICE` | No | `99.99` | Annual membership price (USD) |
| `PAYPAL_WEBHOOK_ID` | For memberships | — | Webhook ID from the PayPal dashboard (required for webhook verification) |
| `CF_R2_ACCOUNT_ID` | No | — | Cloudflare R2 account ID (enables R2 media storage when set with the keys below) |
| `CF_R2_ACCESS_KEY_ID` | No | — | R2 access key ID |
| `CF_R2_SECRET_ACCESS_KEY` | No | — | R2 secret access key |
| `CF_R2_BUCKET_NAME` | No | — | R2 bucket name for uploaded media |
| `CF_R2_CUSTOM_DOMAIN` | No | — | Optional public domain serving the bucket |
| `TURNSTILE_SITE_KEY` | Yes in production | — | Cloudflare Turnstile public site key; the widget is hidden when unset |
| `TURNSTILE_SECRET` | Yes in production | — | Turnstile secret key. **With `DEBUG=False` and no secret, every email/password signup is rejected.** |
| `SECURE_SSL_REDIRECT` | No | `False` | Set `True` in production behind HTTPS |
| `SECURE_HSTS_SECONDS` | No | `0` | Set `31536000` after HTTPS is confirmed working |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | No | `False` | Extend HSTS to subdomains |
| `SECURE_HSTS_PRELOAD` | No | `False` | Enable HSTS preload list eligibility |

---

## Buy It Now Stock

A Buy It Now listing carries a quantity, and **several buyers can each purchase
part of it** until nothing is left. Auction listings are unaffected — they still
sell one lot to one `winner`.

| Field | Meaning |
|---|---|
| `quantity_available` | Units the seller listed. |
| `quantity_remaining` | Units still for sale. Mirrors `quantity_available` at creation, then counts down. |
| `shipping_mode` | `flat` — buyer pays shipping once no matter how many they buy. `per_item` — shipping is multiplied by the quantity. Chosen by the seller **per listing**. |
| `Invoice.quantity` | Units on that invoice. One invoice per purchase, so one listing can have several. `Invoice.amount` is the line total (unit price × quantity), not the unit price. |

Sellers set the quantity and shipping mode per row in **Weekly Setup**; both
inputs appear only when the row's type is Buy It Now. Duplicating a listing in
the admin resets its stock to full rather than inheriting how far the original
sold down.

### Behaviour worth knowing

- **A listing closes at its scheduled `ends_at`, whatever stock is left.** Stock
  caps availability; it is not a second clock. Leftover units stay on the record
  and no invoice is invented for them.
- **Running out also closes it** (`is_closed` and `is_active` both flip) so it
  drops off the browse pages.
- **`winner` is not set for Buy It Now sales any more.** It means "the one person
  who won this lot", which cannot describe a listing several people bought from.
  Who bought what lives in the invoices. Existing auction wins are untouched.
- Reporting counts **units** (`Sum(Invoice.quantity)`), not invoice rows, and
  reads sales from invoices rather than from listings-with-a-winner — otherwise
  every Buy It Now sale would drop out of the totals. The invoice CSV export has
  a Quantity column.

### Overselling cannot happen

The stock decrement is a single conditional `UPDATE`:

```sql
UPDATE ... SET quantity_remaining = quantity_remaining - N
WHERE id = ... AND quantity_remaining >= N
```

The check and the write are one statement, so two buyers cannot both pass the
check and then both write. If it matches no rows, someone else got there first
and the buyer is told how many are actually left — no partial purchase is made.

This deliberately does **not** rely on `select_for_update()`, which the view also
uses. SQLite reports `has_select_for_update = False`, which makes that call a
silent no-op — and this project defaults to SQLite. The lock helps on PostgreSQL;
the conditional UPDATE is what holds everywhere.

`auctions/tests/test_buy_now.py` pins this down with a form that skips its own
stock check, leaving only the UPDATE to stop the oversell. The threaded tests
beside it need a backend with concurrent writers and are skipped on SQLite,
which answers "database table is locked" as soon as two threads write.

### Upgrading an existing site

Migration `0016` gives every pre-existing Buy It Now listing a stock count of 1 —
sold ones get 0 remaining, the rest keep their single unit. Without it a null
`quantity_remaining` reads as sold out, and every live Buy It Now listing would
become unpurchasable the moment the migration landed.

---

## Two-Factor Authentication

**Every password login also requires a one-time code emailed to the user.** It is
on by default for everyone, staff included.

- **Members may turn it off** under *Login & Security* (`/account/security/`),
  leaving them password-only.
- **Staff and superusers cannot.** This is enforced in
  `AccountAdapter.is_login_by_code_required()`, not by hiding the control — so
  clearing the flag directly in the database or the Django admin changes nothing.
  That single check is the whole of the "mandatory 2FA for staff" requirement.
- **An authenticator app replaces the email code.** Any user can set up TOTP
  (plus recovery codes) from the same page. Once active they are challenged by
  the app instead; the two prompts never stack, because allauth's login-by-code
  stage runs *before* its MFA stage and the adapter suppresses the code when the
  MFA stage is going to challenge.
- **Social login is untouched** — no code, no app prompt — matching how Turnstile
  and mandatory email verification are scoped to the password path.
- **Sign-up is untouched.** A brand-new account is not asked for a login code
  before it has confirmed the address the code would go to; email verification
  comes first, as before.

allauth's MFA pages live under `/accounts/2fa/` (not `/accounts/mfa/`).

### Settings

| Setting | Value | Why |
|---|---|---|
| `ACCOUNT_LOGIN_BY_CODE_REQUIRED` | `{'password'}` | Requires the code for password logins only. A set, not `True`, so social logins are exempt. |
| `ACCOUNT_LOGIN_BY_CODE_ENABLED` | *unset (off)* | Deliberate. This is a different feature — "email me a code **instead of** my password", i.e. passwordless login. We want the code as a second factor. |
| `ACCOUNT_EMAIL_SUBJECT_PREFIX` | `''` | allauth otherwise prefixes every subject with `[example.com]`, taken from the unconfigured Sites record. |
| `MFA_SUPPORTED_TYPES` | `['totp', 'recovery_codes']` | allauth's default, pinned because the login flow depends on which types exist. |
| `MFA_ALLOW_UNVERIFIED_EMAIL` | `False` | Consistent with mandatory email verification. |
| `MFA_TOTP_ISSUER` | `ASQ Daylily Auctions` | Name shown in the user's authenticator app; otherwise it reads `example.com`. |

`UserProfile.email_login_code_enabled` (default `True`) holds the per-user
preference. It is ignored for staff, and ignored for anyone with an
authenticator app.

### Before this goes live

**Log in as a staff account immediately after deploying.** The email code is
mandatory for staff and cannot be skipped, so a broken SMTP configuration locks
every administrator out of `/accounts/login/`. `/admin/login/` still works (see
[Locked out?](#locked-out)) — that is the recovery path if codes are not
arriving.

Existing members get `email_login_code_enabled = True`, so **everyone needs
working email to sign in** from the moment this deploys, not just new
registrations.

### Branding allauth's pages

Rather than copying allauth's page templates, the project overrides:

- `templates/allauth/layouts/entrance.html` and `manage.html` — bridge allauth's
  standalone document into the site's own `base.html`.
- `templates/allauth/elements/*.html` — allauth renders all of its UI through
  these primitives (`button`, `field`, `fields`, `form`, `panel`, `h1`, …), so
  overriding them applies Bootstrap to every allauth page at once.
- `templates/account/email/login_code_*.txt` — the code email, subject included,
  so it reads as recognisably from this site.

Because none of allauth's functional markup is duplicated, these survive allauth
upgrades. **Do not override an allauth `base_*.html` template with a bare
wrapper** — those files contain the forms. Doing exactly that to
`account/base_confirm_code.html` once produced a login-code page with no code
field and no submit button: a 200 response nobody could log in through.
`auctions/tests/test_security_settings.py` asserts on rendered markup to catch a
repeat.

---

## Registration Security

New accounts pass through four gates, in this order:

1. **Cloudflare Turnstile** — proves a human filled in the form.
2. **Per-IP rate limit** — caps how fast one connection can register.
3. **Email verification** — proves the address is real and theirs.
4. **Admin approval** — a person decides whether to let them in.

Social login (Google/Facebook) skips 1–3: the provider has already verified the
address and runs its own bot protection. It is still subject to gate 4.

Staff and superusers are exempt from gate 4 entirely — see below.

### Mandatory email verification

`ACCOUNT_EMAIL_VERIFICATION = 'mandatory'`. A new account cannot log in — and
therefore cannot bid, buy, or comment — until the confirmation link in its
signup email is clicked.

**This makes outbound email load-bearing.** If `EMAIL_BACKEND` and the
`EMAIL_HOST*` credentials are wrong in production, signup emails never arrive
and *every* new user is silently locked out, with nothing in the UI to explain
why. Send yourself a test registration after any change to the mail config.
`ADMIN_EMAIL` and `DEFAULT_FROM_EMAIL` should also be set — allauth sends the
verification mail through the same SMTP settings as the rest of the site's
email, so there is no separate mail backend to configure.

Staff accounts get a verified address automatically at creation — see
[Locked out?](#locked-out) below — because `createsuperuser` knows nothing about
allauth and would otherwise leave the new superuser unable to log in.

Clicking the link verifies the address and lands the user on
`/accounts/pending-approval/`. It deliberately does **not** establish a session
(`ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = False`): allauth's login-on-confirmation
resumes the stashed sign-up through `resume_login()`, which skips
`adapter.pre_login()` — the hook the approval gate below lives on. Leaving it
enabled would hand an un-approved account a real session.

Membership gating is unaffected: `has_active_subscription()` is checked
independently of verification status.

### Per-IP signup rate limit

`RateLimitedSignupView` allows **5 signup POSTs per hour per IP** and answers
the sixth with a 403 rendered from `templates/403.html`. The view is registered
ahead of `include('allauth.urls')` in `auction_site/urls.py` so it owns
`/accounts/signup/`. Counters live in Django's cache, so restarting the server
clears them — useful when testing locally.

### Cloudflare Turnstile

The signup form carries a Turnstile widget; the token is verified server-side by
`verify_turnstile()` in `auctions/utils.py` before the account is created.

Get keys from the [Cloudflare dashboard](https://dash.cloudflare.com) →
**Turnstile** → **Add site**. The site key is public and lives in
`TURNSTILE_SITE_KEY`; the secret goes in `TURNSTILE_SECRET` as a server
environment variable and is never logged.

Verification **fails closed** — an unreachable Cloudflare endpoint, a timeout,
or a missing secret rejects the signup rather than letting it through.

For local development, use Cloudflare's
[test keys](https://developers.cloudflare.com/turnstile/troubleshooting/testing/):

| Behaviour | Site key | Secret |
|---|---|---|
| Always passes | `1x00000000000000000000AA` | `1x0000000000000000000000000000000AA` |
| Always blocks | `2x00000000000000000000AB` | `2x0000000000000000000000000000000AA` |

As a convenience, when `DEBUG=True` **and** `TURNSTILE_SECRET` is unset the
check is skipped with a logged warning, so a fresh clone can register accounts
without Cloudflare setup. With `DEBUG=False` a missing secret always fails
closed.

### Admin approval

`UserProfile.is_approved` defaults to `False`, and
`AccountAdapter.pre_login()` refuses to establish a session for an unapproved
account — redirecting to `/accounts/pending-approval/` with an explanation
instead. Because the gate sits in `pre_login()`, it covers **every** login path,
email/password and social alike.

An unapproved user cannot log in for any purpose, including completing their
profile. There is no partial access.

**Staff and superusers bypass the gate.** An account made with
`createsuperuser` starts unapproved like any other, and locking admins out would
leave nobody able to approve anyone.

Where approval shows up in the admin:

- **Users → Pending Approval** in the sidebar is the review queue, with a badge
  showing how many accounts are waiting. Staff are excluded from both the badge
  and the queue, so your own superuser account doesn't sit there forever. The
  badge always renders, reading `0` when the queue is empty.
- The **Users** list has an **Approval** column — *Awaiting approval*,
  *Approved*, or *Exempt (staff)* — and a filter on approval state. Opening a
  user shows an **Is approved** checkbox in their Profile section.
- **Users → User Profiles** is the full list, with the registered email shown
  (the username allauth derives at signup identifies nobody), an **Approval
  status** filter, and an inline **Is approved** toggle.

Three ways to approve, all equivalent:

- Flip the **Is approved** toggle inline on the User profiles list.
- Tick **Is approved** on a user's page.
- Select rows and run **Approve selected account(s) and notify the user**.

Each route emails the user a sign-in link — the notification hangs off a
`post_save` transition in `auctions/signals.py` rather than the admin action, so
all three behave identically.

**Revoke approval for selected account(s)** reverses it, and also works as a
suspension. Revocation is silent; the user is not emailed.

`ADMIN_EMAIL` receives a "new account pending approval" message the moment a
user confirms their email, so nobody sits in the queue unnoticed. With
`ADMIN_EMAIL` unset the notification is skipped and pending accounts must be
found by filtering the admin.

### Locked out?

**Any staff account can always get in at `/admin/login/`.** That is Django's own
login view, so neither the verification nor the approval gate applies. It still
refuses non-staff accounts, so it is not a way around the gates — just a way in
for the people who administer them.

From the command line:

```bash
python manage.py unlock_account you@example.com            # verify the address
python manage.py unlock_account you@example.com --approve  # and clear approval
```

It accepts a username too, creates the `EmailAddress` row if one is missing, and
refuses to hand an address to a second account if another already has it
verified. Use it when confirmation mail isn't arriving — a misconfigured SMTP
setup makes every gate unrecoverable through the browser alone.

#### Upgrading an existing site

Two migrations grandfather in accounts that predate these gates. Both are needed:
the defaults are correct for new sign-ups but, applied to a populated database,
would lock out the entire current membership — paying subscribers included — at
their next login, with nothing in the UI explaining why.

- **`0013`** approves every `UserProfile` that exists when it runs.
- **`0014`** marks every existing account's email verified: rows left
  `verified=False` from when verification was optional, plus `createsuperuser`
  accounts that have no `EmailAddress` row at all. It respects allauth's
  `unique_verified_email` constraint — if two accounts share an address, the
  older one gets it and the newer is left unverified.

Both are one-way doors: rolling back and re-applying grandfathers in everyone
registered in between. `0014` also rubber-stamps any address a member never
actually confirmed, which is the deliberate trade — evicting known members is
worse. If a particular address looks suspicious, un-verify it in the admin under
**Accounts → Email addresses** afterwards.

---

## Background Scheduler

The APScheduler runs automatically when the development server starts. It runs two jobs:

| Job | Schedule | Description |
|---|---|---|
| `close_ended_auctions` | Every 5 minutes | Closes ended auctions, assigns winners, creates invoices, sends emails |
| `deactivate_old_listings` | Monday 03:00 | Sets `is_active=False` on listings ended more than 7 days ago |
| `expire_lapsed_subscriptions` | Daily 02:00 | Cancels memberships whose grace period has passed and emails the user |

Run any job manually at any time:

```bash
python manage.py close_ended_auctions
python manage.py close_ended_auctions --dry-run   # preview only

python manage.py deactivate_old_listings
python manage.py deactivate_old_listings --dry-run

python manage.py expire_lapsed_subscriptions
python manage.py expire_lapsed_subscriptions --dry-run
```

In production, the scheduler still starts automatically inside Gunicorn workers. If you
prefer an external cron, disable the scheduler and add cron entries instead:

```cron
*/5 * * * * /path/to/venv/bin/python /path/to/auction_site/manage.py close_ended_auctions
0   3 * * 1 /path/to/venv/bin/python /path/to/auction_site/manage.py deactivate_old_listings
```

---

## Memberships & PayPal

Bidding and purchasing require an active membership (a PayPal subscription). The
US-residents-only rule still applies on top of membership.

### One-time PayPal setup

1. Create a REST API app at the [PayPal Developer dashboard](https://developer.paypal.com/)
   (start in **Sandbox**). Copy the client ID and secret into `.env` as
   `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET`, and set `PAYPAL_MODE=sandbox`.
2. Set your prices via `PAYPAL_MONTHLY_PRICE` / `PAYPAL_YEARLY_PRICE`, then create
   the billing plans:

   ```bash
   python manage.py create_paypal_plans
   ```

   Copy the printed plan IDs into `PAYPAL_MONTHLY_PLAN_ID` / `PAYPAL_YEARLY_PLAN_ID`.
3. Register a webhook in the PayPal dashboard pointing at
   `https://yourdomain.com/paypal/webhook/` and subscribe to the
   `BILLING.SUBSCRIPTION.*` events. Copy the webhook ID into `PAYPAL_WEBHOOK_ID`
   (verification rejects all webhooks until this is set).

   For **local** webhook testing, expose your dev server with a tunnel and use
   that URL when registering the webhook:

   ```bash
   ngrok http 8000
   ```

4. **Going live:** repeat the steps with live credentials, set `PAYPAL_MODE=live`,
   re-run `create_paypal_plans`, and register a live webhook.

### What happens automatically vs. manually

| Event | Handled automatically (webhook/job) | Needs manual action |
|---|---|---|
| Subscriber completes checkout | Membership activated, welcome email | — |
| Renewal payment succeeds | Period extended, grace cleared | — |
| Payment fails | Status → lapsed, 3-day grace, email sent | — |
| Grace period expires | Daily job cancels membership, emails user | — |
| Subscriber cancels in PayPal | Status → cancelled, email sent | — |
| Comp / staff override | — | Uncheck **subscription required** on the user's profile, or use the Subscriptions admin actions |

Admins can manage memberships under **Users → Subscriptions** in the admin
(mark active/lapsed, exempt a user, require a subscription).

---

## Running the test suite

```bash
cd auction_site
python manage.py test auctions.tests
```

The suite covers subscription gating, the PayPal webhook handler, buy-now,
proxy bidding, registration security (rate limiting, Turnstile, mandatory email
verification, admin approval, two-factor authentication), multi-quantity
Buy It Now stock, and end-to-end integration flows. PayPal API calls, Turnstile
verification, and webhook signature verification are mocked, so no network or
credentials are needed.

---

## Deployment

### Collect static files

```bash
python manage.py collectstatic
```

### Media storage (Cloudflare R2)

Uploaded media (listing images) is stored locally in development. In production,
set the `CF_R2_*` variables and Django automatically switches the default file
storage to S3-compatible R2 (`storages.backends.s3boto3.S3Boto3Storage`); static
files are still collected locally and served by nginx. Verify the backend loads:

```bash
python manage.py shell -c "from django.core.files.storage import default_storage; print(default_storage.__class__)"
```

When R2 is active, `MEDIA_URL` points at the bucket (or `CF_R2_CUSTOM_DOMAIN`),
so the nginx `/media/` alias below is only needed for local-media deployments.

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
TURNSTILE_SITE_KEY=...
TURNSTILE_SECRET=...
PAYPAL_MODE=live
PAYPAL_CLIENT_ID=...
PAYPAL_CLIENT_SECRET=...
PAYPAL_MONTHLY_PLAN_ID=...
PAYPAL_YEARLY_PLAN_ID=...
PAYPAL_WEBHOOK_ID=...
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
