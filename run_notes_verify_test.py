#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_notes_verify_test.py — אימות שההערות (coach_exercise_notes) נשמרות בהחלפה.
עובר על *כל* התרגילים של דני שיש להם הערה, מחליף כל אחד דרך פונקציית הבוט
האמיתית (execute_exercise_swap), ומאמת שההערה נשמרה **תו-בתו** (כולל \\r\\n).
מחזיר כל תרגיל למצב המקורי.
"""
import sys, os, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

from autofit_api import (
    _get_user_assigned_trainings_ex, _get_training_templates_ex,
    _get_template_exercises_ex, _exercise_group, _library_for_muscle,
    execute_exercise_swap,
)

UID = "23203"
NAME = "דני"

print("בונה אינדקס תרגילים...")
INDEX = {}          # aid → (training_id, template_id)
EXES = []           # כל התרגילים
for tr in _get_user_assigned_trainings_ex(UID):
    tid = str(tr.get("training_id", ""))
    for tm in _get_training_templates_ex(UID, tid):
        tmid = str(tm.get("exercise_template_id", ""))
        for ex in _get_template_exercises_ex(UID, tid, tmid):
            INDEX[str(ex["id"])] = (tid, tmid)
            EXES.append(ex)

def read_ex(aid):
    tid, tmid = INDEX[aid]
    for ex in _get_template_exercises_ex(UID, tid, tmid):
        if str(ex["id"]) == aid:
            return ex
    return None

# כל התרגילים שיש להם הערה
WITH_NOTES = [ex for ex in EXES if ex.get("coach_exercise_notes")]
print(f"  {len(WITH_NOTES)} תרגילים עם הערות\n")

PASS = 0; FAIL = 0; FAILS = []
def chk(label, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; FAILS.append(f"{label} — {detail}")
    return cond

print("═" * 72)
print("  אימות שמירת הערות — דרך פונקציית הבוט (execute_exercise_swap)")
print("═" * 72)

for ex in WITH_NOTES:
    aid = str(ex["id"])
    name = ex["exercise_name"]
    orig_exid = str(ex["exercise_id"])
    orig_notes = ex["coach_exercise_notes"]
    orig_sets, orig_reps = ex["exercise_sets"], ex["exercise_reps"]

    muscle = _exercise_group(name)
    lib = _library_for_muscle(muscle, exclude_name=name) if muscle else []
    # יעד חייב להיות exercise_id שונה מהמקורי (כדי שההחלפה תיצור שינוי אמיתי)
    target = next((e for e in lib
                   if str(e["id"]) != orig_exid and e["exercise_name"] != name), None)
    if not target:
        chk(f"{aid} יש יעד החלפה", False, f"אין יעד לשריר {muscle}")
        print(f"  ⚠️  {name[:30]} — דילוג (אין יעד)")
        continue

    # החלפה דרך פונקציית הבוט
    res = execute_exercise_swap(UID, NAME, direct_assignment_id=aid,
                                direct_new_id=str(target["id"]),
                                old_name_hint=name, new_exercise=target["exercise_name"])
    after = read_ex(aid)

    swapped = chk(f"{aid} ההחלפה בוצעה", str(after["exercise_id"]) == str(target["id"]),
                  f"exid={after['exercise_id']}")
    notes_ok = chk(f"{aid} הערה נשמרה בדיוק",
                   after.get("coach_exercise_notes") == orig_notes,
                   f"[{orig_notes!r}] → [{after.get('coach_exercise_notes')!r}]")
    sets_ok = chk(f"{aid} סטים+חזרות", after["exercise_sets"] == orig_sets and after["exercise_reps"] == orig_reps)

    # שחזור
    execute_exercise_swap(UID, NAME, direct_assignment_id=aid,
                          direct_new_id=orig_exid, old_name_hint=after["exercise_name"])
    final = read_ex(aid)
    restored = chk(f"{aid} שוחזר + הערה תקינה",
                   str(final["exercise_id"]) == orig_exid and final.get("coach_exercise_notes") == orig_notes,
                   f"exid={final['exercise_id']} notes_ok={final.get('coach_exercise_notes')==orig_notes}")

    mark = "✅" if (swapped and notes_ok and sets_ok and restored) else "❌"
    nlines = (orig_notes or "").count("\n") + 1
    print(f"  {mark} {name[:32]:32} | הערה {nlines} שורות נשמרה={'כן' if notes_ok else 'לא!'}")

print("\n" + "═" * 72)
pct = 100 * PASS // (PASS + FAIL) if (PASS + FAIL) else 0
print(f"  תרגילים: {len(WITH_NOTES)} | בדיקות: {PASS+FAIL} | ✅ {PASS} | ❌ {FAIL} | {pct}%")
print("═" * 72)
if FAILS:
    print("\n  כשלים:")
    for f in FAILS[:20]:
        print(f"   • {f}")
sys.exit(1 if FAIL else 0)
