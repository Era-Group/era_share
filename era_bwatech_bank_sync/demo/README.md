# Demo seed — bank reconciliation walkthrough

`seed_demo_database.py` builds a teaching database: a Saudi company in SAR,
four customer invoices, one vendor bill, a BWATECH connection wired to the bank
journal, and six unposted transactions — each one a different reconciliation
situation (exact match, partial payment, one payment for two invoices, vendor
payment, bank fee, unidentified deposit).

It is **not** loaded by the manifest. Run it by hand:

    odoo-bin shell -c odoo.conf -d <db> --no-http < demo/seed_demo_database.py

The certificate it stores is an obvious placeholder; the script never calls the
BWATECH API.
