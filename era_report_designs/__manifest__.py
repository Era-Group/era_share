{
    'name': 'Era Document Designs',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Bilingual AR/EN redesign of the Sales Order, Delivery Note and Payment Voucher PDFs',
    'author': 'Era Group',
    'email': 'info@era.net.sa',
    'website': 'https://era.net.sa',
    'license': 'LGPL-3',
    'depends': [
        'sale',
        'stock',
        'account',
    ],
    'data': [
        'report/era_document_layout.xml',
        'report/era_sale_order_report.xml',
        'report/era_delivery_note_report.xml',
        'report/era_payment_receipt_report.xml',
        'report/era_report_paperformat.xml',
    ],
    'assets': {
        'web.report_assets_common': [
            'era_report_designs/static/src/scss/era_report_designs.scss',
        ],
    },
    'installable': True,
}
