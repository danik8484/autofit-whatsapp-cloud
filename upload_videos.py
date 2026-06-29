#!/usr/bin/env python3
# מוריד כל סרטון יוטיוב של יובל (android client) → remux faststart → מעלה כ-Upload ל-cdn דרך exercise-update.
# כל תרגיל שומר את הסרטון שלו עצמו (אפס מיפוי). resumable + אימות. גיבוי ב-BACKUP_youtube_56.json.
import json, os, subprocess, requests, re, time, sys

FF = "node_modules/ffmpeg-static/ffmpeg"
BASE = os.path.expanduser("~/Desktop/autofit-videos")
B = "https://chat.auto-fit.co.il"
tok = json.load(open("session.json")); TOKEN = tok.get("token") or tok.get("jwt")
H = {"Authorization": f"Bearer {TOKEN}"}

lib = {str(i["id"]): i for i in json.load(open("/tmp/exercises.json"))}
yt = json.load(open(f"{BASE}/yt_manifest.json"))   # id, vid, name
STATE = f"{BASE}/upload_state.json"
done = json.load(open(STATE)) if os.path.exists(STATE) else {}

ONLY = sys.argv[1:] if len(sys.argv) > 1 else None  # אופציונלי: רשימת ex_id לבדיקה

def process(e):
    eid, vid = str(e["id"]), e["vid"]
    it = lib.get(eid, {})
    raw = f"/tmp/yt_{eid}.mp4"; fs = f"/tmp/fs_{eid}.mp4"
    for p in (raw, fs):
        if os.path.exists(p): os.remove(p)
    # 1. download
    r = subprocess.run([sys.executable,"-m","yt_dlp","--ffmpeg-location",FF,
        "--extractor-args","youtube:player_client=android","-f","18/best[height<=720]/best",
        "-o",raw,f"https://www.youtube.com/watch?v={vid}"], capture_output=True, text=True)
    if not os.path.exists(raw) or os.path.getsize(raw) < 50000:
        return {"ok":False,"stage":"download","err":r.stderr[-200:]}
    # 2. remux faststart (no re-encode)
    subprocess.run([FF,"-y","-i",raw,"-c","copy","-movflags","+faststart",fs], capture_output=True)
    if not os.path.exists(fs) or os.path.getsize(fs) < 50000:
        fs = raw  # fallback
    # 3. upload via exercise-update (multipart) — preserve all existing fields
    data = {"id": eid,
            "exercise_name": it.get("exercise_name", e.get("name","")),
            "exercise_cat_id": str(it.get("exercise_cat_id","")),
            "description": it.get("description","") or "",
            "video_type": "Upload"}
    with open(fs,"rb") as fh:
        files = {"video": (f"{eid}.mp4", fh, "video/mp4")}
        up = requests.post(f"{B}/coach/exercise-update", headers=H, data=data, files=files, timeout=180)
    try: uj = up.json()
    except: uj = {"raw": up.text[:200]}
    if not (up.status_code==200 and uj.get("status")):
        return {"ok":False,"stage":"upload","http":up.status_code,"resp":uj}
    # 4. verify: re-read library, check Upload + cdn URL reachable + faststart
    time.sleep(1)
    chk = requests.post(f"{B}/coach/training-list", headers=H,
        json={"type":"Exercise","search":"","exercise_category":""}, timeout=30).json()
    items = chk.get("data",{}).get("items",[]) if isinstance(chk.get("data"),dict) else []
    new = next((x for x in items if str(x["id"])==eid), None)
    nurl = new.get("video") if new else None
    vt = new.get("video_type") if new else None
    verify = {"video_type":vt, "url":nurl}
    if nurl and "cdn.auto-fit" in nurl:
        hr = requests.get(nurl, headers={"Range":"bytes=0-65535","User-Agent":"Mozilla/5.0"}, timeout=30)
        front = hr.content
        verify["http"] = hr.status_code
        verify["faststart"] = b"moov" in front[:65536]
        # save compressed copy as backup
        bdir = f"{BASE}/uploaded"; os.makedirs(bdir, exist_ok=True)
        subprocess.run(["cp", fs, f"{bdir}/{eid}.mp4"])
    for p in (raw, fs):
        if os.path.exists(p): os.remove(p)
    ok = verify.get("video_type")=="Upload" and verify.get("http") in (200,206) and verify.get("faststart")
    return {"ok":ok, "verify":verify, "mb":round(os.path.getsize(f'{BASE}/uploaded/{eid}.mp4')/1e6,1) if ok else None}

targets = [e for e in yt if (not ONLY or str(e["id"]) in ONLY)]
for e in targets:
    eid = str(e["id"])
    if done.get(eid,{}).get("ok") and not ONLY:
        continue
    t0=time.time()
    res = process(e)
    done[eid] = {**res, "name": e["name"]}
    json.dump(done, open(STATE,"w"), ensure_ascii=False, indent=2)
    ok_n = sum(1 for v in done.values() if v.get("ok"))
    print(f"[{ok_n}/56] {eid} {e['name'][:34]:36} "
          f"{'✅' if res['ok'] else '❌ '+res.get('stage','')+' '+str(res.get('resp') or res.get('err','') or res.get('verify',''))[:80]} "
          f"({time.time()-t0:.0f}s)", flush=True)
print("DONE_UPLOAD. ok:", sum(1 for v in done.values() if v.get("ok")), "/ 56")
