const { spawn } = require('child_process');
const express = require('express');
const path = require('path');
const fs = require('fs');

const ID    = process.env.GREEN_API_ID    || '7107642551';
const TOKEN = process.env.GREEN_API_TOKEN || '5b7315dfa1cd46beaed1f82da183a246471219b64d674e029d';
const BASE  = `https://api.green-api.com/waInstance${ID}`;

const ALLOWED = [
  '972547198498', // דני
  '972539598622', // רון
];

const SERVER_START = Math.floor(Date.now() / 1000); // זמן עליית השרת

const app = express();
app.use(express.json());

// מניעת עיבוד כפול של אותה הודעה
const processedIds = new Set();

// המתנה לאישור: phone → { originalText, correctedName, type, timestamp }
// type: 'confirm' (confidence) | 'fuzzy' (שם עמום)
const pendingConfirmations = new Map();

// זיכרון בחירת מזון: query → chosen food name (נשמר לדיסק)
const foodPrefs = new Map();
// זיכרון בחירת hint: hintQuery → chosen food row name
const hintPrefs = new Map();
// זיכרון שמות: rawName → correctedName (למניעת CONFIRM חוזר)
const namePrefs = new Map();

const PREFS_FILE = path.join(__dirname, 'prefs.json');

function loadPrefs() {
  try {
    const raw = fs.readFileSync(PREFS_FILE, 'utf8');
    const data = JSON.parse(raw);
    (data.food || []).forEach(([k, v]) => foodPrefs.set(k, v));
    (data.hint || []).forEach(([k, v]) => hintPrefs.set(k, v));
    (data.name || []).forEach(([k, v]) => namePrefs.set(k, v));
    console.log(`[prefs loaded] food=${foodPrefs.size} hint=${hintPrefs.size} name=${namePrefs.size}`);
  } catch (e) {
    if (e.code !== 'ENOENT') console.warn('[prefs load error]', e.message);
  }
}

function savePrefs() {
  try {
    const data = {
      food: [...foodPrefs.entries()],
      hint: [...hintPrefs.entries()],
      name: [...namePrefs.entries()],
    };
    fs.writeFileSync(PREFS_FILE, JSON.stringify(data, null, 2), 'utf8');
  } catch (e) {
    console.warn('[prefs save error]', e.message);
  }
}

loadPrefs();

// ─── שליחת הודעה ─────────────────────────────────────────────
async function sendMessage(phone, text) {
  const chatId = phone.includes('@') ? phone : `${phone}@c.us`;
  const body = JSON.stringify({ chatId, message: text });
  try {
    const res = await fetch(`${BASE}/sendMessage/${TOKEN}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    });
    if (!res.ok) {
      const errBody = await res.text().catch(() => '');
      console.error(`[sendMessage] FAILED ${res.status} to ${chatId}: ${errBody.slice(0,100)}`);
    }
    return res.ok;
  } catch (e) {
    console.error(`[sendMessage] EXCEPTION to ${chatId}: ${e.message}`);
    return false;
  }
}

// תיקונים ממתינים: phone → { type, originalText, alternatives, timestamp }
const pendingCorrections = new Map();

// זיכרון קונטקסט: sender → { text, opts } — הפקודה האחרונה שהצליחה
const lastCommands = new Map();

// ─── הרצת autofit ─────────────────────────────────────────────
function runAutofit(phone, text, opts = {}) {
  const script = path.join(__dirname, 'autofit_api.py');
  const args = [];
  if (opts.force)          args.push('--force');
  if (opts.nameOverride)   { args.push('--name'); args.push(opts.nameOverride); }
  if (opts.mealOverride)   { args.push('--meal'); args.push(opts.mealOverride); }
  if (opts.foodOverride)   { args.push('--food'); args.push(opts.foodOverride); }
  if (opts.hintOverride)   { args.push('--hint'); args.push(opts.hintOverride); }
  if (opts.userIdOverride) { args.push('--user-id'); args.push(opts.userIdOverride); }
  args.push(text);

  const proc = spawn('python3', [script, ...args]);
  let output = '';
  let timedOut = false;

  const killTimer = setTimeout(() => {
    timedOut = true;
    proc.kill();
    console.error('[timeout] Python process killed after 30s');
    sendMessage(phone, '❌ הבקשה לקחה יותר מדי זמן. נסה שוב.');
  }, 30000);

  proc.stdout.on('data', d => { output += d.toString(); });
  proc.stderr.on('data', d => console.error('[autofit err]', d.toString().trim()));

  proc.on('close', async code => {
    clearTimeout(killTimer);
    if (timedOut) return;
    const raw = output.trim() || (code === 0 ? 'בוצע!' : 'משהו השתבש');
    console.log(`[py→] ${raw.slice(0,80).replace(/\n/g,' | ')}`);

    // CONFIRM עם fuzzy name — שם מתוקן כלול בsummary
    if (raw.startsWith('CONFIRM_WITH_NAME:')) {
      const rest = raw.slice('CONFIRM_WITH_NAME:'.length);
      const sepIdx = rest.indexOf('|||');
      const correctedName = rest.slice(0, sepIdx).trim();
      const summary = rest.slice(sepIdx + 3);
      // חלץ את השם המקורי מה-summary
      const rawMatch = summary.match(/מצאתי עבור "([^"]+)"/);
      const rawName = rawMatch ? rawMatch[1] : '';
      // יש name preference? בצע ישירות
      if (rawName && namePrefs.get(rawName.toLowerCase()) === correctedName) {
        console.log(`[name-pref] "${rawName}" → "${correctedName}" (auto)`);
        await sendMessage(phone, '⏳ מבצע...');
        runAutofit(phone, text, { force: true, nameOverride: correctedName });
        return;
      }
      pendingConfirmations.set(phone, { originalText: text, correctedName, rawName, type: 'confirm_with_name', timestamp: Date.now() });
      await sendMessage(phone, `❓ הבנתי:\n${summary}\n\nנכון? שלח *כן* לאישור או *לא* לביטול`);
      return;
    }

    // CONFIRM רגיל
    if (raw.startsWith('CONFIRM:')) {
      const summary = raw.slice('CONFIRM:'.length).trim();
      pendingConfirmations.set(phone, { originalText: text, type: 'confirm', timestamp: Date.now() });
      await sendMessage(phone, `❓ הבנתי:\n${summary}\n\nנכון? שלח *כן* לאישור או *לא* לביטול`);
      return;
    }

    // MEAL_OPTIONS — ארוחה לא נמצאה, רשימה ממוספרת
    if (raw.startsWith('MEAL_OPTIONS:')) {
      const [header, ...rest] = raw.split('\n');
      const alts = header.slice('MEAL_OPTIONS:'.length).split('|').slice(1);
      const userMsg = rest.join('\n');
      pendingCorrections.set(phone, { type: 'meal', originalText: text, alternatives: alts, nameOverride: opts.nameOverride || '', timestamp: Date.now() });
      await sendMessage(phone, userMsg);
      return;
    }

    // HINT_OPTIONS — מזון להחלפה לא נמצא, רשימה ממוספרת
    if (raw.startsWith('HINT_OPTIONS:')) {
      const [header, ...rest] = raw.split('\n');
      const headerBody = header.slice('HINT_OPTIONS:'.length);
      const sepIdx = headerBody.indexOf('||');
      const hintQuery = sepIdx >= 0 ? headerBody.slice(0, sepIdx) : '';
      const alts = (sepIdx >= 0 ? headerBody.slice(sepIdx + 2) : headerBody).split('|');
      const userMsg = rest.join('\n');

      // יש העדפה שמורה? בחר אוטומטית (אלא אם המשתמש ביקש "אפשרות אחרת")
      const pref = hintQuery && !opts.skipFoodPref && hintPrefs.get(hintQuery.toLowerCase());
      if (pref && alts.includes(pref)) {
        console.log(`[hint-pref] ${hintQuery} → ${pref} (auto)`);
        await sendMessage(phone, '⏳ מבצע...');
        runAutofit(phone, text, { force: true, hintOverride: pref, nameOverride: opts.nameOverride || '', foodOverride: opts.foodOverride || '' });
        return;
      }

      // שמור גם foodOverride שכבר נבחר — כדי לא לשכוח אותו
      pendingCorrections.set(phone, { type: 'hint', originalText: text, alternatives: alts, hintQuery, nameOverride: opts.nameOverride || '', foodOverride: opts.foodOverride || '', timestamp: Date.now() });
      await sendMessage(phone, userMsg);
      return;
    }

    // NAME_OPTIONS — ריבוי מתאמנים באותו שם
    if (raw.startsWith('NAME_OPTIONS:')) {
      const [header, ...rest] = raw.split('\n');
      const headerBody = header.slice('NAME_OPTIONS:'.length);
      const sepIdx = headerBody.indexOf('||');
      const ids = (sepIdx >= 0 ? headerBody.slice(0, sepIdx) : headerBody).split('|');
      const names = (sepIdx >= 0 ? headerBody.slice(sepIdx + 2) : '').split('|');
      const userMsg = rest.join('\n');
      pendingCorrections.set(phone, { type: 'name_choice', originalText: text, alternatives: names, userIds: ids, nameOverride: opts.nameOverride || '', timestamp: Date.now() });
      await sendMessage(phone, userMsg);
      return;
    }

    // שם לא נמצא — שאל לתיקון
    if (raw.startsWith('NAME_NOT_FOUND:')) {
      const badName = raw.slice('NAME_NOT_FOUND:'.length).trim();
      pendingCorrections.set(phone, { type: 'name', originalText: text, timestamp: Date.now() });
      await sendMessage(phone, `❌ לא מצאתי מתאמן בשם *${badName}*.\n\nשלח את השם המלא כפי שרשום ב-auto-fit ואנסה שוב.`);
      return;
    }

    // FOOD_OPTIONS — מזון חדש עמום, רשימה ממוספרת
    if (raw.startsWith('FOOD_OPTIONS:')) {
      const [header, ...rest] = raw.split('\n');
      const headerBody = header.slice('FOOD_OPTIONS:'.length);
      const sepIdx = headerBody.indexOf('||');
      const foodQuery = sepIdx >= 0 ? headerBody.slice(0, sepIdx) : '';
      const alts = (sepIdx >= 0 ? headerBody.slice(sepIdx + 2) : headerBody).split('|');
      const userMsg = rest.join('\n');

      // יש העדפה שמורה? בחר אוטומטית (אלא אם המשתמש ביקש "אפשרות אחרת")
      const pref = foodQuery && !opts.skipFoodPref && foodPrefs.get(foodQuery.toLowerCase());
      if (pref && alts.includes(pref)) {
        console.log(`[pref] ${foodQuery} → ${pref} (auto)`);
        await sendMessage(phone, '⏳ מבצע...');
        runAutofit(phone, text, { force: true, foodOverride: pref, nameOverride: opts.nameOverride || '', hintOverride: opts.hintOverride || '' });
        return;
      }

      // שמור גם hintOverride שכבר נבחר — כדי לא לשכוח אותו
      // שמור כל האלטרנטיבות לpagination (pageOffset=0 = עמוד ראשון)
      pendingCorrections.set(phone, { type: 'food', originalText: text, allAlternatives: alts, alternatives: alts.slice(0, 10), pageOffset: 0, foodQuery, nameOverride: opts.nameOverride || '', hintOverride: opts.hintOverride || '', timestamp: Date.now() });
      await sendMessage(phone, userMsg);
      return;
    }

    if (!raw.startsWith('CONFIRM')) {
      pendingCorrections.delete(phone);
    }

    // שמור פקודה אחרונה שהצליחה (לא CONFIRM / OPTIONS)
    if (code === 0 && !raw.startsWith('CONFIRM') && !raw.startsWith('FOOD_OPTIONS') &&
        !raw.startsWith('HINT_OPTIONS') && !raw.startsWith('MEAL_OPTIONS') &&
        !raw.startsWith('NAME_OPTIONS') && !raw.startsWith('NAME_NOT_FOUND')) {
      lastCommands.set(phone, { text, opts });
    }

    await sendMessage(phone, code === 0 ? raw : `❌ ${raw}`);
  });
}

// ─── הצגת תפריט מתאמן ────────────────────────────────────────
function runMenu(phone, name, meal = '') {
  const script = path.join(__dirname, 'autofit_api.py');
  const args = ['--menu', name];
  if (meal) { args.push('--menu-meal'); args.push(meal); }
  const proc = spawn('python3', [script, ...args]);
  let output = '';
  let timedOut = false;
  const killTimer = setTimeout(() => {
    timedOut = true;
    proc.kill();
    sendMessage(phone, '❌ הבקשה לקחה יותר מדי זמן. נסה שוב.');
  }, 30000);
  proc.stdout.on('data', d => { output += d.toString(); });
  proc.stderr.on('data', d => console.error('[menu err]', d.toString().trim()));
  proc.on('close', async code => {
    clearTimeout(killTimer);
    if (timedOut) return;
    await sendMessage(phone, output.trim() || '❌ לא נמצא תפריט');
  });
}

// ─── "לכולם" — הרצת פקודה על כל הלקוחות ────────────────────────────────────
function getAllClientNames() {
  return new Promise((resolve) => {
    const script = path.join(__dirname, 'autofit_api.py');
    const proc = spawn('python3', [script, '--list-users']);
    let output = '';
    proc.stdout.on('data', d => { output += d.toString(); });
    proc.on('close', () => {
      const names = output.split('\n').map(n => n.trim()).filter(n => n.length >= 2);
      resolve(names);
    });
    proc.on('error', () => resolve([]));
    setTimeout(() => { proc.kill(); resolve([]); }, 15000);
  });
}

// בדיקת תקינות שם: האם NAME הוא שם פרטי עצמאי של לקוח (לא שם משפחה בלבד)
function quickFindUser(name) {
  return new Promise(resolve => {
    const script = path.join(__dirname, 'autofit_api.py');
    const proc = spawn('python3', [script, '--find-user', name]);
    let out = '';
    proc.stdout.on('data', d => { out += d.toString(); });
    proc.on('close', () => resolve(out.trim() === 'FOUND'));
    proc.on('error', () => resolve(false));
    setTimeout(() => { proc.kill(); resolve(false); }, 5000);
  });
}

async function runForAllClients(phone, baseText) {
  const names = await getAllClientNames();
  if (!names.length) {
    await sendMessage(phone, '❌ לא הצלחתי לטעון רשימת לקוחות.');
    return;
  }
  await sendMessage(phone, `⏳ מבצע עבור ${names.length} לקוחות...`);
  for (const name of names) {
    await new Promise(resolve => setTimeout(resolve, 300)); // throttle
    runAutofit(phone, baseText, { nameOverride: name, force: true });
  }
}

// ─── Webhook מ-Green API ──────────────────────────────────────
app.post('/webhook', async (req, res) => {
  res.sendStatus(200);

  const body = req.body;

  // ── V3: הודעות יוצאות מדני ללקוחות ────────────────────────────────────
  if (body.typeWebhook === 'outgoingMessageReceived') {
    console.log('[v3-entry] outgoing webhook arrived, BIZ_GROUP=' + BIZ_GROUP);
    v3Log.push({ ts: Date.now(), step: 'entry', biz_group: !!BIZ_GROUP, type: body.typeWebhook });
    if (v3Log.length > 50) v3Log.shift();
    if (!BIZ_GROUP) return;
    const outMsg = body.messageData;
    if (!outMsg || outMsg.typeMessage !== 'textMessage') { console.log('[v3] no textMessage, type=' + outMsg?.typeMessage); return; }
    const outText = outMsg.textMessageData?.textMessage?.trim();
    if (!outText) return;
    const outChatId = body.senderData?.chatId || '';
    if (outChatId.includes('@g.us')) return; // דלג קבוצות
    const BIZ_TEST_PHONE = process.env.BIZ_TEST_PHONE;
    if (BIZ_TEST_PHONE) {
      const allowed = BIZ_TEST_PHONE.split(',').map(p => p.trim()).filter(Boolean);
      const ph = outChatId.replace('@c.us','').replace(/^972/,'0');
      const phAlt = outChatId.replace('@c.us','');
      const isAllowed = allowed.some(a => ph === a || phAlt === a || phAlt === '972'+a.replace(/^0/,''));
      if (!isAllowed) return;
    }
    if (!hasTriggerWord(outText)) return;
    // קבל שם איש קשר: אם ה-webhook לא כולל chatName (כגון polling) — שאל GreenAPI
    let outName = body.senderData?.chatName || '';
    if (!outName || /^\d{7,}$/.test(outName)) {
      outName = await getContactName(outChatId);
    }
    const outPhone = outChatId.replace('@c.us','');
    console.log(`[v3] פקודה יוצאת: "${outName}" (${outPhone}) | "${outText.slice(0,60)}"`);
    v3Log.push({ ts: Date.now(), step: 'trigger', name: outName, phone: outPhone, text: outText.slice(0,40) });
    const cachedId = bizNamePrefs.get(outName.toLowerCase());
    runAutofitBiz(outName, outPhone, outText, cachedId ? { userIdOverride: cachedId } : {});
    return;
  }

  if (body.typeWebhook !== 'incomingMessageReceived') return;

  const msg = body.messageData;
  if (!msg || msg.typeMessage !== 'textMessage') return;

  // התעלם מ-webhooks ישנים (מעל 3 דקות לפני start) — retries קצרים אחרי restart עוברים
  const msgTimestamp = body.timestamp;
  if (msgTimestamp && msgTimestamp < SERVER_START - 180) {
    console.log(`[skip] webhook ישן מדי (${Math.round(SERVER_START - msgTimestamp)}s ago)`);
    return;
  }

  // מניעת כפילויות — idMessage נמצא ב-top level של body
  const msgId = body.idMessage;
  if (msgId) {
    if (processedIds.has(msgId)) return;
    processedIds.add(msgId);
    if (processedIds.size > 500) {
      const arr = [...processedIds];
      arr.slice(0, 250).forEach(id => processedIds.delete(id));
    }
  }

  const text = msg.textMessageData?.textMessage?.trim();
  if (!text) return;

  const sender = body.senderData?.sender?.replace('@c.us', '');
  if (!ALLOWED.includes(sender)) return;

  // ── תגובות דני מהטלפון האישי בקבוצת הצוות ──────────────────
  const inChatId = body.senderData?.chatId || '';
  if (BIZ_GROUP && (inChatId === BIZ_GROUP || inChatId.replace('@g.us','') === (BIZ_GROUP||'').replace('@g.us',''))) {
    console.log(`[group-reply] ${sender}: "${text.slice(0,40)}"`);
    await handleBizGroupResponse(text);
    return;
  }

  console.log(`📨 מ: ${sender} | "${text.slice(0,50).replace(/\n/g,'↵')}"`);
  console.log(`[state] conf=${pendingConfirmations.has(sender)} corr=${pendingCorrections.has(sender)}`);

  // ─── ביטול גלובלי ─────────────────────────────────────────────
  const lower_cancel = text.trim().toLowerCase();
  if (['בטל', 'ביטול', 'cancel', 'לבטל'].includes(lower_cancel)) {
    const hadPending = pendingConfirmations.has(sender) || pendingCorrections.has(sender);
    pendingConfirmations.delete(sender);
    pendingCorrections.delete(sender);
    await sendMessage(sender, hadPending ? '❌ הפעולה בוטלה' : 'אין פעולה פעילה לביטול');
    return;
  }

  // ─── תפריט ─────────────────────────────────────────────────────
  // "דני תפריט" / "תפריט דני" / "מה יש לדני בבוקר?" / "תראי לי את התפריט של דני"
  const _MEAL_KW = '(?:בוקר|צהריים|צהרים|ערב|לילה|ביניים)';
  const _HEB_NAME = `[א-׺][א-׺\\s'״׳]{1,30}`;
  const menuM =
    new RegExp(`^(${_HEB_NAME})\\s+תפריט(?:\\s+(${_MEAL_KW}))?$`).exec(text.trim()) ||
    new RegExp(`^תפריט\\s+(${_HEB_NAME})(?:\\s+(${_MEAL_KW}))?$`).exec(text.trim()) ||
    // "מה יש לדני?" / "מה יש לדני בבוקר?"
    new RegExp(`^מה\\s+(?:יש|אוכל)\\s+ל(${_HEB_NAME})(?:\\s+ב(${_MEAL_KW}))?\\??$`).exec(text.trim()) ||
    // "תראי לי את התפריט של דני" / "תראי לי את התפריט של דני בערב"
    new RegExp(`^(?:תראי?|הראי?)\\s+(?:לי\\s+)?(?:את\\s+)?(?:ה)?תפריט\\s+(?:של\\s+)?(${_HEB_NAME})(?:\\s+(${_MEAL_KW}))?$`).exec(text.trim());
  if (menuM) {
    const menuName = menuM[1].trim();
    const menuMeal = (menuM[2] || '').trim();
    console.log(`📋 תפריט עבור: "${menuName}"${menuMeal ? ` (${menuMeal})` : ''}`);
    runMenu(sender, menuName, menuMeal);
    return;
  }

  // בדוק אם יש תיקון ממתין (ארוחה / בחירת מזון)
  if (pendingCorrections.has(sender)) {
    const corr = pendingCorrections.get(sender);
    if (Date.now() - corr.timestamp > 5 * 60 * 1000) {
      pendingCorrections.delete(sender);
    } else if (corr.type === 'meal') {
      pendingCorrections.delete(sender);
      await sendMessage(sender, '⏳ מבצע...');
      runAutofit(sender, corr.originalText, { force: true, mealOverride: text.trim(), nameOverride: corr.nameOverride || '' });
      return;
    } else if (corr.type === 'name') {
      if (text.trim().length >= 2) {
        pendingCorrections.delete(sender);
        await sendMessage(sender, '⏳ מבצע...');
        runAutofit(sender, corr.originalText, { force: true, nameOverride: text.trim() });
        return;
      }
    } else if (corr.type === 'name_choice') {
      const phoneMatch = text.trim().replace(/[\s\-\(\)]/g, '').match(/^0\d{9}$/);
      if (phoneMatch) {
        // טלפון לזיהוי מתאמן
        pendingCorrections.delete(sender);
        await sendMessage(sender, '⏳ מבצע...');
        runAutofit(sender, corr.originalText, { force: true, nameOverride: phoneMatch[0] });
        return;
      }
      const numMatch = text.trim().match(/^(\d+)$/);
      if (numMatch) {
        const idx = parseInt(numMatch[1]) - 1;
        if (idx >= 0 && idx < corr.alternatives.length) {
          const chosenName = corr.alternatives[idx];
          const chosenId = corr.userIds[idx];
          pendingCorrections.delete(sender);
          await sendMessage(sender, '⏳ מבצע...');
          runAutofit(sender, corr.originalText, { force: true, nameOverride: chosenName, userIdOverride: chosenId });
          return;
        }
      }
      return;
    } else if (corr.type === 'food' || corr.type === 'hint') {
      // פקודה חדשה — נקה state ועבד מחדש (מובנית או פועל בתחילה)
      const _isNewCmd = /שם\s*:|ארוחה\s*:/i.test(text) ||
        /^(?:תוסיפ[יי]?|הוסיפ[יי]?|הוסף|תוסיף|הכנס|תכניס|הורד|הפחת|תוריד|תחליפ[יי]?|החליפ[יי]?|להוסיף|להחליף)\s/i.test(text.trim());
      if (_isNewCmd) {
        pendingCorrections.delete(sender);
      } else if (corr.type === 'food' && /^(?:עוד|הצג עוד|עוד אפשרויות)$/i.test(text.trim())) {
        // pagination — הצג עמוד הבא של 10
        const allAlts = corr.allAlternatives || corr.alternatives;
        const nextOffset = (corr.pageOffset || 0) + 10;
        const nextPage = allAlts.slice(nextOffset, nextOffset + 10);
        if (nextPage.length === 0) {
          await sendMessage(sender, 'אין עוד אפשרויות.');
        } else {
          const listLines = nextPage.map((name, i) => `${nextOffset + i + 1}. ${name}`).join('\n');
          const hasMore = allAlts.length > nextOffset + 10;
          const moreHint = hasMore ? '\n\nשלח *עוד* לעוד אפשרויות.' : '';
          // עדכן pageOffset ורשימת alternatives הנוכחית
          pendingCorrections.set(sender, { ...corr, alternatives: nextPage, pageOffset: nextOffset, timestamp: Date.now() });
          await sendMessage(sender, `${listLines}${moreHint}`);
        }
        return;
      } else {
        const numMatch = text.trim().match(/^(\d+)$/);
        let chosen = null;
        if (numMatch) {
          // המספר הוא גלובלי (1-30), לא יחסי לעמוד
          const idx = parseInt(numMatch[1]) - 1;
          const allAlts = corr.allAlternatives || corr.alternatives;
          if (idx >= 0 && idx < allAlts.length) chosen = allAlts[idx];
        } else if (text.trim().length >= 2) {
          chosen = text.trim();
        }
        if (chosen) {
          pendingCorrections.delete(sender);
          await sendMessage(sender, '⏳ מבצע...');
          if (corr.type === 'hint') {
            // שמור hint preference לפעם הבאה
            if (corr.hintQuery) {
              hintPrefs.set(corr.hintQuery.toLowerCase(), chosen);
              console.log(`[hint-pref saved] "${corr.hintQuery}" → "${chosen}"`);
              savePrefs();
            }
            // זכור גם foodOverride שנבחר קודם
            runAutofit(sender, corr.originalText, { force: true, hintOverride: chosen, nameOverride: corr.nameOverride || '', foodOverride: corr.foodOverride || '' });
          } else {
            // שמור העדפה לפעם הבאה
            if (corr.foodQuery) {
              foodPrefs.set(corr.foodQuery.toLowerCase(), chosen);
              console.log(`[pref saved] "${corr.foodQuery}" → "${chosen}"`);
              savePrefs();
            }
            // זכור גם hintOverride שנבחר קודם
            runAutofit(sender, corr.originalText, { force: true, foodOverride: chosen, nameOverride: corr.nameOverride || '', hintOverride: corr.hintOverride || '' });
          }
          return;
        }
      }
    }
  }

  // בדוק אם יש אישור ממתין
  if (pendingConfirmations.has(sender)) {
    const pending = pendingConfirmations.get(sender);

    // פג תוקף אחרי 5 דקות
    if (Date.now() - pending.timestamp > 5 * 60 * 1000) {
      pendingConfirmations.delete(sender);
    } else {
      const lower = text.trim();
      if (lower === 'כן' || lower === 'yes') {
        pendingConfirmations.delete(sender);
        await sendMessage(sender, '⏳ מבצע...');
        if (pending.type === 'fuzzy' || pending.type === 'confirm_with_name') {
          // שמור name preference — לא ישאל שוב על אותו שם
          if (pending.rawName && pending.correctedName) {
            namePrefs.set(pending.rawName.toLowerCase(), pending.correctedName);
            console.log(`[name-pref saved] "${pending.rawName}" → "${pending.correctedName}"`);
            savePrefs();
          }
          runAutofit(sender, pending.originalText, { force: true, nameOverride: pending.correctedName });
        } else {
          runAutofit(sender, pending.originalText, { force: true });
        }
        return;
      }
      if (lower === 'לא' || lower === 'no') {
        pendingConfirmations.delete(sender);
        await sendMessage(sender, '❌ בוטל');
        return;
      }
    }
  }

  // "אפשרות אחרת" / "אופציה נוספת" — חיפוש חדש, מתעלם מהעדפה שמורה
  const _skipPref = /(?:אפשרות|אופציה)\s+(?:אחרת|נוספת)|תחפש\s+עוד/.test(text);

  // ── context memory: "כמו אתמול" / "שוב" / "עוד X גרם" ───────────────────
  const _ctxRepeat = /^(?:כמו אתמול|אותו דבר|שוב|חזור על זה|אותו הדבר)$/.test(text.trim());
  if (_ctxRepeat) {
    const last = lastCommands.get(sender);
    if (last) {
      await sendMessage(sender, '⏳ מבצע שוב...');
      runAutofit(sender, last.text, { ...last.opts, force: true });
    } else {
      await sendMessage(sender, 'אין פקודה קודמת לחזרה.');
    }
    return;
  }

  const _extraGramsM = /^(?:עוד|תוסיפ[יי]?\s+עוד)\s+(\d+)\s*גרם$/.exec(text.trim());
  if (_extraGramsM) {
    const last = lastCommands.get(sender);
    if (last) {
      const addGrams = parseInt(_extraGramsM[1]);
      const prevGramsM = last.text.match(/(\d+)\s*גרם/);
      let newText;
      if (prevGramsM) {
        // יש גרמים קודמים — חבר אותם
        const newGrams = parseInt(prevGramsM[1]) + addGrams;
        newText = last.text.replace(/\d+\s*גרם/, `${newGrams} גרם`);
      } else {
        // אין גרמים בפקודה הקודמת (למשל "תוסיפי ביצה לדני") — הוסף לפני שם המזון
        newText = last.text.replace(
          /^((?:תוסיפ[יי]?|הוסיפ[יי]?|הוסף)\s+)/,
          `$1${addGrams} גרם `
        );
        if (newText === last.text) {
          // fallback: הוסף בתחילה
          newText = `${addGrams} גרם ` + last.text;
        }
      }
      await sendMessage(sender, '⏳ מבצע...');
      runAutofit(sender, newText, { ...last.opts, force: true });
    } else {
      await sendMessage(sender, 'אין פקודה קודמת — שלח פקודה מלאה.');
    }
    return;
  }

  // ── "ממנו/מזה X גרם" — כינוי לאחרון מזון שהופעל ──────────────────────────
  const _pronounM = /^(?:(?:תוסיפ[יי]?|הוסיפ[יי]?|הוסף|תחסר[יי]?|תורידי?|הורד)\s+)?(?:ממנו|מזה|ממנה)\s+(\d+)\s*גרם$/.exec(text.trim());
  if (_pronounM) {
    const last = lastCommands.get(sender);
    if (last) {
      const foodM = last.text.match(/(\d+)\s*גרם\s+(.+?)(?:\s+ב(?:מקום|וקר|ערב|צהריים|ביניים|לילה)|$)/);
      const lastFood = foodM ? foodM[2].trim() : null;
      if (lastFood) {
        const pronGrams = _pronounM[1];
        // קבע פועל לפי הקשר: "תחסרי/תורידי" = reduce, אחרת = הוסף
        const isReduce = /(?:תחסר[יי]?|תורידי?|הורד)/.test(text.trim());
        const verb = isReduce ? 'תחסרי' : 'תוסיפי';
        const nameM = last.text.match(/ל([א-ת]{2,})/);
        const namePrefix = nameM ? ` ל${nameM[1]}` : '';
        const newText = `${verb}${namePrefix} ${pronGrams} גרם ${lastFood}`;
        await sendMessage(sender, '⏳ מבצע...');
        runAutofit(sender, newText, { ...last.opts, force: true });
        return;
      }
    }
    await sendMessage(sender, 'לא זוכר איזה מזון היה בפקודה הקודמת.');
    return;
  }

  // ── "לכולם" — פקודה לכל הלקוחות ──────────────────────────────────────────
  if (/\bלכולם\b/.test(text)) {
    const baseText = text.replace(/\s*לכולם\s*/g, ' ').trim();
    runForAllClients(sender, baseText);
    return;
  }

  // ── ריבוי לקוחות: "לרון ולדני 120 גרם חזה עוף" ─────────────────────────
  // ולידציה: כל שם (מה-2 ואילך) חייב להיות שם פרטי עצמאי — מונע "רון וליצקו" מלהיפצל
  const _multiNames = _extractMultiNames(text);
  if (_multiNames && _multiNames.length >= 2) {
    const secondaryNames = _multiNames.slice(1);
    const validChecks = await Promise.all(secondaryNames.map(n => quickFindUser(n)));
    const allValid = validChecks.every(Boolean);
    if (allValid) {
      const baseText = _stripMultiNamesPrefix(text, _multiNames);
      await sendMessage(sender, `⏳ מבצע עבור ${_multiNames.join(', ')}...`);
      for (const name of _multiNames) {
        runAutofit(sender, baseText, { nameOverride: name, ..._skipPref ? { skipFoodPref: true } : {} });
      }
      return;
    }
    // שם לא תקין = כנראה שם משפחה — המשך לעיבוד כאדם אחד
    console.log(`[multi-skip] "${secondaryNames.join(',')}" לא שמות עצמאיים, מעבד כאדם אחד`);
  }

  runAutofit(sender, text, _skipPref ? { skipFoodPref: true } : {});
});

// חילוץ ריבוי שמות מ-"לרון ולדני" / "לרון, לדני" / "לרון ודני"
function _extractMultiNames(text) {
  const NAME = '[א-ת]{2,}(?:\\s+[א-ת]{2,})?';
  const names = [];

  // "לNAME ולNAME [ולNAME...]"
  const m1 = text.match(new RegExp(`ל(${NAME})(?:\\s+ול(${NAME}))+`));
  if (m1) {
    // חלץ את כל ה-"ל+שם" ברצף
    const segment = m1[0];
    const parts = segment.match(new RegExp(`ל(${NAME})`, 'g')) || [];
    parts.forEach(p => names.push(p.replace(/^ל/, '').trim()));
    if (names.length >= 2) return names;
  }

  // "לNAME, לNAME" (פסיק)
  const m2 = text.match(new RegExp(`ל(${NAME}),\\s*ל(${NAME})`));
  if (m2) return [m2[1].trim(), m2[2].trim()];

  // "לNAME ודNAME" — ו' ישירה ללא ל' שני (פחות נפוץ)
  const m3 = text.match(new RegExp(`ל(${NAME})\\s+ו(${NAME})\\s+`));
  if (m3) return [m3[1].trim(), m3[2].trim()];

  return null;
}

// הסרת ריבוי שמות מהטקסט, השארת שאר הפקודה
function _stripMultiNamesPrefix(text, names) {
  let result = text;
  // הסר את כל "ל+שם" patterns
  result = result.replace(/ל[א-ת]{2,}(?:\s+[א-ת]{2,})?\s*(?:,\s*|(?:ו(?=ל)))/g, '');
  result = result.replace(/^(?:תוסיפ[יי]?|הוסיפ[יי]?|הוסף)\s+/, '');
  return result.trim() || text;
}

// ════════════════════════════════════════════════════════════════
// ══  BOT V3 — WhatsApp Business (הודעות יוצאות של דני)       ══
// ════════════════════════════════════════════════════════════════

const BIZ_ID    = process.env.GREEN_API_ID_BIZ;
const BIZ_TOKEN = process.env.GREEN_API_TOKEN_BIZ;
const BIZ_BASE  = BIZ_ID ? `https://api.green-api.com/waInstance${BIZ_ID}` : null;
const BIZ_GROUP = process.env.NOTIFY_GROUP_CHAT_ID; // chatId של קבוצת הצוות

// מילות מפתח שמפעילות את הבוט
const TRIGGER_WORDS = [
  'מוסיף לך', 'מוסיף לו', 'תוסיף לך', 'תוסיף לו', 'להוסיף',
  'מעלה לך', 'מעלה לו', 'תעלה לך', 'תעלה לו', 'להעלות',
  'מוריד לך', 'מוריד לו', 'תוריד לך', 'תוריד לו', 'הוריד לו', 'מפחית',
  'להוריד', 'להפחית',
  'מחליף לך', 'מחליף לו', 'תחליף לך', 'תחליף לו', 'להחליף',
];

function hasTriggerWord(text) {
  return TRIGGER_WORDS.some(w => text.includes(w));
}

// מניעת כפילויות v3
const bizProcessedIds = new Set();
// IDs שהבוט עצמו שלח (כדי לא להגיב להם)
const bizBotSentIds = new Set();

// namePrefs v3: שם WhatsApp (lowercase) → auto-fit user_id (אחרי אישור דני)
const bizNamePrefs = new Map();
const BIZ_PREFS_FILE = path.join(__dirname, 'biz_prefs.json');

function loadBizPrefs() {
  try {
    const data = JSON.parse(fs.readFileSync(BIZ_PREFS_FILE, 'utf8'));
    (data.names || []).forEach(([k, v]) => bizNamePrefs.set(k, v));
    console.log(`[biz-prefs loaded] names=${bizNamePrefs.size}`);
  } catch (e) {
    if (e.code !== 'ENOENT') console.warn('[biz-prefs load error]', e.message);
  }
}

function saveBizPrefs() {
  try {
    fs.writeFileSync(BIZ_PREFS_FILE,
      JSON.stringify({ names: [...bizNamePrefs.entries()] }, null, 2), 'utf8');
  } catch (e) {
    console.warn('[biz-prefs save error]', e.message);
  }
}

loadBizPrefs();

// state ממתין v3 (אחד בזמן — דני הוא המשתמש היחיד)
let bizPending = null; // { type, contactName, clientPhone, commandText, alternatives, ... }
const BIZ_PENDING_TTL = 10 * 60 * 1000; // 10 דקות

// ─── שליחה דרך המספר העסקי ───────────────────────────────────
async function sendBizMessage(chatId, text) {
  const id = chatId.includes('@') ? chatId : `${chatId}@c.us`;
  // שלח דרך האינסטנס הרגיל (BIZ instance מחזיר idMessage גם כשלא מחובר)
  await sendMessage(id, text);
}

// ─── שם איש קשר מ-Green API (השם שדני שמר בטלפון) ──────────────
async function getContactName(chatId) {
  if (!BIZ_BASE) return chatId.replace('@c.us', '');
  try {
    const res = await fetch(`${BIZ_BASE}/getContact/${BIZ_TOKEN}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chatId }),
    });
    const data = await res.json();
    return (data.name || data.pushName || '').trim() || chatId.replace('@c.us', '');
  } catch (e) {
    return chatId.replace('@c.us', '');
  }
}

// ─── פורמט קבלה לקבוצה ───────────────────────────────────────
function formatBizReceipt(contactName, autofitResult) {
  const time = new Date().toLocaleTimeString('he-IL', {
    timeZone: 'Asia/Jerusalem', hour: '2-digit', minute: '2-digit'
  });
  return `👤 *${contactName}* | 🕐 ${time}\n${autofitResult}`;
}

// ─── הרצת autofit עבור v3 ─────────────────────────────────────
function runAutofitBiz(contactName, clientPhone, commandText, opts = {}) {
  const script = path.join(__dirname, 'autofit_api.py');
  const args = ['--force'];

  // אם יש user_id ידוע מ-cache → שלח דרכו (מונע חיפוש שם)
  if (opts.userIdOverride) {
    args.push('--user-id'); args.push(String(opts.userIdOverride));
  } else {
    const nameArg = opts.nameOverride || contactName;
    args.push('--name'); args.push(nameArg);
    // כשה-nameOverride הוא מספר טלפון (retry לגיטימי) → הוסף --allow-phone
    if (nameArg === clientPhone) args.push('--allow-phone');
  }
  if (opts.foodOverride) { args.push('--food'); args.push(opts.foodOverride); }
  if (opts.hintOverride) { args.push('--hint'); args.push(opts.hintOverride); }
  if (opts.mealOverride) { args.push('--meal'); args.push(opts.mealOverride); }
  // הודעות V3 מתחילות ב-"אני" — מסיר כדי שלא יבלבל את הפרסר
  args.push(commandText.replace(/^אני\s+/, ''));

  const proc = spawn('python3', [script, ...args]);
  let output = '';
  let timedOut = false;

  const killTimer = setTimeout(() => {
    timedOut = true;
    proc.kill();
    if (BIZ_GROUP) sendBizMessage(BIZ_GROUP, `❌ *${contactName}* — הבקשה לקחה יותר מדי זמן`);
  }, 30000);

  proc.stdout.on('data', d => { output += d.toString(); });
  proc.stderr.on('data', d => console.error('[biz-py err]', d.toString().trim()));

  proc.on('close', async code => {
    clearTimeout(killTimer);
    if (timedOut || !BIZ_GROUP) return;
    const raw = output.trim() || (code === 0 ? '✅ בוצע' : '❌ שגיאה לא ידועה');
    console.log(`[biz→] ${raw.slice(0,80).replace(/\n/g,' | ')}`);

    // ── שם לא מדויק: ישר לטלפון, לא שואלים אישור ──────────────────
    if (raw.startsWith('CONFIRM_WITH_NAME:')) {
      if (opts.nameOverride === clientPhone) {
        // טלפון הביא fuzzy (נדיר) — קח את השם המתוקן ובצע
        const correctedName = raw.slice('CONFIRM_WITH_NAME:'.length, raw.indexOf('|||')).trim();
        console.log(`[biz] fuzzy by phone, executing: ${correctedName}`);
        runAutofitBiz(correctedName, clientPhone, commandText, { ...opts, nameOverride: correctedName });
      } else {
        // שם לא מדויק → ישר לטלפון
        console.log(`[biz] שם לא מדויק → ישר לטלפון: ${clientPhone}`);
        runAutofitBiz(contactName, clientPhone, commandText, { ...opts, nameOverride: clientPhone });
      }
      return;
    }

    // ── שם לא נמצא: נסה לפי מספר טלפון, אחר כך alert ────────────────
    if (raw.startsWith('NAME_NOT_FOUND:')) {
      if (opts.nameOverride === clientPhone) {
        // כבר ניסינו טלפון — שלח alert
        await sendBizMessage(BIZ_GROUP, `❌ לא מצאתי לקוח: *${contactName}*\n(טלפון: ${clientPhone.replace(/^972/, '0')})`);
      } else {
        const badName = raw.slice('NAME_NOT_FOUND:'.length).trim();
        console.log(`[biz] שם לא נמצא: "${badName}", מנסה לפי טלפון: ${clientPhone}`);
        runAutofitBiz(contactName, clientPhone, commandText, { ...opts, nameOverride: clientPhone });
      }
      return;
    }

    // ── שמות כפולים: ישר לטלפון לזיהוי חד-משמעי ──────────────────────
    if (raw.startsWith('NAME_OPTIONS:')) {
      if (opts.nameOverride === clientPhone) {
        // גם טלפון הביא כפילות — מצב נדיר, הצג אפשרויות לבחירה ידנית
        const [header, ...rest] = raw.split('\n');
        const headerBody = header.slice('NAME_OPTIONS:'.length);
        const sepIdx = headerBody.indexOf('||');
        const ids = (sepIdx >= 0 ? headerBody.slice(0, sepIdx) : headerBody).split('|');
        const names = (sepIdx >= 0 ? headerBody.slice(sepIdx + 2) : '').split('|');
        const listMsg = names.map((n, i) => `${i+1}. ${n}`).join('\n');
        bizPending = { type: 'name_options', contactName, clientPhone, commandText, alternatives: names, userIds: ids, opts, timestamp: Date.now() };
        await sendBizMessage(BIZ_GROUP, `👥 *${contactName}* — נמצאו ${names.length} לקוחות:\n${listMsg}\n\n_השב_ *בחר N*`);
      } else {
        // שם כפול → ישר לטלפון לזיהוי חד-משמעי
        console.log(`[biz] שמות כפולים → ישר לטלפון: ${clientPhone}`);
        runAutofitBiz(contactName, clientPhone, commandText, { ...opts, nameOverride: clientPhone });
      }
      return;
    }

    // ── אפשרויות מזון: שלח לקבוצה לבחירה ──────────────────────────
    if (raw.startsWith('FOOD_OPTIONS:')) {
      const [header, ...rest] = raw.split('\n');
      const headerBody = header.slice('FOOD_OPTIONS:'.length);
      const sepIdx = headerBody.indexOf('||');
      const foodQuery = sepIdx >= 0 ? headerBody.slice(0, sepIdx) : '';
      const alts = (sepIdx >= 0 ? headerBody.slice(sepIdx + 2) : headerBody).split('|');
      const listMsg = alts.map((a, i) => `${i+1}. ${a}`).join('\n');
      bizPending = { type: 'food_choice', contactName, clientPhone, commandText, alternatives: alts, foodQuery, opts, timestamp: Date.now() };
      await sendBizMessage(BIZ_GROUP, `❓ *${contactName}* — לא מצאתי "${foodQuery}"\n${listMsg}\n\n_השב_ *בחר N*`);
      return;
    }

    // ── אפשרויות hint (מזון להחלפה) ─────────────────────────────────
    if (raw.startsWith('HINT_OPTIONS:')) {
      const [header, ...rest] = raw.split('\n');
      const headerBody = header.slice('HINT_OPTIONS:'.length);
      const sepIdx = headerBody.indexOf('||');
      const hintQuery = sepIdx >= 0 ? headerBody.slice(0, sepIdx) : '';
      const alts = (sepIdx >= 0 ? headerBody.slice(sepIdx + 2) : headerBody).split('|');
      const listMsg = alts.map((a, i) => `${i+1}. ${a}`).join('\n');
      bizPending = { type: 'hint_choice', contactName, clientPhone, commandText, alternatives: alts, hintQuery, opts, timestamp: Date.now() };
      await sendBizMessage(BIZ_GROUP, `❓ *${contactName}* — לא מצאתי "${hintQuery}"\n${listMsg}\n\n_השב_ *בחר N*`);
      return;
    }

    // ── ארוחה לא ברורה ───────────────────────────────────────────────
    if (raw.startsWith('MEAL_OPTIONS:')) {
      const [header, ...rest] = raw.split('\n');
      const alts = header.slice('MEAL_OPTIONS:'.length).split('|').slice(1);
      const userMsg = rest.join('\n');
      bizPending = { type: 'meal_choice', contactName, clientPhone, commandText, alternatives: alts, opts, timestamp: Date.now() };
      await sendBizMessage(BIZ_GROUP, `⚠️ *${contactName}*\n${userMsg}\n\n_השב_ *בחר N*`);
      return;
    }

    // ── הצלחה או שגיאה ────────────────────────────────────────────────
    bizPending = null;
    // אם contactName הוא מספר טלפון — נסה לחלץ שם לקוח מתשובת אוטופיט
    let displayName = contactName;
    if (/^\d{7,}$/.test(contactName)) {
      const nm = raw.match(/של ([\u05D0-\u05EA]+(?:\s+[\u05D0-\u05EA]+)*)/);
      if (nm) displayName = nm[1].trim();
    }
    const msg = code === 0 ? formatBizReceipt(displayName, raw) : `❌ *${displayName}*\n${raw}`;
    await sendBizMessage(BIZ_GROUP, msg);
  });
}

// ─── טיפול בתגובת דני בקבוצה ("כן" / "בחר N") ─────────────────
async function handleBizGroupResponse(text) {
  if (!bizPending) return;
  if (Date.now() - bizPending.timestamp > BIZ_PENDING_TTL) {
    bizPending = null;
    return;
  }

  const t = text.trim();
  const { type, contactName, correctedName, rawName, clientPhone, commandText, alternatives, userIds, opts } = bizPending;

  // ── בחירת שם מרשימה (NAME_OPTIONS) ─────────────────────────────────
  if (type === 'name_options') {
    const numMatch = t.match(/^(?:בחר\s+)?(\d+)$/);
    if (!numMatch) return;
    const idx = parseInt(numMatch[1]) - 1;
    if (idx < 0 || idx >= alternatives.length) return;
    const chosenName = alternatives[idx];
    bizPending = null;
    runAutofitBiz(chosenName, clientPhone, commandText, { ...opts, nameOverride: chosenName });
    return;
  }

  // ── אישור שם fuzzy ─────────────────────────────────────────────────
  if (type === 'name_confirm') {
    if (t === 'כן' || t === 'yes') {
      bizNamePrefs.set((rawName || contactName).toLowerCase(), correctedName);
      saveBizPrefs();
      console.log(`[biz-name saved] "${rawName}" → "${correctedName}"`);
      bizPending = null;
      runAutofitBiz(correctedName, clientPhone, commandText, { ...opts, nameOverride: correctedName });
    } else if (t === 'לא' || t === 'no') {
      bizPending = null;
      await sendBizMessage(BIZ_GROUP, `❌ פעולה בוטלה`);
    }
    return;
  }

  // ── בחירת מזון / hint / ארוחה ──────────────────────────────────────
  if (['food_choice', 'hint_choice', 'meal_choice'].includes(type)) {
    // "בחר N" או "N" בלבד
    const numMatch = t.match(/^(?:בחר\s+)?(\d+)$/);
    let chosen = null;

    if (numMatch) {
      const idx = parseInt(numMatch[1]) - 1;
      if (idx >= 0 && idx < alternatives.length) chosen = alternatives[idx];
    } else if (t.length >= 2 && !t.includes('\n')) {
      // טקסט חופשי — אפשר גם לכתוב שם מזון
      chosen = t;
    }

    if (!chosen) return;

    bizPending = null;

    if (type === 'food_choice') {
      runAutofitBiz(contactName, clientPhone, commandText, { ...opts, foodOverride: chosen });
    } else if (type === 'hint_choice') {
      runAutofitBiz(contactName, clientPhone, commandText, { ...opts, hintOverride: chosen });
    } else {
      runAutofitBiz(contactName, clientPhone, commandText, { ...opts, mealOverride: chosen });
    }
  }
}

// ─── V3 — עיבוד הודעה מ-BIZ instance ─────────────────────────
const bizDebugLog = [];
const v3Log = [];

async function processBizBody(body) {
  if (!BIZ_ID || !BIZ_GROUP) return;

  const msgId = body.idMessage;
  if (msgId && bizBotSentIds.has(msgId)) return;
  if (msgId) {
    if (bizProcessedIds.has(msgId)) return;
    bizProcessedIds.add(msgId);
    if (bizProcessedIds.size > 500) {
      const arr = [...bizProcessedIds];
      arr.slice(0, 250).forEach(id => bizProcessedIds.delete(id));
    }
  }

  const msg = body.messageData;
  if (!msg || msg.typeMessage !== 'textMessage') return;
  const text = msg.textMessageData?.textMessage?.trim();
  if (!text) return;

  const chatId = body.senderData?.chatId || '';
  console.log(`[biz] type=${body.typeWebhook} chatId=${chatId} text="${text.slice(0,40)}"`);

  // תגובות דני בקבוצה
  if (chatId === BIZ_GROUP || chatId.replace('@g.us','') === (BIZ_GROUP || '').replace('@g.us','')) {
    if (body.typeWebhook === 'outgoingMessageReceived') {
      await handleBizGroupResponse(text);
    }
    return;
  }

  // שיחות 1-on-1 — רק הודעות יוצאות
  if (body.typeWebhook !== 'outgoingMessageReceived') return;
  if (chatId.includes('@g.us')) return;

  const BIZ_TEST_PHONE = process.env.BIZ_TEST_PHONE;
  if (BIZ_TEST_PHONE) {
    const allowed = BIZ_TEST_PHONE.split(',').map(p => p.trim()).filter(Boolean);
    const phone = chatId.replace('@c.us', '').replace(/^972/, '0');
    const phoneAlt = chatId.replace('@c.us', '');
    const isAllowed = allowed.some(a => phone === a || phoneAlt === a || phoneAlt === '972'+a.replace(/^0/,''));
    if (!isAllowed) return;
  }

  if (!hasTriggerWord(text)) return;
  console.log(`[biz] פקודה: chatId=${chatId} | "${text.slice(0,60)}"`);

  const contactName = await getContactName(chatId);
  const clientPhone = chatId.replace('@c.us', '');
  console.log(`[biz] לקוח: "${contactName}" (${clientPhone})`);

  const cachedId = bizNamePrefs.get(contactName.toLowerCase());
  const extraOpts = cachedId ? { userIdOverride: cachedId } : {};
  runAutofitBiz(contactName, clientPhone, text, extraOpts);
}

// ─── BIZ Polling loop — lastOutgoingMessages ──────────────────
async function bizPollLoop() {
  const startTs = Math.floor(Date.now() / 1000);
  let lastTs = startTs;
  console.log('[biz-poll] starting, watermark=', new Date(startTs * 1000).toISOString());
  while (true) {
    await new Promise(r => setTimeout(r, 10000)); // כל 10 שניות
    try {
      const resp = await fetch(`${BIZ_BASE}/lastOutgoingMessages/${BIZ_TOKEN}`);
      if (!resp.ok) continue;
      const msgs = await resp.json();
      if (!Array.isArray(msgs)) continue;
      let maxTs = lastTs;
      for (const msg of msgs) {
        if (!msg.timestamp || msg.timestamp <= lastTs) continue;
        maxTs = Math.max(maxTs, msg.timestamp);
        const text = msg.textMessage || msg.extendedTextMessage?.text || '';
        if (!text) continue;
        bizDebugLog.push({ ts: Date.now(), body: msg });
        if (bizDebugLog.length > 30) bizDebugLog.shift();
        await processBizBody({
          typeWebhook: 'outgoingMessageReceived',
          idMessage: msg.idMessage,
          senderData: { chatId: msg.chatId },
          messageData: { typeMessage: 'textMessage', textMessageData: { textMessage: text } }
        });
      }
      lastTs = maxTs;
    } catch (e) {
      console.error('[biz-poll error]', e.message);
    }
  }
}

// endpoint לdebug בלבד (לא מקבל webhooks יותר)
app.post('/webhook-business', (req, res) => res.sendStatus(200));

// ─── Debug V3 log ─────────────────────────────────────────────
app.get('/debug-v3', (_, res) => {
  res.json({ count: v3Log.length, log: v3Log.slice(-20).reverse() });
});

// ─── Debug env ────────────────────────────────────────────────
app.get('/debug-env', (_, res) => {
  res.json({
    BIZ_GROUP: process.env.NOTIFY_GROUP_CHAT_ID || 'NOT SET',
    BIZ_TEST_PHONE: process.env.BIZ_TEST_PHONE || 'NOT SET',
    BIZ_ID: process.env.GREEN_API_ID_BIZ || 'NOT SET',
    REG_ID: process.env.GREEN_API_ID || 'SET',
  });
});

// ─── Debug BIZ webhooks ────────────────────────────────────────
app.get('/debug-biz', (_, res) => {
  const entries = bizDebugLog.slice(-10).reverse().map(e => ({
    ago: Math.round((Date.now()-e.ts)/1000)+'s ago',
    typeWebhook: e.body.typeWebhook,
    chatId: e.body.senderData?.chatId,
    text: e.body.messageData?.textMessageData?.textMessage?.slice(0,60)
  }));
  res.json({ count: bizDebugLog.length, last10: entries });
});

// ─── Health check ─────────────────────────────────────────────
app.get('/', (_, res) => res.send('✅ v2 autofit bot running'));
app.get('/test', (req, res) => { const {spawn}=require('child_process'); const p=spawn('python3',[require('path').join(__dirname,'autofit_api.py'),'שם: רון וליצקו\nארוחה: ערב\nהוספה: טונה ל חזה עוף מבושל']); let o=''; p.stdout.on('data',d=>{o+=d}); p.on('close',()=>res.send(o)); });

// ─── הפעל שרת + הגדר webhook ──────────────────────────────────
const PORT = process.env.PORT || 3000;
app.listen(PORT, async () => {
  console.log(`✅ Server on port ${PORT}`);

  // ── Webhook v1/v2 (חשבון אישי) ──────────────────────────────────
  const webhookUrl = process.env.WEBHOOK_URL;
  if (webhookUrl) {
    try {
      await fetch(`${BASE}/setSettings/${TOKEN}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ webhookUrl, webhookUrlToken: '' }),
      });
      console.log('✅ Webhook v1 set:', webhookUrl);
    } catch (e) {
      console.error('Webhook v1 setup error:', e.message);
    }
  } else {
    console.log('⚠️  WEBHOOK_URL not set — set it in Railway env vars');
  }

  // ── BIZ Polling v3 ───────────────────────────────────────────────
  if (BIZ_BASE && BIZ_TOKEN) {
    console.log('✅ BIZ polling started (instance', BIZ_ID, ')');
    bizPollLoop(); // לולאה אסינכרונית — לא חוסמת
  }
});
