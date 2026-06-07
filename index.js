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
  const res = await fetch(`${BASE}/sendMessage/${TOKEN}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
  });
  return res.ok;
}

// תיקונים ממתינים: phone → { type, originalText, alternatives, timestamp }
const pendingCorrections = new Map();

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
      pendingCorrections.set(phone, { type: 'food', originalText: text, alternatives: alts, foodQuery, nameOverride: opts.nameOverride || '', hintOverride: opts.hintOverride || '', timestamp: Date.now() });
      await sendMessage(phone, userMsg);
      return;
    }

    if (!raw.startsWith('CONFIRM')) {
      pendingCorrections.delete(phone);
    }

    await sendMessage(phone, code === 0 ? raw : `❌ ${raw}`);
  });
}

// ─── הצגת תפריט מתאמן ────────────────────────────────────────
function runMenu(phone, name) {
  const script = path.join(__dirname, 'autofit_api.py');
  const proc = spawn('python3', [script, '--menu', name]);
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
      const ph = outChatId.replace('@c.us','').replace(/^972/,'0');
      const phAlt = outChatId.replace('@c.us','');
      if (ph !== BIZ_TEST_PHONE && phAlt !== BIZ_TEST_PHONE && phAlt !== '972'+BIZ_TEST_PHONE.replace(/^0/,'')) return;
    }
    if (!hasTriggerWord(outText)) return;
    const outName = body.senderData?.chatName || outChatId.replace('@c.us','');
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
  // "דני תפריט" / "תפריט דני" — הצג תפריט מלא
  const menuM = /^([א-׺][א-׺\s'״׳]{1,30})\s+תפריט$/.exec(text.trim()) ||
                /^תפריט\s+([א-׺][א-׺\s'״׳]{1,30})$/.exec(text.trim());
  if (menuM) {
    const menuName = menuM[1].trim();
    console.log(`📋 תפריט עבור: "${menuName}"`);
    runMenu(sender, menuName);
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
      } else {
        const numMatch = text.trim().match(/^(\d+)$/);
        let chosen = null;
        if (numMatch) {
          const idx = parseInt(numMatch[1]) - 1;
          if (idx >= 0 && idx < corr.alternatives.length) chosen = corr.alternatives[idx];
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
  runAutofit(sender, text, _skipPref ? { skipFoodPref: true } : {});
});

// ════════════════════════════════════════════════════════════════
// ══  BOT V3 — WhatsApp Business (הודעות יוצאות של דני)       ══
// ════════════════════════════════════════════════════════════════

const BIZ_ID    = process.env.GREEN_API_ID_BIZ;
const BIZ_TOKEN = process.env.GREEN_API_TOKEN_BIZ;
const BIZ_BASE  = BIZ_ID ? `https://api.green-api.com/waInstance${BIZ_ID}` : null;
const BIZ_GROUP = process.env.NOTIFY_GROUP_CHAT_ID; // chatId של קבוצת הצוות

// מילות מפתח שמפעילות את הבוט
const TRIGGER_WORDS = [
  'מוסיף לך', 'מוסיף לו',
  'מעלה לך', 'מעלה לו',
  'מוריד לך', 'מפחית',
  'מחליף לך', 'מחליף לו',
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
    args.push('--name'); args.push(opts.nameOverride || contactName);
  }
  if (opts.foodOverride) { args.push('--food'); args.push(opts.foodOverride); }
  if (opts.hintOverride) { args.push('--hint'); args.push(opts.hintOverride); }
  if (opts.mealOverride) { args.push('--meal'); args.push(opts.mealOverride); }
  args.push(commandText);

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

    // ── שם fuzzy: מצא שם מתוקן, בקש אישור ─────────────────────────
    if (raw.startsWith('CONFIRM_WITH_NAME:')) {
      const rest = raw.slice('CONFIRM_WITH_NAME:'.length);
      const sepIdx = rest.indexOf('|||');
      const correctedName = rest.slice(0, sepIdx).trim();
      const summary = rest.slice(sepIdx + 3);
      const rawMatch = summary.match(/מצאתי עבור "([^"]+)"/);
      const rawName = rawMatch ? rawMatch[1] : contactName;

      // יש בcache? בצע ישירות
      const cachedId = bizNamePrefs.get((rawName || contactName).toLowerCase());
      if (cachedId) {
        runAutofitBiz(correctedName, clientPhone, commandText, { ...opts, userIdOverride: cachedId });
        return;
      }

      bizPending = { type: 'name_confirm', contactName, correctedName, rawName, clientPhone, commandText, opts, timestamp: Date.now() };
      await sendBizMessage(BIZ_GROUP, `🔍 *שם לא ברור*\nהבנתי: *${correctedName}*\n(שם בוואטסאפ: "${rawName}")\n\nנכון? השב *כן* לאישור`);
      return;
    }

    // ── שם לא נמצא: נסה לפי מספר טלפון, אחר כך alert ────────────────
    if (raw.startsWith('NAME_NOT_FOUND:')) {
      if (opts.nameOverride === clientPhone) {
        // כבר ניסינו טלפון — שלח alert
        await sendBizMessage(BIZ_GROUP, `❌ לא מצאתי לקוח: *${contactName}*\n(טלפון: ${clientPhone})`);
      } else {
        const badName = raw.slice('NAME_NOT_FOUND:'.length).trim();
        console.log(`[biz] שם לא נמצא: "${badName}", מנסה לפי טלפון: ${clientPhone}`);
        runAutofitBiz(contactName, clientPhone, commandText, { ...opts, nameOverride: clientPhone });
      }
      return;
    }

    // ── שני לקוחות באותו שם: בקש בחירה ──────────────────────────────
    if (raw.startsWith('NAME_OPTIONS:')) {
      const [header, ...rest] = raw.split('\n');
      const headerBody = header.slice('NAME_OPTIONS:'.length);
      const sepIdx = headerBody.indexOf('||');
      const ids = (sepIdx >= 0 ? headerBody.slice(0, sepIdx) : headerBody).split('|');
      const names = (sepIdx >= 0 ? headerBody.slice(sepIdx + 2) : '').split('|');
      const listMsg = names.map((n, i) => `${i+1}. ${n}`).join('\n');
      bizPending = { type: 'name_options', contactName, clientPhone, commandText, alternatives: names, userIds: ids, opts, timestamp: Date.now() };
      await sendBizMessage(BIZ_GROUP, `👥 *${contactName}* — נמצאו ${names.length} לקוחות:\n${listMsg}\n\n_השב_ *בחר N*`);
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
    const msg = code === 0 ? formatBizReceipt(contactName, raw) : `❌ *${contactName}*\n${raw}`;
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

// ─── Webhook v3 — הודעות יוצאות מהחשבון העסקי ─────────────────
const bizDebugLog = []; // מאגר webhooks אחרונים לdebug
const v3Log = []; // לוג V3
app.post('/webhook-business', async (req, res) => {
  res.sendStatus(200);
  const body = req.body;
  bizDebugLog.push({ ts: Date.now(), body });
  if (bizDebugLog.length > 30) bizDebugLog.shift();
  console.log('[biz-raw] typeWebhook=' + (body.typeWebhook||'?') + ' chatId=' + (body.senderData?.chatId||'?'));
  if (!BIZ_ID || !BIZ_GROUP) return; // לא מוגדר → עצור

  // כל הודעה שהבוט עצמו שלח — התעלם (מניעת לולאה)
  const msgId = body.idMessage;
  if (msgId && bizBotSentIds.has(msgId)) return;

  // dedup
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
  console.log(`[biz-wh] type=${body.typeWebhook} chatId=${chatId} text="${text.slice(0,40)}"`);

  // ── תגובות דני בקבוצה ──────────────────────────────────────────────
  if (chatId === BIZ_GROUP || chatId.replace('@g.us','') === (BIZ_GROUP || '').replace('@g.us','')) {
    // הודעות יוצאות מהחשבון בקבוצה = תגובות דני
    if (body.typeWebhook === 'outgoingMessageReceived') {
      await handleBizGroupResponse(text);
    }
    return;
  }

  // ── שיחות 1-on-1 עם לקוחות ─────────────────────────────────────────
  // קולט רק הודעות יוצאות (דני שולח ללקוח)
  if (body.typeWebhook !== 'outgoingMessageReceived') return;
  if (chatId.includes('@g.us')) return; // דלג על קבוצות

  // מצב ניסוי: עבד רק מסרים לטלפון הזה
  const BIZ_TEST_PHONE = process.env.BIZ_TEST_PHONE; // ריק = כולם
  if (BIZ_TEST_PHONE) {
    const phone = chatId.replace('@c.us', '').replace(/^972/, '0');
    const phoneAlt = chatId.replace('@c.us', '');
    if (phone !== BIZ_TEST_PHONE && phoneAlt !== BIZ_TEST_PHONE && phoneAlt !== '972' + BIZ_TEST_PHONE.replace(/^0/, '')) return;
  }

  // בדוק מילות מפתח
  if (!hasTriggerWord(text)) return;

  console.log(`[biz] פקודה: chatId=${chatId} | "${text.slice(0,60)}"`);

  // שלוף שם איש הקשר
  const contactName = await getContactName(chatId);
  const clientPhone = chatId.replace('@c.us', '');
  console.log(`[biz] לקוח: "${contactName}" (${clientPhone})`);

  // בדוק cache שמות
  const cachedId = bizNamePrefs.get(contactName.toLowerCase());
  const extraOpts = cachedId ? { userIdOverride: cachedId } : {};

  runAutofitBiz(contactName, clientPhone, text, extraOpts);
});

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

  // ── Webhook v3 (חשבון עסקי) ──────────────────────────────────────
  const bizWebhookUrl = process.env.WEBHOOK_URL_BIZ;
  if (BIZ_BASE && BIZ_TOKEN && bizWebhookUrl) {
    try {
      await fetch(`${BIZ_BASE}/setSettings/${BIZ_TOKEN}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          webhookUrl: bizWebhookUrl,
          outgoingWebhook: 'yes',        // קבלת הודעות יוצאות
          outgoingMessageWebhook: 'yes', // גיבוי
          incomingWebhook: 'yes',        // קבלת הודעות נכנסות (לתגובות בקבוצה)
        }),
      });
      console.log('✅ Webhook v3 (biz) set:', bizWebhookUrl);
    } catch (e) {
      console.error('Webhook v3 setup error:', e.message);
    }
  } else if (BIZ_ID) {
    console.log('⚠️  BIZ instance מוגדר אבל WEBHOOK_URL_BIZ חסר');
  }
});
