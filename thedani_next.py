#!/usr/bin/env python3
"""מציג את הלקוחות הבאים שעוד לא אומתו ידנית (חסר '✓ אומת ידנית' בכרטיס), לפי סדר.
לחידוש המעבר הידני אחרי שהשיחה מתארכת/מתמצתת."""
import json, os
CARDS = os.path.expanduser('~/Desktop/client_cards')
clients = json.load(open('/tmp/clients.json'))
def verified(ph):
    p = f"{CARDS}/{ph}.md"
    if not os.path.exists(p): return False
    return "אומת ידנית" in open(p, encoding='utf-8').read()
done = sum(1 for c in clients if verified(c['phone']))
print(f"אומתו ידנית: {done}/{len(clients)}")
nxt = [c for c in clients if not verified(c['phone'])]
print(f"נותרו: {len(nxt)}\nהבאים בתור:")
for c in nxt[:int(os.environ.get('N','5'))]:
    print(f"  {c['phone']} | {c['name']}")
