# תמונות מקבוצת "שינויים/המלצות" → Google Drive

כל תמונה שנשלחת בקבוצת הוואטסאפ נשמרת אוטומטית בתיקיית דרייב.
משולב בבוט הקיים (`index.js`), על אותו קו GreenAPI (0509477965).

## מה כבר מוכן (בקוד)
- זיהוי `imageMessage` נכנסת מהקבוצה (לפי chatId מדויק או לפי שם שמכיל "המלצות").
- הורדה מ-WhatsApp (GreenAPI) → העלאה ל-Drive.
- אם תיקיית היעד לא נגישה — הבוט יוצר תיקייה ייעודית "המלצות וואטסאפ" וזוכר אותה.
- endpoint בדיקה: `GET /debug-recs`.

## מה צריך פעם אחת (הקמת חיבור Drive)

### 1. OAuth client ב-Google Cloud (חשבון daniel5085695@gmail.com)
1. https://console.cloud.google.com → צור פרויקט (או בחר קיים).
2. "APIs & Services" → "Library" → חפש **Google Drive API** → **Enable**.
3. "OAuth consent screen" → User type **External** → מלא שם אפליקציה + מייל → Save.
   **חשוב: "PUBLISH APP" → Production** (אחרת ה-token פג כל 7 ימים). עם scope
   `drive.file` הפרסום מיידי, בלי תהליך אימות.
4. "Credentials" → "Create Credentials" → "OAuth client ID" →
   Application type **Desktop app** → Create.
5. העתק את **Client ID** ו-**Client Secret**.

### 2. השגת refresh token (מהמחשב, פעם אחת)
```bash
cd ~/autofit-cloud
GOOGLE_CLIENT_ID=<client_id> GOOGLE_CLIENT_SECRET=<client_secret> node get_drive_token.js
```
ייפתח דפדפן → אשר עם החשבון → הסקריפט מדפיס את 3 הערכים ל-Railway.

### 3. Railway (Variables של service "bot")
```
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=...
# אופציונלי:
RECS_GROUP_CHAT_ID=...     # chatId מדויק של הקבוצה (מומלץ — ראה למטה)
DRIVE_FOLDER_ID=1QZqC8TcTk-gD1quncMAHVdJTGeUYwVWn   # התיקייה הקיימת "המלצות וואטסאפ"
```
ואז: `railway up --service bot`

### 4. אימות + מציאת chatId
- שלח תמונה לקבוצה.
- פתח `https://bot-production-8cfe.up.railway.app/debug-recs` → תראה את ה-chatId
  של הקבוצה ואת סטטוס ההעלאה. העתק את ה-chatId ל-`RECS_GROUP_CHAT_ID` (זיהוי
  מדויק יותר מזיהוי-לפי-שם), deploy שוב.

## הערה על תיקיית היעד
scope `drive.file` מתיר כתיבה רק לתיקיות שהאפליקציה יצרה. אם `DRIVE_FOLDER_ID`
(התיקייה הקיימת) לא נגיש לאפליקציה — הבוט ייצור תיקייה חדשה "המלצות וואטסאפ"
וישתמש בה. הקישור אליה יודפס בלוג וזמין ב-`/debug-recs`.
