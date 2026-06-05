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
import time
from pathlib import Path

BACKEND    = "https://chat.auto-fit.co.il"
FOOD_API   = "https://food.we-site.co.il/api"
FOOD_TOKEN = "eb8b0f58c895019fcbc3bb17480ced3a2d1e12a346d6ed0f0d0267a24587a203"
SESSION_FILE   = Path(__file__).parent / "session.json"
PHONES_FILE    = Path(__file__).parent / "phones.json"
USER_CACHE_FILE = Path(__file__).parent / "user_cache.json"
USER_CACHE_TTL  = 300  # 5 דקות

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

    # cache לקובץ — מונע סריקה מחדש בכל קריאה (TTL=5 דק')
    all_users = []
    try:
        if USER_CACHE_FILE.exists():
            cached = json.loads(USER_CACHE_FILE.read_text(encoding="utf-8"))
            if time.time() - cached.get("ts", 0) < USER_CACHE_TTL:
                all_users = cached["users"]
    except:
        pass

    if not all_users:
        page, per_page = 1, 100
        while True:
            data = _post("/coach/get-coach-users", {"page": page, "limit": per_page})
            users = data.get("data", [])
            all_users.extend(users)
            total = data.get("pagination", {}).get("total", 0)
            if page * per_page >= total:
                break
            page += 1
        try:
            USER_CACHE_FILE.write_text(
                json.dumps({"ts": time.time(), "users": all_users}, ensure_ascii=False),
                encoding="utf-8"
            )
        except:
            pass

    # נרמול גרש לצורך השוואה (קגנוביץ = קגנוביץ')
    def _ng(s):
        return re.sub(r"[\u05F3\u05F4\']", "", s)

    def _full(u):
        return (u.get("name") or f"{u.get('first_name','')} {u.get('last_name','')}").strip()

    terms_norm = [_ng(t) for t in terms]

    # pass 1: כל מילות השאילתה נמצאות בשם
    # אם שאילתה קצרה מהשם המלא → is_fuzzy=True (שם חלקי → שאל אישור)
    exact_match = None   # אותו מספר מילים — התאמה מדויקת
    partial_match = None # מספר מילים פחות — התאמה חלקית
    for u in all_users:
        full_name = _full(u)
        name_words_norm = [_ng(w) for w in full_name.split()]
        if all(nt in name_words_norm for nt in terms_norm):
            if len(terms) == len(full_name.split()):
                exact_match = (str(u["id"]), full_name)
                break
            elif partial_match is None:
                partial_match = (str(u["id"]), full_name)

    if exact_match:
        uid, full_name = exact_match
        _uid_cache[query] = (uid, full_name, False)
        return uid, full_name, False
    if partial_match:
        uid, full_name = partial_match
        _uid_cache[query] = (uid, full_name, True)  # שם חלקי → תמיד שאל
        return uid, full_name, True

    # pass 2 (legacy fallback — נדיר אחרי pass 1 המשופר)
    if len(terms) == 1:
        for u in all_users:
            full_name = _full(u)
            name_words_norm = [_ng(w) for w in full_name.split()]
            if _ng(first_term) in name_words_norm:
                uid = str(u["id"])
                _uid_cache[query] = (uid, full_name, True)
                return uid, full_name, True

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

def _fmt_grams(food: dict) -> str:
    """מחזיר מחרוזת גרמים מפורמטת — משתמש ב-quantity (הכמות האמיתית בתפריט)."""
    q = food.get("quantity") or food.get("gram_value") or ""
    if not q:
        return ""
    try:
        q = int(round(float(q)))
    except (ValueError, TypeError):
        pass
    return f" — {q} גרם"


def format_menu(user_id: str, full_name: str) -> str:
    """מחזיר תפריט מלא של מתאמן כטקסט מפורמט ל-WhatsApp."""
    meals = get_user_meals(user_id)
    if not meals:
        return f"❌ לא נמצאו ארוחות עבור {full_name}"

    MEAL_EMOJI = {
        "ארוחת בוקר": "🍳", "ארוחת צהריים": "🥗",
        "ארוחת ערב": "🌙", "ארוחת ביניים": "🍎",
        "ארוחת לילה": "🌙",
    }

    lines = [f"📋 תפריט של *{full_name}*:\n"]
    total_calories = 0

    for meal in meals:
        meal_name = meal.get("meal_name", "ארוחה").strip()
        emoji = MEAL_EMOJI.get(meal_name, "🍽")
        meal_cals = meal.get("meal_totals", {}).get("calories") or 0
        try: meal_cals = int(round(float(meal_cals)))
        except: meal_cals = 0
        total_calories += meal_cals
        cal_str = f" _{meal_cals} קל'_" if meal_cals else ""
        lines.append(f"{emoji} *{meal_name}*{cal_str}")

        foods = meal.get("mealFoods") or meal.get("new_meal_food") or []
        if not foods:
            lines.append("_(ריקה)_")
        for food in foods:
            fname = food.get("food_name", "?")
            # מזון ראשי — מודגש
            lines.append(f"*• {fname}*{_fmt_grams(food)}")
            # אופציות — italic קטן עם כניסה
            subs = food.get("subFoods") or []
            for sub in subs:
                sname = sub.get("food_name", "?")
                lines.append(f"_  ↳ {sname}{_fmt_grams(sub)}_")
        lines.append("")

    if total_calories:
        lines.append(f"🔥 *סה\"כ יומי: {total_calories} קלוריות*")

    return "\n".join(lines).strip()


def find_meal_and_food(meals: list, meal_name: str, food_hint: str) -> tuple:
    """
    מחזיר (meal_id, food_row) לפי שם ארוחה ו-food_hint (group_hint / "במקום X").
    """
    # נרמל ה' הידיעה משם הארוחה לחיפוש ("ארוחת הערב" → "ארוחת ערב")
    _meal_search = re.sub(r'(?<=\s)ה(?=[א-ת])', '', meal_name).strip()
    target_meal = None
    for m in meals:
        db_name = m.get("meal_name", "")
        if _meal_search in db_name or meal_name in db_name:
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

    return target_meal["id"], None, None  # אין food_hint → מזון חדש, לא תחליף


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

_PROTECT_L = frozenset({"לפני", "לאחר"})  # מילות עזר שלא מנקים את ל' שלהן
_PROTECT_H = frozenset({"הודו"})  # מילים שה' חלק מהמילה (לא ה' הידיעה)

def normalize_food_query(q: str) -> str:
    """מסיר ה' הידיעה, תווי תבנית (<>), ומנרמל ביטויי בישול נפוצים."""
    q = re.sub(r'[<>]', '', q)
    # ביטויי בישול — תומך גם ב"הבישול" / "הבשול" (עם ה' הידיעה)
    q = re.sub(r'לפני\s+ה?(?:בישול|בשול)\b', 'לפני בישול', q)
    q = re.sub(r'(?:אחרי|לאחר)\s+ה?(?:בישול|בשול)\b', 'מבושל', q)
    q = re.sub(r'לא\s+מבושל\b', 'לפני בישול', q)
    words = re.split(r'\s+', q.strip())
    # ה' הידיעה — אל תסיר אם המילה ב-_PROTECT_H (ה' חלק מהשם)
    words = [w if w in _PROTECT_H else re.sub(r'^ה(?=[א-ת])', '', w) for w in words if w]
    # ל' מיידית: "לאורז"→"אורז" — רק כשנשאר ≥3 תווים, ולא מילות עזר כמו "לפני"/"לאחר"
    words = [w if w in _PROTECT_L else re.sub(r'^ל(?=[א-ת]{3,})', '', w) for w in words]
    return " ".join(words)

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

def add_food_to_meal(user_id: str, meal_id: int, food: dict, food_row: dict, grams_override: str = None) -> str:
    """
    מוסיף מזון לארוחה.
    אם food_row קיים → מוסיף כ-תחליף (v2-addUserSubmealFood)
    אם food_row None → מוסיף מזון חדש (v2-addUserMealFood)
    grams_override: אם נשלח, משתמש בגרמים אלו במקום ברירת המחדל
    """
    if food_row:
        # תחליף — מוסיף כאופציה חלופית בקבוצה הקיימת
        # גרמים: grams_override → מהמנה האמיתית → default מאגר
        actual_grams = str(grams_override or food_row.get("gram_value") or food_row.get("grams") or food.get("grams") or food.get("gram_value") or "100")
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
            "sub_new_user_food_gram_value": actual_grams,
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
            "new_user_food_gram_value": str(grams_override or food.get("grams") or food.get("gram_value") or "100"),
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
        _VERB_PAT + r"\s+([^\n]{2,40})\s+במקום(?:\s+של)?\s+([^\n]{2,40})",
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

    # ── pre-process: פסיק/רווח לפני מילת מפתח → שורה חדשה ─────────────────
    # פסיק: "שם: X, ארוחה: Y" → שורות
    text = re.sub(r'[,،]\s*(?=(?:שם|ארוחה|הוספה|שנה|החלף|הוסף|תחליף|פעולה|בקשה)\s*:)', '\n', text)
    # רווח: "ארוחה: X הוספה: Y" → שורות (כשאין newline)
    text = re.sub(r'(?<=[^\n])\s+(?=(?:הוספה|שנה|החלף|הוסף|תחליף|פעולה|בקשה)\s*:)', '\n', text)
    # "הוספה:\nX" → "הוספה: X" (ריווח לפני תוכן בשורה הבאה)
    _KEYS_PAT = r'(?:הוספה|שנה|תחליף|change|פעולה|בקשה)'
    lines = text.split('\n')
    merged = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(rf'^\s*{_KEYS_PAT}\s*:\s*$', line) and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if nxt and not re.match(r'^(?:שם|ארוחה|הוספה|שנה|תחליף|change)\s*:', nxt):
                merged.append(line.rstrip(':').rstrip() + ': ' + nxt)
                i += 2
                continue
        merged.append(line)
        i += 1
    text = '\n'.join(merged)

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
            cleaned = re.sub(r'[^א-ת\u05F3\u05F4\'\u200F\s0-9 \-]', '', val).strip()  # ׳ ״ geresh
            if cleaned:
                result["name"] = cleaned
        elif key in ("ארוחה", "meal", "ארוחת"):
            # תמיכה בריבוי ארוחות: "ערב + צהריים" → שמור רשימה
            # לא מפצלים על "ו" בתוך מילה (כמו "הבוקר") — רק "ו" עם רווחים משני צדדיו
            parts = re.split(r'[+,]|\s+[וV]\s*', val)
            all_m = [m for p in parts for m in [_extract_meal(p.strip())] if m]
            meals = list(dict.fromkeys(all_m))  # dedup תוך שמירת סדר (לילה+ערב→ערב פעם אחת)
            if len(meals) > 1:
                result["meals"] = meals  # ריבוי ארוחות
            elif meals:
                result["meal"] = meals[0]
            else:
                result["meal"] = val.strip()
        elif _extract_meal(key):
            # "ערב: X ל Y" — שם ארוחה ישירות כ-key
            meal_key = _extract_meal(key)
            v = val.strip()
            v = re.sub(r'^(?:הוסיפ[יי]?|הוסיף|תוסיפ[יי]?|הוסף|החלף[יי]?|תחליפ[יי]?)\s+', '', v)
            if " ל " in v:
                nf, ht = v.split(" ל ", 1)
            else:
                ml = re.search(r'^(.+?)\s+ל([\u05D0-\u05EA].+)$', v)
                nf, ht = (ml.group(1), ml.group(2)) if ml else (v, "")
            op = f"הוסף ({nf.strip()}) במקום ({ht.strip()})" if ht.strip() else f"הוסף ({nf.strip()})"
            if "extra_ops" not in result:
                result["extra_ops"] = []
            result["extra_ops"].append({"meal": meal_key, "change": op})
        elif key in ("שנה", "change", "פעולה", "בקשה"):
            result["change"] = val
        elif key in ("החלף", "הוסף", "תחליף"):
            result["change"] = f"{key} {val}"
        elif key in ("כתחליף ל", "כתחליף", "במקום"):
            # מעדכן את ה-hint של הוספה האחרונה
            hint_val = val.strip()
            if result.get("ops"):
                last = result["ops"][-1]
                ch = last.get("change", "")
                prs = re.findall(r'\(([^)]+)\)', ch)
                nf = prs[0] if prs else ch
                last["change"] = f"הוסף ({nf}) במקום ({hint_val})"
                result["ops"][-1] = last
            if "change" in result:
                prs2 = re.findall(r'\(([^)]+)\)', result["change"])
                nf2 = prs2[0] if prs2 else result["change"]
                result["change"] = f"הוסף ({nf2}) במקום ({hint_val})"
        elif key == "הוספה":
            # תמיכה בריבוי פעולות: כל הוספה: שורה = פעולה אחת
            # גם: "X, Y" בשורה אחת = 2 פעולות, "בנוסף ל" = synonym ל-"ל"
            def _parse_one_op(raw):
                v = raw.strip()
                v = re.sub(r'^(?:הוסיפ[יי]?|הוסיף|תוסיפ[יי]?|הוסף|החלף[יי]?|תחליפ[יי]?)\s+', '', v)
                v = re.sub(r'בנוסף\s+ל', 'ל ', v)  # "בנוסף ל X" → "ל X"
                v = re.sub(r'במקום\s+של', 'ל ', v)   # "במקום של X" → "ל X"
                v = re.sub(r'במקום', 'ל ', v)         # "במקום X" → "ל X"
                # גרמים: "עוד X גרם FOOD" / "X גרם [ל]FOOD" / "FOOD X גרם"
                extra_grams = None
                grams_m = re.search(r'\bעוד\s+(\d+)\s*גרם\b', v)
                if grams_m:
                    extra_grams = grams_m.group(1)
                    v = re.sub(r'\bעוד\s+\d+\s*גרם\s*', '', v).strip()
                else:
                    m_gs = re.match(r'^(\d+)\s*גרם\s+ל?\s*', v)  # "50 גרם [ל]אורז"
                    if m_gs:
                        extra_grams = m_gs.group(1)
                        v = v[m_gs.end():].strip()
                    else:
                        m_ge = re.search(r'^(.+?)\s+(\d+)\s*גרם\s*$', v)  # "אורז 50 גרם"
                        if m_ge:
                            extra_grams = m_ge.group(2)
                            v = m_ge.group(1).strip()
                # זהה ארוחה ספציפית בתוך הפעולה ("בבוקר", "בצהריים")
                op_meal = _extract_meal(v)
                if op_meal:
                    v = re.sub(r'\s*ב(?:ארוחת\s+)?(?:ערב|בוקר|צהריים|ביניים|לילה)\b', '', v).strip()
                if " ל " in v:
                    nf, ht = v.split(" ל ", 1)
                else:
                    m_l = re.search(r'^(.+?)\s+ל([\u05D0-\u05EA].+)$', v)
                    if m_l:
                        nf, ht = m_l.group(1), m_l.group(2)
                    else:
                        nf, ht = v, ""
                _cl = lambda s: re.sub(r'[.\s:,]+$', '', s).strip()
                change = f"הוסף ({_cl(nf)}) במקום ({_cl(ht)})" if _cl(ht) else f"הוסף ({_cl(nf)})"
                return {"change": change, "meal": op_meal, "extra_grams": extra_grams}

            # פצל ב-comma לפעולות מרובות
            raw_ops = [s.strip() for s in re.split(r',\s*', val) if s.strip()]
            for raw_op in raw_ops:
                op = _parse_one_op(raw_op)
                if "ops" not in result:
                    result["ops"] = []
                result["ops"].append(op)
            # backwards compat: change = פעולה ראשונה
            if result.get("ops") and "change" not in result:
                first = result["ops"][0]
                result["change"] = first["change"]
                if first["meal"]:
                    result.setdefault("meal", first["meal"])

    # extra_ops (פורמט "ערב: X ל Y") — אם אין change עדיין, קח מהop הראשון
    if "extra_ops" in result and "change" not in result:
        first_op = result["extra_ops"][0]
        result["change"] = first_op["change"]
        result.setdefault("meal", first_op["meal"])

    if "name" in result and "change" in result:
        result.setdefault("meal", "ערב")
        result["confidence"] = 100
        return result

    # ── שפה חופשית ───────────────────────────────────────────────────────────
    full = " ".join(text.strip().split("\n"))

    # שם מתאמן — מחלצים ראשון ומנקים מהטקסט לפני חילוץ מזון
    # (?<!\S) = ל חייב להיות בתחילת מילה; capture: מילה אחת בלבד
    # blacklist: פעלים ומילות ארוחה שלא ייתפסו כשם
    _NOT_NAME_VERBS = frozenset({"הוסיף", "הוסיפי", "תוסיפי", "תוסיף", "החלף",
                                  "תחליפי", "תחליף", "הכנס", "עדכן", "שנה", "בצע",
                                  "שלח", "עשה", "עשי", "הוציא", "מחק", "הסר",
                                  "החליף", "תחליפ", "ארוחת", "ארוחה"})
    if "name" not in result:
        # שם עדיין לא נמצא — חפש בfree text
        name_match = None
        for _m in re.finditer(
            r"(?:של\s+|עבור\s+|(?<!\S)ל\s+|(?<!\S)ל(?=[\u05D0-\u05EA]))([\u05D0-\u05EA]{2,})",
            full,
        ):
            if _m.group(1).strip() not in _NOT_NAME_VERBS:
                name_match = _m
                break
        if name_match:
            result["name"] = name_match.group(1).strip()
            conf = 85
            clean_full = full[:name_match.start()] + full[name_match.end():]
            clean_full = re.sub(r'\s+', ' ', clean_full).strip()
            clean_full = re.sub(r'ב?(?:ארוחת|ארוחה)\s+[\u05D0-\u05EA]+', '', clean_full)
            clean_full = re.sub(r'\bב(?:ערב|בוקר|צהריים|ביניים)\b', '', clean_full)
            clean_full = re.sub(r'\s+', ' ', clean_full).strip()
        else:
            conf = 40
            clean_full = full
    else:
        # שם נמצא בפורמט מובנה — אל תדרוס, חפש מזון מהטקסט המלא
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
    # ריבוי אנשים: "שם: X ... שם: Y" → מפצל ומטפל בנפרד
    if not name_override:
        _parts = re.split(r'(?m)(?=^\s*שם\s*:)', request_text.strip())
        _parts = [p.strip() for p in _parts if p.strip() and re.search(r'שם\s*:', p)]
        if len(_parts) > 1:
            sub_results = []
            for part in _parts:
                sub_results.append(execute_request(part, force, name_override,
                                                   meal_override, food_override, hint_override))
            return "\n\n─────────────\n\n".join(sub_results)

    parsed = parse_message(request_text)
    if "name" not in parsed or "change" not in parsed:
        missing = []
        if "name" not in parsed:  missing.append("שם מתאמן")
        if "change" not in parsed: missing.append("מה להוסיף")
        hint = "ℹ️ חסר: " + ", ".join(missing) + "\n\n"
        return (
            hint +
            "פורמט:\n"
            "שם: <שם מתאמן>\n"
            "ארוחה: ערב / בוקר / צהריים\n"
            "הוספה: <מזון חדש> ל <מזון קיים>"
        )

    meal_map = {"ערב": "ארוחת ערב", "בוקר": "ארוחת בוקר",
                "צהריים": "ארוחת צהריים", "ביניים": "ארוחת ביניים"}

    if not force:
        # בדוק שם לפני CONFIRM — מזהה fuzzy match כבר כאן
        raw_name = parsed["name"]
        uid_check, found_name, is_fuzzy = find_user(raw_name)
        if not uid_check:
            return f"NAME_NOT_FOUND:{raw_name}"

        # confidence >= 90 + שם מדויק → בצע ישירות, ללא אישור
        if parsed.get("confidence", 100) >= 90 and not is_fuzzy:
            return execute_request(request_text, force=True, name_override=found_name,
                                   meal_override=meal_override, food_override=food_override,
                                   hint_override=hint_override)

        # שם להצגה — אם fuzzy → מציין את התיקון
        if is_fuzzy:
            display_name = f"{found_name} (מצאתי עבור \"{raw_name}\")"
        else:
            display_name = found_name

        meals_list = parsed.get("meals") or [parsed.get("meal", "ערב")]
        meal_display = " + ".join(meal_map.get(m, m) for m in meals_list)

        # בנה סיכום — תמיכה בריבוי פעולות
        ops_to_show = parsed.get("ops") or [{"change": parsed.get("change",""), "meal": None}]
        action_lines = []
        for op in ops_to_show:
            op_change = op.get("change", "")
            parens = re.findall(r'\(([^)]+)\)', op_change)
            nf = parens[0] if parens else op_change
            of = parens[1] if len(parens) >= 2 else ""
            op_meal = op.get("meal")
            meal_prefix = f"[{meal_map.get(op_meal, op_meal)}] " if op_meal else ""
            action_lines.append(f"{meal_prefix}➕ {nf}" + (f" ← {of}" if of else ""))

        summary = f"👤 {display_name}\n🍽 {meal_display}\n" + "\n".join(action_lines)

        if is_fuzzy:
            # קידוד: CONFIRM_WITH_NAME:{שם מתוקן}|||{summary}
            return f"CONFIRM_WITH_NAME:{found_name}|||{summary}"
        return f"CONFIRM:{summary}"

    name   = name_override if name_override else parsed["name"]

    # בנה רשימת ארוחות ברירת מחדל
    if meal_override:
        m = _extract_meal(meal_override)
        meals_list = [m if m else meal_override]
    else:
        meals_list = parsed.get("meals") or [parsed.get("meal", "ערב")]

    # מצא מתאמן (פעם אחת)
    user_id, full_name, is_fuzzy = find_user(name)
    if not user_id:
        return f"NAME_NOT_FOUND:{name}"
    # safety: אם עדיין fuzzy אחרי name_override — שלח שגיאה
    if is_fuzzy and name_override and name_override == name:
        return f"❌ לא מצאתי '{name}' בדיוק — שלח שם מלא."

    # קבל ארוחות (פעם אחת)
    all_meals = get_user_meals(user_id)
    coach_id = load_coach_id()

    # ── בנה רשימת פעולות ─────────────────────────────────────────
    # food/hint override → פעולה בודדת (תשובה לתיקון)
    if food_override or hint_override:
        ops_list = [{"change": parsed["change"], "meal": None}]
    else:
        ops_list = parsed.get("ops") or [{"change": parsed["change"], "meal": None}]

    _VP = r'(?:להוסיף|הוסיפ[יי]?|הוסיף|תוסיפ[יי]?|הוסף|החלף[יי]?|החליף|תחליף|תחליפ[יי]?|להחליף)'

    all_results = []

    for op_idx, op in enumerate(ops_list):
        op_change = op.get("change") or parsed.get("change", "")
        op_meal_str = op.get("meal") or None  # ארוחה ספציפית לפעולה זו

        # parse food + hint מהchange
        add_match = (
            re.search(_VP + r'\s+(.+?)\s+במקום(?:\s+של)?\s+(.+)$', op_change)
            or re.search(_VP + r'\s+(.+)', op_change)
            or re.search(r'^(.+?)\s+במקום(?:\s+של)?\s+(.+)$', op_change)
        )
        if not add_match:
            if len(ops_list) == 1:
                return "לא הבנתי. שלח: הוסף (מזון) במקום (מזון קיים)"
            all_results.append(f"⚠️ לא הבנתי את הפעולה: {op_change[:40]}")
            continue

        new_food_raw = add_match.group(1).strip()
        group_hint_raw = (add_match.group(2) or "").strip() if add_match.lastindex and add_match.lastindex >= 2 else ""

        # ניקוי "בערב/בבוקר/בצהריים" שדלף לשם המזון מטקסט חופשי
        new_food_raw = re.sub(r'\s*ב(?:ארוחת\s+)?(?:ערב|בוקר|צהריים|ביניים|לילה)\b', '', new_food_raw).strip()

        # חלץ מסוגריים תחילה — כדי שזיהוי הגרמים יעבוד גם על "(50 גרם אורז)"
        paren = re.search(r'\(([^)]+)\)', new_food_raw)
        new_food_clean = paren.group(1).strip() if paren else new_food_raw
        paren2 = re.search(r'\(([^)]+)\)', group_hint_raw)
        group_hint = paren2.group(1).strip() if paren2 else group_hint_raw

        # גרמים: מה-op / "עוד X גרם" / "X גרם [ל]FOOD" / "FOOD X גרם"
        extra_grams = op.get("extra_grams")
        grams_in_food = re.search(r'\bעוד\s+(\d+)\s*גרם\b', new_food_clean)
        if grams_in_food:
            extra_grams = grams_in_food.group(1)
            new_food_clean = re.sub(r'\bעוד\s+\d+\s*גרם\s*', '', new_food_clean).strip()
        elif not extra_grams:
            m_gs2 = re.match(r'^(\d+)\s*גרם\s+ל?\s*', new_food_clean)  # "50 גרם [ל]אורז"
            if m_gs2:
                extra_grams = m_gs2.group(1)
                new_food_clean = new_food_clean[m_gs2.end():].strip()
            else:
                m_ge2 = re.search(r'^(.+?)\s+(\d+)\s*גרם\s*$', new_food_clean)  # "אורז 50 גרם"
                if m_ge2:
                    extra_grams = m_ge2.group(2)
                    new_food_clean = m_ge2.group(1).strip()

        new_food_query = normalize_food_query(new_food_clean)

        if hint_override and op_idx == 0:
            group_hint = hint_override

        # חפש מזון
        if food_override and op_idx == 0:
            foods = search_food(normalize_food_query(food_override), coach_id)
            best_food = next((f for f in foods if f.get("food_name","").lower() == food_override.lower()), None) \
                        or (foods[0] if foods else None)
            if not best_food:
                all_results.append(f"❓ לא נמצא '{food_override}' במאגר.")
                continue
        else:
            best_food, alternatives = find_best_food(new_food_query, coach_id)
            if not best_food:
                if len(ops_list) == 1:
                    if alternatives:
                        options = "\n".join(f"{i+1}. {f['food_name']}" for i, f in enumerate(alternatives[:5]))
                        alts_pipe = "|".join(f.get("food_name","") for f in alternatives[:5])
                        # כולל את השאילתה המקורית כדי שindex.js יוכל לזכור את הבחירה
                        return (f"FOOD_OPTIONS:{new_food_query}||{alts_pipe}\n"
                                f"לא מצאתי {new_food_query} כאופציה, מה שכן מצאתי זה:\n"
                                f"{options}\n\n"
                                f"שלח מספר לבחירה, או שם מדויק יותר.")
                    return (f"לא מצאתי {new_food_query} במאגר.\n"
                            f"נסה שם ספציפי יותר — לדוגמא: 'פסטה מבושלת', 'גבינה 5% שומן'.")
                all_results.append(f"⚠️ לא מצאתי '{new_food_query}' — דלגתי")
                continue

        # קבע ארוחות לפעולה זו
        if op_meal_str:
            op_meals = [op_meal_str]
        elif meal_override:
            op_meals = meals_list
        else:
            op_meals = meals_list

        # עבד כל ארוחה
        for meal_item in op_meals:
            full_meal = meal_map.get(meal_item, meal_item if "ארוחת" in meal_item else f"ארוחת {meal_item}")

            meal_id, food_row, err = find_meal_and_food(all_meals, full_meal, group_hint)
            if err:
                multi_meal = len(op_meals) > 1 or len(ops_list) > 1
                prefix = ("\n\n".join(all_results) + "\n\n") if all_results else ""
                if "לא נמצאה ארוחה" in err or "לא נמצאו ארוחות" in err:
                    available = [m.get("meal_name","").strip() for m in all_meals]
                    if available and not multi_meal:
                        opts = "\n".join(f"{i+1}. {a}" for i, a in enumerate(available))
                        return f"MEAL_OPTIONS:{full_meal}|" + "|".join(available) + f"\n{prefix}❓ לא מצאתי '{full_meal}'. יש:\n{opts}\n\nשלח מספר או שם ארוחה."
                if meal_id and "לא נמצא" in err:
                    if not multi_meal:
                        meal_foods = next((m.get("mealFoods") or m.get("new_meal_food") or []
                                           for m in all_meals if m["id"] == meal_id), [])
                        if meal_foods:
                            opts = "\n".join(f"{i+1}. {f.get('food_name','')}" for i, f in enumerate(meal_foods))
                            foods_list = "|".join(f.get("food_name","") for f in meal_foods)
                            return (f"HINT_OPTIONS:{group_hint}||{foods_list}\n{prefix}"
                                    f"לא מצאתי {group_hint} ב{full_meal}, במה תרצה להחליף את {new_food_query}?\n"
                                    f"המזונות שיש בארוחה:\n{opts}\n\n"
                                    f"שלח מספר או שם.")
                    all_results.append(f"⚠️ '{group_hint}' לא קיים ב{full_meal} — דלגתי")
                    continue
                all_results.append(err)
                continue

            add_result = add_food_to_meal(user_id, meal_id, best_food, food_row, extra_grams)
            if add_result.startswith("✅"):
                food_name = best_food.get("food_name", "")
                if food_row:
                    replaced = food_row.get("food_name", "")
                    # כמות המזון המוחלף (quantity = גרמים/יחידות אמיתיים)
                    replaced_q = food_row.get("quantity") or food_row.get("quantity_to_calculate") or ""
                    replaced_measure = food_row.get("measure", "grams")
                    try:
                        replaced_q_f = float(replaced_q)
                        replaced_q_str = str(int(replaced_q_f)) if replaced_q_f == int(replaced_q_f) else f"{replaced_q_f:.2f}"
                        if replaced_q_f <= 0: replaced_q_str = ""
                    except: replaced_q_str = str(replaced_q) if replaced_q else ""
                    if replaced_measure == "units" and replaced_q_str:
                        replaced_disp = f" ({replaced_q_str} יחידות)"
                    elif replaced_q_str:
                        replaced_disp = f" ({replaced_q_str} גרם)"
                    else:
                        replaced_disp = ""
                    # כמות המזון החדש — חישוב לפי יחס קלוריות
                    if extra_grams:
                        new_disp = f" ({extra_grams} גרם)"
                    else:
                        try:
                            cal_target = float(food_row.get("calories") or 0)
                            cal_per_100 = float(best_food.get("calories") or 0)
                            if cal_target > 0 and cal_per_100 > 0:
                                new_q = round(cal_target * 100 / cal_per_100, 2)
                                new_disp = f" ({new_q} גרם)"
                            else:
                                new_disp = ""
                        except: new_disp = ""
                    all_results.append(f"✅ נוסף: *{food_name}*{new_disp} ב{full_meal} של {full_name}\nכתחליף ל: {replaced}{replaced_disp}")
                else:
                    disp_grams = extra_grams or best_food.get("grams") or best_food.get("gram_value") or ""
                    grams_str = f" ({disp_grams} גרם)" if disp_grams else ""
                    all_results.append(f"✅ נוסף: *{food_name}*{grams_str} ב{full_meal} של {full_name}")
            else:
                all_results.append(add_result)

    return "\n\n".join(all_results) if all_results else "❌ לא בוצעה פעולה"


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
    menu_name      = _pop_arg("--menu")

    if menu_name:
        uid, full_name, _ = find_user(menu_name)
        if not uid:
            print(f"❌ לא מצאתי '{menu_name}'")
            sys.exit(1)
        print(format_menu(uid, full_name))
        sys.exit(0)

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
