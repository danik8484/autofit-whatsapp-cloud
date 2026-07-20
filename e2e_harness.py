#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
e2e_harness.py — רתמת "פקודה שלמה", יבשה.

למה זו קיימת (19.7): הבדיקות הקודמות הריצו פונקציות מבודדות עם מאגר-מדומה שטוח.
הן עברו 151/151 בזמן שהזרימה האמיתית הייתה שבורה — קודקס תפס 16 כשלים בשני סבבים,
כולם במסלול שהמוקים עקפו (הפרסר, איחוד-ארוחות, סדר-הפעולות).

לכן כאן:
  • הפקודה נכנסת *בדיוק* כמו מדני — טקסט חופשי דרך execute_request.
  • התפריטים = צילום אמיתי של מתאמנים אמיתיים (fixtures/menus.json).
  • חיפוש-המזון = המאגר האמיתי, עם מטמון-דיסק (fixtures/foods.json) כדי שהריצה
    תהיה מהירה, יציבה, ולא תיחסם ב-429.
  • כתיבות *אינן* מבוצעות — הן נרשמות. אין דרך למחוק מזון מתפריט דרך ה-API,
    ולכן רתמה שכותבת באמת מלכלכת לקוחות אמיתיים ללא חזרה.

הרצה:  python3 e2e_harness.py            (כל המקרים)
        python3 e2e_harness.py --refresh  (רענון המטמון מהמאגר החי)
"""
import sys, os, json, re, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FIX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
MENUS_F = os.path.join(FIX_DIR, "menus.json")
FOODS_F = os.path.join(FIX_DIR, "foods.json")

import autofit_api as A

REFRESH = "--refresh" in sys.argv

# ─── מטמון חיפוש-מזון (מאגר אמיתי, נשמר לדיסק) ──────────────────────────────
_food_cache = {}
if os.path.exists(FOODS_F) and not REFRESH:
    _food_cache = json.load(open(FOODS_F, encoding="utf-8"))
_live_search = A.search_food
_new_lookups = []


def cached_search(query, coach_id="", _retry=0):
    key = (query or "").strip()
    if key in _food_cache:
        return _food_cache[key]
    res = _live_search(key, coach_id)
    _food_cache[key] = res
    _new_lookups.append(key)
    return res


# ─── תפריטים: צילום אמיתי ────────────────────────────────────────────────────
MENUS = json.load(open(MENUS_F, encoding="utf-8")) if os.path.exists(MENUS_F) else {}
_ACTIVE = {"uid": None}


def fake_get_user_meals(user_id):
    return json.loads(json.dumps(MENUS.get(str(user_id), [])))


def fake_find_user(name, *a, **k):
    uid = _ACTIVE["uid"]
    return (uid, _ACTIVE.get("name", "בדיקה"), False) if uid else None


# ─── לכידת כתיבות ────────────────────────────────────────────────────────────
WRITES = []
_live_post = A._post


def dry_post(path, body, _retry=0):
    if any(w in path for w in ("addUserSubmealFood", "addUserMealFood",
                               "updateMealFoodQuantity", "swap-training-exercise",
                               "training-exercises-sets")):
        WRITES.append({"path": path.rsplit("/", 1)[-1], "body": body})
        return {"status": True, "data": {}}
    return _live_post(path, body, _retry)


def install():
    A.search_food = cached_search
    A._post = dry_post
    A.get_user_meals = fake_get_user_meals
    A.find_user = fake_find_user


def save_cache():
    os.makedirs(FIX_DIR, exist_ok=True)
    json.dump(_food_cache, open(FOODS_F, "w", encoding="utf-8"), ensure_ascii=False)


# ─── הרצת מקרה ───────────────────────────────────────────────────────────────
PASS = FAIL = 0
FAILURES = []


def run_cmd(uid, text, name="בדיקה", **kw):
    """מריץ פקודה שלמה ומחזיר (תשובה, רשימת-כתיבות)."""
    _ACTIVE["uid"], _ACTIVE["name"] = str(uid), name
    WRITES.clear()
    A._uid_cache = {}
    A._food_cache = {}
    try:
        out = A.execute_request(text, force=True, name_override=name,
                                user_id_override=str(uid), **kw)
    except Exception as e:
        out = f"CRASH: {type(e).__name__}: {e}"
    return out, list(WRITES)


def _meal_name_of(uid, meal_id):
    for m in MENUS.get(str(uid), []):
        if str(m.get("id")) == str(meal_id):
            return m.get("meal_name", "")
    return ""


def case(desc, uid, text, *, writes=None, meal=None, meal_id=None, foods=None,
         write_checks=None, asks=None, no_write=False, contains=None, _kw=None):
    """desc      — תיאור
       writes    — מספר כתיבות צפוי
       meal      — שם הארוחה שאליה *חייבות* כל הכתיבות ללכת
       meal_id   — מזהה הארוחה; נדרש כשיש שתי ארוחות בעלות אותו שם
       foods     — שמות המזונות שנכתבו (סדר לא חשוב)
       write_checks — רשימת כתיבות לפי הסדר; כל איבר יכול לכלול path ו-body חלקי
       asks      — True אם מצופה שאלה (FOOD_OPTIONS / MEAL_OPTIONS)
       no_write  — True אם אסור שתהיה שום כתיבה
       contains  — מחרוזת שחייבת להופיע בתשובה"""
    global PASS, FAIL
    out, w = run_cmd(uid, text, **(_kw or {}))
    errs = []
    if no_write and w:
        errs.append(f"נכתבו {len(w)} כתיבות למרות שאסור: {[x['body'].get('sub_new_user_food_name') or x['body'].get('new_user_food_name') for x in w]}")
    if writes is not None and len(w) != writes:
        errs.append(f"כתיבות: {len(w)} במקום {writes}")
    if meal is not None:
        got = {_meal_name_of(uid, x["body"].get("sub_food_meal_id") or x["body"].get("food_meal_id")) for x in w}
        if got - {meal}:
            errs.append(f"ארוחה: נכתב ל-{got} במקום {meal!r}")
    if meal_id is not None:
        got_ids = {str(x["body"].get("sub_food_meal_id") or x["body"].get("food_meal_id")) for x in w}
        if got_ids != {str(meal_id)}:
            errs.append(f"מזהה ארוחה: נכתב ל-{got_ids} במקום {meal_id!r}")
    if foods is not None:
        got = sorted(x["body"].get("sub_new_user_food_name") or x["body"].get("new_user_food_name") or "?" for x in w)
        if got != sorted(foods):
            errs.append(f"מזונות: {got} במקום {sorted(foods)}")
    if write_checks is not None:
        if len(w) < len(write_checks):
            errs.append(f"אין מספיק כתיבות לבדיקת גוף: {len(w)} במקום {len(write_checks)}")
        else:
            for i, expected in enumerate(write_checks):
                actual = w[i]
                if expected.get("path") and actual.get("path") != expected["path"]:
                    errs.append(f"כתיבה {i+1}: path={actual.get('path')!r} במקום {expected['path']!r}")
                for key, value in (expected.get("body") or {}).items():
                    got_value = actual.get("body", {}).get(key)
                    if str(got_value) != str(value):
                        errs.append(f"כתיבה {i+1}: {key}={got_value!r} במקום {value!r}")
    if asks is not None:
        is_ask = bool(re.match(r'^(FOOD_OPTIONS|MEAL_OPTIONS|HINT_OPTIONS|MULTIMEAL)', out or ""))
        if is_ask != asks:
            errs.append(f"שאלה: {is_ask} במקום {asks}")
    if contains and contains not in (out or ""):
        errs.append(f"חסר בתשובה: {contains!r}")
    if errs:
        FAIL += 1
        FAILURES.append((desc, errs, (out or "")[:160]))
        print(f"  ❌ {desc}")
        for e in errs:
            print(f"       {e}")
    else:
        PASS += 1
        print(f"  ✅ {desc}")
    return out, w


def summary():
    save_cache()
    print("\n" + "═" * 58)
    print(f"תוצאה: {PASS} עברו, {FAIL} נכשלו")
    if _new_lookups:
        print(f"(נוספו {len(_new_lookups)} חיפושים למטמון)")
    if FAIL:
        print("⛔ אל תעלה לפני שהכל עובר")
        return 1
    print("✅ הכל עובר")
    return 0
