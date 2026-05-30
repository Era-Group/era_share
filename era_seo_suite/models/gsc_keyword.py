"""ERA GSC — keyword opportunities.

A read-only PostgreSQL view over era.gsc.query: the last 28 days of per-day
query rows aggregated into one row per (site, keyword), tagged with the same
opportunity buckets shown on the Analytics tab (top / striking distance /
low-CTR / rising / new / question). Backing it with a real model turns the
Analytics worklist into a standard Odoo list — sortable, filterable,
groupable and exportable — instead of a static HTML table.

Windows are relative to CURRENT_DATE inside the view, so the figures stay
current without any refresh job.
"""
from odoo import fields, models, tools


class EraGscKeyword(models.Model):
    _name = 'era.gsc.keyword'
    _description = 'GSC Keyword Opportunity (28d)'
    _auto = False
    _order = 'impressions desc'

    site_id = fields.Many2one('era.gsc.site', string='Site', readonly=True)
    query = fields.Char(string='Keyword', readonly=True)
    clicks = fields.Integer(string='Clicks', readonly=True)
    impressions = fields.Integer(string='Impressions', readonly=True)
    ctr = fields.Float(string='CTR', digits=(8, 4), readonly=True,
                       aggregator='avg')
    position = fields.Float(string='Avg Position', digits=(8, 2),
                            readonly=True, aggregator='avg')
    impr_recent = fields.Integer(string='Impr. (14d)', readonly=True)
    impr_prior = fields.Integer(string='Impr. prior 14d', readonly=True)
    is_top = fields.Boolean(string='Top', readonly=True)
    is_striking = fields.Boolean(string='Striking distance', readonly=True)
    is_low_ctr = fields.Boolean(string='Low CTR', readonly=True)
    is_rising = fields.Boolean(string='Rising', readonly=True)
    is_new = fields.Boolean(string='New', readonly=True)
    is_question = fields.Boolean(string='Question', readonly=True)

    def init(self):
        # self._table is a controlled identifier ('era_gsc_keyword'); the rest
        # of the statement is static, so an f-string is safe and avoids the
        # %%-escaping the params API would force on the LIKE '%?%' below.
        tools.drop_view_if_exists(self.env.cr, self._table)
        # Safety net for a fresh install: Odoo interleaves _auto_init()/init()
        # per model, so if this view is ever initialised before era.gsc.query's
        # table exists the CREATE VIEW would abort the whole install. We import
        # gsc_keyword after gsc_query so that's the normal path, but guard anyway
        # — fall back to a typed empty view that the next upgrade rebuilds.
        self.env.cr.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'era_gsc_query'")
        if not self.env.cr.fetchone():
            self.env.cr.execute(f"""
                CREATE VIEW {self._table} AS SELECT
                    0 AS id, NULL::integer AS site_id, NULL::varchar AS query,
                    0 AS clicks, 0 AS impressions,
                    0.0::double precision AS ctr,
                    0.0::double precision AS position,
                    0 AS impr_recent, 0 AS impr_prior,
                    false AS is_top, false AS is_striking, false AS is_low_ctr,
                    false AS is_rising, false AS is_new, false AS is_question
                WHERE false
            """)
            return
        self.env.cr.execute(f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT
                    row_number() OVER ()                        AS id,
                    t.site_id, t.query, t.clicks, t.impressions,
                    t.ctr, t.position, t.impr_recent, t.impr_prior,
                    (t.clicks_rank <= 15)                       AS is_top,
                    (t.position >= 4.5 AND t.position <= 20)    AS is_striking,
                    (t.impressions >= 30 AND t.ctr < 0.02)      AS is_low_ctr,
                    (t.impr_recent >= 5 AND t.impr_prior > 0
                       AND t.impr_recent >= 1.5 * t.impr_prior) AS is_rising,
                    (t.impr_recent >= 5 AND t.impr_prior = 0)   AS is_new,
                    (t.query ~* '^(how|what|why|who|where|when|which|can|does|do|is|are|should|best|top)( |$)'
                       OR t.query ~ '^(كيف|ما|ماذا|لماذا|من|اين|أين|متى|هل|كم|افضل|أفضل)'
                       OR t.query LIKE '%?%')                   AS is_question
                FROM (
                    SELECT
                        q.site_id                               AS site_id,
                        q.query                                 AS query,
                        SUM(q.clicks)                           AS clicks,
                        SUM(q.impressions)                      AS impressions,
                        CASE WHEN SUM(q.impressions) > 0
                             THEN SUM(q.clicks)::float / SUM(q.impressions)
                             ELSE 0 END                         AS ctr,
                        AVG(q.position)                         AS position,
                        COALESCE(SUM(q.impressions) FILTER (
                            WHERE q.date >= CURRENT_DATE - 14), 0)  AS impr_recent,
                        COALESCE(SUM(q.impressions) FILTER (
                            WHERE q.date >= CURRENT_DATE - 28
                              AND q.date <  CURRENT_DATE - 14), 0)  AS impr_prior,
                        ROW_NUMBER() OVER (PARTITION BY q.site_id
                                     ORDER BY SUM(q.clicks) DESC)   AS clicks_rank
                    FROM era_gsc_query q
                    WHERE q.date >= CURRENT_DATE - 28
                    GROUP BY q.site_id, q.query
                ) t
            )
        """)
