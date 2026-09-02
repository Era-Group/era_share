# Era Saudi Law Firm Management

Operational case management for Saudi law firms: case intake, conflict checks, parties, hearings, deadlines, document review, engagements, time, expenses, invoicing, portal access and segregated client-trust accounting.

Cases move from draft through a mandatory conflict check to confirmation and closure. Company and team record rules isolate legal files. Identity fields are restricted to legal managers. Client trust money is posted through standard Odoo journal entries to a separately configured liability account; applying trust funds is deliberately separate from invoice creation.

The app opens on a role-aware dashboard — open files, the week's hearings, overdue deadlines, screenings still pending, work billable but not invoiced — where each tile opens exactly the list its number counted, and a supervisor switches between the whole firm and their own work. Conflict screening runs firm-wide regardless of who starts it, grades each match (same contact record, same identity or registration number, same name after Arabic normalisation), and keeps a filterable register. The client portal serves accepted cases in Arabic — confirmed hearings, visible parties, published and approved documents, the client's own invoices and their trust balance — with the firm's internal fields refused at the ORM, not merely omitted from the page.

Scheduled jobs belonging to this project are restored at every server start, so a database restored onto a demo or staging server still reminds, retries and indexes; set `era.autostart_crons` to 0 to stop that. Outgoing mail stays disabled by its own separate mechanism.

Najiz numbers, links and Hijri dates are structured manual references. This module does not claim or perform an automated Najiz integration. Saudi taxes and e-invoicing remain handled by `l10n_sa` and `l10n_sa_edi`.

Documentation: [`docs/USER_GUIDE_AR.md`](docs/USER_GUIDE_AR.md) for daily use in Arabic, `DEPLOYMENT.md` for installing and configuring an instance.

License: LGPL-3. Maintained by Era Group (`info@era.net.sa`).
