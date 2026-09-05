from . import ai
from . import ai_field_catalog
from . import ai_attempt
# ai_agent_bridge first: it declares the hooks the charter overrides, and in Odoo the
# later registration wins the MRO.
from . import ai_agent_bridge
from . import legal_charter
from . import ai_help
from . import legal_corpus
from . import ai_source_citation
from . import ai_citation_audit
# after ai_agent_bridge: the composite renderers extend the request the bridge
# declared, and the allowed-field set must be the last one registered.
from . import ai_case_context
from . import ai_playbook
from . import ai_research_button
from . import ai_ask_from_record
from . import ai_chat_record_context
