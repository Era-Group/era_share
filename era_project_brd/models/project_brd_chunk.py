# -*- coding: utf-8 -*-
from odoo import fields, models


class ProjectBrdChunk(models.Model):
    _name = 'project.brd.chunk'
    _description = 'Project BRD Transcript Analysis Chunk'
    _order = 'project_id, meeting_id, sequence, id'

    project_id = fields.Many2one(
        'project.project', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company,
        help="Frozen company whose meeting evidence was used for this run.")
    meeting_id = fields.Many2one(
        'sembly.meeting', required=True, index=True, ondelete='cascade')
    sequence = fields.Integer(required=True)
    char_start = fields.Integer(required=True)
    char_end = fields.Integer(required=True)
    source_hash = fields.Char(required=True, index=True)
    state = fields.Selection([
        ('pending', 'Pending'),
        ('done', 'Done'),
        ('failed', 'Failed'),
    ], default='pending', required=True, index=True)
    extraction = fields.Text(copy=False)
    attempts = fields.Integer(copy=False)
    next_retry_at = fields.Datetime(copy=False, index=True)
    error = fields.Char(copy=False)

    _project_meeting_sequence_unique = models.Constraint(
        'UNIQUE(project_id, meeting_id, sequence)',
        'Each transcript chunk can only be analyzed once per project run.')
