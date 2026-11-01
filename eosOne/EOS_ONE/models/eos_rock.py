from odoo import models, fields, _


class EosRock(models.Model):
    _name = 'eos.rock'
    _description = 'EOS Quarterly Rock'
    _inherit = ['mail.thread', 'mail.activity.mixin']

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

    quarter = fields.Selection([
        ('q1', 'Q1'),
        ('q2', 'Q2'),
        ('q3', 'Q3'),
        ('q4', 'Q4'),
    ], required=True)

    year = fields.Integer(
        required=True,
        default=lambda self: fields.Date.today().year
    )

    level = fields.Selection([
        ('company', 'Company'),
        ('personal', 'Personal'),
    ], default='company', required=True)

    status = fields.Selection([
        ('on_track', 'On Track'),
        ('off_track', 'Off Track'),
        ('done', 'Done'),
        ('not_done', 'Not Done'),
    ], default='on_track', tracking=True)

    due_date = fields.Date()

    description = fields.Text()

    def action_create_issue(self):
        for rock in self:
            self.env['eos.issue'].create({
                'name': 'Rock Off Track: %s' % rock.name,
                'team_id': rock.team_id.id,
                'description': rock.description,
                'source_model': rock._name,
                'source_res_id': rock.id,
            })