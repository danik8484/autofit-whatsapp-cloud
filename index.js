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

const app = express();
app.use(express.json());

// מניעת עיבוד כפול של אותה הודעה
const processedIds = new Set();

// המתנה לאישור: phone → { originalText, correctedName, type, timestamp }
// type: 'confirm' (confidence) | 'fuzzy' (שם עמום)
const pendingConfirmations = new Map();

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

  proc.stdout.on('data', d => { output += d.toString(); });
  proc.stderr.on('data', d => console.error('[autofit err]', d.toString().trim()));

  proc.on('close', async code => {
    const raw = output.trim() || (code === 0 ? 'בוצע!' : 'משהו השתבש');

    // confidence נמוך — שאל אישור
    if (raw.startsWith('CONFIRM:')) {
      const summary = raw.slice('CONFIRM:'.length).trim();
      pendingConfirmations.set(phone, { originalText: text, type: 'confirm', timestamp: Date.now() });
      await sendMessage(phone, `❓ הבנתי:\n${summary}\n\nנכון? שלח *כן* לאישור או *לא* לביטול`);
      return;
    }

    // שם עמום — שאל אישור
    if (raw.startsWith('CONFIRM_FUZZY:')) {
      const foundName = raw.slice('CONFIRM_FUZZY:'.length).trim();
      pendingConfirmations.set(phone, { originalText: text, correctedName: foundName, type: 'fuzzy', timestamp: Date.now() });
      await sendMessage(phone, `❓ האם התכוונת ל: *${foundName}*?\n\nשלח *כן* לאישור או *לא* לביטול`);
      return;
    }

    const msg = code === 0 ? raw : `❌ ${raw}`;

    // MEAL_OPTIONS — ארוחה לא נמצאה, רשימה ממוספרת
    if (raw.startsWith('MEAL_OPTIONS:')) {
      const [header, ...rest] = raw.split('\n');
      const alts = header.slice('MEAL_OPTIONS:'.length).split('|').slice(1);
      const userMsg = rest.join('\n');
      pendingCorrections.set(phone, { type: 'meal', originalText: text, alternatives: alts, timestamp: Date.now() });
      await sendMessage(phone, userMsg);
      return;
    }

    // HINT_OPTIONS — מזון להחלפה לא נמצא, רשימה ממוספרת
    if (raw.startsWith('HINT_OPTIONS:')) {
      const [header, ...rest] = raw.split('\n');
      const alts = header.slice('HINT_OPTIONS:'.length).split('|');
      const userMsg = rest.join('\n');
      pendingCorrections.set(phone, { type: 'hint', originalText: text, alternatives: alts, timestamp: Date.now() });
      await sendMessage(phone, userMsg);
      return;
    }

    // שמור state לתיקון אפשרויות מזון חדש
    if (raw.startsWith('❓ מספר אפשרויות')) {
      const alternatives = raw.split('\n')
        .filter(l => /^\d+\./.test(l))
        .map(l => l.replace(/^\d+\.\s*/, '').trim());
      pendingCorrections.set(phone, { type: 'food', originalText: text, alternatives, timestamp: Date.now() });
    } else if (!raw.startsWith('CONFIRM')) {
      pendingCorrections.delete(phone);
    }

    await sendMessage(phone, msg);
  });
}

// ─── Webhook מ-Green API ──────────────────────────────────────
app.post('/webhook', async (req, res) => {
  res.sendStatus(200);

  const body = req.body;
  if (body.typeWebhook !== 'incomingMessageReceived') return;

  const msg = body.messageData;
  if (!msg || msg.typeMessage !== 'textMessage') return;

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

  console.log(`📨 מ: ${sender} | "${text}"`);

  // בדוק אם יש תיקון ממתין (ארוחה / בחירת מזון)
  if (pendingCorrections.has(sender)) {
    const corr = pendingCorrections.get(sender);
    if (Date.now() - corr.timestamp > 5 * 60 * 1000) {
      pendingCorrections.delete(sender);
    } else if (corr.type === 'meal') {
      pendingCorrections.delete(sender);
      runAutofit(sender, corr.originalText, { force: true, mealOverride: text.trim() });
      return;
    } else if (corr.type === 'food' || corr.type === 'meal' || corr.type === 'hint') {
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
        if (corr.type === 'meal') {
          runAutofit(sender, corr.originalText, { force: true, mealOverride: chosen });
        } else if (corr.type === 'hint') {
          runAutofit(sender, corr.originalText, { force: true, hintOverride: chosen });
        } else {
          runAutofit(sender, corr.originalText, { force: true, foodOverride: chosen });
        }
        return;
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
        if (pending.type === 'fuzzy') {
          // חזור על הבקשה עם השם המתוקן
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
app.get('/', (_, res) => res.send('✅ autofit bot running'));

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
