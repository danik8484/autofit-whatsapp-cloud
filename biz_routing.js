// biz_routing.js — זיהוי-טריגר, פיצול ומיון פקודות של דני (טהור, בלי תלות בשרת).
// הוצא מ-index.js ב-16.7 כדי: (א) שהבדיקות ירוצו על הקוד האמיתי ולא על העתק;
// (ב) לתקן את תקלת אור איבגי — הודעה עם "תוכנית" באחת השורות גררה את *כל* ההודעה
// למסלול החלפת-תרגיל, ופקודות האוכל שבה נזרקו. עכשיו כל פקודה ממוינת בנפרד.

// מילות מפתח שמפעילות את הבוט
// ⚠️ דני כותב פקודות אך ורק עם "לך" — *לעולם לא "לו"*. שמות-פועל סתמיים
// ('להוסיף'/'להוריד'/'להחליף') וגם וריאציות "לו" אסורים כאן — הם מופיעים בשיחה רגילה
// ("מה אתה רוצה להוסיף?", "אני מוסיף לו עוד שבוע") וגרמו לבוט לתפוס הודעות-שיחה כפקודות.
// פקודה אמיתית תמיד = פועל + "לך".
// הווה ("מוסיף לך") = פקודה תמיד. עבר ("הורדתי לך 30 גרם") = פקודה *רק כשיש מספר אחריו*
// (ראה PAST_CMD למטה) — דני מדווח מה שינה: "ליה הורדתי לך 30 גרם אורז". בלי מספר זה שיחה
// ("אם הוספתי לך זה איכותי") ולא נתפס.
const TRIGGER_WORDS = [
  'מוסיף לך', 'תוסיף לך', 'אוסיף לך', 'נוסיף לך',
  'שם לך',
  'מעלה לך', 'תעלה לך',
  'מוריד לך', 'תוריד לך', 'מפחית לך',
  'מחליף לך', 'תחליף לך',
  'משנה לך',
  'מכניס לך', 'תכניס לך',
];
// פקודת-עבר: פועל-עבר + (לך/לה/גם/את) + מספר. "הורדתי לך 30 גרם", "הורדתי 5 גרם", "הורדתי גם 30".
// המספר הוא מה שמפריד פקודה אמיתית משיחה — "אם הוספתי לך זה איכותי" (בלי מספר) לא ייתפס.
const PAST_CMD = /(?:הורדתי|הוספתי|העליתי|הפחתתי|החלפתי|שיניתי|הכנסתי|הורדת|הוספת)(?:\s+(?:ל[ךה]|גם|את|לך\s+גם)){0,2}\s+\d/;

// WhatsApp / תיקון-שגיאות מכניס לעיתים מקף או רווח כפול בין מילים:
// "מוסיף-לך" / "מוסיף  לך" → "מוסיף לך". מנרמל כדי שזיהוי הטריגר לא יתפספס.
function normalizeBizText(text) {
  return text
    .replace(/([א-ת])[־–—\-]([א-ת])/g, '$1 $2')  // מקף (כולל ־/–/—) בין אותיות עבריות → רווח
    .replace(/[^\S\n]+/g, ' ');                    // רווחים/טאבים כפולים → רווח אחד (שומר שורות חדשות)
}

// אפשרות-ארוחה עוברת כ-ID::NAME. שם בלבד נשמר לתאימות עם תשובות Python ישנות.
// ה-ID חיוני כששתי ארוחות נושאות בדיוק אותו שם.
function parseMealOptionToken(token) {
  const raw = String(token || '').trim();
  const m = raw.match(/^(\d+)::([\s\S]*)$/);
  return m ? { id: m[1], name: m[2].trim() } : { id: '', name: raw };
}

function extractResumeOpMetadata(lines) {
  let resumeOpIndex = null;
  const cleanLines = [];
  for (const line of (lines || [])) {
    const m = String(line).match(/^RESUME_OP_INDEX:(\d+)\s*$/);
    if (m) resumeOpIndex = parseInt(m[1], 10);
    else cleanLines.push(line);
  }
  return { lines: cleanLines, resumeOpIndex };
}

// מחלץ טקסט מהודעה — תומך בטקסט רגיל וגם בהודעה *מתויגת* (reply/quote).
// בתיוג Green API שולח typeMessage=quotedMessage/extendedTextMessage, והטקסט נמצא
// ב-extendedTextMessageData.text (לא ב-textMessageData) + stanzaId של ההודעה שצוטטה.
const QUOTED_TYPES = ['textMessage', 'extendedTextMessage', 'quotedMessage'];
function extractMsgText(msg) {
  if (!msg) return { text: '', quotedId: null };
  if (msg.typeMessage === 'textMessage') {
    return { text: (msg.textMessageData?.textMessage || '').trim(), quotedId: null };
  }
  if (msg.typeMessage === 'extendedTextMessage' || msg.typeMessage === 'quotedMessage') {
    return {
      text: (msg.extendedTextMessageData?.text || '').trim(),
      quotedId: msg.extendedTextMessageData?.stanzaId || null,
    };
  }
  return { text: '', quotedId: null };
}

// גבול-מילה לפני הטריגר: "רשם לך" לא יתפוס את "שם לך". (\b של JS לא עובד בעברית.)
// חריג: ו' החיבור ("ומעלה לך 5 גרם...") = פקודה תקינה — אסור לחסום אותה.
function _triggerBoundaryOK(text, idx) {
  return idx === 0 || !/[א-ת]/.test(text[idx - 1]) || text[idx - 1] === 'ו';
}
// עתיד ("אוסיף לך"/"נוסיף לך" = "אני אתן לך בעתיד") = פקודה *רק כשיש מספר אחריו* — כמו עבר.
// בלי מספר זו שיחה רגילה ("ברגע שתסיים תגיד לי ואני אוסיף לך אירובי סיום") ואסור לתפוס אותה כפקודה.
// נשאר ב-TRIGGER_WORDS כדי שפקודת-עתיד אמיתית עם מספר ("אוסיף לך 50 גרם ריבה") עדיין תיעגן ותפוצל.
const _FUTURE_TRIGGERS = new Set(['אוסיף לך', 'נוסיף לך']);

// ── פקודה-כמעט: פועל-פקודה *בלי* "לך" ──────────────────────────────────────
// 19.7: דני כתב "אני מוסיף כוסית+בורגול+קינואה כאופציה לאורז" (בלי "לך") והבוט
// בלע בשקט — לא ביצע, לא שאל, לא התריע.
//
// ⚠️ הניסיון הראשון היה להריץ הודעות כאלה כפקודה. קודקס הוכיח שזה מסוכן מאוד:
//   "אני מעלה כאן תמונה, יש בה 50 גרם אורז בערב"  → שינה תפריט
//   "אתה מוריד 50 גרם אורז בארוחת ערב?"           → שאלה תמימה שינתה תפריט
//   "אני מוסיף *לו* 30 גרם אורז"                  → נכתב ללקוח הלא-נכון
// לכן: הודעה כזו *אינה* מורצת כפקודה. היא רק מסומנת, כדי שהשרת יתריע לדני
// "נראה כמו פקודה בלי 'לך' — שלח שוב". התרעה לא יכולה לקלקל תפריט; ניחוש כן.
const _VERB_NO_LAMED =
  /(?:^|[\s,])(?:אני\s+)?(?:מוסיף|תוסיף|מוריד|תוריד|מפחית|מחליף|תחליף|מעלה|תעלה|מכניס|תכניס|משנה)(?![א-ת])/;
const _MENU_MARKER = /[כבל]?אופצי|במקום|כתחליף|\d+\s*(?:גרם|גר(?![א-ת]))/;
// שאלה / התייחסות לאדם אחר / שלילה / תנאי — לא להתריע בכלל, זו שיחה.
const _NOT_A_COMMAND = /\?\s*$|(?:^|\s)ל[וה](?![א-ת])|(?:^|\s)לא\s|(?:^|\s)אם\s|תמונה|סרטון/;
function looksLikeMissedCommand(text) {
  const t = String(text || '');
  if (_NOT_A_COMMAND.test(t)) return false;
  return _VERB_NO_LAMED.test(t) && _MENU_MARKER.test(t);
}

function hasTriggerWord(text) {
  if (PAST_CMD.test(text)) return true;  // "הורדתי לך 30 גרם" וכו'
  return TRIGGER_WORDS.some(w => {
    let idx = -1;
    while ((idx = text.indexOf(w, idx + 1)) !== -1) {
      if (!_triggerBoundaryOK(text, idx)) continue;
      // עתיד בלי מספר בהמשך = שיחה, לא פקודה
      if (_FUTURE_TRIGGERS.has(w) && !/\d/.test(text.slice(idx + w.length, idx + w.length + 40))) continue;
      return true;
    }
    return false;
  });
}

// הקשר תרגיל: "אימון" וגם "תוכנית" (בקשת דני) — באימון/לאימון/תוכנית/בתוכנית/התוכנית/לתוכנית
const EX_CONTEXT = /באימון|לאימון|[בלה]?ת[ו]?כנית/;

function hasExerciseTrigger(text) {
  return hasTriggerWord(text) && EX_CONTEXT.test(text);
}

// פקודת סט: טריגר + המילה "סט"/"סטים" כמילה עצמאית (לא "סטייק"/"סטטוס")
function hasSetCommand(text) {
  return hasTriggerWord(text) && /(?:^|\s)סט(?:ים)?(?=$|\s)/.test(text);
}

// ─── עיגון הפקודה למילת-הטריגר ─────────────────────────────────
// דני כותב ללקוח משפט שלם ("אני יכול לשים אופציה\nאני מוסיף לך תמרים").
// הפרסר התבלבל מהפתיח ותפס "יכול ל" כמזון. כאן חותכים כל מה שלפני מילת
// הפקודה הראשונה ("מוסיף לך" וכו'), אבל שומרים מילת-ארוחה אם הופיעה בפתיח.
function anchorToCommand(text) {
  const t = (text || '').trim();
  let best = -1;
  for (const w of TRIGGER_WORDS) {
    let idx = -1;
    while ((idx = t.indexOf(w, idx + 1)) !== -1) {
      if (_triggerBoundaryOK(t, idx)) {
        if (best === -1 || idx < best) best = idx;
        break;
      }
    }
  }
  const pm = t.match(PAST_CMD);
  if (pm && pm.index !== undefined && (best === -1 || pm.index < best)) best = pm.index;
  if (best <= 0) return t.replace(/^אני\s+/, '');  // אין פתיח לחתוך — התנהגות קיימת
  const preamble = t.slice(0, best);
  let anchored = t.slice(best).trim();
  // שמירת מילת-ארוחה מהפתיח שנחתך (שלא נאבד "בבוקר"/"בערב")
  const mealM = preamble.match(/(?:^|\s)ב?(בוקר|צהריים|צהרים|ערב|לילה|ביניים)(?=$|\s)/);
  if (mealM && !new RegExp(mealM[1]).test(anchored)) anchored = 'ב' + mealM[1] + ' ' + anchored;
  return anchored;
}

// ─── פיצול הודעה מרובת-הוספות לפקודות נפרדות ─────────────────────
// דני שולח לעיתים כמה הוראות בהודעה אחת, כל אחת בשורה:
//   "אני מוסיף לך אבקת חלבון...\nאני מוסיף לך פקאנים...\n..."
// עד כה כל ההודעה רצה כתהליך אחד ונחנקה ב-timeout של 30 שניות.
// כאן מפצלים לפי שורות: כל שורה שמתחילה במילת-טריגר = פקודה נפרדת;
// שורה בלי טריגר בתחילתה = המשך של הפקודה הקודמת (לא חותכים באמצע משפט).
// פועל-פתיחה בתחילת שורה גם כשחסר "לך" ("אני מוסיף מעדן..." — דני משמיט לעיתים).
const _CMD_VERB_START = /^(?:מוסיף|תוסיף|אוסיף|נוסיף|מעלה|תעלה|מוריד|תוריד|מפחית|מחליף|תחליף|משנה|מכניס|תכניס)(?=\s|$)/;
// שורה שמתחילה כפקודה (טריגר מלא / פועל-פקודה בלי "לך" / פקודת-עבר עם מספר)
function startsAsCommand(line) {
  const body = String(line || '').trim().replace(/^אני\s+/, '');
  return TRIGGER_WORDS.some(w => body.startsWith(w)) || _CMD_VERB_START.test(body) || PAST_CMD.test(body);
}
// מילת "סט" עצמאית (לא "סטייק"/"סטטוס") — משמש גם לזיהוי שורת-הקשר-אימון
const _SET_WORD = /(?:^|\s)סט(?:ים)?(?=$|\s)/;
function splitBizCommands(text) {
  const lines = String(text || '').split('\n');
  const cmds = [];
  for (const line of lines) {
    const t = line.trim();
    if (!t) continue;
    const startsTrigger = startsAsCommand(t);
    // שורת-שיחה על אימון/תוכנית/סטים ("שינתי לך לתוכנית של שלוש פעמים") אסור
    // שתצטרף לפקודת-אוכל פתוחה — היא מזהמת את שם המזון ("אורז שינתי...") וגם
    // גוררת את הפקודה למסלול התרגילים (ממצא-קודקס 16.7). נהיית מקטע נפרד
    // (לא-פקודה → תיזרק במיון), אלא אם הפקודה הפתוחה עצמה עוסקת באימון.
    const openCmd = cmds.length ? cmds[cmds.length - 1] : '';
    const openIsExercise = openCmd && (EX_CONTEXT.test(openCmd) || _SET_WORD.test(openCmd));
    const exerciseChatter = !startsTrigger && (EX_CONTEXT.test(t) || _SET_WORD.test(t)) && !openIsExercise;
    if (startsTrigger || exerciseChatter || cmds.length === 0) cmds.push(t);
    else cmds[cmds.length - 1] += ' ' + t;  // צירוף שורת-המשך לפקודה הקודמת
  }
  return cmds.length ? cmds : [text];
}

function parseExerciseSwap(text) {
  // "מחליף לך בתוכנית את לחיצת חזה ב-לחיצת ספסל"
  const mFull = text.match(/(?:באימון|לאימון|[בלה]?ת[ו]?כנית)\s+(?:את\s+)?(.+?)\s+ב-?(.+)$/);
  if (mFull) return { oldExercise: mFull[1].trim(), newExercise: mFull[2].trim() };
  // "מחליף לך בתוכנית את לחיצת חזה" (בלי תרגיל חדש)
  const mOld = text.match(/(?:באימון|לאימון|[בלה]?ת[ו]?כנית)\s+(?:את\s+)?(.+)$/);
  if (mOld) return { oldExercise: mOld[1].trim(), newExercise: '' };
  return null;
}

// ─── מיון פקודה בודדת + פיצול-ומיון (התיקון של 16.7) ─────────────
// עד כה ההחלטה סט/תרגיל/תזונה נלקחה על *כל* ההודעה: "תוכנית" באחת השורות
// (אור איבגי: "שינתי לך לתוכנית של שלוש פעמים") שלחה גם את פקודות האוכל
// למסלול התרגילים, שנכשל — והאוכל לא עודכן. עכשיו: קודם מפצלים, ואז ממיינים
// כל פקודה לפי התוכן *שלה* בלבד.
function classifyBizCommand(cmd) {
  if (hasTriggerWord(cmd)) {
    if (hasSetCommand(cmd)) return 'set';
    if (hasExerciseTrigger(cmd)) return 'swap';
    return 'food';
  }
  // פועל-פקודה בלי "לך" ("מוסיף 5 גרם שמן זית") — דני משמיט לעיתים. במסלול
  // הישן שורות כאלה רצו כרגיל (הפרסר מבין אותן); בלי זה הן היו נופלות בשקט
  // (ממצא-קודקס 16.7). ממוינות לפי התוכן שלהן.
  if (startsAsCommand(cmd)) {
    // פועל-עתיד בפתיחה ("נוסיף ריבה?") בלי מספר = שיחה — אותו כלל כמו
    // _FUTURE_TRIGGERS, אחרת שאלת-שיחה מוסיפה מזון לתפריט (קודקס סבב 2).
    const body = cmd.trim().replace(/^אני\s+/, '');
    if (/^ו?[אנ]וסיף(?=\s|$)/.test(body) && !/\d/.test(cmd)) return null;
    if (_SET_WORD.test(cmd)) return 'set';
    if (EX_CONTEXT.test(cmd)) return 'swap';
    return 'food';
  }
  return null;  // שורת-שיחה ("שינתי לך לתוכנית...") — לא פקודה
}

function splitAndClassify(text) {
  const routed = splitBizCommands(text)
    .map(cmd => ({ cmd, kind: classifyBizCommand(cmd) }))
    .filter(x => x.kind);
  // רשת-ביטחון: הטריגר עבר על ההודעה המלאה אבל אף מקטע לא סווג (למשל טריגר
  // שנחצה בין שורות בצורה חריגה) → התנהגות ישנה: כל ההודעה כפקודה אחת.
  if (!routed.length && hasTriggerWord(text)) {
    routed.push({ cmd: text, kind: hasSetCommand(text) ? 'set' : hasExerciseTrigger(text) ? 'swap' : 'food' });
  }
  return routed;
}

module.exports = {
  TRIGGER_WORDS, PAST_CMD, EX_CONTEXT, QUOTED_TYPES,
  normalizeBizText, extractMsgText, hasTriggerWord, hasExerciseTrigger,
  hasSetCommand, anchorToCommand, splitBizCommands, parseExerciseSwap,
  classifyBizCommand, splitAndClassify, startsAsCommand, looksLikeMissedCommand, parseMealOptionToken,
  extractResumeOpMetadata,
};
