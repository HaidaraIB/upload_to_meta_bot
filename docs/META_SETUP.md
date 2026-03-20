# Meta Setup - upload_to_meta_bot

## المتطلبات الأساسية
البوت ينشر عبر **Facebook Graph API** و **Instagram Graph API**.

## 1) إنشاء Meta App
1. أنشئ App داخل Meta Developers.
2. فعّل **Facebook Login** (لأن نشر Instagram يعتمد على tokens مرتبطة بالمستخدم/الـ App user).
3. اجعل الـ App لديه الصلاحيات (Permissions) التالية (قد تحتاج اعتماد/موافقة حسب حالتك):
   - `pages_show_list`
   - `pages_read_engagement`
   - `pages_manage_posts`
   - `instagram_basic`
   - `instagram_content_publish`
   - (قد يلزم) `business_management` إذا كنت تستخدم Business System User

### صلاحية `pages_manage_posts` ورسالة «not available / App Review»
ميتا قد تُرجع خطأ مثل: *The permission(s) pages_manage_posts are not available* — أي أن التطبيق **لم يُمنَح** هذه الصلاحية فعليًا في السياق الحالي:

| وضع التطبيق | ماذا يعني عمليًا |
|-------------|------------------|
| **Development** | النشر يعمل عادة **فقط** لحسابات مضافة كـ **Admin / Developer / Tester** على نفس التطبيق في لوحة المطورين. تأكد أن حساب فيسبوك الذي تولّد منه التوكن له أحد هذه الأدوار، وأنك اخترت `pages_manage_posts` عند توليد التوكن (مثلاً من Graph API Explorer أو مسار Facebook Login). |
| **Live** | لاستخدام النشر لحسابات عامة أو لعملاء، غالبًا تحتاج **App Review** وموافقة ميتا على `pages_manage_posts` (وأي صلاحيات أخرى تطلبها واجهة النشر). |

بعد تعديل الصلاحيات أو أدوار المستخدمين، **أعد إصدار long-lived token** وحدّث `META_ACCESS_TOKEN` في `.env`.

## 2) تجهيز Token
أضف long-lived token في ملف `.env` أو متغيرات البيئة باسم:
- `META_ACCESS_TOKEN=<LONG_LIVED_ACCESS_TOKEN>`

البوت يستخدم هذا الـ token لجلب Pages (`/me/accounts`) ثم ينشر نيابة عن الحسابات المتاحة له.

**مهم لستوري فيسبوك (صورة):** رفع صورة ستوري يمرّ بمرحلة صورة غير منشورة (`published=false`). واجهة Graph تطلب أن يُنفَّذ ذلك **بتوكن الصفحة** وليس توكن المستخدم فقط. عند اختيار صفحة من البوت، يُخزَّن `access_token` الخاص بتلك الصفحة من استجابة `/me/accounts` ويُستخدم تلقائيًا للنشر — فتأكد أن التطبيق لديه صلاحية مثل `pages_manage_posts` حتى تُعاد `access_token` لكل صفحة.

اختياري:
- `META_GRAPH_VERSION=v25.0` (افتراضيًا v25.0 إن لم تحدده)
- `TELEGRAM_MEDIA_MAX_MB=200` — أقصى حجم (ميغابايت) لتحميل الوسائط من تيليجرام عبر **Telethon** قبل النشر على ميتا (الافتراضي 200). يتطلّب `API_ID` و`API_HASH` و`BOT_TOKEN` كما في بقية البوت.
- `META_HTTP_TIMEOUT_TOTAL=600` — مهلة بالثواني لطلبات Graph و`rupload` أثناء النشر (الافتراضي 600). ارفعها إذا كان الرفع بطيئاً أو الملفات كبيرة.

### فيديو إنستغرام (ريلز / ستوري / فيديو) وخطأ `ProcessingFailedError`
إذا نجأ إنشاء الحاوية ثم فشل الرفع إلى `rupload.facebook.com` برسالة مثل **`ProcessingFailedError`** / **`Request processing failed`**، فالملف غالباً **وصل إلى ميتا** لكن **لم تُقبل معالجة الفيديو** (ليست بالضرورة مشكلة في البوت).

**حدود الحجم (واجهة ميتا — مرجع `ig-user/media`):**
- **ريلز / فيديو يُعالج كـ REELS** (مثل فيديو الفيد في هذا البوت): حتى **300 ميغابايت**.
- **فيديو ستوري**: حتى **100 ميغابايت**.

فمثلاً فيديو **125 ميغابايت** كريلز **مسموح بالحجم**؛ إن استمر الخطأ فالسبب غالباً **الترميز أو بنية الملف** وليس الحجم وحده.

جرّب عملياً:
- **MP4** بترميز **H.264** للفيديو و**AAC** للصوت، معدّل إطارات منطقي (مثلاً 30fps).
- للريلز: نسبة عرض مناسبة (غالباً **9:16**)، مدة ضمن حدود إنستغرام، وحجم/دقة معقولة.
- وثائق ميتا تطلب **MP4 بدون edit lists** و**ذرة `moov` في مقدمة الملف** (*fast start*). بدون ذلك يفشل الرفع أحياناً بـ `ProcessingFailedError`. مثال إعادة ترتيب بدون إعادة ترميز:  
  `ffmpeg -i input.mp4 -c copy -movflags +faststart output.mp4`
- إعادة تصدير الفيديو من أداة مثل HandBrake أو FFmpeg ثم إعادة الرفع.

راجع أيضاً [Resumable uploads - Instagram Platform](https://developers.facebook.com/docs/instagram-platform/content-publishing/resumable-uploads/) لأحدث المتطلبات.

## 3) جدولة النشر
البوت يستخدم **JobQueue داخل تيليجرام بوت**:
- عند اختيار “مستقبل”، يتم **تأجيل تنفيذ النشر** داخل البوت لوقت محدد.
- هذا يعني أن البوت سيقوم بالـ upload+publish في وقت الجدولة، وليس عبر `scheduled_publish_time` في واجهة Meta.

## 4) إعداد UTC Offset
لمنع اختلاف الوقت، يوجد إعداد في الأدمن:
- `إعدادات Meta` -> تعديل `UTC+offset`
- هذا الإعداد يُستخدم عند إدخال `YYYY-MM-DD HH:MM` في خطوة الجدولة.
- القيمة الافتراضية: `3`

## 5) صور Instagram - شرط `image_url`
عند اختيار:
- Platform تشمل `Instagram`
و
- نوع الوسائط `photo`  
فسيطلب منك البوت `image_url` عام يبدأ بـ `http/https` لأن Graph API لصور Instagram غالبًا يتطلب رابط عام أثناء `media` container creation.

## قناة نتائج النشر (اختياري)
لإرسال تقرير نجاح/فشل عمليات النشر إلى قناة تيليجرام:

1. أنشئ قناة تيليجرام.
2. أضف البوت كـ `Admin` في القناة (بحيث يمكنه إرسال الرسائل).
3. في ملف `.env` أضف متغير:
   - `PUBLISH_RESULTS_CHANNEL=<chat_id>`
4. بعد ذلك سيتم إرسال تقرير تلقائيًا بعد كل عملية نشر (سواء `now` أو `schedule`) مع إخفاء البيانات الحساسة (مثل توكنات الوصول) تلقائيًا.

ملاحظة: `chat_id` غالبًا يكون رقمًا (وقد يبدأ بـ `-100...` للقنوات الخاصة). يمكنك الحصول عليه عبر الأدوات/البوتات الخاصة بمعرفة `chat_id`.

## 6) اختبارات Meta (pytest)

**أين تذهب المخرجات؟** افتراضيًا كل ما يطبعه `pytest` (أسماء الاختبارات، نقاط، أخطاء، ملخص) يظهر **في الطرفية فقط** — لا يُكتب ملف تلقائيًا. اختبارات الوحدة **لا تستدعي Meta**؛ “الردود” الوحيدة هي استجابات **مزيّفة** داخل الذاكرة (mock / aioresponses)، وليست طلبات حقيقية.

```bash
pip install -r requirements-dev.txt
pytest tests/unit          # بدون شبكة — graph_client + كل مسارات publish_to_meta (mock)
pytest                     # نفس الوحدات + تكامل معطّل افتراضيًا
```

**حفظ نفس المخرجات في ملف (مع الاستمرار بالطباعة):**

```powershell
# من جذر المشروع، مع تفعيل .venv إن وُجد
New-Item -ItemType Directory -Force -Path test-results | Out-Null
.\.venv\Scripts\pytest.exe tests/unit -v --tb=short 2>&1 | Tee-Object -FilePath test-results\last-run.txt
```

**تقرير XML (مناسب لـ CI أو الأرشفة):**

```powershell
.\.venv\Scripts\pytest.exe tests/unit -v --tb=short --junitxml=test-results\junit.xml
```

أو تشغيل السكربت الجاهز (نص + XML معًا): `.\scripts\run_tests_save_log.ps1`

مجلد `test-results/` مُدرَج في `.gitignore` ولن يُرفع إلى Git.

لتشغيل اختبار حيّ ضد Graph API (قراءة `.env`):

```bash
set META_RUN_LIVE_TESTS=1
pytest tests/integration -m integration
```

يحتاج `META_ACCESS_TOKEN`. لا يُنشر محتوى؛ يستدعي `list_business_assets` فقط.

## 7) ملاحظات تشغيلية
- تأكد أن الـ token قادر على الوصول للـ Pages الخاصة بك وأن Instagram Business المرتبط يظهر في قائمة Business Assets.
- إذا فشل النشر لسبب صلاحيات/إذن ناقص، سيتم إظهار سبب الفشل للأدمن (وسيتم تسجيل تفاصيل في `errors.txt` عبر معالج الأخطاء).

