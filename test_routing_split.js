#!/usr/bin/env node
// test_routing_split.js — ניתוב פר-פקודה: הודעה מרובת-הוראות מפוצלת וכל הוראה
// מנותבת לבד (תזונה/תרגיל/סט). בודק את הקוד האמיתי (biz_routing.js), לא העתק.
//
// רקע (16.7): דני שלח לאור איבגי הודעה אחת עם "שינתי לך לתוכנית של שלוש פעמים"
// + שתי הורדות אוכל. המילה "לתוכנית" גררה את *כל* ההודעה למסלול החלפת-תרגיל,
// שנכשל — והורדות האוכל נזרקו. הלקוח נשאר עם תפריט לא מעודכן.

const { splitAndClassify, classifyBizCommand, normalizeBizText, parseMealOptionToken,
  extractResumeOpMetadata } = require('./biz_routing');

let PASS = 0, FAIL = 0;
const FAILS = [];
function chk(label, got, expected) {
  const ok = JSON.stringify(got) === JSON.stringify(expected);
  if (ok) { PASS++; console.log(`  ✅ ${label}`); }
  else { FAIL++; FAILS.push(`${label}\n      ציפי: ${JSON.stringify(expected)}\n      קיבל: ${JSON.stringify(got)}`); console.log(`  ❌ ${label}`); }
}
const kinds = text => splitAndClassify(normalizeBizText(text)).map(x => x.kind);

console.log("═══ A. תקלת אור איבגי 16.7 — 'תוכנית' לא חוטפת את האוכל ═══");
const orMsg = "אור עברתי על הכל קודם כל שינתי לך לתוכנית של שלוש פעמים \nראשון -A\nשלישי-B\nחמישי -A \n\n אני מוריד לך 100 גרם אורז בארוחת צהריים + ערב \n\nאני מוריד לך 5 גרם שמן זית בארוחת צהריים + ערב";
chk("A1 ההודעה של אור → 2 פקודות אוכל, 0 תרגיל", kinds(orMsg), ['food', 'food']);
const orCmds = splitAndClassify(normalizeBizText(orMsg)).map(x => x.cmd);
chk("A2 פקודה 1 = הורדת אורז", orCmds[0].includes('אורז') && orCmds[0].includes('100'), true);
chk("A3 פקודה 2 = הורדת שמן זית", orCmds[1].includes('שמן זית') && orCmds[1].includes('5'), true);

console.log("═══ B. שכנים-תמימים (KEEP — ניתוב קיים לא נשבר) ═══");
chk("B1 החלפת תרגיל אמיתית → swap",
  kinds("מחליף לך באימון סקוואט בהאק סקוואט"), ['swap']);
chk("B2 החלפה בתוכנית → swap",
  kinds("מחליף לך בתוכנית את לחיצת חזה בלחיצת ספסל"), ['swap']);
chk("B3 הוספת סט → set",
  kinds("מוסיף לך עוד סט בלחיצת חזה"), ['set']);
chk("B4 פקודת אוכל רגילה → food (בן שפיר 16.7)",
  kinds("אני מעלה לך 70 גרם אורז בארוחת צהריים + ערב"), ['food']);
chk("B5 פתיח + פקודת אוכל → food אחת (הפתיח נזרק)",
  kinds("עברתי על הכל בן\n\nאני מעלה לך 70 גרם אורז בארוחת צהריים + ערב"), ['food']);
chk("B6 החלפת מזון (בלי אימון/תוכנית) → food",
  kinds("מחליף לך לחם בשיבולת שועל"), ['food']);
chk("B7 שיחה רגילה → כלום",
  kinds("היה לי כיף באימון היום, תוכנית טובה"), []);
chk("B8 פקודת-עבר עם מספר → food",
  kinds("הורדתי לך 30 גרם אורז מהצהריים"), ['food']);

console.log("═══ C. שילובים שנשברו עד היום ═══");
chk("C1 תרגיל + אוכל בהודעה אחת → swap ואז food",
  kinds("מחליף לך באימון סקוואט בהאק\nמוסיף לך 50 גרם אורז בבוקר"), ['swap', 'food']);
chk("C2 סט + אוכל בהודעה אחת → set ואז food",
  kinds("מוסיף לך עוד סט במתח\nמוסיף לך 50 גרם אורז בבוקר"), ['set', 'food']);
chk("C3 פתיח עם 'תוכנית' + אוכל → food בלבד",
  kinds("שינתי לך לתוכנית של שלוש פעמים\nאני מוסיף לך 50 גרם אורז בבוקר"), ['food']);

console.log("═══ D. classifyBizCommand ישירות ═══");
chk("D1 פקודת אוכל", classifyBizCommand("מוסיף לך 50 גרם אורז בבוקר"), 'food');
chk("D2 לא-פקודה", classifyBizCommand("מה המצב אחי"), null);

console.log("═══ E. ממצאי ביקורת-קודקס 16.7 ═══");
chk("E1 פועל-פקודה בלי 'לך' לא נופל בשקט",
  kinds("אני מוסיף לך 100 גרם אורז בצהריים\nאני מוסיף 5 גרם שמן זית בערב"), ['food', 'food']);
chk("E2 שורת-'תוכנית' באמצע לא מזהמת את פקודת האוכל שלפניה",
  kinds("מוריד לך 100 גרם אורז בצהריים\nשינתי לך לתוכנית שלוש פעמים\nמוריד לך 5 גרם שמן זית בערב"),
  ['food', 'food']);
const e2cmds = splitAndClassify(normalizeBizText("מוריד לך 100 גרם אורז בצהריים\nשינתי לך לתוכנית שלוש פעמים\nמוריד לך 5 גרם שמן זית בערב")).map(x => x.cmd);
chk("E2b שם המזון נשאר נקי (בלי 'שינתי')", e2cmds[0].includes('שינתי'), false);
chk("E3 המשך-שורה תמים עדיין מצטרף לפקודה",
  splitAndClassify(normalizeBizText("מוריד לך 100 גרם\nאורז בצהריים")).map(x => x.cmd), ['מוריד לך 100 גרם אורז בצהריים']);
chk("E4 פקודת-סט עם המשך-שורה של אימון נשארת שלמה",
  kinds("מוסיף לך עוד סט\nבאימון רגליים בלחיצת רגליים"), ['set']);
// התנהגות מתועדת (הוכרעה 16.7): "מחליף לך X ב-Y" בלי באימון/תוכנית בשורת הפקודה = אוכל.
// הפורמט הרשמי לתרגיל הוא "מחליף לך באימון X ב-Y" — ההקשר חייב להיות בשורת הפקודה.
chk("E5 (מתועד) הקשר-אימון בשורת-פתיח לא הופך פקודה חשופה לתרגיל",
  kinds("שינויים באימון:\nאני מחליף לך לחיצת חזה בלחיצת ספסל"), ['food']);
chk("E6 שאלת-שיחה 'נוסיף ריבה?' לא הופכת לפקודה (קודקס סבב 2)",
  kinds("מוסיף לך 50 גרם חזה עוף בצהריים\nנוסיף ריבה?"), ['food']);
chk("E7 עתיד עם מספר = פקודה אמיתית (KEEP)",
  kinds("אוסיף לך 50 גרם ריבה בבוקר"), ['food']);
chk("E8 'אוסיף לך ריבה' בלי מספר — לא פקודה גם במקטע (KEEP)",
  kinds("מוסיף לך 100 גרם אורז בצהריים\nאוסיף לך ריבה בהמשך"), ['food']);

console.log("═══ F. ממצא 8 — בחירת ארוחה שומרת מזהה ═══");
chk("F1 token חדש מחזיר ID ושם",
  parseMealOptionToken("41446::ארוחת צהריים אחרי בישול"),
  { id: '41446', name: 'ארוחת צהריים אחרי בישול' });
chk("F2 שם ישן נשאר תואם לאחור",
  parseMealOptionToken("ארוחת ערב"), { id: '', name: 'ארוחת ערב' });
chk("F3 שתי אפשרויות זהות בשם נשארות שונות לפי ID",
  ["41445::ארוחת צהריים אחרי בישול", "41446::ארוחת צהריים אחרי בישול"]
    .map(parseMealOptionToken).map(x => x.id), ['41445', '41446']);

console.log("═══ G. ממצא 20 — metadata של פעולת המשך ═══");
chk("G1 אינדקס המשך נשלף ואינו מוצג למשתמש",
  extractResumeOpMetadata(['RESUME_OP_INDEX:1', 'שורת שאלה']),
  { lines: ['שורת שאלה'], resumeOpIndex: 1 });
chk("G2 אינדקס 0 נשמר (אינו נופל בגלל falsy)",
  extractResumeOpMetadata(['RESUME_OP_INDEX:0']),
  { lines: [], resumeOpIndex: 0 });

console.log(`\n${'='.repeat(40)}\nPASS=${PASS} FAIL=${FAIL}`);
FAILS.forEach(f => console.log(`  ❌ ${f}`));
process.exit(FAIL ? 1 : 0);
