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
    'https://www.googleapis.com/auth/drive.readonly'
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


def write_ready_queue(rows):
    sh = _sheet()
    try:
        ws = sh.worksheet('ready_queue')
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title='ready_queue', rows=1000, cols=20)

    headers = [
        'id', 'queue_date', 'name', 'chat_type', 'event_type', 'phone', 'group_invite_link',
        'media_mode', 'media_url', 'final_message_text', 'wa_link', 'action_status', 'action_ts'
    ]
    ws.clear()
    ws.append_row(headers)
    if rows:
        ws.append_rows([[r.get(h, '') for h in headers] for r in rows])


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


def build_media(row, text):
    mode = row.get('media_mode')
    if mode in ('manual_photo', 'text'):
        return {'media_url': ''}
    festival = str(row.get('event_type', 'celebration')).replace(' ', '+')
    name = str(row.get('name', 'friend')).replace(' ', '+')
    caption = text[:80].replace(' ', '+')
    if mode == 'gif':
        return {'media_url': f'https://dummyimage.com/600x600/000/fff.gif&text={festival}+{name}'}
    return {'media_url': f'https://dummyimage.com/1080x1080/ff6600/ffffff.png&text={festival}+{name}+{caption}'}


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


def prepare_daily_queue():
    # Three worksheet reads share the same connection
    contacts = get_sheet_rows('contacts_events')
    templates = get_sheet_rows('message_templates')
    festivals = get_sheet_rows('festival_calendar')
    today = _today_str()
    dt = datetime.strptime(today, '%Y-%m-%d')

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
        if int(f.get('month', 0)) == dt.month and int(f.get('day', 0)) == dt.day
    ]

    todays = [
        r for r in contacts
        if str(r.get('event_date', '')) == today and str(r.get('active', '')).upper() == 'TRUE'
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
    # Batch all three cell updates in a single API call
    ws.batch_update([
        {'range': gspread.utils.rowcol_to_a1(i, text_col),   'values': [[text]]},
        {'range': gspread.utils.rowcol_to_a1(i, action_col), 'values': [['edited']]},
        {'range': gspread.utils.rowcol_to_a1(i, ts_col),     'values': [[datetime.now(ZoneInfo(TZ)).isoformat()]]},
    ])
    return jsonify({'ok': True})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '8080')), debug=True)
