#!/usr/bin/env python3
"""אוסף את כל החומר הגולמי ללקוח אחד: היסטוריית וואטסאפ (Green API) + תפריט/מדדים (AutoFit).
שמירה לקובץ — בסיס לבניית כרטיסיית לקוח. קריאה בלבד, אפס שליחה."""
import sys, json, requests, datetime
sys.path.insert(0, '/Users/danik./autofit-cloud')
import autofit_api as a

BIZ_ID = "7107645253"
BIZ_TOK = "414a50a0ae4e4e9eb0f2baa1faebd1a5fea87cd1866c434892"

def wa_history(phone, count=400):
    url = f"https://api.green-api.com/waInstance{BIZ_ID}/getChatHistory/{BIZ_TOK}"
    r = requests.post(url, json={"chatId": f"{phone}@c.us", "count": count}, timeout=40)
    return r.json() if r.ok else []

def main(phone, name_hint=""):
    msgs = wa_history(phone)
    convo = []
    for m in reversed(msgs):
        who = "דני" if m.get("type") == "outgoing" else "לקוח"
        t = m.get("textMessage") or (m.get("extendedTextMessage") or {}).get("text") or f"[{m.get('typeMessage','?')}]"
        ts = datetime.datetime.fromtimestamp(m.get("timestamp", 0)).strftime("%m-%d")
        convo.append(f"{ts} {who}: {t}")
    # AutoFit
    uid, full, fz = a.find_user(name_hint or phone)
    menu_lines = []
    weight = {}
    try:
        u = a.find_user(phone)
        meals = a.get_user_meals(uid)
        for m in (meals or []):
            nm = m.get("meal_name") or m.get("meal_type", "")
            foods = []
            for f in m.get("foods", m.get("mealFoods", [])):
                fn = f.get("food_name") or (f.get("food") or {}).get("food_name", "")
                q = f.get("quantity") or f.get("gram_value")
                subs = [(s.get("food_name") or (s.get("food") or {}).get("food_name","")) for s in f.get("subFoods", f.get("sub_foods", []))]
                foods.append(f"{fn} {q}ג" + (f" (תחליפים: {', '.join(subs)})" if subs else ""))
            menu_lines.append(f"[{nm}] " + " · ".join(foods))
    except Exception as e:
        menu_lines.append(f"(שגיאת תפריט: {e})")
    out = {
        "phone": phone, "name": full, "uid": uid,
        "n_messages": len(convo),
        "conversation": convo,
        "menu": menu_lines,
    }
    fn = f"/Users/danik./Desktop/client_raw_{phone}.json"
    json.dump(out, open(fn, "w"), ensure_ascii=False, indent=1)
    print(f"לקוח: {full} (uid {uid}) | {len(convo)} הודעות | נשמר ל-{fn}")
    print("\n=== תפריט ===")
    for l in menu_lines: print(" ", l[:140])
    print(f"\n=== שיחה (200 אחרונות) ===")
    for l in convo[-200:]:
        print(" ", l[:150])

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
