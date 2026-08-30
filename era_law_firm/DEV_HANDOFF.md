# era_law_firm — مذكرة تسليم للتطوير (Dev Handoff)

> كُتبت بتاريخ 2026-08-30 بعد دراسة كاملة للموديول وتشغيل اختباراته بنجاح.
> الغرض: استكمال التطوير على خادم آخر دون إعادة الدراسة من الصفر.

- **الإصدار المدروس**: `19.0.11.1.0` (Odoo 19)
- **المسار**: `submodules/era_share_latest/era_law_firm`
- **التبعيات**: `mail, portal, calendar, account, hr_timesheet, l10n_sa, l10n_sa_edi`
- **آخر نتيجة اختبارات**: `62 tests 5.81s — 0 failed, 0 error(s) of 46 tests` (قاعدة جديدة، انظر أمر التشغيل أدناه)

---

## 1) خريطة الموديول

| الملف | المحتوى |
|---|---|
| `models/legal_core.py` | `legal.case` + المراحل، الأطراف، فحص التعارض، الجلسات، المواعيد، المستندات، حقلا الهوية على `res.partner` |
| `models/legal_finance.py` | الارتباطات `legal.engagement`، قيود الوقت، المصاريف، حسابات وحركات الأمانات `legal.trust.*` |
| `models/advanced.py` | مقاييس القضية المخزنة، إبطال فحص التعارض عند تغيّر الأطراف، قواعد المواعيد، الدفعات milestone، أتعاب النجاح، الاستشارات، سجل التدقيق `legal.audit.log`، حقول الربط على `account.move.line` |
| `models/legal_actions.py` | أفعال النوافذ وأزرار الإحصاء + حقل `file_data` inverse لإنشاء المرفق + أفعال ناقصة كانت مفقودة (approve للمصروف، set_ready للدفعة) |
| `models/legal_judiciary.py` | سجلات ولاية/محكمة/دائرة + onchange تصفية + ترحيل النصوص القديمة `legal.judiciary.migration._run` (يعمل عند كل تثبيت/تحديث) |
| `models/legal_trust_setup.py` | تهيئة تلقائية لإعدادات الأمانات (يملأ الفارغ فقط، xmlids حتمية `era_law_firm.<suffix>_company_<id>`) |
| `models/legal_deletion.py` | حارس حذف: حذف `mail.activity`/`mail.message` على سجل `legal.*` حي = مدير قانوني فقط |
| `models/legal_demo.py` | مولّد بيانات تجريبية عربية seeded تحت pseudo-module `__era_law_demo__` + `_purge()` كامل |
| `models/legal_help.py` | كل نصوص help مجمّعة (إعادة تصريح حقول بـ help فقط) |
| `models/res_config.py`, `account_move.py` | إعدادات الشركة + ربط الفاتورة بالقضية وتحرير المصادر عند الإلغاء `button_cancel` |
| `wizard/legal_wizards.py` | معالج الفوترة (قفل `FOR UPDATE` ضد الفوترة المزدوجة) + معالج عمليات الأمانات |
| `controllers/portal.py` | بوابة `/my/legal-cases` + تحميل المستندات المنشورة فقط (`portal_published` + `approved`) |
| `security/` | 5 مجموعات: staff → lawyer → supervisor → manager، + accountant (privilege منفصل). قواعد سجلات: عزل شركة + فريق القضية + تقييد المستندات restricted |
| `tests/` | 5 ملفات (انظر §3) |

## 2) القرارات المعمارية المهمة (لا تكسرها)

1. **فحص التعارض**: `_party_signature()` بصمة مطبَّعة (أرقام هوية/سجل، هاتف آخر 9 أرقام، بريد، اسم مطبَّع). أي تغيير في `client_id`/`party_ids` يمسح `conflict_check_id` (عبر `write` في `advanced.py`)، والتأكيد يرفض بصمة قديمة. تجاوز الحظر = مدير + سبب إلزامي مسجَّل في chatter.
2. **الأمانات**: كل حركة قيد يومية حقيقي (إيداع: مدين بنك الأمانات / دائن التزام). `action_post` يقفل الحساب بـ `SELECT ... FOR UPDATE` ثم يعيد التحقق من الرصيد — هذا ما يمنع السحب المزدوج المتوازي. الإلغاء = قيد عاكس، لا حذف. `apply` يسوّي فاتورة مرحّلة بنفس العميل عبر reconcile على حساب المدينين **نفسه** الذي تستخدمه الفواتير (لهذا `legal_trust_receivable_account_id` يجب أن يساوي مدينِي الشركة).
3. **منع الفوترة المزدوجة**: وجود `invoice_line_id` على المصدر هو الحارس، + قفل `FOR UPDATE` في المعالج، + `button_cancel` على الفاتورة يعيد المصادر لحالتها.
4. **الحذف**: القضايا/الجلسات draft فقط، المستندات المعتمدة تؤرشف ولا تحذف، الحركات المرحّلة لا تحذف أبدًا، وحذف أي شيء لغير المدير محجوب بـ CSV + حارس الرسائل/الأنشطة.
5. **نجيز**: أرقام وروابط وتواريخ هجرية = مراجع يدوية فقط؛ **لا يوجد تكامل آلي مع نجيز** ولا يجوز الإيحاء بوجوده. الضرائب والفوترة الإلكترونية شأن `l10n_sa(_edi)`.
6. **فهرس فريد جزئي** على `najiz_number` غير الفارغ لكل شركة، يُنشأ في `init()` بـ SQL مباشر.

## 3) الاختبارات وكيفية تشغيلها

الملفات: `test_legal_workflow` (المسار الكامل intake→close)، `test_legal_constraints` (رصيد الأمانات، الفوترة المزدوجة، عزل الشركات، البيانات المقيدة)، `test_deletion_rights`، `test_trust_setup`، `test_legal_controls`. كلها `post_install`.

`tests/common.py` يعتمد التهيئة التلقائية للأمانات ويفصل `edi_format_ids` عن يوميات البيع كي لا تعلق الاختبارات في ZATCA.

أمر التشغيل الذي نجح (قاعدة جديدة، منفصلة عن الإنتاج، منافذ بديلة لأن 8069 مشغول):

```bash
/opt/odoo/venv/bin/python3 /opt/odoo/ce/odoo-bin server \
  -d test_era_law_firm_YYYYMMDD \
  --addons-path=/opt/odoo/ce/addons,/opt/odoo/addons,/opt/odoo/submodules/common_latest,/opt/odoo/submodules/aidoo_latest,/opt/odoo/submodules/era_share_latest,/opt/odoo/ee,/opt/odoo/themes \
  --data-dir=/tmp/odoo-test-data \
  -i era_law_firm --test-enable --test-tags=/era_law_firm \
  --stop-after-init --no-http --http-port=8169 --gevent-port=8172 \
  --max-cron-threads=0 --workers=0 --log-level=info --logfile=/tmp/test_run.log
```

النتيجة (2026-08-30): `0 failed, 0 error(s) of 46 tests`.
ملاحظة: سطر `ERROR ... legal_trust_account_unique` في السجل **متوقَّع** — اختبار القيد الفريد يثيره داخل savepoint.

## 4) الفجوات المرصودة لاستكمال التطوير (بالأولوية)

1. **`legal_hearing_reminder_days` غير مفعّل** — الإعداد معرَّف في `models/res_config.py` (افتراضي 3) ونص المساعدة يعد بالتحكم في موعد التذكير، لكن `legal.hearing._cron_reminders` في `models/legal_core.py` يستخدم `days=1` ثابتًا. المطلوب: قراءة قيمة الشركة لكل سجل (مع مراعاة تعدد الشركات داخل الكرون).
2. **`legal_default_city` غير مفعّل** — لا يوجد default على `legal.case.city` يقرأه. المطلوب: `default=lambda self: self.env.company.legal_default_city`.
3. **تحذيرات تسميات مكررة عند التثبيت** — الحقول النصية القديمة `jurisdiction/court/circuit` (نصية، أبقيت للترحيل) تحمل نفس تسميات `jurisdiction_id/court_id/circuit_id`. المطلوب: إعادة تسمية القديمة (مثل `string='Court (legacy text)'`) أو إخفاؤها بعد اكتمال الترحيل.
4. **حركة الأمانات `transfer` شبه فارغة** — `signed_amount=0` ولا قيد ولا منطق توزيع بين القضايا. إمّا إكمالها (توزيع الرصيد على مستوى القضية مع تتبع مخصصات لكل قضية) أو إزالتها من الخيارات.
5. **سياق `skip_conflict_invalidation` يُمرَّر ولا يُقرأ** — في `advanced.py` (`LegalCase.write` و`LegalCaseParty.create/write`). يبدو أنه قُصد به كسر العودية عند مسح `conflict_check_id`؛ حاليًا لا ضرر لأن المسح لا يعيد إثارة الشرط، لكن يجب إمّا فحصه فعليًا في `write` أو حذفه.
6. **`legal.deadline` بلا `_check_company_auto` ولا `check_company=True` على `case_id`** — بخلاف الجلسات والمستندات. إضافة الحماية + اختبار.
7. **البوابة بدائية** — `portal_my_legal_cases` بلا pager (رغم استيراد `pager` في `controllers/portal.py`) ولا فرز ولا تفاصيل جلسات/فواتير للعميل. `party_ids.portal_visible` معرَّف ولا تستخدمه القوالب.
8. **تقرير PDF هيكلي فقط** — `report/legal_reports.xml` يطبع الاسم والموكل والمحكمة. مرشَّح للتوسعة: أطراف، جلسات، مواعيد، ملخص مالي.
9. **الترجمة** — `i18n/ar_001.po` (623 نصًا) يجب تحديثه بعد أي إضافة نصوص.
10. ثانوي: `confidential` حقل توثيقي فقط (لا يقيّد وصولًا — موثَّق في help عمدًا)، و`res.users.can_access_legal_case/can_view_*` في `advanced.py` تبدو API لموديولات أخرى (تحقق من المستهلكين قبل الحذف).

## 5) ملاحظات بيئة الخادم الحالي (قد تختلف عندك)

- الكتابة على قاعدة الإنتاج محجوبة؛ الاختبارات تُجرى على قواعد `test_*` جديدة فقط.
- المنفذ 8069/8072 مشغولان بخادم الإنتاج → استخدم `--no-http` مع منافذ بديلة.
- بيانات التجربة: قائمة **Configuration ▸ (Load Legal Test Data)** أو `env['legal.demo.data']._generate(n)`؛ الإزالة الكاملة بـ `_purge()`.
