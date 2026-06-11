# Manual QA Checklist

Run through all items below before tagging any release.

---

## Install smoke (Task 2 — empty module)

```bash
odoo-bin -c odoo.conf -d test_db -i era_seo_manager --stop-after-init
```

- [ ] Module installs with no errors in the log.
- [ ] `era_seo_manager` appears in **Settings → Apps** as installed.
- [ ] No Python traceback in `/var/log/odoo/odoo-server.log`.

---

## Phase 1 — Core SEO mixin

### Backend checks

1. Open **Website → Pages** → click any page.
2. Verify a **"SEO"** notebook tab is visible with the following fields:
   - SEO Title, Meta Description, Meta Keywords
   - OG Title, OG Description, OG Image
   - Twitter Card, Twitter @site, Twitter @creator
   - Canonical URL Override
   - Index / Follow / Allow archive / Allow snippet checkboxes
   - Include in sitemap, Priority, Change frequency
3. Enter a value in **SEO Title** (e.g. "Test SEO Title") and save.

### Frontend checks

4. Open the page in a new browser tab.
5. View page source (`Ctrl+U`).
6. Verify:
   - [ ] `<title>Test SEO Title</title>` is present (not the fallback website name).
   - [ ] `<meta name="description" content="...">` reflects the saved description.
   - [ ] `<meta name="robots" content="index, follow">` is present.
   - [ ] `<link rel="canonical" href="...">` is present.
   - [ ] `<meta property="og:title" ...>` is present.
   - [ ] `<meta name="twitter:card" ...>` is present.

### Canonical override check

7. Set **Canonical URL Override** to `https://example.com/alt-page` and save.
8. Reload the page, view source.
9. Verify `<link rel="canonical" href="https://example.com/alt-page">`.

### Robots directive check

10. Uncheck **Index this page** and **Follow links on this page**, save.
11. Reload, view source.
12. Verify `<meta name="robots" content="noindex, nofollow">`.

### Post-install migration check

13. Before install, note any existing page with a value in `website_meta_title`.
14. After install, open that page in the admin.
15. Verify **SEO Title** is pre-filled with the old `website_meta_title` value.
