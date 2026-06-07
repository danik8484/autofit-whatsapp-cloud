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
from difflib import SequenceMatcher
import os
import time
from pathlib import Path

BACKEND    = "https://chat.auto-fit.co.il"

# ── AI fallback parser (Claude Haiku) ─────────────────────────────────────────
def _parse_with_ai(text: str, v3_mode: bool = False) -> dict:
    """פרסור עם Claude Haiku כאשר הregex נכשל (confidence נמוך)"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {}
    try:
        import anthropic as _anthropic
        client = _anthropic.Anthropic(api_key=api_key)

        if v3_mode:
            system_prompt = """אתה מחלץ פרטי תזונה מהודעות של מאמן כושר ישראלי בעברית.
המאמן שולח הוראות כמו: "מוסיף לך 50 גרם של חזה עוף בארוחת ערב" / "מחליף לך את הלחם בשיבולת שועל".
החזר JSON בלבד עם השדות הבאים:
- food: שם המזון להוסיף (string, חובה)
- grams: כמות בגרמים (מספר שלם, או null אם לא צוין)
- meal: ארוחה — אחד מ: בוקר / צהריים / ערב / ביניים (או null)
- hint: המזון שמוחלף/מוחסר — אם יש "כאופציה ל / כתחליף ל / במקום" (string או null)
- action: הוסף / הפחת / החלף (ברירת מחדל: הוסף)
אל תמציא ערכים. אם לא בטוח — null."""
        else:
            system_prompt = """אתה מחלץ מידע מהודעות תזונה בעברית של מאמן כושר.
החזר JSON בלבד עם השדות:
- name: שם מלא (שם פרטי + שם משפחה)
- food: שם המזון
- meal: בוקר/צהריים/ערב/ביניים
- grams: מספר בלבד (או null)
- hint: מזון חלופי אם יש (או null)
אל תמציא ערכים. אם לא בטוח — null."""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=system_prompt,
            messages=[{"role": "user", "content": text}]
        )
        import json as _json
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"```[a-z]*\n?", "", raw).strip()
        data = _json.loads(raw)
        result = {}
        if not v3_mode and data.get("name"):
            result["name"] = data["name"]
        if data.get("food"):
            _action = (data.get("action") or "הוסף").strip()
            # נרמל פעולות לפועל הסטנדרטי
            if any(w in _action for w in ("הפחת", "הורד", "מוריד", "הפחית")):
                result["reduce"] = True
                _action = "הוסף"
            elif any(w in _action for w in ("החלף", "מחליף")):
                _action = "הוסף"
            result["change"] = f"הוסף ({data['food']})"
            if data.get("hint"):
                result["change"] = f"הוסף ({data['food']}) במקום ({data['hint']})"
            if data.get("grams"):
                try:
                    result["extra_grams"] = str(int(data["grams"]))
                except (ValueError, TypeError):
                    pass
        if data.get("meal"):
            # שמור meal בפורמט קצר (ערב, לא ארוחת ערב) — parse_message מצפה לזה
            _meal_short = {"ארוחת בוקר": "בוקר", "ארוחת צהריים": "צהריים",
                           "ארוחת ערב": "ערב", "ארוחת ביניים": "ביניים"}
            _m = data["meal"]
            result["meal"] = _meal_short.get(_m, _m.replace("ארוחת ", ""))
        result["confidence"] = 90
        print(f"[AI parse] {data} → {result}", flush=True)
        return result
    except Exception as e:
        print(f"[AI parse error] {e}", flush=True)
        return {}

FOOD_API   = "https://food.we-site.co.il/api"
FOOD_TOKEN = "eb8b0f58c895019fcbc3bb17480ced3a2d1e12a346d6ed0f0d0267a24587a203"
SESSION_FILE   = Path(__file__).parent / "session.json"
PHONES_FILE    = Path(__file__).parent / "phones.json"
USER_CACHE_FILE = Path(__file__).parent / "user_cache.json"
USER_CACHE_TTL  = 1800  # 30 דקות

_uid_cache: dict = {}

_PHONE_RE = re.compile(r'^(?:972|0)(?:5[0-9]|[23489])\d{7,8}$')


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

def _post(path: str, body: dict, _retry: int = 0) -> dict:
    r = requests.post(f"{BACKEND}{path}", json=body, headers=_headers(), timeout=15)
    if r.status_code == 429 and _retry < 3:
        time.sleep(2 ** _retry)
        return _post(path, body, _retry + 1)
    r.raise_for_status()
    return r.json()

def _get(path: str, params: dict = None, _retry: int = 0) -> dict:
    r = requests.get(f"{BACKEND}{path}", params=params, headers=_headers(), timeout=15)
    if r.status_code == 429 and _retry < 3:
        time.sleep(2 ** _retry)
        return _get(path, params, _retry + 1)
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
        if name:
            query = name
        else:
            # phone fallback: חיפוש ב-auto-fit לפי מספר טלפון
            _phone_norm = lambda p: re.sub(r'[\s\-\+]', '', str(p or ''))
            _to_il = lambda p: ('0' + p[3:]) if p.startswith('972') else p
            clean_il = _to_il(clean)
            _phone_users = []
            try:
                if USER_CACHE_FILE.exists():
                    _cd = json.loads(USER_CACHE_FILE.read_text(encoding="utf-8"))
                    if time.time() - _cd.get("ts", 0) < USER_CACHE_TTL:
                        _phone_users = _cd["users"]
            except:
                pass
            if not _phone_users:
                _page = 1
                while True:
                    _data = _post("/coach/get-coach-users", {"page": _page, "limit": 100})
                    _phone_users.extend(_data.get("data", []))
                    _total = _data.get("pagination", {}).get("total", 0)
                    if _page * 100 >= _total:
                        break
                    _page += 1
                try:
                    USER_CACHE_FILE.write_text(
                        json.dumps({"ts": time.time(), "users": _phone_users}, ensure_ascii=False),
                        encoding="utf-8"
                    )
                except:
                    pass
            for u in _phone_users:
                u_phone = _to_il(_phone_norm(u.get("phone", "")))
                _clean_9 = clean_il[1:] if (clean_il.startswith('0') and len(clean_il) == 10) else clean_il
                if u_phone and (u_phone == clean_il or u_phone == clean or u_phone == _clean_9):
                    uid = str(u["id"])
                    full_name = (u.get("name") or f"{u.get('first_name','')} {u.get('last_name','')}").strip()
                    _uid_cache[query] = (uid, full_name, False)
                    return uid, full_name, False
            return None, None, False

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

    def _pass1_search(tlist: list[str]):
        """pass 1 על רשימת מילים נתונה — מחזיר (exact_list, partial_or_None)."""
        tn = [_ng(t) for t in tlist]
        exacts, partial = [], None
        for u in all_users:
            fn = _full(u)
            nwn = [_ng(w) for w in fn.split()]
            if all(t in nwn for t in tn):
                if len(tlist) == len(fn.split()):
                    exacts.append((str(u["id"]), fn))
                elif partial is None:
                    partial = (str(u["id"]), fn)
        return exacts, partial

    def _vav_variants(tlist: list[str]) -> list[list[str]]:
        """וריאציות ו׳ חיבור לכל מילה שאינה ראשונה: מוסיף/מוריד ו׳ בתחילת המילה."""
        variants = []
        for i in range(1, len(tlist)):
            w = tlist[i]
            if w.startswith('ו') and len(w) > 1:
                variants.append(tlist[:i] + [w[1:]] + tlist[i+1:])   # הסר ו (וליצקו→ליצקו)
            else:
                variants.append(tlist[:i] + ['ו' + w] + tlist[i+1:]) # הוסף ו (ליצקו→וליצקו)
        return variants

    # pass 1: כל מילות השאילתה נמצאות בשם (התאמה מדויקת)
    exact_matches, partial_match = _pass1_search(terms)

    if len(exact_matches) == 1:
        uid, full_name = exact_matches[0]
        _uid_cache[query] = (uid, full_name, False)
        return uid, full_name, False
    if len(exact_matches) > 1:
        encoded = ";".join(f"{uid}|{name}" for uid, name in exact_matches)
        return "MULTIPLE", encoded, False

    # pass 1.5: ו׳ חיבור — נסה גם עם וגם בלי ו׳ לכל מילה שאינה ראשונה
    # "רון ליצקו" → "רון וליצקו" | "רון וליצקו" → "רון ליצקו"
    # התאמה כאן = ודאי (is_fuzzy=False), ללא שאלת אישור
    if len(terms) > 1:
        for vt in _vav_variants(terms):
            v_exact, v_partial = _pass1_search(vt)
            if len(v_exact) == 1:
                uid, full_name = v_exact[0]
                _uid_cache[query] = (uid, full_name, False)
                return uid, full_name, False
            if len(v_exact) > 1:
                encoded = ";".join(f"{uid}|{name}" for uid, name in v_exact)
                return "MULTIPLE", encoded, False
            if v_partial and partial_match is None:
                partial_match = v_partial

    if partial_match:
        uid, full_name = partial_match
        _uid_cache[query] = (uid, full_name, True)
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

    # pre-pass: "ד ני" → נסה גם "דני" (רווח בתוך שם = טעות הקלדה)
    _collapsed = re.sub(r'\b([א-ת])\s+(?=[א-ת])', r'\1', query)  # "ד ני" → "דני"
    _extra_variants: list[list[str]] = []
    if _collapsed != query:
        _extra_variants = [_collapsed.split()]

    # pass 3: חיפוש עמום (Levenshtein) — מוצא גם עם טעויות כתיב
    # מנסה גם וריאציות ו׳ חיבור כדי להגביר סיכוי מציאה
    best_uid, best_name, best_score, best_nwords = None, None, 999, 999
    all_variants = [terms] + (_vav_variants(terms) if len(terms) > 1 else []) + _extra_variants
    for vt in all_variants:
        vq = " ".join(vt)
        for u in all_users:
            full_name = u.get("name") or f"{u.get('first_name','')} {u.get('last_name','')}".strip()
            if _fuzzy_name_match(vq, full_name):
                score = sum(min(_levenshtein(qw, nw) for nw in full_name.split()) for qw in vt)
                nwords = len(full_name.split())
                # מעדיף שם קצר יותר כשהציון שווה (דני < אורי דוד דני הקטן)
                if score < best_score or (score == best_score and nwords < best_nwords):
                    best_score, best_uid, best_name, best_nwords = score, str(u["id"]), full_name, nwords

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


def format_menu(user_id: str, full_name: str, meal_filter: str = "") -> str:
    """מחזיר תפריט של מתאמן. meal_filter מסנן לארוחה ספציפית (אופציונלי)."""
    meals = get_user_meals(user_id)
    if not meals:
        return f"❌ לא נמצאו ארוחות עבור {full_name}"

    MEAL_EMOJI = {
        "ארוחת בוקר": "🍳", "ארוחת צהריים": "🥗",
        "ארוחת ערב": "🌙", "ארוחת ביניים": "🍎",
        "ארוחת לילה": "🌙",
    }
    _MEAL_FILTER_MAP = {
        "בוקר": "ארוחת בוקר", "צהריים": "ארוחת צהריים", "צהרים": "ארוחת צהריים",
        "ערב": "ארוחת ערב", "לילה": "ארוחת לילה", "ביניים": "ארוחת ביניים",
    }
    _filter_key = _MEAL_FILTER_MAP.get(meal_filter, "")

    header = f"📋 תפריט של *{full_name}*" + (f" — {_filter_key or meal_filter}" if meal_filter else "") + ":\n"
    lines = [header]
    total_calories = 0

    for meal in meals:
        if _filter_key and meal.get("meal_name", "").strip() != _filter_key:
            continue
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

        def _word_fuzzy(query_words, food_name, max_edit=1):
            """כל מילת query מוצאת (substring או edit≤max_edit) בשם המזון"""
            fw = _norm(food_name).split()
            for qw in query_words:
                found = any(
                    qw in fword or fword in qw or
                    SequenceMatcher(None, qw, fword).ratio() >= 1 - (2*max_edit / max(len(qw)+len(fword), 1))
                    for fword in fw if len(fword) >= 2
                )
                if not found:
                    return False
            return True

        # 1. חיפוש מדויק: כל מילות ה-hint מופיעות בשם המזון
        match = next((f for f in foods if all(
            w in _norm(f.get("food_name", ""))
            for w in hint_words
        )), None)

        # 2. חיפוש רחב — מילה ראשונה בלבד (substring)
        if not match:
            match = next((f for f in foods if hint_words[0] in _norm(f.get("food_name", ""))), None)

        # 3. fuzzy — סובלנות לטעויות כתיב (edit distance ≤ 1 לכל מילה)
        if not match:
            match = next((f for f in foods if _word_fuzzy(hint_words, f.get("food_name", ""))), None)

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
    """מסיר ה' הידיעה, גרש, תווי תבנית (<>), ומנרמל ביטויי בישול נפוצים."""
    q = re.sub(r'[<>]', '', q)
    q = re.sub(r"['׳״]", '', q)  # גרש (' ׳ ״) — "קוטג'" → "קוטג"
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

# אליאסים: כשמבקשים X, מחפשים Y אלא אם צוין אחרת
_FOOD_ALIASES: dict[str, str] = {
    "חזה עוף":       "חזה עוף לאחר בישול",
    "קוואקר":        "שיבולת שועל",
    "שיבולת":        "שיבולת שועל",
    "גרנולה":        "שיבולת שועל",
    "חזה":           "חזה עוף לאחר בישול",
    "הודו טחון":     "הודו",
    "בשר טחון":      "בשר בקר",
    "בשר בקר טחון":  "בשר בקר",
    "דג":            "סלמון",
    "סלמון":         "סלמון אטלנטי",
}

def find_best_food(query: str, coach_id: str = ""):
    """
    מחזיר (best_match, alternatives).
    best_match: התאמה מדויקת/הכי קרובה.
    alternatives: עד 10 תוצאות אם אין התאמה מדויקת.
    """
    # אליאס: "חזה עוף" → מנסה "חזה עוף לאחר בישול" קודם
    alias = _FOOD_ALIASES.get(query.strip())
    if alias:
        best_a, alts_a = find_best_food(alias, coach_id)
        if best_a:
            return best_a, alts_a
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
        best = None

    # אם לא נמצא — נסה עם השם המקורי (לפני נרמול "לאחר בישול"→"מבושל")
    if best is None and norm_query != query:
        foods2 = search_food(query, coach_id)
        if foods2:
            q2 = query.lower()
            exact2    = next((f for f in foods2 if f.get("food_name","").lower() == q2), None)
            starts2   = [f for f in foods2 if f.get("food_name","").lower().startswith(q2)]
            contains2 = [f for f in foods2 if q2 in f.get("food_name","").lower()]
            if exact2:
                return exact2, foods2[:30]
            elif len(starts2) == 1:
                return starts2[0], foods2[:30]
            elif len(contains2) == 1:
                return contains2[0], foods2[:30]
            elif contains2:
                foods = contains2  # עדיף תוצאות מקוריות על פני תוצאות מנורמלות רעות

    return best, foods[:30]


# ─── Update / Add food to meal ───────────────────────────────────────────────

def update_food_quantity(user_id: str, meal_food_id: int, new_quantity: float) -> dict:
    """מעדכן כמות (גרמים) של מזון קיים בארוחה — v2-updateMealFoodQuantity."""
    body = {
        "user_id": str(user_id),
        "food_id": str(meal_food_id),   # = id של רשומת meal_food, לא food_id מהDB
        "quantity": str(int(round(new_quantity))),
    }
    return _post("/coach/v2-updateMealFoodQuantity", body)

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
        actual_grams = str(grams_override or food_row.get("quantity") or food_row.get("quantity_to_calculate") or food_row.get("gram_value") or food.get("grams") or food.get("gram_value") or "")
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
            return f"✅ נוסף כתחליף ל-{food_row.get('food_name','')}: {food['food_name']}|GRAMS={actual_grams}"
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

_VERB_PAT = r"(?:הוסיפ[יי]?|הוסיף|תוסיפ[יי]?|הוסף|החלף[יי]?|החליף|תחליף|תחליפ[יי]?|להוסיף|להחליף|שימ[יי]?|תשימ[יי]?|הכנס[יי]?|הכניס|עדכן|עדכני|תשנ[יה])"

def _extract_foods(text: str):
    """מחלץ (מזון_חדש, מזון_קיים) מטקסט. מחזיר (new_food, group_hint)."""
    # נרמול synonyms של "כאופציה/באופציה" לפני כל חיפוש
    text = re.sub(r'באופצי(?:ה|ות)?\s+של', 'במקום', text)
    text = re.sub(r'כאופצי(?:ה|ות)?\s+(?:ל(?=[א-ת]{3,})|של)', 'במקום ', text)
    text = re.sub(r'כאופציות\b', '', text)  # כאופציות ללא ל(קצר)/של = מחיקה
    text = re.sub(r'אופציה\s+של\s+', '', text)  # "אופציה של X במקום Y" → "X במקום Y"
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

    # "לHINT [verb] NEW_FOOD" — hint לפני הפועל (למשל "לאורז תוסיפי חזה עוף")
    m_pre = re.match(
        r'^ל([\u05D0-\u05EA]+(?:\s+[\u05D0-\u05EA]+)?)\s+' + _VERB_PAT + r'\s+(.+)',
        text,
    )
    if m_pre:
        return m_pre.group(2).strip(), m_pre.group(1).strip()

    # "הוסף X" בלבד
    m2 = re.search(
        _VERB_PAT + r"\s+([^\n]{2,50})",
        text,
    )
    if m2:
        food = m2.group(1).strip()
        # סדר הפוך: "הוסף במקום [hint] [new_food]" → restructure
        if food.startswith("במקום "):
            rest = food[6:].strip()
            _COOK = {'לאחר', 'אחרי', 'בישול', 'לפני', 'מבושל', 'גולמי', 'חי', 'קלוי', 'מאודה', 'טחון', 'מטוגן', 'אפוי'}
            words = rest.split()
            i = 1
            while i < len(words) and words[i] in _COOK:
                i += 1
            if 0 < i < len(words):
                return ' '.join(words[i:]).strip(), ' '.join(words[:i]).strip()
            return "", rest
        return food, ""

    # "X במקום Y" ללא פועל מפורש
    m3 = re.search(r"([^\n]{2,30})\s+במקום(?:\s+של)?\s+([^\n]{2,40})", text)
    if m3:
        return m3.group(1).strip(), m3.group(2).strip()

    return "", ""


_COMMON_SURNAMES = frozenset({
    'כהן', 'לוי', 'מזרחי', 'פרץ', 'שפירא', 'אוחיון', 'אמסלם', 'גולן', 'רוזן',
    'שרון', 'ביטון', 'עמר', 'זוהר', 'שלום', 'מלול', 'אלבז', 'בן', 'אבו',
    'בר', 'דוד', 'יצחק', 'אברהם', 'יוסף', 'חיים', 'שמעון', 'עוז', 'אלון',
    # אשכנזיים + ספרדיים + מתאמנים ידועים
    'פרנקל', 'מרטינז', 'שלו', 'שליו', 'וליצקו', 'גרינברג', 'גולדברג',
    'רוזנברג', 'זינגר', 'שלזינגר', 'ויינשטיין', 'ליבוביץ', 'כהנמן',
    'חן', 'טל', 'לי', 'שי', 'פז', 'לב', 'עד', 'גד',
})
_SURNAME_SUFFIXES = ('ביץ', 'ייץ', 'ניץ', 'מן', 'שטיין', 'ניק', 'ובי', 'ייב', 'נקל', 'רגר', 'ברג')
# Auto-populate surnames from user cache (runs once at import)
_FOOD_NOT_SURNAME = frozenset({
    'סלמון', 'טונה', 'אורז', 'פסטה', 'גבינה', 'חזה', 'עוף', 'ביצה', 'ביצים',
    'בטטה', 'שיבולת', 'שועל', 'קוואקר', 'לחם', 'בשר', 'הודו', 'דג', 'יוגורט',
    'קוטג', 'שקדים', 'אגוזים', 'חומוס', 'עדשים', 'אבוקדו', 'בננה', 'תפוח',
    # תארי מזון שלא יזוהו כשמות פרטיים
    'לבנה', 'בנה', 'לבן', 'צהובה', 'צהוב', 'מלאה', 'מבושל', 'מבושלת', 'טחון', 'טחונה',
    'קפואה', 'קפוא', 'רזה', 'אדומה', 'אדום', 'ירוקה', 'ירוק', 'כבושה', 'שמנת', 'חצי', 'מיובש',
    # מזונות שמתחילים ב-ל (ל הוא חלק מהשם, לא מילת יחס)
    'לביבה', 'ביבה', 'לחמנייה', 'לחמניה', 'לפת', 'פת',
})
try:
    _db_cache_data = json.loads(USER_CACHE_FILE.read_text(encoding="utf-8"))
    _COMMON_SURNAMES = _COMMON_SURNAMES | frozenset(
        w for u in _db_cache_data.get("users", [])
        for w in (u.get("name") or "").split()[1:]
        if w and len(w) >= 3 and re.match(r'^[א-ת]+$', w) and w not in _FOOD_NOT_SURNAME
    )
    del _db_cache_data
except Exception:
    pass

def _looks_like_surname(word: str) -> bool:
    """בודק אם מילה עשויה להיות שם משפחה (לעומת שם מזון)."""
    if word in _FOOD_NOT_SURNAME:
        return False
    if word in _COMMON_SURNAMES:
        return True
    for sfx in _SURNAME_SUFFIXES:
        # require at least 2 chars before suffix to avoid false positives like "שמן" (oil, ends in מן)
        if word.endswith(sfx) and len(word) >= len(sfx) + 2:
            return True
    return False

_H_PROTECT_FOODS = frozenset({"הודו", "הכל", "הכול"})

def _strip_definite_article(s: str) -> str:
    """מסיר ה' הידיעה מכל מילה (למעט מילים מוגנות כגון 'הודו')."""
    return ' '.join(
        w if w in _H_PROTECT_FOODS else re.sub(r'^ה(?=[א-ת])', '', w)
        for w in s.split()
    ) if s else s


def _detect_multi_food(text: str):
    """
    "50 גרם X ו-30 גרם Y" → [("X","50"),("Y","30")]
    Only triggers when ו- (with dash) is followed by a digit, or comma between qty+food pairs.
    Returns None for single-food text.
    """
    _UNIT_PAT = r'(?:גרם|יח\'?|כף(?:ות)?|כפיות?|כוס|מנות?)'

    def _parse_part(part):
        part = part.strip()
        m = re.match(r'^(\d+)\s*' + _UNIT_PAT + r'\s+(.+)$', part)
        if m:
            return m.group(2).strip(), m.group(1)
        m2 = re.search(r'^(.+?)\s+(\d+)\s*' + _UNIT_PAT + r'\s*$', part)
        if m2:
            return m2.group(1).strip(), m2.group(2)
        if re.search(r'[א-ת]', part):
            return part, None
        return None, None

    # ו- with dash — require second part starts with digit
    raw = re.split(r'\s+ו-\s*', text.strip())
    if len(raw) >= 2 and re.match(r'^\d', raw[1].strip()):
        parts = [_parse_part(p) for p in raw]
        if all(f for f, _ in parts):
            return parts

    # Comma — require ALL parts start with digit (qty, food qty food)
    raw_c = [p.strip() for p in re.split(r'\s*,\s*', text.strip()) if p.strip()]
    if len(raw_c) >= 2 and all(re.match(r'^\d', p) for p in raw_c):
        parts = [_parse_part(p) for p in raw_c]
        if all(f for f, _ in parts):
            return parts

    return None


def _convert_heb_numbers(text: str) -> str:
    """ממיר מספרים עבריים לספרות לפני פרסינג: 'מאה גרם' → '100 גרם', 'חמישים' → '50'."""
    _TENS = {
        'תשעים': 90, 'שמונים': 80, 'שבעים': 70, 'שישים': 60,
        'חמישים': 50, 'ארבעים': 40, 'שלושים': 30, 'עשרים': 20,
        'עשרה': 10, 'עשר': 10,
    }
    tens_alts = '|'.join(_TENS.keys())
    def _compound(m: re.Match) -> str:
        return str(100 + _TENS.get(m.group(1), 0))
    # "מאה וחמישים" / "מאה ו-60" compound — לפני כלל "מאה" פשוט
    text = re.sub(r'מאה\s+ו-?(' + tens_alts + r')(?![א-ת])', _compound, text)
    text = re.sub(r'(?<![א-ת])מאתיים(?![א-ת])', '200', text)
    text = re.sub(r'(?<![א-ת])מאה(?![א-ת])', '100', text)
    for word, val in _TENS.items():
        text = re.sub(r'(?<![א-ת])' + word + r'(?![א-ת])', str(val), text)
    return text


def parse_message(text: str, skip_name: bool = False) -> dict:
    """
    מנסה לפרסר הודעה — גם בתבנית מובנית וגם בשפה חופשית.
    מחזיר dict עם: name, meal, change, confidence (0-100).
    """
    result = {}
    conf = 100

    # ── pre-process: פקודות v3 ("מוסיף לך/לו") → פורמט שהפרסר מבין ───────────
    # ל[כךו]: לך (ך = כ סופית 0x5da) + לו — שתי הצורות
    _text_before_v3 = text
    # נרמול "גר" → "גרם" (קיצור נפוץ) — לפני כל parsing
    text = re.sub(r'(\d+)\s*גר(?![\u05D0-\u05EA])', r'\1 גרם', text)
    text = re.sub(r'^אני\s+', '', text)  # 'אני מוסיף לך' → 'מוסיף לך'
    text = re.sub(r'מוסיף\s+ל[כךו]', 'הוסף', text)
    # גוף שני/שלישי: "תוסיף לך", "הוסיף לו"
    text = re.sub(r'(?:תוסיף|הוסיף)\s+ל[כךוי](?![\u05D0-\u05EA])', 'הוסף', text)
    # מחליף לך X ב-Y → הוסף Y במקום X (Y = subFood לבחירה, X = מה שמחליפים)
    # protect "לאחר/לפני בישול" then lazy-match to find FIRST ב separator
    _t = text.replace('לאחר בישול', 'לאחר״בישול').replace('לפני בישול', 'לפני״בישול')
    _t = re.sub(
        r'מחליף\s+ל[כךו]\s+(.+?)\s+ב-?([^\n]+)',
        lambda m: f'הוסף {m.group(2).strip().lstrip("-")} במקום {m.group(1).strip()}',
        _t
    )
    text = _t.replace('״', ' ')
    text = re.sub(r'מחליף\s+ל[כךו]', 'הוסף', text)  # fallback: מחליף ללא "ב"
    # גוף שני/שלישי "תחליף/החליף לך X ב-Y"
    _t2 = text.replace('לאחר בישול', 'לאחר״בישול').replace('לפני בישול', 'לפני״בישול')
    _t2 = re.sub(
        r'(?:תחליף|החליף)\s+ל[כךוי]\s+(.+?)\s+ב-?([^\n]+)',
        lambda m: f'הוסף {m.group(2).strip().lstrip("-")} במקום {m.group(1).strip()}',
        _t2
    )
    text = _t2.replace('״', ' ')
    text = re.sub(r'(?:תחליף|החליף)\s+ל[כךוי]', 'הוסף', text)
    text = re.sub(r'(?:מוריד\s+ל[כךוי](?![\u05D0-\u05EA])|מפחית(?:\s+ל[כךוי](?![\u05D0-\u05EA]))?)', 'הפחת', text)
    # גוף שני/שלישי: "תוריד לך", "הוריד לו"
    text = re.sub(r'(?:תורידי?|הוריד)\s+ל[כךוי](?![\u05D0-\u05EA])', 'הפחת', text)
    text = re.sub(r'מעלה\s+ל[כךו]', 'העלה', text)
    # גוף שני/שלישי: "תעלה לך", "העלה לו"
    text = re.sub(r'(?:תעל[הי]|העל[הי])\s+ל[כךוי](?![\u05D0-\u05EA])', 'העלה', text)
    _v3_triggered = (text != _text_before_v3)
    # V3 only: strip ל-preposition from "N [גרם] לFOOD" (not applied to old-style לNAME format)
    if _v3_triggered:
        text = re.sub(r'(\d+\s*(?:גרם\s+)?)ל([\u05D0-\u05EA]{3,})', r'\1\2', text)

    # ── pre-process: נרמול רווחים לפני נקודותיים בשמות שדות ─────────────────
    # "שם :" → "שם:", "ארוחה  :" → "ארוחה:" (רווח לפני :)
    text = re.sub(r'(?m)^(\s*(?:שם|ארוחה|הוספה|שנה|החלף|הוסף|תחליף|פעולה|בקשה|כתחליף ל|כתחליף|במקום))\s+:', r'\1:', text)
    # פסיק: "שם: X, ארוחה: Y" → שורות
    text = re.sub(r'[,،]\s*(?=(?:שם|ארוחה|הוספה|שנה|החלף|הוסף|תחליף|פעולה|בקשה)\s*:)', '\n', text)
    # רווח: "ארוחה: X הוספה: Y" → שורות (כשאין newline) — כולל "ארוחה:"
    text = re.sub(r'(?<=[^\n])\s+(?=(?:הוספה|שנה|ארוחה|החלף|הוסף|תחליף|פעולה|בקשה)\s*:)', '\n', text)
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
            result.setdefault("meal", meal_key)  # so free-text _bare_meal_re fires
            v = val.strip()
            v = re.sub(r'^(?:הוסיפ[יי]?|הוסיף|תוסיפ[יי]?|הוסף|החלף[יי]?|תחליפ[יי]?)\s+', '', v)
            # multi-food: "20 גרם X ו-15 גרם Y" → multiple ops
            _mf_s = _detect_multi_food(v)
            if _mf_s and len(_mf_s) >= 2:
                for _mf_food, _mf_grams in _mf_s:
                    _mf_ch = f"הוסף ({_mf_food.strip()})"
                    _mf_op_d = {"change": _mf_ch, "meal": meal_key}
                    if _mf_grams:
                        _mf_op_d["extra_grams"] = _mf_grams
                    if "ops" not in result:
                        result["ops"] = []
                    result["ops"].append(_mf_op_d)
                result.setdefault("change", result["ops"][0]["change"])
            else:
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
                # strip outer parens: "(80 גרם אורז)" → "80 גרם אורז"
                if v.startswith('(') and v.endswith(')'):
                    v = v[1:-1].strip()
                _op_reduce = bool(re.match(r'^(?:הורד|הפחת|תוריד|תורידי|הפחיתי|הפחית|תפחית|תפחיתי|הורידי)\s+', v))
                v = re.sub(r'^(?:הורד|הפחת|תוריד|תורידי|הפחיתי|הפחית|תפחית|תפחיתי|הורידי)\s+', '', v)
                v = re.sub(r'^(?:הוסיפ[יי]?|הוסיף|תוסיפ[יי]?|הוסף|החלף[יי]?|תחליפ[יי]?)\s+', '', v)
                v = re.sub(r'בנוסף\s+ל-?', 'ל ', v)         # "בנוסף ל X" → "ל X"
                v = re.sub(r'במקום\s+של', 'ל ', v)      # "במקום של X" → "ל X"
                v = re.sub(r'במקום', 'ל ', v)            # "במקום X" → "ל X"
                v = re.sub(r'באופצי(?:ה|ות)?\s+של', 'ל ', v)  # "באופציה/ות של X" → "ל X"
                v = re.sub(r'כאופציה\s+(?:ל|של)', 'ל ', v)  # "כאופציה ל/של X" → "ל X"
                v = re.sub(r'כתחליף\s+ל', 'ל ', v)      # "כתחליף ל X" → "ל X"
                v = re.sub(r'אופציה\s+של\s+', '', v)    # "אופציה של X" → "X"
                # גרמים: "עוד X גרם FOOD" / "X גרם [ל]FOOD" / "FOOD X גרם"
                extra_grams = None
                grams_m = re.search(r'\bעוד\s+(\d+)\s*גרם\b', v)
                if grams_m:
                    extra_grams = grams_m.group(1)
                    v = re.sub(r'\bעוד\s+\d+\s*גרם\s*', '', v).strip()
                else:
                    m_gs = re.match(r'^(\d+)\s*גרם\s+מה?\s*', v)  # "50 גרם מ/מה-FOOD" (reduce)
                    if not m_gs:
                        m_gs = re.match(r'^(\d+)\s*גרם\s+(?:ל(?=[\u05D0-\u05EA]{3,}))?\s*', v)  # "50 גרם [ל]אורז"
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
                    # לאחר/לפני = מילות בישול, לא separators
                    m_l = re.search(r'^(.+?)\s+ל(?!אחר\b|פני\b)([\u05D0-\u05EA].+)$', v)
                    if m_l:
                        nf, ht = m_l.group(1), m_l.group(2)
                    else:
                        nf, ht = v, ""
                _cl = lambda s: re.sub(r'[.\s:,]+$', '', s).strip()
                change = f"הוסף ({_cl(nf)}) במקום ({_cl(ht)})" if _cl(ht) else f"הוסף ({_cl(nf)})"
                op_dict = {"change": change, "meal": op_meal, "extra_grams": extra_grams}
                if _op_reduce:
                    op_dict["reduce"] = True
                return op_dict

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

    # הודעה מובנית (יש שם/ארוחה) אבל חסר הוספה — לא נפול לfree-text
    _has_structured_key = any(
        re.search(r'(?:^|\n)\s*(?:שם|ארוחה|meal)\s*:', text)
        for _ in [1]
    )
    if "name" in result and _has_structured_key:
        result.setdefault("meal", "ערב")
        result["confidence"] = 100
        return result  # חסר change — execute_request יחזיר "חסר מה להוסיף"

    # ── שפה חופשית ───────────────────────────────────────────────────────────
    full = " ".join(text.strip().split("\n"))
    full = _convert_heb_numbers(full)  # "מאה גרם" → "100 גרם", "חמישים" → "50"
    # נרדפי ארוחה — נרמל לפני חילוץ ארוחה (ל/ב prefix נשמר: "לנשנוש"→"לביניים")
    full = re.sub(r'(?:בין\s+ארוחות|הפסקה|ארוחת\s+ביניים)', 'ביניים', full)
    full = re.sub(r'([לב]?)(?:נשנוש|חטיף|נשנושים)(?![א-ת])', r'\1ביניים', full)
    full = re.sub(r'(?:אחר(?:י)?\s+ה?צהריים)', 'ביניים', full)
    full = re.sub(r'לפני\s+ה?שינה', 'לילה', full)
    full = re.sub(r'ארוחת\s+לילה', 'לילה', full)
    # "מזון חדש" = פקודה, לא שם/מזון — נסיר לפני חילוץ
    _force_new = bool(re.search(r'\bמזון\s+חדש\b', full))
    if _force_new:
        full = re.sub(r'\bמזון\s+חדש\b', '', full)
        full = re.sub(r'\s+', ' ', full).strip()

    # "ב-X גרם" / "ב50 גרם" → "X גרם" (כמות יחסית)
    full = re.sub(r'ב-?(\d+)\s*גרם', r'\1 גרם', full)

    # נרמול שמות עצם של הפחתה → פועל
    full = re.sub(r'(?<![א-ת])(?:הפחתה|הורדה)\s+של', 'הפחת', full)
    full = re.sub(r'(?<![א-ת])(?:הפחתה|הורדה)', 'הפחת', full)
    # "פחות X גרם" = הפחתה (למשל "לדני פחות 150 גרם גרנולה")
    full = re.sub(r'(?<![א-ת])פחות\s+(?=\d)', 'הפחת ', full)

    # "הורד/הפחת X גרם" = פקודת הפחתה — כולל קידומת ש/ו (שתורידי, ותורידי)
    _REDUCE_PAT = r'(?<![א-ת])(?:[שוב]?)(?:להוריד|להפחית|הורד|הפחת|תוריד|תורידי|הפחיתי|הפחית|תפחית|תפחיתי|הורידי|הוריד|תחסר[יי]?|לחסר)(?![א-ת])'
    _reduce = bool(re.search(_REDUCE_PAT, full))
    if _reduce:
        full = re.sub(_REDUCE_PAT + r'\s*', '', full)
        # "N [גרם] מ/מה-FOOD" — strip prefix מ/מה לפני שם מזון (גם ללא גרם)
        full = re.sub(r'(\d+\s*(?:גרם\s+)?)מה?([א-ת])', r'\1\2', full)
        full = re.sub(r'\s+', ' ', full).strip()

    # פועלי הגדלה — הסר לפני פרסינג (לא reduce, פשוט הסרת הפועל)
    full = re.sub(r'\b(?:תעל[יי]?|העל[יה]?|הגדל[יי]?|תגדיל[יי]?)\s+', '', full)
    full = re.sub(r'\s+', ' ', full).strip()

    _NOT_NAME_VERBS = frozenset({"הוסיף", "הוסיפי", "תוסיפי", "תוסיף", "הוסף",
                                  "החלף", "תחליפי", "תחליף", "הכנס", "הכניס", "הכנסי", "להכניס",
                                  "עדכן", "שנה", "בצע", "שלח", "עשה", "עשי",
                                  "הוציא", "מחק", "הסר", "החליף", "תחליפ",
                                  "ארוחת", "ארוחה", "נוסיף", "נוסיפי",
                                  "מזון", "חדש", "הורד", "הוריד", "הורידי", "הפחת", "הפחית", "תוריד", "תורידי",
                                  "תעלי", "תעלה", "העלי", "העלה", "הגדלי", "הגדל", "תגדילי", "תגדיל",
                                  "תחסרי", "תחסר", "לחסר",
                                  # מילות בישול — מונעות לקיחת "לאחר בישול" כשם אדם
                                  "אחר", "אחרי", "בישול", "מבושל", "גולמי", "חי",
                                  "קלוי", "מאודה", "טחון", "מטוגן", "אפוי", "מעורבב",
                                  # מילות אופציה — מונעות לקיחת "דני אופציה" כשם
                                  "אופציה", "אופציות",
                                  # פעלי הוספה בסדר "לX תשימי" — מונעות לאבד את X
                                  "שים", "שימי", "תשים", "תשימי",
                                  # "עוד" — כינוי כמות, לא שם
                                  "עוד",
                                  # כינויי גוף — לא שמות אדם
                                  "אני", "אנו", "אנחנו", "הוא", "היא", "הם", "הן",
                                  # פעלי נתינה — "לתת" / "נתן"
                                  "תת", "ניתן",
                                  # מילות הגבלה / ייעוץ
                                  "כדאי", "אולי", "עדיף", "בואי", "בוא"})

    # שמות עבריים שמתחילים באות ל כחלק מהשם (לא ל' יחס)
    _L_NAMES = frozenset({'ליצקו', 'לי', 'לילך', 'לאה', 'ליאת', 'ליאור', 'ליבי', 'לירן', 'לירון', 'לינור'})

    _MEAL_MAP_FT = {"ערב": "ערב", "לילה": "ערב", "בוקר": "בוקר",
                    "צהריים": "צהריים", "צהרים": "צהריים", "ביניים": "ביניים"}

    # חלץ ארוחות ראשון (לפני שם/מזון) — תומך בריבוי ארוחות
    _meal_hits = []
    if "meal" not in result and "meals" not in result:
        _meal_hits = []
        for w, k in _MEAL_MAP_FT.items():
            if re.search(r'[בולמ](?:ארוחת\s+)?ה?' + w + r'\b', full):
                if k not in _meal_hits:
                    _meal_hits.append(k)
            # מילת ארוחה ללא prefix — "לדני בוקר: X" / "ערב X" / "בוקר:"
            elif re.search(r'(?:^|\s)' + w + r'(?:[\s:,]|$)', full):
                if k not in _meal_hits:
                    _meal_hits.append(k)
        # "X וY" כריבוי ארוחות
        for w1, k1 in _MEAL_MAP_FT.items():
            for w2, k2 in _MEAL_MAP_FT.items():
                if w1 != w2 and re.search(rf'\b{w1}\s+[וV]\s*{w2}\b', full):
                    for k in [k1, k2]:
                        if k not in _meal_hits:
                            _meal_hits.append(k)
        # "+ערב", "+בוקר" וכו' — ארוחה נוספת עם +
        for w, k in _MEAL_MAP_FT.items():
            if re.search(rf'\+\s*{w}\b', full):
                if k not in _meal_hits:
                    _meal_hits.append(k)
        # "פועל ערב X" / "ל ערב X" — ארוחה ללא ב' (למשל: "תוסיפי ערב אורז")
        if not _meal_hits:
            for w, k in _MEAL_MAP_FT.items():
                if re.search(rf'(?:^|[\s]){w}\s+[א-ת]', full):
                    if k not in _meal_hits:
                        _meal_hits.append(k)
        _meal_hits = list(dict.fromkeys(_meal_hits))
        if len(_meal_hits) > 1:
            result["meals"] = _meal_hits
        elif _meal_hits:
            result["meal"] = _meal_hits[0]

    # נקה ביטויי ארוחה לפני חילוץ שם/מזון — ב/ל/מ prefix + "עבור/של ארוחת X"
    _meal_re = r'[בלמ](?:ארוחת\s+)?ה?(?:' + '|'.join(_MEAL_MAP_FT.keys()) + r')\b'
    full_no_meal = re.sub(_meal_re, '', full)
    # "עבור ארוחת X" / "של ארוחת X"
    _meal_words = '|'.join(_MEAL_MAP_FT.keys())
    full_no_meal = re.sub(r'\s*(?:עבור|של)\s+ארוחת\s+(?:' + _meal_words + r')\b', '', full_no_meal)
    full_no_meal = re.sub(r'\s*(?:עבור|של)\s+(?:' + _meal_words + r')\b', '', full_no_meal)
    # גם "+ארוחה" (כמו "+ערב", "+צהריים")
    full_no_meal = re.sub(r'\s*\+\s*(?:ארוחת\s+)?(?:' + '|'.join(_MEAL_MAP_FT.keys()) + r')\b', '', full_no_meal)
    # גם "ו+ארוחה" (כמו "וערב")
    full_no_meal = re.sub(r'\s+[וV](?:ארוחת\s+)?(?:' + '|'.join(_MEAL_MAP_FT.keys()) + r')\b', '', full_no_meal)
    full_no_meal = re.sub(r'\b(?:' + '|'.join(_MEAL_MAP_FT.keys()) + r')\s+[וV]\s*(?:' + '|'.join(_MEAL_MAP_FT.keys()) + r')\b', '', full_no_meal)
    # ארוחה ללא ב' בתחילת ביטוי ("ערב אורז" / "בוקר ביצה" / "לדני ערב: X")
    # גם כשה-meal כבר הוגדר מ-extra_ops — כדי שלא ייכנס לשם
    if _meal_hits or result.get("meal"):
        _bare_meal_re = r'(?:^|\s)(?:' + '|'.join(_MEAL_MAP_FT.keys()) + r')(?=[\s:,]|$)'
        full_no_meal = re.sub(_bare_meal_re, ' ', full_no_meal)
    full_no_meal = re.sub(r'\s+', ' ', full_no_meal).strip()
    # נרמול מוקדם לפני חיפוש שמות — מונע "ל+מזון" אחרי מילות תחליף מלהיות שם אדם
    full_no_meal = re.sub(r'(\d+\s*גרם)\s+של\s+', r'\1 ', full_no_meal)  # '50 גרם של חזה עוף' → '50 גרם חזה עוף'
    full_no_meal = re.sub(r'כתחליף\s+ל', 'במקום ', full_no_meal)
    full_no_meal = re.sub(r'כאופצי(?:ה|ות)?\s+(?:ל(?=[א-ת]{3,})|של)\s*', 'במקום ', full_no_meal)
    full_no_meal = re.sub(r'באופצי(?:ה|ות)?\s+של\s*ה?', 'במקום ', full_no_meal)  # V3: "באופציה של X"
    full_no_meal = re.sub(r'אופציה\s+של\s+', '', full_no_meal)  # standalone — לאחר כ/ב כבר טופלו
    full_no_meal = re.sub(r'כאופציות\b', '', full_no_meal)  # כאופציות ללא ל/של = מחיקה
    full_no_meal = re.sub(r'בנוסף\s+ל-?', 'במקום ', full_no_meal)
    full_no_meal = re.sub(r'\s+', ' ', full_no_meal).strip()

    # ── V3: חילוץ שם לפני הלוגיקה הכללית ────────────────────────────────────
    if skip_name:
        # phone-first: שם ידוע ממספר טלפון — לא מחלצים מהטקסט כדי לא לשבור איתות תחליף
        clean_full = full_no_meal
    elif _v3_triggered and "name" not in result:
        # Fix A: VERB NAME N גרם — שם ישיר לפני גרמים (כולל שמות שמתחילים בל)
        _v3_d = re.match(r'^(?:הוסף|הפחת|העלה)\s+([א-ת\u05F3\']{2,10})\s+(?=\d)', full_no_meal)
        if _v3_d:
            _cand = _v3_d.group(1)
            if _cand not in _NOT_NAME_VERBS and _cand not in _FOOD_NOT_SURNAME:
                result["name"] = _cand
                conf = max(conf, 80)
                full_no_meal = (full_no_meal[:_v3_d.start(1)] + full_no_meal[_v3_d.end():])
                full_no_meal = re.sub(r'\s+', ' ', full_no_meal).strip()
    if not skip_name and _v3_triggered and "name" not in result:
        # Fix C: "במקום NAME FOOD" — שם לפני מזון מוחלף (מחליף לו NAME FOOD בNEW)
        _v3_bk = re.search(r'במקום\s+([א-ת]{2,8})', full_no_meal)
        if _v3_bk:
            _w1 = _v3_bk.group(1)
            if _w1 not in _NOT_NAME_VERBS and _w1 not in _FOOD_NOT_SURNAME:
                result["name"] = _w1
                conf = max(conf, 80)
                full_no_meal = (full_no_meal[:_v3_bk.start(1)] + full_no_meal[_v3_bk.end(1):])
                full_no_meal = re.sub(r'\s+', ' ', full_no_meal).strip()

    if not skip_name and "name" not in result:
        # כלל פוזיציוני: [פעולה] [שם פרטי] [שם משפחה] [מזון...]
        # מילה 1 = פעולה תמיד, מילה 2 = שם פרטי תמיד, מילה 3 = שם משפחה תמיד

        # טלפון: "ל0546..." — טיפול מיוחד לפני הכלל הפוזיציוני
        _phone_lm = re.search(r'(?<!\S)ל(972\d{9}|05[\d\-]{8,})', full_no_meal)
        if _phone_lm:
            result["name"] = _phone_lm.group(1)
            full_no_meal = full_no_meal[:_phone_lm.start()] + full_no_meal[_phone_lm.end():]
            full_no_meal = re.sub(r'\s+', ' ', full_no_meal).strip()
            clean_full = full_no_meal
        else:
            # פורמט "שם:" / "שם -" — שם לפני מפריד
            _pre_sep = re.match(
                r'^([\u05D0-\u05EA\u05F3\u0027]{2,}(?:\s+[\u05D0-\u05EA\u05F3\u0027]{2,}){0,2})\s*[-:\u2013]\s*',
                full_no_meal
            )
            if _pre_sep and all(w not in _NOT_NAME_VERBS for w in _pre_sep.group(1).split()):
                _pn = _pre_sep.group(1)
                if _pn.startswith('ל') and len(_pn) > 2 and _pn not in _L_NAMES:
                    _pn = _pn[1:]
                result["name"] = _pn
                conf = max(conf, 80)
                clean_full = full_no_meal[_pre_sep.end():]
                clean_full = re.sub(r'\s+', ' ', clean_full).strip()
            else:
                # כלל פוזיציוני: דלג על פעלים/מילות קישור, ואז מילה 1 = שם פרטי, מילה 2 = שם משפחה
                _wds = full_no_meal.split()
                _pos = 0

                _SKIP_WORDS = _NOT_NAME_VERBS | {
                    'מוסיף', 'מוריד', 'מחליף', 'מעלה', 'מפחית',
                    'אפשר', 'צריך', 'רוצה', 'מבקש', 'יצטרך', 'אוכל',
                    'ניתן', 'כדאי', 'אולי', 'נא', 'גם', 'בבקשה',
                    'תוכלי', 'תוכל', 'יכולה', 'יכול', 'הכניסי', 'הכניס',
                    'תכניסי', 'תכניס', 'רוצים', 'מבקשים', 'אשמח', 'אם',
                    'לתת', 'לשים', 'של', 'עבור', 'את',
                    'כן', 'אוקי', 'נכון', 'טוב', 'בסדר', 'בטח', 'כבר', 'ממש',
                    'תן', 'תני', 'תתן', 'יש', 'האם', 'חשוב', 'תעשה', 'תעשי',
                    'בכל', 'ותוסיפי', 'שני', 'תקן', 'תקני', 'ותחליף', 'ותוריד',
                    'מקרה', 'חשבתי', 'מציע', 'מאוד', 'בסוף', 'הגיע', 'מוצע',
                    'וסיפי', 'פחת',
                }
                while _pos < len(_wds) and (
                    re.match(_VERB_PAT + r'$', _wds[_pos])
                    or _wds[_pos] in _SKIP_WORDS
                    or _wds[_pos] == 'ל'
                    or re.match(r'^ש(?:תוסיפ|תחליפ|תחסר|תחליפ|תוריד|תעל|הוסיפ|הכנס|תשימ|תניח)', _wds[_pos])
                ):
                    _pos += 1

                _fn = _wds[_pos] if len(_wds) > _pos else ''
                _ln = _wds[_pos + 1] if len(_wds) > _pos + 1 else ''

                # strip ל' יחס מהשם הפרטי ("לרון" → "רון"), אלא אם ל' חלק מהשם
                if _fn.startswith('ל') and len(_fn) > 2 and _fn not in _L_NAMES:
                    _fn = _fn[1:]

                _fn_ok = bool(_fn and _fn not in _NOT_NAME_VERBS and _fn not in _FOOD_NOT_SURNAME
                              and not re.match(r'^\d', _fn) and len(_fn) >= 2)
                _ln_clean = re.sub(r'[\u05F3\u0027]+$', '', _ln)
                _ln_ok = bool(_ln and _ln not in _NOT_NAME_VERBS and _ln not in _FOOD_NOT_SURNAME
                              and not re.match(r'^\d', _ln)
                              and re.match(r'^[\u05D0-\u05EA\u05F3\u0027]{2,}', _ln)
                              and _looks_like_surname(_ln_clean))

                if _fn_ok and _ln_ok:
                    result["name"] = f"{_fn} {_ln}"
                    conf = max(conf, 90)
                    clean_full = ' '.join(_wds[_pos + 2:])
                elif _fn_ok:
                    result["name"] = _fn
                    conf = max(conf, 75)
                    clean_full = ' '.join(_wds[_pos + 1:])
                else:
                    # Fallback: סרוק ל/של/עבור + שם בכל מקום (פורמטים ישנים)
                    _found_fb = False
                    for _l_m in re.finditer(
                        r'(?<!\S)(?:ל\s*|של\s+|עבור\s+)([\u05D0-\u05EA\u05F3\u0027]{2,8}(?:\s+[\u05D0-\u05EA\u05F3\u0027]{2,8})?)',
                        full_no_meal
                    ):
                        _lw = _l_m.group(1).split()
                        _lfn = _lw[0] if _lw else ''
                        _lln = _lw[1] if len(_lw) > 1 else ''
                        if (_lfn not in _NOT_NAME_VERBS and _lfn not in _FOOD_NOT_SURNAME
                                and ('ל' + _lfn) not in _FOOD_NOT_SURNAME
                                and len(_lfn) >= 2 and not re.match(r'^\d', _lfn)):
                            _lln_clean = re.sub(r'[\u05F3\u0027]+$', '', _lln)
                            if _lln and _looks_like_surname(_lln_clean) and _lln not in _FOOD_NOT_SURNAME:
                                result["name"] = f"{_lfn} {_lln}"
                            else:
                                result["name"] = _lfn
                            conf = max(conf, 70)
                            clean_full = (full_no_meal[:_l_m.start()] + full_no_meal[_l_m.end():])
                            _found_fb = True
                            break
                    if not _found_fb:
                        conf = 40
                        clean_full = full_no_meal
                clean_full = re.sub(r'\s+', ' ', clean_full).strip()
    else:
        clean_full = full_no_meal

    # נרמול synonyms לפני חילוץ מזון
    clean_full = re.sub(r'באופצי(?:ה|ות)?\s+של', 'במקום', clean_full)   # "באופציה של" / "באופציות של"
    clean_full = re.sub(r'כאופצי(?:ה|ות)?\s+(?:ל(?=[א-ת]{3,})|של)', 'במקום ', clean_full)
    clean_full = re.sub(r'כאופציות\b', '', clean_full)  # כאופציות ללא ל/של = מחיקה
    clean_full = re.sub(r'כתחליף\s+ל', 'במקום ', clean_full)
    clean_full = re.sub(r'בנוסף\s+ל-?', 'במקום ', clean_full)
    # "תחליפי X ב-Y" / "מחליף X ב-Y" → "X במקום Y"
    if re.search(r'(?:תחליפ[יה]|תחליף|החלפ[יה]?|החלף|להחליף|מחליף)\b', text) and 'במקום' not in clean_full:
        clean_full = re.sub(r"(?<=[א-ת%'׳\"])\s+ב-?([א-ת])", r' במקום \1', clean_full, count=1)
    clean_full = re.sub(r'אופציה\s+של\s+', '', clean_full)     # "אופציה של X" → "X"
    clean_full = re.sub(r'אופציה\s+לתחליף\s+', '', clean_full) # "אופציה לתחליף X" → "X"
    clean_full = re.sub(r'תחליף\s+של\s+', '', clean_full)       # "תחליף של X" → "X"
    clean_full = re.sub(r'\bעוד\s+', '', clean_full)             # "עוד 75 גרם" → "75 גרם"
    clean_full = re.sub(r'\bגם\s+', '', clean_full)             # "גם 50 גרם שקדים" → "50 גרם שקדים"
    clean_full = re.sub(r'\bאת\s+', '', clean_full)             # "את האורז" → "האורז"
    clean_full = re.sub(r'\bבבקשה\b', '', clean_full)           # "שמן זית בבקשה" → "שמן זית"
    clean_full = re.sub(r'^מה\s+עם\s+', '', clean_full)         # "מה עם שמן זית" → "שמן זית"
    clean_full = re.sub(r'^(?:תבדקי?|תראי?|תסתכלי?)\s+', '', clean_full)  # פעלי בדיקה
    clean_full = re.sub(r'\s+', ' ', clean_full).strip()

    # "לFOOD VERB ..." בתחילת משפט — hint לפני פועל: FOOD + "במקום" + HINT
    _hint_pre = re.match(
        r'^ל([\u05D0-\u05EA]{2,}(?:\s+[\u05D0-\u05EA]{2,})?)\s+' + _VERB_PAT + r'\s+',
        clean_full,
    )
    if _hint_pre:
        _pre_hint_word = _hint_pre.group(1)
        clean_full = clean_full[_hint_pre.end():].strip() + f' במקום {_pre_hint_word}'

    # ── ריבוי מזונות: "50 גרם X ו-30 גרם Y" → multi ops ───────────────────
    if not result.get("ops") and not result.get("change"):
        # strip leading verb — may be present when name came from "לNAME" pattern
        _cf_for_multi = re.sub(r'^(?:' + _VERB_PAT + r')\s+', '', clean_full).strip()
        _mf = _detect_multi_food(_cf_for_multi)
        if _mf and len(_mf) >= 2:
            _mf_meal = result.get("meal") or _extract_meal(full) or "ערב"
            result.setdefault("meal", _mf_meal)
            # first food qty may have been consumed by _name_grams into result["extra_grams"]
            _pre_grams = result.pop("extra_grams", None)
            result["ops"] = []
            for _mfi, (_mf_food, _mf_grams) in enumerate(_mf):
                _mf_food = _strip_definite_article(_mf_food.strip())
                _mf_op: dict = {"change": f"הוסף ({_mf_food})", "meal": _mf_meal}
                _qty = _mf_grams or (_pre_grams if _mfi == 0 else None)
                if _qty:
                    _mf_op["extra_grams"] = _qty
                if _reduce:
                    _mf_op["reduce"] = True
                result["ops"].append(_mf_op)
            result["change"] = result["ops"][0]["change"]
            result.setdefault("meal", "ערב")
            result["confidence"] = conf
            if _force_new:
                result["force_new"] = True
            if _reduce:
                result["reduce"] = True
            return result

    # מזון — על הטקסט ללא השם
    new_food, group_hint = _extract_foods(clean_full)
    if not new_food and ("name" in result or not result.get("change")):
        # fallback: הטקסט שנשאר אחרי הסרת שם+ארוחה = שם המזון
        # מסיר פעלים בתחילה ותווי זבל
        fb = re.sub(r'^(?:הוסיפ[יי]?|הוסיף|תוסיפ[יי]?|הוסף|הכנס[יי]?|שימ[יי]?|תשימ[יי]?|הכניס|עדכן|עדכני|תשנ[יה])(?:\s+|$)', '', clean_full).strip()
        fb = re.sub(r'[^א-ת\s\d%\'\"]+', ' ', fb).strip()
        fb = re.sub(r'\s+', ' ', fb).strip()
        if len(fb) >= 2:
            # נקה "כדאי ש...", "אפשר ש...", "תנסי ל...", "בוודאי ל..."
            fb = re.sub(r'^(?:כדאי\s+ש|אפשר\s+(?:ל|ש)?|בוודאי\s+ל?|תנסי\s+ל?|מה\s+אם\s+ל?)\s*', '', fb)
            fb = re.sub(r'^(?:נוסיף|נוסיפי|להוסיף|להכניס|הכניס|הכנס)\s+', '', fb)
            fb = fb.strip()
            # extra_grams: "50 גרם לAFOOD"
            gm = re.match(r'^(\d+)\s*גרם\s+(?:ל(?=[\u05D0-\u05EA]{3,}))?\s*', fb)
            if gm:
                result["extra_grams"] = gm.group(1)
                fb = fb[gm.end():].strip()
            # בדוק hint
            if ' במקום ' in fb:
                parts = fb.split(' במקום ', 1)
                new_food, group_hint = parts[0].strip(), parts[1].strip()
            elif ' ל ' in fb:
                parts = fb.split(' ל ', 1)
                new_food, group_hint = parts[0].strip(), parts[1].strip()
            else:
                # "פרגיות לחזה עוף" — ל מחובר (אל תפצל על לאחר/לפני)
                ml = re.search(r'^(.+?)\s+ל(?!אחר\b|פני\b)([א-ת].+)$', fb)
                if ml:
                    new_food, group_hint = ml.group(1).strip(), ml.group(2).strip()
                else:
                    new_food = fb
    # "אורז 30 גרם" — מספר + גרם בסוף שם מזון
    if new_food and not result.get("extra_grams"):
        _end_grams = re.search(r'^(.+?)\s+(\d+)\s*גרם\s*$', new_food.strip())
        if _end_grams:
            result["extra_grams"] = _end_grams.group(2)
            new_food = _end_grams.group(1).strip()
    # מספר בסוף שם מזון ללא "גרם" — חלץ כמות: "אורז 75" → food="אורז", extra_grams=75
    if new_food:
        _trail_num = re.match(r'^(.*?)\s+(\d+)\s*$', new_food.strip())
        if _trail_num and not re.search(r'\d', _trail_num.group(1)):
            new_food = _trail_num.group(1).strip()
            if not result.get("extra_grams"):
                result["extra_grams"] = _trail_num.group(2)
    # מספר בתחילת שם מזון ללא "גרם" — "150 אורז" → food="אורז", extra_grams=150
    if new_food and not result.get("extra_grams"):
        _lead_num = re.match(r'^(\d+)\s+(?!גרם\b)([א-ת].+)$', new_food.strip())
        if _lead_num:
            result["extra_grams"] = _lead_num.group(1)
            new_food = _lead_num.group(2).strip()

    # Fix B: הסרת ה' הידיעה מהמזון ומה-hint לפני בניית change string
    if new_food:
        new_food = _strip_definite_article(new_food)
    if group_hint:
        group_hint = _strip_definite_article(group_hint)

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

    # ── AI fallback: כשה-confidence נמוך — נסה Claude Haiku ──────────────────
    if conf < 70 and not skip_name:
        _ai = _parse_with_ai(text, v3_mode=_v3_triggered)
        if _ai:
            # merge selective — מעדכן רק שדות חסרים, לא מוחק פרסינג נכון של regex
            for _k, _v in _ai.items():
                if _k == "confidence":
                    conf = _v
                elif _k not in result or not result[_k]:
                    result[_k] = _v

    result.setdefault("meal", "ערב")
    result["confidence"] = conf
    if _force_new:
        result["force_new"] = True
    if _reduce:
        result["reduce"] = True
    return result


_MULTI_VERBS = (r'(?:תוסיפ[יי]?|הוסיפ[יי]?|תחליפ[יי]?|החליפ[יי]?'
                r'|תעל[יי]?|העל[יה]?|הגדל[יי]?|תגדיל[יי]?'
                r'|תוריד[יי]?|הורד[יי]?|הפחת[יי]?|תחסר[יי]?'
                r'|הכנס|תכניס)')

_NEGATION_PREFIX = re.compile(
    r'^\s*(?:אל\s+ת'
    r'|לא\s+(?:ל(?:הוסיף|החליף|עדכן|שנות|הוריד|העלות|העלות|הפחית|הגדיל)'
    r'|ת(?:וסיפ|חליפ|עלה|עלי|ורידי|וריד|גדיל|פחית)))',
    re.UNICODE
)

_READ_QUERY_PAT = re.compile(
    r'(?:מה\s+(?:יש|אוכל|קיים|נמצא)|תראי?\s+מה\s+יש|תבדקי?\s+מה\s+יש'
    r'|כמה\s+(?:יש|גרם)|מה\s+(?:יש\s+)?ב(?:ארוחת?\s+)?(?:ערב|בוקר|צהריים))',
    re.UNICODE
)

_ACTION_VERB_PAT = re.compile(
    r'(?:הוסיפ[יי]?|הוסיף|תוסיפ[יי]?|הוסף|החלף[יי]?|החליף|תחליף|תחליפ[יי]?'
    r'|הפחת[יי]?|תפחית[יי]?|תוריד[יי]?|הוריד[יי]?|עדכן|עדכני|תשנ[יה]'
    r'|מוסיף|מחליף|מוריד|מפחית|מעלה)',
    re.UNICODE
)


def _handle_read_query(request_text: str, name_override: str = "",
                       meal_override: str = "", user_id_override: str = "") -> str:
    """שאילתת קריאה: 'מה יש לדני בצהריים?' → רשימת מזונות ללא שינוי."""
    meal_key = _extract_meal(meal_override) if meal_override else _extract_meal(request_text)

    if name_override:
        name = name_override
    else:
        m = re.search(r'(?<!\S)ל([א-ת׳\']{2,10})(?:\s|$|[?!,])', request_text)
        name = m.group(1) if m else ""
        if not name:
            return "ℹ️ חסר: שם מתאמן לשאילתה"

    if user_id_override:
        user_id, full_name = user_id_override, name_override or name
    else:
        user_id, full_name, _ = find_user(name)
    if not user_id:
        return f"NAME_NOT_FOUND:{name}"

    all_meals = get_user_meals(user_id)

    food_q_match = re.search(
        r'(?:כמה\s+גרם\s+|כמה\s+)(.+?)\s+(?:יש|נמצא|קיים)', request_text
    )
    if food_q_match:
        food_q = normalize_food_query(food_q_match.group(1).strip())
        results_fq = []
        for meal in all_meals:
            for f in (meal.get("mealFoods") or []):
                fn = f.get("food_name","").lower()
                if food_q in fn or fn in food_q:
                    qty = f.get("quantity","")
                    results_fq.append(f"• {f['food_name']} — {qty}ג' ב{meal.get('meal_name','')}")
        if results_fq:
            return f"📋 {full_name}:\n" + "\n".join(results_fq)
        return f"❌ לא מצאתי '{food_q_match.group(1)}' בתפריט של {full_name}"

    _meal_map_rq = {"בוקר": "ארוחת בוקר", "צהריים": "ארוחת צהריים", "ערב": "ארוחת ערב",
                    "ביניים": "ארוחת ביניים", "לילה": "ארוחת ערב"}

    if not meal_key:
        lines = [f"📋 תפריט של {full_name}:"]
        for m in all_meals:
            foods = m.get("mealFoods") or []
            if not foods:
                continue
            lines.append(f"\n*{m.get('meal_name','')}:*")
            for f in foods:
                qty = f.get("quantity","")
                lines.append(f"  • {f.get('food_name','')}" + (f" {qty}ג'" if qty else ""))
        return "\n".join(lines) if len(lines) > 1 else f"לא נמצא תפריט עבור {full_name}"

    full_meal = _meal_map_rq.get(meal_key, f"ארוחת {meal_key}")
    target = next(
        (m for m in all_meals
         if full_meal in m.get("meal_name","") or m.get("meal_name","") in full_meal),
        None
    )
    if not target:
        return f"לא מצאתי {full_meal} עבור {full_name}"

    foods = target.get("mealFoods") or []
    if not foods:
        return f"{full_meal} של {full_name} ריקה"

    lines = [f"📋 {full_meal} של {full_name}:"]
    for f in foods:
        qty = f.get("quantity","")
        lines.append(f"• {f.get('food_name','')}" + (f" — {qty}ג'" if qty else ""))
    return "\n".join(lines)


def execute_request(request_text: str, force: bool = False,
                    name_override: str = "", meal_override: str = "",
                    food_override: str = "", hint_override: str = "",
                    user_id_override: str = "") -> str:
    # ── שאילתת קריאה: "מה יש לדני בצהריים?" ────────────────────────────────────
    if _READ_QUERY_PAT.search(request_text) and not _ACTION_VERB_PAT.search(request_text):
        return _handle_read_query(request_text, name_override, meal_override, user_id_override)
    # ── שלילה: "אל תוסיפי" / "לא להוסיף" ─────────────────────────────────────
    if _NEGATION_PREFIX.search(request_text.strip()):
        return "❓ לא הבנתי — נראה כמו שלילה. אם רצית לבצע פעולה, שלח שוב ללא 'אל' / 'לא'."
    # ── ריבוי אנשים: "לדני ולרון X" → 2 בקשות נפרדות ──────────────────────────
    if not name_override and not food_override and not hint_override:
        # "לX ולY FOOD" — שמות קצרים (מילה אחת, 2-8 תווים), לא מילות ארוחה
        _MEAL_WORDS = r'(?:ערב|בוקר|צהריים|צהרים|ביניים|לילה)'
        _mp = re.search(
            rf'ל([א-ת]{{2,8}})\s+ול([א-ת]{{2,8}})(?!\s+{_MEAL_WORDS})(?=[\s,]|$)',
            request_text
        )
        if _mp:
            n1, n2 = _mp.group(1).strip(), _mp.group(2).strip()
            # ודא ששניהם משתמשים אמיתיים (מונע פיצול שגוי של "רון וליצקו")
            uid2, _, _ = find_user(n2)
            if uid2 and uid2 not in ("MULTIPLE", "NOT_FOUND"):
                req1 = request_text[:_mp.start()] + f'ל{n1} ' + request_text[_mp.end():]
                req2 = request_text[:_mp.start()] + f'ל{n2} ' + request_text[_mp.end():]
                r1 = execute_request(req1.strip(), force, name_override, meal_override, food_override, hint_override, user_id_override)
                r2 = execute_request(req2.strip(), force, name_override, meal_override, food_override, hint_override, user_id_override)
                return f"{r1}\n\n{r2}"
    # ── ריבוי משימות: "תעלי לדני X ותוסיפי לו Y" / שורות נפרדות ─────────────
    if not name_override and not food_override and not hint_override:
        # פיצוח: "ו+פועל" OR שורה חדשה שמתחילה בפועל
        _mt_parts = re.split(
            rf'(?:\s+ו(?={_MULTI_VERBS})|\n\s*(?={_MULTI_VERBS}))',
            request_text.strip()
        )
        if len(_mt_parts) > 1:
            # חלץ שם וארוחה מהחלק הראשון
            _fp = parse_message(_mt_parts[0])
            _first_name = _fp.get("name", "")
            _first_meal = _fp.get("meal", "") or ""
            sub_results = []
            for i, part in enumerate(_mt_parts):
                if i > 0 and _first_name:
                    # "לו/לה/לזה" → "לFIRST_NAME"
                    part = re.sub(r'\bל(?:ו|ה|זה|זו|אותו|אותה)\b', f'ל{_first_name}', part)
                    # הוסף ארוחה אם חסרה
                    if _first_meal and not re.search(r'\b(?:ערב|בוקר|צהריים|ביניים)\b', part):
                        part += f' ב{_first_meal}'
                    # בדוק שיש מספיק תוכן (לפחות שם מזון אחד)
                    _pp = parse_message(part)
                    if not _pp.get("change") or not _pp.get("name"):
                        continue  # דלג על חלק חסר תוכן
                sub_results.append(execute_request(part, force, name_override,
                                                   meal_override, food_override, hint_override,
                                                   user_id_override))
            if not sub_results:
                pass  # ימשיך לפרסינג רגיל
            else:
                return "\n\n".join(sub_results)

    # ריבוי אנשים: "שם: X ... שם: Y" → מפצל ומטפל בנפרד
    if not name_override:
        _parts = re.split(r'(?m)(?=^\s*שם\s*:)', request_text.strip())
        _parts = [p.strip() for p in _parts if p.strip() and re.search(r'שם\s*:', p)]
        if len(_parts) > 1:
            sub_results = []
            for part in _parts:
                sub_results.append(execute_request(part, force, name_override,
                                                   meal_override, food_override, hint_override,
                                                   user_id_override))
            return "\n\n─────────────\n\n".join(sub_results)

    _skip_name = bool(name_override and _PHONE_RE.match(name_override.replace('-','').replace(' ','')))
    parsed = parse_message(request_text, skip_name=_skip_name)
    # V3: שם מגיע דרך --name (name_override), לא מתוך הטקסט
    if name_override:
        parsed["name"] = name_override
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
        # fallback: שם 2 מילים לא נמצא → נסה מילה ראשונה, שאר המילים → לשם המזון
        if not uid_check and ' ' in raw_name:
            parts = raw_name.split(None, 1)
            uid_check, found_name, is_fuzzy = find_user(parts[0])
            if uid_check:
                parsed["name"] = parts[0]
                leftover = parts[1]
                ch = parsed.get("change", "")
                parsed["change"] = re.sub(r'הוסף \(', f'הוסף ({leftover} ', ch, count=1)
        if uid_check == "MULTIPLE":
            # ריבוי מתאמנים — בקש טלפון לזיהוי
            options = found_name.split(";")
            ids_pipe = "|".join(opt.split("|")[0] for opt in options)
            names_pipe = "|".join(opt.split("|")[1] for opt in options)
            return f"NAME_OPTIONS:{ids_pipe}||{names_pipe}\nמצאתי {len(options)} מתאמנים בשם *{raw_name}*.\n\nשלח/י מספר טלפון לזיהוי."

        if not uid_check:
            return f"NAME_NOT_FOUND:{raw_name}"

        # שם מדויק (לא fuzzy) → בצע ישירות, ללא אישור (גם free text)
        if not is_fuzzy and parsed.get("change"):
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
            icon = "➖" if (parsed.get("reduce") or op.get("reduce")) else "➕"
            action_lines.append(f"{meal_prefix}{icon} {nf}" + (f" ← {of}" if of else ""))

        summary = f"👤 {display_name}\n🍽 {meal_display}\n" + "\n".join(action_lines)

        if is_fuzzy:
            # קידוד: CONFIRM_WITH_NAME:{שם מתוקן}|||{summary}
            return f"CONFIRM_WITH_NAME:{found_name}|||{summary}"
        return f"CONFIRM:{summary}"

    name   = name_override if name_override else parsed["name"]

    # כשname_override שונה מהשם שנוסח — בדוק אם יש מילות "עודף" שנספחו לשם
    # דוגמה: parsed["name"]="דני שיבולת", name_override="דני קגנוביץ'" → "שיבולת" חזרה לאוכל
    if name_override and parsed.get("name") and parsed["name"] != name_override:
        def _ng_s(s): return re.sub(r"[׳״\']", "", s)
        def _strip_l(s): return re.sub(r'^ל', '', s)  # "ליה" → "יה"
        # השוואה fuzzy: strip ל, נרמול גרש, levenshtein≤1
        def _name_word_match(pw, ow):
            pn = _ng_s(_strip_l(pw)); on = _ng_s(_strip_l(ow))
            return pn == on or _levenshtein(pn, on) <= 1
        override_words = name_override.split()
        extra_words = [w for w in parsed["name"].split()
                       if not any(_name_word_match(w, ow) for ow in override_words)]
        if extra_words:
            leftover = " ".join(extra_words)
            ch = parsed.get("change", "")
            parsed["change"] = re.sub(r'הוסף \(', f'הוסף ({leftover} ', ch, count=1)
            for _op in parsed.get("ops", []):
                _op["change"] = re.sub(r'הוסף \(', f'הוסף ({leftover} ', _op.get("change",""), count=1)

    # בנה רשימת ארוחות ברירת מחדל
    if meal_override:
        m = _extract_meal(meal_override)
        meals_list = [m if m else meal_override]
    else:
        meals_list = parsed.get("meals") or [parsed.get("meal", "ערב")]

    # מצא מתאמן (פעם אחת)
    if user_id_override:
        user_id, full_name, is_fuzzy = user_id_override, name_override or name, False
    else:
        user_id, full_name, is_fuzzy = find_user(name)
    # fallback: שם 2 מילים לא נמצא → נסה מילה ראשונה
    if not user_id_override and not user_id and not name_override and ' ' in name:
        parts = name.split(None, 1)
        user_id2, full_name2, is_fuzzy2 = find_user(parts[0])
        if user_id2:
            leftover = parts[1]
            user_id, full_name, is_fuzzy = user_id2, full_name2, is_fuzzy2
            parsed["name"] = parts[0]
            ch = parsed.get("change", "")
            parsed["change"] = re.sub(r'הוסף \(', f'הוסף ({leftover} ', ch, count=1)
            # עדכן ops אם קיים
            for _op in parsed.get("ops", []):
                _op["change"] = re.sub(r'הוסף \(', f'הוסף ({leftover} ', _op.get("change",""), count=1)
    if user_id == "MULTIPLE":
        # ריבוי מתאמנים באותו שם — בקש טלפון לזיהוי
        options = full_name.split(";")
        ids_pipe = "|".join(opt.split("|")[0] for opt in options)
        names_pipe = "|".join(opt.split("|")[1] for opt in options)
        return f"NAME_OPTIONS:{ids_pipe}||{names_pipe}\nמצאתי {len(options)} מתאמנים בשם *{name}*.\n\nשלח/י מספר טלפון לזיהוי."
    if not user_id:
        return f"NAME_NOT_FOUND:{name}"
    # safety: אם עדיין fuzzy אחרי name_override — שלח שגיאה
    if is_fuzzy and name_override and name_override == name:
        return f"NAME_NOT_FOUND:{name}"

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

        # ניקוי prefix ארוחה שדלף לשם המזון: "בצהריים", "מארוחת ערב", "עבור/של ארוחת X"
        new_food_raw = re.sub(r'\s*[בלמ](?:ארוחת\s+)?ה?(?:ערב|בוקר|צהריים|ביניים|לילה)\b', '', new_food_raw).strip()
        new_food_raw = re.sub(r'\s*(?:עבור|של)\s+(?:ארוחת\s+)?(?:ערב|בוקר|צהריים|ביניים|לילה)\b', '', new_food_raw).strip()

        # חלץ מסוגריים תחילה — כדי שזיהוי הגרמים יעבוד גם על "(50 גרם אורז)"
        paren = re.search(r'\(([^)]+)\)', new_food_raw)
        new_food_clean = paren.group(1).strip() if paren else new_food_raw
        paren2 = re.search(r'\(([^)]+)\)', group_hint_raw)
        group_hint = paren2.group(1).strip() if paren2 else group_hint_raw

        # גרמים: מה-op / מה-parsed (free-text) / "עוד X גרם" / "X גרם [ל]FOOD" / "FOOD X גרם"
        extra_grams = op.get("extra_grams") or parsed.get("extra_grams")
        grams_in_food = re.search(r'\bעוד\s+(\d+)\s*גרם\b', new_food_clean)
        if grams_in_food:
            extra_grams = grams_in_food.group(1)
            new_food_clean = re.sub(r'\bעוד\s+\d+\s*גרם\s*', '', new_food_clean).strip()
        elif not extra_grams:
            m_gs2 = re.match(r'^(\d+)\s*גרם\s+(?:ל(?=[\u05D0-\u05EA]{3,}))?\s*', new_food_clean)  # "50 גרם [ל]אורז"
            if m_gs2:
                extra_grams = m_gs2.group(1)
                new_food_clean = new_food_clean[m_gs2.end():].strip()
            else:
                m_ge2 = re.search(r'^(.+?)\s+(\d+)\s*גרם\s*$', new_food_clean)  # "אורז 50 גרם"
                if m_ge2:
                    extra_grams = m_ge2.group(2)
                    new_food_clean = m_ge2.group(1).strip()
        # "150 אורז" — מספר לפני שם מזון ללא גרם
        if not extra_grams:
            m_nf = re.match(r'^(\d+)\s+([א-ת].+)$', new_food_clean)
            if m_nf:
                extra_grams = m_nf.group(1)
                new_food_clean = m_nf.group(2).strip()

        # בפקודת הפחתה: strip prefix "מ/מה" אחרי גרמים ("50 גרם מהאורז" → "אורז")
        if op.get("reduce") or parsed.get("reduce"):
            new_food_clean = re.sub(r'^מה?', '', new_food_clean).strip()

        new_food_query = normalize_food_query(new_food_clean)

        if hint_override and op_idx == 0:
            group_hint = hint_override

        # חפש מזון
        if food_override and op_idx == 0:
            foods = search_food(normalize_food_query(food_override), coach_id)
            best_food = next((f for f in foods if f.get("food_name","").lower() == food_override.lower()), None) \
                        or (foods[0] if foods else None)
            if not best_food:
                # fallback: find_best_food עם חיפוש חכם → FOOD_OPTIONS אם יש חלופות
                best_food, alternatives = find_best_food(normalize_food_query(food_override), coach_id)
                if not best_food:
                    if alternatives:
                        def _fmt(f):
                            cal = int(float(f.get('calories') or 0))
                            return f"{f['food_name']}{f' — {cal} קל' if cal else ''}"
                        options = "\n".join(f"{i+1}. {_fmt(f)}" for i, f in enumerate(alternatives[:10]))
                        alts_pipe = "|".join(f"{f.get('food_name','')}:{int(float(f.get('calories',0) or 0))}" for f in alternatives)
                        more_hint = f"\n\n_שלח *עוד* לאפשרויות נוספות_" if len(alternatives) > 10 else ""
                        return (f"FOOD_OPTIONS:{normalize_food_query(food_override)}||{alts_pipe}\n"
                                f"לא מצאתי *{food_override}*, מה שמצאתי:\n"
                                f"{options}\n\n"
                                f"בחר מספר, כתוב שם אחר, או שלח *עוד*.{more_hint}")
                    all_results.append(f"❓ לא נמצא '{food_override}' במאגר.")
                    continue
        else:
            best_food, alternatives = find_best_food(new_food_query, coach_id)
            if not best_food:
                if len(ops_list) == 1:
                    if alternatives:
                        def _fmt(f):
                            cal = int(float(f.get('calories') or 0))
                            return f"{f['food_name']}{f' — {cal} קל' if cal else ''}"
                        options = "\n".join(f"{i+1}. {_fmt(f)}" for i, f in enumerate(alternatives[:10]))
                        alts_pipe = "|".join(f"{f.get('food_name','')}:{int(float(f.get('calories',0) or 0))}" for f in alternatives)
                        more_hint = f"\n\n_שלח *עוד* לאפשרויות נוספות_" if len(alternatives) > 10 else ""
                        return (f"FOOD_OPTIONS:{new_food_query}||{alts_pipe}\n"
                                f"לא מצאתי *{new_food_query}*, מה שמצאתי:\n"
                                f"{options}\n\n"
                                f"בחר מספר, כתוב שם אחר, או שלח *עוד*.{more_hint}")
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

            # ─── גרמים ללא group_hint → UPDATE כמות קיימת ────────────────────
            # force_new=True (בקשת "מזון חדש") → תמיד ADD, לא UPDATE
            _is_reduce = op.get("reduce") or parsed.get("reduce")
            if extra_grams and not group_hint and (not parsed.get("force_new") or _is_reduce):
                _, upd_row, upd_err = find_meal_and_food(all_meals, full_meal, new_food_query)
                if upd_row:
                    curr_q = float(upd_row.get("quantity") or upd_row.get("quantity_to_calculate") or 0)
                    if _is_reduce:
                        new_q = max(0.0, curr_q - float(extra_grams))
                        action_label = "הופחת"
                        arrow = "↓"
                    else:
                        new_q = curr_q + float(extra_grams)
                        action_label = "עודכן"
                        arrow = "→"
                    upd = update_food_quantity(user_id, upd_row["id"], new_q)
                    fname = upd_row.get("food_name", "")
                    if upd.get("status"):
                        all_results.append(
                            f"✅ {action_label}: *{fname}* "
                            f"{int(round(curr_q))}{arrow}{int(round(new_q))} גרם "
                            f"ב{full_meal} של {full_name}"
                        )
                    else:
                        all_results.append(f"❌ שגיאת עדכון: {upd.get('message','')}")
                    continue
                elif _is_reduce:
                    # מזון לא נמצא בארוחה — חפש בארוחות אחרות ודווח
                    avail = [f.get("food_name","") for meal in all_meals
                             if (meal_name_norm := re.sub(r'(?<=\s)ה(?=[א-ת])','',full_meal).strip()) in meal.get("meal_name","")
                             for f in (meal.get("mealFoods") or [])]
                    _other_meals: list[str] = []
                    _nfq_low = new_food_query.lower()
                    for _om in all_meals:
                        _om_name = _om.get("meal_name", "")
                        if full_meal.replace("ארוחת ", "") in _om_name or _om_name in full_meal:
                            continue
                        for _of in (_om.get("mealFoods") or []):
                            _ofn = _of.get("food_name", "")
                            if _nfq_low in _ofn.lower() or _ofn.lower() in _nfq_low:
                                _other_meals.append(_om_name)
                                break
                    _other_str = f" הוא נמצא ב{'/'.join(_other_meals)}." if _other_meals else ""
                    if avail:
                        all_results.append(f"❌ לא מצאתי '{new_food_query}' ב{full_meal}.{_other_str}\nמזונות בארוחה: {', '.join(avail)}")
                    else:
                        all_results.append(f"❌ לא מצאתי '{new_food_query}' ב{full_meal}.{_other_str}")
                    continue

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
                            def _fmt_h(f):
                                cal = int(float(f.get('calories') or 0))
                                return f"{f.get('food_name','')}{f' — {cal} קל' if cal else ''}"
                            opts = "\n".join(f"{i+1}. {_fmt_h(f)}" for i, f in enumerate(meal_foods))
                            foods_list = "|".join(f"{f.get('food_name','')}:{int(float(f.get('calories',0) or 0))}" for f in meal_foods)
                            return (f"HINT_OPTIONS:{group_hint}||{foods_list}\n{prefix}"
                                    f"לא מצאתי *{group_hint}* ב{full_meal}, במה תרצה להחליף את *{new_food_query}*?\n"
                                    f"המזונות שיש בארוחה:\n{opts}\n\n"
                                    f"בחר מספר או כתוב שם אחר.")
                    all_results.append(f"⚠️ '{group_hint}' לא קיים ב{full_meal} — דלגתי")
                    continue
                all_results.append(err)
                continue

            add_result = add_food_to_meal(user_id, meal_id, best_food, food_row, extra_grams)
            # חלץ actual_grams מהתוצאה (הוטמע ע"י add_food_to_meal)
            _ag_match = re.search(r'\|GRAMS=([\d.]+)', add_result)
            _actual_grams_used = _ag_match.group(1) if _ag_match else None
            add_result = re.sub(r'\|GRAMS=[\d.]+', '', add_result)
            if add_result.startswith("✅"):
                food_name = best_food.get("food_name", "")
                if food_row:
                    replaced = food_row.get("food_name", "")
                    # גרמי המזון המוחלף — gram_value הוא השדה הנכון
                    replaced_q = (food_row.get("quantity") or food_row.get("quantity_to_calculate") or
                                  food_row.get("gram_value") or food_row.get("grams") or "")
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
                    # גרמי המזון החדש — actual_grams שנשלחו בפועל לאוטופיט
                    if _actual_grams_used:
                        try:
                            _ag_f = float(_actual_grams_used)
                            new_disp = f" ({int(_ag_f) if _ag_f == int(_ag_f) else round(_ag_f,1)} גרם)"
                        except: new_disp = f" ({_actual_grams_used} גרם)"
                    elif extra_grams:
                        new_disp = f" ({extra_grams} גרם)"
                    else:
                        new_disp = ""
                    all_results.append(f"✅ *{food_name}*{new_disp} ← החליף: {replaced}{replaced_disp}\nב{full_meal} של {full_name}")
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

    allow_phone      = "--allow-phone" in args
    args = [a for a in args if a != "--allow-phone"]

    name_override    = _pop_arg("--name")
    meal_override    = _pop_arg("--meal")
    food_override    = _pop_arg("--food")
    hint_override    = _pop_arg("--hint")
    menu_name        = _pop_arg("--menu")
    menu_meal        = _pop_arg("--menu-meal")
    user_id_override = _pop_arg("--user-id")
    list_users       = "--list-users" in args
    args = [a for a in args if a != "--list-users"]
    find_user_query  = _pop_arg("--find-user")

    # הגנה: חוסם שימוש במספר טלפון ישראלי כ--name ללא דגל --allow-phone
    # מונע הרצת פקודות בטעות על משתמש אמיתי תוך כדי בדיקות
    if name_override and _PHONE_RE.match(name_override.replace("-","").replace(" ","")) and not allow_phone:
        import sys as _sys
        print(f"⛔ BLOCKED: --name '{name_override}' נראה כמו מספר טלפון ישראלי.")
        print("   הוסף --allow-phone אם אתה בטוח שאתה רוצה לפעול על משתמש זה.")
        _sys.exit(1)

    if find_user_query:
        # בדוק אם query הוא שם פרטי עצמאי (= מתחיל את השם המלא של לקוח קיים)
        uid, full_name, _ = find_user(find_user_query)
        if uid and uid != "MULTIPLE" and full_name:
            norm_full = re.sub(r"[׳״\']", "", (full_name or "").strip())
            norm_q    = re.sub(r"[׳״\']", "", find_user_query.strip())
            # FOUND רק אם השם המלא מתחיל ב-query = query הוא שם פרטי, לא שם משפחה
            print("FOUND" if norm_full.lower().startswith(norm_q.lower()) else "NOT_FOUND")
        else:
            print("NOT_FOUND")
        sys.exit(0)

    if list_users:
        # מחזיר את כל שמות הלקוחות (שורה אחת לכל שם) — לשימוש index.js
        _, _, _ = find_user("א")  # warm up cache
        if USER_CACHE_FILE.exists():
            cached = json.loads(USER_CACHE_FILE.read_text(encoding="utf-8"))
            for u in cached.get("users", []):
                fn = u.get("name") or u.get("full_name") or ""
                if fn:
                    print(fn)
        sys.exit(0)

    if menu_name:
        uid, full_name, _ = find_user(menu_name)
        if not uid:
            print(f"❌ לא מצאתי '{menu_name}'")
            sys.exit(1)
        if uid == "MULTIPLE":
            # יש כמה משתמשים עם אותו שם — הצג שניהם עם מספר ID
            entries = [e.split("|", 1) for e in full_name.split(";")]
            parts = [f"⚠️ נמצאו {len(entries)} מתאמנים בשם '{menu_name}':"]
            for eid, ename in entries:
                parts.append(f"\n🔸 {ename} (ID: {eid}):")
                parts.append(format_menu(eid, ename, menu_meal or ""))
            print("\n".join(parts))
            sys.exit(0)
        print(format_menu(uid, full_name, menu_meal or ""))
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
                              hint_override=hint_override,
                              user_id_override=user_id_override))
    except Exception as e:
        print(f"שגיאה: {e}", file=sys.stderr)
        sys.exit(1)
