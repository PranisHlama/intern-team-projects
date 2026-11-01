from odoo import models, fields


class EosTodo(models.Model):
    _name = 'eos.todo'
    _description = 'EOS To-Do'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'done, date_deadline'

    name = fields.Char(required=True, tracking=True)

    team_id = fields.Many2one(
        'eos.team',
        required=True,
        ondelete='cascade'
    )

    owner_id = fields.Many2one(
        'res.users',
        required=True,
        default=lambda self: self.env.user
    )

    meeting_id = fields.Many2one('eos.meeting')

    issue_id = fields.Many2one('eos.issue')

    date_deadline = fields.Date()

    done = fields.Boolean(
        string='Completed',
        default=False,
        tracking=True
    )

    completed_date = fields.Date(readonly=True)

    description = fields.Text()

    def write(self, vals):
        vals = dict(vals)

        if 'done' in vals:
            vals['completed_date'] = (
                fields.Date.today() if vals['done'] else False
            )

        return super().write(vals)