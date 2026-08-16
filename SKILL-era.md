---
name: era-module-metadata
description: Use when creating a new Era Odoo module, or auditing/fixing an existing one's packaging -- the __manifest__.py author/website/contact fields, the module icon (static/description/icon.png), and the Apps-list description page (static/description/index.html). Trigger on requests like "create a new module", "scaffold a module", editing __manifest__.py, "module icon", "static/description", "module description page", or a module missing an icon.
---

# Era module metadata: author, contact, description, icon

This skill ships two ready-to-copy starting points -- `templates/__manifest__.py.tmpl`
and `templates/index.html.tmpl` -- distilled from Era's best-packaged
modules. Copy them into the new module and fill in the placeholders rather
than starting from a blank file or copying an arbitrary existing module
(most existing modules have at least one packaging mistake -- see the
anti-patterns called out below).

## TL;DR checklist for a new module

- [ ] `'author': 'Era Group'`, `'email': 'info@era.net.sa'`, `'website': 'https://era.net.sa'` in `__manifest__.py`
- [ ] `'category'` is a real Odoo category, not `'Uncategorized'` or an invented one
- [ ] `'description'` is either omitted (falls back to `README.md`) or a real RST feature list (see below) -- never left as Odoo scaffold boilerplate comments
- [ ] `static/description/icon.png` exists, is a real square PNG (not a renamed SVG), 256x256 or 512x512
- [ ] `static/description/index.html` exists for anything `'application': True` or user-facing

## Manifest metadata

```python
'author': 'Era Group',
'email': 'info@era.net.sa',
'website': 'https://era.net.sa',
'license': 'AGPL-3',   # or 'LGPL-3' -- match whichever license the modules this one depends on / sits alongside use
```

- **`author`**: always the literal string `Era Group`. Existing modules in
  this org are inconsistent about this (`ERA`, `Era group`, `ERA Group`,
  and even `Era Group | Developer : <name>` all show up) -- don't copy that
  drift into new modules. Attribute an individual developer in the
  module's `README.md` or a `'maintainer'` key if useful, not by folding a
  name into `author`.
- **`email`**: `info@era.net.sa`. This isn't a field Odoo's UI renders
  anywhere -- it's a plain custom manifest key -- but it's the one place a
  human or script can grep for "who owns this module" without hardcoding
  an individual's personal address. Modules that put a specific
  developer's personal email here break the moment that person leaves the
  company. Always use the team alias.
- **`website`**: `https://era.net.sa`, always with the `https://` scheme.
  Watch for two real mistakes that show up in this org's older modules: a
  bare `era.net.sa` with no scheme, and the leftover Odoo scaffold default
  `https://www.yourcompany.com` that nobody replaced.
- **`category`**: Odoo ships a standard, fixed list of module categories
  (visible under Settings > Technical > ... > Categories, or in any
  `ir.module.category` search) -- pick the one matching the parent app your
  module extends or sits alongside (e.g. `Helpdesk`, `Project`, `CRM`),
  rather than inventing a new category string or leaving the
  `'Uncategorized'` default.

## `description`: it's rendered as reStructuredText

Odoo only falls back to rendering the manifest's `'description'` string
when `static/description/index.html` is **absent** -- and when it does, it
runs that string through docutils as **reStructuredText**, not Markdown
and not raw HTML. That means a `====` line under a title is a real RST
title underline and `*` bullets are a real RST list, not decoration --
write it as actual RST, not loosely-formatted prose:

```python
'description': """
Era <Module Name>
==================

One short paragraph: what this module does and why it exists.

* Bullet describing feature one.
* Bullet describing feature two.
* Bullet describing feature three.
""",
```

If a module ships an `index.html`, the manifest `'description'` is
irrelevant to what users see there -- but keep it anyway (or let it default
to `README.md`) since it's still what shows in command-line tooling and
`ir.module.module` search results.

## The module icon: Odoo enforces nothing, so you must

Odoo's module loader looks for `static/description/icon.png`. If it's
missing, **there is no error** -- the module silently gets the generic
puzzle-piece icon in the Apps list instead. Nothing in the install/upgrade
path ever flags this, which is exactly how it goes unnoticed for years.
Two real failure modes to watch for, both of which already exist
somewhere in this org's modules:

- A file at `static/description/icon.png` that isn't actually a PNG --
  e.g. an SVG export that got renamed to `.png` instead of re-exported.
  Odoo doesn't validate the file contents, so a module can ship with a
  completely broken icon and nothing will ever complain.
- No `static/description/` directory at all, so the module shows the
  generic icon forever.

Requirements for a real icon:
- Actual PNG, square, transparent or solid background.
- 256x256 or 512x512. Going much below ~200px or above ~512px looks wrong
  at the size Odoo actually renders it in the Apps list.
- Verify it's real before shipping -- don't trust the file extension:
  ```bash
  python3 -c "
  import struct
  with open('static/description/icon.png','rb') as f:
      data = f.read(33)
  assert data[:8] == b'\x89PNG\r\n\x1a\n', 'not a PNG'
  w, h = struct.unpack('>II', data[16:24])
  print(f'{w}x{h}')
  "
  ```

If the module also has a `static/description/banner.png` (a larger,
non-square cover image), reference it from the manifest so it shows as the
Apps kanban cover:
```python
'images': ['static/description/banner.png'],
```

## Building `static/description/index.html`

### How it actually renders

- The whole file is passed through Odoo's HTML sanitizer before display.
  Inline `style="..."` attributes survive that pass, but a separate
  `<style>` block or any `<script>` tag is not guaranteed to -- stick to
  inline styles only, no external or block-level CSS/JS.
- Any `<img src="...">` whose `src` is a bare filename (doesn't contain
  `static/` or `//`) gets automatically rewritten to point at that
  module's own `static/description/` folder. So write
  `src="banner.png"` -- not `src="static/description/banner.png"`, which
  is left untouched and will 404.
- Wrap every section in Odoo's own containment classes so it lays out
  correctly inside the Apps page:
  ```html
  <section class="oe_container">
      <div class="oe_row oe_spaced" style="...">
          ...
      </div>
  </section>
  ```

### Content pattern

Write it as a short bilingual pitch, not a manual: lead with the reader's
problem, not a feature dump. Structure that works well:

1. **Hero**: a wide banner image, then an English `<h1>` title + thin-weight
   `<h2>` one-line promise, then the same in Arabic (`<h3 dir="rtl">`),
   then a 2-3 sentence paragraph in English followed immediately by its
   Arabic translation (`dir="rtl"` on the AR version). Every string is
   duplicated EN-then-AR, stacked vertically -- not two side-by-side
   columns.
2. **Feature grid**: a `display:flex; flex-wrap:wrap` row of cards (3-6),
   each with a large emoji as a zero-asset icon, an EN `<h4>` + AR `<h4>`,
   and one EN paragraph + one AR paragraph. Emoji-as-icon avoids needing
   extra image assets.
3. **"How it works"**: a solid-brand-color band with 2-4 numbered steps,
   each one line of EN + one line of AR.
4. **Footer**: a muted one-line tagline (EN + AR) and
   `Era Group · https://era.net.sa · License: <license>`.

A palette that reads as "Era" and is already used in more than one of this
org's modules: `#714B67` (accent), `#1f2d3d` (headings), `#5b6b7c` /
`#6b7785` (body text), `#f8f9fb` / `#f2ecf1` (card backgrounds). Reuse it
for consistency unless Era publishes an official brand kit that says
otherwise.

