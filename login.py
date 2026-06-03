"""
login.py - התחברות ל-auto-fit עם OTP
מריץ Chrome headless, שולח OTP לוואטסאפ, שומר JWT ב-session.json
הרץ פעם אחת כשה-token פג.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from playwright.async_api import async_playwright

SESSION_FILE = Path(__file__).parent / "session.json"
EMAIL = "danikaganovichacademy@gmail.com"
AUTOFIT_URL = "https://app.auto-fit.co.il"

# מספר הוואטסאפ לקבלת OTP (דני עצמו)
NOTIFY_PHONE = os.environ.get("NOTIFY_PHONE", "")

OTP_FILE = Path("/tmp/autofit_otp.txt")


async def login():
    print("🔐 מתחיל login...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()

        await page.goto(AUTOFIT_URL, wait_until="networkidle")

        # הזן מייל
        await page.fill('input[type="email"], input[name="email"], input[placeholder*="מייל"]', EMAIL)
        await page.click('button[type="submit"], button:has-text("שלח"), button:has-text("כניסה")')
        await page.wait_for_timeout(2000)

        print(f"📱 קוד OTP נשלח למייל. ממתין לקוד...")

        # ממתין לOTP — יכול להגיע מ:
        # 1. קובץ /tmp/autofit_otp.txt (שהבוט כותב)
        # 2. משתנה סביבה OTP
        # 3. stdin
        otp = None
        for _ in range(60):  # עד דקה
            if OTP_FILE.exists():
                otp = OTP_FILE.read_text().strip()
                OTP_FILE.unlink()
                break
            env_otp = os.environ.get("OTP_CODE", "")
            if env_otp:
                otp = env_otp
                break
            await asyncio.sleep(2)

        if not otp:
            print("❌ לא התקבל OTP תוך 2 דקות")
            await browser.close()
            return False

        # הזן OTP
        await page.fill('input[type="text"], input[name="otp"], input[placeholder*="קוד"]', otp)
        await page.click('button[type="submit"], button:has-text("אימות"), button:has-text("כניסה")')
        await page.wait_for_url("**/dashboard**", timeout=15000)

        # שלוף token
        token = await page.evaluate("localStorage.getItem('authToken')")
        coach_id = await page.evaluate("localStorage.getItem('CoachId')")

        if not token:
            print("❌ לא נמצא token לאחר login")
            await browser.close()
            return False

        SESSION_FILE.write_text(json.dumps({"token": token, "coach_id": coach_id or ""}))
        print(f"✅ Login הצליח! CoachId={coach_id}")
        await browser.close()
        return True


if __name__ == "__main__":
    ok = asyncio.run(login())
    sys.exit(0 if ok else 1)
