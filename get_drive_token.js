// get_drive_token.js — השגת GOOGLE_REFRESH_TOKEN חד-פעמית לחיבור הדרייב של הבוט.
//
// הרצה (פעם אחת, מהמחשב):
//   GOOGLE_CLIENT_ID=xxx GOOGLE_CLIENT_SECRET=yyy node get_drive_token.js
//
// ייפתח דפדפן, מאשרים עם החשבון daniel5085695@gmail.com, והסקריפט מדפיס את
// GOOGLE_REFRESH_TOKEN. מעתיקים אותו (יחד עם ה-CLIENT_ID/SECRET) ל-Railway.
//
// scope: drive.file — הבוט יכול לכתוב רק לתיקייה שהוא יוצר/מנהל. יציב ל-24/7
// (לא פג כל 7 ימים), ולא דורש אימות אפליקציה מול Google.

const http = require('http');
const crypto = require('crypto');
const { exec } = require('child_process');

// PKCE — Google דורשת מ-installed-app clients. בלי זה ה-flow נחסם ("Access blocked").
const VERIFIER = crypto.randomBytes(32).toString('base64url');
const CHALLENGE = crypto.createHash('sha256').update(VERIFIER).digest('base64url');

// ברירת מחדל: ה-OAuth client הפומבי של Google Cloud SDK (installed app, תומך loopback).
// מאפשר השגת refresh token בלי להקים OAuth client ב-Console. אפשר לדרוס עם env.
const CLIENT_ID = process.env.GOOGLE_CLIENT_ID || '32555940559.apps.googleusercontent.com';
const CLIENT_SECRET = process.env.GOOGLE_CLIENT_SECRET || 'ZmssLNjJy2998hD4CTg2ejr2';
const PORT = 53682;                                   // פורט loopback מקומי
const REDIRECT = `http://localhost:${PORT}`;
const SCOPE = 'https://www.googleapis.com/auth/drive';
const fs = require('fs');

const authUrl = 'https://accounts.google.com/o/oauth2/v2/auth?' + new URLSearchParams({
  client_id: CLIENT_ID,
  redirect_uri: REDIRECT,
  response_type: 'code',
  scope: SCOPE,
  access_type: 'offline',      // כדי לקבל refresh_token
  prompt: 'consent',           // מאלץ refresh_token טרי
  code_challenge: CHALLENGE,
  code_challenge_method: 'S256',
});

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, REDIRECT);
  const code = url.searchParams.get('code');
  if (!code) { res.end('אין code'); return; }

  try {
    const tokRes = await fetch('https://oauth2.googleapis.com/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        code, client_id: CLIENT_ID, client_secret: CLIENT_SECRET,
        redirect_uri: REDIRECT, grant_type: 'authorization_code',
        code_verifier: VERIFIER,
      }),
    });
    const tok = await tokRes.json();
    if (!tok.refresh_token) {
      res.end('לא התקבל refresh_token. נסה שוב (ודא prompt=consent / בטל גישה קודמת ב-myaccount.google.com).');
      console.error('\n❌ אין refresh_token בתשובה:', JSON.stringify(tok, null, 2));
      server.close();
      return;
    }
    res.end('✅ הצליח! אפשר לסגור את החלון ולחזור לטרמינל.');
    try { fs.writeFileSync('/tmp/drive_creds.json', JSON.stringify({
      GOOGLE_CLIENT_ID: CLIENT_ID, GOOGLE_CLIENT_SECRET: CLIENT_SECRET, GOOGLE_REFRESH_TOKEN: tok.refresh_token,
    })); } catch (_) {}
    console.log('\n════════════════════════════════════════════════════════');
    console.log('✅ DRIVE_CREDS_OK');
    console.log('GOOGLE_REFRESH_TOKEN=' + tok.refresh_token);
    console.log('════════════════════════════════════════════════════════');
  } catch (e) {
    res.end('שגיאה: ' + e.message);
    console.error(e);
  } finally {
    setTimeout(() => server.close(), 500);
  }
});

server.listen(PORT, () => {
  try { fs.writeFileSync('/tmp/auth_url.txt', authUrl); } catch (_) {}
  console.log('🔐 השרת מאזין על ' + PORT + '. ממתין לאישור...');
});
