import json
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Matches {{ ... }} placeholders for the strip-before-JSON-validate step.
_PLACEHOLDER_RE = re.compile(r'\{\{[^}]+\}\}')


class EraSeoSchemaTemplate(models.Model):
    """Reusable JSON-LD template with {{ placeholder }} syntax.

    A template stores the full JSON-LD body as plain text with
    ``{{ dotted.path | filter }}`` placeholders.  The body is validated on
    save: placeholders are replaced with a sentinel string and the result
    must parse as valid JSON.

    Per SPEC §8.1.1.
    """

    _name = 'era.seo.schema.template'
    _description = 'SEO Schema Template'
    _order = 'category, name'
    _rec_name = 'name'

    name = fields.Char(string='Name', required=True, translate=True)
    code = fields.Char(
        string='Code',
        required=True,
        help='Programmatic key, e.g. "organization", "faq_page". Must be unique.',
    )
    schema_type = fields.Char(
        string='Schema @type',
        required=True,
        help='The schema.org @type value, e.g. "Organization", "FAQPage".',
    )
    category = fields.Selection(
        [
            ('core', 'Core'),
            ('content', 'Content'),
            ('commerce', 'Commerce'),
            ('event', 'Event'),
            ('local', 'Local'),
        ],
        string='Category',
        default='core',
    )
    description = fields.Text(string='Description', translate=True)
    body = fields.Text(
        string='Template Body',
        required=True,
        help='JSON-LD with {{ placeholder }} substitution markers.',
    )
    required_fields_json = fields.Text(
        string='Required Fields (JSON)',
        help='JSON array of dotted context paths expected at render time. '
             'Used by the SEO audit to flag missing data.',
        default='[]',
    )
    documentation_url = fields.Char(string='Schema.org Docs URL')
    active = fields.Boolean(default=True)
    is_default = fields.Boolean(
        string='Default Template',
        default=False,
        help='Reserved for post-init logic. Setting this True on a template '
             'does not auto-attach it to pages; the post_init_hook decides '
             'which templates to attach.',
    )

    _code_unique = models.Constraint(
        'UNIQUE(code)',
        'Template code must be unique.',
    )

    # --- Constraints ----------------------------------------------------------

    @api.constrains('body')
    def _check_body_valid_json(self):
        """Placeholders stripped → result must be parseable JSON.

        Use ``null`` as the sentinel (no quotes) so it is valid JSON whether
        the placeholder is a standalone value or embedded inside a string.
        E.g. ``"{{ x }}/#org"`` → ``"null/#org"`` (valid), and
             ``"key": {{ x | json }}`` → ``"key": null`` (also valid).
        """
        for rec in self:
            if not rec.body:
                continue
            stripped = _PLACEHOLDER_RE.sub('null', rec.body)
            try:
                json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    _('Template body is not valid JSON (after placeholder '
                      'substitution): %s') % str(exc)
                ) from exc

    @api.constrains('body', 'schema_type')
    def _check_schema_type_matches_body(self):
        """The @type declared in schema_type must match @type inside body."""
        for rec in self:
            if not rec.body or not rec.schema_type:
                continue
            stripped = _PLACEHOLDER_RE.sub('null', rec.body)
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                # Body JSON invalidity is caught by _check_body_valid_json.
                continue
            body_type = parsed.get('@type', '')
            if body_type and body_type != rec.schema_type:
                raise ValidationError(
                    _('schema_type field ("%s") does not match the @type '
                      'value inside body ("%s"). Update one to match the '
                      'other.') % (rec.schema_type, body_type)
                )

    @api.constrains('required_fields_json')
    def _check_required_fields_json(self):
        for rec in self:
            if not rec.required_fields_json:
                continue
            try:
                parsed = json.loads(rec.required_fields_json)
                if not isinstance(parsed, list):
                    raise ValueError('Must be a JSON array.')
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValidationError(
                    _('required_fields_json must be a valid JSON array: %s') % str(exc)
                ) from exc

    # --- Helpers --------------------------------------------------------------

    def _get_required_fields(self):
        """Return the list of required context-path strings.

        Returns an empty list when required_fields_json is blank or invalid.
        """
        self.ensure_one()
        if not self.required_fields_json:
            return []
        try:
            return json.loads(self.required_fields_json)
        except (json.JSONDecodeError, ValueError):
            _logger.warning(
                'era.seo.schema.template[%d]: could not parse required_fields_json',
                self.id,
            )
            return []
