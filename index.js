const { default: makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys');
const { Boom } = require('@hapi/boom');
const { spawn, execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const qrcode = require('qrcode-terminal');

const ALLOWED_NUMBERS = [
  '972547198498',    // דני
  '972539598622',    // רון
  '257586553212944', // דני (LID)
  '151573808287824', // רון (LID)
  '110097946665197', // רון (LID 2)
];

const LID_TO_PHONE = {
  '257586553212944': '972547198498',
  '151573808287824': '972539598622',
  '110097946665197': '972539598622',
};

const OTP_FILE = '/tmp/autofit_otp.txt';
const SESSION_FILE = path.join(__dirname, 'session.json');

let pendingOtpSender = null;
let pendingLoginResolve = null;
let sock;

function sessionExists() {
  try {
    const s = JSON.parse(fs.readFileSync(SESSION_FILE, 'utf8'));
    return !!(s && s.token);
  } catch { return false; }
}

async function connectToWhatsApp() {
  const { state, saveCreds } = await useMultiFileAuthState('./baileys_session');

  sock = makeWASocket({ auth: state, printQRInTerminal: false });

  sock.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      console.log('\n📱 סרוק QR:\n');
      qrcode.generate(qr, { small: true });
      const QRCode = require('qrcode');
      await QRCode.toFile('./qr.png', qr, { width: 400 });
    }
    if (connection === 'close') {
      const statusCode = (lastDisconnect?.error instanceof Boom)
        ? lastDisconnect.error.output.statusCode : 0;
      const isConflict = statusCode === 440;
      console.log('חיבור נסגר | סיבה:', statusCode);
      if (isConflict) { console.log('⚠️ Conflict — יוצא.'); process.exit(1); }
      else if (statusCode !== DisconnectReason.loggedOut) setTimeout(connectToWhatsApp, 3000);
    } else if (connection === 'open') {
      console.log('✅ הבוט מחובר ומוכן!');
    }
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;

    for (const msg of messages) {
      if (msg.key.fromMe) continue;
      const senderId = msg.key.remoteJid?.replace('@s.whatsapp.net','').replace('@lid','');
      const text = msg.message?.conversation || msg.message?.extendedTextMessage?.text || '';

      console.log(`📨 מ: ${senderId} | "${text}"`);
      if (!ALLOWED_NUMBERS.includes(senderId) || !text.trim()) continue;

      const phone = LID_TO_PHONE[senderId] || senderId;
      const replyJid = phone.includes('@') ? phone : `${phone}@s.whatsapp.net`;

      // OTP לlogin
      if (pendingOtpSender === senderId && /^\d{4,6}$/.test(text.trim())) {
        fs.writeFileSync(OTP_FILE, text.trim());
        pendingOtpSender = null;
        await sock.sendMessage(replyJid, { text: '✅ קוד התקבל, מתחבר...' });
        if (pendingLoginResolve) { pendingLoginResolve(); pendingLoginResolve = null; }
        continue;
      }

      // OTP request מהמערכת
      if (text.trim().startsWith('OTP_NEEDED')) {
        pendingOtpSender = senderId;
        await sock.sendMessage(replyJid, { text: '🔑 נדרש קוד OTP — שלח לי את הקוד שהגיע למייל' });
        continue;
      }

      await sock.sendMessage(replyJid, { text: '⏳ מבצע...' });
      runAutofit(senderId, replyJid, text.trim());
    }
  });
}

function runAutofit(senderId, jid, requestText) {
  // נסה API חדש קודם, fallback לישן
  const apiScript = path.join(__dirname, 'autofit_api.py');
  const scriptPath = fs.existsSync(apiScript) ? apiScript : path.join(__dirname, 'autofit.py');

  const proc = spawn('python3', [scriptPath, requestText]);
  let output = '';

  proc.stdout.on('data', async (data) => {
    const line = data.toString().trim();
    console.log('[autofit]', line);
    if (line === 'OTP_NEEDED') {
      pendingOtpSender = senderId;
      await sock.sendMessage(jid, { text: '🔑 נדרש קוד OTP — שלח לי את הקוד' });
    } else {
      output += line + '\n';
    }
  });

  proc.stderr.on('data', (data) => {
    const err = data.toString().trim();
    // token פג — טרגר re-login
    if (err.includes('401') || err.includes('Unauthorized') || err.includes('token')) {
      sock.sendMessage(jid, { text: '🔄 Token פג — מתחבר מחדש...' });
      triggerRelogin(senderId, jid, requestText);
    } else {
      console.error('[autofit error]', err);
    }
  });

  proc.on('close', async (code) => {
    pendingOtpSender = null;
    const msg = output.trim() || (code === 0 ? 'בוצע בהצלחה!' : 'משהו השתבש');
    await sock.sendMessage(jid, { text: code === 0 ? `${msg}` : `❌ ${msg}` });
  });
}

async function triggerRelogin(senderId, jid, retryRequest) {
  const phone = LID_TO_PHONE[senderId] || senderId;
  const replyJid = phone.includes('@') ? phone : `${phone}@s.whatsapp.net`;
  await sock.sendMessage(replyJid, { text: '📧 שולח OTP למייל...' });

  // הפעל login.py
  const proc = spawn('python3', [path.join(__dirname, 'login.py')], {
    env: { ...process.env }
  });

  proc.stdout.on('data', d => console.log('[login]', d.toString().trim()));

  // OTP יגיע מהוואטסאפ
  pendingOtpSender = senderId;

  proc.on('close', async (code) => {
    if (code === 0 && retryRequest) {
      await sock.sendMessage(replyJid, { text: '✅ מחובר! מנסה שוב...' });
      runAutofit(senderId, replyJid, retryRequest);
    } else if (code !== 0) {
      await sock.sendMessage(replyJid, { text: '❌ Login נכשל' });
    }
  });
}

connectToWhatsApp();
