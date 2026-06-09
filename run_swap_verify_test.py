#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_swap_verify_test.py — אימות אמיתי שההחלפה עובדת ושומרת נתונים.
לכל מקרה: צילום-מצב → החלפה אמיתית → אימות (השם/exid השתנו, סטים/חזרות/min/הערות נשמרו)
          → שחזור לתרגיל המקורי → אימות שחזור מלא.
פועל על דני (23203) בלבד, ומחזיר הכל למצב ההתחלתי.
"""
import sys, os, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

from autofit_api import (
    _get_user_assigned_trainings_ex, _get_training_templates_ex,
    _get_template_exercises_ex, _post,
)

UID = "23203"

# בנה אינדקס: aid → (training_id, template_id) פעם אחת (למהירות)
print("בונה אינדקס תרגילים...")
INDEX = {}
for tr in _get_user_assigned_trainings_ex(UID):
    tid = str(tr.get("training_id", ""))
    for tm in _get_training_templates_ex(UID, tid):
        tmid = str(tm.get("exercise_template_id", ""))
        for ex in _get_template_exercises_ex(UID, tid, tmid):
            INDEX[str(ex.get("id", ""))] = (tid, tmid)
print(f"  {len(INDEX)} תרגילים\n")

FIELDS = ("exercise_sets", "exercise_reps", "exercise_min_reps", "coach_exercise_notes")

def read_ex(aid):
    """קורא את מצב התרגיל הנוכחי לפי assignment_id (re-fetch אמיתי)."""
    tid, tmid = INDEX[aid]
    for ex in _get_template_exercises_ex(UID, tid, tmid):
        if str(ex.get("id", "")) == aid:
            return ex
    return None

def snap(e):
    return {
        "name": e["exercise_name"],
        "exid": str(e["exercise_id"]),
        "sets": e["exercise_sets"],
        "reps": e["exercise_reps"],
        "min": e["exercise_min_reps"],
        "notes": e.get("coach_exercise_notes"),
    }

def do_swap(aid, new_exid):
    return _post("/coach/swap-training-exercise",
                 {"assignment_id": aid, "new_exercise_id": str(new_exid)}).get("status")

# (aid, exid-יעד-להחלפה, תיאור) — היעד חייב להיות שונה מהמקורי, אותו שריר
CASES = [
    ("1069535", "39089", "חזה reps=20, עם הערות"),
    ("1069537", "39042", "חזה reps=24, עם הערות"),
    ("1069561", "39105", "רגליים סקוואט sets=2, הערה רב-שורתית"),
    ("1069595", "39099", "בטן reps=None (שדה ריק), עם הערות"),
    ("1069553", "39074", "גב reps=8 min=20, עם הערות"),
    ("1069555", "39054", "גב מתח — בלי הערות (null נשמר null)"),
]

PASS = 0; FAIL = 0; FAILS = []
def chk(label, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"    ✅ {label}")
    else: FAIL += 1; FAILS.append(f"{label} — {detail}"); print(f"    ❌ {label} — {detail}")

print("═" * 72)
print("  אימות החלפה אמיתית + שמירת סטים/חזרות/הערות")
print("═" * 72)

for aid, target_exid, desc in CASES:
    print(f"\n▶ {desc}  (aid={aid})")
    before = snap(read_ex(aid))
    print(f"    לפני: {before['name'][:35]} | sets={before['sets']} reps={before['reps']} "
          f"min={before['min']} notes={(before['notes'] or '∅')[:18]}")

    # החלפה אמיתית
    if before["exid"] == target_exid:
        chk("יעד שונה מהמקורי", False, "היעד זהה למקור — דלג")
        continue
    ok_swap = do_swap(aid, target_exid)
    after = snap(read_ex(aid))

    # אימות: ההחלפה קרתה
    chk("ההחלפה בוצעה (status)", ok_swap is True)
    chk("exercise_id השתנה", after["exid"] == target_exid,
        f"{before['exid']}→{after['exid']} (ציפי {target_exid})")
    chk("השם השתנה", after["name"] != before["name"],
        f"{before['name']} == {after['name']}")
    # אימות: הנתונים נשמרו
    chk("סטים נשמרו", after["sets"] == before["sets"], f"{before['sets']}→{after['sets']}")
    chk("חזרות נשמרו", after["reps"] == before["reps"], f"{before['reps']}→{after['reps']}")
    chk("min-reps נשמרו", after["min"] == before["min"], f"{before['min']}→{after['min']}")
    chk("📝 הערות נשמרו", after["notes"] == before["notes"],
        f"[{before['notes']}] → [{after['notes']}]")

    # שחזור
    do_swap(aid, before["exid"])
    final = snap(read_ex(aid))
    chk("שוחזר התרגיל המקורי", final["exid"] == before["exid"] and final["name"] == before["name"],
        f"exid={final['exid']}")
    chk("שחזור: הערות תקינות", final["notes"] == before["notes"])

print("\n" + "═" * 72)
pct = 100 * PASS // (PASS + FAIL) if (PASS + FAIL) else 0
print(f"  סה\"כ: {PASS+FAIL} | ✅ {PASS} | ❌ {FAIL} | {pct}%")
print("═" * 72)
if FAILS:
    print("\n  כשלים:")
    for f in FAILS:
        print(f"   • {f}")
sys.exit(1 if FAIL else 0)
