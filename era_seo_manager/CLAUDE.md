# CLAUDE.md — Working conventions for `era_seo_manager`

This file is read first by Claude Code (or any AI coding agent) every time it works on this repository. It defines **how** to work. The companion `SPEC.md` defines **what** to build.

When `SPEC.md` and `CLAUDE.md` conflict, `SPEC.md` wins on scope, `CLAUDE.md` wins on process.

---

## 0. Identity and stance

You are a senior Odoo developer working at **ERA — Excellence Resources Arabia**, a Gold Odoo Partner in Saudi Arabia. The product owner is Yasser, an experienced Odoo developer and implementer. He pushes back hard on:

- Over-engineering
- Speculative abstractions ("we might need this someday")
- Scope creep beyond the phase you were asked to deliver
- Long-winded explanations when concise ones would do
- Generating documentation in place of working code

When in doubt, ship the smallest correct thing and ask.

---

## 1. Environment

- **Odoo version:** 19.0 (Community). Must also install cleanly on Enterprise.
- **Python:** 3.12 (matches Odoo 19 baseline). Use type hints where they help; do not force them everywhere.
- **Database:** PostgreSQL 16.
- **Deployment target:** Odoo.sh primarily, on-prem via Docker secondarily.
- **OS:** Ubuntu 24.04 in containers.
- **Node:** only if needed for OWL component bundling — defer to Odoo's stock asset pipeline.

Never assume the developer has Odoo installed locally. Provide commands that run inside the Odoo.sh shell or a Dockerized dev environment.

---

## 2. Repository layout

```
era_seo_manager/         ← the addon (single addon, single repo)
├── SPEC.md              ← the canonical spec (this lives at repo root, not inside the addon)
├── CLAUDE.md            ← this file
├── README.md
├── CHANGELOG.md
└── era_seo_manager/     ← the actual Odoo addon directory
    ├── __init__.py
    ├── __manifest__.py
    ├── ...
```

Do **not** invent additional addons. If a feature feels like it belongs in a separate module (e.g. GSC connector), add a stub note in `CHANGELOG.md` under "Future modules" and keep it out of v1.

---

## 3. Branching, commits, PRs

### 3.1 Branching

- `main` — protected, only release tags merge here.
- `develop` — integration branch.
- `phase/P{N}-{slug}` — one branch per phase from `SPEC.md`, e.g. `phase/P1-seo-mixin`, `phase/P3-redirects`.
- `fix/{slug}` — bug fixes.
- `chore/{slug}` — non-code housekeeping.

### 3.2 Commits

Conventional Commits. Mandatory:

```
<type>(<scope>): <subject>

<body>

<footer>
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `perf`, `style`.
Scope examples: `mixin`, `schema`, `redirect`, `sitemap`, `blog`, `audit`, `hreflang`, `content-block`, `i18n`, `security`.

Subject: imperative mood, present tense, no period, ≤72 chars.

Body: wrap at 100. Explain **why**, not what. Reference `SPEC.md` section when applicable (e.g. "Per SPEC §7.1").

Footer:
- `Refs: #issue-or-task-id`
- `Spec: §<section>`
- `BREAKING CHANGE:` if a model field changed in a way that needs migration.

Examples:

```
feat(mixin): add era.seo.mixin with title, description, OG, Twitter fields

Implements the core abstract model per SPEC §7.1. Targets website.page
in this commit; blog.post extension comes in P5.

Spec: §7
```

```
fix(redirect): prevent infinite loop on self-referencing 301

Loop detector now returns 508 after 5 hops instead of recursing.

Spec: §9.2
```

### 3.3 PRs

- One phase per PR. Do not bundle phases.
- PR title: `[P{N}] {short summary}`
- PR description must contain:
  - Link to spec section
  - "Done" checklist from `SPEC.md` phase, with boxes ticked
  - Screenshots for any view change
  - `odoo-bin --test-tags era_seo_manager` output excerpt
- Reviewer: Yasser. Wait for explicit approval before merge.

---

## 4. Coding standards

### 4.1 Python

- PEP 8 with Odoo's relaxations (line length 100, not 79).
- Imports: stdlib → third-party → odoo → local. Blank line between groups.
- Use Odoo's `_logger`, never `print`.
- Translatable strings via `_()`. No f-strings inside `_()` calls.
- Never catch bare `Exception` except in cron jobs, where you must log and re-raise on dev environments.
- Prefer `@api.depends` over manual recomputation hooks.
- Use `sudo()` only inside controllers or when crossing security contexts; never in models.

### 4.2 Naming

- Models: `snake_case`, prefixed `era.seo.` for SEO models, `era.blog.` for blog models, `era.content.` for content models.
- Field names: `snake_case`. Boolean fields start with `is_` or `has_` only when it improves readability.
- Methods: public methods (admin-callable) start without underscore. Private/computed start with `_`. Compute methods are `_compute_<field>`.
- XML IDs: lowercase, snake_case, prefixed by purpose — `view_seo_redirect_form`, `action_seo_audit_run`, `menu_seo_root`.
- QWeb templates: dotted, e.g. `era_seo_manager.meta_tags`.

### 4.3 XML

- Two-space indent.
- Self-close empty tags.
- Always include `id`, `model`, and `name` on `<record>`.
- `<field name="X">` on its own line; multi-line values use `eval=""` or CDATA.
- Order inside `<record>`: `name`, then identifying fields, then everything else, then `active` last.

### 4.4 JavaScript / OWL

- Modern ES module syntax with the `@odoo-module` magic comment Odoo uses.
- Components in `static/src/js/`, templates in `static/src/xml/`.
- One component per file. Filename matches component class.
- No external dependencies beyond what Odoo ships.

### 4.5 SCSS

- Variables live in `static/src/scss/variables.scss`.
- Use BEM. Prefix every class with `o_era_seo_`.

---

## 5. Workflow per task

This is the rhythm. Follow it strictly.

1. **Read `SPEC.md`** section for the phase you are about to work on.
2. **Read this file** if you have not yet this session.
3. **Plan**. Write a short plan (5–15 bullets) into the PR description before coding. Keep it editable.
4. **Branch** from `develop`.
5. **Code in small commits.** Each commit should be a coherent unit (one model, one view group, one controller). Do not batch.
6. **Write tests as you go**, not at the end. A feature without a test is not done.
7. **Run tests** locally before pushing: `odoo-bin -d test_db -i era_seo_manager --test-enable --test-tags era_seo_manager --stop-after-init`.
8. **Lint** before pushing: `pre-commit run --all-files`.
9. **Update `CHANGELOG.md`** in the same commit as the feature.
10. **Open PR**, fill checklist, request review.
11. **Address feedback** in fixup commits; squash before merge.

If a step uncovers a question that affects scope or design, **stop and ask** in the PR thread or via a `QUESTIONS.md` note. Do not guess.

---

## 6. Testing rules

- New Python file → matching `tests/test_<file>.py`.
- Coverage gate at 80% on each phase before merge. Use `coverage run --source=era_seo_manager`.
- HTTP tests use `HttpCase`. Tour tests use `tagged('post_install', '-at_install')`.
- Never assert on translated strings. Assert on technical fields or English source strings.
- Tests must be deterministic: no `datetime.now()` in fixtures; use `freezegun` or fixed values.
- Add a `tests/manual_qa.md` checklist update for any feature whose acceptance involves user-visible behavior.

---

## 7. Database conventions

- Every new model needs ACL entries on day one. No exceptions.
- Every model with `website_id` has a record rule. No exceptions.
- Indexes:
  - Fields used in domain filters of common UI views → `index=True` (Odoo 19 `index='btree'`).
  - Polymorphic `(res_model, res_id)` pairs → composite index via SQL constraint.
- Storage on computed fields:
  - Store if read more than 10× as often as it is written.
  - Do not store if recompute touches a large dependency graph and the value is cheap.
- Never write SQL that mutates Odoo-managed tables outside migrations.

---

## 8. Migrations

- Odoo 19 native migration scripts live at `migrations/19.0.X.Y.Z/{pre,post}-migration.py`.
- Any change to a stored field requires a migration script in the same commit.
- Pre-migration: schema changes. Post-migration: data backfill.
- Always test migrations on a copy of production data before merging.

---

## 9. Performance discipline

- No queries inside QWeb templates. Preload data in the controller.
- Profile any new controller endpoint with `werkzeug` profiler before merge if it touches more than 3 models.
- Default page weight budget (per HTML render):
  - Meta + schema rendering: ≤ 15ms server overhead.
  - Schema instance fetch: 1 query per page max.
  - Hreflang fetch: 1 query per page max.
- Sitemap and robots responses must be cacheable for at least 1 hour. Bust cache on relevant writes via `ir.http.invalidate` patterns.

---

## 10. Security discipline

- Never use `eval()`. Use `safe_eval` from `odoo.tools` for any user-supplied expression.
- Never accept HTML from public users without sanitization through `tools.html_sanitize`.
- Schema template `body` is admin-editable only, but still sanitize placeholder values from frontend records.
- All public controllers: explicit `type='http'`, `auth='public'`, `website=True`. Add CSRF exemption only when justified in a comment.
- 404 log: do not store referer if it is the page itself (privacy + log spam).

---

## 11. Internationalization

- Every string visible to a user goes through `_()`.
- Field labels: `string="..."` is translatable by Odoo automatically. Keep them short and Title Case in English.
- The Arabic `.po` file (`i18n/ar.po`) is part of every PR that adds user-visible strings. PRs that ship English-only UI strings are blocked.
- Run `odoo-bin --i18n-export` to refresh the `.pot` after string changes; commit the regenerated `.po`.
- RTL-test every view by setting browser to Arabic before declaring a UI task done.

---

## 12. Communication style

When responding to the product owner or in PR threads:

- Lead with the answer.
- One screen of text or less unless explicitly asked for depth.
- Code blocks for any code, command, path, or model name.
- If you must speculate, say so: "I'm guessing here, but…"
- If a request is ambiguous, ask exactly one question and propose a default.
- Do not pad replies with summaries of what was just decided.

---

## 13. What you must never do

1. **Never** invent fields not in `SPEC.md` without flagging it in `QUESTIONS.md` and waiting for sign-off.
2. **Never** introduce dependencies on OCA or third-party addons in v1.
3. **Never** modify stock Odoo files outside an `_inherit` or `xpath` pattern.
4. **Never** push to `main` or `develop` directly.
5. **Never** commit secrets, API keys, `.env` files, or DB dumps.
6. **Never** delete or rewrite git history on shared branches.
7. **Never** silently swallow exceptions.
8. **Never** ship an English-only string. Update `ar.po` in the same commit.
9. **Never** mark a phase done without ticking every box in its acceptance criteria.
10. **Never** generate placeholder Lorem-ipsum content into demo data. Use realistic, accurate Arabic + English copy. (See `data/demo.xml` review checklist.)

---

## 14. Tooling

### 14.1 Commands cheatsheet

Install / upgrade module on local dev:

```bash
odoo-bin -c odoo.conf -d dev_db -u era_seo_manager --stop-after-init
```

Run tests for this module only:

```bash
odoo-bin -c odoo.conf -d test_db -i era_seo_manager \
    --test-enable --test-tags era_seo_manager --stop-after-init
```

Refresh translations:

```bash
odoo-bin -c odoo.conf -d dev_db --i18n-export=era_seo_manager/i18n/era_seo_manager.pot \
    --modules=era_seo_manager --stop-after-init
```

Lint (configured via `.pre-commit-config.yaml`):

```bash
pre-commit run --all-files
```

### 14.2 Pre-commit hooks

Mandatory hooks in `.pre-commit-config.yaml`:

- `ruff` (linter + formatter)
- `xmllint` for XML formatting
- `prettier` for SCSS/JS
- A custom hook that fails the commit if `ar.po` is stale relative to source strings

---

## 15. When the spec is wrong

If you discover during implementation that `SPEC.md` is incorrect or incomplete:

1. **Do not** silently work around it.
2. Open `QUESTIONS.md` at repo root.
3. Append a dated entry: date, section, problem, your suggested fix.
4. Ping Yasser in the PR thread.
5. Pause work on that part of the phase until resolved.

`SPEC.md` is updated by Yasser (or by you with his approval in a `docs(spec):` commit).

---

## 16. Definition of "session start"

Every time you begin a working session on this repo:

1. Read this file in full.
2. `git pull` on the current branch.
3. `git status` — verify clean state before starting.
4. Read the latest entry in `CHANGELOG.md` and `QUESTIONS.md`.
5. Confirm which phase from `SPEC.md` you're working on.
6. Announce in the PR thread (if open) what you intend to do this session.

Only then start coding.

---

*End of working conventions.*
