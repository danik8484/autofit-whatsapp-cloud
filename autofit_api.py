"""
autofit_api.py - קריאות ישירות ל-API של auto-fit
ללא Chrome, ללא DOM, ללא AppleScript
Backend: chat.auto-fit.co.il
Food DB: food.we-site.co.il
"""
import requests
import json
import base64
import urllib.parse
import re
import os
from pathlib import Path

BACKEND    = "https://chat.auto-fit.co.il"
FOOD_API   = "https://food.we-site.co.il/api"
FOOD_TOKEN = "eb8b0f58c895019fcbc3bb17480ced3a2d1e12a346d6ed0f0d0267a24587a203"
SESSION_FILE = Path(__file__).parent / "session.json"
PHONES_FILE  = Path(__file__).parent / "phones.json"

_uid_cache: dict = {}


# ─── Session ─────────────────────────────────────────────────────────────────

def load_token() -> str:
    # env variable קודם (ענן), אחר-כך קובץ מקומי
    env_token = os.environ.get("AUTOFIT_TOKEN")
    if env_token:
        return env_token
    if SESSION_FILE.exists():
        return json.loads(SESSION_FILE.read_text())["token"]
    raise RuntimeError("No session — set AUTOFIT_TOKEN env var or run login first")

def save_token(token: str, coach_id: str):
    SESSION_FILE.write_text(json.dumps({"token": token, "coach_id": coach_id}))

def load_coach_id() -> str:
    env_id = os.environ.get("AUTOFIT_COACH_ID")
    if env_id:
        return env_id
    if SESSION_FILE.exists():
        return json.loads(SESSION_FILE.read_text()).get("coach_id", "")
    return ""


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {load_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Language": "he",
    }

def _post(path: str, body: dict) -> dict:
    r = requests.post(f"{BACKEND}{path}", json=body, headers=_headers(), timeout=15)
    r.raise_for_status()
    return r.json()

def _get(path: str, params: dict = None) -> dict:
    r = requests.get(f"{BACKEND}{path}", params=params, headers=_headers(), timeout=15)
    r.raise_for_status()
    return r.json()


# ─── Users ────────────────────────────────────────────────────────────────────

def _load_phones() -> dict:
    try:
        return json.loads(PHONES_FILE.read_text(encoding="utf-8"))
    except:
        return {}

def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        new_row = [i]
        for j, cb in enumerate(b, 1):
            new_row.append(min(row[j] + 1, new_row[-1] + 1, row[j - 1] + (ca != cb)))
        row = new_row
    return row[-1]

def _fuzzy_name_match(query: str, full_name: str) -> bool:
    """מחזיר True אם כל מילות השאילתה קרובות מספיק למילה כלשהי בשם."""
    q_words = query.split()
    n_words = full_name.split()
    for qw in q_words:
        best = min(_levenshtein(qw, nw) for nw in n_words)
        # עד 1 טעות ל-4 אותיות, עד 2 טעויות ל-5+
        limit = 1 if len(qw) <= 4 else (2 if len(qw) <= 6 else 3)
        if best > limit:
            return False
    return True


def find_user(query: str):
    """
    מחזיר (user_id, full_name, is_fuzzy).
    is_fuzzy=True כשנמצאה התאמה עמומה (טעויות כתיב).
    """
    global _uid_cache
    if query in _uid_cache:
        cached = _uid_cache[query]
        return (*cached,) if len(cached) == 3 else (*cached, False)

    clean = query.replace(" ", "").replace("-", "")
    is_phone = clean.isdigit() or (clean.startswith("05") and len(clean) >= 9)

    if is_phone:
        phones = _load_phones()
        name = phones.get(clean) or phones.get("972" + clean[1:] if clean.startswith("0") else clean)
        if not name:
            return None, None, False
        query = name

    query = query.replace("-", " ")  # מקף = רווח בשם
    query = re.sub(r'\s+', ' ', query).strip()
    terms = query.split()
    if not terms:
        return None, None, False
    first_term = terms[0]
    all_users = []
    page, per_page = 1, 100
    while True:
        data = _post("/coach/get-coach-users", {"page": page, "limit": per_page})
        users = data.get("data", [])
        all_users.extend(users)
        total = data.get("pagination", {}).get("total", 0)
        if page * per_page >= total:
            break
        page += 1

    # pass 1: התאמה מדויקת — כל מילות השאילתה בשם
    for u in all_users:
        full_name = u.get("name") or f"{u.get('first_name','')} {u.get('last_name','')}".strip()
        if all(t in full_name for t in terms):
            uid = str(u["id"])
            _uid_cache[query] = (uid, full_name, False)
            return uid, full_name, False

    # pass 2: שם פרטי בלבד — רק לשאילתות עם מילה אחת (כדי לא לטעות בשם משפחה שגוי)
    if len(terms) == 1:
        for u in all_users:
            full_name = u.get("name") or f"{u.get('first_name','')} {u.get('last_name','')}".strip()
            if first_term in full_name:
                uid = str(u["id"])
                _uid_cache[query] = (uid, full_name, False)
                return uid, full_name, False

    # pass 3: חיפוש עמום (Levenshtein) — מוצא גם עם טעויות כתיב
    best_uid, best_name, best_score = None, None, 999
    for u in all_users:
        full_name = u.get("name") or f"{u.get('first_name','')} {u.get('last_name','')}".strip()
        if _fuzzy_name_match(query, full_name):
            score = sum(min(_levenshtein(qw, nw) for nw in full_name.split()) for qw in terms)
            if score < best_score:
                best_score, best_uid, best_name = score, str(u["id"]), full_name

    if best_uid:
        _uid_cache[query] = (best_uid, best_name, True)
        return best_uid, best_name, True

    return None, None, False


# ─── Meals ────────────────────────────────────────────────────────────────────

def get_user_meals(user_id: str) -> list:
    """מחזיר רשימת ארוחות עם המזונות שלהן."""
    data = _post("/coach/v2-getAllUserMeals", {"user_id": user_id})
    return data.get("data", {}).get("new_meals", [])

def find_meal_and_food(meals: list, meal_name: str, food_hint: str) -> tuple:
    """
    מחזיר (meal_id, food_row) לפי שם ארוחה ו-food_hint (group_hint / "במקום X").
    """
    target_meal = None
    for m in meals:
        if meal_name in m.get("meal_name", ""):
            target_meal = m
            break
    if not target_meal:
        if not meals:
            return None, None, "❌ לא נמצאו ארוחות למתאמן זה. בדוק שהתפריט הוגדר ב-auto-fit."
        available = [m.get("meal_name","").strip() for m in meals]
        return None, None, f"❌ לא נמצאה ארוחה '{meal_name}'. ארוחות: {', '.join(available)}"

    foods = target_meal.get("mealFoods") or target_meal.get("new_meal_food") or []
    if not foods:
        return target_meal["id"], None, f"❌ ארוחת {meal_name} ריקה — בדוק שהתפריט הוגדר ב-auto-fit"

    if food_hint:
        # נרמל: ה' הידיעה + ביטויי בישול ("אחרי בישול" → "מבושל" וכו')
        normalized_hint = normalize_food_query(food_hint)
        hint_words = normalized_hint.split()

        def _norm(s):
            return normalize_food_query(s)

        # חיפוש: כל מילות ה-hint נמצאות בשם המזון (אחרי נרמול)
        match = next((f for f in foods if all(
            w in _norm(f.get("food_name", ""))
            for w in hint_words
        )), None)

        # חיפוש רחב יותר — מילה ראשונה בלבד
        if not match:
            match = next((f for f in foods if hint_words[0] in _norm(f.get("food_name", ""))), None)

        if not match:
            available_foods = [f.get("food_name","") for f in foods]
            return target_meal["id"], None, (
                f"❌ לא נמצא '{food_hint}' בארוחת {meal_name}.\n"
                f"מזונות בארוחה: {', '.join(available_foods)}"
            )
        return target_meal["id"], match, None

    return target_meal["id"], foods[0], None


# ─── Food search ──────────────────────────────────────────────────────────────

def search_food(query: str, coach_id: str = "") -> list:
    """מחזיר רשימת מזונות מהמאגר."""
    params = {
        "type": "all",
        "coach_id": coach_id or load_coach_id(),
        "search": query
    }
    r = requests.get(
        f"{FOOD_API}/v2-food-list",
        params=params,
        headers={"api-access-token": FOOD_TOKEN, "Content-Type": "application/json"},
        timeout=10
    )
    r.raise_for_status()
    data = r.json()
    raw = data.get("data", [])
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get("foods", [])
    return []

def normalize_food_query(q: str) -> str:
    """מסיר ה' הידיעה מכל מילה ומנרמל ביטויי בישול נפוצים."""
    words = re.split(r'\s+', q.strip())  # מנרמל רווחים כפולים
    words = [re.sub(r'^ה(?=[א-ת])', '', w) for w in words if w]
    result = " ".join(words)
    # ביטויי בישול נפוצים
    result = re.sub(r'לפני בשול\b', 'לפני בישול', result)
    result = re.sub(r'(?:אחרי|לאחר) בישול\b', 'מבושל', result)
    result = re.sub(r'לא\s+מבושל\b', 'לפני בישול', result)
    return result

def find_best_food(query: str, coach_id: str = ""):
    """
    מחזיר (best_match, alternatives).
    best_match: התאמה מדויקת/הכי קרובה.
    alternatives: עד 5 תוצאות אם אין התאמה מדויקת.
    """
    norm_query = normalize_food_query(query)
    foods = search_food(norm_query, coach_id)
    if not foods and norm_query != query:
        foods = search_food(query, coach_id)
    if not foods:
        first_word = norm_query.split()[0]
        foods = search_food(first_word, coach_id)

    if not foods:
        return None, []

    q_lower = norm_query.lower()
    exact         = next((f for f in foods if f.get("food_name","").lower() == q_lower), None)
    starts_list   = [f for f in foods if f.get("food_name","").lower().startswith(q_lower)]
    contains_list = [f for f in foods if q_lower in f.get("food_name","").lower()]

    if exact:
        best = exact
    elif len(starts_list) == 1:
        best = starts_list[0]
    elif len(contains_list) == 1:
        best = contains_list[0]
    else:
        best = None  # מרובה אפשרויות — לא בוחרים אוטומטית

    return best, foods[:5]


# ─── Add food to meal ─────────────────────────────────────────────────────────

def add_food_to_meal(user_id: str, meal_id: int, food: dict, food_row: dict) -> str:
    """
    מוסיף מזון לארוחה.
    אם food_row קיים → מוסיף כ-תחליף (v2-addUserSubmealFood)
    אם food_row None → מוסיף מזון חדש (v2-addUserMealFood)
    """
    if food_row:
        # תחליף — מוסיף כאופציה חלופית בקבוצה הקיימת
        body = {
            "mavap_status": "0",
            "user_id": str(user_id),
            "new_meal_food_id": str(food_row["id"]),
            "sub_new_user_food_id": food["id"],
            "sub_food_meal_id": str(meal_id),
            "sub_new_user_food_name": food.get("food_name", ""),
            "sub_new_user_food_fat": food.get("fat", 0),
            "sub_new_user_food_carb": food.get("carbs", 0),
            "sub_new_user_food_protein": food.get("protein", 0),
            "sub_new_user_food_calories": food.get("calories", 0),
            "sub_new_user_food_gram_value": str(food.get("grams") or food.get("gram_value") or "100"),
            "sub_new_user_food_cup_value": str(food.get("cups") or food.get("cup_value") or "0.00"),
            "new_meal_food_measure": "grams",
        }
        data = _post("/coach/v2-addUserSubmealFood", body)
        if data.get("status"):
            return f"✅ נוסף כתחליף ל-{food_row.get('food_name','')}: {food['food_name']}"
        return f"❌ שגיאת API: {data.get('message','')}"
    else:
        # מזון חדש — מוסיף ישירות לארוחה
        body = {
            "mavap_status": "0",
            "user_id": str(user_id),
            "new_user_food_id": food["id"],
            "food_meal_id": str(meal_id),
            "new_user_food_name": food.get("food_name", ""),
            "new_user_food_fat": food.get("fat", 0),
            "new_user_food_carb": food.get("carbs", 0),
            "new_user_food_protein": food.get("protein", 0),
            "new_user_food_calories": food.get("calories", 0),
            "new_user_food_gram_value": str(food.get("grams") or food.get("gram_value") or "100"),
            "new_user_food_cup_value": str(food.get("cups") or food.get("cup_value") or "0.00"),
            "food_measure": "grams",
        }
        data = _post("/coach/v2-addUserMealFood", body)
        if data.get("status"):
            return f"✅ נוסף: {food['food_name']}"
        return f"❌ שגיאת API: {data.get('message','')}"


# ─── Main execute ─────────────────────────────────────────────────────────────

# ─── מזהה ארוחה מטקסט חופשי ───────────────────────────────────────────────

def _extract_meal(text: str) -> str:
    """מחלץ ארוחה מטקסט חופשי."""
    t = text.replace("ארוחת ", "").replace("ארוחה", "")
    for word, key in [
        ("ערב","ערב"), ("צהריים","צהריים"), ("צהרים","צהריים"),
        ("בוקר","בוקר"), ("ביניים","ביניים"), ("לילה","ערב"),
    ]:
        if word in t:
            return key
    return ""

_VERB_PAT = r"(?:הוסיפ[יי]?|הוסיף|תוסיפ[יי]?|הוסף|החלף[יי]?|החליף|תחליף|תחליפ[יי]?|להוסיף|להחליף)"

def _extract_foods(text: str):
    """מחלץ (מזון_חדש, מזון_קיים) מטקסט. מחזיר (new_food, group_hint)."""
    # סוגריים: (טונה) במקום (אורז)
    parens = re.findall(r"\(([^)]+)\)", text)
    if len(parens) >= 2:
        return parens[0].strip(), parens[1].strip()
    if len(parens) == 1:
        return parens[0].strip(), ""

    # "הוסף X במקום Y"
    m = re.search(
        _VERB_PAT + r"\s+([^\n]{2,40?})\s+במקום(?:\s+של)?\s+([^\n]{2,40})",
        text,
    )
    if m:
        return m.group(1).strip(), m.group(2).strip()

    # "הוסף X" בלבד
    m2 = re.search(
        _VERB_PAT + r"\s+([^\n]{2,50})",
        text,
    )
    if m2:
        return m2.group(1).strip(), ""

    # "X במקום Y" ללא פועל מפורש
    m3 = re.search(r"([^\n]{2,30})\s+במקום(?:\s+של)?\s+([^\n]{2,40})", text)
    if m3:
        return m3.group(1).strip(), m3.group(2).strip()

    return "", ""


def parse_message(text: str) -> dict:
    """
    מנסה לפרסר הודעה — גם בתבנית מובנית וגם בשפה חופשית.
    מחזיר dict עם: name, meal, change, confidence (0-100).
    """
    result = {}
    conf = 100

    # ── תבנית מובנית (שם: / ארוחה: / שנה:) ───────────────────────────────
    for line in text.strip().split("\n"):
        line = line.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().strip("*").strip()
        val = val.strip()
        if not key or not val:
            continue
        if key in ("שם", "name", "מספר", "טלפון", "phone"):
            # הסרת emoji ותווים לא-עבריים/לא-ספרתיים משם
            cleaned = re.sub(r'[^א-ת\u200F\s0-9 \-]', '', val).strip()
            if cleaned:
                result["name"] = cleaned
        elif key in ("ארוחה", "meal", "ארוחת"):
            # נרמול ארוחה דרך _extract_meal (כולל "צהרים"→"צהריים", "לילה"→"ערב" וכו')
            normalized = _extract_meal(val)
            result["meal"] = normalized if normalized else val
        elif key in ("שנה", "change", "פעולה", "בקשה"):
            result["change"] = val
        elif key in ("החלף", "הוסף"):
            result["change"] = f"{key} {val}"
        elif key == "הוספה":
            # פורמט חדש: "טונה בשמן ל אורז לבן" → הוסף (טונה) במקום (אורז)
            if " ל " in val:
                new_f, hint = val.split(" ל ", 1)
                result["change"] = f"הוסף ({new_f.strip()}) במקום ({hint.strip()})"
            else:
                result["change"] = f"הוסף ({val.strip()})"

    if "name" in result and "change" in result:
        result.setdefault("meal", "ערב")
        result["confidence"] = 100
        return result

    # ── שפה חופשית ───────────────────────────────────────────────────────────
    full = " ".join(text.strip().split("\n"))

    # שם מתאמן — מחלצים ראשון ומנקים מהטקסט לפני חילוץ מזון
    name_match = re.search(
        r"(?:של\s+|עבור\s+|ל\s+|ל(?=[\u05D0-\u05EA]))([\u05D0-\u05EA]{2,}(?:\s+[\u05D0-\u05EA]{2,})?)",
        full,
    )
    if name_match:
        result["name"] = name_match.group(1).strip()
        conf = 85
        clean_full = full[:name_match.start()] + full[name_match.end():]
        clean_full = re.sub(r'\s+', ' ', clean_full).strip()
        # הסר ביטויי ארוחה מהטקסט לפני חילוץ מזון
        clean_full = re.sub(r'ב?(?:ארוחת|ארוחה)\s+[\u05D0-\u05EA]+', '', clean_full)
        clean_full = re.sub(r'\bב(?:ערב|בוקר|צהריים|ביניים)\b', '', clean_full)
        clean_full = re.sub(r'\s+', ' ', clean_full).strip()
    else:
        conf = 40
        clean_full = full

    # מזון — על הטקסט ללא השם
    new_food, group_hint = _extract_foods(clean_full)
    if new_food:
        change_str = f"הוסף ({new_food})"
        if group_hint:
            change_str += f" במקום ({group_hint})"
        result["change"] = change_str

    # ארוחה
    meal = _extract_meal(full)
    if meal:
        result["meal"] = meal

    if not result.get("change"):
        conf = min(conf, 50)

    result.setdefault("meal", "ערב")
    result["confidence"] = conf
    return result


def execute_request(request_text: str, force: bool = False,
                    name_override: str = "", meal_override: str = "",
                    food_override: str = "", hint_override: str = "") -> str:
    parsed = parse_message(request_text)
    if "name" not in parsed or "change" not in parsed:
        return (
            "פורמט שגוי. שלח:\n"
            "שם: <שם מתאמן>\n"
            "ארוחה: ערב / בוקר / צהריים\n"
            "הוספה: <מזון חדש> ל <מזון קיים>"
        )

    if not force and parsed.get("confidence", 100) < 90:
        meal  = parsed.get("meal", "ערב")
        summary = (
            f"שם מתאמן: {parsed['name']}\n"
            f"ארוחה: {meal}\n"
            f"פעולה: {parsed['change']}"
        )
        return f"CONFIRM:{summary}"

    name   = name_override if name_override else parsed["name"]
    meal   = meal_override if meal_override else parsed.get("meal", "ערב")
    change = parsed["change"]

    # שם ארוחה מלא
    meal_map = {"ערב": "ארוחת ערב", "בוקר": "ארוחת בוקר",
                "צהריים": "ארוחת צהריים", "ביניים": "ארוחת ביניים"}
    # תמיכה ב-override גם עם "ארוחת X"
    if meal_override:
        m = _extract_meal(meal_override)
        meal = m if m else meal_override
    full_meal = meal_map.get(meal, meal if "ארוחת" in meal else f"ארוחת {meal}")

    # מצא מתאמן
    user_id, full_name, is_fuzzy = find_user(name)
    if not user_id:
        return f"❌ לא נמצא מתאמן בשם '{name}'"

    # אישור על התאמה עמומה (אם לא כבר אושר)
    if is_fuzzy and not force:
        return f"CONFIRM_FUZZY:{full_name}"

    # parse הוסף/החלף (X) במקום (Y) / במקום של (Y)
    _VP = r'(?:להוסיף|הוסיפ[יי]?|הוסיף|תוסיפ[יי]?|הוסף|החלף[יי]?|החליף|תחליף|תחליפ[יי]?|להחליף)'
    add_match = (
        re.search(_VP + r'\s+(.+?)\s+במקום(?:\s+של)?\s+(.+)$', change)
        or re.search(_VP + r'\s+(.+)', change)
        or re.search(r'^(.+?)\s+במקום(?:\s+של)?\s+(.+)$', change)  # ללא פועל
    )

    if not add_match:
        return "לא הבנתי. שלח: הוסף (מזון) במקום (מזון קיים)"

    new_food_raw = add_match.group(1).strip()
    group_hint_raw = (add_match.group(2) or "").strip() if add_match.lastindex and add_match.lastindex >= 2 else ""

    # חלץ מסוגריים
    paren = re.search(r'\(([^)]+)\)', new_food_raw)
    new_food_query = normalize_food_query(paren.group(1).strip() if paren else new_food_raw)
    paren2 = re.search(r'\(([^)]+)\)', group_hint_raw)
    group_hint = paren2.group(1).strip() if paren2 else group_hint_raw

    # קבל ארוחות
    meals = get_user_meals(user_id)

    # אם סופק hint_override — החלף את group_hint
    if hint_override:
        group_hint = hint_override

    meal_id, food_row, err = find_meal_and_food(meals, full_meal, group_hint)
    if err:
        # ארוחה לא נמצאה — הצג רשימה ממוספרת
        if "לא נמצאה ארוחה" in err or "לא נמצאו ארוחות" in err:
            available = [m.get("meal_name","").strip() for m in meals]
            if available:
                opts = "\n".join(f"{i+1}. {a}" for i, a in enumerate(available))
                return f"MEAL_OPTIONS:{full_meal}|" + "\n".join(available) + f"\n❓ לא מצאתי '{full_meal}'. יש:\n{opts}\n\nשלח מספר או שם ארוחה."
        # group_hint לא נמצא — הצג מזונות בארוחה
        if meal_id and "לא נמצא" in err:
            meal_foods = next((m.get("mealFoods") or m.get("new_meal_food") or []
                               for m in meals if m["id"] == meal_id), [])
            if meal_foods:
                opts = "\n".join(f"{i+1}. {f.get('food_name','')}" for i, f in enumerate(meal_foods))
                foods_list = "|".join(f.get("food_name","") for f in meal_foods)
                return f"HINT_OPTIONS:{foods_list}\n❓ לא מצאתי '{group_hint}'. מה להחליף?\n{opts}\n\nשלח מספר או שם מזון."
        return err

    # חפש מזון — אם food_override סופק → חפש ישירות
    coach_id = load_coach_id()
    if food_override:
        foods = search_food(normalize_food_query(food_override), coach_id)
        best_food = next((f for f in foods if f.get("food_name","").lower() == food_override.lower()), None) \
                    or (foods[0] if foods else None)
        if not best_food:
            return f"❓ לא נמצא '{food_override}' במאגר."
        return add_food_to_meal(user_id, meal_id, best_food, food_row)

    best_food, alternatives = find_best_food(new_food_query, coach_id)

    if not best_food:
        if alternatives:
            options = "\n".join(f"{i+1}. {f['food_name']}" for i, f in enumerate(alternatives[:5]))
            return f"❓ מספר אפשרויות עבור '{new_food_query}':\n{options}\n\nשלח מספר או שם מדויק."
        return f"❓ לא נמצא '{new_food_query}' במאגר. נסה שם ספציפי יותר."

    return add_food_to_meal(user_id, meal_id, best_food, food_row)


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if a != "--force"]

    def _pop_arg(flag):
        if flag in args:
            i = args.index(flag)
            val = args[i + 1] if i + 1 < len(args) else ""
            args[:] = args[:i] + args[i + 2:]
            return val
        return ""

    name_override  = _pop_arg("--name")
    meal_override  = _pop_arg("--meal")
    food_override  = _pop_arg("--food")
    hint_override  = _pop_arg("--hint")

    if not args:
        print("Usage: python3 autofit_api.py [--force] [--name <name>] <request>")
        sys.exit(1)
    request = " ".join(args)
    try:
        print(execute_request(request, force=force,
                              name_override=name_override,
                              meal_override=meal_override,
                              food_override=food_override,
                              hint_override=hint_override))
    except Exception as e:
        print(f"שגיאה: {e}", file=sys.stderr)
        sys.exit(1)
