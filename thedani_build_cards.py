#!/usr/bin/env python3
"""בונה כרטיסיית לקוח לכל לקוחות הליווי. resumable + rate-limited + עמיד לשגיאות.
מקור: היסטוריית וואטסאפ (Green API) + תפריט AutoFit → חילוץ Groq → קובץ .md.
קריאה בלבד מהלקוחות, אפס שליחה."""
import sys, os, json, time, requests, datetime
sys.path.insert(0, '/Users/danik./autofit-cloud')
import autofit_api as a

BIZ_ID, BIZ_TOK = "7107645253", "414a50a0ae4e4e9eb0f2baa1faebd1a5fea87cd1866c434892"
GROQ_KEY = open('/tmp/gk').read().strip()
OUT = os.path.expanduser('~/Desktop/client_cards')
os.makedirs(OUT, exist_ok=True)

PROMPT = '''אתה בונה כרטיסיית לקוח עבור מאמן כושר (דני) כדי שבוט יוכל לדבר עם הלקוח בדיוק כמו דני, עם הקשר אישי מלא.
קרא את התפריט ואת השיחה והפק כרטיס תמציתי וחד בעברית. **חלץ ספציפית** (לא כללי!):
- 🎯 שלב ומטרה (חיטוב/בנייה, יעד, אירוע-יעד)
- 🩺 רפואי/מגבלות: פציעות, מחלות, **הפרעות אכילה**, אלרגיות, כשרות/צמחוני
- 🧠 פסיכולוגיה — **מה הלקוח עושה שוב ושוב? על מה הוא חרד/אובססיבי? מה לוחץ על דני? איך הכי נכון לדבר איתו** (להרגיע / לדחוף / פרגון / ישיר)
- 💬 סגנון וצחוקים: ביטויים קבועים, אימוג'ים, כינויים, צורת דיבור
- 🏋️ אימון: תוכנית, **חולשות טכניקה ספציפיות**, תרגילים שלא יכול/בעייתיים
- ✈️ אירועים שהוזכרו (חופשה/אירוע/נסיעה) + תאריכים
- 🍚 תזונה: העדפות, מאכלים אוהב/שונא, דפוסי חריגה
- 🚩 דגלים לבוט
אם אין מידע לסעיף — דלג עליו. היה ספציפי לאדם הזה, לא תשובות גנריות.'''

def wa_history(phone, count=300):
    url = f"https://api.green-api.com/waInstance{BIZ_ID}/getChatHistory/{BIZ_TOK}"
    try:
        r = requests.post(url, json={"chatId": f"{phone}@c.us", "count": count}, timeout=40)
        return r.json() if r.ok else []
    except Exception:
        return []

def get_menu(name, phone):
    try:
        uid, full, _ = a.find_user(name) if name else a.find_user(phone)
        if not uid or uid == "MULTIPLE":
            uid, full, _ = a.find_user(phone)
        meals = a.get_user_meals(uid) if uid and uid != "MULTIPLE" else []
        lines = []
        for m in (meals or []):
            nm = m.get("meal_name") or m.get("meal_type", "")
            fs = []
            for f in m.get("foods", m.get("mealFoods", [])):
                fn = f.get("food_name") or (f.get("food") or {}).get("food_name", "")
                q = f.get("quantity") or f.get("gram_value")
                fs.append(f"{fn} {q}ג")
            lines.append(f"[{nm}] " + " · ".join(fs))
        return uid, full, "\n".join(lines)
    except Exception as e:
        return None, name, f"(אין תפריט: {e})"

def groq_card(menu, convo):
    body = {"model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": f"{PROMPT}\n\nתפריט:\n{menu}\n\nשיחה:\n{convo[:6500]}"}],
            "temperature": 0.25, "max_tokens": 1100}
    for attempt in range(4):
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                              headers={"Authorization": f"Bearer {GROQ_KEY}"}, json=body, timeout=70)
            if r.ok:
                return r.json()["choices"][0]["message"]["content"]
            if r.status_code == 429:
                time.sleep(8 * (attempt + 1)); continue
            return f"(Groq {r.status_code})"
        except Exception:
            time.sleep(4)
    return "(Groq failed)"

def main():
    clients = json.load(open('/tmp/clients.json'))
    done = set(f[:-3] for f in os.listdir(OUT) if f.endswith('.md'))
    print(f"סהכ {len(clients)} לקוחות, כבר {len(done)} בנויים")
    for i, c in enumerate(clients, 1):
        ph, nm = c['phone'], c['name'].replace(' ליווי', '').strip()
        safe = ph
        if safe in done:
            continue
        msgs = wa_history(ph)
        convo = []
        for m in reversed(msgs):
            who = "דני" if m.get("type") == "outgoing" else "לקוח"
            t = m.get("textMessage") or (m.get("extendedTextMessage") or {}).get("text") or ""
            if t: convo.append(f"{who}: {t}")
        convo_s = "\n".join(convo[-130:])
        uid, full, menu = get_menu(nm, ph)
        if len(convo_s) < 40 and "אין תפריט" in menu:
            card = "(אין מספיק נתונים)"
        else:
            card = groq_card(menu, convo_s)
        header = f"# 🪪 כרטיסיית לקוח — {nm}\n*נבנה {datetime.date.today()} | טלפון {ph} | uid {uid} | {len(convo)} הודעות | מתעדכן בלייב*\n\n"
        open(f"{OUT}/{safe}.md", "w", encoding="utf-8").write(header + card)
        print(f"  [{i}/{len(clients)}] ✅ {nm} ({len(convo)} הוד')")
        time.sleep(2.5)  # rate-limit ל-Groq + עדינות ל-Green API
    print("סיום.")

if __name__ == "__main__":
    main()
