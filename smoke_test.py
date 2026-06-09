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

# ══════════════════════════════════════════════════════════════════
print(f"\n{'═'*50}")
print(f"תוצאה: {PASS} עברו, {FAIL} נכשלו")
if FAIL:
    print("⛔ אל תעשה deploy לפני שהכל עובר!")
    sys.exit(1)
else:
    print("✅ הכל עובר — אפשר לפרוס")
