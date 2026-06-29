#!/usr/bin/env python3
"""עדכון כרטיסיית לקוח בלייב מהודעה חדשה. נקרא ע"י הבוט (index.js) על כל הודעה.
פילטר-מילים זול קודם → רק אם יש פוטנציאל לעובדה חדשה, קוראים ל-Groq לעדכון. resumable, לא חוסם.
שימוש: python3 thedani_update_card.py <phone> "<who>:<message>"
"""
import sys, os, json, time, requests, datetime, re

CARDS = os.path.expanduser('~/Desktop/client_cards')   # בענן: /data/client_cards
GROQ_KEY = open('/tmp/gk').read().strip() if os.path.exists('/tmp/gk') else os.environ.get('GROQ_API_KEY', '')

# מילות-טריגר שמרמזות על עובדה חדשה ששווה לתעד (אחרת מדלגים — חוסך Groq)
TRIGGERS = re.compile(
    r'חופש|נוסע|טיול|חתונ|אירוע|מסיב|כואב|כאב|פציע|נקע|נתפס|אלרגי|רגיש|לא אוכל|צמחוני|טבעוני|כשר|'
    r'מחל|חולה|ניתוח|תרופ|הפרע|אסור לי|לא יכול|קשה לי|מתחיל עבוד|התחלתי לעבוד|עברתי דירה|'
    r'פצוע|שריר|גב|כתף|ברך|מרפק|צוואר|דיאט|חזרתי מ|טס ל|בחו'
)

def load_card(phone):
    p = f"{CARDS}/{phone}.md"
    return (p, open(p, encoding='utf-8').read()) if os.path.exists(p) else (p, None)

def groq_extract_fact(card, msg):
    prompt = f'''להלן כרטיסיית לקוח של מאמן והודעה חדשה מהלקוח/מהמאמן.
האם בהודעה יש **עובדה חדשה וקבועה** ששווה להוסיף לכרטיס (פציעה, חופשה/אירוע עתידי, העדפת אוכל, מגבלה, מחלה, שינוי בחיים, רגישות)?
אם כן — החזר שורה אחת קצרה בעברית בפורמט: "סעיף | העובדה". הסעיפים: רפואי, אירוע, תזונה, פסיכולוגיה, אימון.
אם אין עובדה חדשה קבועה (סתם דיווח שגרתי/אישור/שאלה) — החזר בדיוק: NONE.

כרטיס (תקציר):
{card[:1500]}

הודעה חדשה:
{msg[:400]}'''
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}"},
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.1, "max_tokens": 120}, timeout=30)
        if r.ok:
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    return "NONE"

def append_log(path, card, fact):
    line = f"- {datetime.date.today()}: {fact}"
    if "📓 יומן למידה" in card:
        card = card.replace("## 📓 יומן למידה", f"## 📓 יומן למידה\n{line}", 1)
    else:
        card = card.rstrip() + f"\n\n## 📓 יומן למידה (מתעדכן בלייב)\n{line}\n"
    open(path, "w", encoding="utf-8").write(card)

def main(phone, msg):
    if not TRIGGERS.search(msg):
        return  # אין טריגר — לא נוגעים (חוסך Groq, לא חוסם את הבוט)
    path, card = load_card(phone)
    if card is None:
        return  # אין כרטיס עדיין
    fact = groq_extract_fact(card, msg)
    if fact and fact != "NONE" and "|" in fact:
        append_log(path, card, fact.replace("\n", " ")[:160])
        print(f"📝 עודכן כרטיס {phone}: {fact[:80]}")

if __name__ == "__main__":
    if len(sys.argv) >= 3:
        main(sys.argv[1], sys.argv[2])
