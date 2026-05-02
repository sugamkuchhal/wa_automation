import os
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, request, send_from_directory
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', 'REPLACE_WITH_SPREADSHEET_ID')
TZ = os.getenv('TZ', 'Asia/Kolkata')

app = Flask(__name__)

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.readonly'
]


def _client():
    creds_file = os.getenv('GOOGLE_APPLICATION_CREDENTIALS', 'service_account.json')
    creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    return gspread.authorize(creds)


def _today_str():
    return datetime.now(ZoneInfo(TZ)).strftime('%Y-%m-%d')


def get_sheet_rows(sheet_name):
    ws = _client().open_by_key(SPREADSHEET_ID).worksheet(sheet_name)
    records = ws.get_all_records()
    return records


def write_ready_queue(rows):
    sh = _client().open_by_key(SPREADSHEET_ID)
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


def render_template(row, templates):
    exact = next((t for t in templates if t.get('event_type') == row.get('event_type') and t.get('language') == row.get('language') and t.get('tone') == row.get('tone')), None)
    fallback = next((t for t in templates if t.get('event_type') == row.get('event_type') and t.get('language') == row.get('language')), None) \
        or next((t for t in templates if t.get('event_type') == row.get('event_type')), None) \
        or {'template_text': 'Hi {{name}}, wishing you a wonderful day!'}
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
    import urllib.parse
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
    wa_link = build_wa_link(row, text, media)
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
        'wa_link': wa_link,
        'action_status': 'ready',
        'action_ts': ''
    }


def prepare_daily_queue():
    contacts = get_sheet_rows('contacts_events')
    templates = get_sheet_rows('message_templates')
    festivals = get_sheet_rows('festival_calendar')
    today = _today_str()
    dt = datetime.strptime(today, '%Y-%m-%d')

    festival_rows = []
    for f in festivals:
        if int(f.get('month', 0)) == dt.month and int(f.get('day', 0)) == dt.day:
            festival_rows.append({
                'id': f"festival-{str(f.get('festival', '')).lower()}-{today}",
                'name': f"{f.get('festival', 'Festival')} Group",
                'phone': '',
                'chat_type': 'group',
                'group_invite_link': '',
                'event_type': str(f.get('festival', '')).lower(),
                'event_date': today,
                'relation': 'community',
                'language': f.get('default_language', 'en'),
                'tone': 'warm',
                'media_mode': f.get('default_media', 'text'),
                'active': 'TRUE'
            })

    todays = [r for r in contacts if str(r.get('event_date', '')) == today and str(r.get('active', '')).upper() == 'TRUE'] + festival_rows
    output = [build_queue_record(r, templates, today) for r in todays]
    write_ready_queue(output)
    return output


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

    sh = _client().open_by_key(SPREADSHEET_ID).worksheet('ready_queue')
    values = sh.get_all_values()
    headers = values[0]
    id_col = headers.index('id')
    action_col = headers.index('action_status')
    ts_col = headers.index('action_ts')

    for i, row in enumerate(values[1:], start=2):
      if str(row[id_col]) == rid:
            sh.update_cell(i, action_col + 1, action)
            sh.update_cell(i, ts_col + 1, datetime.now(ZoneInfo(TZ)).isoformat())
            return jsonify({'ok': True})
    return jsonify({'ok': False})


@app.post('/api/edit')
def edit_message():
    payload = request.get_json(force=True)
    rid = str(payload.get('id'))
    text = str(payload.get('text', ''))

    sh = _client().open_by_key(SPREADSHEET_ID).worksheet('ready_queue')
    values = sh.get_all_values()
    headers = values[0]
    id_col = headers.index('id')
    action_col = headers.index('action_status')
    ts_col = headers.index('action_ts')
    text_col = headers.index('final_message_text')

    for i, row in enumerate(values[1:], start=2):
        if str(row[id_col]) == rid:
            sh.update_cell(i, text_col + 1, text)
            sh.update_cell(i, action_col + 1, 'edited')
            sh.update_cell(i, ts_col + 1, datetime.now(ZoneInfo(TZ)).isoformat())
            return jsonify({'ok': True})
    return jsonify({'ok': False})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '8080')), debug=True)
