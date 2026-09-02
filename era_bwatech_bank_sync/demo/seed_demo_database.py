# -*- coding: utf-8 -*-
"""Seed a teaching database for BWATECH -> Odoo bank reconciliation.

Six deliberately different bank transactions, each demonstrating one
reconciliation situation an accountant actually meets.
"""
import base64
import json
from datetime import date

from odoo import Command

company = env.ref("base.main_company")

# ---------------------------------------------------------------- company
sar = env["res.currency"].with_context(active_test=False).search(
    [("name", "=", "SAR")], limit=1)
assert sar, "SAR currency row not found"
sar.active = True
company.write({
    "name": "ERA Group",
    "currency_id": sar.id,
    "country_id": env.ref("base.sa").id,
})
print("company:", company.name, company.currency_id.name)

journal = env["account.journal"].search(
    [("type", "=", "bank"), ("company_id", "=", company.id)], limit=1)
journal.write({"name": "بنك الراجحي - الجاري", "currency_id": False})
assert journal.suspense_account_id, "bank journal has no suspense account"
print("journal:", journal.code, journal.suspense_account_id.display_name)

# ---------------------------------------------------------------- partners
def partner(name, is_company=True):
    rec = env["res.partner"].search([("name", "=", name)], limit=1)
    return rec or env["res.partner"].create({"name": name, "is_company": is_company})

C1 = partner("مؤسسة الأفق للتجارة")
C2 = partner("شركة النخبة للمقاولات")
C3 = partner("مجموعة الواحة القابضة")
V1 = partner("شركة الإمداد للتوريدات")

# ---------------------------------------------------------------- invoices
def make_move(move_type, prt, amount, day, label):
    move = env["account.move"].create({
        "move_type": move_type,
        "partner_id": prt.id,
        "invoice_date": date(2026, 8, day),
        "date": date(2026, 8, day),
        "currency_id": sar.id,
        "invoice_line_ids": [Command.create({
            "name": label,
            "quantity": 1.0,
            "price_unit": amount,
            "tax_ids": [Command.clear()],
        })],
    })
    move.action_post()
    return move

INV_A = make_move("out_invoice", C1, 11500.00, 5,  "توريد أجهزة حاسب")
INV_B = make_move("out_invoice", C2, 25000.00, 8,  "أعمال تشطيبات - الدفعة الأولى")
INV_C = make_move("out_invoice", C3,  4300.00, 10, "اشتراك صيانة ربع سنوي")
INV_D = make_move("out_invoice", C3,  6150.00, 12, "قطع غيار")
BILL_A = make_move("in_invoice", V1, 18750.00, 6,  "توريد مواد خام")

for m in (INV_A, INV_B, INV_C, INV_D, BILL_A):
    print("  %-14s %-28s %12.2f  residual %10.2f" % (
        m.name, m.partner_id.name, m.amount_total, m.amount_residual))

# ---------------------------------------------------------------- BWATECH
conn = env["bwatech.connection"].create({
    "name": "بواتك - بيئة تجريبية",
    "environment": "test",
    "client_id": "DEMO-CLIENT-ID",
    "auto_sync": False,
    "verify_ssl": False,
    # Obvious placeholder: this database never calls the real API.
    "certificate_file": base64.b64encode(
        b"-----BEGIN CERTIFICATE-----\n"
        b"PLACEHOLDER - DEMO DATABASE - NOT A REAL CERTIFICATE\n"
        b"-----END CERTIFICATE-----\n"),
    "certificate_filename": "demo-placeholder.pem",
})

acct = env["bwatech.bank.account"].create({
    "connection_id": conn.id,
    "account_name": "الحساب الجاري الرئيسي",
    "iban": "SA0380000000608010167519",
    "bban": "608010167519",
    "bank_name": "مصرف الراجحي",
    "bank_code": "RJHISARI",
    "currency_code": "SAR",
    "entity_name": company.name,
    "journal_id": journal.id,
    "balance": 412350.75,
    "auto_post": False,
})
print("bwatech account:", acct.display_account, acct.currency_id.name)

# Six transactions, each one a different reconciliation lesson.
TX = [
    # (seq, day, amount, description, third party, customer ref, type)
    (100101, 15,  11500.00, "تحويل وارد سداد فاتورة %s" % INV_A.name,
     C1.name, INV_A.name, "حوالة واردة"),
    (100102, 17,  10000.00, "دفعة تحت الحساب - %s" % C2.name,
     C2.name, INV_B.name, "حوالة واردة"),
    (100103, 18,  10450.00, "سداد دفعة واحدة عن فاتورتين",
     C3.name, "%s + %s" % (INV_C.name, INV_D.name), "حوالة واردة"),
    (100104, 19, -18750.00, "حوالة صادرة سداد فاتورة مورد %s" % BILL_A.name,
     V1.name, BILL_A.name, "حوالة صادرة"),
    (100105, 20,    -75.00, "رسوم خدمات بنكية شهرية",
     "مصرف الراجحي", "FEE-2026-08", "رسوم"),
    (100106, 21,   6000.00, "إيداع نقدي بفرع العليا",
     False, False, "إيداع نقدي"),
]

for seq, day, amount, desc, third, cref, ttype in TX:
    payload = {
        "sequenceNumber": seq,
        "amount": abs(amount),
        "creditDebitIndicator": "CRDT" if amount > 0 else "DBIT",
        "valueDate": "2026-08-%02dT00:00:00" % day,
        "transactionDescription": desc,
        "thirdPartyName": third or "",
        "customerReference": cref or "",
    }
    env["bwatech.transaction"].create({
        "connection_id": conn.id,
        "bank_account_id": acct.id,
        "sequence_number": seq,
        "bank_code": "RJHISARI",
        "account_number": acct.iban,
        "amount": amount,
        "value_date": "2026-08-%02d 09:00:00" % day,
        "entry_date": "2026-08-%02d 09:00:00" % day,
        "transaction_type_code": "TRF",
        "transaction_type_description": ttype,
        "bank_reference": "RJH%08d" % seq,
        "customer_reference": cref or False,
        "transaction_description": desc,
        "third_party_name": third or False,
        "third_party_account_number": "SA1122334455667788990011" if third else False,
        "raw_payload": json.dumps(payload, ensure_ascii=False, indent=2),
    })
acct.last_sequence_number = TX[-1][0]
print("transactions created:", len(TX), "unposted:", acct.unposted_count)

# ------------------------------------------------- bank-fee write-off rule
fee_account = env["account.account"].search(
    [("account_type", "=", "expense")], limit=1)
model = env["account.reconcile.model"].create({
    "name": "رسوم بنكية - تحميل مباشر",
    "company_id": company.id,
    "trigger": "manual",
    "match_journal_ids": [Command.set(journal.ids)],
    "match_label": "contains",
    "match_label_param": "رسوم",
    "match_amount": "lower",
    "match_amount_max": 500.0,
    "line_ids": [Command.create({
        "account_id": fee_account.id,
        "label": "رسوم بنكية",
        "amount_type": "percentage",
        "amount_string": "100",
    })],
})
print("reconcile model:", model.name, "-> account", fee_account.display_name,
      "| can_be_proposed:", model.can_be_proposed)

env.cr.commit()
print("SEED OK")
