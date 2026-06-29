#!/usr/bin/env python3
# הורדה+frames+אורך+pHash+דחיסה לכל .mov של יובל מהדרייב. state מצטבר, חסין-המשך.
import gdown, subprocess, os, json, re, time, sys
import imagehash
from PIL import Image

FF = "node_modules/ffmpeg-static/ffmpeg"
BASE = os.path.expanduser("~/Desktop/autofit-videos")
COMP = f"{BASE}/compressed"; FR = f"{BASE}/mov-frames"
os.makedirs(COMP, exist_ok=True); os.makedirs(FR, exist_ok=True)
MANIFEST = f"{BASE}/mov_manifest.json"
movs = json.load(open("movlist.json"))

done = {}
if os.path.exists(MANIFEST):
    done = {d["mov"]: d for d in json.load(open(MANIFEST))}

def dur_of(path):
    r = subprocess.run([FF, "-i", path], capture_output=True, text=True)
    m = re.search(r'Duration: (\d+):(\d+):(\d+)\.(\d+)', r.stderr)
    if m: return int(m[1])*3600+int(m[2])*60+int(m[3]) + int(m[4])/100
    return None

results = list(done.values())
for fid, title in movs:
    key = title
    if key in done and done[key].get("ok"):
        continue
    t0 = time.time()
    raw = f"/tmp/{re.sub(r'[^0-9A-Za-z]','_',title)}.mov"
    try:
        gdown.download(id=fid, output=raw, quiet=True)
        if not os.path.exists(raw) or os.path.getsize(raw) < 10000:
            raise RuntimeError("download failed")
        d = dur_of(raw)
        # extract 6 frames evenly across duration
        hashes = []
        if d:
            for pct in (0.1,0.25,0.4,0.55,0.7,0.85):
                ts = max(0, d*pct)
                fp = f"{FR}/{re.sub(r'[^0-9A-Za-z]','_',title)}_{int(pct*100)}.jpg"
                subprocess.run([FF,"-y","-ss",str(ts),"-i",raw,"-frames:v","1","-vf","scale=320:-1",fp],
                               capture_output=True)
                if os.path.exists(fp):
                    try: hashes.append(str(imagehash.phash(Image.open(fp))))
                    except: pass
        # compress to mp4 720p faststart
        outmp4 = f"{COMP}/{re.sub(r'[^0-9A-Za-z]','_',title)}.mp4"
        subprocess.run([FF,"-y","-i",raw,"-vf","scale='min(1280,iw)':-2","-c:v","libx264",
                        "-crf","26","-preset","veryfast","-c:a","aac","-b:a","96k",
                        "-movflags","+faststart",outmp4], capture_output=True)
        comp_ok = os.path.exists(outmp4) and os.path.getsize(outmp4) > 50000
        rec = {"mov": title, "fid": fid, "dur": d, "phash": hashes,
               "mp4": outmp4 if comp_ok else None,
               "mp4_mb": round(os.path.getsize(outmp4)/1e6,1) if comp_ok else None, "ok": comp_ok}
        os.remove(raw)
    except Exception as e:
        rec = {"mov": title, "fid": fid, "ok": False, "err": str(e)[:120]}
        if os.path.exists(raw): os.remove(raw)
    done[key] = rec
    results = list(done.values())
    json.dump(results, open(MANIFEST,"w"), ensure_ascii=False, indent=2)
    print(f"[{len([r for r in results if r.get('ok')])}/{len(movs)}] {title}: "
          f"dur={rec.get('dur')} mp4={rec.get('mp4_mb')}MB frames={len(rec.get('phash',[]))} "
          f"{'OK' if rec.get('ok') else 'FAIL '+rec.get('err','')} ({time.time()-t0:.0f}s)", flush=True)

print("DONE. ok:", len([r for r in results if r.get('ok')]), "/", len(movs))
