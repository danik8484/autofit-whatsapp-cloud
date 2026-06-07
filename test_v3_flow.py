#!/usr/bin/env python3
"""
test_v3_flow.py — מדמה שיחות V3 מלאות
  Dani שולח הודעה יוצאת ללקוח → בוט מזהה → autofit → קבלה לקבוצה.

  חלק A: trigger word detection     — לוגיקה טהורה, ללא API
  חלק B: parse_message לפקודות V3  — ללא API (mock)
  חלק C: subprocess calls           — API אמיתי, בדיקת סוג תגובה
  חלק D: formatBizReceipt           — לוגיקה טהורה
"""
import sys, os, subprocess, time, datetime
sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("AUTOFIT_TOKEN",    "fake_token")
os.environ.setdefault("AUTOFIT_COACH_ID", "9999")
from autofit_api import parse_message

PASS = 0; FAIL = 0; FAILS = []

# ─── helpers ─────────────────────────────────────────────────────────────────

def chk(label, msg, *, food=None, hint=None, meal=None, grams=None, reduce=None):
    global PASS, FAIL
    p = parse_message(msg)
    errors = []
    ch  = p.get("change", "")
    ops = p.get("ops", [])
    all_ch = ch + " " + " ".join(o.get("change", "") for o in ops)

    if food is not None and food.lower() not in all_ch.lower():
        errors.append(f"food: ציפיתי '{food}' ב-change='{ch}'")
    if hint is not None and hint.lower() not in all_ch.lower():
        errors.append(f"hint: ציפיתי '{hint}' ב-change='{ch}'")
    if meal is not None:
        got_meal = p.get("meal", "") or ""
        meals = p.get("meals") or ([got_meal] if got_meal else [])
        if not any(meal.lower() in m.lower() for m in meals):
            errors.append(f"meal: ציפיתי '{meal}' ב-{meals}")
    if grams is not None:
        got_g = str(p.get("extra_grams") or "")
        ops_g = " ".join(str(o.get("extra_grams", "")) for o in ops)
        if str(grams) not in got_g and str(grams) not in ops_g and str(grams) not in all_ch:
            errors.append(f"grams: ציפיתי '{grams}', extra_grams='{got_g}'")
    if reduce is not None:
        got_r = bool(p.get("reduce")) or any(o.get("reduce") for o in ops)
        if got_r != reduce:
            errors.append(f"reduce: ציפיתי {reduce} קיבלתי {got_r}")

    if not errors:
        PASS += 1; print(f"  ✅ {label}")
    else:
        FAIL += 1; FAILS.append(label)
        print(f"  ❌ {label}")
        for e in errors: print(f"       {e}")


def run_biz(contact_name, command, **kwargs):
    """מריץ autofit_api.py בדיוק כמו שV3 מריץ אותו"""
    args = ['python3', 'autofit_api.py', '--force', '--name', contact_name, command]
    for k, v in kwargs.items():
        args += [f'--{k.replace("_","-")}', str(v)]
    env = {k: v for k, v in os.environ.items() if k != 'AUTOFIT_TOKEN'}
    r = subprocess.run(args, capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.abspath(__file__)), env=env)
    return r.stdout.strip()


def chk_run(label, raw, *, starts=None, contains=None, one_of=None):
    global PASS, FAIL
    errors = []
    if starts and not raw.startswith(starts):
        errors.append(f"starts='{starts}', קיבלתי='{raw[:70]}'")
    if contains and contains not in raw:
        errors.append(f"חסר '{contains}' ב-'{raw[:70]}'")
    if one_of and not any(raw.startswith(p) for p in one_of):
        errors.append(f"ציפיתי אחד מ-{one_of}, קיבלתי '{raw[:70]}'")
    if not errors:
        PASS += 1; print(f"  ✅ {label}")
    else:
        FAIL += 1; FAILS.append(label)
        print(f"  ❌ {label}")
        for e in errors: print(f"       {e}")


# ═════════════════════════════════════════════════════════════════════════════
# A — Trigger Word Detection
# ═════════════════════════════════════════════════════════════════════════════
print("\n══ A — Trigger Word Detection ══")

TRIGGER_WORDS = [
    'מוסיף לך', 'מוסיף לו', 'מעלה לך', 'מעלה לו',
    'מוריד לך', 'מפחית', 'מחליף לך', 'מחליף לו',
]
def has_trigger(text):
    return any(w in text for w in TRIGGER_WORDS)

triggers = [
    ("A.01 מוסיף לך",         "מוסיף לך 50 גרם אורז בצהריים",            True),
    ("A.02 מוסיף לו",         "מוסיף לו שיבולת שועל בבוקר",              True),
    ("A.03 מעלה לך",          "מעלה לך 30 גרם אורז",                      True),
    ("A.04 מעלה לו",          "מעלה לו 50 גרם חזה עוף בערב",              True),
    ("A.05 מוריד לך",         "מוריד לך 20 גרם פסטה בצהריים",             True),
    ("A.06 מפחית",            "מפחית 40 גרם אורז בצהריים",               True),
    ("A.07 מחליף לך",         "מחליף לך חזה עוף ב פרגיות",               True),
    ("A.08 מחליף לו",         "מחליף לו טונה במקום חזה עוף בערב",        True),
    ("A.09 הודעה רגילה",       "מה שלומך? מה אכלת היום?",                  False),
    ("A.10 מוסיף בלבד",       "מוסיף עוד ספרינטים מחר",                   False),
    ("A.11 במקום בלבד",       "אכלת ביצה במקום הטוסט?",                   False),
    ("A.12 טריגר באמצע משפט", "כן מוסיף לך 100 גרם חזה עוף לערב",       True),
    ("A.13 מעלה לך עם הסבר",  "מעלה לך קצת שומן בריא בבוקר",             True),
    ("A.14 מוריד לו",         "מוריד לו 30 גרם אורז בצהריים",             False),  # לו לא ברשימה
]

for label, text, expected in triggers:
    got = has_trigger(text)
    if got == expected:
        PASS += 1; print(f"  ✅ {label}")
    else:
        FAIL += 1; FAILS.append(label)
        print(f"  ❌ {label}  (ציפיתי {expected}, קיבלתי {got})")


# ═════════════════════════════════════════════════════════════════════════════
# B — parse_message לפקודות V3 (ללא API)
# ═════════════════════════════════════════════════════════════════════════════
print("\n══ B — Parse פקודות V3 ══")

print("\n  ─ מוסיף לך / לו ─")
chk("B.01 מוסיף לך 50g אורז בצהריים",
    "מוסיף לך 50 גרם אורז בצהריים",             food="אורז",         meal="צהריים", grams="50")
chk("B.02 מוסיף לו 100g חזה עוף בערב",
    "מוסיף לו 100 גרם חזה עוף בערב",            food="חזה עוף",      meal="ערב",    grams="100")
chk("B.03 מוסיף לך שיבולת שועל בבוקר (ללא גרמים)",
    "מוסיף לך שיבולת שועל בבוקר",               food="שיבולת שועל",  meal="בוקר")
chk("B.04 מוסיף לך 80g ביצים (ללא ארוחה)",
    "מוסיף לך 80 גרם ביצים",                    food="ביצ",           grams="80")
chk("B.05 מוסיף לך 100g חזה עוף מבושל לצהריים",
    "מוסיף לך 100 גרם חזה עוף מבושל לצהריים",  food="חזה עוף",      meal="צהריים", grams="100")

print("\n  ─ מחליף לך / לו ─")
chk("B.06 מחליף לך חזה עוף ב אורז מבושל בערב",
    "מחליף לך חזה עוף ב אורז מבושל בערב",      food="אורז",   hint="חזה עוף",  meal="ערב")
chk("B.07 מחליף לו טונה במקום חזה עוף בצהריים",
    "מחליף לו טונה במקום חזה עוף בצהריים",     food="טונה",   hint="חזה עוף",  meal="צהריים")
chk("B.08 מוסיף לך אופציה של שיבולת שועל בבוקר",
    "מוסיף לך אופציה של שיבולת שועל בבוקר",    food="שיבולת שועל",  meal="בוקר")
chk("B.09 מוסיף לך ביצים כתחליף לטונה בבוקר",
    "מוסיף לך ביצים כתחליף לטונה בבוקר",       food="ביצ",    hint="טונה",     meal="בוקר")
chk("B.10 מוסיף לו אופציה של ביצים כתחליף לטונה",
    "מוסיף לו אופציה של ביצים כתחליף לטונה בבוקר", food="ביצ", hint="טונה",   meal="בוקר")
chk("B.11 מוסיף לך שיבולת שועל באופציה של לחם בבוקר",
    "מוסיף לך שיבולת שועל באופציה של הלחם בבוקר", food="שיבולת שועל", hint="לחם", meal="בוקר")

print("\n  ─ מוריד לך / לו ─")
chk("B.12 מוריד לך 30g פסטה בצהריים",
    "מוריד לך 30 גרם פסטה בצהריים",             food="פסטה",  meal="צהריים", grams="30",  reduce=True)
chk("B.13 מוריד לו 20g מהאורז בבוקר",
    "מוריד לו 20 גרם מהאורז בבוקר",             food="אורז",  meal="בוקר",   grams="20",  reduce=True)
chk("B.14 מוריד לך 20g מהאורז בבוקר",
    "מוריד לך 20 גרם מהאורז בבוקר",             food="אורז",  meal="בוקר",   grams="20",  reduce=True)
chk("B.15 מפחית 50g אורז בצהריים",
    "מפחית 50 גרם אורז בצהריים",                food="אורז",  meal="צהריים", grams="50",  reduce=True)

print("\n  ─ מעלה לך / לו ─")
chk("B.16 מעלה לך 50g אורז בצהריים",
    "מעלה לך 50 גרם אורז בצהריים",              food="אורז",     meal="צהריים", grams="50")
chk("B.17 מעלה לו 100g חזה עוף בערב",
    "מעלה לו 100 גרם חזה עוף בערב",             food="חזה עוף",  meal="ערב",    grams="100")
chk("B.18 מעלה לך 30g ביצים בבוקר",
    "מעלה לך 30 גרם ביצים בבוקר",               food="ביצ",      meal="בוקר",   grams="30")

print("\n  ─ פורמטים שונים ─")
chk("B.19 גרמים לפני מזון (פורמט רגיל)",
    "מוסיף לך 50 גרם אורז בצהריים",             food="אורז",    meal="צהריים", grams="50")
chk("B.20 ל + ארוחה",
    "מוסיף לך 100 גרם חזה עוף לצהריים",         food="חזה עוף", meal="צהריים", grams="100")
chk("B.21 כן + טריגר באמצע",
    "כן מוסיף לך 100 גרם חזה עוף מבושל לערב",  food="חזה עוף", meal="ערב",    grams="100")


# ═════════════════════════════════════════════════════════════════════════════
# C — subprocess (API אמיתי) — בדיקת סוג תגובה
# ═════════════════════════════════════════════════════════════════════════════
print("\n══ C — Subprocess / API ══")

print("\n  ─ שם לא נמצא → NAME_NOT_FOUND ─")
r = run_biz("לקוח_לא_קיים_XXXXXX", "מוסיף לך אורז בצהריים")
chk_run("C.01 שם לא קיים → NAME_NOT_FOUND", r, starts="NAME_NOT_FOUND:")

r = run_biz("John_Doesnt_Exist_999", "מוסיף לך חזה עוף בערב")
chk_run("C.02 שם אנגלי לא קיים → NAME_NOT_FOUND", r, starts="NAME_NOT_FOUND:")

print("\n  ─ שם חלקי / fuzzy → CONFIRM_WITH_NAME ─")
time.sleep(0.5)
r = run_biz("רון", "מוסיף לך אורז בצהריים")
chk_run("C.03 'רון' בלבד → CONFIRM / NOT_FOUND / שלח שם מלא",
        r, one_of=["CONFIRM_WITH_NAME:", "NAME_NOT_FOUND:", "❌"])

print("\n  ─ מזון לא נמצא → FOOD_OPTIONS ─")
time.sleep(0.5)
r = run_biz("רון וליצקו", "מוסיף לך בנגבצקיבוצקי בצהריים")
chk_run("C.04 מזון לא קיים → FOOD_OPTIONS / not found",
        r, one_of=["FOOD_OPTIONS:", "לא מצאתי"])

print("\n  ─ פקודות שלמות → ✅ / OPTIONS ─")
time.sleep(0.5)

r = run_biz("רון וליצקו", "מוסיף לך קינואה בצהריים")
chk_run("C.05 מוסיף לך קינואה → ✅ / OPTIONS",
        r, one_of=["✅", "FOOD_OPTIONS:", "CONFIRM_WITH_NAME:"])

time.sleep(0.5)
r = run_biz("רון וליצקו", "מוריד לך 20 גרם מהאורז בצהריים")
chk_run("C.06 מוריד לך אורז → ✅ / OPTIONS",
        r, one_of=["✅", "FOOD_OPTIONS:", "MEAL_OPTIONS:"])

time.sleep(0.5)
r = run_biz("רון וליצקו", "מעלה לך 30 גרם ביצים בבוקר")
chk_run("C.07 מעלה לך ביצים → ✅ / OPTIONS",
        r, one_of=["✅", "FOOD_OPTIONS:", "MEAL_OPTIONS:"])

time.sleep(0.5)
r = run_biz("רון וליצקו", "מוסיף לך ביצים כתחליף לטונה בבוקר")
chk_run("C.08 מוסיף תחליף → ✅ / OPTIONS",
        r, one_of=["✅", "FOOD_OPTIONS:", "HINT_OPTIONS:"])

time.sleep(0.5)
r = run_biz("רון וליצקו", "מחליף לך חזה עוף ב פרגיות בצהריים")
chk_run("C.09 מחליף לך → ✅ / OPTIONS",
        r, one_of=["✅", "FOOD_OPTIONS:", "HINT_OPTIONS:", "MEAL_OPTIONS:"])


# ═════════════════════════════════════════════════════════════════════════════
# D — formatBizReceipt (לוגיקה טהורה)
# ═════════════════════════════════════════════════════════════════════════════
print("\n══ D — formatBizReceipt ══")

def format_biz_receipt(contact_name, autofit_result):
    tz = datetime.timezone(datetime.timedelta(hours=3))
    t = datetime.datetime.now(tz).strftime("%H:%M")
    return f"👤 *{contact_name}* | 🕐 {t}\n{autofit_result}"

r1 = format_biz_receipt("רון וליצקו", "✅ נוסף אורז (50g) לצהריים")
ok = "👤 *רון וליצקו*" in r1 and "🕐" in r1 and "אורז" in r1
if ok: PASS += 1; print("  ✅ D.01 receipt: שם + שעה + תוכן")
else:  FAIL += 1; FAILS.append("D.01"); print(f"  ❌ D.01: {r1}")

r2 = format_biz_receipt("אורן פורמן", "FOOD_OPTIONS:קינואה||קינואה מבושלת|קינואה גולמית")
ok = "👤 *אורן פורמן*" in r2
if ok: PASS += 1; print("  ✅ D.02 receipt עם OPTIONS — שם תמיד מוצג")
else:  FAIL += 1; FAILS.append("D.02"); print(f"  ❌ D.02: {r2}")

r3 = format_biz_receipt("דני כהן", "✅ הופחתו 20g מפסטה (צהריים: 180g → 160g)")
ok = "👤 *דני כהן*" in r3 and "פסטה" in r3
if ok: PASS += 1; print("  ✅ D.03 receipt הפחתה")
else:  FAIL += 1; FAILS.append("D.03"); print(f"  ❌ D.03: {r3}")


# ─── סיכום ───────────────────────────────────────────────────────────────────
total = PASS + FAIL
icon = "✅" if FAIL == 0 else "❌"
print(f"\n══ סיכום: {PASS}/{total} עברו {icon} ══")
if FAILS:
    print("  נכשלו:")
    for f in FAILS: print(f"    • {f}")
    sys.exit(1)
