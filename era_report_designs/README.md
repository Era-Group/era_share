# Era Document Designs

Bilingual (Arabic/English) redesign of the three PDF documents that leave the
company: the **Sales Order**, the **Delivery Note** and the **Payment Voucher**.

## What it does

The bodies of Odoo's own report templates are replaced through view
inheritance, so each document keeps a single Print button, keeps its
`report_name`, and mail templates, portal attachments and the
`%(stock.action_report_delivery)d` button already wired in `stock` all keep
working. Uninstalling the module restores Odoo's own layout.

| Document | Standard template that is overridden |
| --- | --- |
| Sales order / quotation / pro-forma | `sale.report_saleorder_document` |
| Delivery, receipt and internal transfer notes | `stock.report_delivery_document` |
| Receipt and payment vouchers | `account.report_payment_receipt_document` |

All three share `era_report_designs.era_document_layout`: the header (logo,
bilingual title, optional company identity block), the tinted meta bar, the two
colour-coded party cards, the signature row and the page footer. Only the line
table and the totals block are written per document.

## Design system

The palette is fixed in `static/src/scss/era_report_designs.scss` and scoped
under `.o_era_doc`, so no other report sharing the `web.report_assets_common`
bundle is affected.

| Variable | Value | Used for |
| --- | --- | --- |
| `$era-ink` | `#22303A` | headings and primary values |
| `$era-muted` | `#6B7280` | secondary latin labels |
| `$era-line` | `#E2E8EC` | table, card and divider borders |
| `$era-indigo` | `#43437E` | counterparty card bar, line table head |
| `$era-indigo-dark` | `#33335F` | first table head cell |
| `$era-teal` | `#1F7E80` | company card bar, grand total, amount panel |
| `$era-tint` | `#F3F8F9` | meta bar, notes box, light tiles |
| `$era-ok` / `$era-ok-bg` | `#1D8A5F` / `#E3F4EE` | "سليم" badge on the delivery note |

The Arabic face is **Tajawal**, which Odoo already ships under
`web/static/fonts/google/Tajawal/`. `fonts.scss` only declares its Regular
weight, so the family is redeclared here as `EraTajawal` in four weights to get
real bold faces instead of QtWebKit's synthetic emboldening. No font file is
added to this repository.

## Renderer constraints these templates work around

* **wkhtmltopdf 0.12.6 (QtWebKit)** — no flexbox and no Bootstrap grid; the
  layout is tables with explicit widths, which is what lays out reliably under
  `dir="rtl"`.
* **`dir="rtl"` is forced** on the header, article and footer roots, and
  `direction: rtl` is restated in scss: `web.report_layout` derives the page
  direction from the *user's* language, and `web/report.scss` hard-sets
  `direction: ltr` on `body`.
* **Latin digits always** — every record is read through
  `with_context(lang='en_US')`, so figures stay `1580.00` even if Arabic is
  activated in `res.lang` later.
* **No translation files** — every label is written bilingually in the
  template, so the documents do not depend on `ar_001` being installed.

## Paperformat

`report.paperformat` `paperformat_era_document` (A4, 34mm top, 20mm bottom,
side margins left to css like Odoo's own A4 format) is assigned to
`sale.action_report_saleorder`, `sale.action_report_pro_forma_invoice`,
`stock.action_report_delivery` and `account.action_report_payment_receipt`.
This is the one change that survives uninstalling; resetting those four records
to Odoo's A4 format restores the default.

Era Group · info@era.net.sa · https://era.net.sa
