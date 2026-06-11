# -*- coding: utf-8 -*-
from . import models
from . import services
from . import controllers


def post_init_hook(env):
    """Seed the editable norm vocabulary from the in-code defaults, only when
    the table is empty (so re-installs/upgrades never clobber manager edits)."""
    from .services.norms import CulturalNorms
    Term = env["crm.ai.norm.term"]
    if Term.search_count([]):
        return
    categories = {
        "greeting": CulturalNorms.GREETINGS,
        "informal_opener": CulturalNorms.INFORMAL_OPENERS,
        "honorific": CulturalNorms.HONORIFICS,
    }
    vals = [
        {"category": cat, "text": term}
        for cat, terms in categories.items()
        for term in terms
    ]
    Term.create(vals)
