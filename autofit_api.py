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
    if SESSION_FILE.exists():
        return json.loads(SESSION_FILE.read_text())["token"]
    raise RuntimeError("No session — run login first")

def save_token(token: str, coach_id: str):
    SESSION_FILE.write_text(json.dumps({"token": token, "coach_id": coach_id}))

def load_coach_id() -> str:
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

def find_user(query: str):
    """
    מחזיר (user_id, full_name) לפי שם מלא או מספר טלפון.
    user_id הוא מספר, לא base64.
    """
    global _uid_cache
    if query in _uid_cache:
        return _uid_cache[query]

    clean = query.replace(" ", "").replace("-", "")
    is_phone = clean.isdigit() or (clean.startswith("05") and len(clean) >= 9)

    if is_phone:
        phones = _load_phones()
        name = phones.get(clean) or phones.get("972" + clean[1:] if clean.startswith("0") else clean)
        if not name:
            return None, None
        query = name

    # ה-API מתעלם מ-search — טוענים הכל ומסננים client-side
    terms = query.split()
    page, per_page = 1, 100
    while True:
        data = _post("/coach/get-coach-users", {"page": page, "limit": per_page})
        users = data.get("data", [])
        for u in users:
            full_name = u.get("name") or f"{u.get('first_name','')} {u.get('last_name','')}".strip()
            if all(t in full_name for t in terms):
                uid = str(u["id"])
                _uid_cache[query] = (uid, full_name)
                return uid, full_name
        total = data.get("pagination", {}).get("total", 0)
        if page * per_page >= total:
            break
        page += 1

    return None, None


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
        available = [m.get("meal_name","").strip() for m in meals]
        return None, None, f"❌ לא נמצאה ארוחה '{meal_name}'. ארוחות: {', '.join(available)}"

    foods = target_meal.get("mealFoods") or target_meal.get("new_meal_food") or []
    if not foods:
        return target_meal["id"], None, f"❌ ארוחת {meal_name} ריקה"

    if food_hint:
        hint_words = food_hint.split()
        match = next((f for f in foods if all(w in f.get("food_name","") for w in hint_words)), None)
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

def find_best_food(query: str, coach_id: str = ""):
    """
    מחזיר (best_match, alternatives).
    best_match: התאמה מדויקת/הכי קרובה.
    alternatives: עד 5 תוצאות אם אין התאמה מדויקת.
    """
    foods = search_food(query, coach_id)
    if not foods:
        first_word = query.split()[0]
        foods = search_food(first_word, coach_id)

    if not foods:
        return None, []

    q_lower = query.lower()
    exact   = next((f for f in foods if f.get("food_name","").lower() == q_lower), None)
    starts  = next((f for f in foods if f.get("food_name","").lower().startswith(q_lower)), None)
    contains = next((f for f in foods if q_lower in f.get("food_name","").lower()), None)
    best = exact or starts or contains

    return best, foods[:5]


# ─── Add food to meal ─────────────────────────────────────────────────────────

def add_food_to_meal(user_id: str, meal_id: int, food: dict, food_row_id: int) -> str:
    """
    מוסיף food כתחליף לשורה food_row_id בארוחת meal_id של user_id.
    food: dict שהגיע מ-search_food (מכיל id, food_name, calories, protein, carbs, fat, grams...).
    """
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

def parse_message(text: str) -> dict:
    result = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().strip("*").strip()
        val = val.strip()
        if key in ("שם", "name", "מספר", "טלפון", "phone"):
            result["name"] = val
        elif key in ("ארוחה", "meal"):
            result["meal"] = val
        elif key in ("שנה", "change", "פעולה", "בקשה"):
            result["change"] = val
    return result


def execute_request(request_text: str) -> str:
    parsed = parse_message(request_text)
    if "name" not in parsed or "change" not in parsed:
        return (
            "פורמט שגוי. שלח:\n"
            "שם: <שם מתאמן> (או מספר: 05X)\n"
            "ארוחה: ערב / בוקר / צהריים\n"
            "שנה: הוסף (מזון) במקום (מזון קיים)"
        )

    name   = parsed["name"]
    meal   = parsed.get("meal", "ערב")
    change = parsed["change"]

    # שם ארוחה מלא
    meal_map = {"ערב": "ארוחת ערב", "בוקר": "ארוחת בוקר",
                "צהריים": "ארוחת צהריים", "ביניים": "ארוחת ביניים"}
    full_meal = meal_map.get(meal, meal if "ארוחת" in meal else f"ארוחת {meal}")

    # מצא מתאמן
    user_id, full_name = find_user(name)
    if not user_id:
        return f"❌ לא נמצא מתאמן בשם '{name}'"

    # parse הוסף (X) במקום (Y)
    add_match = re.search(
        r'(?:להוסיף|הוסיפ[יי]?|תוסיפ[יי]?|הוסף)\s+(.+?)(?:\s+במקום\s+(.+))?$', change
    ) or re.search(r'(?:הוסף|תוסיף|להוסיף)\s+(.+)', change)

    if not add_match:
        return "לא הבנתי. שלח: הוסף (מזון) במקום (מזון קיים)"

    new_food_raw = add_match.group(1).strip()
    group_hint_raw = (add_match.group(2) or "").strip() if add_match.lastindex and add_match.lastindex >= 2 else ""

    # חלץ מסוגריים
    paren = re.search(r'\(([^)]+)\)', new_food_raw)
    new_food_query = paren.group(1).strip() if paren else new_food_raw
    paren2 = re.search(r'\(([^)]+)\)', group_hint_raw)
    group_hint = paren2.group(1).strip() if paren2 else group_hint_raw

    # קבל ארוחות
    meals = get_user_meals(user_id)
    meal_id, food_row, err = find_meal_and_food(meals, full_meal, group_hint)
    if err:
        return err

    # חפש מזון
    coach_id = load_coach_id()
    best_food, alternatives = find_best_food(new_food_query, coach_id)

    if not best_food:
        return f"❓ לא נמצא '{new_food_query}' במאגר. נסה שם ספציפי יותר."

    if best_food.get("food_name","").lower() != new_food_query.lower() and not any(
        new_food_query.lower() in f.get("food_name","").lower() for f in [best_food]
    ):
        if not alternatives:
            return f"❓ לא נמצא '{new_food_query}'. אין תוצאות."
        options = "\n".join(f"{i+1}. {f['food_name']}" for i, f in enumerate(alternatives[:5]))
        return f"❓ לא נמצא '{new_food_query}' — אפשרויות דומות:\n{options}\n\nשלח בדיוק את שם המאכל."

    row_id = food_row["id"] if food_row else 0
    return add_food_to_meal(user_id, meal_id, best_food, row_id)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 autofit_api.py <request>")
        sys.exit(1)
    request = " ".join(sys.argv[1:])
    try:
        print(execute_request(request))
    except Exception as e:
        print(f"שגיאה: {e}", file=sys.stderr)
        sys.exit(1)
