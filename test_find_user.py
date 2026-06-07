#!/usr/bin/env python3
"""
בדיקת זיהוי משתמשים — ~2000 הודעות
✅ קריאה בלבד: parse_message + find_user בלבד
❌ אין execute_request, אין כתיבה ל-auto-fit
"""
import json, re, random
from autofit_api import parse_message, find_user, USER_CACHE_FILE

# ── טעינת רשימת משתמשים מה-cache ─────────────────────────────────
_raw    = json.loads(USER_CACHE_FILE.read_text(encoding="utf-8"))
_USERS  = _raw["users"]

def _name(u):   return (u.get("name") or "").strip()
def _uid(u):    return str(u["id"])
def _phone(u):
    p = str(u.get("phone", "")).strip()
    if len(p) == 9 and p.isdigit():
        return "0" + p   # 9 ספרות → הוסף 0 בהתחלה
    if p.startswith("972") and len(p) == 12:
        return "0" + p[3:]
    return p if p.startswith("05") else ""

# ── סינון: שמות עבריים תקינים ─────────────────────────────────────
SKIP_NAMES = {"DANIKAGANOVICH - לא למחוק!", "Laurel grass", "rachel robin",
              "בן", "ארתור", "rachel robin"}
def _valid(u):
    n = _name(u)
    if not n or n in SKIP_NAMES: return False
    if not re.search(r'[א-ת]', n):  return False  # חייב לפחות תו עברי
    return True

USERS = [u for u in _USERS if _valid(u)]
print(f"משתמשים תקינים: {len(USERS)}")

# ── מיפוי שם → uid (לזיהוי כפילויות) ────────────────────────────
from collections import Counter
_first_name_count = Counter(_name(u).split()[0] for u in USERS)

# ── תבניות הודעה ──────────────────────────────────────────────────
VERBS   = ["תוסיפי", "הוסיפי", "הוסף", "תוסיף", "שימי", "תשימי",
           "עדכן", "שנה", "תחליפי", "החלף"]
FOODS_A = ["טונה", "אורז", "לחם מלא", "פסטה", "חזה עוף", "ביצים",
           "גבינה 5%", "שיבולת שועל", "בטטה", "קוואקר", "סלמון"]
FOODS_B = ["חזה עוף מבושל", "אורז מבושל", "פסטה", "לחם לבן", "ביצה",
           "בשר טחון", "קוואקר", "גבינה", "בטטה", "טונה", "אורז"]
MEALS   = ["בבוקר", "בצהריים", "בערב", ""]
REPS    = ["כתחליף ל", "במקום", "בנוסף ל", "תחליף ל", "כאופציה ל"]

def fa():   return random.choice(FOODS_A)
def fb():   return random.choice(FOODS_B)
def meal(): return random.choice(MEALS)
def verb(): return random.choice(VERBS)
def rep():  return random.choice(REPS)

def msgs_for(u):
    n  = _name(u)
    ph = _phone(u)
    uid = _uid(u)
    fn  = n.split()[0]  # שם פרטי
    uniq_first = _first_name_count[fn] == 1

    rows = []

    # ── שם מלא ────────────────────────────────────────────────────
    for _ in range(3):
        rows.append((f"{verb()} ל{n} {fa()} {rep()}{fb()} {meal()}".strip(), uid))
    rows.append((f"ל{n} {verb()} {fa()} {rep()}{fb()} {meal()}".strip(), uid))
    rows.append((f"{n}: {fa()} {rep()}{fb()} {meal()}".strip(), uid))
    rows.append((f"{n} - {verb()} {fa()} {rep()}{fb()}".strip(), uid))
    rows.append((f"שם: {n}\nהוספה: {fa()} {rep()}{fb()} {meal()}".strip(), uid))

    # ── שם פרטי בלבד (אם ייחודי) ──────────────────────────────────
    if uniq_first:
        rows.append((f"{verb()} ל{fn} {fa()} {rep()}{fb()} {meal()}".strip(), uid))
        rows.append((f"ל{fn} {verb()} {fa()} {rep()}{fb()}".strip(), uid))
        rows.append((f"{fn}: {fa()} {rep()}{fb()}".strip(), uid))

    # ── מספר טלפון ────────────────────────────────────────────────
    if ph:
        rows.append((f"{verb()} ל{ph} {fa()} {rep()}{fb()} {meal()}".strip(), uid))
        rows.append((f"ל{ph} {fa()} {rep()}{fb()}".strip(), uid))

    return rows

# ── בניית 2000 בדיקות ─────────────────────────────────────────────
random.seed(42)
all_tests = []
for u in USERS:
    all_tests.extend(msgs_for(u))

random.shuffle(all_tests)
TESTS = all_tests[:2000]
print(f"בדיקות שנבחרו: {len(TESTS)}\n")

# ── הרצה ─────────────────────────────────────────────────────────
passed = confirmed = failed = 0
no_name = no_user = wrong_uid = 0
failures = []

for i, (msg, exp_uid) in enumerate(TESTS):
    parsed = parse_message(msg)
    pname  = parsed.get("name", "")

    if not pname:
        # נסה לזהות טלפון ישירות
        ph_m = re.search(r'05\d{8}', msg)
        if ph_m:
            pname = ph_m.group()
        else:
            no_name += 1; failed += 1
            failures.append((msg, exp_uid, "שם לא חולץ", "", ""))
            continue

    uid_f, name_f, fuzzy = find_user(pname)

    if not uid_f:
        no_user += 1; failed += 1
        failures.append((msg, exp_uid, f"לא נמצא (name={pname!r})", pname, ""))
        continue

    if uid_f == exp_uid:
        if fuzzy: confirmed += 1
        else:     passed   += 1
    else:
        wrong_uid += 1; failed += 1
        failures.append((msg, exp_uid, f"uid שגוי: {uid_f}={name_f!r}", pname, uid_f))

    if (i + 1) % 400 == 0:
        print(f"  {i+1}/{len(TESTS)} | ✅{passed} ⚠️{confirmed} ❌{failed}")

# ── דו"ח סופי ─────────────────────────────────────────────────────
total = len(TESTS)
ok    = passed + confirmed
print(f"\n{'='*60}")
print(f"📊 תוצאות: {total} בדיקות")
print(f"  ✅ נכון מדויק  : {passed:4d} ({100*passed/total:.1f}%)")
print(f"  ⚠️  נכון+fuzzy  : {confirmed:4d} ({100*confirmed/total:.1f}%)")
print(f"  🎯 סה\"כ נמצאו  : {ok:4d} ({100*ok/total:.1f}%)")
print(f"  ❌ נכשל        : {failed:4d} ({100*failed/total:.1f}%)")
print(f"     ↳ שם לא חולץ    : {no_name}")
print(f"     ↳ משתמש לא נמצא : {no_user}")
print(f"     ↳ uid שגוי       : {wrong_uid}")

if failures:
    cats = {}
    for _, _, r, *_ in failures:
        k = r.split("(")[0].strip()
        cats[k] = cats.get(k, 0) + 1
    print("\n── קטגוריות כשלים ──")
    for k, v in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {v:3d}× {k}")

    print("\n── 15 דוגמאות כשל ──")
    for msg, exp, reason, pname, uid_got in failures[:15]:
        print(f"  ❌ {reason}")
        print(f"     הודעה : {msg[:70]!r}")
        print(f"     name  : {pname!r}  exp_uid={exp}")
