#!/usr/bin/env python3
"""
audit_routing.py — בדיקת ניתוב יסודית. דני (23203) ורון (22982) בלבד.
מיירט כתיבות ל-autofit (add/update) אך משתמש בקריאות קריאה אמיתיות
(get_user_meals, search_food) — כך שזיהוי מזון/ארוחה/גרמים אמיתי לחלוטין,
בלי לשנות שום נתון אמיתי. מדווח לכל ניסוח מה היה קורה בפועל.
"""
import sys
sys.argv = ['x']
import autofit_api as a

WRITES = []
_real_post = a._post
def _spy_post(path, body, _retry=0):
    if any(w in path for w in ('addUserSubmealFood', 'addUserMealFood', 'updateMealFoodQuantity')):
        WRITES.append({"path": path, "body": body})
        return {"status": True, "message": "ok", "data": {}}
    return _real_post(path, body, _retry)
a._post = _spy_post

DANI = "23203"   # 0547198498
RON  = "22982"   # רון וליצקו

PHONE = {"23203":"972547198498", "22982":"972539598622"}
def run(label, cmd, user_id):
    WRITES.clear()
    try:
        out = a.execute_request(cmd, force=True, name_override=PHONE[user_id], user_id_override=user_id)
    except Exception as e:
        import traceback; traceback.print_exc(file=sys.stderr)
        out = f"EXCEPTION: {type(e).__name__}: {e}"
    w = []
    for x in WRITES:
        b = x["body"]
        if 'Submeal' in x["path"]:
            w.append(f"SUB {b.get('sub_new_user_food_name')!r} gram={b.get('sub_new_user_food_gram_value')} →parent_id={b.get('new_meal_food_id')}")
        elif 'addUserMealFood' in x["path"]:
            w.append(f"ADD {b.get('new_user_food_name')!r} gram={b.get('new_user_food_gram_value')} →meal={b.get('food_meal_id')}")
        elif 'updateMealFoodQuantity' in x["path"]:
            w.append(f"UPDATE meal_food_id={b.get('food_id')} qty={b.get('quantity')}")
    is_ok = out.startswith("✅") or any(k in out for k in ("נוסף","עודכן","הופחת","החליף"))
    is_opt = any(out.startswith(p) for p in ("FOOD_OPTIONS","HINT_OPTIONS","MEAL_OPTIONS","MULTIMEAL","NAME_OPTIONS"))
    status = "✅ OK" if is_ok else ("❓ דורש בחירה" if is_opt else "⚠️ בעיה")
    print(f"\n[{label}]  {status}")
    print(f"  פקודה: {cmd}")
    print(f"  פלט:   {out.split(chr(10))[0][:95]}")
    for ws in w: print(f"  כתיבה: {ws}")
    if is_ok and not w:
        print("  🔴🔴 אמר הצלחה אבל אין כתיבה בפועל — כשל שקט!")

print("="*72)
meals = a.get_user_meals(DANI)
print(f"תפריט דני (23203) — {len(meals)} ארוחות:")
for m in meals:
    fs = [f.get("food_name","") for f in (m.get("mealFoods") or m.get("new_meal_food") or [])]
    print(f"  • {m.get('meal_name','').strip()}: {', '.join(fs) or '(ריק)'}")
print("="*72)

for label, cmd in [
    ("substitution כאופציה", "אני מוסיף לך אורז מלא מבושל כאופציה לאורז לאחר בישול בצהריים"),
    ("add עם גרמים",         "אני מוסיף לך 50 גרם שיבולת שועל עבה בבוקר"),
    ("reduce גרמים",         "אני מוריד לך 50 גרם מהאורז לאחר בישול בצהריים"),
    ("replace ב-",           "אני מחליף לך אורז לאחר בישול בתפוח אדמה אפוי בצהריים"),
    ("increase גרמים",       "אני מעלה לך 40 גרם לאורז לאחר בישול בצהריים"),
    ("past add שמתי",        "שמתי לך 30 גרם טחינה גולמית בבוקר"),
    ("past reduce הורדתי",   "הורדתי לך 20 גרם מהאורז לאחר בישול בצהריים"),
    ("past sub הוספתי",      "הוספתי לך בטטה אפויה כאופציה לאורז לאחר בישול בצהריים"),
    ("change משנה",          "אני משנה לך אורז לאחר בישול בקינואה מבושלת בצהריים"),
    ("מכניס לך",             "אני מכניס לך 100 גרם תפוח אדמה אפוי בצהריים"),
    ("בלי ארוחה (hint)",     "אני מוסיף לך קינואה מבושלת כאופציה לאורז לאחר בישול"),
]:
    run(label, cmd, DANI)

print("\n" + "="*72)
meals_r = a.get_user_meals(RON)
print(f"תפריט רון (22982) — {len(meals_r)} ארוחות:")
for m in meals_r:
    fs = [f.get("food_name","") for f in (m.get("mealFoods") or m.get("new_meal_food") or [])]
    print(f"  • {m.get('meal_name','').strip()}: {', '.join(fs) or '(ריק)'}")
print("="*72)

for label, cmd in [
    ("substitution כאופציה", "אני מוסיף לך טונה במים כאופציה לחזה עוף מבושל בצהריים"),
    ("add גרמים",            "אני מוסיף לך 90 גרם פסטה מבושלת בצהריים"),
    ("reduce",               "אני מוריד לך 30 גרם מחזה עוף מבושל בצהריים"),
    ("replace",              "אני מחליף לך חזה עוף מבושל בהודו טחון בצהריים"),
    ("increase",             "אני מעלה לך 60 גרם לחזה עוף מבושל בצהריים"),
]:
    run(label, cmd, RON)

print("\n" + "="*72)
print("סיום — כל הכתיבות יורטו. לא שונה שום דבר בתפריטים האמיתיים.")
print("="*72)
