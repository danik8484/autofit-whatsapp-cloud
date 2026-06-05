const { spawn } = require('child_process');
const express = require('express');
const path = require('path');

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

// זיכרון בחירת מזון: query → chosen food name (נשמר בזיכרון עד restart)
const foodPrefs = new Map();
// זיכרון בחירת hint: hintQuery → chosen food row name
const hintPrefs = new Map();
// זיכרון שמות: rawName → correctedName (למניעת CONFIRM חוזר)
const namePrefs = new Map();

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
  if (opts.force)        args.push('--force');
  if (opts.nameOverride) { args.push('--name'); args.push(opts.nameOverride); }
  if (opts.mealOverride) { args.push('--meal'); args.push(opts.mealOverride); }
  if (opts.foodOverride) { args.push('--food'); args.push(opts.foodOverride); }
  if (opts.hintOverride) { args.push('--hint'); args.push(opts.hintOverride); }
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

      // יש העדפה שמורה? בחר אוטומטית
      const pref = hintQuery && hintPrefs.get(hintQuery.toLowerCase());
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

      // יש העדפה שמורה? בחר אוטומטית
      const pref = foodQuery && foodPrefs.get(foodQuery.toLowerCase());
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
  if (body.typeWebhook !== 'incomingMessageReceived') return;

  const msg = body.messageData;
  if (!msg || msg.typeMessage !== 'textMessage') return;

  // התעלם מ-webhooks שנשלחו לפני שהשרת עלה (retries אחרי restart)
  const msgTimestamp = body.timestamp;
  if (msgTimestamp && msgTimestamp < SERVER_START - 10) {
    console.log(`[skip] webhook לפני start (${Math.round(SERVER_START - msgTimestamp)}s ago)`);
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
    } else if (corr.type === 'food' || corr.type === 'hint') {
      // פקודה חדשה מובנית — נקה state ועבד מחדש
      if (/שם\s*:|ארוחה\s*:/i.test(text)) {
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
            }
            // זכור גם foodOverride שנבחר קודם
            runAutofit(sender, corr.originalText, { force: true, hintOverride: chosen, nameOverride: corr.nameOverride || '', foodOverride: corr.foodOverride || '' });
          } else {
            // שמור העדפה לפעם הבאה
            if (corr.foodQuery) {
              foodPrefs.set(corr.foodQuery.toLowerCase(), chosen);
              console.log(`[pref saved] "${corr.foodQuery}" → "${chosen}"`);
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

  runAutofit(sender, text);
});

// ─── Health check ─────────────────────────────────────────────
app.get('/', (_, res) => res.send('✅ v2 autofit bot running'));
app.get('/test', (req, res) => { const {spawn}=require('child_process'); const p=spawn('python3',[require('path').join(__dirname,'autofit_api.py'),'שם: רון וליצקו\nארוחה: ערב\nהוספה: טונה ל חזה עוף מבושל']); let o=''; p.stdout.on('data',d=>{o+=d}); p.on('close',()=>res.send(o)); });

// ─── הפעל שרת + הגדר webhook ──────────────────────────────────
const PORT = process.env.PORT || 3000;
app.listen(PORT, async () => {
  console.log(`✅ Server on port ${PORT}`);

  const webhookUrl = process.env.WEBHOOK_URL;
  if (webhookUrl) {
    try {
      await fetch(`${BASE}/setSettings/${TOKEN}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ webhookUrl, webhookUrlToken: '' }),
      });
      console.log('✅ Webhook set:', webhookUrl);
    } catch (e) {
      console.error('Webhook setup error:', e.message);
    }
  } else {
    console.log('⚠️  WEBHOOK_URL not set — set it in Railway env vars');
  }
});
