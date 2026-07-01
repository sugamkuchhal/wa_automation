# WA Automated Wishes

A human-in-the-loop WhatsApp messaging agent. Generates personalised birthday, anniversary, and festival messages — you review and send from your phone.

**Zero cost. No server. Runs entirely on Google infrastructure.**

---

## Architecture

```
Google Apps Script (Code.gs)
    ├── Time trigger 9 AM IST  → morningRun()
    ├── Time trigger 7 PM IST  → eveningRun()
    └── Web App endpoint       → dashboard API (doGet / doPost)

GitHub Pages (index.html)
    └── Dashboard UI → calls Apps Script web app

Google Sheets
    ├── people_and_groups   — everyone who gets a wish
    ├── event_ref           — event type → category mapping
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
id | chat_type | event_type | event_date | group_invite_link | phone | name | cascade_name | relation | language | tone | media_mode | is_active
```

**`event_ref`**
```
event_type | event_category
```
Values for `event_category`: `historical`, `fixed_festival`, `variable_festival`
Import `event_ref.csv` from this repo.

**`festival_calendar`**
```
festival | year | month | day
```
Import `festival_calendar.csv` from this repo.

**`message_templates`**
```
chat_type | event_type | language | tone | template_text
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
| id | `mom-bday` | Unique. Group card gets `-group` suffix automatically. |
| chat_type | `personalized` / `broadcast` | Controls destination and tone. |
| event_type | `birthday`, `diwali` … | Must match `event_ref` and `festival_calendar` exactly. Use `cascade_birthday` for child birthday via parent. |
| event_date | `1987-05-20` | Used for `historical` events (month+day match). Unknown year → `1900`. Fully unknown → `1900-01-01` (skipped). |
| group_invite_link | `https://chat.whatsapp.com/…` | Required for `broadcast`. On a `personalized` row, generates a second group card. |
| phone | `919876543210` | Digits only. For `cascade_birthday`, this is the parent's number. |
| name | `Mom` | Replaces `{{name}}` in templates. For `cascade_birthday`, this is the child's name. |
| cascade_name | `Priya` | Parent's name for `cascade_birthday` rows; replaces `{{cascade_name}}`. Unused otherwise. |
| relation | `mother`, `cousin` | Used in AI Generate prompt. ` via Parent` suffix stripped automatically. |
| language | `en` / `hi` / `hinglish` | Template matching. |
| tone | `warm` / `casual` / `fun` / `formal` | Group-link destination cards are forced to `formal`; phone destination cards retain this sheet tone. |
| media_mode | `text` / `image` / `gif` / `manual_photo` | |
| is_active | `TRUE` / `FALSE` | `FALSE` rows skipped entirely. |

### event_ref

| event_category | Firing rule |
|---|---|
| `historical` | Fires on month+day every year (birthdays, anniversaries) |
| `fixed_festival` | Fires when `festival_calendar` month+day matches today (year ignored) |
| `variable_festival` | Fires on exact year+month+day match (year required; skipped with warning if missing) |

### festival_calendar

| Column | Notes |
|---|---|
| festival | Must match `event_type` in `people_and_groups` exactly |
| year | Required for `variable_festival`; ignored for `fixed_festival` |
| month | Integer 1–12 |
| day | Integer 1–31 |

### message_templates

Templates support `{{name}}` and `{{cascade_name}}` placeholders. If source values are present, placeholders are replaced for every `chat_type`, including `broadcast`.

Match priority (best tier wins, random pick across all matches at that tier):
1. `chat_type` + event + language + tone
2. `chat_type` + event + language
3. `chat_type` + event
4. (no `chat_type`) event + language + tone
5. (no `chat_type`) event + language
6. (no `chat_type`) event
7. Hardcoded fallback

---

## Message routing

| chat_type | event_type | Has phone | Has group link | Destination | Tone | Placeholders |
|---|---|---:|---:|---|---|---|
| `personalized` | any non-cascade event | ✅ | ❌ | `wa.me/{phone}` | from sheet | replaced |
| `personalized` | any non-cascade event | ✅ | ✅ | Card 1: `wa.me/{phone}` | from sheet | replaced |
| `personalized` | any non-cascade event | ✅ | ✅ | Card 2: group invite link, with `chat_type` preserved | forced formal | replaced |
| `broadcast` | any non-cascade event | ✅ | ❌ | `wa.me/{phone}` | from sheet | replaced |
| `broadcast` | any non-cascade event | ❌ | ✅ | group invite link | forced formal | replaced |
| `broadcast` | any non-cascade event | ✅ | ✅ | Card 1: `wa.me/{phone}`; Card 2: group invite link | phone from sheet; group forced formal | replaced |
| any valid `chat_type` | `cascade_birthday` | ✅ (parent) | — | `wa.me/{parent phone}` | from sheet | `{{name}}` = child, `{{cascade_name}}` = parent |

---

## Dashboard features

- **Review cards** — view generated message, WA link to send
- **Edit** — modify message text before sending
- **AI Generate** — calls Anthropic API via GAS (30/day cap); strips ` via Parent` from relation before prompting
- **Skip / Unskip** — amber badge; persists across refresh; skipped cards excluded from 7 PM reminder; archived to `send_history`
- **Belated** — auto-prepends "Belated " on render if event was yesterday or earlier; saved to sheet on card render
- **Add Contact** — add a new row to `people_and_groups` from the dashboard

---

## Daily flow

**9 AM:** Apps Script archives yesterday's queue → generates today's `ready_queue` → sends email (+ Telegram if configured) with names.

**You:** Open dashboard on phone → review cards → tap Send → WhatsApp opens with message pre-filled → tap send arrow.

**7 PM:** Any cards still `ready` or `edited` → reminder email + Telegram. All done → silence.
