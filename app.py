import os
import smtplib
import urllib.parse
from datetime import datetime
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, request, send_from_directory
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', 'REPLACE_WITH_SPREADSHEET_ID')
TZ = os.getenv('TZ', 'Asia/Kolkata')
NOTIFY_EMAIL = os.getenv('NOTIFY_EMAIL', '')
SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER', '')
SMTP_PASS = os.getenv('SMTP_PASS', '')

app = Flask(__name__)

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# ---------------------------------------------------------------------------
# Single shared spreadsheet handle — created once, reused across requests.
# gspread credentials are long-lived service-account tokens; one client is fine.
# ---------------------------------------------------------------------------
_spreadsheet = None


def _sheet():
    """Return the cached Spreadsheet object, opening it on first call."""
    global _spreadsheet
    if _spreadsheet is None:
        creds_file = os.getenv('GOOGLE_APPLICATION_CREDENTIALS', 'service_account.json')
        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
        _spreadsheet = gspread.authorize(creds).open_by_key(SPREADSHEET_ID)
    return _spreadsheet


def _today_str():
    return datetime.now(ZoneInfo(TZ)).strftime('%Y-%m-%d')


# ---------------------------------------------------------------------------
# Sheet helpers
# ---------------------------------------------------------------------------

def get_sheet_rows(sheet_name):
    return _sheet().worksheet(sheet_name).get_all_records()


QUEUE_HEADERS = [
    'id', 'queue_date', 'name', 'chat_type', 'event_type', 'phone', 'group_invite_link',
    'media_mode', 'media_url', 'final_message_text', 'wa_link', 'action_status', 'action_ts'
]


def _get_or_create_ws(name, rows=5000, cols=20):
    sh = _sheet()
    try:
        return sh.worksheet(name)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=name, rows=rows, cols=cols)


def _archive_to_history():
    """Copy all rows currently in ready_queue into send_history before clearing."""
    try:
        rq = _sheet().worksheet('ready_queue')
    except gspread.WorksheetNotFound:
        return  # nothing to archive yet
    values = rq.get_all_values()
    if len(values) < 2:
        return  # header only or empty
    data_rows = values[1:]  # skip header

    hist = _get_or_create_ws('send_history', rows=50000, cols=20)
    # Write header on first use
    if hist.row_count < 1 or not hist.get('A1'):
        hist.append_row(QUEUE_HEADERS)
    hist.append_rows(data_rows)
    print(f'[history] archived {len(data_rows)} row(s) to send_history')


def write_ready_queue(rows):
    _archive_to_history()   # persist yesterday's results before wiping
    ws = _get_or_create_ws('ready_queue')
    ws.clear()
    ws.append_row(QUEUE_HEADERS)
    if rows:
        ws.append_rows([[r.get(h, '') for h in QUEUE_HEADERS] for r in rows])


def _queue_worksheet():
    return _sheet().worksheet('ready_queue')


def _find_row(values, id_col, rid):
    """Return 1-based sheet row index for a given id, or None."""
    for i, row in enumerate(values[1:], start=2):
        if str(row[id_col]) == rid:
            return i
    return None


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

def render_template(row, templates):
    exact = next(
        (t for t in templates if t.get('event_type') == row.get('event_type')
         and t.get('language') == row.get('language')
         and t.get('tone') == row.get('tone')), None
    )
    fallback = (
        next((t for t in templates if t.get('event_type') == row.get('event_type')
              and t.get('language') == row.get('language')), None)
        or next((t for t in templates if t.get('event_type') == row.get('event_type')), None)
        or {'template_text': 'Hi {{name}}, wishing you a wonderful day!'}
    )
    template = (exact or fallback).get('template_text', '')
    return template.replace('{{name}}', row.get('name', 'there'))


# ---------------------------------------------------------------------------
# Media helpers
# ---------------------------------------------------------------------------

GIPHY_API_KEY = os.getenv('GIPHY_API_KEY', '')          # optional — public beta key works too
GDRIVE_IMAGE_FOLDER_ID = os.getenv('GDRIVE_IMAGE_FOLDER_ID', '')  # optional Drive folder
UNSPLASH_ACCESS_KEY = os.getenv('UNSPLASH_ACCESS_KEY', '')        # optional — free key at unsplash.com/developers


def _giphy_url(query):
    """Search Giphy for a relevant GIF. Falls back to None if unavailable."""
    import urllib.request, json as _json
    key = GIPHY_API_KEY or 'dc6zaTOxFJmzC'  # Giphy public beta key
    q = urllib.parse.quote(query)
    url = f'https://api.giphy.com/v1/gifs/search?api_key={key}&q={q}&limit=1&rating=g'
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            data = _json.loads(r.read())
        return data['data'][0]['images']['original']['url']
    except Exception:
        return None


def _gdrive_image_url(event_type):
    """Find first image in the configured Drive folder whose name contains event_type.
    Returns a direct sharing URL, or None if folder not configured / no match."""
    if not GDRIVE_IMAGE_FOLDER_ID:
        return None
    try:
        drive = _sheet().client.auth  # reuse existing credentials
        from googleapiclient.discovery import build as _build
        svc = _build('drive', 'v3', credentials=drive)
        q = f"'{GDRIVE_IMAGE_FOLDER_ID}' in parents and mimeType contains 'image/' and name contains '{event_type}' and trashed=false"
        results = svc.files().list(q=q, fields='files(id,name)', pageSize=1).execute()
        files = results.get('files', [])
        if files:
            fid = files[0]['id']
            svc.permissions().create(fileId=fid, body={'role': 'reader', 'type': 'anyone'}).execute()
            return f'https://drive.google.com/uc?export=view&id={fid}'
    except Exception:
        pass
    return None


def _unsplash_url(event_type):
    """Fetch a random Unsplash image via the official API. Requires UNSPLASH_ACCESS_KEY.
    Returns None if key not set or request fails."""
    if not UNSPLASH_ACCESS_KEY:
        return None
    import urllib.request, json as _json
    q = urllib.parse.quote(f'{event_type} celebration')
    url = (
        f'https://api.unsplash.com/photos/random'
        f'?query={q}&orientation=squarish&content_filter=high'
        f'&client_id={UNSPLASH_ACCESS_KEY}'
    )
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            data = _json.loads(r.read())
        return data['urls']['regular']   # ~1080px wide, no auth needed to display
    except Exception:
        return None


def build_media(row, text):
    mode = row.get('media_mode')
    if mode in ('manual_photo', 'text'):
        return {'media_url': ''}

    event_type = str(row.get('event_type', 'celebration'))

    if mode == 'gif':
        url = _giphy_url(f'{event_type} celebration')
        return {'media_url': url or ''}

    if mode == 'image':
        # 1. Try Google Drive folder first
        url = _gdrive_image_url(event_type)
        if url:
            return {'media_url': url}
        # 2. Fall back to Unsplash API (free key) or blank if not configured
        url = _unsplash_url(event_type)
        return {'media_url': url or ''}

    return {'media_url': ''}


def build_wa_link(row, text, media):
    encoded = urllib.parse.quote(text + (f"\n{media['media_url']}" if media.get('media_url') else ''))
    if row.get('chat_type') == 'individual' and row.get('phone'):
        digits = ''.join(ch for ch in str(row.get('phone')) if ch.isdigit())
        return f'https://wa.me/{digits}?text={encoded}'
    if row.get('chat_type') == 'group' and row.get('group_invite_link'):
        return row.get('group_invite_link')
    return ''


def build_queue_record(row, templates, date_str):
    text = render_template(row, templates)
    media = build_media(row, text)
    return {
        'id': row.get('id'),
        'queue_date': date_str,
        'name': row.get('name'),
        'chat_type': row.get('chat_type'),
        'event_type': row.get('event_type'),
        'phone': row.get('phone', ''),
        'group_invite_link': row.get('group_invite_link', ''),
        'media_mode': row.get('media_mode'),
        'media_url': media.get('media_url', ''),
        'final_message_text': text,
        'wa_link': build_wa_link(row, text, media),
        'action_status': 'ready',
        'action_ts': ''
    }


def notify_queue_ready(count, date_str):
    if not all([NOTIFY_EMAIL, SMTP_USER, SMTP_PASS]):
        return
    msg = MIMEText(f'You have {count} WhatsApp message(s) ready to review and send.')
    msg['Subject'] = f'WhatsApp queue ready: {count} message(s) for {date_str}'
    msg['From'] = SMTP_USER
    msg['To'] = NOTIFY_EMAIL
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
    except Exception as e:
        print(f'[notify] email failed: {e}')


def _matches_today(row, dt):
    """Return True if a contacts_events row should fire today.

    Recurrence values (case-insensitive):
      yearly  - match month + day (e.g. birthdays, anniversaries)
      monthly - match day-of-month only
      weekly  - match day-of-week (Mon=0, Sun=6) derived from event_date
      <empty> - exact date match (one-time event)
    """
    raw_date = str(row.get('event_date', '')).strip()
    if not raw_date:
        return False
    event_dt = None
    for fmt in ('%Y-%m-%d', '%d-%b-%Y', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y'):
        try:
            event_dt = datetime.strptime(raw_date, fmt)
            break
        except ValueError:
            continue
    if event_dt is None:
        print(f'[queue] unrecognised date format: {raw_date!r}')
        return False

    recurrence = str(row.get('recurrence', '')).strip().lower()

    if recurrence == 'yearly':
        return event_dt.month == dt.month and event_dt.day == dt.day
    if recurrence == 'monthly':
        return event_dt.day == dt.day
    if recurrence == 'weekly':
        return event_dt.weekday() == dt.weekday()
    # Default: one-time exact match
    return raw_date == dt.strftime('%Y-%m-%d')


def prepare_daily_queue():
    # Three worksheet reads share the same connection
    contacts = get_sheet_rows('contacts_events')
    templates = get_sheet_rows('message_templates')
    festivals = get_sheet_rows('festival_calendar')
    today = _today_str()
    dt = datetime.strptime(today, '%Y-%m-%d')

    def _festival_matches(f, dt):
        """Match festival to today using year+month+day (variable) or month+day (fixed)."""
        try:
            month = int(f.get('month', 0))
            day   = int(f.get('day', 0))
        except (ValueError, TypeError):
            return False
        if month != dt.month or day != dt.day:
            return False
        year_val = str(f.get('year', '')).strip()
        if year_val and year_val != '0':
            try:
                return int(year_val) == dt.year
            except ValueError:
                return False
        return True  # blank or 0 = fixed annual, month+day match is enough

    festival_rows = [
        {
            'id': f"festival-{str(f.get('festival', '')).lower()}-{today}",
            'name': f"{f.get('festival', 'Festival')} Group",
            'phone': '', 'chat_type': 'group', 'group_invite_link': '',
            'event_type': str(f.get('festival', '')).lower(),
            'event_date': today, 'relation': 'community',
            'language': f.get('default_language', 'en'),
            'tone': 'warm', 'media_mode': f.get('default_media', 'text'), 'active': 'TRUE'
        }
        for f in festivals
        if _festival_matches(f, dt)
    ]

    todays = [
        r for r in contacts
        if _matches_today(r, dt) and str(r.get('active', '')).upper() == 'TRUE'
    ] + festival_rows

    output = [build_queue_record(r, templates, today) for r in todays]
    write_ready_queue(output)
    notify_queue_ready(len(output), today)
    return output


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get('/')
def home():
    return send_from_directory('.', 'Dashboard.html')


@app.get('/api/today_queue')
def today_queue():
    today = _today_str()
    rows = get_sheet_rows('ready_queue')
    return jsonify([r for r in rows if str(r.get('queue_date')) == today])


@app.get('/api/history')
def get_history():
    """Return all rows from send_history, optionally filtered by ?days=N (default 30)."""
    try:
        days = int(request.args.get('days', 30))
    except ValueError:
        days = 30
    try:
        ws = _sheet().worksheet('send_history')
        rows = ws.get_all_records()
    except gspread.WorksheetNotFound:
        return jsonify([])
    if days > 0:
        from datetime import timedelta
        cutoff = (datetime.now(ZoneInfo(TZ)) - timedelta(days=days)).strftime('%Y-%m-%d')
        rows = [r for r in rows if str(r.get('queue_date', '')) >= cutoff]
    return jsonify(rows)


@app.post('/api/prepare_queue')
def prepare_queue():
    """Trigger queue generation on demand from the dashboard."""
    try:
        output = prepare_daily_queue()
        return jsonify({'ok': True, 'count': len(output)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.post('/api/mark')
def mark_action():
    payload = request.get_json(force=True)
    rid = str(payload.get('id'))
    action = str(payload.get('action'))

    ws = _queue_worksheet()
    values = ws.get_all_values()
    headers = values[0]
    i = _find_row(values, headers.index('id'), rid)
    if i is None:
        return jsonify({'ok': False})

    action_col = headers.index('action_status') + 1
    ts_col = headers.index('action_ts') + 1
    # Batch both cell updates in a single API call
    ws.batch_update([
        {'range': gspread.utils.rowcol_to_a1(i, action_col), 'values': [[action]]},
        {'range': gspread.utils.rowcol_to_a1(i, ts_col),     'values': [[datetime.now(ZoneInfo(TZ)).isoformat()]]},
    ])
    return jsonify({'ok': True})


@app.post('/api/edit')
def edit_message():
    payload = request.get_json(force=True)
    rid = str(payload.get('id'))
    text = str(payload.get('text', ''))

    ws = _queue_worksheet()
    values = ws.get_all_values()
    headers = values[0]
    i = _find_row(values, headers.index('id'), rid)
    if i is None:
        return jsonify({'ok': False})

    text_col   = headers.index('final_message_text') + 1
    action_col = headers.index('action_status') + 1
    ts_col     = headers.index('action_ts') + 1
    wa_col     = headers.index('wa_link') + 1

    # Rebuild wa_link with the updated text so Send on WhatsApp uses the edited message
    row_data = dict(zip(headers, values[i - 1]))
    row_data['final_message_text'] = text
    media = {'media_url': row_data.get('media_url', '')}
    new_wa_link = build_wa_link(row_data, text, media)

    ws.batch_update([
        {'range': gspread.utils.rowcol_to_a1(i, text_col),   'values': [[text]]},
        {'range': gspread.utils.rowcol_to_a1(i, wa_col),     'values': [[new_wa_link]]},
        {'range': gspread.utils.rowcol_to_a1(i, action_col), 'values': [['edited']]},
        {'range': gspread.utils.rowcol_to_a1(i, ts_col),     'values': [[datetime.now(ZoneInfo(TZ)).isoformat()]]},
    ])
    return jsonify({'ok': True, 'wa_link': new_wa_link})


AI_DAILY_LIMIT = int(os.getenv('AI_DAILY_LIMIT', '30'))
_AI_META_KEY = 'ai_usage'   # row key in the meta sheet


def _meta_ws():
    """Return (or create) the meta sheet — a simple key/value store."""
    return _get_or_create_ws('_meta', rows=100, cols=3)


def _read_ai_usage():
    """Read today's AI call count from the _meta sheet. Returns 0 if not set."""
    today = _today_str()
    try:
        ws = _meta_ws()
        rows = ws.get_all_values()
        for row in rows[1:]:
            if len(row) >= 3 and row[0] == _AI_META_KEY and row[1] == today:
                return int(row[2])
    except Exception:
        pass
    return 0


def _write_ai_usage(count):
    """Persist today's AI call count to the _meta sheet."""
    today = _today_str()
    try:
        ws = _meta_ws()
        values = ws.get_all_values()
        # Ensure header exists
        if not values or values[0] != ['key', 'date', 'value']:
            ws.clear()
            ws.append_row(['key', 'date', 'value'])
            values = [['key', 'date', 'value']]
        # Find existing row for this key+date
        for i, row in enumerate(values[1:], start=2):
            if len(row) >= 2 and row[0] == _AI_META_KEY and row[1] == today:
                ws.update_cell(i, 3, str(count))
                return
        # Not found — append new row
        ws.append_row([_AI_META_KEY, today, str(count)])
    except Exception as e:
        print(f'[meta] failed to persist AI usage: {e}')


def _check_ai_rate_limit():
    today = _today_str()
    count = _read_ai_usage()
    if count >= AI_DAILY_LIMIT:
        return False, f'Daily AI limit of {AI_DAILY_LIMIT} calls reached. Resets tomorrow.'
    _write_ai_usage(count + 1)
    return True, None


@app.post('/api/ai_generate')
def ai_generate():
    """Generate a personalised message via Claude. Opt-in only — called from dashboard button."""
    import anthropic

    allowed, err = _check_ai_rate_limit()
    if not allowed:
        return jsonify({'ok': False, 'error': err}), 429

    payload = request.get_json(force=True)
    name       = str(payload.get('name', 'there'))
    event_type = str(payload.get('event_type', 'occasion'))
    relation   = str(payload.get('relation', ''))
    language   = str(payload.get('language', 'en'))
    tone       = str(payload.get('tone', 'warm'))
    current    = str(payload.get('current_text', ''))

    lang_label = {'en': 'English', 'hi': 'Hindi', 'hinglish': 'Hinglish (mix of Hindi and English)'}.get(language, 'English')
    tone_label = {'warm': 'warm and heartfelt', 'casual': 'casual and friendly',
                  'formal': 'formal and respectful', 'fun': 'fun and playful'}.get(tone, 'warm and heartfelt')
    relation_hint = f" They are my {relation}." if relation else ""

    prompt = (
        f"Write a short WhatsApp message for {name} on their {event_type}.{relation_hint}\n"
        f"Language: {lang_label}. Tone: {tone_label}.\n"
        f"Keep it under 3 sentences, personal, no hashtags, no generic filler.\n"
        f"Existing message for reference (improve it, don't copy): {current}\n"
        f"Reply with ONLY the message text, nothing else."
    )

    api_key = os.getenv('ANTHROPIC_API_KEY', '')
    if not api_key:
        return jsonify({'ok': False, 'error': 'ANTHROPIC_API_KEY not set'}), 500

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model='claude-haiku-4-5',
            max_tokens=256,
            messages=[{'role': 'user', 'content': prompt}]
        )
        text = message.content[0].text.strip()
        return jsonify({'ok': True, 'text': text})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


def _validate_phone_numbers(sh):
    """Warn about contacts with malformed phone numbers (non-digits, too short, leading +)."""
    try:
        rows = sh.worksheet('contacts_events').get_all_records()
    except Exception:
        return  # sheet missing — _startup_check will catch it
    bad = []
    for r in rows:
        if str(r.get('chat_type', '')).strip().lower() != 'individual':
            continue
        if str(r.get('active', '')).upper() != 'TRUE':
            continue
        phone = str(r.get('phone', '')).strip()
        digits = ''.join(ch for ch in phone if ch.isdigit())
        if not phone:
            bad.append((r.get('id'), 'phone is blank'))
        elif phone != digits:
            bad.append((r.get('id'), f'phone "{phone}" has non-digit characters — use digits only e.g. 919876543210'))
        elif len(digits) < 7:
            bad.append((r.get('id'), f'phone "{phone}" looks too short ({len(digits)} digits)'))
    if bad:
        print(f'[startup] WARNING: {len(bad)} contact(s) with phone issues — wa.me links will be broken:')
        for cid, msg in bad:
            print(f'[startup]   id={cid}: {msg}')
    else:
        print(f'[startup] phone check OK — {len([r for r in rows if str(r.get("active","")).upper()=="TRUE" and str(r.get("chat_type","")).lower()=="individual"])} individual contacts validated')


def _startup_check():
    """Verify sheet connectivity before accepting traffic. Exits with a clear message on failure."""
    import sys
    print('[startup] checking Google Sheets connection…')
    try:
        sh = _sheet()
        titles = [ws.title for ws in sh.worksheets()]
        required = {'contacts_events', 'festival_calendar', 'message_templates'}
        missing = required - set(titles)
        if missing:
            print(f'[startup] ERROR: missing sheet tab(s): {", ".join(sorted(missing))}')
            print('[startup] Create the tabs and restart. See README for schema.')
            sys.exit(1)
        print(f'[startup] OK — connected to "{sh.title}", tabs: {titles}')
        _validate_phone_numbers(sh)
    except FileNotFoundError:
        creds_file = os.getenv('GOOGLE_APPLICATION_CREDENTIALS', 'service_account.json')
        print(f'[startup] ERROR: credentials file not found: {creds_file}')
        print('[startup] Set GOOGLE_APPLICATION_CREDENTIALS or place service_account.json in repo root.')
        sys.exit(1)
    except Exception as e:
        print(f'[startup] ERROR: could not connect to Google Sheets: {e}')
        sys.exit(1)


if __name__ == '__main__':
    _startup_check()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '8080')), debug=True)
