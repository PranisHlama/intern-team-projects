from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class EosMeasurable(models.Model):
    _name = 'eos.measurable'
    _description = 'EOS Scorecard Measurable'
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

    goal = fields.Float(required=True)

    comparison = fields.Selection([
        ('gte', 'At Least'),
        ('lte', 'At Most'),
    ], default='gte', required=True)

    unit = fields.Selection([
        ('number', 'Number'),
        ('currency', 'Currency'),
        ('percent', 'Percentage'),
    ], default='number')

    entry_ids = fields.One2many(
        'eos.measurable.entry',
        'measurable_id'
    )

    active = fields.Boolean(default=True)


class EosMeasurableEntry(models.Model):
    _name = 'eos.measurable.entry'
    _description = 'Weekly Scorecard Entry'
    _order = 'week_date desc'

    measurable_id = fields.Many2one(
        'eos.measurable',
        required=True,
        ondelete='cascade'
    )

    week_date = fields.Date(
        required=True,
        default=fields.Date.today
    )

    value = fields.Float(required=True)

    goal = fields.Float(
        related='measurable_id.goal',
        store=True
    )

    status = fields.Selection([
        ('on', 'On Track'),
        ('off', 'Off Track'),
    ], compute='_compute_status', store=True)

    @api.depends(
        'value',
        'goal',
        'measurable_id.comparison'
    )
    def _compute_status(self):
        for record in self:
            if record.measurable_id.comparison == 'lte':
                record.status = (
                    'on' if record.value <= record.goal else 'off'
                )
            else:
                record.status = (
                    'on' if record.value >= record.goal else 'off'
                )

    _unique_weekly_entry = models.Constraint(
        'UNIQUE(measurable_id, week_date)',
        'Only one entry is allowed per measurable per week.',
    )

    def action_create_issue(self):
        self.ensure_one()

        issue = self.env['eos.issue'].create({
            'name': 'Scorecard: %s' % self.measurable_id.name,
            'team_id': self.measurable_id.team_id.id,
            'description': (
                'Actual value: %s, Goal: %s'
                % (self.value, self.goal)
            ),
            'source_model': self._name,
            'source_res_id': self.id,
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'eos.issue',
            'view_mode': 'form',
            'res_id': issue.id,
        }
