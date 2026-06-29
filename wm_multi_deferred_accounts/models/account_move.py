# -*- coding: utf-8 -*-
# Copyright 2020 CorTex IT Solutions Ltd. (<https://cortexsolutions.net/>)
# License OPL-1

from odoo import models, fields, _, api, Command
from odoo.exceptions import UserError
from itertools import chain

class AccountMove(models.Model):
    _inherit = 'account.move'

    # Override deferred account
    def _generate_deferred_entries(self):
        """
        Generates the deferred entries for the invoice.
        """
        self.ensure_one()
        if self.state != 'posted':
            return

        deferred_type = "expense" if self.is_purchase_document() else "revenue"
        deferred_account = self.company_id.deferred_expense_account_id if deferred_type == "expense" else self.company_id.deferred_revenue_account_id
        deferred_journal = self.company_id.deferred_expense_journal_id if deferred_type == "expense" else self.company_id.deferred_revenue_journal_id
        if not deferred_journal:
            raise UserError(_("Please set the deferred journal in the accounting settings."))
        if not deferred_account:
            raise UserError(_("Please set the deferred accounts in the accounting settings."))

        moves_vals_to_create = []
        lines_vals_to_create = []
        lines_periods = []
        for line in self.line_ids.filtered(lambda l: l.deferred_start_date and l.deferred_end_date):
            periods = line._get_deferred_periods()
            if not periods:
                continue

            # [1] Waleed Update: This below two lines added to use deferred account for each line
            if line.deferred_account_id:
                deferred_account = line.deferred_account_id
            # End of [1]

            ref = _("Deferral of %s", line.move_id.name or '')

            moves_vals_to_create.append({
                'move_type': 'entry',
                'deferred_original_move_ids': [Command.set(line.move_id.ids)],
                'journal_id': deferred_journal.id,
                'company_id': self.company_id.id,
                'partner_id': line.partner_id.id,
                'auto_post': 'at_date',
                'ref': ref,
                'name': False,
                'date': line.move_id.date,
            })
            lines_vals_to_create.append([
                self.env['account.move.line']._get_deferred_lines_values(account.id, coeff * line.balance, ref, line.analytic_distribution, line)
                for (account, coeff) in [(line.account_id, -1), (deferred_account, 1)]
            ])
            lines_periods.append((line, periods))
        # create the deferred moves
        moves_fully_deferred = self.create(moves_vals_to_create)
        # We write the lines after creation, to make sure the `deferred_original_move_ids` is set.
        # This way we can avoid adding taxes for deferred moves.
        for move_fully_deferred, lines_vals in zip(moves_fully_deferred, lines_vals_to_create):
            for line_vals in lines_vals:
                # This will link the moves to the lines. Instead of move.write('line_ids': lines_ids)
                line_vals['move_id'] = move_fully_deferred.id
        self.env['account.move.line'].create(list(chain(*lines_vals_to_create)))

        deferral_moves_vals = []
        deferral_moves_line_vals = []
        # Create the deferred entries for the periods [deferred_start_date, deferred_end_date]
        for (line, periods), move_vals in zip(lines_periods, moves_vals_to_create):
            remaining_balance = line.balance
            for period_index, period in enumerate(periods):
                # For the last deferral move the balance is forced to remaining balance to avoid rounding errors
                force_balance = remaining_balance if period_index == len(periods) - 1 else None
                deferred_amounts = self._get_deferred_amounts_by_line(line, [period], deferred_type)[0]
                balance = deferred_amounts[period] if force_balance is None else force_balance
                remaining_balance -= line.currency_id.round(balance)
                deferral_moves_vals.append({**move_vals, 'date': period[1]})
                deferral_moves_line_vals.append([
                    {
                        **self.env['account.move.line']._get_deferred_lines_values(account.id, coeff * balance, move_vals['ref'], line.analytic_distribution, line),
                        'partner_id': line.partner_id.id,
                        'product_id': line.product_id.id,
                    }
                    for (account, coeff) in [(deferred_amounts['account_id'], 1), (deferred_account, -1)]
                ])

        deferral_moves = self.create(deferral_moves_vals)
        for deferral_move, lines_vals in zip(deferral_moves, deferral_moves_line_vals):
            for line_vals in lines_vals:
                # This will link the moves to the lines. Instead of move.write('line_ids': lines_ids)
                line_vals['move_id'] = deferral_move.id
        self.env['account.move.line'].create(list(chain(*deferral_moves_line_vals)))

        to_unlink = deferral_moves.filtered(lambda move: move.currency_id.is_zero(move.amount_total))
        for move_fully_deferred in moves_fully_deferred:
            # If, after calculation, we have 2 deferral entries in the same month, it means that
            # they simply cancel out each other, so there is no point in creating them.
            deferred_move_ids = move_fully_deferred + deferral_moves
            cancelling_moves = deferred_move_ids.filtered(lambda move:
                move_fully_deferred.date.replace(day=1) == move.date.replace(day=1)
                and move.amount_total == move_fully_deferred.amount_total
            )
            if len(cancelling_moves) == 2:
                to_unlink |= cancelling_moves
                continue

        to_unlink.unlink()
        (moves_fully_deferred + deferral_moves - to_unlink)._post(soft=True)