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

# ── חיבור משותף: keep-alive בין קריאות באותה פקודה (חוסך TLS handshake חוזר) ──
_session = requests.Session()

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
מבנה ההודעה: מילה1=פעולה, מילה2=שם פרטי, מילה3=שם משפחה, שאר=מזון+ארוחה.
החזר JSON בלבד עם השדות:
- name: שם פרטי + שם משפחה (מילה 2 + מילה 3 בהודעה)
- food: שם המזון
- meal: בוקר/צהריים/ערב/ביניים (או null)
- grams: מספר בלבד (או null)
- hint: מזון להחלפה אם יש "במקום X" (או null)
- action: הוסף / הפחת / החלף (ברירת מחדל: הוסף)
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
        print(f"[AI parse] {data} → {result}", flush=True, file=__import__("sys").stderr)
        return result
    except Exception as e:
        print(f"[AI parse error] {e}", flush=True, file=__import__("sys").stderr)
        return {}

FOOD_API   = "https://food.we-site.co.il/api"
FOOD_TOKEN = "eb8b0f58c895019fcbc3bb17480ced3a2d1e12a346d6ed0f0d0267a24587a203"
SESSION_FILE   = Path(__file__).parent / "session.json"
PHONES_FILE    = Path(__file__).parent / "phones.json"
USER_CACHE_FILE = Path(__file__).parent / "user_cache.json"
USER_CACHE_TTL  = 1800  # 30 דקות

_uid_cache: dict = {}
_last_phone_refetch: float = 0.0  # הגנת קצב לרענון-לקוח-חדש

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
    r = _session.post(f"{BACKEND}{path}", json=body, headers=_headers(), timeout=15)
    if r.status_code == 429 and _retry < 3:
        time.sleep(2 ** _retry)
        return _post(path, body, _retry + 1)
    r.raise_for_status()
    return r.json()

def _get(path: str, params: dict = None, _retry: int = 0) -> dict:
    r = _session.get(f"{BACKEND}{path}", params=params, headers=_headers(), timeout=15)
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
    global _uid_cache, _last_phone_refetch
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
            def _match_phone(users):
                for u in users:
                    u_phone = _to_il(_phone_norm(u.get("phone", "")))
                    _clean_9 = clean_il[1:] if (clean_il.startswith('0') and len(clean_il) == 10) else clean_il
                    if u_phone and (u_phone == clean_il or u_phone == clean or u_phone == _clean_9):
                        uid = str(u["id"])
                        full_name = (u.get("name") or f"{u.get('first_name','')} {u.get('last_name','')}").strip()
                        return uid, full_name
                return None

            def _fetch_all_users():
                _users, _page = [], 1
                while True:
                    _data = _post("/coach/get-coach-users", {"page": _page, "limit": 100})
                    _users.extend(_data.get("data", []))
                    _total = _data.get("pagination", {}).get("total", 0)
                    if _page * 100 >= _total:
                        break
                    _page += 1
                try:
                    USER_CACHE_FILE.write_text(
                        json.dumps({"ts": time.time(), "users": _users}, ensure_ascii=False),
                        encoding="utf-8"
                    )
                except:
                    pass
                return _users

            # 1) נסה מהקאש (אם טרי)
            _phone_users, _from_cache = [], False
            try:
                if USER_CACHE_FILE.exists():
                    _cd = json.loads(USER_CACHE_FILE.read_text(encoding="utf-8"))
                    if time.time() - _cd.get("ts", 0) < USER_CACHE_TTL:
                        _phone_users = _cd["users"]
                        _from_cache = True
            except:
                pass
            if not _phone_users:
                _phone_users = _fetch_all_users()

            _m = _match_phone(_phone_users)
            # 2) לקוח חדש (היום): לא נמצא + הנתונים מהקאש → רענן מיד ונסה שוב.
            #    הגנת קצב: רענון אחד ל-90 שניות (שלא נציף את ה-API על מספר לא-קיים).
            if _m is None and _from_cache and (time.time() - _last_phone_refetch > 90):
                _last_phone_refetch = time.time()
                _m = _match_phone(_fetch_all_users())
            if _m:
                _uid_cache[query] = (_m[0], _m[1], False)
                return _m[0], _m[1], False
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
    r = _session.get(
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

_PROTECT_L = frozenset({"לפני", "לאחר", "לבנה", "לבן", "לחה", "לחים", "לחות",
                        "לחם", "לחמניה", "לחמניות", "לחמנייה", "לביבה", "לביבות",
                        "לזניה", "לפתן", "לימון", "לקט"})  # מילים שה-ל' חלק מהשם (לא ל' יחס)
# סיומות (המילה ללא ה-ל') — לשלילת ה-ל' כמפריד "במקום": "1 לחם" לא ייחתך ל-"1"/"חם",
# "גבינה לבנה" לא תיחתך ל-"גבינה"/"בנה". מחליף את ה-hardcoded אחר/פני בלוקאהד המפריד.
_PROTECT_L_SUFFIX = '|'.join(sorted((re.escape(w[1:]) for w in _PROTECT_L if len(w) > 1),
                                    key=len, reverse=True))
_PROTECT_H = frozenset({"הודו"})  # מילים שה' חלק מהמילה (לא ה' הידיעה)

def normalize_food_query(q: str) -> str:
    """מסיר ה' הידיעה, גרש, תווי תבנית (<>), ומנרמל ביטויי בישול נפוצים."""
    q = re.sub(r'[<>]', '', q)
    q = re.sub(r"['׳״]", '', q)  # גרש (' ׳ ״) — "קוטג'" → "קוטג"
    # ביטויי בישול — תומך גם ב"הבישול" / "הבשול" (עם ה' הידיעה)
    q = re.sub(r'לפני\s+ה?(?:בישול|בשול)\b', 'לפני בישול', q)
    q = re.sub(r'(?:אחרי|לאחר)\s+ה?(?:בישול|בשול)\b', 'מבושל', q)
    q = re.sub(r'\bלא\s+מבושל\b', 'לפני בישול', q)  # \b מונע התאמה ב'מלא מבושל'
    words = re.split(r'\s+', q.strip())
    # ה' הידיעה — אל תסיר אם המילה ב-_PROTECT_H (ה' חלק מהשם)
    words = [w if w in _PROTECT_H else re.sub(r'^ה(?=[א-ת])', '', w) for w in words if w]
    # ל' מיידית: "לאורז"→"אורז" — רק כשנשאר ≥3 תווים, ולא מילות עזר כמו "לפני"/"לאחר"
    words = [w if w in _PROTECT_L else re.sub(r'^ל(?=[א-ת]{3,})', '', w) for w in words]
    return " ".join(words)

def _collapse_doubled_letters(s: str) -> str:
    """מכווץ רצף של אותו תו עברי עוקב לתו יחיד — תיקון שגיאת הקלדה נפוצה של
    הקשה כפולה ('פפרגיות'→'פרגיות', 'ססלמון'→'סלמון'). בעברית כמעט ואין שמות
    מזון עם אות כפולה רצופה, לכן הכיווץ בטוח ומופעל רק כשחיפוש רגיל לא מצא כלום."""
    return re.sub(r'([א-ת])\1+', r'\1', s)

def _canon_cook(s: str) -> str:
    """מאחד את כל ביטויי הבישול לצורה קנונית אחת ('מבושל') — להשוואת שמות מזון.
    'לאחר בישול' = 'אחרי בישול' = 'מבושל' = 'מבושלת' → כולם זהים. מונע כישלון חיפוש
    כשהמאגר כותב 'פרגית לאחר בישול' אבל ה-query נורמל ל'פרגית מבושל' (וגם הפוך)."""
    s = s.lower()
    s = re.sub(r'(?:אחרי|לאחר)\s+ה?(?:בישול|בשול)\b', 'מבושל', s)
    s = re.sub(r'\bמבושל[הת]?\b', 'מבושל', s)
    return re.sub(r'\s+', ' ', s).strip()

# אליאסים: כשמבקשים X, מחפשים Y אלא אם צוין אחרת
_FOOD_ALIASES: dict[str, str] = {
    "חזה עוף":       "חזה עוף לאחר בישול",
    "קוואקר":        "שיבולת שועל",
    "שיבולת":        "שיבולת שועל",
    "שיבולות שועל":  "שיבולת שועל",   # רבים → יחיד (דני כותב "שיבולות")
    "לחמניה":        "לחמניה רגילה",   # "לחמניה" לבד לא במאגר — דני: = לחמניה רגילה
    "לחמניות":       "לחמניה רגילה",
    "תפוח":          "תפוח עץ",          # "תפוח" לבד = תפוח עץ (לא במאגר נקי); תוקן 2026-06-11
    "תפוחים":        "תפוח עץ",
    "קוטג":          "קוטג 5%",          # "קוטג" לבד לא במאגר נקי; ברירת מחדל 5%; תוקן 2026-06-12
    "קוטג'":         "קוטג 5%",
    "קוטג׳":         "קוטג 5%",
    "פרכיות":        "פרכית",            # רבים→יחיד: "פרכיות" לא נמצא נקי, "פרכית"→פרכיות אורז; 2026-06-12
    "קוסמת":         "כוסמת",            # שגיאת כתיב נפוצה: ק במקום כ (דני); 2026-06-12
    "ריבה":          "ריבה - מעדן ארבעה פירות, סאנט דלפור",  # קבוע: דני קבע (2026-06-11)
    "גרנולה":        "שיבולת שועל",
    "חזה":           "חזה עוף לאחר בישול",
    "הודו טחון":     "הודו",
    "בשר טחון":      "בשר בקר",
    "בשר בקר טחון":  "בשר בקר",
    "דג":            "סלמון",
    "סלמון":         "סלמון אטלנטי",
}


def _singularize_query(q: str) -> list:
    """מחזיר וריאציות יחיד אפשריות לשאילתת מזון ברבים (כל מילה ברבים מומרת ליחיד,
    אחת בכל פעם — בד\"כ רק שם-העצם הראשי ברבים). דוגמה: 'שיבולות שועל' → 'שיבולת שועל'."""
    words = q.split()
    cands = []
    for idx, w in enumerate(words):
        sing = []
        if w.endswith("יות") and len(w) > 4:      # עוגיות→עוגיה
            sing.append(w[:-3] + "יה")
        if w.endswith("ות") and len(w) > 3:        # שיבולות→שיבולת/שיבולה, בננות→בננה
            stem = w[:-2]
            sing += [stem + "ת", stem + "ה", stem]
        if w.endswith("ים") and len(w) > 3:        # תפוחים→תפוח, חלבונים→חלבון
            sing.append(w[:-2])
        for s in sing:
            cand = " ".join(words[:idx] + [s] + words[idx + 1:])
            if cand != q and cand not in cands:
                cands.append(cand)
    return cands

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
    # ביטוי בישול ב-query → ה-API (התאמת מחרוזת) לא יחזיר 'X לאחר בישול' עבור 'X מבושל'.
    # חפש גם לפי הבסיס (בלי ביטוי הבישול) כדי להביא את כל הווריאציות; ה-canon יתאים.
    _cook_base = re.sub(r'\b(?:מבושל[הת]?|לאחר\s+בישול|אחרי\s+בישול)\b', '', norm_query).strip()
    if _cook_base and _cook_base != norm_query:
        _extra = search_food(_cook_base, coach_id)
        if _extra:
            _seen = {(f.get("food_name"), f.get("food_id")) for f in foods}
            foods = foods + [f for f in _extra if (f.get("food_name"), f.get("food_id")) not in _seen]
    # רבים → יחיד: "שיבולות שועל" לא במאגר אבל "שיבולת שועל" כן. נסה וריאציות יחיד.
    if not foods:
        for _sc in _singularize_query(norm_query):
            _sf = search_food(_sc, coach_id)
            if _sf:
                norm_query = _sc
                foods = _sf
                break
    if not foods:
        _nq_words = norm_query.split()
        if _nq_words:
            foods = search_food(_nq_words[0], coach_id)

    # שגיאת הקלדה — אות כפולה ("פפרגיות"→"פרגיות"). רק כשלא נמצא כלום:
    # כווץ אותיות עוקבות זהות ונסה את כל הצינור מחדש על הצורה המתוקנת.
    if not foods:
        _collapsed = _collapse_doubled_letters(norm_query)
        if _collapsed and _collapsed != norm_query and _collapsed != query:
            _b2, _a2 = find_best_food(_collapsed, coach_id)
            if _b2 or _a2:
                return _b2, _a2

    if not foods:
        return None, []

    q_lower = norm_query.lower()
    # השוואה עמידה-לבישול: 'מבושל'/'לאחר בישול'/'אחרי בישול' נחשבים זהים בשני הצדדים.
    q_canon = _canon_cook(q_lower)
    _nc = lambda f: _canon_cook(f.get("food_name","").lower())
    exact         = next((f for f in foods if _nc(f) == q_canon), None)
    starts_list   = [f for f in foods if _nc(f).startswith(q_canon)]
    contains_list = [f for f in foods if q_canon in _nc(f)]

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
            q2 = _canon_cook(query.lower())
            exact2    = next((f for f in foods2 if _canon_cook(f.get("food_name","").lower()) == q2), None)
            starts2   = [f for f in foods2 if _canon_cook(f.get("food_name","").lower()).startswith(q2)]
            contains2 = [f for f in foods2 if q2 in _canon_cook(f.get("food_name","").lower())]
            if exact2:
                return exact2, foods2[:30]
            elif len(starts2) == 1:
                return starts2[0], foods2[:30]
            elif len(contains2) == 1:
                return contains2[0], foods2[:30]
            elif contains2:
                foods = contains2  # עדיף תוצאות מקוריות על פני תוצאות מנורמלות רעות

    # נקודה 2: ברירת מחדל "אחרי בישול" — אם השאילתה לא ציינה מצב בישול,
    # העדף גרסה מבושלת על פני "לפני בישול" (גם כשיש כמה וריאציות).
    _COOKED_MARKERS = ("מבושל", "לאחר בישול", "אפוי", "צלוי", "מטוגן")
    _RAW_MARKERS = ("לפני בישול", "יבש", " נא", "חי ")
    _query_specifies_cook = ("מבושל" in norm_query) or ("לפני בישול" in norm_query) or ("אפוי" in norm_query) or ("צלוי" in norm_query)
    if not _query_specifies_cook and contains_list:
        _is_raw = lambda f: any(m in f.get("food_name","") for m in _RAW_MARKERS)
        # התאמה ישירה ובטוחה: השם בדיוק = "{query} לאחר בישול"/"{query} מבושל"... (בסיס+סימן בלבד).
        # מכסה "פרגית"→"פרגית לאחר בישול", "חזה עוף"→"חזה עוף לאחר בישול".
        # ⚠️ אסור לבחור גרסה עם מילים נוספות ("גבינה לבנה עם בצל *מטוגן*") — זה ניחוש,
        # ובמקרה כזה מחזירים None כדי שהבוט ישאל בקבוצה (איסור מוחלט לבחור לבד — בקשת דני).
        _direct_cooked = {q_lower + s for s in (" לאחר בישול", " מבושל", " מבושלת", " אפוי", " צלוי", " מטוגן")}
        _direct = [f for f in foods if f.get("food_name","").lower() in _direct_cooked]
        # ה-best הנוכחי "לפני בישול" אבל קיימת גרסה מבושלת *בסיס+סימן* יחידה → החלף
        if best is not None and _is_raw(best) and len(_direct) == 1:
            best = _direct[0]
        # אין best — בחר רק אם יש גרסת בסיס+סימן יחידה ומובהקת (לא ניחוש בין מוצרים שונים)
        elif best is None and len(_direct) == 1:
            best = _direct[0]

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

def add_food_to_meal(user_id: str, meal_id: int, food: dict, food_row: dict, grams_override: str = None, is_addition_as_option: bool = False) -> str:
    """
    מוסיף מזון לארוחה.
    אם food_row קיים → מוסיף כ-תחליף (v2-addUserSubmealFood)
    אם food_row None → מוסיף מזון חדש (v2-addUserMealFood)
    grams_override: אם נשלח, משתמש בגרמים אלו במקום ברירת המחדל
    """
    if food_row:
        # תחליף (כאופציה / מחליף) — autofit מחשב את הגרמים השקולים אוטומטית (לפי קלוריות).
        # אנחנו שולחים gram_value כערך-ייחוס בלבד, ואז קוראים את מה ש-autofit חישב.
        actual_grams = str(grams_override or "100")
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
            # קרא את הגרמים ש-autofit חישב (subFoods[].calculated_quantity) — לא 100
            computed = None
            try:
                _m2 = get_user_meals(str(user_id))
                for _mm in _m2:
                    for _ff in (_mm.get("mealFoods") or _mm.get("new_meal_food") or []):
                        if str(_ff.get("id")) == str(food_row.get("id")):
                            _subs = [s for s in (_ff.get("subFoods") or [])
                                     if str(s.get("food_id")) == str(food.get("id"))]
                            if _subs:
                                _newest = max(_subs, key=lambda s: int(s.get("id", 0) or 0))
                                computed = _newest.get("calculated_quantity") or _newest.get("quantity")
            except Exception:
                pass
            disp = computed if computed not in (None, "", 0, "0") else actual_grams
            try:
                disp = str(int(round(float(disp))))
            except Exception:
                disp = str(disp)
            return f"✅ נוסף כתחליף ל-{food_row.get('food_name','')}: {food['food_name']}|GRAMS={disp}"
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
            # רשת ביטחון: ודא שהגרמים נכנסו בפועל — אם autofit שם measure="אחר"/יחידות
            # והכמות ריקה, כפה אותה דרך update_food_quantity (שתמיד עובד).
            _target = str(grams_override or food.get("grams") or food.get("gram_value") or "100")
            try:
                _tg = float(_target)
                _m2 = get_user_meals(str(user_id))
                for _mm in _m2:
                    if str(_mm.get("id")) == str(meal_id):
                        _matches = [f for f in (_mm.get("mealFoods") or [])
                                    if str(f.get("food_id")) == str(food.get("id"))]
                        if _matches:
                            _newest = max(_matches, key=lambda f: int(f.get("id", 0) or 0))
                            _cq = _newest.get("quantity")
                            _cqf = float(_cq) if _cq not in (None, "", "0", 0, "0.0") else 0.0
                            if abs(_cqf - _tg) > 0.5:
                                update_food_quantity(str(user_id), _newest["id"], _tg)
            except Exception:
                pass
            return f"✅ נוסף: {food['food_name']}"
        return f"❌ שגיאת API: {data.get('message','')}"


# ─── קיזוז קלורי + הוספה עם אופציות מרובות ───────────────────────────────────

# טריגרים לקיזוז קלורי — כל ביטוי שמשמעו "הורד את אותו ערך קלורי ממזון אחר".
# ה-\s*ו? בהתחלה בולע את ו' החיבור כדי שחלק-ההוספה לא יסתיים ב-"ו".
_OFFSET_TRIG = re.compile(
    r'\s*ו?\s*(?:'
    r'מקזז\w*|תקזז\w*|לקזז|קזז|בקיזוז|קיזוז'
    r'|על\s+חשבון'
    r'|מאז[נן]\w*|לאז[נן]'
    r'|(?:מוריד|מורידה|תוריד\w*|להוריד|מפחית\w*|מורד\w*)\s+(?:ל[כךוי]\s+)?(?:את\s+)?'
    r'(?:אות[םן]\s+ה?קלוריות|אותה\s+כמות(?:\s+ה?קלוריות)?'
    r'|אות[הו]\s+ה?ערך(?:\s+ה?קלורי)?|ה?ערך\s+ה?קלורי|ה?קלוריות)'
    r')\b',
    re.U)

# טריגר אופציות מרובות — "...[ו]שם/שים [ב/כ]אופציה/אופציות [גם] A [גם] B".
# דורש פועל-שימה ("שם/שים") או "אופציות" ברבים, כדי לא להתנגש עם
# המנגנון הקיים "X כאופציה ל-Y" (תחליף יחיד, ללא שימה וביחיד).
_OPTIONS_TRIG = re.compile(
    r'\s*(?:'
    r'(?:ו?(?:שים|שם|תשימ\w*|תוסיפ\w*)\s+)(?:עוד\s+)?(?:ב|כ)?(?:אופצי(?:ה|ות)|אפשרו(?:ת|יות))'
    r'|ו?ב?(?:אופציות|אפשרויות)'
    r')\b\s*(?:של\s+)?',
    re.U)

_MEAL_WORD_RE = re.compile(r'\b[במל]?ה?(בוקר|צהריי?ם|ערב|ביניים|בינים|לילה)\b', re.U)

# יחידת-קלוריות: "80 קלוריות" / "80 קל'" / "80 קק\"ל" / "80 cal".
# מומרת ל-"80 גרם" כדי לרכוב על מנגנון הגרמים; ההמרה לגרמים-אמיתיים נעשית
# בהמשך לפי היחס הקלורי של המזון הספציפי (קל'→גרם). "קל" לבד (=light) לא נתפס.
_CAL_UNIT_RE = re.compile(
    r'(\d+)\s*(?:קלורי\w*|קק["״]?ל|קל[\'"׳״]|kcal|calories?|cal)(?![א-ת])',
    re.U)


def _normalize_meal_word(w: str) -> str:
    w = re.sub(r'^[במל]?ה?', '', w or "")
    if w in ("צהרים", "צהריים"): return "צהריים"
    if w in ("בינים", "ביניים"): return "ביניים"
    return w


def _food_calories_for_grams(food: dict, grams: float) -> float:
    """כמה קלוריות יש ב-grams גרם של מזון מהמאגר (calories הוא ל-grams field, בד\"כ 100)."""
    cal_per = float(food.get("calories") or 0)
    base = float(food.get("grams") or food.get("gram_value") or 100) or 100
    return cal_per * grams / base


def _split_offset(text: str):
    """מפצל הודעת קיזוז ל-(חלק_הוספה, מזון_קיזוז, ארוחת_קיזוז) או None אם אין קיזוז."""
    m = _OFFSET_TRIG.search(text)
    if not m:
        return None
    add_part = text[:m.start()].strip()
    rest = text[m.end():].strip()
    if not add_part or not rest:
        return None
    # נקה שאריות שדבקו: "לך/לו/לה", "את זה", "אותם הקלוריות"
    rest = re.sub(r'^(?:את\s+)?(?:זה|אות[םוה]|אות[הם]\s+ה?קלוריות)\s*', '', rest).strip()
    rest = re.sub(r'^ל[כךוהי]\s+', '', rest).strip()  # "מקזז לך מהטחינה" → "מהטחינה"
    # ארוחת הקיזוז (אם צוינה)
    offset_meal = None
    om = _MEAL_WORD_RE.search(rest)
    if om:
        offset_meal = _normalize_meal_word(om.group(1))
        rest = _MEAL_WORD_RE.sub('', rest).strip()
    # מזון הקיזוז: הסר prefix "מ/מה"
    food = re.sub(r'^מ(?=[א-ת])', '', rest, count=1).strip()
    food = re.sub(r'^ה(?=[א-ת])', '', food, count=1).strip()
    food = re.sub(r'^(?:את\s+|של\s+)', '', food).strip()
    if len(re.sub(r'[\s\d.]', '', food)) < 2:
        return None
    return add_part, food, offset_meal


def _split_options(text: str):
    """מפצל הודעת הוספה-עם-אופציות ל-(חלק_הוספה, רשימת_אופציות) או None."""
    m = _OPTIONS_TRIG.search(text)
    if not m:
        return None
    add_part = text[:m.start()].strip()
    opts_part = text[m.end():].strip()
    if not add_part or not opts_part:
        return None
    raw = re.split(r'\s*(?:גם|,|\s+ו(?=[א-ת]))\s*', opts_part)
    opts = []
    for o in raw:
        o = _MEAL_WORD_RE.sub('', (o or "").strip()).strip()
        if o and o != 'גם' and len(re.sub(r'[\s\d.]', '', o)) >= 2:
            opts.append(o)
    if not opts:
        return None
    return add_part, opts


def _find_added_meal_food(user_id: str, meal_id, food_id):
    """מוצא את רשומת ה-meal_food החדשה ביותר של food_id בארוחה (אחרי הוספה)."""
    for m in get_user_meals(str(user_id)):
        if str(m.get("id")) == str(meal_id):
            matches = [f for f in (m.get("mealFoods") or m.get("new_meal_food") or [])
                       if str(f.get("food_id")) == str(food_id)]
            if matches:
                return max(matches, key=lambda f: int(f.get("id", 0) or 0))
    return None


def _meal_full_name(meal: str, default: str = "ארוחת ערב") -> str:
    if not meal:
        return default
    return meal if "ארוחת" in meal else f"ארוחת {meal}"


def handle_calorie_offset(user_id: str, full_name: str, all_meals: list, coach_id: str,
                          new_food_query: str, grams, add_meal: str,
                          offset_food: str, offset_meal: str) -> str:
    """מוסיף מזון חדש ומקזז את הקלוריות שלו (בגרמים שקולים) ממזון אחר."""
    if not grams:
        return f"❓ לקיזוז צריך לציין כמה גרם של *{new_food_query}* להוסיף (למשל: 70 גרם)."
    best, _alts = find_best_food(new_food_query, coach_id)
    if not best:
        return f"❓ לא מצאתי את המזון '{new_food_query}' במאגר לקיזוז."

    add_meal_full = _meal_full_name(add_meal)
    meal_id, _r, err = find_meal_and_food(all_meals, add_meal_full, "")
    if not meal_id:
        return err or f"❌ לא מצאתי את {add_meal_full} של {full_name}"

    add_res = add_food_to_meal(user_id, meal_id, best, None, grams_override=str(int(round(float(grams)))))
    if not add_res.startswith("✅"):
        return add_res
    cal_added = _food_calories_for_grams(best, float(grams))
    g_added = int(round(float(grams)))

    # מזון הקיזוז — אותה ארוחה אלא אם צוינה אחרת
    off_meal_full = _meal_full_name(offset_meal, default=add_meal_full)
    fresh = get_user_meals(user_id)
    _id2, off_row, _err2 = find_meal_and_food(fresh, off_meal_full, offset_food)
    base_line = f"➕ {g_added}ג' {best['food_name']} = {int(round(cal_added))} קל' ב{add_meal_full}"
    if not off_row:
        return (f"✅ *{full_name}*:\n{base_line}\n"
                f"⚠️ לא מצאתי '{offset_food}' ב{off_meal_full} — הוספתי בלי לקזז.")

    curr_cal = float(off_row.get("calories") or 0)
    curr_q = float(off_row.get("quantity") or off_row.get("quantity_to_calculate") or 0)
    if curr_cal <= 0 or curr_q <= 0:
        return (f"✅ *{full_name}*:\n{base_line}\n"
                f"⚠️ ל-'{off_row.get('food_name','')}' אין נתוני קלוריות/כמות — לא קיזזתי.")

    reduce_q = cal_added * curr_q / curr_cal
    new_q = max(0.0, curr_q - reduce_q)
    cal_offset_actual = min(cal_added, curr_cal)
    upd = update_food_quantity(user_id, off_row["id"], new_q)
    if not upd.get("status"):
        return (f"✅ *{full_name}*:\n{base_line}\n"
                f"❌ שגיאה בהפחתת {off_row['food_name']}: {upd.get('message','')}")

    msg = (f"✅ *{full_name}* — קיזוז ב{add_meal_full}:\n"
           f"{base_line}\n"
           f"➖ {off_row['food_name']}: {int(round(curr_q))}→{int(round(new_q))}ג' "
           f"(קוזזו {int(round(cal_offset_actual))} קל')")
    if cal_added > curr_cal + 0.5:
        msg += (f"\n⚠️ {off_row['food_name']} הגיע ל-0 — "
                f"קוזזו רק {int(round(curr_cal))} מתוך {int(round(cal_added))} קל'.")
    return msg


def handle_calorie_adjust(user_id: str, full_name: str, all_meals: list,
                          parsed: dict, meals_list: list, request_text: str) -> str:
    """מוסיף/מוריד N קלוריות ממזון *קיים* בארוחה ע\"י המרה לגרמים לפי היחס הקלורי
    של אותו המזון בתפריט (calories/quantity). דוגמה: 'מוריד לך 80 קלוריות מהפיתה בבוקר'."""
    reduce = bool(parsed.get("reduce"))
    # שם המזון + כמות הקלוריות מתוך ה-change (כבר עבר המרת קלוריות→גרם בכניסה)
    _ch = (parsed.get("ops") or [{}])[0].get("change") or parsed.get("change", "")
    _pm = re.search(r'\(([^)]+)\)', _ch)
    food_raw = (_pm.group(1) if _pm else _ch).strip()

    # כמות הקלוריות: extra_grams (חולץ בפרסר) או מתוך ה-change ("80 גרם")
    cal_amount = parsed.get("extra_grams")
    _gm = re.search(r'(\d+)\s*גרם', food_raw) or re.search(r'(\d+)', food_raw)
    if not cal_amount and _gm:
        cal_amount = _gm.group(1)
    if not cal_amount:
        return "❓ לא הבנתי כמה קלוריות. דוגמה: *מוריד לך 80 קלוריות מהפיתה בבוקר*"
    cal_amount = float(cal_amount)

    # נקה שם מזון: הסר מספר/גרם, פעלים, ושאריות-ארוחה שדלפו (כולל ש.כתיב 'ארחות')
    fq = re.sub(r'\d+\s*גרם', '', food_raw)
    fq = re.sub(r'\b(?:הוסף|הפחת|העלה|מוסיף|מוריד|תוסיפ\w*|תורידי?)\b', '', fq)
    fq = re.sub(r'\s*[במל]?ה?אר[ו]?חו?ת\b', ' ', fq)  # "בארוחת"/"בארחות"/"מארוחת"
    fq = _MEAL_WORD_RE.sub(' ', fq)                    # שמות ארוחות שנשארו
    fq = re.sub(r'^מה(?=[א-ת])', '', fq.strip()).strip()
    fq = re.sub(r'\s+', ' ', fq).strip()
    fq = normalize_food_query(fq)
    if len(re.sub(r'[\s\d.]', '', fq)) < 2:
        return "❓ לא ציינת מאיזה מזון. דוגמה: *מוריד לך 80 קלוריות מהפיתה בבוקר*"

    meal = meals_list[0] if meals_list else "ערב"
    meal_full = _meal_full_name(meal)

    def _ok(q, row):
        # התאמה אמיתית בלבד: כל מילות השאילתה מוכלות בשם המזון. מונע fuzzy שגוי
        # שמסכן מזון קיים (למשל "פיצה" שמתאים ל-"ביצה" בהבדל אות אחת).
        if not row:
            return False
        fn = normalize_food_query(row.get("food_name", ""))
        qw = normalize_food_query(q.lstrip('בל')).split()
        return bool(qw) and all(w in fn for w in qw)

    def _locate(q):
        """מצא רשומת מזון קיימת. (meal_name, row) או (None, None)."""
        _id, row, _err = find_meal_and_food(all_meals, meal_full, q)
        if _ok(q, row):
            return meal_full, row
        # קידומת ב'/ל' מקום ("באורז"→"אורז") — רק אם הגרסה המקוצרת נמצאת
        if re.match(r'^[בל][א-ת]{3,}', q):
            _id2, row2, _e2 = find_meal_and_food(all_meals, meal_full, q[1:])
            if _ok(q[1:], row2):
                return meal_full, row2
        # לא נמצא בארוחה הנתונה — סרוק את כל הארוחות (אם נמצא ביחידה אחת)
        qw = normalize_food_query(q.lstrip('בל')).split()
        cand = []
        for m in all_meals:
            for f in (m.get("mealFoods") or m.get("new_meal_food") or []):
                fn = normalize_food_query(f.get("food_name", ""))
                if qw and all(w in fn for w in qw):
                    cand.append((m.get("meal_name", "").strip(), f))
        return cand[0] if len(cand) == 1 else (None, None)

    found_meal, row = _locate(fq)
    if not row:
        avail = [f.get("food_name", "") for m in all_meals
                 if meal_full.replace("ארוחת ", "") in m.get("meal_name", "")
                 for f in (m.get("mealFoods") or [])]
        extra = f"\nמזונות ב{meal_full}: {', '.join(avail)}" if avail else ""
        return f"❌ לא מצאתי '{fq}' ב{meal_full} של {full_name}.{extra}"

    fname = row.get("food_name", "")
    curr_q = float(row.get("quantity") or row.get("quantity_to_calculate") or 0)
    curr_cal = float(row.get("calories") or 0)
    # יחידת המידה של המזון: ברירת מחדל תמיד גרמים; מזון שמסומן "unit" → יחידות.
    _measure = (row.get("measure") or "grams").strip().lower()
    _is_unit = _measure == "unit"
    _ulabel = "יח'" if _is_unit else "גרם"
    def _fq(x):  # גרמים → שלם; יחידות → עד ספרה עשרונית אחת (2→"2", 1.5→"1.5")
        return f"{x:.1f}".rstrip("0").rstrip(".") if _is_unit else f"{int(round(x))}"
    if curr_q <= 0 or curr_cal <= 0:
        return (f"⚠️ ל-*{fname}* ({found_meal}) אין נתוני קלוריות/כמות — "
                f"לא יכולתי להמיר {int(cal_amount)} קל'.")

    # ההמרה זהה לכל מידה: delta בכמות = קל' × (כמות נוכחית / קל' נוכחי).
    # עבור גרמים → גרמים, עבור יחידות → יחידות (curr_cal תואם ל-curr_q ביחידות שלו).
    q_delta = cal_amount * curr_q / curr_cal
    if reduce:
        new_q = max(0.0, curr_q - q_delta)
        cal_actual = min(cal_amount, curr_cal)
        new_cal = max(0.0, curr_cal - cal_amount)
        verb, arrow, sign = "הופחת", "↓", "−"
    else:
        new_q = curr_q + q_delta
        cal_actual = cal_amount
        new_cal = curr_cal + cal_amount
        verb, arrow, sign = "עודכן", "↑", "+"

    upd = update_food_quantity(user_id, row["id"], new_q)
    if not upd.get("status"):
        return f"❌ שגיאת עדכון של {fname}: {upd.get('message','')}"

    msg = (f"✅ {verb}: *{fname}* ב{found_meal} של {full_name}\n"
           f"{sign}{int(round(cal_actual))} קל' → {_fq(curr_q)}{arrow}{_fq(new_q)} {_ulabel} "
           f"({int(round(curr_cal))}→{int(round(new_cal))} קל')")
    if reduce and cal_amount > curr_cal + 0.5:
        msg += (f"\n⚠️ {fname} הגיע ל-0 — הופחתו רק {int(round(curr_cal))} "
                f"מתוך {int(round(cal_amount))} קל'.")
    elif reduce and curr_q > 0 and 0 < new_q < curr_q * 0.5:
        # נקודה 4: אזהרה בולטת על הורדה גדולה (מתחת ל-50% מהכמות המקורית)
        _pct = int(round((1 - new_q / curr_q) * 100))
        msg += (f"\n⚠️ שים לב: הורדה גדולה — *{fname}* ירד ב-{_pct}% "
                f"({_fq(curr_q)}→{_fq(new_q)} {_ulabel}). אם זו טעות שלח שוב עם פחות קלוריות.")
    return msg


def handle_multi_options(user_id: str, full_name: str, all_meals: list, coach_id: str,
                         main_food_query: str, grams, add_meal: str, options: list) -> str:
    """מוסיף מזון ראשי + אופציות (תחליפים; auto-fit מחשב גרמים שקולי-קלוריות)."""
    best, _alts = find_best_food(main_food_query, coach_id)
    if not best:
        return f"❓ לא מצאתי את המזון '{main_food_query}' במאגר."
    add_meal_full = _meal_full_name(add_meal)
    meal_id, _r, err = find_meal_and_food(all_meals, add_meal_full, "")
    if not meal_id:
        return err or f"❌ לא מצאתי את {add_meal_full} של {full_name}"

    g_override = str(int(round(float(grams)))) if grams else None
    add_res = add_food_to_meal(user_id, meal_id, best, None, grams_override=g_override)
    if not add_res.startswith("✅"):
        return add_res
    main_row = _find_added_meal_food(user_id, meal_id, best["id"])
    if not main_row:
        return f"✅ הוספתי {best['food_name']} ב{add_meal_full}, אך לא הצלחתי לצרף אופציות."

    added, missing = [], []
    for opt_q in options:
        opt_best, _o = find_best_food(normalize_food_query(opt_q), coach_id)
        if not opt_best:
            missing.append(opt_q); continue
        sub_res = add_food_to_meal(user_id, meal_id, opt_best, main_row)
        if sub_res.startswith("✅"):
            added.append(opt_best["food_name"])
        else:
            missing.append(opt_q)

    gtxt = f"{int(round(float(grams)))}ג' " if grams else ""
    msg = f"✅ *{full_name}* — ב{add_meal_full}:\n➕ {gtxt}{best['food_name']}"
    if added:
        msg += f"\n🔄 אופציות (שקולות בקלוריות): {', '.join(added)}"
    if missing:
        msg += f"\n⚠️ לא נמצאו במאגר: {', '.join(missing)}"
    return msg


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
    text = re.sub(r'כאופצי(?:יה|ה|ות|יות)?\s+במקום', 'במקום', text)  # "טונה כאופציה במקום X" → "טונה במקום X"
    text = re.sub(r'באופצי(?:יה|ה|ות|יות)?\s+של', 'במקום', text)
    text = re.sub(r'כאופצי(?:יה|ה|ות|יות)?\s+(?:ל(?=[א-ת]{3,})|של)', 'במקום ', text)
    # "X לאופציה של/ל Y" → "X במקום Y" (דני כותב "פרגית לאופציה של החזה עוף").
    # חייב לרוץ לפני הסרת "אופציה של" למטה, אחרת ה-ל' נשארת תלושה ("להחזה").
    text = re.sub(r'לאופצי(?:יה|ה|ות|יות)?\s+(?:של|ל(?=[א-ת]{3,}))', 'במקום ', text)
    text = re.sub(r'כאופציות\b', '', text)  # כאופציות ללא ל(קצר)/של = מחיקה
    text = re.sub(r'אופציה\s+של\s+', '', text)  # "אופציה של X במקום Y" → "X במקום Y"
    # שארית "כאופציה"/"כאופצייה" תלושה (לא לפני ל/של/במקום) → הסר, שלא תידבק לשם המזון
    text = re.sub(r'\s*כאופצי(?:יה|ה|ות|יות)(?!\s*(?:ל|של|במקום))(?![א-ת])', '', text)
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

    # ו- / ו / וגם לפני ספרה — מחבר מולטי-מזון ("X ו-30 גרם Y" / "X וגם 30 גרם Y")
    raw = re.split(r'\s+ו-?גם\s+|\s+ו-?\s*(?=\d)', text.strip())
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
    # "יחידה/יחידת <מזון>" → "1 <מזון>"; "N יחידות" → "N". כמות *ביחידות* (לא גרם!) —
    # למזון שמסומן unit (לחם/ביצה), reduce מפחית מהכמות-ביחידות. "מוריד יחידה לחם" → "מוריד 1 לחם".
    text = re.sub(r'(?<![א-ת0-9])(\d+)\s*יחיד(?:ות|ה|ת)(?![א-ת])', r'\1', text)
    text = re.sub(r'(?<![א-ת0-9])יחיד[הת](?:\s+של)?(?![א-ת])', '1', text)  # "יחידה של לחם" → "1 לחם"
    return text


# ── נרמול יחידות מידה → גרמים (לפני הפרסור) ─────────────────────────────────
_HEB_NUM = {'אחת':1,'אחד':1,'שתי':2,'שתיים':2,'שני':2,'שניים':2,
            'שלוש':3,'שלושה':3,'ארבע':4,'ארבעה':4,'חמש':5,'חמישה':5,
            'שש':6,'שישה':6,'שבע':7,'שבעה':7,'שמונה':8,'תשע':9,'עשר':10,'עשרה':10}
_MOD_UNITS = {'כפות':15,'כף':15,'כפיות':5,'כפית':5,'כוסות':200,'כוס':200,
              'פרוסות':25,'פרוסה':25,'פרוסת':25}

def _num_token(tok):
    return int(tok) if tok.isdigit() else _HEB_NUM.get(tok)

def _normalize_units(text: str) -> str:
    """ממיר יחידות מידה לגרמים: עשרוני, קילו, ביצים, כפות/כפית/כוס/פרוסה."""
    # 1) גרמים עשרוניים: "12.5 גרם" → "13 גרם" (autofit שולח שלם ממילא)
    text = re.sub(r'(\d+\.\d+)\s*גרם', lambda m: f"{int(round(float(m.group(1))))} גרם", text)
    # 2) קילו: חצי/רבע + "N קילו / ק\"ג"
    text = re.sub(r'\bחצי\s+ק(?:ילו|["\u05F3\u05F4]?ג)\b', '500 גרם', text)
    text = re.sub(r'\bרבע\s+ק(?:ילו|["\u05F3\u05F4]?ג)\b', '250 גרם', text)
    text = re.sub(r'(\d+(?:\.\d+)?)\s*ק(?:["\u05F3\u05F4]?ג|ילו)\b',
                  lambda m: f"{int(round(float(m.group(1))*1000))} גרם", text)
    text = re.sub(r'(?<![\d.\u05D0-\u05EA])קילו\b(?!\s*גרם)', '1000 גרם', text)  # "קילו" לבד
    _num_word = r'(?:\d+|' + '|'.join(_HEB_NUM) + r')'
    # 3) ביצים: "N ביצים/ביצה" → "N*55 גרם ביצה" (היחידה היא המזון)
    def _eggs(m):
        n = _num_token(m.group(1))
        return f"{int(round(n*55))} גרם ביצה" if n else m.group(0)
    text = re.sub(rf'\b({_num_word})\s+(?:ביצים|ביצה|ביצת)\b', _eggs, text)
    # 4) כף/כפית/כוס/פרוסה: "N <יחידה> [של] <מזון>" → "N*g גרם <מזון>" (lookahead שומר על המזון)
    _mod_pat = '|'.join(sorted(_MOD_UNITS, key=len, reverse=True))
    def _mod(m):
        n = _num_token(m.group(1))
        return f"{int(round(n*_MOD_UNITS[m.group(2)]))} גרם " if n else m.group(0)
    text = re.sub(rf'\b({_num_word})\s+({_mod_pat})\s+(?:של\s+)?(?=[\u05D0-\u05EA])', _mod, text)
    # 4b) חצי/רבע + יחידה: "חצי כוס אורז" → "100 גרם אורז"
    def _frac_mod(m):
        frac = {'חצי': 0.5, 'רבע': 0.25}[m.group(1)]
        return f"{int(round(frac*_MOD_UNITS[m.group(2)]))} גרם "
    text = re.sub(rf'\b(חצי|רבע)\s+({_mod_pat})\s+(?:של\s+)?(?=[\u05D0-\u05EA])', _frac_mod, text)
    # 4c) יחידה בודדת ללא מספר = 1: "כף שמן זית" → "15 גרם שמן זית"
    def _mod1(m):
        return f"{_MOD_UNITS[m.group(1)]} גרם "
    text = re.sub(rf'(?<![\u05D0-\u05EA\d.])({_mod_pat})\s+(?:של\s+)?(?=[\u05D0-\u05EA])', _mod1, text)
    return text


# מילות אישור/שיחה — לעולם לא מאכל ("כן"/"לא"/"אוקיי" שדני עונה ללקוח). מונע "נוסף: כן".
_NOT_FOOD_WORDS = frozenset({"כן","לא","אוקיי","אוקייי","אוקי","סבבה","בטח","יאללה","יאלה",
    "מעולה","פגז","אחי","תודה","ביי","טוב","יופי","מצוין","סגור","בסדר","ברור","נכון","בדיוק",
    "ok","אהלן","וואלה","חחח","💪","👍"})

def _wrap_change_foods(val: str) -> str:
    """עוטף את שם המזון בפקודת 'הוסף/החלף X [במקום Y]' בסוגריים — כך שמנגנון פיצול
    ה-multi-food (שדורש 'הוסף (...)') יזהה כמה מאכלים מופרדים ב-, / ו / +.
    לדוגמה: 'הוסף שיבולת שועל,פסטרמה,פסטה' → 'הוסף (שיבולת שועל,פסטרמה,פסטה)'.
    אם המזון כבר עטוף בסוגריים — לא נוגע. רק הוספה/החלפה (לא הפחתה). 'במקום' נשמר."""
    m = re.match(r'^(הוסף|החלף|תוסיף|הוסיף|תחליף|החליף|תוסיפי|הוסיפי|תחליפי)\s+(.+)$', val.strip())
    if not m:
        return val
    verb, rest = m.group(1), m.group(2).strip()
    if '(' in rest:  # כבר עטוף בסוגריים — אל תיגע
        return val
    mm = re.match(r'^(.+?)\s+במקום(?:\s+של)?\s+(.+)$', rest)
    if mm:
        return f"{verb} ({mm.group(1).strip()}) במקום ({mm.group(2).strip()})"
    return f"{verb} ({rest})"

def parse_message(text: str, skip_name: bool = False) -> dict:
    """
    מנסה לפרסר הודעה — גם בתבנית מובנית וגם בשפה חופשית.
    מחזיר dict עם: name, meal, change, confidence (0-100).
    """
    result = {}
    conf = 100

    # ── pre-process: פקודות v3 ("מוסיף לך/לו") → פורמט שהפרסר מבין ───────────
    # ל[כךו]: לך (ך = כ סופית 0x5da) + לו — שתי הצורות
    # נרמול כלי (לא V3-ספציפי) — לפני הגדרת _text_before_v3
    text = re.sub(r'(\d+)\s*גר(?![\u05D0-\u05EA])', r'\1 גרם', text)
    # ל' דבוקה למספר שברה את הפענוח: "לחם ל100 גרם" → "לחם ל 100 גרם".
    # שומרים את ה-ל' (set-to) ורק מוסיפים רווח, אחרת זה הופך לתוספת
    # (66+100=166 במקום קביעה ל-100). ממוקד: רק כשאחרי המספר בא "גרם".
    text = re.sub(r'ל-?(\d+)\s*גרם', r'ל \1 גרם', text)
    text = re.sub(r'^אני\s+', '', text)  # 'אני מוסיף לך' → 'מוסיף לך'
    # מילת-אישור בודדת בשורה ראשונה ("כן\nמוסיף לך...") = תשובה ללקוח → הסר
    text = re.sub(r'^\s*(?:כן|לא|אוקיי?|סבבה|בטח|יאללה|יאלה|מעולה|פגז|תודה|טוב|יופי|מצוין|סגור|בסדר|ברור|נכון)\s*\n', '', text)
    # "מאכל/מזון/מוצר חדש" = הערה של דני, לא חלק משם המזון ("ריבה מאכל חדש" → "ריבה")
    text = re.sub(r'\bמ(?:זון|אכל|וצר)\s+חדש\b', '', text)
    # שם-פנייה מוביל ("ליה הורדתי לך 30 גרם" / "אילן אני מוסיף לך") — ב-skip_name השם
    # מגיע מהטלפון, אז מילה מובילה לפני פועל-פקודה היא פנייה ללקוח, לא חלק מהמזון → הסר.
    if skip_name:
        _vcmd = (r'(?:מוסי[פף]|תוסי[פף]|אוסי[פף]|נוסי[פף]|מוריד|תוריד|הורד\w*|הוספ\w*'
                 r'|מעלה|תעלה|העל\w*|מחלי[פף]|תחלי[פף]|החלפ\w*|מפחי\w*|הפח\w*'
                 r'|מכניס|תכניס|הכנס\w*|משנה|שינ\w*)')
        text = re.sub(rf'^\s*[\u05D0-\u05EA]{{2,12}}\s+(?=(?:אני\s+)?{_vcmd}(?![\u05D0-\u05EA]))',
                      '', text, count=1)
        text = re.sub(r'^אני\s+', '', text)
    text = _normalize_units(text)  # יחידות → גרמים (ביצים/כפות/קילו/עשרוני)
    # ── קיזוז קלורי + אופציות מרובות — זהה וחתוך לפני נרמול V3 ─────────────
    # (חיוני: 'מוריד' שב-V3 הופך ל'הפחת' היה שובר את זיהוי הקיזוז)
    _off = _split_offset(text)
    if _off:
        _add_part, result["offset_food"], result["offset_meal"] = _off
        # ארוחה משותפת: אם חלק ההוספה לא ציין ארוחה אבל הקיזוז כן ("...ומקזז מהטחינה
        # בצהריים") → אותה ארוחה חלה על שניהם. ארוחות נפרדות נשמרות אם ההוספה כבר ציינה.
        if result["offset_meal"] and not _MEAL_WORD_RE.search(_add_part):
            _add_part = f"{_add_part} ב{result['offset_meal']}"
        text = _add_part
    else:
        _opts = _split_options(text)
        if _opts:
            text, result["options"] = _opts
    _text_before_v3 = text  # שמור לפני נרמולי V3 בלבד
    text = re.sub(r'מוסיף\s+ל[כךו]', 'הוסף', text)
    text = re.sub(r'(?:מכניס|תכניס)\s+ל[כךו]', 'הוסף', text)  # "אני מכניס לך X" = הוסף
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
    # עבר הוספה: "שמתי לך", "הוספתי לך"
    text = re.sub(r'(?:שמתי|הוספתי)\s+ל[כךוי](?![\u05D0-\u05EA])', 'הוסף', text)
    text = re.sub(r'הכנסתי\s+ל[כךוי](?![\u05D0-\u05EA])', 'הוסף', text)  # "הכנסתי לך X" = הוסף
    # עבר הפחתה: "הורדתי לך"
    text = re.sub(r'הורדתי\s+ל[כךוי](?![\u05D0-\u05EA])', 'הפחת', text)
    # עבר החלפה: "החלפתי לך X ב-Y"
    _t3 = text.replace('לאחר בישול', 'לאחר׳בישול').replace('לפני בישול', 'לפני׳בישול')
    _t3 = re.sub(
        r'החלפתי\s+ל[כךוי]\s+(.+?)\s+ב-?([^\n]+)',
        lambda m: f'הוסף {m.group(2).strip().lstrip("-")} במקום {m.group(1).strip()}',
        _t3
    )
    text = _t3.replace('׳', ' ')
    text = re.sub(r'החלפתי\s+ל[כךוי]', 'הוסף', text)
    # "משנה לך X ב-Y" = תחליף
    _t4 = text.replace('לאחר בישול', 'לאחר׳בישול').replace('לפני בישול', 'לפני׳בישול')
    _t4 = re.sub(
        r'משנה\s+ל[כךוי]\s+(.+?)\s+ב-?([^\n]+)',
        lambda m: f'הוסף {m.group(2).strip().lstrip("-")} במקום {m.group(1).strip()}',
        _t4
    )
    text = _t4.replace('׳', ' ')
    text = re.sub(r'משנה\s+ל[כךוי]', 'הוסף', text)
    # "אז" מילת-קישור אחרי פועל הפקודה ("מוסיף לך אז מנגו..." → "הוסף אז מנגו...")
    # מילת מילוי שדלפה לשם המזון ושברה את פיצול-הרשימה. להסיר (גם "אז גם").
    text = re.sub(r'(הוסף|הפחת|העלה)\s+אז(?:\s+גם)?\s+', r'\1 ', text)
    _v3_triggered = (text != _text_before_v3)
    # "קצת/מעט" בלי גרמים → default 50 גרם להוריד
    text = re.sub(r'(?:קצת|מעט)\s+(?:מה?\s*)?(?=[א-ת])', '50 גרם ', text)
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
            result["change"] = _wrap_change_foods(val)
        elif key in ("החלף", "הוסף", "תחליף"):
            result["change"] = _wrap_change_foods(f"{key} {val}")
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
                _is_option = bool(re.search(r'כאופציה\s+(?:ל|של)', v))
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
                    m_l = re.search(rf'^(.+?)\s+ל(?!(?:{_PROTECT_L_SUFFIX})(?![א-ת]))([\u05D0-\u05EA].+)$', v)
                    if m_l:
                        nf, ht = m_l.group(1), m_l.group(2)
                    else:
                        nf, ht = v, ""
                _cl = lambda s: re.sub(r'[.\s:,]+$', '', s).strip()
                change = f"הוסף ({_cl(nf)}) במקום ({_cl(ht)})" if _cl(ht) else f"הוסף ({_cl(nf)})"
                op_dict = {"change": change, "meal": op_meal, "extra_grams": extra_grams, "as_option": _is_option}
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
    _REDUCE_PAT = r'(?<![א-ת])(?:[שוב]?)(?:להוריד|להפחית|הורדתי|הורדת|הפחתתי|הפחתת|חי?סרתי|הורד|הפחת|תוריד|תורידי|הפחיתי|הפחית|תפחית|תפחיתי|הורידי|הוריד|תחסר[יי]?|לחסר)(?![א-ת])'
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
    # [וV]? בולע 'ו' מחבר: 'ובצהריים' → נמחק במלואו (לא משאיר 'ו' תלוש בשם המזון)
    _meal_re = r'[וV]?[בלמ](?:ארוחת\s+)?ה?(?:' + '|'.join(_MEAL_MAP_FT.keys()) + r')\b'
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
    # "VERB כאופציה לNAME FOOD" (V2: שם אחרי "כאופציה ל") → "VERB לNAME FOOD"
    # חייב לפני "כאופציה ל→במקום" כדי שהשם לא יהפוך ל"במקום קובי"
    if not _v3_triggered and not skip_name:
        _opt_nm = re.match(
            r'^(.+?)\s+כאופצי(?:ה|ות)?\s+ל([א-ת׳\']{2,8}'
            r'(?:\s+[א-ת׳\']{2,8})?)\ +(.+)$',
            full_no_meal, re.UNICODE
        )
        if _opt_nm:
            full_no_meal = f"{_opt_nm.group(1)} ל{_opt_nm.group(2)} {_opt_nm.group(3)}"
            full_no_meal = re.sub(r'\s+', ' ', full_no_meal).strip()
    full_no_meal = re.sub(r'כאופצי(?:ה|ות)?\s+(?:ל(?=[א-ת]{3,})|של)\s*', 'במקום ', full_no_meal)
    full_no_meal = re.sub(r'באופצי(?:ה|ות)?\s+של\s*ה?', 'במקום ', full_no_meal)  # V3: "באופציה של X"
    full_no_meal = re.sub(r'לאופצי(?:ה|ות)?\s+(?:של|ל(?=[א-ת]{3,}))\s*', 'במקום ', full_no_meal)  # "X לאופציה של/ל Y" → "X במקום Y" (דני)
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
                    'וסיפי', 'פחת', 'יפי', 'יופי', 'יפה', 'במקום',
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
    # "VERB גם ..." → "גם" כאן filler (הוספה רגילה) — הסר אותו
    clean_full = re.sub(r'^\s*(' + _VERB_PAT + r')\s+גם\s+', r'\1 ', clean_full)
    # "FOOD גם FOOD" (גם בין שני מזונות) → "FOOD וגם FOOD" כדי שיישמר כמפריד
    clean_full = re.sub(r'([א-ת])\s+גם\s+(?=[א-ת])', r'\1 וגם ', clean_full)
    clean_full = re.sub(r'\bגם\s+', '', clean_full)             # שאר filler: "גם 50 גרם שקדים" → "50 גרם שקדים" (לא נוגע ב-וגם)
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
                ml = re.search(rf'^(.+?)\s+ל(?!(?:{_PROTECT_L_SUFFIX})(?![א-ת]))([א-ת].+)$', fb)
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
        # V3: שלח טקסט מקורי (לפני נרמול) — ה-prompt של AI מצפה לפורמט "מוסיף לך..."
        _ai_text = _text_before_v3 if _v3_triggered else text
        _ai = _parse_with_ai(_ai_text, v3_mode=_v3_triggered)
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


# פעלי-פעולה לפיצול ריבוי-משימות. [פף] תופס גם אות סופית (תוסיף=ף),
# וכולל לשון הווה (מוסיף/מחליף) שדני משתמש בה — לא רק ציווי.
_MULTI_VERBS = (r'(?:'
                r'מוסי[פף]|תוסי[פף]י?|הוסי[פף]י?|הוסף|אוסי[פף]|נוסי[פף]'   # הוספה
                r'|מחלי[פף]|תחלי[פף]י?|החלי[פף]י?|החלף'                     # החלפה
                r'|מעלה|תעל[הי]י?|מגדיל|תגדיל[יי]?'                          # העלאה/הגדלה
                r'|מוריד|תוריד[יי]?|הורד[יי]?|מפחית|תפחית|הפחת[יי]?|תחסר[יי]?'  # הורדה
                r'|משנה|תשנ[הי]|מעדכן|תעדכן|עדכן'                            # שינוי/עדכון
                r'|שים|תשים|מכניס|תכניס|הכנס'                                # שימה/הכנסה
                r')')

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


# מפרידים בין כמה מזונות חדשים: פסיק / + / ו(גם) / או / גם.
# לא כולל "עם" (לרוב מנה אחת: "סלט עם עגבניות") ולא "בנוסף ל" (מטופל כ-hint).
_FOOD_SEP_RE = r'\s*,\s*|\s*\+\s*|\s*/\s*|\s+ו-?גם\s+|\s+ו(?:\s+|(?=[א-ת]))|\s+גם\s+|\s+או\s+'

def _is_exact_food(token: str, coach_id: str = "") -> bool:
    """האם token הוא שם מזון מדויק במאגר (עמיד-לבישול). לשימוש ב-tokenization חמדני.
    תומך גם באליאס וברבים→יחיד ('שיבולות שועל' = 'שיבולת שועל') כדי שפיצול מולטי-מזון
    לא ייכשל בגלל צורת רבים."""
    token = token.strip()
    if not token:
        return False
    def _hit(t):
        qc = _canon_cook(t.lower())
        return any(_canon_cook(f.get("food_name", "").lower()) == qc for f in search_food(t, coach_id))
    if _hit(token):
        return True
    alias = _FOOD_ALIASES.get(token)
    if alias and _hit(alias):
        return True
    return any(_hit(c) for c in _singularize_query(token))

def _split_multi_new_food(new_foods_str: str, coach_id: str = ""):
    """מפצל מחרוזת מזון חדש שמחברת כמה מזונות לרשימה.
    1. מפריד 'חזק' (פסיק / + / וגם / גם / או) = כוונת-רשימה מפורשת → מפצל תמיד,
       גם אם חלק לא נמצא במאגר (כדי לשאול עליו — דיווח חלקי).
    2. אחרת (רווחים + 'ו' בלבד, גם בלי פסיקים) → tokenization חמדני לפי המאגר:
       מזהה את הצירוף הארוך ביותר שהוא מזון ('חזה עוף', 'תפוח אדמה'), ומפצל רק אם כל
       הטוקנים הם מזון מדויק — שומר 'יוגורט וניל'/'אורז מלא' שלמים (מילה לא-מזון → לא מפצל)."""
    s = (new_foods_str or "").strip()
    if not s:
        return []
    # ספרת-גרמים בלבד מבטלת פיצול (מטופל בפיצול הגרמים). ספרה שהיא חלק משם מזון
    # ("קוטג 5%", "גבינה לבנה 3%") לא צריכה לבטל פיצול-רשימה מפורש.
    _has_grams = bool(re.search(r'\d+\s*(?:גרם|גר(?![א-ת])|ג(?![א-ת]))', s))
    # מפריד חזק = רשימה מפורשת → פצל גם אם חלק אינו מזון (יישאל עליו בדיווח חלקי),
    # וגם אם יש אחוז/ספרה בתוך שם מזון — כל עוד אין ספרת-גרמים.
    if not _has_grams and re.search(r'[,+/]|\sו-?גם\s|\sגם\s|\sאו\s', s):
        parts = [p.strip() for p in re.split(_FOOD_SEP_RE, s) if p.strip()]
        return parts if len(parts) >= 2 else [s]
    if re.search(r'\d', s):  # מספרים/גרמים → מטופלים בפיצול הגרמים הנפרד
        return [s]
    # רווחים + 'ו' — חמדני: פצל רק אם כל הטוקנים מזון מדויק
    words = re.sub(_FOOD_SEP_RE, ' ', s).split()  # 'ו' צמוד → רווח
    if len(words) < 2:
        return [s]
    tokens, i = [], 0
    while i < len(words):
        matched = None
        for j in range(min(len(words), i + 4), i, -1):  # הצירוף הארוך ביותר שהוא מזון
            cand = " ".join(words[i:j])
            if _is_exact_food(cand, coach_id):
                matched = (cand, j)
                break
        if not matched:
            return [s]  # מילה שאינה מזון → כנראה שם רב-מילי/טעות → אל תפצל
        tokens.append(matched[0])
        i = matched[1]
    return tokens if len(tokens) >= 2 else [s]


def _expand_plus_ops(line: str) -> str:
    """מפצל פקודה עם כמה פריטים באותה שורה ב-"+"/"," ("מוריד לך יחידה של לחם בביניים + 10 גרם
    חמאת בוטנים") לשתי פקודות נפרדות עם אותו פועל+ארוחה. מפצל *רק* אם אחרי המפריד יש כמות
    (ספרה/"יחיד") — כך "צהריים+ערב" (רשימת-ארוחות) לא נפגע."""
    m = re.match(r'^\s*((?:אז\s+|ו?אני\s+)?(?:מוסי[פף]\w*|תוסי[פף]\w*|מורי[ד]\w*|תורי[ד]\w*|מפחית\w*|מחלי[פף]\w*|מעלה|תעלה|מכני[ס]\w*)\s+ל[ךו]\s+)(.+)$', line)
    if not m:
        return line
    verb, body = m.group(1), m.group(2)
    if not re.search(r'[+,]\s*(?:\d|יחיד)', body):
        return line
    mm = re.search(r'\sב(?:ארוחת\s+)?(?:בוקר|צהריי?ם|ערב|ביניים|לילה)', body)
    meal_suffix = mm.group(0) if mm else ''
    segs = [x.strip() for x in re.split(r'\s*[+,]\s*', body) if x.strip()]
    if len(segs) < 2:
        return line
    out = []
    for seg in segs:
        if meal_suffix and not re.search(r'(?:בוקר|צהריי?ם|ערב|ביניים|לילה)', seg):
            seg = seg + meal_suffix
        out.append(verb + seg)
    return '\n'.join(out)


def execute_request(request_text: str, force: bool = False,
                    name_override: str = "", meal_override: str = "",
                    food_override: str = "", hint_override: str = "",
                    user_id_override: str = "", cal_mode=None) -> str:
    # ── שאילתת קריאה: "מה יש לדני בצהריים?" ────────────────────────────────────
    if _READ_QUERY_PAT.search(request_text) and not _ACTION_VERB_PAT.search(request_text):
        return _handle_read_query(request_text, name_override, meal_override, user_id_override)
    # ── שלילה: "אל תוסיפי" / "לא להוסיף" ─────────────────────────────────────
    if _NEGATION_PREFIX.search(request_text.strip()):
        return "❓ לא הבנתי — נראה כמו שלילה. אם רצית לבצע פעולה, שלח שוב ללא 'אל' / 'לא'."
    # שורות-אישור/שיחה מובילות ("כן\nאני מוסיף לך...") = תשובה ללקוח, לא פקודה → הסר לפני הפיצול.
    request_text = re.sub(r'^(?:\s*(?:כן|לא|אוקיי?|סבבה|בטח|יאללה|יאלה|מעולה|פגז|תודה|טוב|יופי|מצוין|סגור|בסדר|ברור|נכון|בדיוק|אחי)\s*\n)+', '', request_text)
    # ── מצב קלוריות: "מוריד 80 קלוריות מהפיתה" → המר ל-"80 גרם" ואותת cal_mode.
    # ההמרה לגרמים-אמיתיים (לפי המזון) נעשית ב-handle_calorie_adjust. cal_mode מועבר
    # לכל הקריאות הרקורסיביות כי אחרי ההמרה "קלוריות" כבר לא בטקסט.
    if cal_mode is None:
        cal_mode = bool(_CAL_UNIT_RE.search(request_text))
    if cal_mode:
        request_text = _CAL_UNIT_RE.sub(r'\1 גרם', request_text)
    # ── קיזוז / אופציות-מרובות: דפוסים שאסור לפצל ל-multi-task (מכילים 'מוריד'/'ו') ──
    _special_cmd = bool(_OFFSET_TRIG.search(request_text) or _OPTIONS_TRIG.search(request_text))
    # ── ריבוי אנשים: "לדני ולרון X" → 2 בקשות נפרדות ──────────────────────────
    if not name_override and not food_override and not hint_override and not _special_cmd:
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
                r1 = execute_request(req1.strip(), force, name_override, meal_override, food_override, hint_override, user_id_override, cal_mode)
                r2 = execute_request(req2.strip(), force, name_override, meal_override, food_override, hint_override, user_id_override, cal_mode)
                return f"{r1}\n\n{r2}"
    # ── ריבוי משימות: "מוסיף לך X בבוקר ותוסיף Y בערב" / שורות נפרדות ───────
    # עובד גם ב-V3 (שם מגיע מטלפון) — לא רק כשהשם בטקסט.
    # נמנע רק כשאנחנו בהמשך-בחירה (food_override/hint_override) או בקיזוז/אופציות.
    if not food_override and not hint_override and not _special_cmd:
        request_text = '\n'.join(_expand_plus_ops(ln) for ln in request_text.split('\n'))
        _phone_name = bool(force) or bool(name_override and _PHONE_RE.match(
            name_override.replace('-', '').replace(' ', '')))
        # lookahead לארוחה אופציונלית לפני הפועל — תופס "ובצהריים תוסיף", "ובארוחת בוקר תוסיף"
        _MEAL_LK = r'(?:ב?(?:ארוחת\s+)?(?:בוקר|צהריים|צהרים|ערב|ביניים|לילה)\s+)?'
        # כינוי-גוף אופציונלי בין ו'/שורה-חדשה לפועל: "ואני מוסיף", "ואז תוסיף", "וגם מוריד"
        _PRON_LK = r'(?:אני\s+|אנחנו\s+|אז\s+|גם\s+)?'
        _mt_parts = re.split(
            rf'(?:\s+ו\s*{_PRON_LK}{_MEAL_LK}(?={_MULTI_VERBS})'
            rf'|\n\s*ו?\s*{_PRON_LK}{_MEAL_LK}(?={_MULTI_VERBS}))',
            request_text.strip()
        )
        if len(_mt_parts) > 1:
            _fp = parse_message(_mt_parts[0], skip_name=_phone_name)
            _first_name = name_override or _fp.get("name", "")
            _first_meal = _fp.get("meal", "") or ""
            sub_results = []
            for i, part in enumerate(_mt_parts):
                part = (part or "").strip()
                if not part:
                    continue
                # ודא שהפועל המוביל מלווה ב"לך" כדי שהנרמול ב-V3 יזהה אותו
                # ("תוסיף 100 גרם בננה" → "תוסיף לך 100 גרם בננה"); לא נוגע ב"תוסיף לאורז"
                part = re.sub(rf'^({_MULTI_VERBS})\s+(?!ל)', r'\1 לך ', part, count=1)
                if i > 0:
                    # V1/V2: "לו/לה/לזה" → השם הראשון (ב-V3 השם מגיע מ-override)
                    if not name_override and _first_name:
                        part = re.sub(r'\bל(?:ו|ה|זה|זו|אותו|אותה)\b', f'ל{_first_name}', part)
                    # ירש ארוחה מהחלק הראשון אם חסרה בחלק הנוכחי
                    # ללא \b — בעברית "בבוקר"/"בצהריים" (עם ב') לא נתפס עם \b
                    if _first_meal and not re.search(r'(?:ערב|בוקר|צהריים|צהרים|ביניים|לילה)', part):
                        part += f' ב{_first_meal}'
                    # ודא שיש תוכן פעולה אמיתי (מזון/שינוי)
                    _pp = parse_message(part, skip_name=_phone_name)
                    if not _pp.get("change"):
                        continue
                    if not name_override and not _pp.get("name"):
                        continue
                sub_results.append(execute_request(part, force, name_override,
                                                   meal_override, food_override, hint_override,
                                                   user_id_override, cal_mode))
            # החזר רק אם באמת פוצל למשימות מרובות (אחרת המשך לפרסינג רגיל)
            if len(sub_results) > 1:
                # בריבוי-משימות אי-אפשר לנהל בחירה אינטראקטיבית אחת לכל פריט —
                # פריט עמום (FOOD_OPTIONS וכו') הופך להודעת אזהרה ברורה (לא choice קבור)
                def _sanitize_mt(r):
                    if re.match(r'^(?:FOOD_OPTIONS|HINT_OPTIONS|MEAL_OPTIONS|MULTIMEAL|CONFIRM)', r):
                        _m = re.search(r'\*([^*]+)\*', r)
                        _item = _m.group(1) if _m else "אחד הפריטים"
                        return f"⚠️ לא זיהיתי במדויק את *{_item}* — שלח אותו בנפרד לבחירה"
                    return r
                return "\n\n".join(_sanitize_mt(r) for r in sub_results)

    # ריבוי אנשים: "שם: X ... שם: Y" → מפצל ומטפל בנפרד
    if not name_override:
        _parts = re.split(r'(?m)(?=^\s*שם\s*:)', request_text.strip())
        _parts = [p.strip() for p in _parts if p.strip() and re.search(r'שם\s*:', p)]
        if len(_parts) > 1:
            sub_results = []
            for part in _parts:
                sub_results.append(execute_request(part, force, name_override,
                                                   meal_override, food_override, hint_override,
                                                   user_id_override, cal_mode))
            return "\n\n─────────────\n\n".join(sub_results)

    # force=True = הלקוח כבר זוהה (רקורסיה אחרי פתרון שם) → לעולם לא לחלץ שם שוב מהטקסט,
    # אחרת מזון שנראה כמו שם פרטי ("כוסמת"/"פתיתים"/"בננה") נאכל בטעות כשם ושובר את הפקודה.
    _skip_name = bool(force) or bool(name_override and _PHONE_RE.match(name_override.replace('-','').replace(' ','')))
    _request_has_as_option = bool(re.search(r'כאופציה', request_text))
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
            options = [o for o in found_name.split(";") if "|" in o]
            ids_pipe = "|".join(opt.split("|")[0] for opt in options)
            names_pipe = "|".join(opt.split("|")[1] for opt in options)
            return f"NAME_OPTIONS:{ids_pipe}||{names_pipe}\nמצאתי {len(options)} מתאמנים בשם *{raw_name}*.\n\nשלח/י מספר טלפון לזיהוי."

        if not uid_check:
            return f"NAME_NOT_FOUND:{raw_name}"

        # שם מדויק (לא fuzzy) → בצע ישירות, ללא אישור (גם free text)
        if not is_fuzzy and parsed.get("change"):
            return execute_request(request_text, force=True, name_override=found_name,
                                   meal_override=meal_override, food_override=food_override,
                                   hint_override=hint_override, cal_mode=cal_mode)

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
        options = [o for o in full_name.split(";") if "|" in o]
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

    # ── קיזוז קלורי / הוספה עם אופציות מרובות ────────────────────────────
    if parsed.get("offset_food") or parsed.get("options"):
        _ch = (parsed.get("ops") or [{}])[0].get("change") or parsed.get("change", "")
        _pm = re.search(r'\(([^)]+)\)', _ch)
        _nf_raw = (_pm.group(1) if _pm else _ch).strip()
        _grams = parsed.get("extra_grams")
        _gm = re.search(r'(\d+)\s*גרם', _nf_raw)
        if _gm:
            _grams = _grams or _gm.group(1)
        _nf_raw = re.sub(r'\d+\s*גרם', '', _nf_raw).strip()
        _nf_raw = re.sub(r'\b(?:הוסף|הפחת|העלה)\b', '', _nf_raw).strip()
        _nf_query = normalize_food_query(_nf_raw)
        _add_meal = meals_list[0] if meals_list else None
        if parsed.get("offset_food"):
            return handle_calorie_offset(user_id, full_name, all_meals, coach_id,
                                         _nf_query, _grams, _add_meal,
                                         parsed["offset_food"], parsed.get("offset_meal"))
        return handle_multi_options(user_id, full_name, all_meals, coach_id,
                                    _nf_query, _grams, _add_meal, parsed["options"])

    # ── התאמת קלוריות למזון קיים: "מוריד/מוסיף N קלוריות מ/ל-FOOD" ───────
    # (ריבוי-פעולות עם "ו" כבר פוצל ל-multi-task; כאן פעולה בודדת)
    if cal_mode and not food_override and not hint_override:
        return handle_calorie_adjust(user_id, full_name, all_meals,
                                     parsed, meals_list, request_text)

    # ── בנה רשימת פעולות ─────────────────────────────────────────
    # food/hint override → פעולה בודדת (תשובה לתיקון)
    if food_override or hint_override:
        ops_list = [{"change": parsed["change"], "meal": None}]
    else:
        ops_list = parsed.get("ops") or [{"change": parsed["change"], "meal": None}]

    # ── פיצול multi-food: "הוסף (X גרם FOOD ו Y גרם FOOD2)" → שתי פעולות ──
    _split_ops = []
    for _sop in ops_list:
        _sch = _sop.get("change", "")
        _smm = re.search(r'\((.+?)\s+ו(\d+\s*גרם\s+[^)]+)\)', _sch)
        if _smm:
            _split_ops.append({"change": re.sub(r'\([^)]+\)', f'({_smm.group(1)})', _sch, count=1), "meal": _sop.get("meal")})
            _split_ops.append({"change": f'הוסף ({_smm.group(2)})', "meal": _sop.get("meal")})
        else:
            _split_ops.append(_sop)
    ops_list = _split_ops

    # ── פיצול multi-food נוסף: כמה מזונות חדשים מחוברים בקישור (ו/וגם/או/גם/,/+//) ללא גרמים ──
    # "הוסף (בננה ותפוח עץ) במקום (תמר)" → הוסף(בננה)במקום(תמר) + הוסף(תפוח עץ)במקום(תמר)
    _split_ops_b = []
    for _sop in ops_list:
        _sch = _sop.get("change", "")
        _mb = re.match(r'^(הוסף|החלף)\s+\(([^)]+)\)(.*)$', _sch)
        # כל מחרוזת רב-מילית עוברת ל-_split_multi_new_food (כולל רשימה ברווחים בלבד "כוסמת פסטה בורגול");
        # הפונקציה מחליטה בבטחה — מפצלת רק אם כל הטוקנים מזון מדויק, אחרת משאירה שלם.
        # שער: רווח *או* מפריד-רשימה (פסיק/+/וגם/או) — "כוסמת,פתיתים" בלי רווח חייב להיכנס גם.
        if not food_override and not hint_override and _mb and not _sop.get("extra_grams") and re.search(r'[\s,+]|וגם|\bאו\b', _mb.group(2).strip()):
            _foods = _split_multi_new_food(_mb.group(2).strip(), coach_id)
            if len(_foods) >= 2:
                for _f in _foods:
                    _new = dict(_sop)
                    _new["change"] = f"{_mb.group(1)} ({_f}){_mb.group(3)}"
                    _split_ops_b.append(_new)
                continue
        _split_ops_b.append(_sop)
    ops_list = _split_ops_b

    _VP = r'(?:להוסיף|הוסיפ[יי]?|הוסיף|תוסיפ[יי]?|הוסף|החלף[יי]?|החליף|תחליף|תחליפ[יי]?|להחליף)'

    all_results = []
    # מזונות מ-multi-food שלא נמצאו במאגר — נאספים כדי לשאול עליהם בסוף (אחרי ביצוע הנמצאים)
    missing_foods = []  # [(query, alternatives), ...]

    # האם צוינה ארוחה כלשהי בטקסט? (גם free-text, לא רק op-level)
    _meal_in_text = bool(re.search(r'(?:בוקר|צהריי?ם|ערב|ביניים|בינים|לילה|לפני\s+אימון|אחרי\s+אימון)', request_text))
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
        # הקדמה לפני הכמות: "קצת ערכים בתפריט 50 גרם אורז" (שתי שורות שנדבקו) → קח את המזון
        # שאחרי "N גרם" וזרוק את ההקדמה. (group1=הקדמה לא-מספרית, group3=המזון האמיתי.)
        m_pre = re.search(r'^(.+?\S)\s+(\d+)\s*גרם\s+(?:ל(?=[א-ת]{3,}))?([א-ת].*)$', new_food_clean)
        if m_pre and not re.match(r'^\s*\d', m_pre.group(1)):
            extra_grams = extra_grams or m_pre.group(2)
            new_food_clean = m_pre.group(3).strip()
        grams_in_food = re.search(r'\bעוד\s+(\d+)\s*גרם\b', new_food_clean)
        if grams_in_food:
            extra_grams = extra_grams or grams_in_food.group(1)
            new_food_clean = re.sub(r'\bעוד\s+\d+\s*גרם\s*', '', new_food_clean).strip()
        else:
            m_gs2 = re.match(r'^(\d+)\s*גרם\s+(?:ל(?=[\u05D0-\u05EA]{3,}))?\s*', new_food_clean)  # "50 גרם [ל]אורז"
            if m_gs2:
                extra_grams = extra_grams or m_gs2.group(1)
                new_food_clean = new_food_clean[m_gs2.end():].strip()
            else:
                m_ge2 = re.search(r'^(.+?)\s+(\d+)\s*גרם\s*$', new_food_clean)  # "אורז 50 גרם"
                if m_ge2:
                    extra_grams = extra_grams or m_ge2.group(2)
                    new_food_clean = m_ge2.group(1).strip()
        # "150 אורז" — מספר לפני שם מזון ללא גרם (ניקוי תמיד, גם אם extra_grams ידוע)
        m_nf = re.match(r'^(\d+)\s+([א-ת].+)$', new_food_clean)
        if m_nf:
            extra_grams = extra_grams or m_nf.group(1)
            new_food_clean = m_nf.group(2).strip()

        # strip "מה" הידיעה prefix לכל פעולה: "100 גרם מהאורז" → "אורז"
        new_food_clean = re.sub(r'^מה(?=[א-ת])', '', new_food_clean).strip()
        # בפקודת הפחתה: strip prefix "מ" ("50 גרם מאורז" → "אורז")
        if op.get("reduce") or parsed.get("reduce"):
            new_food_clean = re.sub(r'^מ(?=[א-ת])', '', new_food_clean).strip()

        new_food_query = normalize_food_query(new_food_clean)

        if hint_override and op_idx == 0:
            group_hint = hint_override

        # חפש מזון
        if food_override and op_idx == 0:
            # בחירה מפורשת של דני מהרשימה = שם מדויק מהמאגר. חפש אותו כמו שהוא קודם —
            # נרמול ("לאחר בישול"→"מבושל") עלול להרוס שם תקף שקיים במאגר וליצור לולאת FOOD_OPTIONS.
            foods = search_food(food_override, coach_id)
            if not foods:
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
            # הגנה: שם מזון ריק/רק מספר ("50 גרם בצהריים" בלי מזון) → אל תוסיף מזון אקראי!
            _food_letters = re.sub(r'גרם|גר|[\d\s.\-]', '', new_food_query)
            if new_food_query.strip() in _NOT_FOOD_WORDS:
                # מילת-אישור/שיחה ("כן") — לא מאכל. דלג בשקט (לא להוסיף שטות לתפריט).
                if len(ops_list) == 1:
                    return "❓ לא זיהיתי מאכל בבקשה (נראה כמו תשובה כללית). שלח שוב עם שם המזון."
                continue
            if len(_food_letters) < 2:
                _miss = "❓ לא ציינת איזה מזון. דוגמה: *מוסיף לך 50 גרם אורז בצהריים*"
                if len(ops_list) == 1:
                    return _miss
                all_results.append(_miss)
                continue
            # הורדה ממזון קיים: אל תחפש במאגר הגלובלי ("לחם"=30+ וריאציות → שאלה מיותרת).
            # בלוק ההורדה בהמשך מאתר את המזון *בארוחה* ומפחית. best_food לא נדרש להורדה.
            _is_reduce_op = bool(op.get("reduce") or parsed.get("reduce"))
            if _is_reduce_op and extra_grams and not group_hint:
                best_food, alternatives = None, []
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
                        return (f"FOOD_OPTIONS:{new_food_query}||\n"
                                f"לא מצאתי *{new_food_query}* במאגר 🤔\n"
                                f"כתוב שם מזון אחר:")
                    # multi-food: אל תדלג בשקט — אסוף את החסר ושאל עליו בסוף (אחרי ביצוע הנמצאים)
                    missing_foods.append((new_food_query, alternatives or [], group_hint))
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
            # אם אין ארוחה מפורשת ויש מאכל-להחלפה — מצא באיזו ארוחה הוא נמצא
            if group_hint and not op_meal_str and not _meal_in_text and not meal_override:
                _hw = normalize_food_query(group_hint).split()
                _hint_meals = []
                for _m in all_meals:
                    for _f in (_m.get("mealFoods") or _m.get("new_meal_food") or []):
                        if _hw and all(w in normalize_food_query(_f.get("food_name","")) for w in _hw):
                            _hint_meals.append(_m); break
                if len(_hint_meals) == 1:
                    full_meal = _hint_meals[0].get("meal_name","").strip()
                elif len(_hint_meals) > 1:
                    _mnames = [m.get("meal_name","").strip() for m in _hint_meals]
                    _opts = "\n".join(f"{i+1}. {nm}" for i, nm in enumerate(_mnames))
                    _prefix = ("\n\n".join(all_results) + "\n\n") if all_results else ""
                    return ("MULTIMEAL:" + "|".join(_mnames) + f"\n{_prefix}"
                            f"❓ *{group_hint}* נמצא ב-{len(_hint_meals)} ארוחות:\n{_opts}\n\n"
                            f"שלח מספר לארוחה אחת, או *הכל* לכולן.")

            # ─── גרמים ללא group_hint → UPDATE כמות קיימת ────────────────────
            # force_new=True (בקשת "מזון חדש") → תמיד ADD, לא UPDATE
            _is_reduce = op.get("reduce") or parsed.get("reduce")
            if extra_grams and not group_hint and (not parsed.get("force_new") or _is_reduce):
                _, upd_row, upd_err = find_meal_and_food(all_meals, full_meal, new_food_query)
                if upd_row:
                    curr_q = float(upd_row.get("quantity") or upd_row.get("quantity_to_calculate") or 0)
                    _unit = (upd_row.get("measure") or "grams").strip().lower() == "unit"
                    _ul = "יח'" if _unit else "גרם"
                    _fmtq = (lambda x: f"{x:.1f}".rstrip("0").rstrip(".")) if _unit else (lambda x: f"{int(round(x))}")
                    if _is_reduce:
                        if curr_q <= 0:
                            all_results.append(f"⚠️ '{new_food_query}' כבר ב-0 {_ul} ב{full_meal}")
                            continue
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
                            f"{_fmtq(curr_q)}{arrow}{_fmtq(new_q)} {_ul} "
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
            # אם המאכל-להחלפה לא נמצא בארוחה שצוינה/ברירת-המחדל — חפש אותו בכל הארוחות.
            # (דני לרוב לא מציין ארוחה: "תוסיף X באופציה של Y" → מצא איפה Y נמצא)
            if err and group_hint and "לא נמצא" in err:
                _hw = normalize_food_query(group_hint).split()
                _found = []
                for _m in all_meals:
                    for _f in (_m.get("mealFoods") or _m.get("new_meal_food") or []):
                        _fn = normalize_food_query(_f.get("food_name",""))
                        if _hw and all(w in _fn for w in _hw):
                            _found.append(_m); break
                if len(_found) == 1:
                    full_meal = _found[0].get("meal_name","").strip()
                    meal_id, food_row, err = find_meal_and_food(all_meals, full_meal, group_hint)
            if err:
                multi_meal = len(op_meals) > 1 or len(ops_list) > 1
                prefix = ("\n\n".join(all_results) + "\n\n") if all_results else ""
                if "לא נמצאה ארוחה" in err or "לא נמצאו ארוחות" in err:
                    available = [m.get("meal_name","").strip() for m in all_meals]
                    if available and not multi_meal:
                        opts = "\n".join(f"{i+1}. {a}" for i, a in enumerate(available))
                        # אם דני לא ציין ארוחה (ברירת מחדל ערב) — שאל ישר "לאיזו ארוחה", לא "לא מצאתי ערב"
                        _q = (f"❓ לא מצאתי '{full_meal}'. יש:" if (op_meal_str or _meal_in_text)
                              else f"❓ לאיזו ארוחה להוסיף את *{new_food_query}*?")
                        return f"MEAL_OPTIONS:{full_meal}|" + "|".join(available) + f"\n{prefix}{_q}\n{opts}\n\nשלח מספר או שם ארוחה."
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

            _as_option = op.get("as_option") or parsed.get("as_option") or _request_has_as_option
            add_result = add_food_to_meal(user_id, meal_id, best_food, food_row, extra_grams, is_addition_as_option=bool(_as_option))
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
                            if _ag_f > 0:
                                new_disp = f" ({int(_ag_f) if _ag_f == int(_ag_f) else round(_ag_f,1)} גרם)"
                            else:
                                new_disp = ""
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

    # ── דיווח חלקי: חלק מהמזונות נמצאו ובוצעו, חלק לא — שלח קבלה ושאל על החסר ──
    if missing_foods:
        _done = "\n\n".join(all_results)
        _mq, _malts, _mhint = missing_foods[0]
        # מה שעוד נשאר אחרי החסר הנוכחי — להזכיר לדני שיצטרך לטפל בו בנפרד
        _rest = [m[0] for m in missing_foods[1:]]
        _rest_note = f"\n_(ממתינים גם: {', '.join(_rest)} — נטפל אחרי זה)_" if _rest else ""
        _done_block = (_done + "\n\n") if _done else ""
        if _malts:
            _opts = "\n".join(f"{i+1}. {f['food_name']}{(' — ' + str(int(float(f.get('calories') or 0))) + ' קל') if float(f.get('calories') or 0) else ''}" for i, f in enumerate(_malts[:10]))
            _alts_pipe = "|".join(f"{f.get('food_name','')}:{int(float(f.get('calories',0) or 0))}" for f in _malts)
            _more = "\n\n_שלח *עוד* לאפשרויות נוספות_" if len(_malts) > 10 else ""
            return (f"FOOD_OPTIONS:{_mq}||{_alts_pipe}\n"
                    f"{_done_block}❓ צריך עזרה ב-*{_mq}* — מה שמצאתי:\n{_opts}{_rest_note}\n\n"
                    f"בחר מספר, כתוב שם אחר, או שלח *עוד*.{_more}")
        return (f"FOOD_OPTIONS:{_mq}||\n"
                f"{_done_block}❓ לא מצאתי *{_mq}* במאגר 🤔{_rest_note}\nכתוב שם מזון אחר:")

    return "\n\n".join(all_results) if all_results else "❌ לא בוצעה פעולה"


# ─── Exercises ────────────────────────────────────────────────────────────────

def _get_user_assigned_trainings_ex(user_id: str) -> list:
    data = _post("/coach/get-user-assigned-trainings", {"user_id": str(user_id)})
    return data.get("data", {}).get("items", [])

def _get_training_templates_ex(user_id: str, training_id: str) -> list:
    data = _post("/coach/get-all-user-assigned-training-templates", {
        "user_id": str(user_id), "training_id": str(training_id)
    })
    return data.get("data", {}).get("exercise_template", [])

def _get_template_exercises_ex(user_id: str, training_id: str, exercise_template_id: str) -> list:
    data = _post("/coach/user-assigned-training-exercise", {
        "user_id": str(user_id),
        "training_id": str(training_id),
        "exercise_template_id": str(exercise_template_id)
    })
    return data.get("data", {}).get("exercise", {}).get("items", [])

_FINAL_LETTERS = str.maketrans("םןץףך", "מנצפכ")

def _norm_heb(s: str) -> str:
    """נרמול עברי לחיפוש סובלני לכתיב מלא/חסר ושגיאות נפוצות.
    מטפל ב: ה' הידיעה, כתיב מלא/חסר (וו/יי), א'/ע' שותקות,
    אותיות סופיות, ות'/ט (סקוואט=סקוואת), רווחים ומקפים.
    """
    s = s.strip().lower()
    s = s.translate(_FINAL_LETTERS)        # אותיות סופיות → רגילות
    s = re.sub(r'^ה(?=[א-ת])', '', s)       # ה' הידיעה בתחילת מילה
    s = re.sub(r'\bה(?=[א-ת])', '', s)      # ה' הידיעה אחרי רווח
    s = re.sub(r'ות(?=\s|$)', 'ת', s)       # רבים נקבה: -ות→-ת (הרחקות~הרחקת~הרחקה).
                                            # לפני הסרת א' כדי ש'סקוואת' לא ייצור 'ות' מלאכותי
    s = s.replace('א', '').replace('ע', '')  # אותיות שותקות
    s = re.sub(r'ו+', 'ו', s)               # כתיב מלא/חסר: וו→ו
    s = re.sub(r'י+', 'י', s)               # יי→י
    s = re.sub(r'ה(?=\s|$)', 'ת', s)        # סמיכות: ה' סופית→ת' (לחיצה~לחיצת)
    s = s.replace('ת', 'ט')                 # סקוואת=סקוואט, לחיצת=לחיצה=לחיצט
    s = re.sub(r'[\s\-־״׳\'".,]', '', s)    # רווחים/מקפים/פיסוק
    return s


# מילות חיבור/יחס + תיאורי-ציוד שלא מבדילים בין תרגילים — מתעלמים מהם בהתאמה.
# הציוד (מוט/כבל/חבל/פולי...) נכלל כי 'פשיטת מרפקים במוט' = 'פשיטת מרפקים' —
# ה'ב' היא ב' יחס ('בעזרת מוט'), חלק מתיאור התרגיל ולא מילה מבדילה.
_EX_STOPWORDS = {
    'את', 'של', 'עם', 'כנגד', 'נגד', 'על', 'ה', 'או', 'עד', 'בין', 'כ',
    'לתרגיל', 'תרגיל', 'בתרגיל', 'בעמידה', 'בישיבה', 'במכונה', 'מכונה',
    'ייעודית', 'חופשי', 'מוט', 'בסמיט', 'יד', 'אחיזה',
    'משקולת', 'משקולות', 'דמבל', 'דמבלים', 'כבל', 'כבלים', 'חבל', 'חבלים',
    'פולי', 'גריף', 'קטלבל', 'גומיה', 'גומיות', 'סמיט', 'w',
}
_EX_STOP_NORM = {_norm_heb(x) for x in _EX_STOPWORDS}

def _ex_tokens(s: str) -> list:
    """מפרק שם תרגיל למילים משמעותיות מנורמלות (ללא מילות חיבור/ציוד).
    מסיר גם ב'/ל' יחס שדבקה למילת-ציוד ('במוט'→'מוט') כדי שלא תיחשב מבדילה."""
    raw = re.split(r'[\s\-־]+', s.strip().lower())
    toks = []
    for w in raw:
        if not w or w in _EX_STOPWORDS:
            continue
        n = _norm_heb(w)
        if len(n) < 2 or n in _EX_STOP_NORM:
            continue
        # ב'/ל' יחס לפני מילת-ציוד: 'במוט'→'מוט', 'בכבל'→'כבל' → דלג
        if re.sub(r'^[בל]', '', n) in _EX_STOP_NORM:
            continue
        toks.append(n)
    return toks


# ─── זיהוי קבוצת שריר ────────────────────────────────────────────────────────
# canonical → רשימת כינויים/שמות נרדפים שהמאמן עשוי לכתוב
_MUSCLE_GROUPS = {
    "רגליים":   ["רגליים", "רגלים", "רגל", "רגלי", "רגליות"],
    "חזה":      ["חזה"],
    "גב":       ["גב"],
    "כתפיים":   ["כתפיים", "כתפים", "כתף", "כתפי"],
    "בטן":      ["בטן", "בטני"],
    "יד קדמית": ["יד קדמית", "יד קידמית", "יד קדמי", "ביצפס", "בייספס", "בייצפס", "יד-קדמית"],
    "יד אחורית": ["יד אחורית", "יד אחורי", "טרייספס", "טרייסיפס", "יד-אחורית"],
}
_ARM_BOTH = {"ידיים", "ידים", "יד"}  # סלנג → שתי קבוצות הזרוע

# תרגילים ללא קידומת 'שריר - ' תקנית — מיפוי ידני לפי החלטת המאמן.
# נבדק לפני לוגיקת הקידומת כי השם עלול להכיל מילת-שריר מטעה
# (למשל 'משיכה לחזה' = lat pulldown, שהוא תרגיל גב ולא חזה).
_EXERCISE_NAME_GROUP = {
    "משיכה לחזה":  "גב",       # פולי עליון / lat pulldown
    "גוד מורנינג": "רגליים",   # good morning — ירך אחורית
}

# שמות מדוברים/נרדפים/לועזיים שמאמן כותב → מילות-מפתח של השם הרשמי בספרייה.
# מוחל ב-match_exercises רק על התאמה-מלאה של המונח (override), כדי לתפוס גם
# מונחים שכיום מחזירים תוצאה שגויה (למשל 'עגלים'). ניתן להרחבה לפי בקשת המאמן.
_EXERCISE_SYNONYMS = {
    # כתפיים
    "הרחקות צד": "הרחקה לצדדים", "הרחקות צדדים": "הרחקה לצדדים",
    "כתף צד": "הרחקה לצדדים", "לטרל רייז": "הרחקה לצדדים",
    "שולדר פרס": "לחיצת כתפיים", "שראגים": "טרפזים", "כתף קדמית": "כתף קידמית",
    # חזה
    "שכיבות שמיכה": "שכיבות סמיכה", "פוש אפ": "שכיבות סמיכה", "בנץ פרס": "לחיצת חזה",
    # גב
    "לאט פולדאון": "פולי עליון אחיזה רחבה", "בנדאובר": "בנט אובר",
    # ידיים
    "כפיפת מרפק": "יד קדמית", "כפיפות מרפק": "יד קדמית",
    "האמר": "פטישים", "פושדאון": "פשיטת מרפקים", "דיפס": "מקבילים",
    # רגליים
    "לאנגים": "מכרעיים", "לאנג": "מכרעיים", "לאנג׳": "מכרעיים",
    "לג פרס": "לחיצת רגליים", "לג קרל": "כפיפת ברכיים", "לג אקסטנשן": "פשיטת ברכיים",
    "מקרבים": "קירוב ירך", "אדוקטור": "קירוב ירך",
    "מרחיקים": "הרחקת ירך", "אבדוקטור": "הרחקת ירך",
    "קאף": "תאומים", "עגלים": "תאומים",
    # בטן
    "קראנץ": "כפיפות בטן",
}
_SYN_NORM = {_norm_heb(k): v for k, v in _EXERCISE_SYNONYMS.items()}

# ─── ניטור: תרגילים שלא נמצאו (לתפיסת שמות מדוברים שחסר להם נרדף) ──────────────
_UNMATCHED_LOG = Path("/data" if os.path.isdir("/data") else str(Path(__file__).parent)) / "unmatched_exercises.log"

def _log_unmatched_exercise(term: str, full_name: str, kind: str = "old") -> None:
    """רושם מונח-תרגיל שהבוט לא מצא — כדי לזהות שמות מדוברים שחסר להם
    ערך ב-_EXERCISE_SYNONYMS. נכתב ל-stderr (תג [EX-NOT-FOUND] לחיפוש מהיר
    ב-Railway logs) ולקובץ מרוכז ב-volume הקבוע (/data, שורד deploys).
    לעולם לא ל-stdout — שם נשלח לוואטסאפ."""
    import sys
    print(f"[EX-NOT-FOUND] kind={kind} term={term!r} user={full_name!r}", file=sys.stderr, flush=True)
    try:
        with open(_UNMATCHED_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{kind}\t{term}\t{full_name}\n")
    except Exception:
        pass

def _detect_muscle_groups(term: str) -> list:
    """אם המונח הוא שם קבוצת שריר (ולא תרגיל ספציפי) — מחזיר את הקבוצות.
    'הרגליים'→['רגליים'], 'ידיים'→['יד קדמית','יד אחורית'], 'סקוואט'→[].
    """
    t = re.sub(r'^ה', '', term.strip().lower()).strip()
    tn = _norm_heb(t)
    if tn in {_norm_heb(x) for x in _ARM_BOTH}:
        return ["יד קדמית", "יד אחורית"]
    for canon, aliases in _MUSCLE_GROUPS.items():
        if tn in {_norm_heb(a) for a in aliases}:
            return [canon]
    return []

def _exercise_group(name: str):
    """מזהה את קבוצת השריר של תרגיל לפי הקידומת ('רגליים - X' → 'רגליים').
    קודם בודק override ידני (_EXERCISE_NAME_GROUP) לתרגילים ללא קידומת תקנית.
    מחזיר None אם אין קידומת ' - ' ברורה ואין override (כדי לא לסווג שגוי)."""
    name_norm = _norm_heb(name)
    for pat, canon in _EXERCISE_NAME_GROUP.items():
        if _norm_heb(pat) in name_norm:
            return canon
    if " - " not in name:
        return None
    prefix_norm = _norm_heb(name.split(" - ")[0])
    for canon, aliases in _MUSCLE_GROUPS.items():
        for a in aliases:
            if _norm_heb(a) in prefix_norm:
                return canon
    return None


def _collect_user_exercises(user_id: str) -> list:
    """אוסף את כל התרגילים של המשתמש מכל האימונים (קריאות API).
    מחזיר רשימת dict: {assignment_id, exercise_name, template_name, sets, reps, min_reps}.
    """
    all_ex = []
    seen = set()
    for tr in _get_user_assigned_trainings_ex(user_id):
        t_id = str(tr.get("training_id", ""))
        for tmpl in _get_training_templates_ex(user_id, t_id):
            tmpl_id = str(tmpl.get("exercise_template_id", ""))
            tmpl_name = tmpl.get("template_name", "")
            for ex in _get_template_exercises_ex(user_id, t_id, tmpl_id):
                aid = str(ex.get("id", ""))
                if not aid or aid in seen:
                    continue
                seen.add(aid)
                _sets_raw = ex.get("assigned_training_exercise") or ex.get("assignedTrainingExercises") or []
                _sets = [{"id": str(s.get("id", "")), "reps": s.get("exercise_reps")}
                         for s in _sets_raw if s.get("id")]
                all_ex.append({
                    "assignment_id": aid,
                    "exercise_id": str(ex.get("exercise_id", "")),
                    "exercise_name": ex.get("exercise_name", ""),
                    "template_name": tmpl_name,
                    "sets": ex.get("exercise_sets", ""),
                    "reps": ex.get("exercise_reps", ""),
                    "min_reps": ex.get("exercise_min_reps", ""),
                    "sets_list": _sets,
                })
    return all_ex


_HEB_PREFIXES = set('בכלמ')  # אותיות-שימוש שכיחות לפני שם תרגיל

def _tok_in(tok: str, name_toks: set) -> bool:
    """התאמת טוקן עם סובלנות לאות-שימוש מובילה: 'בלחיצת'~'לחיצת', 'מהרחקה'~'הרחקה'.
    מסיר אות-שימוש בודדת (ב/כ/ל/מ) רק כשהשארית (≥3 תווים) תואמת טוקן קיים בשם —
    כך לא נוצרות התאמות-שווא (מילה שמתחילה באות שורשית לא תאבד את תחילתה סתם)."""
    if tok in name_toks:
        return True
    if len(tok) >= 4 and tok[0] in _HEB_PREFIXES and tok[1:] in name_toks:
        return True
    return False


def match_exercises(all_ex: list, search_term: str) -> list:
    """מתאים את search_term לרשימת תרגילים נתונה (ללא API — לוגיקה טהורה).
    שלב 0: קבוצת שריר ('רגליים') → כל תרגילי הקבוצה.
    שלב 1: התאמת תת-מחרוזת (כולל נרמול כתיב מלא/חסר).
    שלב 2 (אם אין): התאמה לפי מילים משמעותיות (לחיצת חזה כנגד = עם).
    """
    # שם מדובר/נרדף → השם הרשמי בספרייה (override רק בהתאמה-מלאה של המונח)
    _syn = _SYN_NORM.get(_norm_heb(search_term))
    if _syn:
        search_term = _syn
    term = search_term.strip().lower()
    term_no_heh = re.sub(r'^ה(?=[א-ת])', '', term)
    term_norm = _norm_heb(search_term)
    search_toks = _ex_tokens(search_term)

    def _rec(e):
        return {k: e.get(k) for k in ("assignment_id", "exercise_id", "exercise_name", "template_name", "sets", "reps", "min_reps", "sets_list")}

    # שלב 0: זיהוי קבוצת שריר — אם המונח הוא שם שריר, החזר את כל תרגילי הקבוצה
    groups = _detect_muscle_groups(search_term)
    if groups:
        grp = [_rec(e) for e in all_ex if _exercise_group(e["exercise_name"]) in groups]
        if grp:
            return grp

    # שלב 1: התאמת תת-מחרוזת
    substr = []
    for e in all_ex:
        nl = e["exercise_name"].lower()
        n_no_heh = re.sub(r'^ה(?=[א-ת])', '', nl)
        n_norm = _norm_heb(e["exercise_name"])
        if (term in nl) or (term_no_heh in n_no_heh) or (len(term_norm) >= 3 and term_norm in n_norm):
            substr.append(_rec(e))
    if substr:
        return substr

    # שלב 2: התאמה לפי מילים משמעותיות
    if not search_toks:
        return []
    scored = []
    for e in all_ex:
        name_toks = set(_ex_tokens(e["exercise_name"]))
        if not name_toks:
            continue
        matched = sum(1 for t in search_toks if _tok_in(t, name_toks))
        frac = matched / len(search_toks)
        # דרוש 70%+ מהמילים (=כל המילים בחיפוש של 2-3 מילים) כדי למנוע
        # התאמות-שווא (חזה≠כתפיים כששתיהן "לחיצת...משקולות")
        if frac >= 0.7 and (matched >= 2 or len(search_toks) == 1):
            scored.append((frac, matched, _rec(e)))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [r for _, _, r in scored]


def find_all_exercise_assignments(user_id: str, search_term: str) -> list:
    """מחפש את כל התרגילים שמתאימים ל-search_term בכל אימוני המשתמש.
    מחזיר רשימה ממוינת: {assignment_id, exercise_name, template_name, sets, reps, min_reps}.
    """
    return match_exercises(_collect_user_exercises(user_id), search_term)


def swap_candidates(all_ex: list, term: str):
    """מחזיר (רשימת_התאמות_לתרגיל_הישן, שם_שריר_אם_term_הוא_שריר).
    - term שם שריר ('רגליים') → כל תרגילי הקבוצה שבתוכנית (לבחירת הישן).
    - term תרגיל ספציפי → ההתאמות עצמן.
    """
    matches = match_exercises(all_ex, term)
    if not matches:
        return [], None
    detected = _detect_muscle_groups(term)
    if detected:
        return matches, "/".join(detected)
    return matches, None


def _library_for_muscle(muscle: str, exclude_name: str = "") -> list:
    """מחזיר את כל תרגילי הספרייה לשריר נתון (אפשרויות החלפה)."""
    lib = search_exercise_library(muscle)
    out, seen = [], set()
    for e in lib:
        name = e.get("exercise_name", "").strip()
        eid = str(e.get("id", ""))
        if not eid or not name or name == exclude_name or eid in seen:
            continue
        # סנן תרגילים ששייכים בבירור לשריר אחר (קידומת שונה)
        g = _exercise_group(name)
        if g is not None and muscle not in g and g not in muscle:
            continue
        seen.add(eid)
        out.append({"id": eid, "exercise_name": name})
    return out


def _exercise_replace_list(assignment_id: str, old_name: str, full_name: str, muscle: str) -> str:
    """בונה תגובת EXERCISE_REPLACE — רשימת תרגילי הספרייה לאותו שריר להחלפה."""
    lib = _library_for_muscle(muscle, exclude_name=old_name)[:25]
    if not lib:
        return ""
    opts = "|".join(f"{e['id']}:{e['exercise_name']}" for e in lib)
    lines = "\n".join(f"{i+1}. {e['exercise_name']}" for i, e in enumerate(lib))
    return (
        f"EXERCISE_REPLACE:{assignment_id}:{old_name}\n"
        f"OPTS:{opts}\n"
        f"🔁 להחלפת *{old_name}* של {full_name}\n"
        f"בחר תרגיל *{muscle}* חלופי מהספרייה:\n{lines}\n\n"
        f"שלח *מספר* לבחירת התרגיל החדש.\n"
        f"_טעיתי בשריר? שלח שם שריר אחר (חזה/גב/רגליים/כתפיים/בטן/יד קדמית/יד אחורית)._"
    )


def find_exercise_assignment(user_id: str, exercise_hint: str):
    """מחזיר (assignment_id, exercise_name, template_name) של ההתאמה הראשונה."""
    results = find_all_exercise_assignments(user_id, exercise_hint)
    if not results:
        return None, None, None
    return results[0]["assignment_id"], results[0]["exercise_name"], results[0]["template_name"]

def search_exercise_library(query: str) -> list:
    """מחפש תרגיל בספריית auto-fit. מחזיר [{id, exercise_name}]"""
    data = _post("/coach/training-list", {"type": "Exercise", "search": query})
    return data.get("data", {}).get("items", [])

# מילות קישור שמתחילות משפט-המשך (לא חלק משם התרגיל)
_TAIL_CONJ = r'\s+(?:כי|כדי|אבל|בגלל|מפני|כיוון|כש|מאחר|היות|אז|כך|ש)\b.*$'

def _clean_ex_name(s: str) -> str:
    """מנקה שם תרגיל ממילות פתיחה שדבקו (את/של) ומהמשך משפט (כי/אבל/...)."""
    s = s.strip()
    s = re.sub(r'^(?:את|של)\s+', '', s)
    s = re.sub(_TAIL_CONJ, '', s)           # חתוך "...כי כואב לך הברך"
    return s.strip()


def parse_exercise_command(text: str):
    """חולץ (תרגיל_ישן, תרגיל_חדש) מהודעה חופשית בעברית.
    תומך גם ב'באימון' לפני התרגיל וגם אחריו (הסגנון של דני):
      A. "מחליף לך באימון [את] X ב-Y"   ← באימון לפני, עם תרגיל חדש
      B. "משנה לך את X באימון [ב-/ל-Y]"  ← באימון אחרי התרגיל
      C. "מחליף לך באימון [את] X"        ← באימון לפני, בלי תרגיל חדש
    מחזיר (old, new) — new יכול להיות ריק.
    """
    text = re.sub(r'^(?:אז\s+)?(?:אני\s+)?', '', text.strip())

    # D: "...X [ב-Y] באימון$"  (באימון בסוף, בלי "את" — סגנון דני: "מחליף לך דדלפיט רומני באימון")
    m_end = re.search(r'\s+(?:באימון|לאימון|[בלה]?ת[ו]?כנית)\s*$', text)
    if m_end:
        body = text[:m_end.start()].strip()
        body = re.sub(r'^(?:מחלי[פף]\w*|תחלי[פף]\w*|החלי[פף]\w*|משנה|תשנ\w*|מעדכן\w*|מסיר\w*|תסיר\w*|מוריד\w*|תוריד\w*)\s+(?:ל[ךו]\s+)?(?:את\s+)?', '', body).strip()
        mb = re.search(r'^(.+?)\s+ב-?([א-ת].+)$', body)
        if mb and _ex_tokens(mb.group(2)):
            return _clean_ex_name(mb.group(1)), _clean_ex_name(mb.group(2))
        if body and _ex_tokens(body):
            return _clean_ex_name(body), ''

    # A: "...באימון [את] X ב-Y"  (באימון לפני, עם תרגיל חדש)
    m = re.search(r'(?:באימון|לאימון|[בלה]?ת[ו]?כנית)\s+(?:את\s+)?(.+?)\s+ב-?([^\n]+)$', text)
    if m:
        old_part = _clean_ex_name(m.group(1))
        new_part = _clean_ex_name(m.group(2).lstrip('-'))
        # 'ב' יחס לתיאור-ציוד ('במוט'/'בכבל'/'בפולי') הוא חלק משם התרגיל —
        # לא תרגיל חדש. אם בחלק ה'חדש' אין אף מילה מבדילה (רק ציוד/יחס),
        # צרף אותו לשם הישן והשאר new ריק (→ זרימת בחירת-חלופי לפי שריר).
        if not _ex_tokens(new_part):
            return _clean_ex_name(f"{old_part} ב{new_part}"), ''
        return old_part, new_part

    # B: "...את X באימון [ב-/ל-Y] [שאר המשפט]"  (באימון אחרי — סגנון דני)
    m = re.search(r'את\s+(.+?)\s+(?:באימון|לאימון|[בלה]?ת[ו]?כנית)(\s+.+)?$', text)
    if m:
        old = _clean_ex_name(m.group(1))
        rest = (m.group(2) or '').strip()
        new = ''
        mn = re.match(r'^[בל]-?\s*(.+)$', rest)   # תרגיל חדש רק אם מתחיל ב-ב/ל
        if mn:
            new = _clean_ex_name(mn.group(1))
        if old:
            return old, new

    # C: "...באימון [את] X"  (באימון לפני, בלי תרגיל חדש)
    m = re.search(r'(?:באימון|לאימון|[בלה]?ת[ו]?כנית)\s+(?:את\s+)?(.+)$', text)
    if m:
        return _clean_ex_name(m.group(1)), ''

    return '', ''


# ─── סטים: הוספה / הורדה / עדכון חזרות (endpoints מקוד האתר, אומת live) ──────────
def add_exercise_set(assignment_id: str, exercise_id: str) -> dict:
    """מוסיף סט חדש לתרגיל. מחזיר {status, data:{id,...}}."""
    return _post("/coach/user-add-new-training-exercise-set",
                 {"user_assigned_trainings": str(assignment_id), "exercise_id": str(exercise_id)})

def remove_exercise_set(set_id: str) -> dict:
    """מוחק סט בודד לפי id."""
    return _post("/coach/user-delete-training-exercises-sets", {"id": str(set_id)})

def update_set_reps(set_id: str, reps) -> dict:
    """מעדכן חזרות (exercise_reps) של סט בודד."""
    return _post("/coach/user-update-training-exercises-sets",
                 {"id": str(set_id), "key": "exercise_reps", "value": str(reps)})


_SET_REMOVE_VERBS = ("מוריד", "תוריד", "תורידי", "הורד", "מורידה", "מפחית", "פחות", "להוריד", "הורדתי")

def parse_set_command(text: str):
    """מזהה פקודת סט. מחזיר (op, reps, exercise_hint) או None.
    op='add'/'remove'; reps=int|None. דוגמאות:
      'מוסיף לך עוד סט בלחיצת חזה'         → ('add', None, 'לחיצת חזה')
      'מוסיף לך סט של 20 חזרות בלחיצת חזה' → ('add', 20, 'לחיצת חזה')
      'מוריד לך סט בלחיצת חזה'             → ('remove', None, 'לחיצת חזה')
    """
    t = re.sub(r'^אני\s+', '', text.strip())
    if not re.search(r'(?<![א-ת])סט(?:ים)?(?![א-ת])', t):
        return None
    op = 'remove' if any(v in t for v in _SET_REMOVE_VERBS) else 'add'
    # חזרות — בכל מקום (לפני או אחרי שם התרגיל): "N חזרות" / "של N" / "עם N"
    reps = None
    mr = re.search(r'(\d+)\s*חזרות', t) or re.search(r'(?:של|עם)\s+(\d+)', t)
    if mr:
        reps = int(mr.group(1))
    # הסר את ביטוי החזרות כדי שלא יזהם את שם התרגיל (משני הסדרים)
    t2 = re.sub(r'(?:של|עם)\s+\d+\s*(?:חזרות)?', ' ', t)
    t2 = re.sub(r'\d+\s*חזרות', ' ', t2)
    t2 = re.sub(r'\s+', ' ', t2).strip()
    # שם התרגיל: אחרי 'סט [נוסף]' + מילת יחס ב/ל
    m = re.search(r'סט(?:ים)?(?:\s+נוסף)?\s+[בל]?(.+)$', t2)
    ex = (m.group(1).strip() if m else '')
    ex = re.sub(r'^ה(?=[א-ת])', '', ex).strip()
    ex = re.sub(r'^(?:עוד|נוסף)\s+', '', ex).strip()
    # נקה שאריות "באימון X" / "בתוכנית" / "לשניהם" מהקצה (נשמרות ב-raw_text)
    ex = re.sub(r'\s*(?:לשניהם|לשתיהן|לשלושתם|לכולם)\s*$', '', ex).strip()
    ex = re.sub(r'\s*(?:ב|ל|ה)?(?:אימון|ת[ו]?כנית)(?:\s+[A-Za-zא-ת0-9]{1,12})?\s*$', '', ex).strip()
    if len(ex) < 2:
        return None
    return (op, reps, ex)


def _apply_set_op(ex: dict, op: str, reps, full_name: str) -> str:
    """מבצע הוספה/הורדה של סט בודד לתרגיל יחיד. מחזיר הודעת תוצאה."""
    name = ex["exercise_name"]
    tmpl = ex.get("template_name", "")
    label = f" (אימון: {tmpl})" if tmpl else ""
    sets = ex.get("sets_list") or []
    n = len(sets)
    if op == "remove":
        if not sets:
            return f"❌ ל-*{name}*{label} אין סטים להורדה"
        r = remove_exercise_set(sets[-1]["id"])
        if r.get("status"):
            return f"✅ הורדתי סט מ-*{name}*{label}\n👤 {full_name} | 📉 {n} → {n-1} סטים"
        return f"❌ שגיאה בהורדת סט: {r.get('message','')}"
    # add
    if not ex.get("exercise_id"):
        return f"❌ חסר מזהה תרגיל עבור *{name}* — לא ניתן להוסיף סט"
    target_reps = reps
    if target_reps is None and sets:
        target_reps = sets[-1].get("reps")   # "עוד סט" ללא חזרות = כמו הסט האחרון
    r = add_exercise_set(ex["assignment_id"], ex["exercise_id"])
    if not r.get("status"):
        return f"❌ שגיאה בהוספת סט: {r.get('message','')}"
    new_id = (r.get("data") or {}).get("id")
    reps_txt = ""
    if target_reps not in (None, "", "null") and new_id:
        ur = update_set_reps(new_id, target_reps)
        if ur.get("status"):
            reps_txt = f" של {target_reps} חזרות"
    return f"✅ הוספתי סט{reps_txt} ל-*{name}*{label}\n👤 {full_name} | 📈 {n} → {n+1} סטים"


def execute_set_command(uid: str, full_name: str, op: str, reps, exercise_hint: str, raw_text: str = "") -> str:
    """מבצע הוספת/הורדת סט לתרגיל. לא נוגע בסטים אחרים (כלל עריכת אימונים)."""
    all_ex = _collect_user_exercises(uid)
    matches = match_exercises(all_ex, exercise_hint)
    if not matches:
        _log_unmatched_exercise(exercise_hint, full_name, kind="set")
        return f"❌ לא מצאתי תרגיל '{exercise_hint}' בתוכנית של {full_name}"
    _all_word = bool(raw_text and re.search(r'לשניהם|לשתיהן|לשלושתם|לכולם|לכל ה', raw_text))
    # כפילות (אותו תרגיל בכמה אימונים) — סנן לפי שם אימון שדני ציין (A/B/C / "אימון X")
    if len(matches) > 1 and raw_text and not _all_word:
        flt = [m for m in matches if m.get("template_name") and
               re.search(r'(?<![A-Za-zא-ת])' + re.escape(m["template_name"]) + r'(?![A-Za-zא-ת])', raw_text)]
        if len(flt) == 1:
            matches = flt
    if len(matches) > 1:
        if _all_word:   # "לשניהם" — בצע על כל ההתאמות
            return "\n\n".join(_apply_set_op(m, op, reps, full_name) for m in matches)
        _same = len({(m["exercise_name"], m.get("template_name", "")) for m in matches}) == 1
        if _same:
            return (f"❓ ל-{full_name} יש {len(matches)} תרגילים זהים בשם *{matches[0]['exercise_name']}* "
                    f"באימון {matches[0].get('template_name','?')} — אי אפשר להבחין ביניהם מהפקודה.\n"
                    f"שנה אחד מהם ב-auto-fit, או כתוב *לשניהם* כדי שאוסיף לכולם.")
        lst = "\n".join(f"{i+1}. {m['exercise_name']} — אימון {m.get('template_name','?')}"
                        for i, m in enumerate(matches))
        _eg = matches[0].get("template_name", "")
        return (f"❓ '{exercise_hint}' מופיע ב-{len(matches)} אימונים אצל {full_name}:\n{lst}\n\n"
                f"שלח עם האימון, למשל: *עוד סט ב{exercise_hint} באימון {_eg}*")
    return _apply_set_op(matches[0], op, reps, full_name)


def _do_swap(assignment_id: str, new_ex_id: str, new_ex_name: str,
             old_name: str, full_name: str, tmpl_name: str = "") -> str:
    """מבצע את ההחלפה בפועל מול ה-API ומחזיר הודעת תוצאה."""
    result = _post("/coach/swap-training-exercise", {
        "assignment_id": str(assignment_id),
        "new_exercise_id": str(new_ex_id),
    })
    if result.get("status"):
        label = f" (אימון: {tmpl_name})" if tmpl_name else ""
        return f"✅ הוחלף תרגיל של *{full_name}*{label}\n🔁 {old_name} → {new_ex_name}"
    return f"❌ שגיאה: {result.get('message', 'שגיאה לא ידועה')}"


def execute_exercise_swap(
    user_id: str,
    full_name: str,
    old_exercise: str = "",
    new_exercise: str = "",
    direct_assignment_id: str = "",
    old_name_hint: str = "",
    direct_new_id: str = "",
    replace_muscle: str = "",
) -> str:
    """מבצע החלפת תרגיל.
    שלב א — זיהוי התרגיל הישן (assignment).
    שלב ב — בחירת התרגיל החדש:
       * direct_new_id → החלפה ישירה (דני בחר מספר מרשימת הספרייה).
       * new_exercise → חיפוש בספרייה והחלפה.
       * אחרת → זיהוי שריר התרגיל הישן + שליחת כל תרגילי השריר מהספרייה (EXERCISE_REPLACE).
    """
    # ── שלב א: זהה את התרגיל הישן ─────────────────────────────────────
    if direct_assignment_id:
        assignment_id = direct_assignment_id
        old_name = old_name_hint or direct_assignment_id
        tmpl_name = ""
    else:
        if not old_exercise:
            return "❌ חסר שם תרגיל"
        all_ex = _collect_user_exercises(user_id)
        candidates, group_label = swap_candidates(all_ex, old_exercise)
        if not candidates:
            _log_unmatched_exercise(old_exercise, full_name, kind="swap")
            return f"❌ לא מצאתי תרגיל '{old_exercise}' בתוכנית של {full_name}"

        if len(candidates) == 1 and not group_label:
            m = candidates[0]
            assignment_id, old_name, tmpl_name = m["assignment_id"], m["exercise_name"], m["template_name"]
        else:
            # כמה התאמות / שם שריר → בחר קודם את התרגיל הישן
            parts = "|".join(
                f"{m['assignment_id']}:{m['exercise_name']}:{m['template_name']}:{m['sets']}:{m['reps']}"
                for m in candidates[:12]
            )
            lines = "\n".join(
                f"{i+1}. {m['exercise_name']} (אימון: {m['template_name']})"
                for i, m in enumerate(candidates[:12])
            )
            if group_label:
                header = f"🔍 *{group_label}* — איזה תרגיל להחליף אצל {full_name}?"
            else:
                header = f"❓ איזה תרגיל להחליף אצל {full_name}?"
            new_hint = f"NEW={new_exercise}\n" if new_exercise else ""
            return (
                f"EXERCISE_CHOICE:{parts}\n{new_hint}{header}\n{lines}\n\nשלח *מספר* לבחירה."
            )

    # ── שלב ב: בחירת התרגיל החדש ──────────────────────────────────────
    # החלפה ישירה לפי מזהה (דני בחר מרשימת הספרייה)
    if direct_new_id:
        return _do_swap(assignment_id, direct_new_id, new_exercise or "תרגיל חדש",
                        old_name, full_name, tmpl_name)

    # לא צוין תרגיל חדש → שלח רשימת תרגילי השריר מהספרייה לבחירה
    if not new_exercise:
        if replace_muscle:
            _grps = _detect_muscle_groups(replace_muscle)
            muscle = _grps[0] if _grps else replace_muscle
        else:
            muscle = _exercise_group(old_name)
        if muscle:
            resp = _exercise_replace_list(assignment_id, old_name, full_name, muscle)
            if resp:
                return resp
        # אין שריר מזוהה → שאל פתוח
        return (
            f"EXERCISE_NEED_NEW:{assignment_id}:{old_name}\n"
            f"✅ מצאתי את *{old_name}* של {full_name}\n❓ במה להחליף?"
        )

    # צוין שם תרגיל חדש → חפש בספרייה
    lib_results = search_exercise_library(new_exercise)
    if not lib_results:
        return f"❌ לא מצאתי תרגיל '{new_exercise}' בספרייה"

    if len(lib_results) > 1:
        q_lower = new_exercise.strip().lower()
        exact = next((e for e in lib_results if e.get("exercise_name", "").lower() == q_lower), None)
        if exact:
            lib_results = [exact]
        else:
            names = [e.get("exercise_name", "") for e in lib_results[:8]]
            names_list = "\n".join(f"{i+1}. {n}" for i, n in enumerate(names))
            return (
                f"EXERCISE_OPTIONS:{new_exercise}||{'|'.join(names)}\n"
                f"❓ מצאתי כמה תרגילים עבור *{new_exercise}*:\n{names_list}\n\nשלח *בחר N*"
            )

    new_ex = lib_results[0]
    new_ex_id = str(new_ex.get("id", ""))
    if not new_ex_id:
        return f"❌ תרגיל '{new_exercise}' לא תקין"
    return _do_swap(assignment_id, new_ex_id, new_ex.get("exercise_name", new_exercise),
                    old_name, full_name, tmpl_name)


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
    swap_exercise       = "--swap-exercise" in args
    args = [a for a in args if a != "--swap-exercise"]
    old_exercise        = _pop_arg("--old-exercise")
    new_exercise        = _pop_arg("--new-exercise")
    assignment_id_cli   = _pop_arg("--assignment-id")
    old_name_cli        = _pop_arg("--old-name")
    ex_command_text     = _pop_arg("--command")
    new_exercise_id_cli = _pop_arg("--new-exercise-id")
    replace_muscle_cli  = _pop_arg("--replace-muscle")
    set_command_text    = _pop_arg("--set-command")

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

    if set_command_text:
        if user_id_override:
            uid, full_name = user_id_override, (name_override or user_id_override)
        elif name_override:
            uid, full_name, is_fuzzy = find_user(name_override)
            if not uid:
                print(f"NAME_NOT_FOUND:{name_override}"); sys.exit(0)
            if uid == "MULTIPLE":
                entries = [e.split("|", 1) for e in full_name.split(";")]
                print(f"NAME_OPTIONS:{'|'.join(e[0] for e in entries)}||{'|'.join(e[1] for e in entries)}")
                sys.exit(0)
        else:
            print("❌ חסר --name או --user-id"); sys.exit(1)
        _parsed = parse_set_command(set_command_text)
        if not _parsed:
            print("❌ לא זיהיתי פקודת סט. פורמט: 'מוסיף לך עוד סט בלחיצת חזה' / 'סט של 20 חזרות'")
            sys.exit(0)
        _op, _reps, _ex_hint = _parsed
        print(execute_set_command(uid, full_name, _op, _reps, _ex_hint, raw_text=set_command_text))
        sys.exit(0)

    if swap_exercise:
        if user_id_override:
            uid, full_name = user_id_override, (name_override or user_id_override)
        elif name_override:
            uid, full_name, is_fuzzy = find_user(name_override)
            if not uid:
                print(f"NAME_NOT_FOUND:{name_override}")
                sys.exit(0)
            if uid == "MULTIPLE":
                entries = [e.split("|", 1) for e in full_name.split(";")]
                ids   = "|".join(e[0] for e in entries)
                names = "|".join(e[1] for e in entries)
                print(f"NAME_OPTIONS:{ids}||{names}")
                sys.exit(0)
        else:
            print("❌ חסר --name או --user-id")
            sys.exit(1)
        # אם הגיע טקסט גולמי — חלץ ממנו (כמו בוט תזונה)
        if not old_exercise and not assignment_id_cli and ex_command_text:
            old_exercise, _parsed_new = parse_exercise_command(ex_command_text)
            if not new_exercise:
                new_exercise = _parsed_new
        if not old_exercise and not assignment_id_cli:
            print("❌ לא הצלחתי לזהות תרגיל בפקודה. פורמט: מחליף לך באימון X ב-Y")
            sys.exit(0)
        print(execute_exercise_swap(
            uid, full_name,
            old_exercise=old_exercise,
            new_exercise=new_exercise,
            direct_assignment_id=assignment_id_cli,
            old_name_hint=old_name_cli,
            direct_new_id=new_exercise_id_cli,
            replace_muscle=replace_muscle_cli,
        ))
        sys.exit(0)

    if not args:
        print("Usage: python3 autofit_api.py [--force] [--name <name>] <request>")
        sys.exit(1)
    request = " ".join(args)
    try:
        _result = execute_request(request, force=force,
                              name_override=name_override,
                              meal_override=meal_override,
                              food_override=food_override,
                              hint_override=hint_override,
                              user_id_override=user_id_override)
        # רשת ביטחון: לעולם אל תחזיר ריק — מחרוזת ריקה גורמת ל-node להציג
        # "✅ בוצע" כוזב בזמן ששום פעולה לא קרתה. תמיד יש הודעת מפלט ברורה.
        if _result is None or not str(_result).strip():
            print("❌ לא הצלחתי לעבד את הבקשה — בדוק שם מתאמן / שם מאכל / ארוחה ונסה שוב.")
            sys.exit(1)
        print(_result)
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)  # מלא ללוגים של Railway
        # סיבה קצרה ל-stdout כדי שדני יראה אותה בקבוצה (לא רק בלוגים)
        print(f"שגיאה בעיבוד הבקשה: {str(e)[:140]}")
        sys.exit(1)
