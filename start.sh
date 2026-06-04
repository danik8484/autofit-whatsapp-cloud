#!/bin/bash
# התקן ו-connect Cloudflare WARP לפני הבוט
set -e

# התקן warp-cli אם לא קיים
if ! command -v warp-cli &> /dev/null; then
  echo "Installing Cloudflare WARP..."
  curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/cloudflare-client.list
  apt-get update -qq && apt-get install -y cloudflare-warp 2>/dev/null || echo "WARP install failed, continuing without..."
fi

# חבר ל-WARP
if command -v warp-cli &> /dev/null; then
  warp-cli register --accept-tos 2>/dev/null || true
  warp-cli connect 2>/dev/null || true
  sleep 3
  echo "WARP status: $(warp-cli status 2>/dev/null || echo 'unknown')"
  echo "My IP: $(curl -s https://ipinfo.io/ip 2>/dev/null || echo 'unknown')"
fi

# הפעל הבוט
exec node index.js
