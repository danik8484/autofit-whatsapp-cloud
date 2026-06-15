#!/usr/bin/env python3
"""
smoke_test.py — רץ לפני כל deploy.
מכסה את הבאגים שחזרו על עצמם + את הלוגיקה הקריטית.
"""
import re, sys
from difflib import SequenceMatcher

PASS = 0; FAIL = 0

def check(name, got, expected):
    global PASS, FAIL
    ok = got == expected
    if ok:
        print(f"  ✅ {name}")
        PASS += 1
    else:
        print(f"  ❌ {name}")
        print(f"     got:      {got!r}")
        print(f"     expected: {expected!r}")
        FAIL += 1

def check_contains(name, got, substr):
    global PASS, FAIL
    ok = substr in str(got)
    if ok:
        print(f"  ✅ {name}")
        PASS += 1
    else:
        print(f"  ❌ {name}")
        print(f"     got:      {got!r}")
        print(f"     missing:  {substr!r}")
        FAIL += 1

def check_not_contains(name, got, substr):
    global PASS, FAIL
    ok = substr not in str(got)
    if ok:
        print(f"  ✅ {name}")
        PASS += 1
    else:
        print(f"  ❌ {name}")
        print(f"     got:      {got!r}")
        print(f"     should NOT contain: {substr!r}")
        FAIL += 1

# ══════════════════════════════════════════════════════════════════
print("\n── 1. גרמי תחליף: שדות food_row ──────────────────────────────")
# BUG היסטורי: השתמשנו ב-quantity במקום gram_value → תמיד ריק
food_row_real = {"id": 1, "food_name": "לחם מלא", "gram_value": "80", "measure": "grams", "calories": "200"}
food_row_no_gramval = {"id": 1, "food_name": "ביצה", "grams": "60", "measure": "grams"}
food_row_quantity_only = {"id": 1, "food_name": "אורז", "quantity": "150", "measure": "grams"}

def get_replaced_q(fr):
    return (fr.get("quantity") or fr.get("quantity_to_calculate") or
            fr.get("gram_value") or fr.get("grams") or "")

check("quantity קיים → מחזיר quantity (לא gram_value!)", get_replaced_q({"gram_value": "100", "quantity": "300"}), "300")
check("רק gram_value → מחזיר gram_value", get_replaced_q(food_row_real), "80")
check("grams fallback → מחזיר 60", get_replaced_q(food_row_no_gramval), "60")
check("quantity fallback → מחזיר 150", get_replaced_q(food_row_quantity_only), "150")
check("אין שדה → ריק", get_replaced_q({"id": 1, "food_name": "X"}), "")

# ══════════════════════════════════════════════════════════════════
print("\n── 2. חילוץ GRAMS מ-add_food_to_meal ──────────────────────────")
# BUG היסטורי: group(1) בלי capture group → None
def extract_grams(add_result):
    m = re.search(r'\|GRAMS=([\d.]+)', add_result)
    return m.group(1) if m else None

check("חילוץ גרמים מהתשובה", extract_grams("✅ נוסף כתחליף ל-לחם: שיבולת שועל|GRAMS=100"), "100")
check("גרמים עשרוניים", extract_grams("✅ נוסף כתחליף ל-X: Y|GRAMS=87.5"), "87.5")
check("ללא GRAMS → None", extract_grams("✅ נוסף כתחליף ל-X: Y"), None)
check("ניקוי sentinel", re.sub(r'\|GRAMS=[\d.]+', '', "✅ נוסף|GRAMS=100"), "✅ נוסף")

# ══════════════════════════════════════════════════════════════════
print("\n── 3. פורמט קבלת תחליף ──────────────────────────────────────")
def build_sub_receipt(food_name, new_grams, replaced_name, replaced_grams, meal, client):
    try:
        _ag = float(new_grams)
        new_disp = f" ({int(_ag) if _ag == int(_ag) else round(_ag,1)} גרם)"
    except: new_disp = ""
    try:
        rq = float(replaced_grams)
        replaced_disp = f" ({int(rq) if rq == int(rq) else round(rq,1)} גרם)"
    except: replaced_disp = ""
    return f"✅ *{food_name}*{new_disp} ← החליף: {replaced_name}{replaced_disp}\nב{meal} של {client}"

r = build_sub_receipt("שיבולת שועל מלא", "100", "לחם מלא", "80", "ארוחת בוקר", "רון")
check_contains("גרמי מזון חדש מוצגים", r, "(100 גרם)")
check_contains("גרמי מזון מוחלף מוצגים", r, "(80 גרם)")
check_contains("שם מזון חדש", r, "שיבולת שועל מלא")
check_contains("שם מזון מוחלף", r, "לחם מלא")
check_contains("שם לקוח", r, "רון")

# ══════════════════════════════════════════════════════════════════
print("\n── 4. preprocessing כאופציה/תחליף ──────────────────────────")
def preprocess(text):
    text = re.sub(r'מוסיף\s+ל[כךו]', 'הוסף', text)
    text = re.sub(r'כתחליף\s+ל', 'במקום ', text)
    text = re.sub(r'כאופצי(?:ה|ות)?\s+(?:ל(?=[א-ת]{3,})|של)\s*', 'במקום ', text)
    text = re.sub(r'באופצי(?:ה|ות)?\s+של\s*ה?', 'במקום ', text)
    text = re.sub(r'אופציה\s+של\s+', '', text)
    return text.strip()

check("מוסיף לך → הוסף", preprocess("מוסיף לך שיבולת שועל"), "הוסף שיבולת שועל")
check("כאופציה ללחם → במקום לחם", preprocess("שיבולת שועל כאופציה ללחם"), "שיבולת שועל במקום לחם")
check("כאופציה לבטטה → במקום בטטה", preprocess("אורז כאופציה לבטטה"), "אורז במקום בטטה")
check("באופציה של הבטטה → במקום בטטה", preprocess("אורז באופציה של הבטטה"), "אורז במקום בטטה")
check("כתחליף ל → במקום", preprocess("טונה כתחליף לסלמון"), "טונה במקום סלמון")
check("מלא: מוסיף לך X כאופציה ללחם", preprocess("מוסיף לך שיבולת שועל כאופציה ללחם"), "הוסף שיבולת שועל במקום לחם")

# ══════════════════════════════════════════════════════════════════
print("\n── 5. fuzzy matching לgroup_hint ─────────────────────────────")
def word_fuzzy(query, food_name, max_edit=1):
    from difflib import SequenceMatcher
    def norm(s): return s.strip()
    qw = norm(query).split(); fw = norm(food_name).split()
    for q in qw:
        found = any(
            q in f or f in q or
            SequenceMatcher(None, q, f).ratio() >= 1 - (2*max_edit / max(len(q)+len(f),1))
            for f in fw if len(f) >= 2
        )
        if not found: return False
    return True

check("לחלם ↔ לחם מלא (טעות כתיב)", word_fuzzy("לחלם", "לחם מלא"), True)
check("לחם ↔ לחם מלא (substring)", word_fuzzy("לחם", "לחם מלא"), True)
check("ביצה ↔ ביצת עין (substring)", word_fuzzy("ביצה", "ביצת עין"), True)
check("טונה ↔ אורז (לא קשור)", word_fuzzy("טונה", "אורז"), False)
check("בטטה ↔ אורז בסמטי (לא קשור)", word_fuzzy("בטטה", "אורז בסמטי"), False)

# ══════════════════════════════════════════════════════════════════
print("\n── 6. FOOD_OPTIONS פורמט name:calories ─────────────────────")
def parse_alts_pipe(pipe_str):
    raw = pipe_str.split('|')
    result = []
    for a in raw:
        ci = a.lastIndexOf(':') if hasattr(a, 'lastIndexOf') else a.rfind(':')
        result.append({"name": a[:ci], "cal": int(a[ci+1:]) or 0} if ci > 0 else {"name": a, "cal": 0})
    return result

alts = parse_alts_pipe("אורז בסמטי:130|שיבולת שועל:350|קינואה:0")
check("שם מזון ראשון", alts[0]["name"], "אורז בסמטי")
check("קלוריות ראשון", alts[0]["cal"], 130)
check("שם שני", alts[1]["name"], "שיבולת שועל")
check("קלוריות=0 כשאין", alts[2]["cal"], 0)


# ══════════════════════════════════════════════════════════════════
print("\n── 7. ברירת מחדל שגויה של גרמים ──────────────────────────────")
# BUG היסטורי: or "100" בסוף → מציג גרמים שגויים כשהשדה חסר

def get_actual_grams(grams_override, food_row, food):
    return str(grams_override or food_row.get("gram_value") or food_row.get("grams") or
               food_row.get("quantity") or food.get("grams") or food.get("gram_value") or "")

# מקרה תקין: gram_value קיים
check("gram_value קיים → מחזיר ערך אמיתי",
    get_actual_grams(None, {"gram_value": "80"}, {"grams": "100"}), "80")

# מקרה תקין: grams_override מנצח
check("grams_override → מנצח הכל",
    get_actual_grams("150", {"gram_value": "80"}, {}), "150")

# מקרה שגיאה: אין שדות — חייב להחזיר ריק, לא 100
check("אין שדות כלל → ריק (לא 100!)",
    get_actual_grams(None, {}, {}), "")

check("gram_value=None → ריק (לא 100!)",
    get_actual_grams(None, {"gram_value": None}, {"grams": None}), "")

# וידוא שהקבלה לא מציגה כלום כשגרמים ריקים
def new_disp_from_grams(actual_grams_used):
    if not actual_grams_used:
        return ""
    try:
        f = float(actual_grams_used)
        return f" ({int(f) if f == int(f) else round(f,1)} גרם)"
    except:
        return ""

check("גרמים ריקים → אין תצוגה", new_disp_from_grams(""), "")
check("גרמים None → אין תצוגה", new_disp_from_grams(None), "")
check("גרמים 100 → מציג", new_disp_from_grams("100"), " (100 גרם)")
check("גרמים 87.5 → מציג עשרוני", new_disp_from_grams("87.5"), " (87.5 גרם)")


# ══════════════════════════════════════════════════════════════════
print("\n── 8. preprocessing: אני + גרם של ────────────────────────────")
import re

def preprocess_v3(text):
    text = re.sub(r'^אני\s+', '', text)
    text = re.sub(r'מוסיף\s+ל[כךו]', 'הוסף', text)
    text = re.sub(r'(\d+\s*גרם)\s+של\s+', r'\1 ', text)
    return text.strip()

check("אני מוסיף לך → הוסף",
    preprocess_v3("אני מוסיף לך 50 גרם חזה עוף"),
    "הוסף 50 גרם חזה עוף")
check("50 גרם של חזה עוף → 50 גרם חזה עוף",
    preprocess_v3("הוסף 50 גרם של חזה עוף"),
    "הוסף 50 גרם חזה עוף")
check("מלא: אני מוסיף לך 50 גרם של X",
    preprocess_v3("אני מוסיף לך 50 גרם של חזה עוף בארוחת ערב"),
    "הוסף 50 גרם חזה עוף בארוחת ערב")


# ══════════════════════════════════════════════════════════════════
print("\n── 8b. ניקוי גרמים משם המזון (באג AI-merge) ──────────────────")
# BUG: כש-regex שם change="הוסף (50 גרם FOOD)" ו-AI מוסיף extra_grams="50",
# הניקוי ב-execute_request דילג (elif not extra_grams) → חיפש "50 גרם FOOD".
# התיקון: תמיד מנקים "N גרם" משם המזון. הטסט משכפל את לוגיקת הניקוי.
def strip_grams_from_food(new_food_clean, extra_grams=None):
    grams_in_food = re.search(r'\bעוד\s+(\d+)\s*גרם\b', new_food_clean)
    if grams_in_food:
        extra_grams = extra_grams or grams_in_food.group(1)
        new_food_clean = re.sub(r'\bעוד\s+\d+\s*גרם\s*', '', new_food_clean).strip()
    else:
        m = re.match(r'^(\d+)\s*גרם\s+(?:ל(?=[\u05D0-\u05EA]{3,}))?\s*', new_food_clean)
        if m:
            extra_grams = extra_grams or m.group(1)
            new_food_clean = new_food_clean[m.end():].strip()
        else:
            m2 = re.search(r'^(.+?)\s+(\d+)\s*גרם\s*$', new_food_clean)
            if m2:
                extra_grams = extra_grams or m2.group(2)
                new_food_clean = m2.group(1).strip()
    return new_food_clean, extra_grams

# הבאג: extra_grams כבר מוגדר (מ-AI) — בעבר הניקוי דילג
check("גרמים בשם + extra_grams ידוע → מנקה בכל זאת",
    strip_grams_from_food("50 גרם שיבולת שועל עבה", extra_grams="50")[0],
    "שיבולת שועל עבה")
check("גרמים בשם בלי extra_grams → מנקה",
    strip_grams_from_food("90 גרם פסטה מבושלת")[0],
    "פסטה מבושלת")
check("גרם עם ל-prefix → מנקה",
    strip_grams_from_food("100 גרם לתפוח אדמה אפוי")[0],
    "תפוח אדמה אפוי")
check("גרמים בסוף השם → מנקה",
    strip_grams_from_food("אורז 50 גרם")[0],
    "אורז")
check("שם נקי ללא גרמים → ללא שינוי",
    strip_grams_from_food("חזה עוף מבושל", extra_grams="40")[0],
    "חזה עוף מבושל")

# ══════════════════════════════════════════════════════════════════
print("\n── 8c. נרמול יחידות מידה → גרמים ──────────────────────────────")
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("autofit_api", __file__.replace("smoke_test.py","autofit_api.py"))
_af = _ilu.module_from_spec(_spec)
import sys as _sys; _sys.argv=['x']
_spec.loader.exec_module(_af)
nu = _af._normalize_units
check("2 ביצים → גרם ביצה (לא אטריות!)", nu("2 ביצים"), "110 גרם ביצה")
check("שתי ביצים (מילה) → גרם", nu("שתי ביצים"), "110 גרם ביצה")
check("2 כפות טחינה → 30 גרם", nu("2 כפות טחינה גולמית"), "30 גרם טחינה גולמית")
check("3 כפיות → 15 גרם", nu("3 כפיות סוכר"), "15 גרם סוכר")
check("חצי קילו → 500 גרם", nu("חצי קילו אורז"), "500 גרם אורז")
check("רבע קילו → 250 גרם", nu("רבע קילו בשר"), "250 גרם בשר")
check("1.5 קילו → 1500 גרם", nu("1.5 קילו עוף"), "1500 גרם עוף")
check("קילו לבד → 1000 גרם", nu("קילו אורז"), "1000 גרם אורז")
check_not_contains("עשרוני לא משאיר נקודה", nu("12.5 גרם שקדים"), ".")
check("גרם רגיל ללא שינוי", nu("50 גרם שקדים"), "50 גרם שקדים")

print("\n── 8d. מולטי-מזון: ו / ו- / וגם ──────────────────────────────")
_dmf = _af._detect_multi_food
check("ו- (מקף)", bool(_dmf("50 גרם אורז ו-30 גרם חזה")), True)
check("וגם", bool(_dmf("100 גרם אורז וגם 50 גרם חזה")), True)
check("ו רגיל", bool(_dmf("50 גרם אורז ו 30 גרם חזה")), True)
check("מזון בודד → None", _dmf("50 גרם אורז"), None)

# ══════════════════════════════════════════════════════════════════
print("\n── 9. אין debug prints ב-stdout (יישלחו לוואטסאפ!) ──────────")
import ast, re as _re

with open(__file__.replace("smoke_test.py","autofit_api.py"), encoding="utf-8") as _f:
    _src = _f.read()

# מחפש print( שלא הולך ל-stderr ושלא בmain block (שורות לפני if __name__)
_main_start = _src.find("if __name__")
_before_main = _src[:_main_start] if _main_start > 0 else _src
_bad_prints = [
    line.strip() for line in _before_main.split("\n")
    if "print(" in line
    and "stderr" not in line
    and not line.strip().startswith("#")
    and "flush=True" in line  # debug prints בד"כ עם flush=True
]
if _bad_prints:
    for p in _bad_prints:
        print(f"  ⚠️  {p[:80]}")
check("אין debug prints ב-stdout", len(_bad_prints), 0)

# ══════════════════════════════════════════════════════════════════
print("\n── 10. קיזוז קלורי + אופציות מרובות ──────────────────────────")
from autofit_api import (_split_offset, _split_options, _food_calories_for_grams,
                         parse_message)

# _split_offset — זיהוי קיזוז (regex טהור, offline)
_o1 = _split_offset("מוסיף לך 70 גרם תפוח עץ בצהריים ומקזז מהאורז")
check("קיזוז: מזון קיזוז = אורז", _o1[1] if _o1 else None, "אורז")
check("קיזוז: ללא ארוחה → None", _o1[2] if _o1 else "X", None)
_o2 = _split_offset("מוסיף לך 70 גרם תפוח עץ בצהריים ומקזז מהאורז בערב")
check("קיזוז: ארוחת קיזוז = ערב", _o2[2] if _o2 else None, "ערב")
_o3 = _split_offset("מוסיף לך תפוח אדמה 30 גרם ומוריד את אותם הקלוריות מהאורז")
check("קיזוז: 'מוריד את אותם הקלוריות' → אורז", _o3[1] if _o3 else None, "אורז")
check("לא-קיזוז: הוספה רגילה → None", _split_offset("מוסיף לך 100 גרם אורז בצהריים"), None)

# _split_options — זיהוי אופציות מרובות (regex טהור)
_p1 = _split_options("מוסיף לך 50 גרם מנגו בצהריים ושם באופציות גם תפוח גם בננה")
check("אופציות: רשימה = [תפוח, בננה]", _p1[1] if _p1 else None, ["תפוח", "בננה"])
check("לא-אופציות: 'כאופציה ל-X' (תחליף יחיד) → None",
      _split_options("תחליפי לדני ביצים כאופציה לגבינה"), None)
check("לא-אופציות: הוספה רגילה → None", _split_options("מוסיף לך 100 גרם אורז"), None)

# _food_calories_for_grams — חישוב קלוריות (calories ל-grams field)
check("קלוריות: 70ג תפוח (52/100) = 36", round(_food_calories_for_grams({"calories": "52", "grams": "100"}, 70)), 36)
check("קלוריות: 200ג (52/100) = 104", round(_food_calories_for_grams({"calories": "52", "grams": "100"}, 200)), 104)
check("קלוריות: gram_value fallback", round(_food_calories_for_grams({"calories": "100", "gram_value": "100"}, 50)), 50)

# parse_message — offset_food/options נשמרים ב-result (גם ללא AI — חיתוך regex)
_pm1 = parse_message("מוסיף לך 70 גרם תפוח עץ בצהריים ומקזז מהאורז")
check_contains("parse: offset_food נשמר", _pm1.get("offset_food"), "אורז")
_pm2 = parse_message("מוסיף לך 50 גרם מנגו בצהריים ושם באופציות גם תפוח גם בננה")
check("parse: options נשמר", _pm2.get("options"), ["תפוח", "בננה"])

# ── 11. התאמת קלוריות למזון קיים (cal_mode) ─────────────────────
print("\n── 11. התאמת קלוריות למזון קיים ──────────────────────────")
import autofit_api as _afc

# זיהוי יחידת-קלוריות + המרה ל-"גרם"
check("cal: '80 קלוריות' מזוהה", bool(_afc._CAL_UNIT_RE.search("מוריד לך 80 קלוריות מהפיתה")), True)
check("cal: \"קל'\" מזוהה", bool(_afc._CAL_UNIT_RE.search("מוריד 50 קל' מהביצה")), True)
check("cal: 'קל' לבד (=light) לא מזוהה", bool(_afc._CAL_UNIT_RE.search("אורז קל לאכול")), False)
check("cal: גרם רגיל לא מזוהה כקלוריות", bool(_afc._CAL_UNIT_RE.search("מוסיף 50 גרם אורז")), False)
check("cal: המרה '80 קלוריות'→'80 גרם'",
      _afc._CAL_UNIT_RE.sub(r"\1 גרם", "מוריד לך 80 קלוריות מהפיתה"), "מוריד לך 80 גרם מהפיתה")

# חישוב גרמים מקלוריות + עדכון מזון קיים (mock — בלי רשת)
_CAL_MEALS = [
    {"id": 1, "meal_name": "ארוחת בוקר", "mealFoods": [
        {"id": 101, "food_name": "פיתה", "quantity": 100, "calories": 270}]},
    {"id": 2, "meal_name": "ארוחת צהריים", "mealFoods": [
        {"id": 201, "food_name": "אורז לאחר בישול", "quantity": 200, "calories": 260}]},
]
_cal_calls = []
_orig_meals, _orig_upd = _afc.get_user_meals, _afc.update_food_quantity
_afc.get_user_meals = lambda uid: _CAL_MEALS
_afc.update_food_quantity = lambda uid, mfid, q: (_cal_calls.append((mfid, round(q, 1))) or {"status": True})
try:
    _r1 = _afc.handle_calorie_adjust(
        "23203", "דני", _CAL_MEALS,
        {"reduce": True, "change": "הוסף (פיתה)", "extra_grams": "80"}, ["בוקר"],
        "מוריד לך 80 גרם מהפיתה בבוקר")
    # 270 קל'/100ג → 80 קל' = 29.6ג → 70.4ג
    check("cal: הורדת 80 קל' מפיתה → 70ג", _cal_calls[-1], (101, 70.4))
    check_contains("cal: דיווח הורדה", _r1, "הופחת")
    _cal_calls.clear()
    _r2 = _afc.handle_calorie_adjust(
        "23203", "דני", _CAL_MEALS,
        {"reduce": False, "change": "הוסף (80 גרם באורז)"}, ["צהריים"],
        "מוסיף לך 80 גרם באורז בצהריים")
    # 260 קל'/200ג → 80 קל' = 61.5ג → 261.5ג. ('באורז' → ב' מקום מולחת)
    check("cal: הוספת 80 קל' לאורז → 262ג", _cal_calls[-1], (201, 261.5))
    check_contains("cal: זיהה 'באורז' כאורז קיים", _r2, "אורז")
    _cal_calls.clear()
    # הורדה שעוברת את הסכום הקיים → 0 + אזהרה
    _r3 = _afc.handle_calorie_adjust(
        "23203", "דני", _CAL_MEALS,
        {"reduce": True, "change": "הוסף (פיתה)", "extra_grams": "500"}, ["בוקר"],
        "מוריד לך 500 גרם מהפיתה בבוקר")
    check("cal: הורדה גדולה מהקיים → 0ג", _cal_calls[-1], (101, 0.0))
    check_contains("cal: אזהרה על הגעה ל-0", _r3, "הגיע ל-0")
    # מזון לא קיים בארוחה → שגיאה, ללא עדכון
    _cal_calls.clear()
    _r4 = _afc.handle_calorie_adjust(
        "23203", "דני", _CAL_MEALS,
        {"reduce": True, "change": "הוסף (פסטה)", "extra_grams": "50"}, ["צהריים"],
        "מוריד לך 50 גרם מהפסטה בצהריים")
    check("cal: מזון לא קיים → ללא עדכון", _cal_calls, [])
    check_contains("cal: מזון לא קיים → שגיאה", _r4, "לא מצאתי")

    # מזון-יחידות (measure=unit): דיווח ביח', לא גרם. ברירת מחדל = גרמים.
    _UNIT_MEALS = [{"id": 9, "meal_name": "ארוחת בוקר", "mealFoods": [
        {"id": 901, "food_name": "ביצה קשה בינונית", "quantity": 2, "calories": 164.3, "measure": "unit"},
        {"id": 902, "food_name": "לחם מלא", "quantity": 66, "calories": 163, "measure": "grams"}]}]
    _afc.get_user_meals = lambda uid: _UNIT_MEALS
    _cal_calls.clear()
    _ru = _afc.handle_calorie_adjust("23203", "דני", _UNIT_MEALS,
        {"reduce": True, "change": "הוסף (ביצה קשה)", "extra_grams": "80"}, ["בוקר"],
        "מוריד לך 80 גרם מהביצה קשה בבוקר")
    check_contains("cal-unit: דיווח ביחידות (יח')", _ru, "יח'")
    check_not_contains("cal-unit: לא מדווח 'גרם' למזון יחידות", _ru.split("(")[0], "גרם")
    check("cal-unit: 80 קל' מ-2 ביצים (164) → ~1 יח'", _cal_calls[-1], (901, round(2 - 80*2/164.3, 1)))
    _cal_calls.clear()
    _rg = _afc.handle_calorie_adjust("23203", "דני", _UNIT_MEALS,
        {"reduce": True, "change": "הוסף (לחם מלא)", "extra_grams": "80"}, ["בוקר"],
        "מוריד לך 80 גרם מהלחם מלא בבוקר")
    check_contains("cal-grams: מזון רגיל עדיין בגרמים (ברירת מחדל)", _rg, "גרם")
    check_not_contains("cal: 66→34 (51%) → ללא אזהרת הורדה גדולה", _rg, "הורדה גדולה")
    # נקודה 4: הורדה גדולה (מתחת ל-50%) → אזהרה בולטת (100 קל' → 66→25ג = 38%)
    _rbig = _afc.handle_calorie_adjust("23203", "דני", _UNIT_MEALS,
        {"reduce": True, "change": "הוסף (לחם מלא)", "extra_grams": "100"}, ["בוקר"],
        "מוריד לך 100 גרם מהלחם מלא בבוקר")
    check_contains("cal: אזהרת הורדה גדולה (<50%)", _rbig, "הורדה גדולה")
finally:
    _afc.get_user_meals, _afc.update_food_quantity = _orig_meals, _orig_upd

# ── 12. רבים → יחיד (offline, regex טהור) ───────────────────────
print("\n── 12. המרת רבים → יחיד ──────────────────────────────────")
check("רבים: 'שיבולות שועל' → כולל 'שיבולת שועל'",
      "שיבולת שועל" in _afc._singularize_query("שיבולות שועל"), True)
check("רבים: 'בננות' → כולל 'בננה'", "בננה" in _afc._singularize_query("בננות"), True)
check("רבים: 'תפוחים' → כולל 'תפוח'", "תפוח" in _afc._singularize_query("תפוחים"), True)
check("רבים: 'עוגיות' → כולל 'עוגיה'", "עוגיה" in _afc._singularize_query("עוגיות"), True)
check("רבים: יחיד ('אורז') → אין וריאציות", _afc._singularize_query("אורז"), [])
check("רבים: אליאס 'שיבולות שועל' קיים",
      _afc._FOOD_ALIASES.get("שיבולות שועל"), "שיבולת שועל")

# ── 13. פיצול רשימה עם אחוז/ספרה בשם מזון (באג הפסיק של אלנתן) ──
print("\n── 13. פיצול רשימה עם אחוז (קוטג 5%) ─────────────────────")
# מפריד חזק (פסיק) + ספרה שהיא חלק מהשם (5%) → חייב לפצל (offline, ללא search)
check("פסיק: 'טונה,קוטג 5% ,גבינה לבנה' → 3 מזונות",
      _afc._split_multi_new_food("טונה,קוטג 5% ,גבינה לבנה"),
      ["טונה", "קוטג 5%", "גבינה לבנה"])
check("פסיק+אחוז: 'גבינה 3%,יוגורט 5%' → 2",
      _afc._split_multi_new_food("גבינה 3%,יוגורט 5%"), ["גבינה 3%", "יוגורט 5%"])
# ספרת-גרמים עדיין מבטלת פיצול (מטופל בפיצול הגרמים הנפרד)
check("גרמים+פסיק: '50 גרם אורז, 30 גרם בורגול' → לא מפצל כאן",
      _afc._split_multi_new_food("50 גרם אורז, 30 גרם בורגול"),
      ["50 גרם אורז, 30 גרם בורגול"])

# ── 14. "כאופציה" לא דולף לשם המזון (באג טונה של ליאת) ──────────
print("\n── 14. 'כאופציה' לא נדבק לשם המזון ───────────────────────")
def _ch(msg): return parse_message(msg, skip_name=True).get("change", "")
check("'טונה כאופציה במקום חזה עוף' → טונה נקי",
      _ch("מוסיף לך טונה כאופציה במקום חזה עוף בצהריים"), "הוסף (טונה) במקום (חזה עוף)")
check("'טונה כאופצייה במקום' (יוד כפול) → טונה נקי",
      _ch("מוסיף לך טונה כאופצייה במקום חזה עוף"), "הוסף (טונה) במקום (חזה עוף)")
check("'פיתה כאופציה ללחם' → פיתה נקי",
      _ch("מוסיף לך פיתה כאופציה ללחם בבוקר"), "הוסף (פיתה) במקום (לחם)")

# ── 15. פורמט מובנה "שנה:" עוטף מזון בסוגריים → פיצול multi-food (באג גבינה/פסטרמה) ──
print("\n── 15. 'שנה:' מובנה עוטף בסוגריים לפיצול ריבוי מאכלים ──")
def _struct_change(food_line):
    return parse_message(f"שם: רון\nארוחה: בוקר\nשנה: {food_line}").get("change", "")
# בלי עטיפה ידנית — חייב להיעטף כדי שמנגנון הפיצול יזהה כמה מאכלים
check("מובנה: 'הוסף גבינה צהובה ופסטרמה' → עטוף",
      _struct_change("הוסף גבינה צהובה ופסטרמה"), "הוסף (גבינה צהובה ופסטרמה)")
check("מובנה: פסיקים 'שיבולת שועל,פסטרמה,פסטה' → עטוף",
      _struct_change("הוסף שיבולת שועל,פסטרמה,פסטה"), "הוסף (שיבולת שועל,פסטרמה,פסטה)")
check("מובנה: 'הוסף אורז במקום פסטה' → עטוף עם במקום",
      _struct_change("הוסף אורז במקום פסטה"), "הוסף (אורז) במקום (פסטה)")
check("מובנה: כבר עטוף → לא נעטף כפול",
      _struct_change("הוסף (אורז) במקום (פסטה)"), "הוסף (אורז) במקום (פסטה)")
# פיצול בפועל: רשימת המאכלים מתפצלת לטוקנים נכונים
check("split: 'שיבולת שועל,פסטרמה,פסטה' → 3 מאכלים",
      _afc._split_multi_new_food("שיבולת שועל,פסטרמה,פסטה"), ["שיבולת שועל", "פסטרמה", "פסטה"])
check("split: 'יוגורט וניל' (שם רב-מילי בודד) → לא מתפצל",
      _afc._split_multi_new_food("יוגורט וניל"), ["יוגורט וניל"])

# ── 16. "לאופציה של" + ריבוי ארוחות "צהריים+ערב" (באג נתיב — פרגית) ──
print("\n── 16. 'לאופציה של' + ריבוי ארוחות ───────────────────────")
def _pm(msg): return parse_message(msg, skip_name=True)
_n1 = _pm("מוסיף לך פרגית לאופציה של החזה עוף בארוחת צהריים+ערב")
check("'לאופציה של' → במקום (לא מאבד מזון)", _n1.get("change"), "הוסף (פרגית) במקום (חזה עוף)")
check("'צהריים+ערב' → שתי ארוחות", _n1.get("meals"), ["צהריים", "ערב"])
check("'לאופציה ל' (בלי 'של')",
      _pm("מוסיף לך פרגית לאופציה לחזה עוף בערב").get("change"), "הוסף (פרגית) במקום (חזה עוף)")
check("'לאופציה של הודו' → לא בולע ה' של המזון",
      _pm("מוסיף לך פרגית לאופציה של הודו בערב").get("change"), "הוסף (פרגית) במקום (הודו)")
check("ריבוי ארוחות 'בוקר וצהריים' (מנגנון קיים)",
      _pm("מוסיף לך פרגית במקום חזה עוף בארוחת בוקר וצהריים").get("meals"), ["בוקר", "צהריים"])
check("שגיאת הקלדה אות כפולה 'פפרגיות' → פרגיות",
      _afc._collapse_doubled_letters("פפרגיות"), "פרגיות")

# ══════════════════════════════════════════════════════════════════
print(f"\n{'═'*50}")
print(f"תוצאה: {PASS} עברו, {FAIL} נכשלו")
if FAIL:
    print("⛔ אל תעשה deploy לפני שהכל עובר!")
    sys.exit(1)
else:
    print("✅ הכל עובר — אפשר לפרוס")
