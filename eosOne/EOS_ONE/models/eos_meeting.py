from odoo import models, fields, api, _
from odoo.exceptions import UserError


class EosMeeting(models.Model):
    _name = 'eos.meeting'
    _description = 'EOS Level 10 Meeting'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'meeting_date desc'

    name = fields.Char(
        required=True,
        tracking=True
    )

    team_id = fields.Many2one(
        'eos.team',
        required=True,
        ondelete='cascade'
    )

    meeting_date = fields.Datetime(
        default=fields.Datetime.now,
        required=True
    )

    meeting_type = fields.Selection([
        ('weekly', 'Weekly Level 10'),
        ('quarterly', 'Quarterly'),
        ('annual', 'Annual'),
    ], default='weekly', required=True)

    facilitator_id = fields.Many2one(
        'res.users',
        string='Facilitator'
    )

    attendee_ids = fields.Many2many(
        'res.users',
        string='Attendees'
    )

    state = fields.Selection([
        ('draft', 'Draft'),
        ('in_progress', 'In Progress'),
        ('done', 'Completed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True)

    rating = fields.Integer(
        string='Meeting Rating',
        tracking=True
    )

    recap = fields.Html(string='Meeting Recap')

    section_ids = fields.One2many(
        'eos.meeting.section',
        'meeting_id',
        string='Agenda'
    )

    started_at = fields.Datetime(readonly=True)
    ended_at = fields.Datetime(readonly=True)

    def action_start(self):
        for meeting in self:
            if meeting.state != 'draft':
                raise UserError(_('Only draft meetings can be started.'))

            meeting.write({
                'state': 'in_progress',
                'started_at': fields.Datetime.now(),
            })

    def action_conclude(self):
        for meeting in self:
            if not 1 <= meeting.rating <= 10:
                raise UserError(
                    _('Meeting rating must be between 1 and 10.')
                )

            meeting.write({
                'state': 'done',
                'ended_at': fields.Datetime.now(),
            })


class EosMeetingSection(models.Model):
    _name = 'eos.meeting.section'
    _description = 'Meeting Agenda Section'
    _order = 'sequence'

    name = fields.Char(required=True)

    meeting_id = fields.Many2one(
        'eos.meeting',
        required=True,
        ondelete='cascade'
    )

    sequence = fields.Integer(default=10)

    planned_minutes = fields.Integer(default=5)

    state = fields.Selection([
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('done', 'Done'),
    ], default='pending')