# Handoff from Claude Code — ASQ Daylily Auction

Written for a planning conversation that cannot see this repository. Everything
here was checked against the code at commit `0d3249b` (October 2026). No secrets,
keys or credential values appear in this file. Environment variables are listed
by name only.

**How reliable each part is:**

- **Runs from 7 Sept 2026 onward** (backlog items 1–21, the audit follow-ups and
  the four-item batch): written by the session that built them, and checked
  against their prompts where those prompts were still available.
- **Older commits** (May–August 2026): reconstructed from commit messages
  only, because the prompts are not available. Treat their "requested" column
  as inferred.

---

## a. Every run and commit: requested vs. built

### Sept–Oct 2026 (most recent first)

| Commit | Date | Requested | Built | Deviations and why |
|---|---|---|---|---|
| `0d3249b` | 18 Oct | **Item 4: Push notifications.** VAPID keys, `PushSubscription` and `SellerFollow`, `sw.js` push handlers, opt-in UI, follow/unfollow, push alongside the outbid, close and Buy It Now emails, a "Notify followers of new listings" email plus push, tests, README. | All of it. Migration `0027`; `/account/notifications/`; follow button on seller pages; `send_push_to_user()`; `notify_followers_of_new_listings()`; `generate_vapid_keys` command; `SERVICE_WORKER_VERSION` bumped to `v2`; 81 tests. | **Where the opt-in lives:** its own page, `/account/notifications/`, not a section of Login & Security, because that page is about signing in. It is linked from Profile and from Security.<br>**Seller model:** there is no `Seller` model, so `seller__name` became first/last/username, and `SellerProfileView` is really `SellerListingView`.<br>**Notify action:** goes on the Users admin list *and* on Add Listing (client's choice). Both lead to a **confirmation page**; nothing sends in one click, because emails cannot be unsent. The page shows when followers were last notified (new field `UserProfile.followers_notified_at`).<br>**Constraints:** `unique_together` became a `UniqueConstraint`, plus a `CheckConstraint` that forbids following yourself.<br>**Winner push:** sent at close although the winner gets **no email** at close (combined invoicing removed that in `2814c9a`). It says "invoice will follow" and asks for no money.<br>**Buy It Now buyer push:** the buyer gets a receipt push, also without any buyer email.<br>**iOS check order:** the iOS-not-installed check runs *before* the "no PushManager" check. Otherwise iPhone users in Safari would be told "unsupported" instead of "install first".<br>**Outbid push timing:** deferred with `transaction.on_commit`, so no network call happens while the listing row is locked.<br>**Additions not in the prompt:**<ul><li>an endpoint host allow-list (SSRF guard)</li><li>P-256 key validation</li><li>a 10-device cap</li><li>a 30/h rate limit on subscribe</li><li>per-device "Turn off"</li><li>notification `tag` collapsing</li><li>12h TTL and 5s timeout</li><li>deactivated accounts skipped</li></ul>**Private key:** read from the environment at send time, never held in settings (same pattern as Turnstile).<br>**Not done:** real Android/iPhone testing (needs the client). |
| `d6007ba` | 17 Sep | **Item 3: PWA install banner.** Dismissible bar fixed to the top; Install button on `beforeinstallprompt`; instructions on iOS; 14-day dismissal; hidden above 992px. | All behaviours, `static/js/install-banner.js`, 26 tests. | **Position:** in normal page flow, **not `position: fixed`**. The nav is `sticky-top`, so a fixed bar would cover it.<br>**Wording:** the site name comes from the branding row instead of hard-coded "ASQ Daylily Auctions".<br>**iPad detection:** added for iPadOS, which reports itself as a Mac.<br>**The prompt's "accessibility bar"** to match styling against **does not exist**.<br>**Not done:** real-device testing. |
| `49a1824` | 17 Sep | **Items 2a + 2b, combined (client's choice).** 2a: seller-only plans, `plan_audience`, seller PayPal env vars. 2b: `SubscriptionPlan` model, admin price editing that calls PayPal *Update Pricing Schemes*, and a "Sync to PayPal" retry action. | `SubscriptionPlan` (migration `0026` seeds 4 rows from env). Admin `save_model` pushes to PayPal first and **refuses to save** on failure. Seller pricing ships **off**: sellers see "not available yet" plus the contact address (client's choice). | **`is_seller`:** already existed from the seller merge, so it was not added again.<br>**Price source:** prices come from the database, not the seller env vars, which are seed-only.<br>**Retry action:** named **"Re-send the price to PayPal"**.<br>**Seeding:** done by a data migration, not a `seed_subscription_plans` command.<br>**PayPal behaviour:** a price change **reaches existing subscribers from their next billing date**. This was confirmed by web search because developer.paypal.com was blocked, and the admin shows the warning.<br>**Also fixed:** four multi-line `{# #}` comments that were rendering as visible page text, plus a template-hygiene test.<br>**Not done:** PayPal sandbox walkthrough (no credentials; all PayPal calls are mocked in tests). |
| `c20ee45` | 17 Sep | **Item 1: Photo cut off on the listing detail page.** Do not touch the grid. | Detail image uses `max-width:100%; max-height:30rem; width/height:auto; object-fit:contain`. Separate placeholder class. Grid untouched. | **Max height:** 30rem (480px), not the "~500px" suggested.<br>**Also fixed:** a `.gitignore` media rule that never matched (`**/media/`).<br>**Not verified:** that R2 in production behaves the same (no R2 access). There is no lightbox, so nothing there to fix. |
| `6588424` | 17 Sep | Client: "Install is greyed out on Android and missing on desktop." | Manifest, 192/512 plus maskable icons, root-scoped `sw.js` that caches **only** `/offline/`, and an offline page. | **Icons and manifest are generated on request** from the `SiteSettings` branding row with Pillow (cached by `updated_at`). The originally planned approach was a `generate_pwa_icons` management command writing static files. Generating on request means a rename or new upload in the admin updates the app without a deploy.<br>**Icon sizes:** only 192 and 512 are served (anti-abuse).<br>**Theme colour:** fixed to `#212529`, not admin-editable.<br>**HTTPS** cannot be fixed from code and is documented.<br>**The install banner** was *not* part of this commit; it came later in `d6007ba`. |
| `1d39a15` | 17 Sep | Make home page content editable. | Welcome and steps heading are `SitePage` rows; step cards are a new `HomePageStep` model. Migration `0025` seeds the old wording. | `/legal/home/` redirects to `/`. Every lookup falls back to the shipped wording. |
| `525d3ff` | 17 Sep | Site name and slogan editable in admin, plus a third line. | `SiteSettings.site_name`, `site_slogan`, `site_extra_line` (migration `0024`). A blank field hides that line. | **Partial:** the browser tab title, meta description and footer still hard-code the name, in about 20 templates. Deliberately deferred. |
| `346f8ff` | 17 Sep | Profiles cannot be added or deleted on their own; users with bids, purchases etc. cannot be deleted. | Profile add/delete is closed in the admin, with a model backstop. `user_activity()` blocks deleting an account with history and names the reason. | A profile is still deleted along with its user. Wishlists, MFA devices, email addresses, push devices and follows are treated as part of the account. |
| `63ded79` | 17 Sep | "Do those three changes" (consolidate Users vs. User profiles). | Opening a profile's change page redirects to its user page. The inline shows the full profile. The unfiltered "User Profiles" sidebar entry is removed; the profile list stays, as the Pending Approval and Sellers worklists. | The profile list was kept because bulk `is_approved` editing only works on the profile changelist. |
| `c7aed95` | 14 Sep | Stop the FAQ email being scrapable. | `protected_email` tag and `protect_emails` filter (address reversed and base64-encoded, rebuilt by `protected-email.js`). `CONTACT_EMAIL` setting. | Applied to **every** public page that published the address, not just the FAQ. It raises the cost of harvesting but does not prevent it. |
| `ccad3ae` | 14 Sep | (Client pasted a PostgreSQL migrate traceback.) | Migration `0018` gets `check_constraints()`; `select_for_update(of=('self',))` in BuyNowView and the close job. | Found that **every Buy It Now failed on PostgreSQL** while the SQLite tests passed. |
| `7616c1f` | 14 Sep | (Client's `.gitignore` conflict.) | Ignore `*.sqlite3` and its journal. | — |
| `7fdcb29` | 14 Sep | Apply the audit's dependency fixes; fix `robots.txt`. HTML rendering and phone numbers accepted; emails deferred. | Django 6.0.8, Pillow 12.3.0, PyJWT 2.13.0, sqlparse 0.6.0, cryptography 50.0.0. Sitemap line removed. | **`fido2` bumped** to 2.2.1 as well, because its old pin capped `cryptography`. |
| `b20a42c` | 8 Sep | Item 21: security audit. | `SECURITY_AUDIT.md` plus `test_security_audit.py`. Report only; no fixes. | Areas in the brief that did not exist then (PayPal price sync, push, VAPID, PWA) were reported as "not applicable". That table in the audit file is now **stale**: all four have since been built. |
| `f028b5b` | 7 Sep | Items 18–20: admin-managed banner and app icon; Membership moved to the profile page; welcome banner reorder. | `SiteSettings` singleton; Membership on the profile page; lapsed warning kept in the nav. | **Not done at the time:** multi-size PWA icon generation, because no PWA existed. Later built differently in `6588424`. Also fixed an overflow bug with wide banners. |
| `3f68edb` | 7 Sep | Item 17: per-listing multi-buy discount. | `additional_item_discount`; `price_for(quantity)`. | — |
| `2814c9a` | 7 Sep | Item 16: combined invoices. | `CombinedInvoice`; generate, review and send in the admin. | **Breaking:** winners no longer get an email at close, and Buy It Now buyers get no email at purchase. The buyer's invoice page says "nothing to pay yet" until the invoice is sent. |
| `171e8d8` | 7 Sep | Items 12–15: FAQ, content pages, homepage redesign. | `FAQItem`, `SitePage`, `/legal/<slug>/`, `/about/`. Active Auctions removed from the homepage. | `/privacy/` now redirects (OAuth apps point at it). Data-deletion page stays static. Bodies render with `\|safe` (audit finding 6). |
| `3268325` | 7 Sep | Item 11: category pages list sellers. | `auctions/queries.py`. Sellers are grouped by **profile** category, not listing category. | — |
| `675dbf3` | 7 Sep | Items 6–10: payment fields, rename to "Add Listing", copy listing, lock sold listings. | Six payment fields; `copy_listing()`; sold listings are read-only. | **Rename is UI-only:** "Weekly Setup" became "Add Listing" in the interface; URL names and view classes still say `weekly_setup*`.<br>**Payment handles** show only on a billed invoice. |
| `61e8519` | 7 Sep | Items 1–5: merge the Seller model into User. | A seller is a `User` with `profile.is_seller`. `/seller/dashboard/` added. | **Migration `0018` deleted every listing, bid, proxy bid, comment and invoice** together with the Seller table (documented, deliberate). |

### Earlier history (reconstructed from commit messages)

| Commit | Date | What the commit message says was done |
|---|---|---|
| `e2d5b91` | 15 Aug | Per-listing Buy It Now shipping fee, falling back to the seller's fee (`shipping_rate`). |
| `3ce7949` | 15 Aug | Bulk listing formset replaced by one-at-a-time entry. The old formset could never save (it had two faults). |
| `5b720aa` | 4 Aug | Multi-quantity Buy It Now: an atomic conditional-UPDATE stock decrement; `winner` is not set for buy_now; reporting moved onto `Invoice`. |
| `51f4745` | 2 Aug | 2FA: an emailed login code for every password login (mandatory for staff) plus optional TOTP and recovery codes. |
| `3544d20` | 29 Jul | Fixed the lockout caused by mandatory email verification (migration `0014`); added the `unlock_account` command. |
| `ee90e23` | 29 Jul | Approval gate surfaced in the admin: "Pending Approval" sidebar entry with a badge. |
| `0f95672` | 29 Jul | Admin approval required before login (`is_approved`, migration `0013` approves existing users). |
| `2a2d737` | 29 Jul | Registration hardening: mandatory email verification, 5/h signup rate limit, Cloudflare Turnstile. |
| `57c6a3f` | 28 Jul | Merge of PR #1 from `dev`. |
| `bfaef99` | 5 Jun | Prompt 9: integration tests and QA. |
| `63a62eb` | 5 Jun | Per-seller toggle for comment notifications. |
| `fd39600` | 5 Jun | Prompt 8: listing comments and questions. |
| `fab5011` | 5 Jun | Prompt 7: proxy (automatic) bidding. |
| `e9ddaa5` | 5 Jun | Buy It Now restricted to US residents. |
| `10f316f` | 5 Jun | Prompt 6: Buy It Now listings. |
| `45cb1bf` | 5 Jun | Prompt 5: membership management page and expiry job. |
| `461ae50` | 5 Jun | Prompt 4: PayPal webhook handler. |
| `20f6304` | 5 Jun | Prompt 3: PayPal subscribe checkout. |
| `ba67d53` | 5 Jun | Prompt 2: subscription utility, decorator, bid gating. |
| `7c1cb20` | 5 Jun | Prompt 1: subscription model and PayPal plan setup. |
| `a9f6b39` | 4 Jun | Stopped tracking build artifacts. |
| `0962306` | 19 May | Fixed duplicate invoices when several gunicorn workers run the close job (row lock). |
| `c6ba248`, `404da1b` | 12 May | Privacy and data-deletion pages; **Microsoft login removed**. |
| `94a6583` | 11 May | Email configured for Resend SMTP. |
| `29ac537` | 11 May | Bidding restricted to US residents. |
| `577b177`, `4041005`, `66a3c15`, `7e15993` | 11 May | Category slug auto-generation; category FK removed from the (now deleted) Seller model. |
| `4e7a247`, `780052e`, `0116276`, `57dbe18`, `831b5ff`, `81140e9`, `bbf9bdf`, `8f7a3ae`, `94805ac` | 11 May | Signup and login templates, first/last name fields, social buttons. |
| `81bdde7`, `ce19f62`, `2660362` | 11 May | "Back to main site" link in the Unfold sidebar. |
| initial commits | before May | Base Django project: listings, bids, invoices, admin. |

---

## b. Security audit (September 2026, `b20a42c`; outcomes recorded in `7fdcb29`)

| # | Finding | Severity (as reported) | Status | Left for the client |
|---|---|---|---|---|
| 1 | 68 known vulnerabilities in Django 6.0.3, Pillow 12.2.0, PyJWT 2.12.1, sqlparse 0.5.5 and cryptography 46.0.5. | High (Pillow decodes every upload) | **Fixed** in `7fdcb29`; `pip-audit` was clean afterwards. `pywebpush`, `py-vapid` and `http-ece` were added since and have **not** been re-audited. | Re-run `pip-audit` periodically. |
| 2 | `check --deploy` W009: insecure `SECRET_KEY` fallback when the env var is missing. | Deployment config | Not a code fix. HSTS (W004) and SSL redirect (W008) are silenced on purpose and controlled by env. | Confirm `SECRET_KEY` is set on the production server. |
| 3 | `SECURE_PROXY_SSL_HEADER` is trusted unconditionally. | Low as deployed | No change. Safe only while gunicorn binds to 127.0.0.1 behind nginx. | Revisit if the hosting changes. |
| 4 | `PostCommentView` (each post emails the admin and seller) and `BuyNowView` are not rate-limited. | Low | **Still open.** Neither has a `ratelimit` decorator today. | Add `10/m` per user on comments (recommended); decide on Buy It Now. |
| 5 | `robots.txt` pointed at a nonexistent `/sitemap.xml`. | Low | **Fixed**; a test checks any Sitemap line resolves. | — |
| 6 | `SitePage.body` and `FAQItem.answer` render with `\|safe` (staff-authored HTML). | Medium if a staff account is compromised | **Accepted** by the client. | Revisit if staff access widens. |
| 7 | Phone numbers are visible to all staff on the profile changelist. | Low | **Accepted** by the client. | — |
| 8 | Buyer and seller emails go to each other: the seller notification at close and at Buy It Now includes the buyer's email; the invoice page shows the seller's email to its buyer. | Privacy decision | **Deferred** by the client: "to be looked at again later". | Decide whether contact should go through an admin only. The line to change is the seller notification. |

**Confirmed clean.** `test_security_audit.py` has 25 tests covering:

- registration gates (the seller flag bypasses none of them);
- MFA rules;
- the four original rate limits (signup 5/h per IP, login 5/m per IP, bid and proxy bid 10/m per user);
- invoice authorisation (the seller on an invoice cannot read it);
- the seller dashboard hiding buyer contact details;
- no personal data on public pages;
- `DEBUG` off by default;
- secrets read from the environment.

**Not covered by the audit** because built afterwards: PWA, subscription pricing sync, push notifications. Run 4 added its own security tests (endpoint allow-list, key validation, CSRF, cross-user unsubscribe). Nobody has independently audited them.

---

## c. Skipped, partial, or not possible

**Not done because they need the client's hardware or credentials:**

- **Real-device testing.** Push on Android and iPhone, the install banner on real devices, iOS Home Screen push.
- **PayPal sandbox.** Price change, checkout at the new price, seller checkout. All PayPal calls are mocked in tests.
- **VAPID keys.** Not generated for production. Until `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY` are set, push is silently off and the settings page says alerts are "not available yet".
- **Seller membership prices.** Never set. Seller memberships show "not available yet".

**Partial or still open:**

- **Site name hard-coding.** The tab titles, meta description and footer still hard-code the site name (`525d3ff`). Titles are also inconsistent: some say "– Above Status Quo", others "– ASQ Daylily Auctions".
- **Audit finding 4.** The comment rate limit was never added.
- **Audit finding 8.** The email-exposure decision is deferred.
- **Combined deploy.** No documented single "upgrade the server" sequence covers runs 1–4 together. Migrations `0024`–`0027` and new requirements all need applying (see CLAUDE.md).

**Not possible from code:** HTTPS. A PWA install and push both require it, and it is a server and certificate matter.

---

## d. Known bugs, test gaps, unverified assumptions

**Test status:** 803 tests. All pass on SQLite (5 skipped: concurrency tests SQLite cannot run) and all 803 pass on PostgreSQL 16. Nothing is currently failing.

**Known issues and gaps:**

- **Shared devices keep receiving push.** Logging out does not unsubscribe a browser. The previous user's pushes keep arriving on that device until someone else subscribes on it or it is turned off.
- **The scheduler runs in every gunicorn worker** (README suggests 3 workers). The close job is protected by a row lock. `deactivate_old_listings` and `expire_lapsed_subscriptions` run once per worker; they are assumed idempotent but have not been verified under concurrency.
- **Push sends are synchronous** inside the request for Buy It Now and in the close job: up to 5 seconds' timeout per device, with a cap of 10 devices per user. There is no task queue.
- **Follower counts disagree slightly.** The Add Listing panel counts deactivated accounts; the confirmation page and the actual send exclude them.
- **`psycopg2` is missing from `requirements.txt`**, though production uses PostgreSQL. The README notes this.
- **Unpinned packages.** `requests` and `aiohttp` (pulled in by `pywebpush` 2.x) are not pinned.
- **README errors:**
  - the env table lists `EMAIL_HOST`'s default as `smtp.gmail.com`, but the code defaults to `smtp.resend.com`;
  - the PostgreSQL section still says "484 tests".
- **`SECURITY_AUDIT.md` is stale.** Its "Not applicable" table still lists PayPal sync, push and VAPID as not built.

**Unverified assumptions:**

- The push endpoint allow-list (`fcm.googleapis.com`, `android.googleapis.com`, `push.services.mozilla.com`, `push.apple.com`, `notify.windows.com`) covers every browser members use. Any other push service gets a 400 on subscribe.
- The R2-served photo displays the same as locally.
- Chrome's real `pushManager.subscribe()` was never completed: the sandbox cannot reach Google's registration service, so the subscribe path was tested with a stubbed subscription. A real FCM send was tested only against a made-up device (410, correctly switched off).
- The production Python version is unknown. Local runs used Python 3.13.

---

## e. Current state

### Migrations (latest per app)

| App | Latest |
|---|---|
| `auctions` | `0027_push_notifications` |
| `account` (allauth) | `0009_emailaddress_unique_primary_email` |
| `socialaccount` | `0006_alter_socialaccount_extra_data` |
| `mfa` | `0003_authenticator_type_uniq` |
| `django_apscheduler` | `0009_djangojobexecution_unique_job_executions` |
| `auth` | `0012_alter_user_first_name_max_length` |
| `admin` | `0003_logentry_add_action_flag_choices` |
| `contenttypes` | `0002_remove_content_type_name` |
| `sessions` | `0001_initial` |
| `sites` | `0002_alter_domain_unique` |

Migrations to know about:

- `0013`: approves existing users.
- `0014`: backfills verified emails.
- `0016`: Buy It Now stock backfill.
- `0018`: **destructive** seller merge.
- `0024`: header text fields.
- `0025`: seeds home page content.
- `0026`: seeds the 4 `SubscriptionPlan` rows from env.
- `0027`: push and follows.

### Scheduled jobs

APScheduler runs in-process, started from `AuctionsConfig.ready()`. It is skipped for tests and management commands; timezone is `America/Chicago`.

| Job id | Schedule | Command |
|---|---|---|
| `close_ended_auctions` | every 5 min | `close_ended_auctions` (close, invoice, seller/admin emails, pushes) |
| `deactivate_old_listings` | Mon 03:00 | `deactivate_old_listings` |
| `expire_lapsed_subscriptions` | daily 02:00 | `expire_lapsed_subscriptions` |

Other management commands: `create_paypal_plans`, `unlock_account`, `generate_vapid_keys`.

### Environment variables the app reads (names only)

- **Core:** `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `CSRF_COOKIE_SECURE`, `SESSION_COOKIE_SECURE`
- **Database:** `DATABASE_ENGINE`, `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_HOST`, `DATABASE_PORT`
- **Email:** `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`, `ADMIN_EMAIL`, `CONTACT_EMAIL`
- **Social login:** `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `FACEBOOK_APP_ID`, `FACEBOOK_APP_SECRET`
- **PayPal:** `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_MODE`, `PAYPAL_WEBHOOK_ID`
- **PayPal seed-only** (read once by migration `0026`): `PAYPAL_MONTHLY_PLAN_ID`, `PAYPAL_YEARLY_PLAN_ID`, `PAYPAL_MONTHLY_PRICE`, `PAYPAL_YEARLY_PRICE`, `PAYPAL_SELLER_MONTHLY_PLAN_ID`, `PAYPAL_SELLER_YEARLY_PLAN_ID`, `PAYPAL_SELLER_MONTHLY_PRICE`, `PAYPAL_SELLER_YEARLY_PRICE`
- **Cloudflare R2:** `CF_R2_ACCOUNT_ID`, `CF_R2_ACCESS_KEY_ID`, `CF_R2_SECRET_ACCESS_KEY`, `CF_R2_BUCKET_NAME`, `CF_R2_CUSTOM_DOMAIN`
- **Turnstile:** `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET` (the secret is read per call, not in settings)
- **Web Push:** `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` (read at send time, not in settings), `VAPID_CLAIM_EMAIL`
- **HTTPS hardening:** `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`

### Third-party integrations

| Integration | State |
|---|---|
| **PayPal Subscriptions** | **Built.** Checkout uses `auctions/paypal.py` (`paypal_request`, plain REST via `requests`). Webhook signature verification uses `paypalrestsdk`. Prices live in `SubscriptionPlan` and are changed in the admin, which pushes them to PayPal. Never sandbox-tested here. |
| **Cloudflare Turnstile** | **Built** on email/password signup only. Fails closed without the secret unless `DEBUG` is on. |
| **Cloudflare R2** | **Built** as media storage via `django-storages` S3, active when the `CF_R2_*` vars are set. Static files stay on nginx. |
| **Resend** | **Built** as SMTP (`smtp.resend.com` default host). Email is load-bearing: verification, login codes, approvals and invoices all depend on it. |
| **Web Push (VAPID)** | **Code built** (`pywebpush` 2.5.0), but **keys are not configured in production**, so push is off. |
| **Google and Facebook OAuth** | **Built** (allauth). Microsoft was removed in May. |

---

## f. Do NOT assume these exist

**Removed or never existed:**

- **No `Seller` model.** A seller is a `User` with `profile.is_seller = True`.
- **No `SellerProfileView`.** The public seller page is `SellerListingView` (`seller_listings`).
- **No Microsoft login.** It was removed.
- **No `seed_subscription_plans` command.** Seeding is migration `0026`.
- **No "Sync to PayPal" action.** It is called "Re-send the price to PayPal".

**PWA:**

- **No static PWA files.** There is no `static/manifest.json` and no `static/icons/`, and no `generate_pwa_icons` command. The manifest is served dynamically at `/manifest.webmanifest` (`pwa_manifest`) and the icons at `/pwa/icon-192.png`, `/pwa/icon-512.png` and their `-maskable` variants (`pwa_icon`, `pwa_icon_maskable`).
- **No admin-editable theme colours.** The PWA theme colour is fixed.
- **No caching in the service worker** beyond `/offline/`. That was a deliberate decision.

**Notifications:**

- **No wishlist alerts.** The `Wishlist` model and admin exist (`listing_title_keyword`, `notified`), but **no code matches listings to wishlists or notifies anyone**.
- **No automatic new-listing announcements.** "Notify followers" is manual and per seller.
- **No push for comments, approvals or invoices.** Push covers only outbid, auction close, Buy It Now and new listings.
- **No email to winners at close or to buyers at purchase.** The combined invoice is the buyer's notification.

**Missing features:**

- **No seller self-service.** Sellers cannot create or edit listings; admins do it through Add Listing. The seller dashboard is read-only.
- **No sitemap.**
- **No contact form.**
- **No browse-all-listings page.** The query exists (`queries.active_listings()` with no arguments), but there is no page.
- **No WebAuthn or passkeys.** MFA is TOTP plus recovery codes only, even though `fido2` is installed.
- **No high-contrast or large-text accessibility bar**, although an earlier prompt referred to one.
- **No admin control for page titles, meta description or footer.** The site name is still hard-coded there.

**Infrastructure:**

- **No task queue** (Celery/Redis). Everything runs in-request or in the in-process scheduler.
- **No REST API.**
- **No PostgreSQL driver in `requirements.txt`.**

---

## g. Hook points for new features

### Models (`auctions/models.py`)

- **`UserProfile`**, one-to-one with `User`, `related_name='profile'`. Fields:
  - account: `is_approved`, `is_seller`, `subscription_required`, `email_login_code_enabled`, `country`, `phone_number`, `address`
  - seller: `seller_bio`, `seller_shipping_fee`, `seller_category`, `seller_active_week`, `seller_notify_on_comments`, `followers_notified_at`
  - payment: `paypal_info`, `venmo_info`, `zelle_info`, `cashapp_info`, `apple_pay_info`, `google_pay_info`, `seller_payment_methods`
  - helpers: `display_name`, `active_listings()`, `payment_options`, `payment_method_names`
- **`AuctionListing`**:
  - FKs: `seller` (User), `category`, `winner`
  - fields: `listing_type` (`auction` / `buy_now`), `start_price`, `current_bid`, `buy_now_price`, `quantity_available`, `quantity_remaining`, `additional_item_discount`, `shipping_mode`, `shipping_fee`, `starts_at`, `ends_at`, `is_active`, `is_closed`
  - helpers: `price_for()`, `shipping_rate`, `shipping_for()`, `units_remaining`, `has_completed_sale`
- **Bidding:** `Bid`, `ProxyBid`
- **Invoicing:**
  - `Invoice`: one per sale; `combined_invoice` null means unbilled.
  - `CombinedInvoice`: draft or sent, one draft per buyer and seller.
- **Content:** `ListingComment`, `Wishlist`, `AuctionCategory`, `SiteSettings` (singleton, pk=1, `SiteSettings.load()`), `FAQItem`, `SitePage` (`HOMEPAGE_SLUGS`), `HomePageStep`
- **Membership:**
  - `SubscriptionPlan`: `audience`, `billing_cycle`, `price`, `paypal_plan_id`, `is_active`; helpers `offered_to(user)`, `audience_for(user)`.
  - `Subscription`: `plan`, `plan_audience`, `status`, `grace_period_end`.
- **Notifications:**
  - `PushSubscription`: `user`, `endpoint`, `p256dh_key`, `auth_key`, `user_agent`, `is_active`.
  - `SellerFollow`: `user`, `seller`.
- **Account deletion helpers:** `user_activity(user)`, `describe_user_activity(user)`, `ProfileDeletionNotAllowed`

### Service and helper functions

- **`auctions/services.py`:** `run_proxy_bids(listing)`, `copy_listing(source)`, `generate_combined_invoices()`, `send_combined_invoice(ci, request=None)`, `notify_followers_of_new_listings(seller, request=None)`, `COPIED_LISTING_FIELDS`
- **`auctions/utils.py`:**
  - email and push: `_safe_send(subject, body, recipients)` (email only; never raises), `send_push_to_user(user, title, body, url='/', tag=None)` (never raises), `push_is_configured()`, `is_allowed_push_endpoint()`
  - display: `seller_display_name(user)`, `payment_block(profile)`
  - gates: `has_active_subscription(user)`, `needs_admin_approval(user)`
  - other: `verify_turnstile(token, remote_ip)`, `client_ip(request)`, `update_paypal_plan_price(plan, price)`
- **`auctions/queries.py`:** `active_listings(category=None, seller=None)`, `sellers_in_category()`, `sellers_grouped_by_category()`
- **`auctions/paypal.py`:** `paypal_request(method, path, ...)`, `get_access_token()`
- **`auctions/signals.py`:** creates the profile on user creation; sends the approval email on the `is_approved` transition; notifies the admin of a pending approval; sends comment reply notifications.
- **`auctions/decorators.py`:** `subscription_required`
- **`auctions/mixins.py`:** `StaffRequiredMixin`
- **`auction_site/pwa.py`:** `SERVICE_WORKER_VERSION`, `manifest`, `icon`, `service_worker`, `offline`
- **`auction_site/context_processors.py`:** `site_settings`, `sidebar`

### Views and URL names

**Public and member pages:**

| URL name | View / path |
|---|---|
| `home` | `auction_site/views.index` |
| `faq` | `FAQView` |
| `about_us` | `SitePageView` |
| `site_page` | `/legal/<slug>/` |
| `privacy_policy` | `/privacy/` redirect |
| `data_deletion` | static page |
| `category_listings` | `CategoryListingView` |
| `seller_listings` | `SellerListingView`, `/seller/<pk>/` |
| `seller_dashboard` | `SellerDashboardView` |
| `listing_detail` | `ListingDetailView` |
| `place_bid` | `PlaceBidView` |
| `place_proxy_bid` | `PlaceProxyBidView` |
| `buy_now` | `BuyNowView` |
| `post_comment` | `PostCommentView` |
| `invoice_detail` | `InvoiceDetailView` |
| `profile` | profile page |
| `profile_edit` | profile edit |
| `security_settings` | Login & Security |
| `toggle_email_login_code` | login-code toggle |
| `account_pending_approval` | pending approval page |

**Notifications:**

| URL name | View |
|---|---|
| `notification_settings` | notification settings page |
| `push_subscribe` | subscribe endpoint |
| `push_unsubscribe` | unsubscribe endpoint |
| `push_device_remove` | per-device turn off |
| `follow_seller` | `/seller/<pk>/follow/` |
| `unfollow_seller` | `/seller/<pk>/unfollow/` |

Views are in `auctions/notification_views.py`.

**Membership** (`auctions/subscription_views.py`): `subscribe`, `subscribe_create`, `subscribe_return`, `subscribe_cancelled`, `membership`, `membership_cancel`, `paypal_webhook`.

**Staff pages** (under `/admin/…`, all `StaffRequiredMixin`):

- Add Listing: `weekly_setup`, `weekly_setup_listings`, `weekly_setup_done`, `weekly_setup_notify_followers`
- Invoices: `invoice_dashboard`, `invoice_edit`, `invoice_create`
- Combined invoices: `combined_invoice_dashboard`, `combined_invoice_generate`, `combined_invoice_review`
- Reports: `reports_dashboard`, `reports_export_csv`

**PWA:** `pwa_manifest`, `pwa_service_worker` (`/sw.js`), `pwa_offline`, `pwa_icon`, `pwa_icon_maskable`

**Auth:** `account_login` (`RateLimitedLoginView`), `account_signup` (`RateLimitedSignupView`); the rest is allauth.

### Admin (`auctions/admin.py`, all `unfold.admin.ModelAdmin`)

- `CustomUserAdmin`: profile inline; action `notify_followers`; deletion guarded.
- `UserProfileAdmin`: worklist for approval and sellers; the change page redirects to the user.
- `AuctionListingAdmin`: duplicate action; sold listings locked.
- `SubscriptionPlanAdmin`: PayPal sync on save.
- Also: `CombinedInvoiceAdmin`, `PushSubscriptionAdmin`, `SellerFollowAdmin`, `SiteSettingsAdmin`, and others.
- The sidebar is configured in `settings.UNFOLD['SIDEBAR']`.

### Templates

- **Site-wide:** `base.html` (blocks `title`, `content`, `extra_js`, `navbar_items`), `index.html`, `pwa/sw.js` (a Django template), `pwa/offline.html`
- **`auctions/`:** `listing_detail.html`, `seller_listings.html`, `category.html`, `_listing_card.html`, `_sidebar.html`, `seller_dashboard.html`, `security_settings.html`, `notification_settings.html`
- **`accounts/`:** `profile.html`, `profile_edit.html`
- **`weekly_setup/`:** `seller.html`, `add_listing.html`, `done.html`, `notify_followers.html`, `_notify_followers.html`
- **`subscriptions/`:** `landing.html`, `membership.html`, `cancelled.html`
- **`invoices/`:** `detail.html`, `dashboard.html`, `create.html`, `edit.html`
- **`combined_invoices/`:** `dashboard.html`, `review.html`
- **`content/`:** `faq.html`, `site_page.html`
- **Account and allauth:** `account/*` and `allauth/layouts|elements/*` (branding overrides)

**Static JS:** `js/pwa.js`, `js/install-banner.js`, `js/notifications.js`, `js/protected-email.js`.

**Email bodies** are inline f-strings in Python (views, services, the close command), not template files.
