"""Jurisdiction, court and circuit as records instead of free text.

Seeded from نظام القضاء (Royal Decree M/78, 19/9/1428H): article 9 for the courts,
article 16 for appeal circuits, articles 19-23 for first-instance circuits. The
administrative judiciary is separate, under نظام ديوان المظالم.

These are starting points, not a closed list -- every one of the three fields
accepts a new value typed straight into the dropdown, because the Supreme
Judicial Council may create further specialised courts and circuits.
"""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class LegalJurisdiction(models.Model):
    _name = 'legal.jurisdiction'
    _description = 'Judicial Jurisdiction'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True,
                       help="The branch of the judiciary the matter falls under.")
    sequence = fields.Integer(default=10)
    code = fields.Char(help="Short code used in reports and reference lists.")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', index=True,
                                 help="Leave empty to share across every company.")
    court_ids = fields.One2many('legal.court', 'jurisdiction_id')
    note = fields.Text(help="Statutory basis or a note on what this jurisdiction covers.")

    _name_unique = models.Constraint('UNIQUE(name, company_id)',
                                     'This jurisdiction already exists.')


class LegalCourt(models.Model):
    _name = 'legal.court'
    _description = 'Court'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True,
                       help="The court as it is named in the Najiz record, for example the Commercial Court in Riyadh.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', index=True,
                                 help="Leave empty to share across every company.")
    jurisdiction_id = fields.Many2one('legal.jurisdiction', index=True,
                                      help="The branch of the judiciary this court belongs to. Setting it filters the court list on a case.")
    degree = fields.Selection([
        ('supreme', 'Supreme Court'),
        ('appeal', 'Court of Appeal'),
        ('first', 'First Instance'),
    ], default='first', help="Degree of litigation, per article 9 of the Law of the Judiciary.")
    city = fields.Char(help="Where the court sits.")
    circuit_ids = fields.One2many('legal.circuit', 'court_id')
    note = fields.Text()

    _name_unique = models.Constraint('UNIQUE(name, city, company_id)',
                                     'This court already exists for that city.')

    @api.depends('name', 'city')
    def _compute_display_name(self):
        for record in self:
            record.display_name = f'{record.name} - {record.city}' if record.city else record.name


class LegalCircuit(models.Model):
    _name = 'legal.circuit'
    _description = 'Judicial Circuit'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True,
                       help="The circuit or bench inside the court that hears the case.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', index=True,
                                 help="Leave empty to share across every company.")
    court_id = fields.Many2one('legal.court', index=True,
                               help="The court this circuit sits in. Leave empty for a generic circuit type usable in any court.")
    jurisdiction_id = fields.Many2one('legal.jurisdiction', index=True,
                                      help="Branch of the judiciary, used to filter the list when no court is chosen yet.")
    note = fields.Text(help="Statutory basis for this circuit.")


class LegalCase(models.Model):
    _inherit = 'legal.case'

    jurisdiction_id = fields.Many2one(
        'legal.jurisdiction', string='Jurisdiction', tracking=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        help="The branch of the judiciary the case falls under. Pick from the list or type a new one -- the Supreme Judicial Council may create further specialised courts.")
    court_id = fields.Many2one(
        'legal.court', string='Court', tracking=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        help="The court hearing the case. The list narrows to the chosen jurisdiction; type a new name to add a court that is not listed yet.")
    circuit_id = fields.Many2one(
        'legal.circuit', string='Circuit', tracking=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        help="The circuit or bench inside the court. The list narrows to the chosen court; type a new name to add one.")

    @api.onchange('jurisdiction_id')
    def _onchange_jurisdiction_id(self):
        if self.jurisdiction_id and self.court_id.jurisdiction_id != self.jurisdiction_id:
            self.court_id = False

    @api.onchange('court_id')
    def _onchange_court_id(self):
        if self.court_id:
            if not self.jurisdiction_id:
                self.jurisdiction_id = self.court_id.jurisdiction_id
            if self.circuit_id.court_id and self.circuit_id.court_id != self.court_id:
                self.circuit_id = False
            if self.court_id.city and not self.city:
                self.city = self.court_id.city


class LegalJudiciaryMigration(models.AbstractModel):
    """Move the old free-text jurisdiction/court/circuit onto records.

    Runs on install and on every update. Text that already matches a record is
    linked to it; anything else becomes a new record, so nothing a firm typed
    before the change is lost.
    """
    _name = 'legal.judiciary.migration'
    _description = 'Judiciary Text to Record Migration'

    @api.model
    def _match_or_create(self, model, name, values=None):
        name = (name or '').strip()
        if not name:
            return False
        Model = self.env[model]
        record = Model.with_context(active_test=False).search([('name', '=ilike', name)], limit=1)
        if record:
            return record
        return Model.create(dict(values or {}, name=name))

    @api.model
    def _run(self):
        cases = self.env['legal.case'].with_context(active_test=False).search([
            '|', '|', ('jurisdiction', '!=', False), ('court', '!=', False), ('circuit', '!=', False),
        ])
        migrated = 0
        for case in cases:
            values = {}
            if case.jurisdiction and not case.jurisdiction_id:
                values['jurisdiction_id'] = self._match_or_create(
                    'legal.jurisdiction', case.jurisdiction).id
            if case.court and not case.court_id:
                values['court_id'] = self._match_or_create('legal.court', case.court, {
                    'city': case.city or False,
                    'jurisdiction_id': values.get('jurisdiction_id') or case.jurisdiction_id.id or False,
                }).id
            if case.circuit and not case.circuit_id:
                values['circuit_id'] = self._match_or_create('legal.circuit', case.circuit, {
                    'court_id': values.get('court_id') or case.court_id.id or False,
                }).id
            if values:
                case.write(values)
                migrated += 1
        if migrated:
            _logger.info('era_law_firm: migrated judiciary text to records on %s case(s)', migrated)
