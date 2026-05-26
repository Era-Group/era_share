# Kick-off prompt for Claude Code

Paste the following message into Claude Code at the root of a fresh empty repository. It links to the two spec files and instructs Claude Code to scaffold the project and start with Phase 1.

---

```
I am building a new Odoo 19 addon called `era_seo_manager`.

Two specification files are at the repo root:
- SPEC.md — the canonical specification (what to build)
- CLAUDE.md — your working conventions (how to work)

Read both files in full before doing anything else.

Your first session has three tasks, in order:

1. Scaffold the repository per SPEC §4 (Module structure).
   - Create the `era_seo_manager/` directory with all subdirectories and empty `__init__.py` files.
   - Create a minimal `__manifest__.py` per SPEC §5, with `data` and `assets` lists populated but all referenced XML files created as empty stubs containing only `<odoo></odoo>`.
   - Add `.pre-commit-config.yaml` with the hooks listed in CLAUDE.md §14.2.
   - Add a `pyproject.toml` with `ruff` configured (line-length 100, target Python 3.12, Odoo-friendly ignores).
   - Add a stub `README.md`, `CHANGELOG.md`, and empty `QUESTIONS.md`.

2. Verify the empty module installs on a fresh Odoo 19 instance.
   - Provide the exact command to install it.
   - If you cannot run Odoo in your sandbox, write a `tests/manual_qa.md` install-smoke entry that I can run.

3. Begin Phase 1 (SEO core mixin) per SPEC §7.
   - Branch from `develop` as `phase/P1-seo-mixin`.
   - Write the plan in the PR description before coding.
   - Implement `era.seo.mixin`, the `website.page` inheritance, the QWeb meta template, the `post_init_hook` data migration, and the matching tests (`tests/test_seo_mixin.py`).
   - Stop after Phase 1 acceptance criteria (SPEC §7.4) are met. Do not start Phase 2.

Open question to resolve before you start coding Phase 1: SPEC §23 question 1 — module key. Default is `era_seo_manager`. Confirm with me or proceed with the default if I do not respond within your first session.

Adhere to CLAUDE.md §13 — the "never do" list — without exception.
```

---

## After Phase 1

Once Phase 1 is merged, kick off the next phase with a much shorter prompt:

```
Begin Phase 2 (JSON-LD schema engine) per SPEC §8.

Branch: phase/P2-schema-engine.
Stop at SPEC §8.6 acceptance.

Re-read CLAUDE.md if it has been more than one working session since you last did.
```

Repeat for each subsequent phase.
