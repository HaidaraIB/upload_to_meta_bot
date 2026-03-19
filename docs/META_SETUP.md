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

## 2) تجهيز Token
أضف long-lived token في ملف `.env` أو متغيرات البيئة باسم:
- `META_ACCESS_TOKEN=<LONG_LIVED_ACCESS_TOKEN>`

البوت يستخدم هذا الـ token لجلب Pages (`/me/accounts`) ثم ينشر نيابة عن الحسابات المتاحة له.

اختياري:
- `META_GRAPH_VERSION=v25.0` (افتراضيًا v25.0 إن لم تحدده)

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

## 6) ملاحظات تشغيلية
- تأكد أن الـ token قادر على الوصول للـ Pages الخاصة بك وأن Instagram Business المرتبط يظهر في قائمة Business Assets.
- إذا فشل النشر لسبب صلاحيات/إذن ناقص، سيتم إظهار سبب الفشل للأدمن (وسيتم تسجيل تفاصيل في `errors.txt` عبر معالج الأخطاء).

