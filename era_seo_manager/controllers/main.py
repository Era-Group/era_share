# Phase 1: no controller overrides required.
# The era_seo dict is computed directly in the QWeb template
# (website_layout_templates.xml) from seo_object._fields, so no Python
# controller hook is needed for basic meta rendering.
#
# Phase 3 will add redirect middleware here.
# Phase 4 will add sitemap / robots.txt controllers here.
