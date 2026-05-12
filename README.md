# WhatsApp Personal Messaging Agent (Android, Safe Automation)

This project provides a **human-in-the-loop WhatsApp workflow** using Google Sheets as the source of truth.

You can run it in **two modes**:

1. **Python/Flask mode** (`app.py` + `Dashboard.html`), or
2. **Google Apps Script mode** (`Code.gs` + `Dashboard.html` inside Apps Script).

Both modes follow the same safety model: they prepare send-ready messages, but **you manually send from WhatsApp UI**.

---

## What your code is doing

### Data model in Google Sheets
The system expects these tabs:

- `contacts_events`
- `festival_calendar`
- `message_templates`
- (auto-created) `ready_queue`

`contacts_events` stores one-time entries (birthday/anniversary/etc.) for people or groups, while `festival_calendar` creates date-based festival entries. Templates are selected from `message_templates`.

### Queue generation flow
At queue-generation time (`prepare_daily_queue` / `prepareDailyQueue`):

1. Read contacts, templates, festivals.
2. Compute today in configured timezone.
3. Include matching contacts for today where `active=TRUE`.
4. Add any matching festivals for today.
5. Build final message text from best-match template (exact match, then fallback).
6. Optionally build media URL placeholder (`image`/`gif`; blank for `text`/`manual_photo`).
7. Build WhatsApp deep link (`wa.me`) for individuals.
8. Write all records to `ready_queue` with `action_status=ready`.

### Dashboard flow
`Dashboard.html` loads today's queue and lets you:

- Open WhatsApp with prefilled text (manual final send).
- Save message edits back to sheet.
- Mark message as `sent` or `skipped`.
- Copy/share text from Android browser.

---

## Setup (Python/Flask mode) — recommended if you want local backend control

## 1) Prerequisites

- Python 3.10+
- A Google account
- A Google Sheet with tabs listed above
- A Google Cloud service account JSON key

## 2) Create and share the Sheet

1. Create a Google Sheet.
2. Add tabs: `contacts_events`, `festival_calendar`, `message_templates`.
3. Put headers exactly as documented in **Sheet schema** below.
4. Copy the sheet ID from URL:
   - `https://docs.google.com/spreadsheets/d/<THIS_PART>/edit`
5. Share the sheet with your service account email (`...iam.gserviceaccount.com`) as Editor.

## 3) Configure environment

Create a `.env` (or export env vars in shell):

```bash
export SPREADSHEET_ID="your_sheet_id"
export GOOGLE_APPLICATION_CREDENTIALS="/full/path/to/service_account.json"
export TZ="Asia/Kolkata"
export PORT="8080"
```

> If `GOOGLE_APPLICATION_CREDENTIALS` is omitted, code defaults to `service_account.json` in repo root.

## 4) Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 5) Build today's queue once

```bash
python -c "from app import prepare_daily_queue; print(len(prepare_daily_queue()))"
```

If this command succeeds, your Sheets auth + schema are working.

## 6) Start dashboard server

```bash
python app.py
```

Open:

- `http://localhost:8080` (desktop)
- `http://<your-lan-ip>:8080` from Android on same Wi-Fi

---

## Setup (Google Apps Script mode)

Use this mode if you want everything in Google ecosystem without running Python server.

1. Open Apps Script project attached to your Sheet.
2. Paste `Code.gs` and `Dashboard.html`.
3. Set `SPREADSHEET_ID` constant in `Code.gs`.
4. Run `createDailyTrigger()` once (authorizes + creates 9 AM trigger).
5. Deploy as web app (or open sidebar/web output depending workflow).

---

## Triggering queue creation daily at 9 AM

### Python mode (cron example)

```bash
0 9 * * * cd /path/to/repo && /path/to/venv/bin/python -c "from app import prepare_daily_queue; prepare_daily_queue()"
```

### Apps Script mode

Use `createDailyTrigger()`; it creates a daily 9 AM trigger for `prepareDailyQueue`.

---

## Sheet schema

### `contacts_events`
- `id`
- `name`
- `phone` (E.164 for individuals)
- `chat_type` (`individual` | `group`)
- `group_invite_link`
- `event_type`
- `event_date` (`YYYY-MM-DD`)
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

- `Missing sheet: ...` / worksheet not found:
  - Ensure tab names are exact and case-sensitive.
- Empty dashboard:
  - Verify `event_date` matches today's date in `TZ` timezone.
  - Confirm `active` is `TRUE` (uppercase works best).
- Auth errors:
  - Confirm service account JSON path and sheet sharing.
- WhatsApp link not opening contact:
  - Ensure `chat_type=individual` and valid numeric phone exists.

---

## Compliance-first design

- Uses personal WhatsApp only (no Business API).
- No direct WhatsApp API calls.
- No auto-send/background send.
- No WhatsApp Web scraping/automation.
- Final send always happens inside WhatsApp UI.
