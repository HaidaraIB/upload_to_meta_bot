# دليل Ubuntu الكامل لتشغيل البوت بـ systemd

هذا الدليل هو أبسط طريقة Production لتشغيل بوتات Python على VPS بدون `screen`، مع إعادة تشغيل تلقائية بعد إعادة تشغيل السيرفر أو تعطل العملية.

## لماذا `systemd` أفضل من `screen`؟

- تشغيل تلقائي بعد reboot
- إعادة تشغيل تلقائية عند crash
- إدارة موحدة عبر `systemctl`
- متابعة Logs بسهولة عبر `journalctl`

---

## 1) الاتصال بالسيرفر

```bash
ssh your_user@your_vps_ip
```

## 2) تثبيت المتطلبات

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

## 3) الدخول لمجلد المشروع

```bash
cd /path/to/upload_to_meta_bot
```

## 4) إعداد البيئة الافتراضية وتنزيل المكتبات

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
```

## 5) تجهيز ملف البيئة `.env`

مشروعك يقرأ الإعدادات من `.env` عبر `load_dotenv()`، لذلك تأكد أن الملف موجود في جذر المشروع.

إذا ملفك الجاهز هو `.env.prod`:

```bash
cp .env.prod .env
```

تأكد أن `.env` يحتوي المتغيرات المطلوبة (مثل):

- `API_ID`
- `API_HASH`
- `BOT_TOKEN`
- `OWNER_ID`
- `LOG_LEVEL` (اختياري: `DEBUG` أو `INFO` … الافتراضي في الكود `DEBUG`؛ عيّن `INFO` في الإنتاج إذا أردت أقل ضجيجاً)
- أي متغيرات DB / Meta أخرى يحتاجها البوت

---

## 6) إنشاء خدمة systemd

أنشئ الملف:

```bash
sudo nano /etc/systemd/system/upload_to_meta_bot.service
```

### الخيار الموصى به (آمن - مستخدم مخصص)

```ini
[Unit]
Description=Upload To Meta Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=botuser
WorkingDirectory=/home/botuser/upload_to_meta_bot
EnvironmentFile=/home/botuser/upload_to_meta_bot/.env
ExecStart=/home/botuser/upload_to_meta_bot/.venv/bin/python /home/botuser/upload_to_meta_bot/main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

### الخيار السريع (إذا المشروع داخل `/root`)

```ini
[Unit]
Description=Upload To Meta Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/upload_to_meta_bot
EnvironmentFile=/root/upload_to_meta_bot/.env
ExecStart=/root/upload_to_meta_bot/.venv/bin/python /root/upload_to_meta_bot/main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

احفظ الملف ثم اخرج.

---

## 7) تفعيل وتشغيل الخدمة

```bash
sudo systemctl daemon-reload
sudo systemctl enable upload_to_meta_bot
sudo systemctl start upload_to_meta_bot
```

## 8) التأكد من الحالة

```bash
sudo systemctl status upload_to_meta_bot
```

النتيجة المطلوبة:

- `active (running)`

## 9) متابعة السجلات (Logs)

```bash
sudo journalctl -u upload_to_meta_bot -f
```

---

## أوامر يومية مهمة

تشغيل:

```bash
sudo systemctl start upload_to_meta_bot
```

إيقاف:

```bash
sudo systemctl stop upload_to_meta_bot
```

إعادة تشغيل:

```bash
sudo systemctl restart upload_to_meta_bot
```

تعطيل التشغيل التلقائي مع الإقلاع:

```bash
sudo systemctl disable upload_to_meta_bot
```

---

## بعد أي تعديل في ملف الخدمة

إذا عدلت `/etc/systemd/system/upload_to_meta_bot.service` نفذ دائمًا:

```bash
sudo systemctl daemon-reload
sudo systemctl restart upload_to_meta_bot
```

---

## نشر تحديثات الكود (روتين سريع)

داخل مجلد المشروع:

```bash
cd /path/to/upload_to_meta_bot
source .venv/bin/activate
pip install -r requirements.txt
deactivate
sudo systemctl restart upload_to_meta_bot
```

---

## مشاكل شائعة وحلها

### الخدمة لا تعمل

افحص:

```bash
sudo systemctl status upload_to_meta_bot
sudo journalctl -u upload_to_meta_bot -n 100 --no-pager
```

### خطأ: ملف `.env` غير موجود

- تأكد من المسار في `EnvironmentFile=...`
- تأكد أن الملف موجود فعلاً في نفس المسار

### خطأ: Python أو venv غير موجود

- تأكد من المسار داخل `ExecStart=...`
- يجب أن يكون الملف التنفيذي موجودًا:
  - `/path/to/project/.venv/bin/python`

### الخدمة تعمل ثم تتوقف فورًا

- غالبًا متغير بيئة ناقص أو خطأ في الكود
- راجع `journalctl` وستظهر تفاصيل الخطأ

---

## تشغيل عدة بوتات بنفس الأسلوب

لكل بوت:

1. مجلد مشروع مستقل
2. `.venv` مستقل
3. ملف خدمة مستقل باسم مختلف، مثال:
   - `bot_upload_meta.service`
   - `bot_orders.service`
4. فعّل كل خدمة:
   - `daemon-reload`
   - `enable`
   - `start`

---

## خلاصة

باستخدام `systemd` لن تحتاج مراقبة يدوية مثل `screen`:

- البوت يشتغل تلقائيًا بعد reboot
- يعيد التشغيل تلقائيًا عند التعطل
- إدارة واضحة وسريعة من مكان واحد

---

## Update Workflow (تطبيق التحديثات)

### الحالة العادية: تحديث كود البوت

بعد أي تحديث للكود، نفّذ:

```bash
cd /path/to/upload_to_meta_bot
git pull
sudo systemctl restart upload_to_meta_bot
sudo systemctl status upload_to_meta_bot
```

### إذا التحديث يحتوي مكتبات جديدة

نفّذ:

```bash
cd /path/to/upload_to_meta_bot
git pull
source .venv/bin/activate
pip install -r requirements.txt
deactivate
sudo systemctl restart upload_to_meta_bot
sudo systemctl status upload_to_meta_bot
```

### إذا عدّلت ملف الخدمة نفسه

إذا عدّلت:

- `/etc/systemd/system/upload_to_meta_bot.service`

فلازم تعمل:

```bash
sudo systemctl daemon-reload
sudo systemctl restart upload_to_meta_bot
sudo systemctl status upload_to_meta_bot
```

### التحقق الفوري بعد أي تحديث

```bash
sudo journalctl -u upload_to_meta_bot -f
```

إذا ظهرت مشكلة بعد تحديث:

- تحقق من dependencies (`pip install -r requirements.txt`)
- تحقق من متغيرات البيئة داخل `.env`
