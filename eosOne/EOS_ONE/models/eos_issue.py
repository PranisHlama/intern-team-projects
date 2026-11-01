from odoo import models, fields, _
from odoo.exceptions import UserError


class EosIssue(models.Model):
    _name = 'eos.issue'
    _description = 'EOS Issues IDS'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'priority_rank, id desc'

    name = fields.Char(required=True, tracking=True)

    team_id = fields.Many2one(
        'eos.team',
        required=True,
        ondelete='cascade'
    )

    meeting_id = fields.Many2one('eos.meeting')

    priority_rank = fields.Integer(default=10)

    status = fields.Selection([
        ('open', 'Open'),
        ('discussion', 'In Discussion'),
        ('solved', 'Solved'),
    ], default='open', tracking=True)

    description = fields.Html()

    source_model = fields.Char(readonly=True)
    source_res_id = fields.Integer(readonly=True)

    todo_ids = fields.One2many(
        'eos.todo',
        'issue_id'
    )

    def action_start_discussion(self):
        self.write({'status': 'discussion'})

    def action_create_todo(self):
        self.ensure_one()

        todo = self.env['eos.todo'].create({
            'name': self.name,
            'team_id': self.team_id.id,
            'issue_id': self.id,
            'owner_id': self.env.user.id,
            'meeting_id': self.meeting_id.id,
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'eos.todo',
            'view_mode': 'form',
            'res_id': todo.id,
        }

    def action_solve(self):
        for issue in self:
            if not issue.todo_ids:
                raise UserError(
                    _('Create a To-Do before solving this issue.')
                )

            issue.status = 'solved'