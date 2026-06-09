#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_exercise_search_test.py — מריץ את 101 הדוגמאות מ-exercise_test_cases.py.
טוען את תרגילי דני+רון פעם אחת (cache), ואז מריץ את כל ההתאמות מקומית.
לכל דוגמה: parse_exercise_command → match_exercises, ובודק התאמה.
"""
import sys, os, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

from autofit_api import parse_exercise_command, match_exercises, _collect_user_exercises
from exercise_test_cases import CASES

print("טוען תרגילים מה-API (פעם אחת לכל משתמש)...")
CACHE = {
    "23203": _collect_user_exercises("23203"),
    "22982": _collect_user_exercises("22982"),
}
print(f"  דני: {len(CACHE['23203'])} תרגילים | רון: {len(CACHE['22982'])} תרגילים\n")

USERS = {"D": [("23203", "דני")],
         "R": [("22982", "רון")],
         "B": [("23203", "דני"), ("22982", "רון")]}

PASS = 0
FAIL = 0
FAILS = []

print("═" * 72)
print("  מבחן חיפוש שמות תרגילים — 101 דוגמאות")
print("═" * 72)

for i, (msg, expected, who, note) in enumerate(CASES, 1):
    old, new = parse_exercise_command(msg)

    if not old:
        FAIL += 1
        FAILS.append((i, msg, "פרסור נכשל — לא חולץ שם תרגיל", expected, note))
        print(f"❌ {i:>3}. parse=∅  ←  \"{msg[:48]}\"")
        continue

    ok_all = True
    detail = []
    for uid, uname in USERS[who]:
        matches = match_exercises(CACHE[uid], old)
        names = [m["exercise_name"] for m in matches]
        if expected == "":
            found = (len(matches) == 0)
        else:
            found = any(expected in n for n in names)
        detail.append((uname, len(matches), found, names))
        if not found:
            ok_all = False

    if ok_all:
        PASS += 1
        d0 = detail[0]
        sample = d0[3][0][:42] if d0[3] else "0 (לא קיים ✓)"
        cnt = "/".join(str(x[1]) for x in detail)
        new_str = f"  +חדש:{new}" if new else ""
        print(f"✅ {i:>3}. [{old}]{new_str} → {cnt} תוצ' | {sample}")
    else:
        FAIL += 1
        bad = next(x for x in detail if not x[2])
        reason = f"'{expected}' לא נמצא אצל {bad[0]} (קיבל {bad[1]}: {[n[:30] for n in bad[3][:2]]})"
        FAILS.append((i, msg, reason, expected, note))
        print(f"❌ {i:>3}. [{old}] → {reason}")

print("\n" + "═" * 72)
pct = 100 * PASS // (PASS + FAIL)
print(f"  סה\"כ: {PASS+FAIL} | ✅ {PASS} | ❌ {FAIL} | {pct}%")
print("═" * 72)

if FAILS:
    print("\n  פירוט הכשלים:")
    for i, msg, reason, exp, note in FAILS:
        print(f"\n  ❌ #{i} ({note})")
        print(f"     הודעה: \"{msg}\"")
        print(f"     בעיה:  {reason}")

sys.exit(0)
