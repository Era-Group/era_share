# ERA SEO Manager — خطة التطوير القادمة

آخر تحديث: 2026-05-27
الفرع الحالي: `fix/P2-rendering` (جاهز للدمج)

---

## 1. دمج فرع الإصلاحات (أولوية عالية)

- [ ] دمج `fix/P2-rendering` → `main`
- [ ] التأكد من نجاح جميع الاختبارات بعد الدمج

---

## 2. إصلاحات بيانات — الموقع الحالي

مشاكل ظهرت في نتائج Google Rich Results Test:

- [ ] **URLs نسبية في JSON-LD**: الحقل `"url": "/"` يجب أن يكون absolute
      مثل `https://seo-era-share.stg1.era.net.sa/`
      → تحقق من إعداد Website → Domain Name
      → عدّل templates لتستخدم `website.domain` بدل `/`
- [ ] **اسم الشركة افتراضي**: `"name": "My Company"` يحتاج تحديث
      → Settings → Companies → اسم الشركة
- [ ] **روابط السوشال ميديا فارغة**: `"sameAs": []`
      → Website → Settings → ERA SEO → Social Profiles
- [ ] **وصف الموقع افتراضي**: `"description": "This is the homepage of the website"`
      → تحديث من إعدادات الموقع أو صفحة الهوم

---

## 3. تحسينات تقنية (الإصدار القادم)

- [ ] تحسين الـ Schema Templates لدعم absolute URLs تلقائياً
      (استخدام `website.domain` + path بدل path فقط)
- [ ] إزالة تكرار `<meta name="robots">` (واحد من Odoo core + واحد من ERA)
- [ ] تحسين أداء Schema rendering (preload في الـ controller بدل query في QWeb)
      حسب CLAUDE.md §9: "No queries inside QWeb templates"

---

## 4. ميزات مستقبلية

- [ ] `era_seo_gsc_connector` — ربط Google Search Console (read-only)
      مذكور في CHANGELOG كـ future module
- [ ] تقارير SEO مجدولة
- [ ] تحسين SEO Audit Dashboard بتوصيات قابلة للتنفيذ
- [ ] دعم أنواع Schema إضافية حسب الحاجة

---

## 5. ملاحظات للسيرفر الجديد

### التثبيت
```bash
# تثبيت الموديول
odoo-bin -c odoo.conf -d <DB_NAME> -i era_seo_manager --stop-after-init

# ترقية بعد التحديث
odoo-bin -c odoo.conf -d <DB_NAME> -u era_seo_manager --stop-after-init
```

### الاختبارات
```bash
odoo-bin -c odoo.conf -d <DB_NAME> -i era_seo_manager \
    --test-enable --test-tags era_seo_manager --stop-after-init
```

### بعد التثبيت — إعداد أساسي
1. Website → Settings → Domain Name → تأكد إنه absolute URL
2. Settings → Companies → اسم الشركة الصحيح
3. Website → Settings → ERA SEO → Social Profiles → أضف الروابط
4. Website → Settings → ERA SEO → Organization → راجع البيانات
5. Website → SEO → Overview → تحقق من الحالة

### الفروع
- `main` — الإصدار المستقر
- `fix/P2-rendering` — إصلاحات الـ rendering (جاهز للدمج)
- `phase/P1-seo-mixin` — Phase 1
- `phase/P2-schema-engine` — Phase 2

### الإصدار الحالي
`19.0.2.1.0`
