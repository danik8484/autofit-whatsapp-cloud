#!/usr/bin/env python3
"""בונה כרטיסיות לקוח v2 — רוטציית מודלים ב-Groq (מכסות נפרדות לכל מודל),
פרומפט מקוצר, קירור אוטומטי לפי retry-after, resumable. קריאה בלבד, אפס שליחה."""
import sys, os, json, time, requests, datetime
sys.path.insert(0, '/Users/danik./autofit-cloud')
import autofit_api as a

BIZ_ID, BIZ_TOK = "7107645253", "414a50a0ae4e4e9eb0f2baa1faebd1a5fea87cd1866c434892"
GROQ_KEY = open('/tmp/gk').read().strip()
OUT = os.path.expanduser('~/Desktop/client_cards')
os.makedirs(OUT, exist_ok=True)

MODELS = [
    "openai/gpt-oss-120b",       # עברית הכי טובה מבין החינמיים
    "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "qwen/qwen3-32b",            # אחרון — עברית חלשה, נוטה להמציא
]
cooldown = {}  # model -> unix time עד מתי בקירור

PROMPT = '''אתה בונה כרטיסיית לקוח עבור מאמן כושר (דני) כדי שבוט יוכל לדבר עם הלקוח בדיוק כמו דני, עם הקשר אישי מלא.
קרא בעיון את כל השיחה והתפריט והפק כרטיס חד וספציפי בעברית. **חפש במיוחד את הדברים העדינים שקל לפספס:**
- 🎯 שלב ומטרה (חיטוב/בנייה/שמירה, יעד, אירוע-יעד)
- 🩺 רפואי/מגבלות: פציעות, מחלות, **הפרעות אכילה**, אלרגיות, כשרות/צמחוני, תרופות
- 🚨 רגישויות/דפוסים: **האם נפל/חרג? איך הגיב רגשית (בושה/אכזבה/ספירלה)? אובססיה/חרדה ספציפית? בקשות חוזרות? מה לוחץ על דני?** — וכן **איך דני מטפל בזה אצלו** (מרגיע/בולם/דוחף/מפרגן)
- 🍽️ העדפות אוכל ספציפיות: מה אוהב/שונא/מוותר עליו, מאכלים שהזכיר, דפוסי חריגה (מתי/למה נופל), קפאין/ציט
- 🏋️ אימון: התוכנית, **חולשות טכניקה ספציפיות שדני תיקן, תרגילים שלא יכול לעשות / שצריך לדלג / מגבלת ציוד**
- ✈️ אירועים שהוזכרו (חופשה/אירוע/נסיעה/מבחנים/עבודה) + מתי
- 💬 סגנון וצחוקים: ביטויים קבועים שלו, אימוג'ים, כינויים, צורת דיבור
- 🚩 דגלים לבוט (מה אסור / מה דורש זהירות אצלו)
ספציפי לאדם הזה, בלי משפטים גנריים. אל תכתוב חשיבה, רק את הכרטיס.
⚠️⚠️ קריטי: כתוב **אך ורק עובדות שמופיעות במפורש בשיחה או בתפריט**. **אסור להמציא, לנחש, או להשלים פרטים.** לא נאמר → לא כותבים. עדיף קצר ומדויק מארוך עם המצאות.'''

def wa_history(phone, count=200):
    try:
        r = requests.post(f"https://api.green-api.com/waInstance{BIZ_ID}/getChatHistory/{BIZ_TOK}",
                          json={"chatId": f"{phone}@c.us", "count": count}, timeout=40)
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
        return None, name, ""

def groq_card(menu, convo):
    content = f"{PROMPT}\n\nתפריט:\n{menu[:900]}\n\nשיחה:\n{convo[:4200]}"
    now = time.time()
    for model in MODELS:
        if cooldown.get(model, 0) > now:
            continue
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                              headers={"Authorization": f"Bearer {GROQ_KEY}"},
                              json={"model": model, "messages": [{"role": "user", "content": content}],
                                    "temperature": 0.25, "max_tokens": 1000}, timeout=90)
            if r.ok:
                txt = r.json()["choices"][0]["message"]["content"]
                # qwen3 מחזיר לפעמים <think> — לחתוך
                if "</think>" in txt:
                    txt = txt.split("</think>")[-1].strip()
                return txt, model
            if r.status_code == 429:
                ra = int(float(r.headers.get("retry-after", 60)))
                cooldown[model] = now + min(ra, 3600) + 5
                continue
        except Exception:
            cooldown[model] = now + 60
            continue
    return None, None

def main():
    clients = json.load(open('/tmp/clients.json'))
    done = set(f[:-3] for f in os.listdir(OUT) if f.endswith('.md'))
    todo = [c for c in clients if c['phone'] not in done]
    print(f"סהכ {len(clients)} | בנויים {len(done)} | נותרו {len(todo)}", flush=True)
    fails = 0
    for i, c in enumerate(todo, 1):
        ph, nm = c['phone'], c['name'].replace(' ליווי', '').strip()
        msgs = wa_history(ph)
        convo = []
        for m in reversed(msgs):
            who = "דני" if m.get("type") == "outgoing" else "לקוח"
            t = m.get("textMessage") or (m.get("extendedTextMessage") or {}).get("text") or ""
            if t: convo.append(f"{who}: {t}")
        convo_s = "\n".join(convo[-100:])
        uid, full, menu = get_menu(nm, ph)
        if len(convo_s) < 60 and not menu:
            card, used = "(אין מספיק נתונים — לקוח לא פעיל בוואטסאפ ואין תפריט)", "skip"
        else:
            card, used = groq_card(menu, convo_s)
            if card is None:
                # כל המודלים בקירור — חכה לקצר ביותר ונסה שוב פעם אחת
                wait = max(5, min(cooldown.values()) - time.time())
                print(f"  ⏸️ כל המודלים בקירור, ממתין {int(wait)}s", flush=True)
                time.sleep(min(wait, 900))
                card, used = groq_card(menu, convo_s)
            if card is None:
                fails += 1
                print(f"  [{i}/{len(todo)}] ❌ {nm} — נכשל (יושלם בסבב הבא)", flush=True)
                continue  # לא כותבים קובץ → resumable יתפוס בסבב הבא
        header = (f"# 🪪 כרטיסיית לקוח — {nm}\n"
                  f"*נבנה {datetime.date.today()} | טלפון {ph} | uid {uid} | "
                  f"{len(convo)} הודעות | מודל {used} | מתעדכן בלייב*\n\n")
        open(f"{OUT}/{ph}.md", "w", encoding="utf-8").write(header + card)
        print(f"  [{i}/{len(todo)}] ✅ {nm} ({used.split('/')[-1]})", flush=True)
        time.sleep(3)
    print(f"סיום. כשלונות שנותרו: {fails}", flush=True)

if __name__ == "__main__":
    main()
