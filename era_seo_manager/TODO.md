# ERA SEO Manager — خطة التطوير القادمة

آخر تحديث: 2026-05-27
الفرع الحالي: `seo`
الإصدار الحالي: `19.0.2.2.0`

---

## 1. إصلاحات بيانات — الموقع الحالي

مشاكل تحتاج ضبط من إعدادات الموقع (ليست أخطاء برمجية):

- [ ] **اسم الشركة افتراضي**: `"name": "My Company"` يحتاج تحديث
      → Settings → Companies → اسم الشركة
- [ ] **روابط السوشال ميديا فارغة**: `"sameAs": []`
      → Website → Settings → ERA SEO → Social Profiles
- [ ] **وصف الموقع افتراضي**: `"description": "This is the homepage of the website"`
      → تحديث من إعدادات الموقع أو صفحة الهوم

---

## 2. تحسينات تقنية — تم إنجازها في `19.0.2.2.0`

- [x] **URLs مطلقة في JSON-LD** — تم في `19.0.2.2.0`
      Schema templates تستخدم `{{ site_url }}` الآن، مع fallback تلقائي
      من `web.base.url` ورفع `example.com` إلى `https://example.com`.
- [x] **إزالة تكرار `<meta name="robots">`** — تم في `19.0.2.2.0`
      `meta_tags` لم يعد يُصدر الـ tag (Layout xpath Step 5 هو المصدر الوحيد).
- [x] **Preload لمخططات الـ Schema خارج QWeb** — تم في `19.0.2.2.0`
      تم استبدال الـ search inline بـ
      `era.seo.schema.instance._get_for_render(main_object, website)`.

---

## 3. ميزات مستقبلية

- [ ] `era_seo_gsc_connector` — ربط Google Search Console (read-only)
      مذكور في CHANGELOG كـ future module
- [ ] تقارير SEO مجدولة
- [ ] تحسين SEO Audit Dashboard بتوصيات قابلة للتنفيذ
- [ ] دعم أنواع Schema إضافية حسب الحاجة

---

## 4. ملاحظات للسيرفر الجديد

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
- `seo` — الفرع الحالي (يحتوي 19.0.2.x)
- `phase/P1-seo-mixin` — Phase 1
- `phase/P2-schema-engine` — Phase 2

### الإصدار الحالي
`19.0.2.2.0`
