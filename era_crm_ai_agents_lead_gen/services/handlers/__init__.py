# -*- coding: utf-8 -*-
"""Source-handler package. Importing it registers every handler.

The engine imports this package and then dispatches by provider_type via
``get_handler``. Each submodule registers its handler at import time through the
``@register`` decorator in base.
"""
from .base import HANDLER_REGISTRY, get_handler, register, BaseHandler

# Import each handler module so its @register decorator runs.
from . import web_search
from . import local_registry
from . import web_scrape
from . import contact_data
from . import social
