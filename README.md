# WhatsApp Personal Messaging Agent (Android, Safe Automation)

A **human-in-the-loop WhatsApp workflow** using Google Sheets as the source of truth.

Runs as a Python/Flask server. It prepares send-ready messages — you manually send from the WhatsApp UI.

---

## What it does

### Data model in Google Sheets
The system expects these tabs:

- `contacts_events`
- `festival_calendar`
- `message_templates`
- (auto-created) `ready_queue`

### Queue generation flow

At queue-generation time (`prepare_daily_queue`):

1. Read contacts, templates, festivals from Sheets.
2. Compute today in configured timezone.
3. Include matching contacts for today where `active=TRUE`.
4. Add any matching festivals for today.
5. Build final message text from best-match template (exact match, then fallback).
6. Optionally build a media URL placeholder (`image`/`gif`; blank for `text`/`manual_photo`).
7. Build WhatsApp deep link (`wa.me`) for individuals.
8. Write all records to `ready_queue` with `action_status=ready`.
9. Send an email notification if `NOTIFY_EMAIL` / `SMTP_*` env vars are set.

### Dashboard flow

`Dashboard.html` loads today's queue and lets you:

- Open WhatsApp with prefilled text (manual final send).
- Save message edits back to the sheet.
- Mark a message as `sent` or `skipped`.
- Copy/share text from Android browser.

---

## Setup

### 1) Prerequisites

- Python 3.10+
- A Google account
- A Google Sheet with tabs listed above
- A Google Cloud service account JSON key

### 2) Create and share the Sheet

1. Create a Google Sheet.
2. Add tabs: `contacts_events`, `festival_calendar`, `message_templates`.
3. Put headers exactly as documented in **Sheet schema** below.
4. Copy the sheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/<THIS_PART>/edit`
5. Share the sheet with your service account email (`...iam.gserviceaccount.com`) as Editor.

### 3) Configure environment

Create a `.env` (or export env vars in shell):

```bash
export SPREADSHEET_ID="your_sheet_id"
export ANTHROPIC_API_KEY="sk-ant-..."   # Optional — only needed for AI Generate button
export GOOGLE_APPLICATION_CREDENTIALS="/full/path/to/service_account.json"
export TZ="Asia/Kolkata"
export PORT="8080"

# Optional — email notification when queue is ready
export NOTIFY_EMAIL="you@example.com"
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USER="sender@gmail.com"
export SMTP_PASS="your_app_password"
```

> If `GOOGLE_APPLICATION_CREDENTIALS` is omitted, code defaults to `service_account.json` in repo root.

### 4) Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 5) Build today's queue once

```bash
python -c "from app import prepare_daily_queue; print(len(prepare_daily_queue()))"
```

If this succeeds, your Sheets auth + schema are working.

### 6) Start the dashboard server

```bash
python app.py
```

Open:

- `http://localhost:8080` (desktop)
- `http://<your-lan-ip>:8080` from Android on the same Wi-Fi

---

## Setup (Docker)

The easiest way to run the server — no Python install needed.

### 1) Create a `.env` file

```bash
SPREADSHEET_ID=your_sheet_id
TZ=Asia/Kolkata

# Optional email notification
NOTIFY_EMAIL=you@example.com
SMTP_USER=sender@gmail.com
SMTP_PASS=your_app_password
```

### 2) Place your service account key

Put `service_account.json` in the repo root. Docker Compose mounts it as a secret — it is never baked into the image.

### 3) Build and run

```bash
docker compose up --build
```

Open `http://localhost:8080`.

### 4) Run in the background

```bash
docker compose up -d
```

### 5) Trigger queue generation inside the container

```bash
docker compose exec wa_automation python -c "from app import prepare_daily_queue; prepare_daily_queue()"
```

---

## Triggering queue creation daily at 9 AM (cron)

```bash
0 9 * * * cd /path/to/repo && /path/to/venv/bin/python -c "from app import prepare_daily_queue; prepare_daily_queue()"
```

---

## Sheet schema

### `contacts_events`
- `id`
- `name`
- `phone` (E.164 for individuals)
- `chat_type` (`individual` | `group`)
- `group_invite_link`
- `event_type`
- `event_date` (`YYYY-MM-DD`) — the original date of the event
- `recurrence` (`yearly` | `monthly` | `weekly` | leave blank for one-time)
  - `yearly` — fires every year on the same month/day (birthdays, anniversaries)
  - `monthly` — fires every month on the same day-of-month
  - `weekly` — fires every week on the same weekday as `event_date`
  - blank — fires once on the exact `event_date`
- `relation`
- `language` (`en` | `hi` | `hinglish`)
- `tone` (`warm` | `casual` | `formal` | `fun`)
- `media_mode` (`text` | `image` | `gif` | `manual_photo`)
- `active` (`TRUE` | `FALSE`)

### `festival_calendar`
- `festival`
- `month`
- `day`
- `default_language`
- `default_media`

### `message_templates`
- `event_type`
- `language`
- `tone`
- `template_text` (supports `{{name}}`)

---

## Common setup mistakes (and fixes)

- `Missing sheet: ...` — ensure tab names are exact and case-sensitive.
- Empty dashboard — verify `event_date` matches today in `TZ` timezone; confirm `active` is `TRUE`.
- Auth errors — confirm service account JSON path and that the sheet is shared with that account.
- WhatsApp link not opening a contact — ensure `chat_type=individual` and a valid numeric phone exists.

---

## Compliance-first design

- Uses personal WhatsApp only (no Business API).
- No direct WhatsApp API calls.
- No auto-send / background send.
- No WhatsApp Web scraping or automation.
- Final send always happens inside the WhatsApp UI.
