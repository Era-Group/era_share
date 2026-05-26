# `era_seo_manager` — Full Module Specification

**Target platform:** Odoo 19 Community (must also install cleanly on Enterprise)
**Module type:** Application
**Author:** ERA — Excellence Resources Arabia
**License:** OPL-1
**Repo layout:** Monorepo-friendly, single addon

---

## 0. Document purpose

This document is the **single source of truth** for building the `era_seo_manager` Odoo addon. It is written to be handed to Claude Code (or any senior Odoo developer) as a complete build brief. Every model, field, view, controller, and acceptance criterion is enumerated. If a question is not answered here, default to **stock Odoo 19 `website` module conventions**.

A companion `CLAUDE.md` file ships with this spec and defines **how** Claude Code should work (style, commit conventions, testing rhythm). This document defines **what** to build.

---

## 1. Problem statement

Odoo 19's stock SEO support is fragmented and shallow:

- `website.seo.metadata` mixin only covers `website_meta_title`, `website_meta_description`, `website_meta_keywords`, `website_meta_og_img`. No Twitter, no canonical override, no robots directives per page.
- No proper JSON-LD support. The `<meta>` tag soup must be hand-rolled per page in QWeb.
- No redirect manager UI — 301/302 redirects require editing `ir.http` or installing third-party addons.
- Sitemap is auto-generated but cannot be filtered, prioritized, or extended without subclassing controllers.
- `website_blog` lacks: reading time, related posts, article series, structured TOC, author bio pages, RSS schema, FAQ-on-article support, and per-post schema variants.
- No SEO health/audit dashboard. Admins cannot see which pages are missing meta, have duplicate titles, or have broken canonicals.
- No hreflang automation for the bilingual ar/en sites ERA clients typically need.

`era_seo_manager` fills these gaps as a single, opinionated addon. It is built to deliver Fatoratec-grade SEO out of the box and to scale across all ERA client deployments.

---

## 2. Goals and non-goals

### 2.1 Goals

1. Provide a unified, declarative SEO layer applied via a single mixin (`era.seo.mixin`).
2. Ship a **JSON-LD schema engine** with built-in templates for the schemas that matter: `Organization`, `LocalBusiness`, `WebSite`, `MobileApplication`, `SoftwareApplication`, `Article`, `BlogPosting`, `FAQPage`, `BreadcrumbList`, `Product`, `Service`, `Person`, `Event`.
3. Deliver a **redirect manager** with bulk CSV import, regex support, and hit-counter.
4. Generate **per-language sitemaps** plus a **sitemap index**, with priority and `changefreq` per content type, and an admin UI for inclusion rules.
5. Enhance `website_blog` with reading time, related posts, article series, auto-TOC, author profiles, RSS/Atom schema, and per-post FAQ embedding.
6. Provide an **SEO audit dashboard** that flags pages with missing/duplicate meta, missing OG image, missing canonical, missing schema, slow URL slugs, and broken redirects.
7. Manage **hreflang** automatically when Odoo's i18n is in use.
8. Be **fully RTL/Arabic-friendly** with translated UI, Arabic field labels, and Hijri-aware date helpers where relevant.
9. Be **portable**: install on any Odoo 19 site, no dependency on `website_sale` or other vertical modules unless the user opts in.

### 2.2 Non-goals

1. Replacing the Odoo `website` module or its builder.
2. Real-time Google Search Console API integration (a separate `era_seo_gsc_connector` may follow in v2; keep hooks but no live polling).
3. Building a page builder. Reusable content blocks ship as QWeb snippets only.
4. Full A/B testing engine.
5. Backlink analysis or scraping competitor SERPs.
6. Replacing `website_blog`. We **extend** it.

---

## 3. Dependencies

```
'depends': [
    'base',
    'web',
    'website',
    'website_blog',
    'mail',
    'portal',
]
```

Optional soft-dependencies handled via `try/except` and `module:` checks in views:

- `website_sale` → product schema enrichment
- `website_event` → event schema
- `hr` → author profile linking
- `crm` → lead capture from blog forms (optional widget)

Python packages (add to `requirements.txt`, not `__manifest__.py`):

- `python-slugify >= 8.0`
- `beautifulsoup4 >= 4.12` (already shipped with Odoo)
- `lxml` (already shipped)

No new system dependencies.

---

## 4. Module structure

```
era_seo_manager/
├── __init__.py
├── __manifest__.py
├── README.md
├── CHANGELOG.md
├── data/
│   ├── ir_cron.xml
│   ├── ir_sequence.xml
│   ├── seo_schema_template_data.xml
│   ├── seo_robots_default_data.xml
│   └── seo_default_settings.xml
├── demo/
│   └── demo.xml
├── i18n/
│   └── ar.po
├── models/
│   ├── __init__.py
│   ├── seo_mixin.py
│   ├── ir_ui_view.py
│   ├── website.py
│   ├── website_page.py
│   ├── website_menu.py
│   ├── seo_schema_template.py
│   ├── seo_schema_instance.py
│   ├── seo_redirect.py
│   ├── seo_sitemap_config.py
│   ├── seo_robots_rule.py
│   ├── seo_audit_run.py
│   ├── seo_audit_finding.py
│   ├── seo_hreflang.py
│   ├── blog_post.py
│   ├── blog_category.py
│   ├── blog_series.py
│   ├── blog_author.py
│   ├── res_partner.py
│   ├── res_config_settings.py
│   └── content_block.py
├── controllers/
│   ├── __init__.py
│   ├── main.py
│   ├── sitemap.py
│   ├── robots.py
│   ├── redirect.py
│   ├── blog.py
│   └── feed.py
├── views/
│   ├── menus.xml
│   ├── res_config_settings_views.xml
│   ├── seo_schema_template_views.xml
│   ├── seo_schema_instance_views.xml
│   ├── seo_redirect_views.xml
│   ├── seo_sitemap_config_views.xml
│   ├── seo_robots_rule_views.xml
│   ├── seo_audit_run_views.xml
│   ├── seo_audit_dashboard.xml
│   ├── seo_hreflang_views.xml
│   ├── blog_post_views.xml
│   ├── blog_post_templates.xml
│   ├── blog_series_views.xml
│   ├── blog_author_views.xml
│   ├── content_block_views.xml
│   ├── content_block_snippets.xml
│   ├── website_layout_templates.xml
│   └── website_meta_templates.xml
├── reports/
│   └── seo_audit_report.xml
├── wizards/
│   ├── __init__.py
│   ├── seo_bulk_update_wizard.py
│   ├── seo_bulk_update_wizard_views.xml
│   ├── seo_redirect_import_wizard.py
│   ├── seo_redirect_import_wizard_views.xml
│   ├── seo_audit_wizard.py
│   └── seo_audit_wizard_views.xml
├── security/
│   ├── seo_security.xml
│   └── ir.model.access.csv
├── static/
│   ├── description/
│   │   ├── icon.png
│   │   ├── index.html
│   │   └── screenshots/
│   └── src/
│       ├── js/
│       │   ├── seo_dashboard.js
│       │   └── seo_snippets.js
│       ├── scss/
│       │   ├── backend.scss
│       │   └── frontend.scss
│       └── xml/
│           └── seo_dashboard.xml
└── tests/
    ├── __init__.py
    ├── common.py
    ├── test_seo_mixin.py
    ├── test_schema_engine.py
    ├── test_redirects.py
    ├── test_sitemap.py
    ├── test_robots.py
    ├── test_blog_extensions.py
    ├── test_audit.py
    ├── test_hreflang.py
    └── test_controllers.py
```

---

## 5. Manifest

```python
# __manifest__.py
{
    'name': 'ERA SEO Manager',
    'summary': 'Complete SEO, schema, redirects, sitemap, and blog enhancement for Odoo 19',
    'description': """
ERA SEO Manager
===============
A unified SEO layer for Odoo 19 websites:
- Per-page meta, OG, Twitter, canonical, robots directives
- JSON-LD schema engine with 13+ built-in templates
- Redirect manager (301/302) with bulk CSV import
- Sitemap and robots.txt admin UI
- SEO audit dashboard with actionable findings
- Blog enhancements: reading time, related posts, series, TOC, author profiles
- Hreflang automation for multilingual websites
- Full Arabic / RTL support
    """,
    'author': 'ERA — Excellence Resources Arabia',
    'website': 'https://era.net.sa',
    'license': 'OPL-1',
    'category': 'Website/SEO',
    'version': '19.0.1.0.0',
    'depends': [
        'base',
        'web',
        'website',
        'website_blog',
        'mail',
        'portal',
    ],
    'data': [
        'security/seo_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence.xml',
        'data/ir_cron.xml',
        'data/seo_schema_template_data.xml',
        'data/seo_robots_default_data.xml',
        'data/seo_default_settings.xml',
        'wizards/seo_bulk_update_wizard_views.xml',
        'wizards/seo_redirect_import_wizard_views.xml',
        'wizards/seo_audit_wizard_views.xml',
        'views/menus.xml',
        'views/res_config_settings_views.xml',
        'views/seo_schema_template_views.xml',
        'views/seo_schema_instance_views.xml',
        'views/seo_redirect_views.xml',
        'views/seo_sitemap_config_views.xml',
        'views/seo_robots_rule_views.xml',
        'views/seo_audit_run_views.xml',
        'views/seo_audit_dashboard.xml',
        'views/seo_hreflang_views.xml',
        'views/blog_post_views.xml',
        'views/blog_series_views.xml',
        'views/blog_author_views.xml',
        'views/content_block_views.xml',
        'views/content_block_snippets.xml',
        'views/blog_post_templates.xml',
        'views/website_layout_templates.xml',
        'views/website_meta_templates.xml',
        'reports/seo_audit_report.xml',
    ],
    'demo': [
        'demo/demo.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'era_seo_manager/static/src/scss/backend.scss',
            'era_seo_manager/static/src/js/seo_dashboard.js',
            'era_seo_manager/static/src/xml/seo_dashboard.xml',
        ],
        'web.assets_frontend': [
            'era_seo_manager/static/src/scss/frontend.scss',
            'era_seo_manager/static/src/js/seo_snippets.js',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
}
```

---

## 6. Phased delivery plan

Each phase is independently testable, mergeable, and demoable. Claude Code should complete a phase fully (models + views + tests + docs) before moving to the next.

| Phase | Scope | Acceptance gate |
|-------|-------|-----------------|
| **P1** | Core SEO mixin + meta rendering | Every page model can declare SEO fields. `<head>` renders correctly on all websites. |
| **P2** | JSON-LD schema engine + 13 built-in templates | Admin can attach any schema to any page. Rich Results Test passes for all built-ins. |
| **P3** | Redirect manager + bulk import wizard | 301/302 redirects served before 404. CSV import works. Loop detection works. |
| **P4** | Sitemap + robots.txt admin | `/sitemap.xml`, `/sitemap-{lang}.xml`, `/sitemap-index.xml`, `/robots.txt` all configurable in UI. |
| **P5** | Blog extensions | Reading time, related posts, series, TOC, author profiles, RSS+JSON Feed, Article+BlogPosting+FAQ schema. |
| **P6** | Hreflang automation | `<link rel="alternate" hreflang>` injected automatically for translated pages. x-default supported. |
| **P7** | SEO audit dashboard | One-click audit produces findings sorted by severity. Each finding is actionable from the UI. |
| **P8** | Content blocks (snippets) | Reusable schema-aware QWeb snippets: FAQ accordion, CTA, author box, related posts, breadcrumbs. |
| **P9** | Tests, docs, demo data, translation | ≥80% coverage on Python code. README complete. Arabic .po file complete. |

---

## 7. Phase 1 — Core SEO mixin

### 7.1 Model: `era.seo.mixin`

A reusable Abstract Model that any record type can inherit from to gain SEO fields.

```python
# models/seo_mixin.py
from odoo import api, fields, models

class EraSeoMixin(models.AbstractModel):
    _name = 'era.seo.mixin'
    _description = 'ERA SEO Mixin'

    # --- Title and description -------------------------------------------------
    seo_title = fields.Char(
        string='SEO Title',
        translate=True,
        help='Overrides <title>. Recommended ≤60 chars.',
    )
    seo_title_length = fields.Integer(
        compute='_compute_seo_lengths', store=False,
    )
    seo_description = fields.Text(
        string='Meta Description',
        translate=True,
        help='Recommended 140–160 chars.',
    )
    seo_description_length = fields.Integer(
        compute='_compute_seo_lengths', store=False,
    )
    seo_keywords = fields.Char(
        string='Meta Keywords',
        translate=True,
        help='Comma-separated. Most search engines ignore this; included for legacy.',
    )

    # --- Open Graph ------------------------------------------------------------
    seo_og_title = fields.Char(string='OG Title', translate=True)
    seo_og_description = fields.Text(string='OG Description', translate=True)
    seo_og_image = fields.Binary(string='OG Image', attachment=True)
    seo_og_image_url = fields.Char(
        string='OG Image URL',
        compute='_compute_og_image_url', store=True,
    )
    seo_og_type = fields.Selection([
        ('website', 'Website'),
        ('article', 'Article'),
        ('product', 'Product'),
        ('profile', 'Profile'),
    ], default='website')

    # --- Twitter Card ----------------------------------------------------------
    seo_twitter_card = fields.Selection([
        ('summary', 'Summary'),
        ('summary_large_image', 'Summary Large Image'),
        ('app', 'App'),
        ('player', 'Player'),
    ], default='summary_large_image')
    seo_twitter_site = fields.Char(string='Twitter @site')
    seo_twitter_creator = fields.Char(string='Twitter @creator')

    # --- Indexing controls -----------------------------------------------------
    seo_canonical_url = fields.Char(string='Canonical URL Override')
    seo_robots_index = fields.Boolean(string='Index this page', default=True)
    seo_robots_follow = fields.Boolean(string='Follow links on this page', default=True)
    seo_robots_archive = fields.Boolean(string='Allow archive', default=True)
    seo_robots_snippet = fields.Boolean(string='Allow snippet', default=True)

    # --- Sitemap ---------------------------------------------------------------
    seo_sitemap_include = fields.Boolean(string='Include in sitemap', default=True)
    seo_sitemap_priority = fields.Selection([
        ('0.1', '0.1'), ('0.2', '0.2'), ('0.3', '0.3'), ('0.4', '0.4'),
        ('0.5', '0.5 (default)'), ('0.6', '0.6'), ('0.7', '0.7'),
        ('0.8', '0.8'), ('0.9', '0.9'), ('1.0', '1.0 (home)'),
    ], default='0.5')
    seo_sitemap_changefreq = fields.Selection([
        ('always', 'Always'), ('hourly', 'Hourly'), ('daily', 'Daily'),
        ('weekly', 'Weekly'), ('monthly', 'Monthly'),
        ('yearly', 'Yearly'), ('never', 'Never'),
    ], default='weekly')

    # --- Schema ----------------------------------------------------------------
    seo_schema_instance_ids = fields.One2many(
        'era.seo.schema.instance',
        compute='_compute_schema_instances',
        string='JSON-LD Schemas',
    )

    # --- Computes --------------------------------------------------------------
    @api.depends('seo_title', 'seo_description')
    def _compute_seo_lengths(self):
        for rec in self:
            rec.seo_title_length = len(rec.seo_title or '')
            rec.seo_description_length = len(rec.seo_description or '')

    @api.depends('seo_og_image')
    def _compute_og_image_url(self):
        for rec in self:
            if rec.seo_og_image:
                rec.seo_og_image_url = f'/web/image/{rec._name}/{rec.id}/seo_og_image'
            else:
                rec.seo_og_image_url = False

    def _compute_schema_instances(self):
        Schema = self.env['era.seo.schema.instance']
        for rec in self:
            rec.seo_schema_instance_ids = Schema.search([
                ('res_model', '=', rec._name),
                ('res_id', '=', rec.id),
            ])

    # --- Public API ------------------------------------------------------------
    def get_seo_url(self):
        """Return canonical absolute URL of the record. Override per model."""
        self.ensure_one()
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        return f'{base}{self._get_seo_path()}'

    def _get_seo_path(self):
        """Override in subclasses. Default: empty path."""
        return '/'

    def get_seo_meta_dict(self):
        """Return a dict ready for QWeb template consumption."""
        self.ensure_one()
        return {
            'title': self.seo_title,
            'description': self.seo_description,
            'keywords': self.seo_keywords,
            'og_title': self.seo_og_title or self.seo_title,
            'og_description': self.seo_og_description or self.seo_description,
            'og_image': self.seo_og_image_url,
            'og_type': self.seo_og_type,
            'twitter_card': self.seo_twitter_card,
            'twitter_site': self.seo_twitter_site,
            'twitter_creator': self.seo_twitter_creator,
            'canonical': self.seo_canonical_url or self.get_seo_url(),
            'robots': self._get_robots_directive(),
        }

    def _get_robots_directive(self):
        self.ensure_one()
        parts = []
        parts.append('index' if self.seo_robots_index else 'noindex')
        parts.append('follow' if self.seo_robots_follow else 'nofollow')
        if not self.seo_robots_archive:
            parts.append('noarchive')
        if not self.seo_robots_snippet:
            parts.append('nosnippet')
        return ', '.join(parts)
```

### 7.2 Inheritance targets

Apply `era.seo.mixin` to:

- `website.page` — pages built with the website builder
- `blog.post` — already enhanced in Phase 5, but the mixin lands first
- `product.template` (only if `website_sale` is installed; guard via `_inherit` in optional file)
- `era.content.block` — reusable blocks (Phase 8)

Important: do **not** replace stock `website.seo.metadata` mixin. Compose with it. If a stock field exists (e.g. `website_meta_title`), keep it readable but treat `seo_title` as authoritative when both are set. Provide a one-time data migration script that copies stock values into the new fields on install.

### 7.3 Meta rendering template

QWeb template `era_seo_manager.meta_tags` rendered inside `website.layout` `<head>`:

```xml
<template id="meta_tags" name="ERA SEO Meta Tags">
    <t t-if="seo">
        <title><t t-esc="seo['title'] or website.name"/></title>
        <meta name="description" t-att-content="seo['description']"/>
        <meta name="keywords" t-att-content="seo['keywords']" t-if="seo['keywords']"/>
        <meta name="robots" t-att-content="seo['robots']"/>
        <link rel="canonical" t-att-href="seo['canonical']"/>

        <!-- Open Graph -->
        <meta property="og:title" t-att-content="seo['og_title']"/>
        <meta property="og:description" t-att-content="seo['og_description']"/>
        <meta property="og:type" t-att-content="seo['og_type']"/>
        <meta property="og:url" t-att-content="seo['canonical']"/>
        <meta property="og:image" t-att-content="seo['og_image']" t-if="seo['og_image']"/>
        <meta property="og:site_name" t-att-content="website.name"/>
        <meta property="og:locale" t-att-content="request.lang.code.replace('_', '-')"/>

        <!-- Twitter -->
        <meta name="twitter:card" t-att-content="seo['twitter_card']"/>
        <meta name="twitter:title" t-att-content="seo['og_title']"/>
        <meta name="twitter:description" t-att-content="seo['og_description']"/>
        <meta name="twitter:image" t-att-content="seo['og_image']" t-if="seo['og_image']"/>
        <meta name="twitter:site" t-att-content="seo['twitter_site']" t-if="seo['twitter_site']"/>
        <meta name="twitter:creator" t-att-content="seo['twitter_creator']" t-if="seo['twitter_creator']"/>
    </t>
</template>
```

The template is injected into `website.layout` via an `xpath` inherit that removes Odoo's stock meta block and replaces it.

### 7.4 Definition of Done — Phase 1

- [ ] `era.seo.mixin` exists with all fields above.
- [ ] `website.page` inherits and exposes all SEO fields in the page form view.
- [ ] `<head>` of every page on the site renders the new meta template instead of the stock one.
- [ ] Existing values from `website_meta_title`, `website_meta_description`, `website_meta_keywords`, `website_meta_og_img` are copied into new fields at install time via a post-init hook.
- [ ] Unit tests: `test_seo_mixin.py` covers length computes, robots directive composition, URL canonicalization, meta dict generation.
- [ ] Manual QA: open any page, change `seo_title` in the admin, reload frontend, confirm `<title>` changes.

---

## 8. Phase 2 — JSON-LD schema engine

### 8.1 Models

#### 8.1.1 `era.seo.schema.template`

A reusable template that produces JSON-LD when rendered with a context.

| Field | Type | Notes |
|-------|------|-------|
| `name` | Char, required | Display name, e.g. "Organization", "FAQ Page" |
| `code` | Char, required, unique | Programmatic key: `organization`, `faq_page`, `mobile_application`, etc. |
| `schema_type` | Char, required | The schema.org `@type`, e.g. `Organization`, `FAQPage`, `MobileApplication` |
| `category` | Selection | `core` / `content` / `commerce` / `event` / `local` |
| `description` | Text | Admin-facing description |
| `body` | Text, required | The JSON template with Jinja2-style placeholders. Stored as text, validated as JSON on save. |
| `required_fields_json` | Text | JSON array of dotted keys expected in the rendering context. Used by audit. |
| `documentation_url` | Char | Link to schema.org docs |
| `active` | Boolean | Default `True` |
| `is_default` | Boolean | If `True`, attached automatically to new pages of matching type |

#### 8.1.2 `era.seo.schema.instance`

A concrete attachment of a template to a specific record.

| Field | Type | Notes |
|-------|------|-------|
| `template_id` | Many2one `era.seo.schema.template` | Required |
| `res_model` | Char, required | Target model |
| `res_id` | Integer, required | Target record ID |
| `name` | Char, computed | `{template.name} on {res_model}#{res_id}` |
| `data_json` | Text | Optional JSON of context overrides for this instance |
| `rendered_json` | Text, computed | Final rendered JSON-LD (computed live, not stored) |
| `sequence` | Integer | Render order on the page |
| `active` | Boolean | Default `True` |

`res_model`/`res_id` form a polymorphic reference. Indexed together.

### 8.2 Rendering engine

Implement as a service-style class on `era.seo.schema.instance`:

```python
def get_rendered_json_ld(self, page_record=None):
    """
    Returns the final JSON-LD string to embed in <head>.
    page_record: the record the page is rendering (e.g. a blog.post).
                 If None, uses {self.res_model: self.res_id}.
    """
```

Context resolution order (each later step overrides earlier):

1. Site-wide defaults from `res.config.settings` (company name, logo URL, social profiles).
2. Template defaults baked into `body`.
3. Record fields read from `page_record`.
4. Per-instance overrides from `data_json`.

Placeholders use `{{ dotted.path }}` syntax. Resolve via `safe_eval` on a sandboxed dict — **never** eval arbitrary Python. Document the allowed expression grammar in a docstring.

### 8.3 Built-in templates (data file)

Ship these in `data/seo_schema_template_data.xml`. Each template's `body` is a JSON string with placeholders. Example:

```xml
<record id="schema_tpl_organization" model="era.seo.schema.template">
    <field name="name">Organization</field>
    <field name="code">organization</field>
    <field name="schema_type">Organization</field>
    <field name="category">core</field>
    <field name="is_default">True</field>
    <field name="body"><![CDATA[
{
  "@context": "https://schema.org",
  "@type": "Organization",
  "@id": "{{ website.url }}/#organization",
  "name": "{{ company.name }}",
  "legalName": "{{ company.legal_name }}",
  "url": "{{ website.url }}/",
  "logo": "{{ company.logo_url }}",
  "address": {
    "@type": "PostalAddress",
    "addressCountry": "{{ company.country_code }}"
  },
  "sameAs": {{ company.social_profiles_json }}
}
    ]]></field>
</record>
```

Templates to ship in `data/seo_schema_template_data.xml`:

1. `organization` — site-wide entity
2. `local_business` — for physical premises
3. `website` — sitelinks search box + publisher
4. `mobile_application` — apps (Fatoratec use case)
5. `software_application` — web apps
6. `article` — generic articles
7. `blog_posting` — blog posts with extended fields
8. `news_article` — news mode
9. `faq_page` — list of questions and answers
10. `how_to` — step-by-step guides
11. `breadcrumb_list` — navigation breadcrumbs
12. `product` — for `website_sale` (conditional)
13. `service` — service offerings
14. `person` — author profiles
15. `event` — events (conditional)
16. `video_object` — for video pages
17. `aggregate_rating` — composable into others, not standalone

For each template, write a complete `body` with sane defaults. Document required context keys in `required_fields_json`. Reference output of the manual schema work we already produced for Fatoratec as the gold standard for `organization`, `website`, `mobile_application`, `faq_page`.

### 8.4 Frontend integration

QWeb template `era_seo_manager.schema_ld` rendered in `<head>`:

```xml
<template id="schema_ld" name="ERA JSON-LD">
    <t t-foreach="schema_instances" t-as="inst">
        <script type="application/ld+json">
            <t t-raw="inst.get_rendered_json_ld(page_record)"/>
        </script>
    </t>
</template>
```

Page controllers (or the layout inherit) populate `schema_instances` via:

```python
schema_instances = request.env['era.seo.schema.instance'].sudo().search([
    ('res_model', '=', record._name),
    ('res_id', '=', record.id),
    ('active', '=', True),
], order='sequence asc')
```

### 8.5 Admin UI

- Tree + form views for `era.seo.schema.template` under **Website → SEO → Schema Templates**.
- On any page form (e.g. `website.page` form), add a notebook tab **"SEO → Schemas"** showing the One2many of `era.seo.schema.instance` filtered by `res_model='website.page'` and `res_id=id`.
- Inline form allows picking a template and editing `data_json` (use the Ace editor widget that ships with `web_editor`).
- Add a **"Preview JSON-LD"** button that opens a dialog with the rendered output.
- Add a **"Validate"** button that submits to Google's Rich Results Test URL with the page URL prefilled.

### 8.6 Definition of Done — Phase 2

- [ ] All 17 built-in templates loaded by demo install and pass JSON schema validation.
- [ ] At least 3 templates (Organization, FAQ, BlogPosting) verified live in Google Rich Results Test against a demo site.
- [ ] Per-page schema instances render in source HTML in the order specified by `sequence`.
- [ ] Templates can be edited in the UI without code changes.
- [ ] Tests: rendering with missing fields produces a JSON-LD with `null` values, not a crash. Invalid JSON in `body` is rejected on save.

---

## 9. Phase 3 — Redirect manager

### 9.1 Model: `era.seo.redirect`

| Field | Type | Notes |
|-------|------|-------|
| `name` | Char, computed | Display name |
| `source_url` | Char, required, indexed | Path only, no domain. E.g. `/old-page` or regex pattern |
| `target_url` | Char, required | Path or full URL |
| `redirect_type` | Selection | `301` (permanent, default), `302` (temporary), `307`, `308`, `410` (gone, no target needed) |
| `is_regex` | Boolean | If `True`, `source_url` is treated as a regex |
| `is_active` | Boolean, default True | |
| `website_id` | Many2one `website` | Scope to a website, or all if empty |
| `lang_id` | Many2one `res.lang` | Scope to a language, or all if empty |
| `hit_count` | Integer, default 0 | Incremented each match |
| `last_hit_date` | Datetime | |
| `notes` | Text | |
| `created_from` | Selection | `manual` / `import` / `auto_404` / `auto_rename` |

Indexes: `(source_url, website_id, lang_id, is_active)` composite, plus a partial index on `is_active=True`.

### 9.2 Controller hook

Inherit `ir.http._dispatch` (or the Odoo 19 equivalent) to check redirects **before** the standard router fires 404. Use a single SQL query with `website_id IN (current, NULL)` and `lang_id IN (current, NULL)`. Cache results in a `tools.lru` keyed by `(path, website, lang)` with TTL 60s.

Loop detection: maintain a per-request seen-set; if the same source URL is hit twice, log an error and return 508.

### 9.3 Bulk CSV import wizard

Model: `era.seo.redirect.import.wizard`

Fields:

- `data_file` (Binary): CSV
- `delimiter` (Selection): `,` `;` `\t`
- `has_header` (Boolean, default True)
- `default_type` (Selection): `301` / `302`
- `default_website_id` (Many2one)
- `dry_run` (Boolean, default True)

Behavior:

1. Parse CSV. Required columns: `source`, `target`. Optional: `type`, `website`, `lang`, `notes`.
2. Validate each row. Collect errors.
3. If `dry_run`, return a report dialog with counts (would create / would update / errors).
4. Otherwise, upsert by `(source_url, website_id, lang_id)`.

### 9.4 404 auto-suggest

When a 404 fires, log to a transient table `era.seo.redirect.404_log` with path, referer, hit count. Surface in admin under **Website → SEO → 404 Log** with one-click "Create redirect" action.

### 9.5 Definition of Done — Phase 3

- [ ] Plain path redirects work and increment `hit_count`.
- [ ] Regex redirects work (test: `^/blog/old/(.*)$` → `/articles/\\1`).
- [ ] Loop detection returns 508 on infinite chain.
- [ ] CSV import wizard handles 1,000-row file in under 5s.
- [ ] 404 log captures hits and offers one-click conversion.

---

## 10. Phase 4 — Sitemap and robots.txt admin

### 10.1 Model: `era.seo.sitemap.config`

| Field | Type | Notes |
|-------|------|-------|
| `name` | Char | Internal name |
| `website_id` | Many2one `website`, required | One config per website |
| `enabled` | Boolean, default True | |
| `split_by_language` | Boolean, default True | Generate `/sitemap-{lang}.xml` plus `/sitemap-index.xml` |
| `max_urls_per_file` | Integer, default 45000 | Google limit 50,000 |
| `inclusion_rule_ids` | One2many `era.seo.sitemap.rule` | Per-model inclusion |
| `last_generated` | Datetime, readonly | |
| `last_url_count` | Integer, readonly | |

### 10.2 Model: `era.seo.sitemap.rule`

| Field | Type | Notes |
|-------|------|-------|
| `config_id` | Many2one `era.seo.sitemap.config`, required | |
| `model_id` | Many2one `ir.model`, required | Must implement `_get_seo_path()` |
| `domain` | Char | Odoo domain string, e.g. `[('is_published','=',True)]` |
| `default_priority` | Selection | Same options as mixin |
| `default_changefreq` | Selection | Same options as mixin |
| `sequence` | Integer | |

### 10.3 Controllers

- `/sitemap.xml` — root sitemap. Returns index if `split_by_language`, else the only sitemap.
- `/sitemap-index.xml` — explicit index.
- `/sitemap-{lang_code}.xml` — per-language.
- `/sitemap-{rule_id}.xml` — per-rule (for chunking large catalogs).
- `/robots.txt` — generated from `era.seo.robots.rule` records.

Cache: HTTP-level response cache, key `(path, website, lang)`, TTL 1h. Bust on cron rebuild.

### 10.4 Cron

`era_seo_manager.cron_rebuild_sitemap`:
- Interval: daily 03:00 site-local time.
- Action: regenerate all sitemap configs and bust caches.

### 10.5 Model: `era.seo.robots.rule`

| Field | Type | Notes |
|-------|------|-------|
| `name` | Char | Internal label |
| `website_id` | Many2one `website` | |
| `user_agent` | Char, default `*` | |
| `directive` | Selection | `allow` / `disallow` / `crawl_delay` / `sitemap` |
| `value` | Char | Path for allow/disallow, integer for crawl_delay, URL for sitemap |
| `sequence` | Integer | |
| `active` | Boolean, default True | |

Seed defaults via `data/seo_robots_default_data.xml`:
- `User-agent: *`
- `Disallow: /web/`
- `Disallow: /my/`
- `Disallow: /shop/cart`
- `Sitemap: {base_url}/sitemap.xml`

### 10.6 Definition of Done — Phase 4

- [ ] `/sitemap.xml` returns valid XML with correct `lastmod`, `priority`, `changefreq`.
- [ ] Multilingual site produces `/sitemap-en.xml`, `/sitemap-ar.xml`, and `/sitemap-index.xml`.
- [ ] `/robots.txt` reflects all `era.seo.robots.rule` records.
- [ ] Admin can disable a model from the sitemap and the change is visible on next request.
- [ ] Cron regenerates and busts caches.

---

## 11. Phase 5 — Blog enhancements

`website_blog` provides `blog.blog`, `blog.post`, `blog.tag`. We **extend** all three.

### 11.1 `blog.post` extension

Add fields:

| Field | Type | Notes |
|-------|------|-------|
| `era_subtitle` | Char, translate | Optional secondary headline |
| `era_reading_time_minutes` | Integer, computed | Based on `content` word count / 200 wpm |
| `era_word_count` | Integer, computed | Stripped HTML word count |
| `era_excerpt` | Text, translate | If empty, auto-generated from first 200 chars of stripped content |
| `era_series_id` | Many2one `era.blog.series` | |
| `era_series_sequence` | Integer | Position in series |
| `era_category_id` | Many2one `era.blog.category` | Distinct from tags |
| `era_related_post_ids` | Many2many `blog.post`, computed (stored) | Top 4 by tag overlap |
| `era_toc_html` | Html, computed | Auto-generated from `<h2>`/`<h3>` in content |
| `era_show_toc` | Boolean, default True | |
| `era_show_author_box` | Boolean, default True | |
| `era_show_related` | Boolean, default True | |
| `era_show_share_buttons` | Boolean, default True | |
| `era_faq_ids` | One2many `era.blog.faq` | Inline FAQ block for `FAQPage` schema |
| `era_author_profile_id` | Many2one `era.blog.author` | Extends `res.users` |
| `era_canonical_external_url` | Char | For syndicated content |

Inherit `era.seo.mixin`. Override `_get_seo_path()` to use the blog post URL.

### 11.2 Related posts algorithm

Compute on `(write_date, tag_ids)` change, stored. Top 4 by:

1. Same `era_series_id` (exclude self) — up to 2 slots
2. Most shared `tag_ids` with current post — fill remaining
3. Tie-break by `published_date desc`
4. Always exclude unpublished posts

### 11.3 Auto-TOC

Implement `_compute_toc_html`:

1. Parse `content` with `lxml.html`.
2. Find all `h2`, `h3`.
3. Inject `id` attributes (slugified) if missing.
4. Build nested `<ul>` HTML.
5. Persist `id` injections back into `content` (write only if changed, to avoid recursion).

### 11.4 Reading time

`era_reading_time_minutes = max(1, round(era_word_count / 200))`. Display as "X min read" / "X دقيقة قراءة" depending on lang.

### 11.5 New model: `era.blog.series`

| Field | Type | Notes |
|-------|------|-------|
| `name` | Char, translate, required | |
| `slug` | Char, required, unique | |
| `description` | Html, translate | |
| `cover_image` | Binary | |
| `post_ids` | One2many `blog.post` | |
| `post_count` | Integer, computed | |
| Inherits `era.seo.mixin` | | |

URL: `/blog/series/<slug>` — series landing page lists posts in order.

### 11.6 New model: `era.blog.category`

Distinct from tags: categories are taxonomic (one per post), tags are folksonomic.

| Field | Type | Notes |
|-------|------|-------|
| `name` | Char, translate, required | |
| `slug` | Char, required, unique | |
| `parent_id` | Many2one self | Hierarchical |
| `description` | Html, translate | |
| Inherits `era.seo.mixin` | | |

### 11.7 New model: `era.blog.author`

Author profile, distinct from `res.users` so non-staff authors can be published.

| Field | Type | Notes |
|-------|------|-------|
| `name` | Char, required | |
| `slug` | Char, required, unique | |
| `user_id` | Many2one `res.users` | Optional link |
| `bio` | Html, translate | |
| `avatar` | Binary | |
| `email` | Char | |
| `social_twitter` | Char | |
| `social_linkedin` | Char | |
| `social_github` | Char | |
| `post_ids` | One2many `blog.post` | |
| Inherits `era.seo.mixin` | | |

URL: `/blog/author/<slug>`.

### 11.8 New model: `era.blog.faq`

| Field | Type | Notes |
|-------|------|-------|
| `post_id` | Many2one `blog.post`, required, ondelete cascade | |
| `question` | Char, translate, required | |
| `answer` | Html, translate, required | |
| `sequence` | Integer | |

Rendered both as a visible accordion at the bottom of the article and as `FAQPage` JSON-LD.

### 11.9 Feed controllers

- `/blog/feed/rss` — RSS 2.0
- `/blog/feed/atom` — Atom 1.0
- `/blog/feed/json` — JSON Feed 1.1
- Per-blog, per-tag, per-category, per-author variants

### 11.10 Frontend templates

Override `website_blog.blog_post_complete`:

```
+ subtitle under H1
+ reading time + word count meta line
+ TOC sidebar (if era_show_toc)
+ author box at top and bottom (if era_show_author_box)
+ FAQ accordion before footer (if era_faq_ids)
+ related posts block (if era_show_related)
+ share buttons (if era_show_share_buttons)
+ series navigation (prev/next in series) (if in series)
+ breadcrumbs: Home → Blog → Category → Post
```

Auto-attach schemas at render: `BlogPosting`, `BreadcrumbList`, and `FAQPage` (if any FAQs).

### 11.11 Definition of Done — Phase 5

- [ ] Existing `website_blog` posts continue to render and SEO improves automatically (auto-excerpt, reading time, TOC visible).
- [ ] New series, categories, authors, FAQs editable from the backend.
- [ ] RSS/Atom/JSON feeds validate against W3C feed validator.
- [ ] Article schema, breadcrumb schema, and FAQ schema all present on published posts.

---

## 12. Phase 6 — Hreflang

### 12.1 Model: `era.seo.hreflang`

Tracks language alternates per URL. Often auto-populated; manual override available.

| Field | Type | Notes |
|-------|------|-------|
| `res_model` | Char, required | |
| `res_id` | Integer, required | |
| `lang_id` | Many2one `res.lang`, required | |
| `url` | Char, required | Absolute URL |
| `is_xdefault` | Boolean, default False | If True, emitted as `hreflang="x-default"` |

### 12.2 Auto-population

On any record that inherits `era.seo.mixin`, after write, compute alternates by:

1. Get the record's available translations from `_translate_fields`.
2. For each active website language, resolve the canonical URL with that lang prefix.
3. Upsert into `era.seo.hreflang`.

### 12.3 Frontend rendering

Template `era_seo_manager.hreflang_links`:

```xml
<t t-foreach="hreflang_entries" t-as="hl">
    <link rel="alternate"
          t-att-hreflang="hl.is_xdefault and 'x-default' or hl.lang_id.code"
          t-att-href="hl.url"/>
</t>
```

### 12.4 Definition of Done — Phase 6

- [ ] Multilingual demo site emits `<link rel="alternate" hreflang>` on every translated page.
- [ ] One `x-default` per page enforced (validation).
- [ ] Admin can override the auto-computed URL for any (record, lang) pair.

---

## 13. Phase 7 — SEO audit dashboard

### 13.1 Models

#### 13.1.1 `era.seo.audit.run`

| Field | Type | Notes |
|-------|------|-------|
| `name` | Char | Auto: `Audit YYYY-MM-DD HH:MM` |
| `date_started` | Datetime, readonly | |
| `date_finished` | Datetime, readonly | |
| `state` | Selection | `draft` / `running` / `done` / `failed` |
| `website_id` | Many2one `website` | Scope |
| `pages_scanned` | Integer, readonly | |
| `finding_ids` | One2many `era.seo.audit.finding` | |
| `critical_count` | Integer, computed | |
| `warning_count` | Integer, computed | |
| `info_count` | Integer, computed | |

#### 13.1.2 `era.seo.audit.finding`

| Field | Type | Notes |
|-------|------|-------|
| `run_id` | Many2one `era.seo.audit.run`, required | |
| `severity` | Selection | `critical` / `warning` / `info` |
| `check_code` | Char, indexed | E.g. `missing_meta_description` |
| `check_name` | Char | Human-readable |
| `res_model` | Char | |
| `res_id` | Integer | |
| `url` | Char | |
| `details` | Text | |
| `suggested_fix` | Text | |
| `is_resolved` | Boolean, default False | |

### 13.2 Audit checks (must-have list)

| Code | Severity | Description |
|------|----------|-------------|
| `missing_seo_title` | critical | Page has no `seo_title` and falls back to website name |
| `title_too_long` | warning | `seo_title_length > 60` |
| `title_too_short` | info | `seo_title_length < 20` |
| `duplicate_seo_title` | critical | Same title on 2+ pages of same website/lang |
| `missing_meta_description` | critical | No `seo_description` |
| `description_too_long` | warning | > 160 chars |
| `description_too_short` | info | < 70 chars |
| `duplicate_meta_description` | warning | Same description on 2+ pages |
| `missing_og_image` | warning | No OG image set |
| `og_image_too_small` | warning | < 1200×630 |
| `missing_canonical` | info | No canonical override AND auto-canonical not resolvable |
| `noindex_in_sitemap` | critical | Page is `noindex` but `seo_sitemap_include=True` |
| `missing_h1` | critical | No `<h1>` in rendered content |
| `multiple_h1` | warning | More than one `<h1>` |
| `image_missing_alt` | warning | `<img>` without `alt` (per image) |
| `slug_too_long` | info | URL slug > 75 chars |
| `slug_contains_uppercase` | info | |
| `slug_contains_stopwords` | info | Stop-word list per language |
| `missing_schema` | warning | Page has no schema instances |
| `broken_redirect_chain` | critical | Redirect chain length > 3 |
| `redirect_loop` | critical | |
| `orphan_page` | warning | No internal links point to it |
| `thin_content` | warning | Rendered text < 300 words |

### 13.3 Execution

A queued background job (use `queue_job` if available, else cron):

```python
def action_run_audit(self):
    self.write({'state': 'running', 'date_started': fields.Datetime.now()})
    for check_method in self._get_check_methods():
        check_method()
    self.write({'state': 'done', 'date_finished': fields.Datetime.now()})
```

Each `check_method` writes findings as it goes.

### 13.4 Dashboard view

OWL component (`seo_dashboard.js`) showing:

- KPI cards: total pages, critical, warning, info, resolved-this-month
- Findings list with severity filters, search, bulk-resolve
- "Run audit now" button
- Per-page drill-down

### 13.5 Definition of Done — Phase 7

- [ ] All 22+ checks listed implemented.
- [ ] Running an audit on a 100-page site completes in under 60s.
- [ ] Dashboard renders correctly, supports filtering by severity.
- [ ] Findings expose a "Go to page" action that opens the offending record in form view.
- [ ] Resolved findings can be archived.

---

## 14. Phase 8 — Content blocks

### 14.1 Model: `era.content.block`

A reusable schema-aware QWeb partial, editable from the admin.

| Field | Type | Notes |
|-------|------|-------|
| `name` | Char, required | |
| `code` | Char, unique, required | Reference key for QWeb (`t-call`) |
| `block_type` | Selection | `faq` / `cta` / `author_box` / `related_posts` / `breadcrumbs` / `feature_grid` / `pricing_table` / `testimonial` / `custom` |
| `content_html` | Html, translate | The block content |
| `schema_template_id` | Many2one `era.seo.schema.template` | If set, auto-attaches schema when rendered |
| Inherits `era.seo.mixin` | | |

### 14.2 Snippets

Ship as `web_editor` snippets visible in the website builder:

- ERA: FAQ Accordion (renders `FAQPage` schema automatically)
- ERA: CTA Block
- ERA: Author Box
- ERA: Related Posts Carousel
- ERA: Breadcrumbs (auto-builds `BreadcrumbList` schema)
- ERA: Feature Grid
- ERA: Pricing Table

Each snippet has options panel for the typical knobs (columns, dark/light, etc.).

### 14.3 Definition of Done — Phase 8

- [ ] All 7 snippets installable and dragable in the website builder.
- [ ] FAQ snippet auto-injects `FAQPage` JSON-LD when published.
- [ ] Breadcrumbs snippet auto-builds breadcrumb chain from URL.

---

## 15. Security

### 15.1 Groups

Add two groups in `seo_security.xml`:

- `group_era_seo_user` — read SEO fields, no edit.
- `group_era_seo_manager` — full CRUD on all SEO models. Implies `group_era_seo_user` and `website.group_website_designer`.

### 15.2 Record rules

- Multi-website: SEO records scoped to a `website_id` are visible only to managers of that website. Use `website.website_id` rules pattern from stock `website` module.
- 404 log entries are admin-only.

### 15.3 ACLs

Standard `ir.model.access.csv` entries for every new model, with read for `group_era_seo_user` and full for `group_era_seo_manager`. Public/portal user gets read on `era.seo.schema.instance` and `era.seo.hreflang` (needed for frontend rendering via `sudo()` in controllers).

---

## 16. Settings

Extend `res.config.settings` with an "ERA SEO" section:

- Default Organization name, legal name, logo URL
- Default OG image
- Default Twitter handle
- Social profiles (Twitter, LinkedIn, Facebook, Instagram, YouTube) — used by Organization schema `sameAs`
- Enable hreflang auto-emission (default `True`)
- Enable schema engine (default `True`)
- Default robots policy
- Google Site Verification meta value
- Bing Site Verification meta value

---

## 17. RTL and Arabic

- All views must render correctly in RTL. Test with `lang=ar_001`.
- Field labels include Arabic translations in `i18n/ar.po`.
- The Arabic `.po` file is **required** for v1.0 release.
- Use `dir="auto"` on rendered HTML attributes where mixed content is expected.
- All UI strings using `_()`. No hardcoded English in templates.
- Date displays must respect website language; default to Gregorian (do not introduce Hijri unless explicitly requested).

---

## 18. Performance budget

| Surface | Budget |
|---------|--------|
| Adding meta + schema to a page | < 15ms server-side overhead |
| Sitemap generation, 5k URLs | < 8s |
| Audit run, 1k pages | < 90s |
| Redirect lookup per request | < 2ms (with cache) |
| Schema rendering, 5 instances | < 5ms |

Profile with `werkzeug.middleware.profiler` during dev. No N+1 queries on rendering — preload schema instances per request in a single query.

---

## 19. Testing strategy

### 19.1 Unit tests

`tests/test_*.py` modules. Use `odoo.tests.TransactionCase`. Target coverage: ≥80% on Python files (excluding generated migrations).

### 19.2 Integration tests

`tests/test_controllers.py` uses `HttpCase` to hit `/sitemap.xml`, `/robots.txt`, redirect responses, blog feeds.

### 19.3 Tour tests

`tests/tours/` — JS tours covering:

- Editing SEO fields on a page and seeing them reflected on the frontend
- Creating a schema instance and verifying it appears in source HTML
- Importing a CSV of redirects
- Running an audit and resolving a finding

### 19.4 Manual QA checklist

Documented in `tests/manual_qa.md`. Reviewer runs through all checklist items before any release tag.

---

## 20. Documentation

### 20.1 In-repo

- `README.md` — Installation, feature overview, screenshots, license.
- `CHANGELOG.md` — Keep-a-Changelog format.
- `CLAUDE.md` — Working conventions for AI agents (see separate file).
- `docs/usage.md` — End-user guide.
- `docs/extending.md` — How to add new schema templates and audit checks.
- `docs/migration.md` — Migrating from stock `website` SEO and from competing addons (e.g. OCA `website_seo`).

### 20.2 External

- Arabic user guide (.docx) following ERA's established style for client guides — produced after Phase 9 ships.

---

## 21. Default data and demo

`data/seo_default_settings.xml` ships with:

- Default robots rules (see §10.5)
- All 17 schema templates active
- A "Default Sitemap" config for the first website with rules for `website.page` and `blog.post`

`demo/demo.xml` (loaded only with `--without-demo=False`):

- 3 demo blog posts with full SEO populated
- 1 demo series, 1 demo category, 2 demo authors
- 5 demo redirects covering all redirect types
- 1 completed audit run with 10 findings of mixed severity

---

## 22. Migration and install hooks

`__init__.py` exposes:

```python
def post_init_hook(env):
    # 1. Copy existing website_meta_* values into new SEO fields where empty.
    # 2. Attach default Organization + WebSite schema instances to each website's home page.
    # 3. Generate initial sitemap.
    # 4. Create default robots rules per website if none exist.

def uninstall_hook(env):
    # Clean up no-op: leave SEO data intact in case of reinstall. Document this in README.
```

---

## 23. Open questions for product owner

Items to confirm before development starts. Default given is the recommended choice.

1. **Module key** — `era_seo_manager` (default) vs `era_seo` vs `era_content_seo`. **Decision needed.**
2. **License** — OPL-1 (default, ERA-internal) vs LGPL-3 (publishable to OCA). **Decision needed.**
3. **GSC integration** — defer to v2 (default) or include read-only impressions in v1?
4. **A/B testing** — out of scope for v1 (default).
5. **Multi-company** — every model carries `company_id`? Default: scope by `website_id` only.
6. **Default schema attached on install** — Organization + WebSite only (default) or all 17 active by default?

---

## 24. Acceptance — what "done" looks like at v1.0.0

- [ ] Module installs cleanly on a fresh Odoo 19 instance with `website`, `website_blog` enabled.
- [ ] All 9 phases delivered with passing tests.
- [ ] Demo site shows:
  - 100/100 on Lighthouse SEO
  - All Rich Results Test types valid for the home page (Organization, WebSite, MobileApplication, FAQPage)
  - Sitemap and robots.txt produced from admin
  - Blog post with reading time, TOC, FAQ, related posts, breadcrumbs, full schema
- [ ] Audit dashboard reports zero critical findings on demo data.
- [ ] Arabic translation 100% complete.
- [ ] README + usage docs published in the repo.
- [ ] One ERA client (Fatoratec) migrated as a real-world reference.

---

*End of specification. Companion document: `CLAUDE.md`.*
