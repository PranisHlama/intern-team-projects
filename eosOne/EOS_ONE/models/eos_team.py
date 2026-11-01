from odoo import models, fields, api


class EosTeam(models.Model):
    _name = 'eos.team'
    _description = 'EOS Team'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(
        string='Team Name',
        required=True,
        tracking=True
    )

    member_ids = fields.Many2many(
        'res.users',
        string='Team Members'
    )

    facilitator_id = fields.Many2one(
        'res.users',
        string='Team Facilitator'
    )

    description = fields.Text()

    active = fields.Boolean(default=True)

    meeting_ids = fields.One2many(
        'eos.meeting',
        'team_id',
        string='Meetings'
    )

    company_id = fields.Many2one(
        'res.company',
        default=lambda self: self.env.company
    )