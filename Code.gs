// ============================================================================
// WA Automated Wishes — Google Apps Script
// Single source of truth for all queue logic, dashboard API, notifications
// ============================================================================

const SHEET_ID   = '1xYfyHukNh379NXdS99SJ3plqts0NlUt_w7bkIIKxvLQ';
const TZ         = 'Asia/Kolkata';
const NOTIFY_TO  = 'sugam.kuchhal.iimc@gmail.com';
const DASHBOARD_URL = 'https://sugamkuchhal.github.io/wa_automation/';

const QUEUE_HEADERS = [
  'id','queue_date','name','chat_type','event_type','phone',
  'group_invite_link','media_mode','media_url','final_message_text',
  'wa_link','action_status','action_ts'
];

// ── Script Properties (set via Apps Script editor → Project Settings → Script Properties)
// ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
// GIPHY_API_KEY (optional), UNSPLASH_ACCESS_KEY (optional)
function _prop(key) {
  return PropertiesService.getScriptProperties().getProperty(key) || '';
}

// ============================================================================
// HTTP endpoints — doGet / doPost
// ============================================================================

function doGet(e) {
  const action = (e.parameter.action || 'queue').toLowerCase();
  try {
    if (action === 'queue')   return _json(getTodayQueue());
    if (action === 'history') return _json(getHistory(parseInt(e.parameter.days || '30')));
    if (action === 'check')   return _json(runChecks());
    return _json({ error: 'Unknown action' });
  } catch(err) {
    return _json({ error: err.message });
  }
}

function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);
    const action = (data.action || '').toLowerCase();
    if (action === 'mark')        return _json(markRow(data.id, data.status));
    if (action === 'edit')        return _json(editRow(data.id, data.text));
    if (action === 'ai_generate') return _json(aiGenerate(data));
    if (action === 'add_contact') return _json(addContact(data));
    if (action === 'prepare')     return _json({ ok: true, count: prepareDailyQueue().length });
    return _json({ error: 'Unknown action' });
  } catch(err) {
    return _json({ ok: false, error: err.message });
  }
}

function _json(data) {
  return ContentService
    .createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}

// ============================================================================
// Sheet helpers
// ============================================================================

function _ss() {
  return SpreadsheetApp.openById(SHEET_ID);
}

function _ws(name) {
  const ws = _ss().getSheetByName(name);
  if (!ws) throw new Error('Sheet not found: ' + name);
  return ws;
}

function _getOrCreate(name) {
  const ss = _ss();
  return ss.getSheetByName(name) || ss.insertSheet(name);
}

function _sheetToObjects(name) {
  const ws = _ws(name);
  const values = ws.getDataRange().getValues();
  if (values.length < 2) return [];
  const headers = values[0].map(h => String(h).trim());
  return values.slice(1).map(row => {
    const obj = {};
    headers.forEach((h, i) => { obj[h] = row[i]; });
    return obj;
  });
}

function _todayStr() {
  return Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd');
}

function _nowStr() {
  return Utilities.formatDate(new Date(), TZ, "yyyy-MM-dd'T'HH:mm:ss");
}

// ============================================================================
// Date parsing — handles multiple formats from Google Sheets
// ============================================================================

function _parseDate(raw) {
  if (!raw) return null;
  const s = String(raw).trim();
  if (!s || s === '0') return null;

  // If Sheets already gave us a Date object
  if (raw instanceof Date && !isNaN(raw)) return raw;

  // Try common string formats
  const fmts = [
    { re: /^(\d{4})-(\d{2})-(\d{2})$/, fn: m => new Date(+m[1], +m[2]-1, +m[3]) },
    { re: /^(\d{2})-([A-Za-z]{3})-(\d{4})$/, fn: m => new Date(Date.parse(m[2]+' '+m[3])) },
    { re: /^(\d{2})\/(\d{2})\/(\d{4})$/, fn: m => new Date(+m[3], +m[1]-1, +m[2]) },
  ];
  for (const { re, fn } of fmts) {
    const m = s.match(re);
    if (m) { const d = fn(m); if (!isNaN(d)) return d; }
  }
  const d = new Date(s);
  return isNaN(d) ? null : d;
}

// ============================================================================
// Queue generation
// ============================================================================

function prepareDailyQueue() {
  const people   = _sheetToObjects('people_and_groups');
  const templates= _sheetToObjects('message_templates');
  const festivals= _sheetToObjects('festival_calendar');
  const eventRef = _loadEventRef();
  const today    = _todayStr();
  const todayDt  = new Date(today);

  // Build todays_festivals set from festival_calendar
  const todaysFestivals = new Set();
  festivals.forEach(f => {
    if (_festivalMatchesToday(f, todayDt, eventRef)) {
      todaysFestivals.add(String(f.festival || '').toLowerCase().trim());
    }
  });

  // Filter people_and_groups
  const todays = [];
  people.forEach(r => {
    const active = String(r.is_active || r.active || '').toUpperCase();
    if (active !== 'TRUE') return;
    const eventName = String(r.event_name || r.event_type || '').toLowerCase().trim();
    const category  = eventRef[eventName] || 'historical';
    if (category === 'fixed_festival' || category === 'variable_festival') {
      if (todaysFestivals.has(eventName)) todays.push(r);
    } else {
      if (_matchesToday(r, todayDt)) todays.push(r);
    }
  });

  // Build queue records
  const output = [];
  todays.forEach(r => {
    const eventName = String(r.event_name || r.event_type || '').toLowerCase().trim();
    const row = Object.assign({}, r, { event_type: eventName });

    if (String(row.chat_type || '').toLowerCase() === 'individual') {
      output.push(_buildRecord(row, templates, today));
      if (String(row.group_invite_link || '').trim()) {
        const rowGroup = Object.assign({}, row, { original_chat_type: 'individual' });
        output.push(_buildRecord(rowGroup, templates, today, '-group', 'group'));
      }
    } else {
      output.push(_buildRecord(row, templates, today));
    }
  });

  _archiveToHistory();
  _writeReadyQueue(output);
  Logger.log('Queue ready: ' + output.length + ' message(s)');
  return output;
}

function _loadEventRef() {
  try {
    const rows = _sheetToObjects('event_ref');
    const ref = {};
    rows.forEach(r => {
      const name = String(r.event_name || '').toLowerCase().trim();
      const cat  = String(r.event_category || 'historical').toLowerCase().trim();
      if (name) ref[name] = cat;
    });
    return ref;
  } catch(e) {
    return {};
  }
}

function _festivalMatchesToday(f, todayDt, eventRef) {
  const name = String(f.festival || '').toLowerCase().trim();
  const category = eventRef[name] || 'fixed_festival';
  const month = parseInt(f.month) || 0;
  const day   = parseInt(f.day)   || 0;
  if (!month || !day) return false;

  const yearVal = String(f.year || '').trim();
  const yearSet = yearVal && yearVal !== '0';

  if (category === 'fixed_festival') {
    return month === (todayDt.getMonth()+1) && day === todayDt.getDate();
  }
  if (category === 'variable_festival') {
    if (!yearSet) {
      Logger.log('WARNING: variable festival "' + name + '" has no year — skipping');
      return false;
    }
    return parseInt(yearVal) === todayDt.getFullYear()
      && month === (todayDt.getMonth()+1)
      && day === todayDt.getDate();
  }
  return month === (todayDt.getMonth()+1) && day === todayDt.getDate();
}

function _matchesToday(row, todayDt) {
  const raw = row.event_date;
  if (!raw) return false;
  const d = _parseDate(raw);
  if (!d) { Logger.log('Unrecognised date: ' + raw); return false; }
  return d.getMonth() === todayDt.getMonth() && d.getDate() === todayDt.getDate();
}

// ============================================================================
// Template rendering
// ============================================================================

function _renderTemplate(row, templates) {
  const eventType = String(row.event_type || '').trim();
  const language  = String(row.language   || '').trim();
  const chatType  = String(row.chat_type  || '').toLowerCase();
  // Groups always forced formal — hard override
  const tone = (chatType === 'group') ? 'formal' : String(row.tone || '').trim();

  // Match priority: exact → language → event → hardcoded
  const exact = templates.find(t =>
    String(t.event_type||'') === eventType &&
    String(t.language||'')   === language  &&
    String(t.tone||'')       === tone
  );
  const byLang = !exact && templates.find(t =>
    String(t.event_type||'') === eventType &&
    String(t.language||'')   === language
  );
  const byEvent = !exact && !byLang && templates.find(t =>
    String(t.event_type||'') === eventType
  );
  const tmpl = (exact || byLang || byEvent || { template_text: 'Hi {{name}}, wishing you a wonderful day!' });
  const text = String(tmpl.template_text || '');

  // Personalisation rules
  const originalChatType = String(row.original_chat_type || row.chat_type || '').toLowerCase();
  if (originalChatType === 'group') {
    // Pure group row — strip name
    return text.replace(/\{\{name\}\}/g, '').replace(/  +/g, ' ').trim();
  }
  // Individual (or individual-sourced group card) — keep name
  return text.replace(/\{\{name\}\}/g, String(row.name || 'there'));
}

// ============================================================================
// Media URL
// ============================================================================

function _buildMedia(row) {
  const mode = String(row.media_mode || '').toLowerCase();
  if (mode === 'text' || mode === 'manual_photo') return '';
  const eventType = String(row.event_type || 'celebration');

  if (mode === 'gif') {
    const key = _prop('GIPHY_API_KEY') || 'dc6zaTOxFJmzC';
    try {
      const url = 'https://api.giphy.com/v1/gifs/search?api_key=' + key +
        '&q=' + encodeURIComponent(eventType + ' celebration') + '&limit=1&rating=g';
      const res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
      const data = JSON.parse(res.getContentText());
      return data.data[0].images.original.url || '';
    } catch(e) { return ''; }
  }

  if (mode === 'image') {
    const unsplashKey = _prop('UNSPLASH_ACCESS_KEY');
    if (unsplashKey) {
      try {
        const url = 'https://api.unsplash.com/photos/random?query=' +
          encodeURIComponent(eventType + ' celebration') +
          '&orientation=squarish&content_filter=high&client_id=' + unsplashKey;
        const res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
        const data = JSON.parse(res.getContentText());
        return data.urls.regular || '';
      } catch(e) {}
    }
    return '';
  }
  return '';
}

// ============================================================================
// wa_link builder
// ============================================================================

function _buildWaLink(row, text, mediaUrl) {
  const fullText = mediaUrl ? text + '\n' + mediaUrl : text;
  const encoded  = encodeURIComponent(fullText);
  const chatType = String(row.chat_type || '').toLowerCase();
  const phone    = String(row.phone || '').replace(/\D/g, '');

  if (chatType === 'individual' && phone) {
    return 'https://wa.me/' + phone + '?text=' + encoded;
  }
  if (chatType === 'group') {
    const link = String(row.group_invite_link || '').trim();
    return link || '';
  }
  return '';
}

// ============================================================================
// Build single queue record
// ============================================================================

function _buildRecord(row, templates, dateStr, idSuffix, chatTypeOverride) {
  idSuffix = idSuffix || '';
  const chatType   = chatTypeOverride || String(row.chat_type || '').toLowerCase();
  const resolved   = Object.assign({}, row, { chat_type: chatType });
  const text       = _renderTemplate(resolved, templates);
  const mediaUrl   = _buildMedia(resolved);
  const waLink     = _buildWaLink(resolved, text, mediaUrl);
  const eventType  = String(resolved.event_type || '');

  return {
    id:                 String(row.id || '') + idSuffix,
    queue_date:         dateStr,
    name:               String(row.name || ''),
    chat_type:          chatType,
    event_type:         eventType,
    phone:              String(row.phone || ''),
    group_invite_link:  String(row.group_invite_link || ''),
    media_mode:         String(row.media_mode || ''),
    media_url:          mediaUrl,
    final_message_text: text,
    wa_link:            waLink,
    action_status:      'ready',
    action_ts:          ''
  };
}

// ============================================================================
// ready_queue read / write
// ============================================================================

function getTodayQueue() {
  try {
    const ws = _ws('ready_queue');
    const values = ws.getDataRange().getValues();
    if (values.length < 2) return [];
    const headers = values[0].map(h => String(h).trim());
    return values.slice(1)
      .map(row => _rowToObj(headers, row))
      .filter(r => {
        const s = String(r.action_status || '').toLowerCase();
        return s === 'ready' || s === 'edited' || s === 'skipped' || s === '';
      });
  } catch(e) {
    return [];
  }
}

function _writeReadyQueue(records) {
  const ws = _getOrCreate('ready_queue');
  ws.clearContents();
  ws.appendRow(QUEUE_HEADERS);
  if (records.length) {
    const rows = records.map(r => QUEUE_HEADERS.map(h => r[h] !== undefined ? r[h] : ''));
    ws.getRange(2, 1, rows.length, QUEUE_HEADERS.length).setValues(rows);
  }
}

function _archiveToHistory() {
  try {
    const rq = _ss().getSheetByName('ready_queue');
    if (!rq) return;
    const values = rq.getDataRange().getValues();
    if (values.length < 2) return;
    const dataRows = values.slice(1);

    const hist = _getOrCreate('send_history');
    const existing = hist.getDataRange().getValues();
    if (!existing || existing.length === 0 || existing[0].join(',') !== QUEUE_HEADERS.join(',')) {
      if (existing.length === 0) {
        hist.appendRow(QUEUE_HEADERS);
      } else {
        hist.insertRowBefore(1);
        hist.getRange(1, 1, 1, QUEUE_HEADERS.length).setValues([QUEUE_HEADERS]);
      }
    }
    hist.getRange(hist.getLastRow()+1, 1, dataRows.length, dataRows[0].length).setValues(dataRows);
    Logger.log('Archived ' + dataRows.length + ' rows to send_history');
  } catch(e) {
    Logger.log('Archive failed: ' + e.message);
  }
}

// ============================================================================
// mark / edit / unskip
// ============================================================================

function markRow(id, status) {
  const ws = _ws('ready_queue');
  const values = ws.getDataRange().getValues();
  const headers = values[0].map(h => String(h).trim());
  const idCol     = headers.indexOf('id');
  const statusCol = headers.indexOf('action_status') + 1;
  const tsCol     = headers.indexOf('action_ts') + 1;

  for (let i = 1; i < values.length; i++) {
    if (String(values[i][idCol]) === String(id)) {
      const row = i + 1;
      ws.getRange(row, statusCol).setValue(status);
      // Unskip clears timestamp; all other actions set it
      ws.getRange(row, tsCol).setValue(status === 'ready' ? '' : _nowStr());
      return { ok: true };
    }
  }
  return { ok: false, error: 'Row not found: ' + id };
}

function editRow(id, text) {
  const ws = _ws('ready_queue');
  const values = ws.getDataRange().getValues();
  const headers = values[0].map(h => String(h).trim());
  const idCol     = headers.indexOf('id');
  const textCol   = headers.indexOf('final_message_text') + 1;
  const statusCol = headers.indexOf('action_status') + 1;
  const tsCol     = headers.indexOf('action_ts') + 1;
  const waCol     = headers.indexOf('wa_link') + 1;
  const phoneCol  = headers.indexOf('phone');
  const chatCol   = headers.indexOf('chat_type');
  const inviteCol = headers.indexOf('group_invite_link');
  const mediaCol  = headers.indexOf('media_url');

  for (let i = 1; i < values.length; i++) {
    if (String(values[i][idCol]) === String(id)) {
      const row      = i + 1;
      const phone    = String(values[i][phoneCol] || '').replace(/\D/g, '');
      const chatType = String(values[i][chatCol]  || '').toLowerCase();
      const invite   = String(values[i][inviteCol]|| '');
      const mediaUrl = String(values[i][mediaCol] || '');
      const rowObj   = { chat_type: chatType, phone, group_invite_link: invite };
      const waLink   = _buildWaLink(rowObj, text, mediaUrl);

      ws.getRange(row, textCol).setValue(text);
      ws.getRange(row, statusCol).setValue('edited');
      ws.getRange(row, tsCol).setValue(_nowStr());
      ws.getRange(row, waCol).setValue(waLink);
      return { ok: true, wa_link: waLink };
    }
  }
  return { ok: false, error: 'Row not found: ' + id };
}

// ============================================================================
// History
// ============================================================================

function getHistory(days) {
  try {
    const ws = _ss().getSheetByName('send_history');
    if (!ws) return [];
    const values = ws.getDataRange().getValues();
    if (values.length < 2) return [];
    const headers = values[0].map(h => String(h).trim());
    let rows = values.slice(1).map(r => _rowToObj(headers, r));
    if (days > 0) {
      const cutoff = new Date();
      cutoff.setDate(cutoff.getDate() - days);
      const cutoffStr = Utilities.formatDate(cutoff, TZ, 'yyyy-MM-dd');
      rows = rows.filter(r => String(r.queue_date || '') >= cutoffStr);
    }
    return rows;
  } catch(e) {
    return [];
  }
}

// ============================================================================
// AI Generate
// ============================================================================

function aiGenerate(data) {
  const apiKey = _prop('ANTHROPIC_API_KEY');
  if (!apiKey) return { ok: false, error: 'ANTHROPIC_API_KEY not set in Script Properties' };

  // Rate limit — 30 calls/day stored in Script Properties
  const limit = 30;
  const today = _todayStr();
  const usageKey = 'ai_usage_' + today;
  const used = parseInt(_prop(usageKey) || '0');
  if (used >= limit) return { ok: false, error: 'Daily AI limit of ' + limit + ' reached. Resets tomorrow.' };
  PropertiesService.getScriptProperties().setProperty(usageKey, String(used + 1));

  const name      = String(data.name || 'there');
  const eventType = String(data.event_type || 'occasion');
  const relation  = String(data.relation || '');
  const language  = String(data.language || 'en');
  const tone      = String(data.tone || 'warm');
  const current   = String(data.current_text || '');

  const langLabel = { en: 'English', hi: 'Hindi', hinglish: 'Hinglish (mix of Hindi and English)' }[language] || 'English';
  const toneLabel = { warm: 'warm and heartfelt', casual: 'casual and friendly', formal: 'formal and respectful', fun: 'fun and playful' }[tone] || 'warm and heartfelt';
  const relationHint = relation ? ' They are my ' + relation + '.' : '';

  const prompt = 'Write a short WhatsApp message for ' + name + ' on their ' + eventType + '.' + relationHint + '\n' +
    'Language: ' + langLabel + '. Tone: ' + toneLabel + '.\n' +
    'Keep it under 3 sentences, personal, no hashtags, no generic filler.\n' +
    'Existing message for reference (improve it, do not copy): ' + current + '\n' +
    'Reply with ONLY the message text, nothing else.';

  try {
    const res = UrlFetchApp.fetch('https://api.anthropic.com/v1/messages', {
      method: 'post',
      contentType: 'application/json',
      headers: {
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01'
      },
      payload: JSON.stringify({
        model: 'claude-haiku-4-5',
        max_tokens: 256,
        messages: [{ role: 'user', content: prompt }]
      }),
      muteHttpExceptions: true
    });
    const d = JSON.parse(res.getContentText());
    if (d.content && d.content[0]) return { ok: true, text: d.content[0].text.trim() };
    return { ok: false, error: 'No response from API' };
  } catch(e) {
    return { ok: false, error: e.message };
  }
}

// ============================================================================
// Add contact from dashboard
// ============================================================================

function addContact(data) {
  const ws = _ws('people_and_groups');
  const headers = ws.getRange(1, 1, 1, ws.getLastColumn()).getValues()[0].map(h => String(h).trim());

  // Validate required fields
  if (!data.id)         return { ok: false, error: 'id is required' };
  if (!data.name)       return { ok: false, error: 'name is required' };
  if (!data.event_name) return { ok: false, error: 'event_name is required' };
  if (!data.chat_type)  return { ok: false, error: 'chat_type is required' };

  // Check duplicate id
  const existing = ws.getDataRange().getValues();
  const idCol = headers.indexOf('id');
  for (let i = 1; i < existing.length; i++) {
    if (String(existing[i][idCol]) === String(data.id)) {
      return { ok: false, error: 'id "' + data.id + '" already exists' };
    }
  }

  const row = headers.map(h => {
    if (h === 'is_active') return data.is_active !== undefined ? data.is_active : 'TRUE';
    return data[h] !== undefined ? data[h] : '';
  });
  ws.appendRow(row);
  return { ok: true };
}

// ============================================================================
// Notifications
// ============================================================================

function _sendEmail(subject, body) {
  try {
    MailApp.sendEmail({ to: NOTIFY_TO, subject, body, name: 'WA Automated Wishes' });
  } catch(e) {
    Logger.log('Email failed: ' + e.message);
  }
}

function _sendTelegram(text) {
  const token  = _prop('TELEGRAM_BOT_TOKEN');
  const chatId = _prop('TELEGRAM_CHAT_ID');
  if (!token || token === 'placeholder') return;
  try {
    UrlFetchApp.fetch('https://api.telegram.org/bot' + token + '/sendMessage', {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify({ chat_id: chatId, text, parse_mode: 'Markdown' }),
      muteHttpExceptions: true
    });
  } catch(e) {
    Logger.log('Telegram failed: ' + e.message);
  }
}

// ============================================================================
// Morning trigger — called by time-based trigger at 9 AM IST
// ============================================================================

function morningRun() {
  const results = prepareDailyQueue();
  const today   = Utilities.formatDate(new Date(), TZ, 'dd MMMM yyyy');

  if (!results.length) {
    Logger.log('No messages today — no notifications sent');
    return;
  }

  const lines   = results.map(r => '• ' + r.name + ' — ' + (r.event_type || '').replace(/\b\w/g, c => c.toUpperCase()));
  const summary = lines.join('\n');
  const count   = results.length;

  _sendTelegram('📨 *WA Wishes — ' + today + '*\n\n' + summary + '\n\n👉 ' + DASHBOARD_URL);
  _sendEmail(
    '📨 WA Wishes — ' + count + ' message(s) for ' + today,
    'Good morning! Here\'s your WhatsApp queue for ' + today + ':\n\n' + summary + '\n\nOpen dashboard:\n' + DASHBOARD_URL + '\n\n— WA Automated Wishes'
  );
  Logger.log('Morning run complete: ' + count + ' message(s)');
}

// ============================================================================
// Evening trigger — called by time-based trigger at 7 PM IST
// ============================================================================

function eveningRun() {
  const pending = getTodayQueue().filter(r => {
    const s = String(r.action_status || '').toLowerCase();
    return s === 'ready' || s === 'edited' || s === '';
  });

  if (!pending.length) {
    Logger.log('Evening: all done — no reminder needed');
    return;
  }

  const today   = Utilities.formatDate(new Date(), TZ, 'dd MMMM yyyy');
  const lines   = pending.map(r => '• ' + r.name + ' — ' + (r.event_type || '').replace(/\b\w/g, c => c.toUpperCase()));
  const summary = lines.join('\n');
  const count   = pending.length;

  _sendTelegram('⏰ *WA Wishes reminder*\n\nYou still have ' + count + ' message(s) unsent:\n\n' + summary + '\n\n👉 ' + DASHBOARD_URL);
  _sendEmail(
    '⏰ WA Wishes reminder — ' + count + ' message(s) still pending',
    'Friendly reminder — you still have unsent messages for ' + today + ':\n\n' + summary + '\n\nOpen dashboard:\n' + DASHBOARD_URL + '\n\n— WA Automated Wishes'
  );
  Logger.log('Evening reminder sent: ' + count + ' pending');
}

// ============================================================================
// Health checks — run manually to verify setup
// ============================================================================

function runChecks() {
  const required = ['people_and_groups', 'event_ref', 'festival_calendar', 'message_templates'];
  const ss = _ss();
  const titles = ss.getSheets().map(s => s.getName());
  const missing = required.filter(n => !titles.includes(n));
  const warnings = [];

  // Phone validation
  try {
    const people = _sheetToObjects('people_and_groups');
    people.forEach(r => {
      const active = String(r.is_active || r.active || '').toUpperCase();
      if (active !== 'TRUE') return;
      if (String(r.chat_type || '').toLowerCase() !== 'individual') return;
      const phone = String(r.phone || '').trim();
      const digits = phone.replace(/\D/g, '');
      if (!phone)          warnings.push('id=' + r.id + ': phone is blank');
      else if (phone !== digits) warnings.push('id=' + r.id + ': phone "' + phone + '" has non-digit characters');
      else if (digits.length < 7) warnings.push('id=' + r.id + ': phone too short');
    });
  } catch(e) {}

  // Duplicate id check
  try {
    const people = _sheetToObjects('people_and_groups');
    const ids = people.map(r => String(r.id || ''));
    const dupes = ids.filter((id, i) => ids.indexOf(id) !== i);
    if (dupes.length) warnings.push('Duplicate ids found: ' + [...new Set(dupes)].join(', '));
  } catch(e) {}

  return {
    ok: missing.length === 0,
    missing_sheets: missing,
    warnings,
    all_sheets: titles
  };
}

// ============================================================================
// Utility
// ============================================================================

function _rowToObj(headers, row) {
  const obj = {};
  headers.forEach((h, i) => { obj[h] = row[i]; });
  return obj;
}

// ============================================================================
// Time trigger setup — run once to install triggers
// ============================================================================

function installTriggers() {
  // Remove existing triggers first
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));

  // Morning: 9:00 AM IST = 3:30 AM UTC → use hour 3 (Apps Script uses local time of script owner)
  ScriptApp.newTrigger('morningRun')
    .timeBased()
    .atHour(9)
    .nearMinute(0)
    .inTimezone(TZ)
    .everyDays(1)
    .create();

  // Evening: 7:00 PM IST
  ScriptApp.newTrigger('eveningRun')
    .timeBased()
    .atHour(19)
    .nearMinute(0)
    .inTimezone(TZ)
    .everyDays(1)
    .create();

  Logger.log('Triggers installed: morningRun at 9 AM, eveningRun at 7 PM IST');
}
