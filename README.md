# WhatsApp Personal Messaging Agent (Android, Safe Automation) — Python Version

This project implements a Google Sheets + Python (Flask) + Web dashboard architecture for **send-ready** WhatsApp messages with strict human-in-the-loop control.

## Compliance-first design

- Uses personal WhatsApp only (no Business API).
- No direct WhatsApp API calls.
- No auto-send/background send.
- No WhatsApp Web scraping/automation.
- Final send always happens inside WhatsApp UI.

## Sheets schema

### `contacts_events`
- `id`
- `name`
- `phone` (E.164, individuals)
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

## Files

- `app.py`: Flask backend, queue prep logic, API routes, sheet update actions.
- `Dashboard.html`: send-ready dashboard UI.
- `requirements.txt`: Python dependencies.

## Setup

1. Create a Google Sheet with the three tabs above.
2. Create a Google Cloud service account and download JSON key as `service_account.json`.
3. Share your Google Sheet with the service-account email.
4. Set env vars:
   - `SPREADSHEET_ID`
   - `GOOGLE_APPLICATION_CREDENTIALS` (default: `service_account.json`)
   - `TZ` (default: `Asia/Kolkata`)
5. Install deps: `pip install -r requirements.txt`
6. Run queue generation once manually:
   - `python -c "from app import prepare_daily_queue; prepare_daily_queue()"`
7. Run the app:
   - `python app.py`
8. Open `http://localhost:8080` on Android and send via WhatsApp buttons.

## Triggering daily at 9 AM

Use OS scheduler/cron (example):

```bash
0 9 * * * cd /path/to/repo && /usr/bin/python -c "from app import prepare_daily_queue; prepare_daily_queue()"
```

## Android flow

- 9 AM job prepares today's queue in `ready_queue` sheet.
- User opens dashboard from bookmark/notification shortcut.
- For each item, user taps send/share.
- WhatsApp opens with prefilled text.
- User manually taps **Send**.
