#!/usr/bin/env python3
"""מבחן-פחד / סורק-פערים: לכל לקוח מושך את כל הצ'אט, מצליב מול הכרטיס לפי קבוצות
מילות-מפתח קריטיות, ומסמן כל נושא שמופיע בצ'אט אבל לא מכוסה בכרטיס.
מטרה: לתפוס כל דבר שפוספס. מטמין צ'אטים ב-/tmp/chatcache. קריאה בלבד.
שימוש: python3 thedani_examine.py            # סורק את כולם, מדפיס רק חשודים
       N=20 python3 thedani_examine.py       # מגביל ל-N ראשונים (debug)
"""
import json, os, re, requests, time
BIZ_ID, BIZ_TOK = "7107645253", "414a50a0ae4e4e9eb0f2baa1faebd1a5fea87cd1866c434892"
CARDS = os.path.expanduser('~/Desktop/client_cards')
CACHE = '/tmp/chatcache'; os.makedirs(CACHE, exist_ok=True)
clients = json.load(open('/tmp/clients.json'))

# קבוצות: (שם, מילים-שמחפשים-בצ'אט-של-הלקוח, מילים-שמוכיחות-שהכרטיס-מכסה)
GROUPS = [
 ("🩺 רפואי/פציעה", r"\b(כואב|כאב|פציע|נפצע|מתיחה בשריר|נתפס לי|דלקת|ניתוח|גבס|נקע|שבר|רופא|פיזיו|סחרחור|ורטיגו|מחלה|חולה|וירוס|חום \d|מצונן)\b",
   r"פציע|כאב|רפואי|🩺|מתאושש|מחלה|חולה|ורטיגו|כתף|גב תחתון|ברכי|המסטרינג|צלע|עקב|אגן|מנוחה מהאזור"),
 ("🥗 דיאטה מיוחדת", r"\b(טבעוני|צמחוני|ללא גלוטן|צליאק|לקטוז|אלרגי|אלרגיה|כשר|קוטו|קיטו|קטו)\b",
   r"טבעוני|צמחוני|ללא גלוטן|לקטוז|אלרגי|כשר|קטו|דל-פחמימה|דג"),
 ("✈️ אירוע/נסיעה", r"\b(חתונה|מתחתן|טס|טיסה|בחו.ל|חופש|נופש|מילואים|בסיס|בשטח|אירוע)\b",
   r"חתונה|טס|חו.ל|חופש|נופש|מילואים|בסיס|בשטח|אירוע|✈|🪖|טיול|נסיע"),
 ("🏆 תחרות/הכנה", r"\b(תחרות|במה|במות|ריקון|העמסה|פיתוח גוף|מר ישראל|קונטסט|prep)\b",
   r"תחרות|קונטסט|במות|ריקון|העמסה|הכנה|🏆"),
 ("⛔ חומרים", r"\b(טסטו|טסטוסטרון|סטרואיד|חומרים|הורמון|HCG|בולדנון|אנאבולי|ציקל)\b",
   r"חומרים|טסטוסטרון|HCG|בולדנון|הורמון|ספורטאי משופר|⛔"),
 ("🤰 הריון", r"\b(בהריון|הריון|בהיריון|הנקה|לידה|ילדתי)\b",
   r"הריון|הנקה|לידה|הריונית"),
 ("🧠 רגיש פסיכולוגי", r"\b(הפרעת אכילה|דיכאון|חרדה|מתבייש|מתביישת|בושה|נפילה|נפלתי|מתייאש|לוותר על)\b",
   r"הפרעת אכילה|חרד|דיכאון|בושה|מתבייש|אובססי|סטרס|יו-יו|רגיש|פסיכולוג|להרגיע"),
]
TOKS = {g[0]: (re.compile(g[1]), re.compile(g[2])) for g in GROUPS}

def chat_text(ph):
    cf = f"{CACHE}/{ph}.txt"
    if os.path.exists(cf):
        return open(cf, encoding='utf-8').read()
    try:
        r = requests.post(f"https://api.green-api.com/waInstance{BIZ_ID}/getChatHistory/{BIZ_TOK}",
                          json={"chatId": f"{ph}@c.us", "count": 400}, timeout=40)
        msgs = r.json() if r.ok else []
    except Exception:
        msgs = []
    lines=[]
    for m in msgs:
        if m.get("type") != "incoming":   # רק הודעות הלקוח — מה שהוא אמר על עצמו
            continue
        t = m.get("textMessage") or (m.get("extendedTextMessage") or {}).get("text") or ""
        if t.strip(): lines.append(t.replace(chr(10),' '))
    txt = "\n".join(lines)
    open(cf,'w',encoding='utf-8').write(txt)
    time.sleep(0.15)
    return txt

def main():
    lim = int(os.environ.get('N', len(clients)))
    flags=0; scanned=0; nochat=0
    for c in clients[:lim]:
        ph, nm = c['phone'], c['name']
        cardp = f"{CARDS}/{ph}.md"
        card = open(cardp, encoding='utf-8').read() if os.path.exists(cardp) else ""
        chat = chat_text(ph); scanned+=1
        if not chat.strip(): nochat+=1
        hits=[]
        for label,(chatre, cardre) in TOKS.items():
            cm = chatre.findall(chat)
            if cm and not cardre.search(card):
                uniq = sorted(set(x if isinstance(x,str) else x[0] for x in cm))
                hits.append(f"{label}: {','.join(uniq[:4])}")
        if hits:
            flags+=1
            print(f"⚠️ {ph} | {nm}")
            for h in hits: print(f"     {h}")
    print(f"\n═══ נסרקו {scanned} | חשודים {flags} | ללא צ'אט {nochat} ═══")

if __name__ == "__main__":
    main()
