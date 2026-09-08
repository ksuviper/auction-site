# Security Audit — September 2026

A review pass over everything shipped in the backlog batches: the seller merge,
payment fields, listing copying and locking, category browsing, content pages,
combined invoicing, the multi-buy discount, and the site images. It re-checks
the registration and MFA hardening from before those batches, since several of
them touched `User` and `UserProfile`.

**This is a report, not a fix.** Nothing in the codebase was changed as a result
of it except the addition of `auctions/tests/test_security_audit.py`, which
turns the "confirmed clean" items below into 23 tests so they stay confirmed.
Everything under *Findings* is for a decision, in the order it should get one.

---

## Findings

### 1. Five dependencies have known vulnerabilities — **act on this first**

`pip-audit -r requirements.txt` reports **68 known vulnerabilities in 5
packages**. All have fixed versions available:

| Package | Pinned | Fix | Notes |
|---|---|---|---|
| `Django` | 6.0.3 | **6.0.8** | 26 advisories across five patch releases. Patch releases only — no behaviour changes expected. |
| `Pillow` | 12.2.0 | **12.3.0** | 20 advisories. Pillow decodes every uploaded listing photo, banner and app icon, so this is the most exposed of the five. |
| `PyJWT` | 2.12.1 | **2.13.0** | 8 advisories. Used by the PayPal webhook signature path. |
| `sqlparse` | 0.5.5 | **0.6.0** | 5 advisories. Django's SQL formatting dependency. |
| `cryptography` | 46.0.5 | **46.0.7** clears 4; **50.0.0** clears all 10 | Check `paypalrestsdk` still installs against whichever you pick. |

**Recommendation:** bump all five in `requirements.txt`, reinstall, run the
full suite (`python manage.py test auctions.tests`, 479 tests), and deploy.
Not done here because the brief asked for findings before changes, and a
Django bump deserves a deliberate deploy rather than riding along with an audit
commit. Re-run `pip-audit` afterwards; it should report nothing.

### 2. `check --deploy` — one warning, and it is a deployment check, not a code one

```
security.W009: Your SECRET_KEY has less than 50 characters ... prefixed with 'django-insecure-'
```

The code reads `SECRET_KEY` from the environment and falls back to an insecure
development default. The warning fires here because this checkout has no env.
**Confirm `SECRET_KEY` is set on the production server** (`SECURITY.md` has the
generation command). If it is, this warning does not apply there.

`W004` (HSTS) and `W008` (SSL redirect) are silenced in settings on purpose —
both are env-controlled and documented as opt-in after TLS is confirmed.

### 3. `SECURE_PROXY_SSL_HEADER` is set unconditionally

Django is told to trust `X-Forwarded-Proto: https` from whatever is in front of
it. That is correct **only** when a proxy always sets that header itself. The
documented nginx config does (`proxy_set_header X-Forwarded-Proto $scheme;`
overrides anything a client sends) and gunicorn binds to `127.0.0.1`, so as
deployed this is fine. It becomes a hole if gunicorn is ever exposed directly.

**Recommendation:** no code change. Keep gunicorn on loopback; if the hosting
ever changes, revisit this line.

### 4. Two endpoints that write are not rate limited

The four the brief lists — signup, login, bid, proxy bid — all still are (see
*Confirmed clean*). Two others never were:

- **`BuyNowView`** — protected by the membership gate and the atomic stock
  decrement, so hammering it cannot oversell. Low.
- **`PostCommentView`** — every submission emails the admin and, if enabled, the
  seller. A logged-in member could flood both inboxes. Low, but it is the one
  unauthenticated-to-inbox path left. A `10/m` per-user limit like the bid views
  would close it.

**Recommendation:** add the comment limit; it is one decorator. Your call on
Buy It Now.

### 5. `robots.txt` advertises a sitemap that does not exist

It ends with `Sitemap: https://<host>/sitemap.xml`, and there is no such route.
Crawlers get a 404. Harmless, untidy. It also predates `/admin/combined-invoices/`,
which is covered by the existing `Disallow: /admin/` prefix anyway.

**Recommendation:** drop the `Sitemap:` line, or add a sitemap.

### 6. Admin-edited page bodies render as HTML — *decision needed*

`SitePage.body` (About Us, Terms, Privacy) and `FAQItem.answer` render with
`|safe`, so an admin can write headings, lists and links. Only staff can edit
them, and staff already have full admin access, so this is trusted input in the
same sense the admin itself is. It does mean a page body is not somewhere to
paste markup from an untrusted source, and a compromised staff account could
inject script into a public page.

**Recommendation:** either accept this (the README documents it) or switch to
escaped text — in which case the seeded Privacy Policy's headings and links show
as visible tags and it needs rewriting as plain prose. Flagged in the content
pages batch; restated here because it is the one deliberate `|safe` on
public-facing pages.

### 7. Phone numbers are visible to every staff account — *decision needed*

`UserProfileAdmin` lists `phone_number` on the changelist, and the change form
shows `address`. Any account with `is_staff` sees them. Staff coordinate
shipping and invoicing, so they plausibly need both — but the brief asked
whether non-superuser staff should. Nothing else exposes either field: they
appear on the member's own profile page and nowhere public (tested).

**Recommendation:** leave as is if every staff account is trusted with
shipping details, which for a club of this size is likely. If not, drop
`phone_number` from `list_display` and the field stays on the change form only.

### 8. Where buyer and seller email addresses travel — *for awareness*

Two places show one party's email to the other, both intentional:

- The **seller notification** at auction close and at Buy It Now purchase
  includes the buyer's email, so the seller can arrange shipping.
- The **invoice page** shows the seller's email to the buyer named on it.

Neither is public. The seller dashboard deliberately shows buyer *names* and
not their contact details (tested). If you would rather sellers only ever
reached buyers through an admin, the seller notification is the line to change.

---

## Confirmed clean

Each item here is a passing test in `auctions/tests/test_security_audit.py`
unless marked otherwise.

**Registration hardening — intact, and the seller flag bypasses none of it**

- `ACCOUNT_EMAIL_VERIFICATION = 'mandatory'`; `ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = False`
  so confirming an email cannot skip the approval gate.
- Signup rate limit: 5/hour per IP, hard 403 on the sixth.
- Turnstile fails closed; the secret is read via `os.getenv` inside
  `verify_turnstile` and is not a settings constant.
- An **unapproved seller-flagged account cannot log in**; a **seller with an
  unverified email cannot log in**; `needs_admin_approval()` never reads
  `is_seller`.

**MFA — intact**

- `ACCOUNT_LOGIN_BY_CODE_REQUIRED = {'password'}`; passwordless login-by-code is off.
- A seller-flagged member still receives the emailed code.
- A **seller-flagged staff account cannot switch the code off** (403 server-side).
- TOTP replaces the email code; recovery codes alone do not (existing `test_mfa.py`).

**Rate limiting — all four original endpoints**

- Signup, login, bid, proxy bid all carry the decorator, and `/accounts/login/`
  and `/accounts/signup/` resolve to the limited views rather than allauth's.

**Combined invoicing — authorisation**

- Generate, review and send are all `StaffRequiredMixin`; a seller-flagged
  member gets 403 on every one, including the POST.
- A buyer cannot open another buyer's invoice (403). **The seller on an invoice
  cannot open it either** — selling the plant does not make the buyer's bill
  yours to read.
- Two staff requests for two different invoices each see only their own
  buyer's details: no module-level state, no shared context.
- A sent invoice cannot be edited or deleted (existing `test_combined_invoices.py`).

**Seller dashboard**

- Non-sellers are bounced home; a seller sees only their own listings and
  revenue, with a second seller's data absent.
- Shows buyer names, **not** emails, phones or addresses.

**Personal data on public pages**

- Home, category, seller page, listing page, FAQ and About carry no phone
  number, address or payment handle belonging to any seller or buyer.
- Payment handles appear only on a billed invoice, to its buyer or staff
  (existing `test_payment_methods.py`).

**Secrets and settings** *(by inspection and test)*

- `DEBUG` defaults off when the env says nothing.
- `SECRET_KEY`, `PAYPAL_CLIENT_SECRET`, `EMAIL_HOST_PASSWORD` are `os.getenv`
  reads; a repo-wide grep finds no literal secret in any tracked file; `.env`
  is git-ignored and only `.env.example` is committed.
- `X_FRAME_OPTIONS = 'DENY'`, `SECURE_CONTENT_TYPE_NOSNIFF = True`; session and
  CSRF cookies are `Secure` whenever `DEBUG` is off.

**Uploads** *(by inspection)*

- The banner and app icon are `ImageField`s, so Pillow must decode them —
  an SVG is rejected, which closes the SVG-with-script route to a public image.
- Every `mark_safe` in the admin wraps a storage URL or a `reverse()` result,
  never user text, and all are staff-only.

---

## Not applicable to this codebase

The brief covers several systems that were never built here:

| Brief item | Status |
|---|---|
| Admin-managed **PayPal pricing sync** and "Sync to PayPal" retry | Does not exist. Membership prices are `PAYPAL_MONTHLY_PRICE` / `PAYPAL_YEARLY_PRICE` env settings; there is no admin price editor and no sync. |
| **Seller-follow / push notifications**, notify-followers action | Not built. |
| **VAPID keys** | Not built (no push). |
| **PWA** icon generation | Not built; see the README note under *Site images*. |

---

## How to re-run this audit

```bash
cd auction_site
python manage.py test auctions.tests.test_security_audit   # 23 checks
python manage.py check --deploy
pip-audit -r ../requirements.txt
```
