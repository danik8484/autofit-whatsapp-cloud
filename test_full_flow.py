"""
50 סימולציות מלאות — mock API, תשובה מלאה כמו הבוט האמיתי.
כולל: שמות עם טעויות, ביטויי בישול, ארוחות שגויות, פורמטים שונים,
       flow של אישור (כן/לא), ניסיונות להכשיל את הבוט.
"""
import sys, re
sys.path.insert(0, ".")

# ── Mock Data ──────────────────────────────────────────────────────────────────
MOCK_USERS = [
    {"id": 22982, "name": "רון וליצקו"},
    {"id": 12345, "name": "דני קגנוביץ"},
    {"id": 99991, "name": "רון כהן"},
    {"id": 99992, "name": "מוריה שלו"},
    {"id": 99993, "name": "נבו כהן"},
    {"id": 99994, "name": "יובל לוי"},
]

MOCK_MEALS = {
    22982: [  # רון וליצקו
        {"id": 1001, "meal_name": "ארוחת בוקר", "new_meal_food": [
            {"id": 201, "food_name": "ביצה קשה בינונית", "calories": 78, "protein": 6, "carbs": 1, "fat": 5, "grams": 50},
            {"id": 202, "food_name": "תמר מג'הול",       "calories": 66, "protein": 0, "carbs": 18, "fat": 0, "grams": 25},
            {"id": 203, "food_name": "שקדים",             "calories": 164, "protein": 6, "carbs": 6, "fat": 14, "grams": 28},
        ]},
        {"id": 1002, "meal_name": "ארוחת צהריים", "new_meal_food": [
            {"id": 204, "food_name": "שיבולת שועל",          "calories": 150, "protein": 5, "carbs": 27, "fat": 3, "grams": 40},
            {"id": 205, "food_name": "חמאת בוטנים",          "calories": 188, "protein": 8, "carbs": 6, "fat": 16, "grams": 32},
            {"id": 206, "food_name": "בננה",                 "calories": 89, "protein": 1, "carbs": 23, "fat": 0, "grams": 100},
            {"id": 207, "food_name": "חזה עוף לפני בישול",   "calories": 120, "protein": 22, "carbs": 0, "fat": 3, "grams": 100},
        ]},
        {"id": 1003, "meal_name": "ארוחת ערב", "new_meal_food": [
            {"id": 208, "food_name": "אורז לא מבושל",   "calories": 365, "protein": 7, "carbs": 80, "fat": 1, "grams": 100},
            {"id": 209, "food_name": "שמן זית",         "calories": 119, "protein": 0, "carbs": 0, "fat": 14, "grams": 13},
            {"id": 210, "food_name": "חזה עוף מבושל",   "calories": 165, "protein": 31, "carbs": 0, "fat": 4, "grams": 100},
        ]},
    ],
    12345: [  # דני
        {"id": 2001, "meal_name": "ארוחת בוקר", "new_meal_food": [
            {"id": 301, "food_name": "ביצה קשה",      "calories": 78, "protein": 6, "carbs": 1, "fat": 5, "grams": 50},
            {"id": 302, "food_name": "לחם מחיטה מלאה","calories": 69, "protein": 3, "carbs": 12, "fat": 1, "grams": 30},
        ]},
        {"id": 2002, "meal_name": "ארוחת ערב", "new_meal_food": [
            {"id": 303, "food_name": "דג סלמון מבושל","calories": 208, "protein": 20, "carbs": 0, "fat": 13, "grams": 100},
            {"id": 304, "food_name": "בטטה מבושלת",   "calories": 86, "protein": 2, "carbs": 20, "fat": 0, "grams": 100},
        ]},
    ],
}

MOCK_FOOD_DB = [
    {"id": 501, "food_name": "טונה בשמן זית",        "calories": 120, "protein": 20, "carbs": 0, "fat": 5, "grams": 100},
    {"id": 502, "food_name": "טונה במים",             "calories": 90,  "protein": 20, "carbs": 0, "fat": 1, "grams": 100},
    {"id": 503, "food_name": "חזה עוף מבושל",         "calories": 165, "protein": 31, "carbs": 0, "fat": 4, "grams": 100},
    {"id": 504, "food_name": "חזה עוף לפני בישול",    "calories": 120, "protein": 22, "carbs": 0, "fat": 3, "grams": 100},
    {"id": 505, "food_name": "ביצה קשה",              "calories": 78,  "protein": 6,  "carbs": 1, "fat": 5, "grams": 50},
    {"id": 506, "food_name": "קוטג 3 אחוז",           "calories": 98,  "protein": 11, "carbs": 4, "fat": 3, "grams": 100},
    {"id": 507, "food_name": "שיבולת שועל",           "calories": 389, "protein": 17, "carbs": 66, "fat": 7, "grams": 100},
    {"id": 508, "food_name": "אנטריקוט מבושל",        "calories": 271, "protein": 26, "carbs": 0,  "fat": 18, "grams": 100},
    {"id": 509, "food_name": "דג סלמון מבושל",        "calories": 208, "protein": 20, "carbs": 0,  "fat": 13, "grams": 100},
    {"id": 510, "food_name": "אורז מלא מבושל",        "calories": 111, "protein": 2,  "carbs": 23, "fat": 1, "grams": 100},
    {"id": 511, "food_name": "יוגורט 3 אחוז",         "calories": 59,  "protein": 5,  "carbs": 5,  "fat": 3, "grams": 100},
    {"id": 512, "food_name": "יוגורט 0 אחוז",         "calories": 40,  "protein": 5,  "carbs": 5,  "fat": 0, "grams": 100},
    {"id": 513, "food_name": "בננה",                   "calories": 89,  "protein": 1,  "carbs": 23, "fat": 0, "grams": 100},
    {"id": 514, "food_name": "חמאת בוטנים",           "calories": 590, "protein": 25, "carbs": 20, "fat": 50, "grams": 100},
]

# ── Patches ────────────────────────────────────────────────────────────────────
import autofit_api as api

def mock_post(path, body):
    if path == "/coach/get-coach-users":
        return {"data": MOCK_USERS, "pagination": {"total": len(MOCK_USERS)}}
    if path == "/coach/v2-getAllUserMeals":
        uid = int(body.get("user_id", 0))
        meals = MOCK_MEALS.get(uid, [])
        return {"data": {"new_meals": meals}}
    if path == "/coach/v2-addUserSubmealFood":
        return {"status": True, "message": "ok"}
    if path == "/coach/v2-addUserMealFood":
        return {"status": True, "message": "ok"}
    return {}

def mock_search_food(query, coach_id=""):
    q = api.normalize_food_query(query).lower()
    results = [f for f in MOCK_FOOD_DB if q in f["food_name"].lower() or f["food_name"].lower() in q]
    if not results:
        first = q.split()[0] if q.split() else q
        results = [f for f in MOCK_FOOD_DB if first in f["food_name"].lower()]
    return results[:5]

api._post         = mock_post
api.search_food   = mock_search_food
api._uid_cache    = {}

# ── סימולציית flow כולל אישור ─────────────────────────────────────────────────
def simulate(msg, confirm_with=None):
    """
    מריץ הודעה דרך execute_request.
    אם מקבל CONFIRM/CONFIRM_FUZZY — מחכה ל-confirm_with ("כן"/"לא").
    מחזיר מה הבוט יגיד בסופו של דבר.
    """
    api._uid_cache = {}  # cache חדש לכל בדיקה
    result = api.execute_request(msg)

    if result.startswith("CONFIRM_FUZZY:"):
        found_name = result.split(":", 1)[1].strip()
        bot_question = f"❓ האם התכוונת ל: *{found_name}*?\n\nשלח *כן* לאישור או *לא* לביטול"
        if confirm_with == "כן":
            result2 = api.execute_request(msg, force=True, name_override=found_name)
            return bot_question, result2
        elif confirm_with == "לא":
            return bot_question, "❌ בוטל"
        return bot_question, "(ללא תשובה)"

    if result.startswith("CONFIRM:"):
        summary = result.split(":", 1)[1].strip()
        bot_question = f"❓ הבנתי:\n{summary}\n\nנכון? שלח *כן* לאישור או *לא* לביטול"
        if confirm_with == "כן":
            result2 = api.execute_request(msg, force=True)
            return bot_question, result2
        elif confirm_with == "לא":
            return bot_question, "❌ בוטל"
        return bot_question, "(ללא תשובה)"

    return result, None

# ── 50 בדיקות ─────────────────────────────────────────────────────────────────
TESTS = [
    # ══ פורמט מובנה תקין ══
    ("01 — מובנה בסיסי — ערב",
     "שם: רון וליצקו\nארוחה: ערב\nשנה: הוסף (טונה בשמן זית) במקום (חזה עוף מבושל)", None),
    ("02 — מובנה — בוקר",
     "שם: רון וליצקו\nארוחה: בוקר\nשנה: הוסף (קוטג 3 אחוז) במקום (ביצה קשה בינונית)", None),
    ("03 — מובנה — צהריים",
     "שם: רון וליצקו\nארוחה: צהריים\nשנה: הוסף (טונה בשמן זית) במקום (שיבולת שועל)", None),
    ("04 — מובנה — דני ערב",
     "שם: דני קגנוביץ\nארוחה: ערב\nשנה: הוסף (טונה בשמן זית) במקום (דג סלמון מבושל)", None),
    ("05 — מובנה — הוסף בלי במקום",
     "שם: רון וליצקו\nארוחה: ערב\nשנה: הוסף (אנטריקוט מבושל)", None),

    # ══ ה' הידיעה ══
    ("06 — ה' הידיעה — החזה עוף המבושל",
     "שם: רון וליצקו\nארוחה: ערב\nשנה: הוסף (טונה בשמן זית) במקום (החזה עוף המבושל)", None),
    ("07 — ה' הידיעה — השיבולת שועל",
     "שם: רון וליצקו\nארוחה: צהריים\nשנה: הוסף (טונה בשמן זית) במקום (השיבולת שועל)", None),

    # ══ ביטויי בישול ══
    ("08 — לאחר בישול = מבושל",
     "שם: רון וליצקו\nארוחה: ערב\nשנה: הוסף (טונה) במקום (חזה עוף לאחר בישול)", None),
    ("09 — אחרי בישול = מבושל",
     "שם: רון וליצקו\nארוחה: ערב\nשנה: הוסף (טונה) במקום (חזה עוף אחרי בישול)", None),
    ("10 — לפני הבישול = לפני בישול",
     "שם: רון וליצקו\nארוחה: צהריים\nשנה: הוסף (טונה) במקום (חזה עוף לפני הבישול)", None),
    ("11 — לפני בשול (טעות כתיב)",
     "שם: רון וליצקו\nארוחה: צהריים\nשנה: הוסף (טונה) במקום (חזה עוף לפני בשול)", None),
    ("12 — לא מבושל = לפני בישול",
     "שם: רון וליצקו\nארוחה: צהריים\nשנה: הוסף (טונה) במקום (חזה עוף לא מבושל)", None),

    # ══ ארוחות שגויות / קיצורים ══
    ("13 — ארוחה: צהרים (חסר י) → צהריים",
     "שם: רון וליצקו\nארוחה: צהרים\nשנה: הוסף (טונה) במקום (שיבולת שועל)", None),
    ("14 — ארוחה: לילה → ערב",
     "שם: רון וליצקו\nארוחה: לילה\nשנה: הוסף (טונה) במקום (חזה עוף מבושל)", None),
    ("15 — ארוחה: ארוחת ערב (עם ארוחת)",
     "שם: רון וליצקו\nארוחה: ארוחת ערב\nשנה: הוסף (טונה) במקום (חזה עוף מבושל)", None),
    ("16 — ארוחה לא קיימת",
     "שם: רון וליצקו\nארוחה: ביניים\nשנה: הוסף (טונה) במקום (שקדים)", None),

    # ══ שמות עם טעויות — fuzzy → אישור ══
    ("17 — רן וליצקו (חסר ו) → fuzzy → כן",
     "שם: רן וליצקו\nארוחה: ערב\nשנה: הוסף (טונה) במקום (חזה עוף מבושל)", "כן"),
    ("18 — רון וולציקו (טעות) → fuzzy → כן",
     "שם: רון וולציקו\nארוחה: ערב\nשנה: הוסף (טונה) במקום (חזה עוף מבושל)", "כן"),
    ("19 — רון וליצקון (נ' מיותרת) → fuzzy → לא",
     "שם: רון וליצקון\nארוחה: ערב\nשנה: הוסף (טונה) במקום (חזה עוף מבושל)", "לא"),
    ("20 — דנניה קגנוביץ (טעות קשה) → fuzzy → כן",
     "שם: דניה קגנוביץ\nארוחה: ערב\nשנה: הוסף (טונה) במקום (דג סלמון מבושל)", "כן"),
    ("21 — שם פרטי בלבד — רון",
     "שם: רון\nארוחה: ערב\nשנה: הוסף (טונה) במקום (חזה עוף מבושל)", None),
    ("22 — שם לא קיים בכלל",
     "שם: משה לוי אברהם\nארוחה: ערב\nשנה: הוסף (טונה) במקום (חזה עוף מבושל)", None),

    # ══ מזון לא קיים / קיים חלקית ══
    ("23 — מזון חדש לא קיים במאגר",
     "שם: רון וליצקו\nארוחה: ערב\nשנה: הוסף (שניצל ביתי) במקום (חזה עוף מבושל)", None),
    ("24 — מזון קיים חלקית — יוגורט (ללא %) → אפשרויות",
     "שם: רון וליצקו\nארוחה: ערב\nשנה: הוסף (יוגורט) במקום (חזה עוף מבושל)", None),
    ("25 — group_hint לא קיים בארוחה",
     "שם: רון וליצקו\nארוחה: ערב\nשנה: הוסף (טונה) במקום (ביצה קשה)", None),
    ("26 — מזון קיים לפי שם חלקי",
     "שם: רון וליצקו\nארוחה: ערב\nשנה: הוסף (טונה) במקום (אורז)", None),

    # ══ פורמט עם <> (הטמפלייט הפרובלמטי) ══
    ("27 — תבנית עם <> — מוריה",
     "שם: <מוריה שלו>\nארוחה: ארוחת ערב\nשנה: הוסף ( אנטריקוט מבושל) במקום (דג סלמון)", None),
    ("28 — תבנית עם <> ורווחים — רון",
     "שם: < רון וליצקו >\nארוחה: ערב\nשנה: הוסף (טונה) במקום (חזה עוף מבושל)", None),

    # ══ רווחים כפולים ══
    ("29 — רווח כפול בשם",
     "שם: רון  וליצקו\nארוחה: ערב\nשנה: הוסף (טונה) במקום (חזה עוף מבושל)", None),
    ("30 — רווח כפול במזון",
     "שם: רון וליצקו\nארוחה: ערב\nשנה: הוסף (טונה  בשמן זית) במקום (חזה עוף מבושל)", None),

    # ══ emoji ══
    ("31 — emoji בשם",
     "שם: רון וליצקו 💪\nארוחה: ערב\nשנה: הוסף (טונה) במקום (חזה עוף מבושל)", None),
    ("32 — emoji בטקסט חופשי",
     "תוסיפי לרון טונה 🐟 בארוחת ערב במקום חזה עוף", "כן"),

    # ══ כתיב חופשי ══
    ("33 — כתיב חופשי — לרון + ארוחה",
     "תוסיפי לרון טונה בשמן זית בארוחת ערב במקום חזה עוף מבושל", "כן"),
    ("34 — כתיב חופשי — לדני",
     "תחליף לדני סלמון בארוחת ערב במקום בטטה", "כן"),
    ("35 — כתיב חופשי — ללא ציון ארוחה",
     "תוסיפי לרון טונה במקום אורז", "כן"),
    ("36 — כתיב חופשי — ללא שם",
     "תוסיפי טונה בשמן זית בארוחת ערב במקום חזה עוף מבושל", "לא"),

    # ══ מפתח שנה עם פועל ══
    ("37 — מפתח החלף:",
     "שם: רון וליצקו\nארוחה: ערב\nהחלף: (טונה בשמן זית) במקום (חזה עוף מבושל)", None),
    ("38 — מפתח הוסף:",
     "שם: רון וליצקו\nארוחה: ערב\nהוסף: (אנטריקוט מבושל) במקום (חזה עוף מבושל)", None),

    # ══ שנה: ללא פועל ══
    ("39 — שנה: X במקום Y (ללא הוסף)",
     "שם: רון וליצקו\nארוחה: ערב\nשנה: טונה בשמן זית במקום חזה עוף מבושל", None),

    # ══ ניסיונות הכשלה מכוונים ══
    ("40 — הודעה ריקה לחלוטין",
     "", None),
    ("41 — רק שם ללא שאר",
     "שם: רון וליצקו", None),
    ("42 — רק ארוחה",
     "ארוחה: ערב", None),
    ("43 — בלבול ארוחה ומזון",
     "שם: רון וליצקו\nארוחה: טונה\nשנה: הוסף (ערב) במקום (אורז)", None),
    ("44 — שם עם מספר טלפון",
     "שם: 0539598622\nארוחה: ערב\nשנה: הוסף (טונה) במקום (חזה עוף מבושל)", None),
    ("45 — הודעה לא רלוונטית",
     "מה נשמע? הכל בסדר?", None),
    ("46 — ניסיון SQL injection",
     "שם: רון'; DROP TABLE users;--\nארוחה: ערב\nשנה: הוסף (טונה) במקום (אורז)", None),
    ("47 — שם נכון + ארוחה לא קיימת",
     "שם: רון וליצקו\nארוחה: ארוחת חצות\nשנה: הוסף (טונה) במקום (אורז)", None),
    ("48 — group_hint עם ה' + לאחר בישול",
     "שם: רון וליצקו\nארוחה: ערב\nשנה: הוסף (טונה) במקום (החזה עוף לאחר הבישול)", None),
    ("49 — כתיב חופשי — שם בלי ל/של",
     "רון וליצקו ערב תחליף חזה עוף טונה", "כן"),
    ("50 — הפועל תחליף (ף סופית)",
     "שם: רון וליצקו\nארוחה: צהריים\nשנה: תחליף (טונה בשמן זית) במקום (חמאת בוטנים)", None),
]

# ── הרצה ──────────────────────────────────────────────────────────────────────
ISSUES = []

def run():
    print("=" * 75)
    print(f"{'#':<3}  {'תוצאה':<10}  {'תיאור'}")
    print("=" * 75)
    for name, msg, confirm in TESTS:
        api._uid_cache = {}
        try:
            r1, r2 = simulate(msg, confirm_with=confirm)
        except Exception as e:
            r1, r2 = f"EXCEPTION: {e}", None

        # קביעת סטטוס
        def classify(r):
            if r is None: return ""
            if r.startswith("✅"):    return "✅ OK"
            if r.startswith("❓") and "האם" in r:   return "🔄 FUZZY"
            if r.startswith("❓") and "הבנתי" in r: return "🔄 CONFIRM"
            if r.startswith("❓"):   return "⚠️ FOOD?"
            if r.startswith("❌"):   return "❌ ERR"
            if "EXCEPTION" in r:     return "💥 CRASH"
            if "פורמט שגוי" in r:    return "⚠️ FORMAT"
            if "לא הבנתי" in r:     return "⚠️ PARSE"
            return "ℹ️ INFO"

        status1 = classify(r1)
        status2 = classify(r2) if r2 else ""

        combined = f"{status1}"
        if r2:
            combined += f" → {status2}"

        print(f"{name[:2]:<3}  {combined:<18}  {name[5:]}")

        # ─ ניתוח בעיות ─
        num = name[:2]

        # בדיקות שצריכות להצליח
        expect_ok = {
            "01","02","03","04","05","06","07","08","09","10","11","12",
            "13","14","15","17","18","20","21","26","27","28","29","30",
            "31","37","38","39","44","48","50",
        }
        expect_ok_after_confirm = {"17","18","20","33","34","35","37","38"}
        expect_not_found = {"22","23","25","47"}
        expect_format_err = {"40","41","42","45"}
        expect_options = {"24"}
        expect_fuzzy = {"17","18","19","20"}
        expect_confirm = {"32","33","34","35","36","49"}

        final = r2 if r2 else r1

        if num in expect_ok and not (r1.startswith("✅") or (r2 and r2.startswith("✅"))):
            if not r1.startswith("🔄") and not "FUZZY" in r1 and not "CONFIRM" in r1:
                ISSUES.append((num, name[5:], f"ציפינו להצלחה, קיבלנו: {final[:80]}"))

        if num in expect_not_found and "❌" not in final and "נמצא" not in final:
            ISSUES.append((num, name[5:], f"ציפינו 'לא נמצא', קיבלנו: {final[:80]}"))

        if num in expect_format_err and "פורמט" not in final and "לא הבנתי" not in final and "❌" not in final:
            ISSUES.append((num, name[5:], f"ציפינו שגיאת פורמט, קיבלנו: {final[:80]}"))

    print("\n" + "=" * 75)
    print(f"בעיות שנמצאו: {len(ISSUES)}")
    for num, desc, problem in ISSUES:
        print(f"  #{num}: {desc}")
        print(f"       ↳ {problem}")

    # ─ פירוט מלא ─
    print("\n" + "─" * 75)
    print("פירוט מלא:")
    for name, msg, confirm in TESTS:
        api._uid_cache = {}
        try:
            r1, r2 = simulate(msg, confirm_with=confirm)
        except Exception as e:
            r1, r2 = f"EXCEPTION: {e}", None
        print(f"\n▶ {name}")
        print(f"  בוט: {r1[:120]}")
        if r2:
            print(f"  אחרי '{confirm}': {r2[:120]}")

if __name__ == "__main__":
    run()
