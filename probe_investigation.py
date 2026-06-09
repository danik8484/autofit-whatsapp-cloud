#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_investigation.py — חקירת באגים/כשלים. דני(23203) בלבד, ללא שינוי נתונים.
מיירט כתיבות, מציג parse + כתיבות + סטטוס לכל הודעה.
מטרה: למצוא היכן הבוט לא יבין / יבצע חלקית / יכשל בשקט.
"""
import sys, warnings
warnings.filterwarnings("ignore")
sys.argv = ['x']
import autofit_api as a

WRITES = []
_real_post = a._post
def _spy(path, body, _retry=0):
    if any(w in path for w in ('addUserSubmealFood','addUserMealFood','updateMealFoodQuantity','swap-training-exercise')):
        WRITES.append({"path": path, "body": body})
        return {"status": True, "message": "ok", "data": {}}
    return _real_post(path, body, _retry)
a._post = _spy

DANI = "23203"
PHONE = "972547198498"

def run(label, cmd):
    WRITES.clear()
    try:
        out = a.execute_request(cmd, force=True, name_override=PHONE, user_id_override=DANI)
    except Exception as e:
        import traceback; traceback.print_exc(file=sys.stderr)
        out = f"💥EXCEPTION: {type(e).__name__}: {e}"
    writes = []
    for x in WRITES:
        b = x["body"]
        if 'Submeal' in x["path"]:
            writes.append(f"תחליף {b.get('sub_new_user_food_name')!r} {b.get('sub_new_user_food_gram_value')}g")
        elif 'addUserMealFood' in x["path"]:
            writes.append(f"הוסף {b.get('new_user_food_name')!r} {b.get('new_user_food_gram_value')}g →meal {b.get('food_meal_id')}")
        elif 'updateMealFoodQuantity' in x["path"]:
            writes.append(f"עדכן qty={b.get('quantity')}")
        elif 'swap' in x["path"]:
            writes.append(f"swap-ex aid={b.get('assignment_id')}→{b.get('new_exercise_id')}")
    line0 = out.split(chr(10))[0][:90]
    is_ok = out.startswith("✅") or any(k in out for k in ("נוסף","עודכן","הופחת","הוחלף","החלפ"))
    is_opt = any(out.startswith(p) for p in ("FOOD_OPTIONS","HINT_OPTIONS","MEAL_OPTIONS","MULTIMEAL","NAME_OPTIONS","CONFIRM","EXERCISE"))
    if "💥" in out: tag="💥CRASH"
    elif is_ok and not WRITES: tag="🔴SILENT-FAIL"
    elif is_ok: tag=f"✅ ({len(WRITES)} כתיבות)"
    elif is_opt: tag="❓בחירה"
    else: tag="⚠️לא-בוצע"
    print(f"\n[{label}] {tag}")
    print(f"  IN : {cmd}")
    print(f"  OUT: {line0}")
    for w in writes: print(f"  → {w}")
    return tag, len(WRITES)

print("="*78)
print("חקירת באגים — דני (23203). ⚠️ כל הכתיבות מיורטות, שום נתון לא משתנה.")
print("="*78)

# ── הדוגמה הקיצונית של דני ───────────────────────────────────────
print("\n########## ריבוי משימות קיצוני ##########")
run("דוגמת דני המקורית", "תחליף לי אורז בבורגול ותוסיף לאורז 50 גרם ובארוחת בוקר תוסיף ריבה")
run("3 פעולות מפורש ו+פועל", "תחליף אורז בבורגול ותוסיף ריבה בבוקר ותוריד 20 גרם מהאורז")
run("פסיקים", "תוסיף ריבה בבוקר, תחליף אורז בבורגול, תוסיף 30 גרם חלבון")
run("ארוחות שונות בכל פעולה", "תוסיף ביצה בבוקר ותוסיף אורז בצהריים ותוסיף קוטג בערב")
run("4 פעולות", "תוסיף ריבה בבוקר ותוסיף חמאה בבוקר ותחליף אורז בקינואה ותוריד 10 גרם מהלחם")

# ── גוף ראשון / ניסוחים לא רגילים ────────────────────────────────
print("\n########## ניסוחים ##########")
run("לי (גוף ראשון)", "תחליף לי את האורז בבורגול")
run("בלי פועל ברור", "אורז בבורגול בצהריים")
run("שאלה+פעולה", "מה יש לו בבוקר? תוסיף ריבה")
run("פועל בסוף", "אורז 50 גרם תוסיף בצהריים")
run("עברית מדוברת", "אחי תזרוק לו עוד 50 גרם אורז בצהריים")
run("סלנג שים", "שים לו בבוקר חביתה")
run("עדכן", "עדכן את האורז ל100 גרם בצהריים")

# ── יחידות / כמויות ──────────────────────────────────────────────
print("\n########## יחידות וכמויות ##########")
run("כף", "תוסיף כף שמן זית בערב")
run("כפית", "תוסיף כפית דבש בבוקר")
run("חצי כוס", "תוסיף חצי כוס אורז בצהריים")
run("2 ביצים", "תוסיף 2 ביצים בבוקר")
run("קילו", "תוסיף קילו עוף בצהריים")
run("יחידה", "תוסיף יחידה אחת בננה בבוקר")

# ── מקרי קצה מזון/ארוחה ──────────────────────────────────────────
print("\n########## מזון/ארוחה ##########")
run("מזון לא קיים בתפריט להחלפה", "תחליף את הפיצה בסלט בערב")
run("ארוחה לא קיימת", "תוסיף ריבה בארוחת אחהצ")
run("מזון עמום", "תוסיף גבינה בבוקר")
run("בלי ארוחה כלל", "תוסיף ריבה")

print("\n" + "="*78)
print("סיום probe.")
