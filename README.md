# WA Automated Wishes

A human-in-the-loop WhatsApp messaging agent. Generates personalised birthday, anniversary, and festival messages — you review and send from your phone.

**Zero cost. No server. Runs entirely on Google infrastructure.**

---

## Architecture

```
Google Apps Script (Code.gs)
    ├── Time trigger 9 AM IST  → prepareDailyQueue()
    ├── Time trigger 7 PM IST  → eveningRun()
    └── Web App endpoint       → dashboard API

GitHub Pages (index.html)
    └── Dashboard UI → calls Apps Script

Google Sheets
    ├── people_and_groups   — everyone who gets a wish
    ├── event_ref           — event name → category mapping
    ├── festival_calendar   — festival dates by year
    ├── message_templates   — message variants
    ├── ready_queue         — today's pending messages (auto-generated)
    ├── send_history        — permanent archive
    └── _meta               — AI usage counter
```

---

## Setup

### 1. Google Sheet

Create a sheet at [sheets.new](https://sheets.new) and add these tabs with exact names:

**`people_and_groups`**
```
id | chat_type | event_name | event_date | group_invite_link | phone | name | relation | language | tone | media_mode | is_active
```

**`event_ref`**
```
event_name | event_category
```
Values for event_category: `historical`, `fixed_festival`, `variable_festival`
Import `event_ref.csv` from this repo.

**`festival_calendar`**
```
festival | year | month | day
```
Import `festival_calendar.csv` from this repo.

**`message_templates`**
```
event_type | language | tone | template_text
```
Import `templates.csv` and `templates_additional.csv` from this repo.

### 2. Apps Script

1. Open your sheet → Extensions → Apps Script
2. Paste the entire contents of `Code.gs`
3. Update `SHEET_ID` at the top with your sheet ID
4. Update `NOTIFY_TO` and `NOTIFY_FROM` with your email addresses

### 3. Script Properties

Apps Script editor → Project Settings → Script Properties:

| Key | Value |
|---|---|
| `ANTHROPIC_API_KEY` | For AI Generate button (optional) |
| `TELEGRAM_BOT_TOKEN` | From BotFather (optional) |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID (optional) |
| `GIPHY_API_KEY` | For GIF media (optional) |
| `UNSPLASH_ACCESS_KEY` | For image media (optional) |

### 4. Deploy as Web App

Deploy → New deployment → Web app → Execute as Me → Anyone → Deploy.
Copy the URL and update `GAS_URL` in `index.html`.

### 5. Install time triggers

In Apps Script editor, run `installTriggers()` once.
This sets up 9 AM (queue generation) and 7 PM (reminder) triggers automatically.

### 6. Enable GitHub Pages

Repo → Settings → Pages → Branch: main → / (root) → Save.
Dashboard available at: `https://{username}.github.io/{repo}/`

### 7. Verify

Run `runChecks()` in Apps Script editor — validates all sheets, phones, duplicate ids.
Run `morningRun()` once to test the full flow.

---

## Sheet schemas

### people_and_groups

| Column | Values | Notes |
|---|---|---|
| id | mom-bday | Unique. Group card gets -group suffix. |
| chat_type | individual / group | Controls destination and tone. |
| event_name | birthday, diwali | Must match event_ref and festival_calendar exactly. |
| event_date | 1987-05-20 | Used for historical events only (month+day matching). |
| group_invite_link | https://chat.whatsapp.com/... | Required for group. Generates 2nd card if on individual row. |
| phone | 919876543210 | Digits only. |
| name | Mom | Replaces {{name}} in individual templates. |
| relation | mother, cousin | Used in AI Generate prompt. |
| language | en / hi / hinglish | Template matching. |
| tone | warm / casual / fun / formal | Groups always forced to formal. |
| media_mode | text / image / gif / manual_photo | |
| is_active | TRUE / FALSE | FALSE rows skipped. |

### event_ref

| event_category | Firing rule |
|---|---|
| historical | Fires on month+day every year (birthday, anniversary) |
| fixed_festival | Fires when festival_calendar month+day matches today (year ignored) |
| variable_festival | Fires when festival_calendar exact year+month+day matches today (year required) |

### festival_calendar

| Column | Notes |
|---|---|
| festival | Must match event_name in people_and_groups exactly |
| year | Blank = fires every year (fixed). Filled = fires that year only (variable). |
| month | Integer 1–12 |
| day | Integer 1–31 |

### message_templates

Templates support `{{name}}` placeholder. Match priority: exact (event+lang+tone) → language → event → hardcoded fallback.

---

## Message rules

| chat_type | destination | tone | {{name}} |
|---|---|---|---|
| individual | wa.me/{phone} | from sheet | replaced |
| individual + group_invite_link (card 1) | wa.me/{phone} | from sheet | replaced |
| individual + group_invite_link (card 2) | group invite link | forced formal | replaced |
| group | group invite link | forced formal | stripped |

---

## Daily flow

**9 AM:** Apps Script generates queue → archives yesterday → writes ready_queue → sends email + Telegram with names.

**You:** Open dashboard on phone → review cards → tap Send on WhatsApp → message pre-filled → tap send arrow in WhatsApp.

**7 PM:** If any messages still pending → reminder email + Telegram. If all done → silence.
