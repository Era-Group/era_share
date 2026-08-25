from . import ai
from . import ai_field_catalog
from . import ai_attempt
from . import legal_legislation
# ai_agent_bridge first: it declares the hooks the charter overrides, and in Odoo the
# later registration wins the MRO.
from . import ai_agent_bridge
from . import legal_charter
from . import ai_help
