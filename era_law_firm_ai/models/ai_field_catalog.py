"""What may be sent, as a list of things a lawyer recognises.

The request used to ask for a comma-separated list of technical field names and
then send whatever free text the user had typed, which meant the whitelist policed
a string nobody read while the actual payload went unchecked.

Now the catalogue below is the whitelist, the lawyer ticks entries by their Arabic
name, and the payload is assembled from exactly those entries. The free-text box
is for the lawyer's own instructions, and it is redacted with everything else.
"""

from odoo import _, api, fields, models


class LegalAIField(models.Model):
    _name = 'legal.ai.field'
    _description = 'Legal AI Shareable Field'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True,
                       help="What this is called in the request form. Lawyers pick from these, "
                            "never from technical field names.")
    technical_name = fields.Char(required=True,
                                 help="The field actually read. Anything not listed here cannot be sent at all.")
    source = fields.Selection([('case', 'Case'), ('document', 'Document')], required=True, default='case',
                              help="Where the value is read from.")
    sequence = fields.Integer(default=10)
    description = fields.Text(translate=True,
                              help="Shown to the lawyer so they know what they are about to share.")
    sensitive = fields.Boolean(help="Carries more than a label -- narrative text rather than a reference. "
                                    "Shown with a warning and never ticked by default.")

    _technical_unique = models.Constraint('UNIQUE(technical_name, source)',
                                          'This field is already in the catalogue.')

    def _value_for(self, request):
        """The value this entry contributes, rendered for a human reader."""
        self.ensure_one()
        record = request.document_id if self.source == 'document' else request.case_id
        if not record:
            return ''
        if self.technical_name == 'document_text':
            return request._document_text()
        if self.technical_name not in record._fields:
            return ''
        field = record._fields[self.technical_name]
        value = record[self.technical_name]
        if not value:
            return ''
        if field.type == 'selection':
            return dict(field._description_selection(record.env)).get(value, value)
        if field.type == 'many2one':
            return value.display_name
        if field.type == 'html':
            return request._strip_html(value)
        return str(value)
