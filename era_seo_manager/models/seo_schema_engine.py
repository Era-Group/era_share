"""ERA SEO — JSON-LD placeholder resolution engine.

This module is intentionally **not** a model.  It is a pure-Python service
that can be tested independently of the ORM.

Placeholder grammar
-------------------
Only the following forms are accepted.  No expressions, loops, conditionals,
arithmetic, or string concatenation.

    {{ name }}                   — single identifier in context
    {{ foo.bar }}                — dotted attribute access, up to 5 levels
    {{ foo.bar | json }}         — JSON-serialize the resolved value
    {{ foo.bar | string }}       — coerce to str
    {{ foo.bar | default("X") }} — use X if resolved value is falsy

Filters may not be stacked.  The grammar is hand-rolled and restricted on
purpose — do not replace it with Jinja2 or any other templating library.
Security note: resolution uses getattr() only.  eval/exec are not used
anywhere in this file.

Public API
----------
    build_context(env, record=None) -> dict
    render_jsonld(template_body, context, record=None, instance_data=None) -> str
"""

import json
import logging
import re

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Match {{ ... }} blocks (non-greedy, single-line).
_PLACEHOLDER_RE = re.compile(r'\{\{([^}]+)\}\}')

# Validate a dotted identifier path: letters, digits, underscores, dots only.
# Max 5 segments (guards against runaway attribute traversal).
_PATH_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_.]*$')
_MAX_DEPTH = 5

# default("...") or default('...') — value may contain escaped quotes.
_DEFAULT_FILTER_RE = re.compile(r'''default\(["']([^"']*)["']\)''')


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def build_context(env, record=None):
    """Build the base rendering context dict.

    Context precedence (later keys override earlier):
    1. Global defaults from ir.config_parameter.
    2. Website and company from the current environment.
    3. record (the host page or model instance).

    :param env:    Odoo Environment (from request.env or a model's self.env).
    :param record: Optional host record (website.page, blog.post, etc.).
    :returns:      Dict ready to pass to render_jsonld().
    """
    ICP = env['ir.config_parameter'].sudo()

    # Resolve website — use the current website if available, else first.
    try:
        website = env['website'].get_current_website()
    except Exception:  # noqa: BLE001
        websites = env['website'].sudo().search([], limit=1)
        website = websites[:1] if websites else env['website'].sudo().browse()

    company = website.company_id if website and website.company_id else env.company

    ctx = {
        # Global ERA SEO settings (prefixed to avoid namespace collisions)
        'settings': _SettingsProxy(ICP),
        # Site objects
        'website': website,
        'company': company,
        # Record being rendered
        'record': record,
    }

    # Inject request-level values when inside an HTTP request.
    try:
        from odoo.http import request  # noqa: PLC0415
        if request:
            ctx['request'] = request
            ctx['lang'] = request.lang
    except (ImportError, RuntimeError):
        pass

    return ctx


# ---------------------------------------------------------------------------
# Settings proxy (lazy ir.config_parameter access)
# ---------------------------------------------------------------------------

class _SettingsProxy:
    """Lazy proxy that reads ERA SEO ir.config_parameter values on first access.

    Used inside the rendering context as ``settings.key`` so templates can
    reference e.g. ``{{ settings.organization_name }}``.
    """

    _PREFIX = 'era_seo.'

    def __init__(self, icp):
        object.__setattr__(self, '_icp', icp)
        object.__setattr__(self, '_cache', {})

    def __getattr__(self, name):
        cache = object.__getattribute__(self, '_cache')
        if name in cache:
            return cache[name]
        icp = object.__getattribute__(self, '_icp')
        value = icp.get_param(self._PREFIX + name, '')
        cache[name] = value
        return value

    def __bool__(self):
        return True


# ---------------------------------------------------------------------------
# Placeholder resolution
# ---------------------------------------------------------------------------

def _resolve_path(path: str, context: dict):
    """Walk context dict and ORM records to resolve a dotted path.

    :param path:    Dotted string, e.g. "company.name" or "record.seo_title".
    :param context: The rendering context dict built by build_context().
    :returns:       The resolved value, or None if not resolvable.
    """
    parts = path.strip().split('.')
    if len(parts) > _MAX_DEPTH:
        _logger.debug('era_seo_engine: path too deep (>%d): %s', _MAX_DEPTH, path)
        return None

    # Start from the context root.
    obj = context.get(parts[0])
    if obj is None and parts[0] not in context:
        return None

    for part in parts[1:]:
        if obj is None:
            return None
        # Recordsets: use the first record's attribute.
        try:
            # dict-like (e.g. our _SettingsProxy or a plain dict)
            if isinstance(obj, dict):
                obj = obj.get(part)
            else:
                obj = getattr(obj, part, None)
        except Exception:  # noqa: BLE001
            return None

    return obj


def _coerce_to_json_value(value, filter_name: str, default_val: str = ''):
    """Apply the filter and return a JSON-encodable Python value.

    :param value:       Resolved value (any Python type, including ORM record).
    :param filter_name: One of "", "json", "string", "default".
    :param default_val: The default string for the default() filter.
    """
    # Recordsets: use display_name.
    if hasattr(value, '_name') and hasattr(value, 'ids'):
        # It's an Odoo recordset.
        if not value:
            value = None
        else:
            value = value[:1].display_name or getattr(value[:1], 'name', None)

    if filter_name == 'default':
        if not value:
            value = default_val

    if filter_name == 'string':
        value = str(value) if value is not None else ''

    if filter_name == 'json':
        # Return the raw JSON representation; the outer json.dumps call will
        # insert it as a pre-encoded string — but we want it as a raw value.
        # We handle this by returning a _RawJSON wrapper that the serializer
        # recognises.
        return _RawJSON(json.dumps(value, ensure_ascii=False, default=str))

    return value


class _RawJSON:
    """Wrapper that signals the serializer to insert the value verbatim."""
    def __init__(self, raw: str):
        self.raw = raw


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render_jsonld(
    template_body: str,
    context: dict,
    record=None,
    instance_data: dict | None = None,
) -> str:
    """Render a JSON-LD template body by resolving all {{ }} placeholders.

    :param template_body:  The raw template text with placeholders.
    :param context:        The base rendering context (from build_context()).
    :param record:         Optional host record; merged into context as
                           ``context['record']``.
    :param instance_data:  Per-instance override dict; merged last.
    :returns:              A valid JSON string.  Unresolvable placeholders
                           become JSON ``null``.
    :raises ValueError:    If template_body contains structurally invalid
                           placeholder syntax (e.g. arithmetic operators).
    """
    if not template_body:
        return 'null'

    # Merge record and instance overrides into a working copy of context.
    ctx = dict(context)
    if record is not None:
        ctx['record'] = record
    if instance_data:
        ctx.update(instance_data)

    def _replace(match):
        raw = match.group(1).strip()
        return _resolve_placeholder(raw, ctx)

    rendered = _PLACEHOLDER_RE.sub(_replace, template_body)

    # Validate the output is parseable JSON.
    try:
        json.loads(rendered)
    except json.JSONDecodeError as exc:
        _logger.warning(
            'era_seo_engine: rendered output is not valid JSON: %s\n%s',
            exc, rendered[:200],
        )
        # Return the best-effort string rather than crashing; callers handle.
        return rendered

    return rendered


def _resolve_placeholder(raw: str, ctx: dict) -> str:
    """Resolve one placeholder expression and return a JSON fragment string.

    Accepted forms (see module docstring):
      path
      path | json
      path | string
      path | default("X")

    Returns JSON ``null`` on any resolution failure.
    """
    # Reject obvious expression injection attempts.
    for forbidden in ('+', '-', '*', '/', ' or ', ' and ', ' not ', '(', ')') :
        # Allow parentheses inside default(...) — handled specially.
        if forbidden in ('(', ')'):
            continue
        if forbidden in raw:
            raise ValueError(
                'Invalid placeholder syntax (expressions not allowed): '
                '{{ %s }}' % raw
            )

    filter_name = ''
    default_val = ''
    path = raw

    if '|' in raw:
        parts = raw.split('|', 1)
        path = parts[0].strip()
        filter_expr = parts[1].strip()

        if filter_expr == 'json':
            filter_name = 'json'
        elif filter_expr == 'string':
            filter_name = 'string'
        else:
            m = _DEFAULT_FILTER_RE.fullmatch(filter_expr)
            if m:
                filter_name = 'default'
                default_val = m.group(1)
            else:
                raise ValueError(
                    'Unknown filter in placeholder: {{ %s }}' % raw
                )

    # Validate path characters.
    if not _PATH_RE.match(path):
        raise ValueError(
            'Invalid placeholder path (only dotted identifiers allowed): '
            '{{ %s }}' % raw
        )

    value = _resolve_path(path, ctx)

    # Apply filter.
    coerced = _coerce_to_json_value(value, filter_name, default_val)

    # Serialise to a JSON fragment.
    if isinstance(coerced, _RawJSON):
        return coerced.raw
    if coerced is None:
        return 'null'
    return json.dumps(coerced, ensure_ascii=False, default=str)
