#!/usr/bin/env node
// test_exercise_js.js — בודק את לוגיקת ה-JS של החלפת תרגילים
// (זיהוי trigger, פירוק תשובות Python) ללא תלות בשרת.

let PASS = 0, FAIL = 0;
const FAILS = [];
function chk(label, got, expected) {
  const ok = JSON.stringify(got) === JSON.stringify(expected);
  if (ok) { PASS++; console.log(`  ✅ ${label}`); }
  else { FAIL++; FAILS.push(`${label}\n      ציפי: ${JSON.stringify(expected)}\n      קיבל: ${JSON.stringify(got)}`); console.log(`  ❌ ${label}`); }
}
function chkTrue(label, cond, detail = "") {
  if (cond) { PASS++; console.log(`  ✅ ${label}`); }
  else { FAIL++; FAILS.push(`${label} — ${detail}`); console.log(`  ❌ ${label} — ${detail}`); }
}

// ── שכפול הפונקציות מ-index.js (אותו קוד בדיוק) ──────────────────
const TRIGGER_WORDS = ['מחליף לך', 'מחליף לו', 'תחליף לך', 'מוסיף לך', 'להחליף'];
function hasTriggerWord(text) { return TRIGGER_WORDS.some(w => text.includes(w)); }
function hasExerciseTrigger(text) {
  return hasTriggerWord(text) && (text.includes('באימון') || text.includes('לאימון'));
}

// ═══ A. hasExerciseTrigger — ניתוב נכון ═══
console.log("\n═══ A. hasExerciseTrigger (תרגיל vs תזונה) ═══");
chkTrue("A1 'מחליף לך באימון לחיצת חזה' → תרגיל",
  hasExerciseTrigger("מחליף לך באימון לחיצת חזה ב-לחיצת ספסל"));
chkTrue("A2 'אני מחליף לך באימון את הסקוואט' → תרגיל",
  hasExerciseTrigger("קיבלתי, אני מחליף לך באימון את הסקוואט"));
chkTrue("A3 'מחליף לך לאימון X' → תרגיל",
  hasExerciseTrigger("מחליף לך לאימון דדליפט ב-סקוואט"));
chkTrue("A4 'מחליף לך לחם בשיבולת' → NOT תרגיל (תזונה)",
  !hasExerciseTrigger("מחליף לך לחם בשיבולת שועל"));
chkTrue("A5 הודעה רגילה → NOT תרגיל",
  !hasExerciseTrigger("מה המצב אחי, איך האימון"));
chkTrue("A6 'באימון' בלי trigger word → NOT (לא פקודה)",
  !hasExerciseTrigger("היה לי כיף באימון היום"));

// ═══ B. פירוק EXERCISE_CHOICE (תרגיל ישן כפול) ═══
console.log("\n═══ B. פירוק EXERCISE_CHOICE ═══");
function parseExerciseChoice(raw) {
  const lines = raw.split('\n');
  const choiceData = lines[0].slice('EXERCISE_CHOICE:'.length);
  return choiceData.split('|').map(seg => {
    const parts = seg.split(':');
    return { id: parts[0], name: parts[1] || '', tmpl: parts[2] || '', sets: parts[3] || '', reps: parts[4] || '' };
  }).filter(a => a.id);
}
const choiceRaw = "EXERCISE_CHOICE:1069535:חזה - לחיצת חזה עם משקולות בשיפוע עליון:A:3:20|1069537:חזה - לחיצת חזה עליון במוט:A:3:24\n❓ מצאתי 2 תרגילים";
const parsed = parseExerciseChoice(choiceRaw);
chk("B1 מספר אפשרויות", parsed.length, 2);
chk("B2 id ראשון", parsed[0].id, "1069535");
chk("B3 שם ראשון", parsed[0].name, "חזה - לחיצת חזה עם משקולות בשיפוע עליון");
chk("B4 sets ראשון", parsed[0].sets, "3");
chk("B5 reps שני", parsed[1].reps, "24");
chkTrue("B6 בחירת אפשרות 2 → id נכון", parsed[1].id === "1069537");

// ═══ C. פירוק EXERCISE_NEED_NEW (בלי תרגיל חדש) ═══
console.log("\n═══ C. פירוק EXERCISE_NEED_NEW ═══");
function parseNeedNew(raw) {
  const lines = raw.split('\n');
  const head = lines[0].slice('EXERCISE_NEED_NEW:'.length);
  const ci = head.indexOf(':');
  return { aid: ci >= 0 ? head.slice(0, ci) : head, oldName: ci >= 0 ? head.slice(ci + 1) : '' };
}
const needRaw = "EXERCISE_NEED_NEW:1069539:חזה - לחיצת חזה עם משקולות\n✅ מצאתי\n❓ במה להחליף?";
const nn = parseNeedNew(needRaw);
chk("C1 assignment_id", nn.aid, "1069539");
chk("C2 שם התרגיל הישן", nn.oldName, "חזה - לחיצת חזה עם משקולות");

// ═══ D. ניקוי תשובת דני ("במה להחליף?") ═══
console.log("\n═══ D. ניקוי תשובת תרגיל חדש ═══");
function cleanNewName(t) { return t.replace(/^ב-?/, '').trim(); }
chk("D1 'בלחיצת רגליים' → 'לחיצת רגליים'", cleanNewName("בלחיצת רגליים"), "לחיצת רגליים");
chk("D2 'ב-לחיצת רגליים' → 'לחיצת רגליים'", cleanNewName("ב-לחיצת רגליים"), "לחיצת רגליים");
chk("D3 'לחיצת רגליים' → ללא שינוי", cleanNewName("לחיצת רגליים"), "לחיצת רגליים");

// ═══ E. זיהוי מספר בחירה ═══
console.log("\n═══ E. זיהוי מספר בחירה מהקבוצה ═══");
function pickNum(t) { const m = t.trim().match(/^(?:בחר\s+)?(\d+)$/); return m ? parseInt(m[1]) : null; }
chk("E1 '2'", pickNum("2"), 2);
chk("E2 'בחר 3'", pickNum("בחר 3"), 3);
chk("E3 'לא מספר' → null", pickNum("לחיצת חזה"), null);

// ═══ F. פירוק EXERCISE_REPLACE (רשימת ספרייה להחלפה) ═══
console.log("\n═══ F. פירוק EXERCISE_REPLACE ═══");
function parseReplace(raw) {
  const lines = raw.split('\n');
  const head = lines[0].slice('EXERCISE_REPLACE:'.length);
  const ci = head.indexOf(':');
  const aid = head.slice(0, ci), oldName = head.slice(ci + 1);
  const optsLine = lines.find(l => l.startsWith('OPTS:')) || 'OPTS:';
  const libOptions = optsLine.slice(5).split('|').map(s => {
    const c = s.indexOf(':');
    return c >= 0 ? { id: s.slice(0, c), name: s.slice(c + 1) } : null;
  }).filter(Boolean);
  const bodyText = lines.filter(l => !l.startsWith('OPTS:')).slice(1).join('\n');
  return { aid, oldName, libOptions, bodyText };
}
const replRaw = "EXERCISE_REPLACE:1069561:רגליים - סקוואט עם מוט חופשי\nOPTS:85230:לחיצת רגליים כנגד מכונה|39105:רגליים - סומו סקוואט|39110:רגליים - דדליפט רומני\n🔁 להחלפת *סקוואט*\n1. לחיצת רגליים\nשלח מספר";
const rp = parseReplace(replRaw);
chk("F1 assignment_id", rp.aid, "1069561");
chk("F2 oldName", rp.oldName, "רגליים - סקוואט עם מוט חופשי");
chk("F3 מספר אפשרויות ספרייה", rp.libOptions.length, 3);
chk("F4 בחירה 2 → id", rp.libOptions[1].id, "39105");
chk("F5 בחירה 2 → שם", rp.libOptions[1].name, "רגליים - סומו סקוואט");
chkTrue("F6 body בלי שורת OPTS", !rp.bodyText.includes("OPTS:"));
chkTrue("F7 תיקון שריר: 'חזה' אינו מספר", pickNum("חזה") === null);

console.log("\n" + "═".repeat(55));
console.log(`  סה"כ: ${PASS + FAIL} | ✅ ${PASS} | ❌ ${FAIL}`);
if (FAILS.length) { console.log("\n  כשלים:"); FAILS.forEach(f => console.log(`   • ${f}`)); }
console.log("═".repeat(55));
process.exit(FAIL ? 1 : 0);
