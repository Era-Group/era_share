"""19.0.7.1.0 — Change era_seo_schema_instance.template_id FK to CASCADE.

The previous RESTRICT prevented deleting schema templates that still had
instances attached, causing upgrade failures when demo data was refreshed.
Instances without a template are meaningless, so CASCADE is the correct policy.
"""


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE era_seo_schema_instance
            DROP CONSTRAINT IF EXISTS era_seo_schema_instance_template_id_fkey;
        ALTER TABLE era_seo_schema_instance
            ADD CONSTRAINT era_seo_schema_instance_template_id_fkey
            FOREIGN KEY (template_id)
            REFERENCES era_seo_schema_template (id)
            ON DELETE CASCADE;
    """)
