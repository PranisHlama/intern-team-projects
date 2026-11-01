from odoo import models, fields


class EosHeadline(models.Model):
    _name = 'eos.headline'
    _description = 'EOS Headlines'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(required=True)

    meeting_id = fields.Many2one(
        'eos.meeting',
        required=True,
        ondelete='cascade'
    )

    team_id = fields.Many2one(
        related='meeting_id.team_id',
        store=True
    )

    headline_type = fields.Selection([
        ('customer', 'Customer'),
        ('employee', 'Employee'),
        ('other', 'Other'),
    ], default='customer')

    description = fields.Text()

    needs_discussion = fields.Boolean(
        string='Needs Discussion'
    )

    issue_id = fields.Many2one(
        'eos.issue',
        readonly=True
    )

    def action_create_issue(self):
        for headline in self:
            if headline.issue_id:
                continue

            issue = self.env['eos.issue'].create({
                'name': headline.name,
                'team_id': headline.team_id.id,
                'meeting_id': headline.meeting_id.id,
                'description': headline.description,
                'source_model': headline._name,
                'source_res_id': headline.id,
            })

            headline.issue_id = issue.id