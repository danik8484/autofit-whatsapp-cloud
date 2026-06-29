#!/usr/bin/env python3
"""כלי אימות ידני: לכל לקוח מציג את הכרטיס הנוכחי + כל השיחה + התפריט בקריאה אחת.
משמש למעבר הידני לקוח-לקוח (לוודא שאין טעויות, ולהשלים). קריאה בלבד."""
import sys, json, requests, datetime, os
sys.path.insert(0, '/Users/danik./autofit-cloud')
import autofit_api as a
BIZ_ID, BIZ_TOK = "7107645253", "414a50a0ae4e4e9eb0f2baa1faebd1a5fea87cd1866c434892"
CARDS = os.path.expanduser('~/Desktop/client_cards')

def main(phone, name=""):
    # כרטיס נוכחי
    cp = f"{CARDS}/{phone}.md"
    print("═"*55, "\n📇 הכרטיס הנוכחי:\n")
    print(open(cp, encoding='utf-8').read() if os.path.exists(cp) else "(אין כרטיס)")
    # תפריט
    try:
        uid, full, _ = a.find_user(name or phone)
        if not uid or uid == "MULTIPLE": uid, full, _ = a.find_user(phone)
        print("\n" + "═"*55, "\n🍚 תפריט:")
        for m in (a.get_user_meals(uid) or []):
            nm = m.get("meal_name") or m.get("meal_type","")
            fs=[]
            for f in m.get("foods", m.get("mealFoods", [])):
                fn=f.get("food_name") or (f.get("food") or {}).get("food_name","")
                q=f.get("quantity") or f.get("gram_value")
                subs=[(s.get("food_name") or (s.get("food") or {}).get("food_name","")) for s in f.get("subFoods", f.get("sub_foods", []))]
                fs.append(f"{fn} {q}ג" + (f"(תח: {','.join(subs[:3])})" if subs else ""))
            print(f"  [{nm}] " + " · ".join(fs))
    except Exception as e:
        print("תפריט שגיאה:", e)
    # שיחה מלאה
    r = requests.post(f"https://api.green-api.com/waInstance{BIZ_ID}/getChatHistory/{BIZ_TOK}",
                      json={"chatId": f"{phone}@c.us", "count": 400}, timeout=40)
    msgs = r.json() if r.ok else []
    print("\n" + "═"*55, f"\n💬 שיחה מלאה ({len(msgs)} הודעות):")
    for m in reversed(msgs):
        who = "דני" if m.get("type")=="outgoing" else "לקוח"
        t = m.get("textMessage") or (m.get("extendedTextMessage") or {}).get("text") or f"[{m.get('typeMessage','')[:8]}]"
        ts = datetime.datetime.fromtimestamp(m.get("timestamp",0)).strftime("%m-%d")
        if t.strip(): print(f"  {ts} {who}: {t.replace(chr(10),' ')[:200]}")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv)>2 else "")
