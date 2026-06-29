#!/usr/bin/env python3
# התאמת 56 תרגילי יוטיוב (של יובל) ל-.mov של הדרייב, לפי frame (pHash) + אורך.
import json, os
import imagehash
from PIL import Image

BASE = os.path.expanduser("~/Desktop/autofit-videos")
yt = json.load(open(f"{BASE}/yt_manifest.json"))       # id, vid, name, frame_q, dur
mov = [m for m in json.load(open(f"{BASE}/mov_manifest.json")) if m.get("ok")]

# pHash של ה-frame של כל תרגיל יוטיוב
for e in yt:
    fp = f"{BASE}/yt-frames/{e['id']}.jpg"
    e["ph"] = imagehash.phash(Image.open(fp).resize((320,180))) if os.path.exists(fp) else None

def mov_hashes(m):
    return [imagehash.hex_to_hash(h) for h in m.get("phash", [])]

# מטריצת עלות: לכל (תרגיל, mov) = מרחק pHash מינימלי בין frame היוטיוב ל-6 frames של ה-mov + עונש אורך
N, M = len(yt), len(mov)
COST = [[999]*M for _ in range(N)]
DIST = [[(99,99)]*M for _ in range(N)]
for i,e in enumerate(yt):
    if e["ph"] is None: continue
    for j,m in enumerate(mov):
        mh = mov_hashes(m)
        if not mh: continue
        pd = min(e["ph"]-h for h in mh)            # 0..64, מרחק האמינג
        dd = abs((e["dur"] or 0)-(m["dur"] or 0))  # פער אורך בשניות
        COST[i][j] = pd + min(dd,15)*0.7           # pHash דומיננטי, אורך כמסנן-עזר
        DIST[i][j] = (pd, round(dd,1))

# שיוך אופטימלי 1:1 (Hungarian אם יש scipy, אחרת חמדני)
try:
    from scipy.optimize import linear_sum_assignment
    import numpy as np
    ri, ci = linear_sum_assignment(np.array(COST))
    assign = {int(r):int(c) for r,c in zip(ri,ci)}
except Exception:
    assign = {}; usedm=set(); pairs=sorted([(COST[i][j],i,j) for i in range(N) for j in range(M)])
    for c,i,j in pairs:
        if i in assign or j in usedm: continue
        assign[i]=j; usedm.add(j)

rows=[]
for i,e in enumerate(yt):
    j = assign.get(i)
    if j is None: rows.append((e,None,(99,99))); continue
    rows.append((e, mov[j], DIST[i][j]))

rows.sort(key=lambda r: r[2][0])
out=[]
print(f"{'ex_id':6} {'pHash':5} {'Δsec':5} {'mov':28} exercise")
print("-"*90)
for e,m,(pd,dd) in rows:
    conf = "✅" if pd<=12 and dd<=6 else ("⚠️" if pd<=20 else "❌")
    mv = m["mov"] if m else "—"
    print(f"{e['id']:6} {pd:5} {dd:5} {mv[:28]:28} {conf} {e['name']}")
    out.append({"ex_id":e["id"],"ex_name":e["name"],"vid":e["vid"],"yt_dur":e["dur"],
                "mov":mv,"mov_dur":m["dur"] if m else None,"mp4":m["mp4"] if m else None,
                "phash_dist":pd,"dur_diff":dd,"conf":conf})
json.dump(out, open(f"{BASE}/MATCHES.json","w"), ensure_ascii=False, indent=2)
good=sum(1 for o in out if o["conf"]=="✅"); warn=sum(1 for o in out if o["conf"]=="⚠️"); bad=sum(1 for o in out if o["conf"]=="❌")
print(f"\n✅ ודאי: {good}   ⚠️ לבדוק: {warn}   ❌ חלש: {bad}")
EOF_GUARD = None
