#!/usr/bin/env python3
"""
בדיקת זיהוי לפי מספר טלפון — 2000 הודעות
✅ קריאה בלבד: parse_message + find_user בלבד
❌ אין execute_request, אין כתיבה ל-auto-fit
"""
import json, re, random
from autofit_api import parse_message, find_user, USER_CACHE_FILE

# ── טעינת משתמשים ─────────────────────────────────────────────────
_raw   = json.loads(USER_CACHE_FILE.read_text(encoding="utf-8"))
_USERS = _raw["users"]

def _name(u): return (u.get("name") or "").strip()
def _uid(u):  return str(u["id"])
def _phone(u):
    p = str(u.get("phone", "") or "").strip()
    if len(p) == 9 and p.isdigit(): return "0" + p
    if p.startswith("972") and len(p) == 12: return "0" + p[3:]
    return p if p.startswith("05") else ""

USERS = [(u, _phone(u)) for u in _USERS if _phone(u) and _name(u) and re.search(r'[א-ת]', _name(u))]
print(f"משתמשים עם טלפון: {len(USERS)}")

# ── תבניות הודעה ──────────────────────────────────────────────────
VERBS  = ["תוסיפי", "הוסיפי", "הוסף", "תוסיף", "שימי", "תשימי", "עדכן", "שנה", "תחליפי", "החלף"]
FOODS  = ["טונה", "אורז", "לחם מלא", "פסטה", "חזה עוף", "ביצים", "גבינה 5%", "שיבולת שועל", "בטטה", "קוואקר", "סלמון"]
FOODS2 = ["חזה עוף מבושל", "אורז מבושל", "פסטה", "לחם לבן", "ביצה", "בשר טחון", "קוואקר", "גבינה", "בטטה", "טונה", "אורז"]
MEALS  = ["בבוקר", "בצהריים", "בערב", ""]
REPS   = ["כתחליף ל", "במקום", "בנוסף ל", "תחליף ל", "כאופציה ל"]

fa   = lambda: random.choice(FOODS)
fb   = lambda: random.choice(FOODS2)
meal = lambda: random.choice(MEALS)
verb = lambda: random.choice(VERBS)
rep  = lambda: random.choice(REPS)

def fmt_phone_variants(ph):
    """מחזיר פורמטים שונים של אותו טלפון"""
    clean = ph.replace("-", "")  # 0546739981
    dashed = f"{clean[:3]}-{clean[3:6]}-{clean[6:]}"   # 054-673-9981
    il972  = "972" + clean[1:]                          # 972546739981
    return [clean, dashed, il972]

def msgs_for(u, ph):
    uid = _uid(u)
    rows = []
    for fmt in fmt_phone_variants(ph):
        rows.append((f"{verb()} ל{fmt} {fa()} {rep()}{fb()} {meal()}".strip(), uid))
        rows.append((f"ל{fmt} {fa()} {rep()}{fb()} {meal()}".strip(), uid))
        rows.append((f"ל{fmt}: {fa()} {rep()}{fb()} {meal()}".strip(), uid))
    return rows

# ── בניית 2000 בדיקות ─────────────────────────────────────────────
random.seed(99)
all_tests = []
for u, ph in USERS:
    all_tests.extend(msgs_for(u, ph))

random.shuffle(all_tests)
TESTS = all_tests[:2000]
print(f"בדיקות שנבחרו: {len(TESTS)}\n")

# ── הרצה ─────────────────────────────────────────────────────────
passed = confirmed = failed = 0
no_name = no_user = wrong_uid = 0
failures = []

for i, (msg, exp_uid) in enumerate(TESTS):
    # חלץ טלפון מההודעה (parse_message או fallback)
    parsed = parse_message(msg)
    pname  = parsed.get("name", "")

    if not pname:
        ph_m = re.search(r'972\d{9}|0\d[\d\-]{8,}', msg)
        if ph_m:
            pname = ph_m.group().replace("-", "")
        else:
            no_name += 1; failed += 1
            failures.append((msg, exp_uid, "שם/טלפון לא חולץ", "", ""))
            continue

    uid_f, name_f, fuzzy = find_user(pname)

    if not uid_f:
        no_user += 1; failed += 1
        failures.append((msg, exp_uid, f"לא נמצא (query={pname!r})", pname, ""))
        continue

    if uid_f == exp_uid:
        if fuzzy: confirmed += 1
        else:     passed   += 1
    else:
        wrong_uid += 1; failed += 1
        failures.append((msg, exp_uid, f"uid שגוי: {uid_f}={name_f!r}", pname, uid_f))

    if (i + 1) % 400 == 0:
        print(f"  {i+1}/{len(TESTS)} | ✅{passed} ⚠️{confirmed} ❌{failed}")

# ── דו"ח ─────────────────────────────────────────────────────────
total = len(TESTS)
ok    = passed + confirmed
print(f"\n{'='*60}")
print(f"📊 תוצאות: {total} בדיקות")
print(f"  ✅ נכון מדויק  : {passed:4d} ({100*passed/total:.1f}%)")
print(f"  ⚠️  נכון+fuzzy  :  {confirmed:4d} ({100*confirmed/total:.1f}%)")
print(f"  🎯 סה\"כ נמצאו  : {ok:4d} ({100*ok/total:.1f}%)")
print(f"  ❌ נכשל        :   {failed:4d} ({100*failed/total:.1f}%)")
print(f"     ↳ לא חולץ      : {no_name}")
print(f"     ↳ לא נמצא      : {no_user}")
print(f"     ↳ uid שגוי      : {wrong_uid}")

if failures:
    print("\n── 10 דוגמאות כשל ──")
    for msg, exp, reason, pname, uid_got in failures[:10]:
        print(f"  ❌ {reason}")
        print(f"     הודעה : {msg[:70]!r}")
        print(f"     query : {pname!r}  exp_uid={exp}")
