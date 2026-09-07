# ASQ Daylily Auctions — Weekly Workflow

Each auction week follows a predictable six-step cycle. Most of it is automated;
admin action is only needed at the start (setup) and end (invoices).

---

## Step 0 (once per seller) — Flag their account as a seller

A seller is a **user account** with the seller flag ticked; there is no separate
seller record to create. Do this once, the first time someone sells with you:

1. In the Django admin, go to **Users → Users** and open their account.
2. On their profile, tick **Is seller**.
3. Fill in their **standard shipping fee**, category and the week they are
   featured.
4. Under **Payment methods**, fill in however they want to be paid — a Venmo
   handle, a PayPal address, a Zelle phone number. Leave a method blank if they
   do not accept it, and use **Other payment notes** for anything the named
   fields do not cover.

> Buyers see these details on their own invoice, where they owe that seller
> money. The public seller page lists only *which* methods are accepted, not the
> handles.

They are then selectable in Step 1 from that moment on, every week, with no
repeat setup. If nobody has been flagged yet, Step 1 says so and links you here
rather than showing an empty dropdown.

> Sellers can see what is listed under their name and what has sold, at
> **Seller Dashboard** in their own navigation. It is read-only — they cannot
> add or edit listings, so Steps 1 and 2 are still an admin job.

---

## Step 1 — Select the seller

**Where:** `/admin/weekly-setup/`  (navbar: *Add Listing*)

1. Pick the seller from the dropdown.
2. Click **Continue to Listings**.

Their shipping fee and payment details come along automatically, so a returning
seller needs nothing re-entered.

---

## Step 2 — Add plant listings, one at a time

**Where:** `/admin/weekly-setup/<seller>/add-listing/`  (arrives here automatically after Step 1)

One plant per form. Fill it in, click **Add This Listing**, and the same page
comes back empty and ready for the next one — you never have to navigate between
entries.

| Field | Notes |
|---|---|
| Plant name | Cultivar name |
| Description | Optional — colour, height, bloom time, etc. |
| Photo | JPG/PNG. A thumbnail appears once you pick the file, so you can check it before saving. |
| Category | Which category the plant is listed under |
| Listing type | **Auction** or **Buy It Now** — the price fields change to match |
| Starting price | Auction only: the opening bid |
| Reserve price | Auction only, optional: the listing will not sell below this |
| Price each | Buy It Now only |
| How many | Buy It Now only: units for sale. Several buyers can each take some. |
| Shipping cost | Buy It Now only: what the buyer pays to ship this plant. Starts at the seller's standard fee (from Step 0); change it for a plant that ships differently. Leave blank to always follow the seller's fee. |
| Charged how? | Buy It Now only: the shipping cost once per order (flat), or multiplied by the quantity bought (per item) |
| Starts / Ends | Date + time bidding opens and closes |

**Category, start time and end time carry over to the next entry**, since a
week's plants almost always share them. Everything else starts blank.

### Re-listing a plant you have sold before

Use **Copy an existing listing** at the top of the page. Pick one of this
seller's past listings, click **Copy details**, and the form fills in with the
plant's name, description, type, prices, quantity and shipping. The dates always
start blank — set new ones and save it as a new listing. The original is left
untouched.

The same thing is available in the Django admin: select a listing on **Auction
Listings** and choose **Duplicate selected listing(s)**. That saves the copy
immediately and opens it for editing; it is inactive until you set its dates and
tick **Is active**.

Underneath the form is a read-only list of everything added for this seller so
far, newest first, so you can see your entries land. It is for reference only —
to change or remove a listing, use the **View in admin** link on its row.

When you have added everything, click **Finish**.

> **Note:** A listing somebody has actually bought from is locked in the admin —
> readable, but not editable or deletable, because its title and price are what
> the buyer's invoice says they agreed to. The **Sold** column shows which ones.
> A listing that simply expired with no bids is *not* locked: give it new dates
> and tick **Is active** to run it again.

> **Tip:** To re-use a listing from a previous week, go to the Django admin
> → Auction Listings, select the listing, and choose the
> **"Duplicate listing(s) for re-use next week"** action.
> Then update the dates on the copy. A duplicated Buy It Now listing starts
> fully in stock again.

---

## Step 3 — Set or confirm start/end datetimes

Listings go live automatically at their `starts_at` time — no manual action needed.

If you need to adjust times after adding a listing:
- Go to Django admin → Auction Listings.
- Edit the listing and update `starts_at` / `ends_at`.

A common schedule is **Sunday 8 pm → Saturday 8 pm** (all times Central).

---

## Step 4 — Listings go live automatically

At `starts_at`, the listing appears on the home page and category pages and
accepts bids. No admin action needed.

Bidders receive no automated notification when a listing opens; promotion
(Facebook group post, email blast, etc.) is handled outside this system.

---

## Step 5 — Auction auto-closes; invoices auto-send

The background scheduler checks for ended auctions **every 5 minutes**.
When `ends_at` passes, the system automatically:

1. Marks the listing `is_closed = True`, `is_active = False`.
2. Assigns the highest bidder as winner.
3. Creates an Invoice (amount = winning bid, shipping = seller's fee).
4. Emails the **winner** with their winning amount and seller contact info.
5. Emails the **seller** (or admin if no seller email) to arrange payment/shipping.
6. Emails the **admin** a summary of the closed auction.

If no bids were placed, the listing is closed with no winner and no invoice,
and the admin receives a "no bids" notification.

---

## Step 6 — Admin reviews invoices and marks as sent

**Where:** `/admin/invoices/`  (navbar: *Invoices*)

1. Review all invoices grouped by seller.
2. Confirm payment method has been received from the winner (update the
   **Payment method** field on the invoice edit page if needed).
3. Check the box next to each resolved invoice and click **Mark Selected as Sent**.

For off-platform or manual sales, click **New Invoice** to create one without
a linked listing.

---

## Weekly cleanup (automatic)

Every **Monday at 3:00 am**, the scheduler runs `deactivate_old_listings`,
which sets `is_active = False` on any listing that ended more than 7 days ago.
This keeps the home page and category pages clean without manual archiving.

To run it manually at any time:

```bash
python manage.py deactivate_old_listings
# preview only:
python manage.py deactivate_old_listings --dry-run
```

---

## Quick reference — admin URLs

| Page | URL |
|---|---|
| Add Listing | `/admin/weekly-setup/` |
| Invoice Dashboard | `/admin/invoices/` |
| Django Admin | `/admin/` |
| New Invoice | `/admin/invoices/create/` |
