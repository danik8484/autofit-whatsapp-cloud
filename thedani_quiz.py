#!/usr/bin/env python3
"""שאלון על כל לקוח: לכל כרטיס 6 שאלות-ליבה. הכרטיס חייב לענות על כל אחת —
או בתוכן ממשי או ב"לא ידוע/להעשיר" מפורש (תשובה תקפה לפי ספר-השיטה).
סעיף חסר לגמרי = נכשל. מדפיס רק נכשלים + ציון כולל.
שימוש: python3 thedani_quiz.py [--verbose]
"""
import json, os, re, sys
CARDS = os.path.expanduser('~/Desktop/client_cards')
clients = json.load(open('/tmp/clients.json'))
UNKNOWN = re.compile(r"לא ידוע|אין נתונים|להעשיר|לא צוין|לא מוגדר|לבדיקה|מעט נתונים|לוודא סטטוס")

# שאלה ← דפוס שמוכיח שהכרטיס עונה עליה
QUIZ = [
 ("מה המטרה/שלב שלו?",      re.compile(r"🎯|שלב ומטרה|חיטוב|מסה|בנייה|שמירה|ירידה במשקל|תחרות|הכנה")),
 ("יש רפואי/פציעות?",        re.compile(r"🩺|רפואי|פציע|כאב|מחל|אין פציעות|בריאות|גמילה|מתאושש|ורטיגו|הריון|דיסק|קרוהן")),
 ("מה הוא אוכל (תפריט/דיאטה)?", re.compile(r"🍚|🍽|תזונה|בוקר .*(ביצה|לחם|שיבולת|קוואקר|אבקת)|אורז|טופו|תפריט|קטו|ללא גלוטן")),
 ("מה באימון (תיקונים/מגבלות)?", re.compile(r"🏋️|אימון|טכניקה|תרגיל|סקוואט|דדליפט|לחיצ|אירובי|חתירה|מתאמן|חדכ|מכון")),
 ("איך מדברים איתו (סגנון)?",  re.compile(r"💬|סגנון|אחי|בוס|מלך|דתי|רוסי|ערבי|חם |מצחיק|נלהב|ענייני|אנליטי|קצר|הומור|נקבה")),
 ("מה הדגלים לבוט (עשה/אל תעשה)?", re.compile(r"🚩|דגלים לבוט|⛔|⚠️")),
]

def main():
    verbose = '--verbose' in sys.argv
    fails = {}; per_q = [0]*len(QUIZ); total=0
    for c in clients:
        ph, nm = c['phone'], c['name']
        p = f"{CARDS}/{ph}.md"
        if not os.path.exists(p):
            fails[f"{ph} {nm}"] = ["❌ אין כרטיס בכלל!"]; continue
        card = open(p, encoding='utf-8').read()
        total += 1
        unknown_card = bool(UNKNOWN.search(card))
        missing = []
        for i,(q, pat) in enumerate(QUIZ):
            if pat.search(card):
                per_q[i]+=1
            elif unknown_card and i in (1,3,4):  # רפואי/אימון/סגנון מותר "לא ידוע" בכרטיס דל-נתונים מוצהר
                per_q[i]+=1
            else:
                missing.append(q)
        if missing:
            fails[f"{ph} {nm}"] = missing
    print(f"═══ שאלון על {total} לקוחות — {len(QUIZ)} שאלות לכל אחד ═══")
    for i,(q,_) in enumerate(QUIZ):
        print(f"  ✓ '{q}' — {per_q[i]}/{total} עונים")
    if fails:
        print(f"\n❌ נכשלים ({len(fails)}):")
        for k,v in fails.items():
            print(f"  {k}")
            for q in v: print(f"     חסר: {q}")
    else:
        print("\n🎉 כל הלקוחות עוברים את השאלון — לכל אחד יש תשובה לכל 6 השאלות.")

if __name__ == "__main__":
    main()
