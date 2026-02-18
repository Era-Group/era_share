# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'ERA Artificial Intelligence Client Module',
    'summary': 'Artificial Intelligence Integration Client',
    'description': """
موديول ERA AI Client هو طبقة تكامل ذكية داخل Odoo لتمكين المستخدم من التفاعل مع مساعد ذكاء اصطناعي مباشرة من واجهة النظام.

الدور الرئيسي للموديول:
- توفير واجهة حوار (AI Dialog) داخل الـ Backend لطلب المساعدة أثناء العمل اليومي.
- دعم تنفيذ جولات إرشادية (Tours) بشكل ذكي لتوجيه المستخدم داخل الشاشات والخطوات المطلوبة.
- تمكين الربط مع إعدادات النظام عبر صفحة الإعدادات لتخصيص سلوك التكامل.
- تحسين تجربة المستخدم من خلال أدوات واجهة إضافية مثل أزرار الحوار والنوافذ التفاعلية.
- تجهيز بنية قابلة للتوسعة لربط خدمات ذكاء اصطناعي أو استعلامات SQL مستقبلًا حسب الحاجة.

القيمة العملية:
يساعد الموديول على تقليل وقت التدريب، تسريع إنجاز المهام، ورفع كفاءة استخدام النظام عبر دعم تفاعلي لحظي داخل Odoo.
    """,
    'version': '1.0',
    'author': 'ERA Groups',
    'website': 'https://era.net.sa/',
    'depends': ['base', 'web', 'web_tour'],
    'data': [
        'security/ir.model.access.csv',
        'views/tour_connector.xml',
        # 'views/sql_connector.xml',
        'views/res_config_settings_view.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'era_ai_client/static/src/js/execute_tour.js',
            # 'era_ai_client/static/src/js/tour_service.js',

            'era_ai_client/static/src/js/tour_dialog/ai_bot_button.js',
            'era_ai_client/static/src/xml/tour_dialog/ai_bot_button.xml',

            'era_ai_client/static/src/js/tour_dialog/tour_ai_dialog.js',
            'era_ai_client/static/src/xml/tour_dialog/tour_ai_dialog.xml',

            'era_ai_client/static/src/xml/tour_dialog/sql_ai_dialog.xml',
            'era_ai_client/static/src/js/tour_dialog/sql_ai_dialog.js',

            'https://code.jquery.com/jquery-3.6.0.min.js',  # jQuery first
            'era_ai_client/static/lib/select2/select2.js',
            'era_ai_client/static/lib/select2/select2.css',
     
         
        ],
       
        
    },
    'images': ["static/description/icon.png"],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
