import re
from datetime import datetime, timezone

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

from ..providers import ProviderNotReady, get_provider
from ..providers.runtime import configuration, server_configuration, TokenVault, active_token
from ..providers.base import provider_capabilities
from ..providers.pop3 import pop3_configuration
from ..providers.smtp import validate_mail_settings, pop_config


class MailClientAccount(models.Model):
    _name = 'mail_client.account'
    _description = 'Mail Client Account Setup'
    _order = 'name, id'
    _check_company_auto = True

    name = fields.Char(required=True, size=120)
    provider = fields.Selection([('gmail', 'Gmail'), ('outlook', 'Outlook'), ('pop3', 'POP3 + SMTP')], required=True)
    email_address = fields.Char(required=False, size=254)
    # POP3/SMTP fields (only used when provider == 'pop3')
    pop3_username = fields.Char(string='POP3 Username', size=254)
    pop3_password = fields.Char(string='POP3 Password', size=1024)
    pop3_host = fields.Char(string='POP3 Host', size=254, default='pop.gmail.com')
    pop3_port = fields.Integer(string='POP3 Port', default=995)
    smtp_host = fields.Char(string='SMTP Host', size=254, default='smtp.gmail.com')
    smtp_port = fields.Integer(string='SMTP Port', default=465)
    smtp_security = fields.Selection([('ssl', 'Implicit TLS (usually 465)'), ('starttls', 'STARTTLS (usually 587)')], default='ssl')
    owner_id = fields.Many2one('res.users', required=True, default=lambda self: self.env.user,
                              index=True, ondelete='cascade', readonly=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company,
                                index=True, readonly=True)
    active = fields.Boolean(default=True)
    connection_state = fields.Selection(
        [('not_connected', 'Not connected'), ('connected', 'Connected'),
         ('reauthorize', 'Reconnect required')], default='not_connected', required=True, readonly=True)
    provider_account_id = fields.Char(readonly=True, copy=False)
    # Reference only, never an OAuth token. Hidden even from the mail-client manager.
    credential_ref = fields.Char(readonly=True, copy=False, groups='base.group_system')
    token_expires_at = fields.Datetime(readonly=True, copy=False, groups='base.group_system')

    _account_identity_unique = models.Constraint(
        'UNIQUE(owner_id, company_id, provider, email_address)',
        'An account setup for this email and provider already exists for this user and company.',
    )

    _protected = frozenset({'connection_state', 'provider_account_id', 'credential_ref', 'token_expires_at'})
    _identity = frozenset({'owner_id', 'company_id', 'provider', 'email_address'})

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for original in vals_list:
            vals = dict(original)
            if self._protected.intersection(vals):
                raise AccessError(_('Connection credentials and status are managed by the server.'))
            if not self.env.su and vals.get('owner_id', self.env.uid) != self.env.uid:
                raise AccessError(_('Create an account setup only for yourself.'))
            vals.setdefault('owner_id', self.env.uid)
            company_id = vals.setdefault('company_id', self.env.company.id)
            if not self.env.su and company_id not in self.env.companies.ids:
                raise AccessError(_('Select an allowed company.'))
            provider = vals.get('provider', 'gmail')
            # For OAuth providers (gmail/outlook), email comes from OAuth callback, not user input
            if provider in ('gmail', 'outlook'):
                vals['email_address'] = ''
            else:
                vals['email_address'] = str(vals.get('email_address', '')).strip().lower()
            vals['connection_state'] = 'not_connected'
            prepared.append(vals)
        # RPC context is user-controlled too: never accept default credentials/status.
        safe_context = {k: v for k, v in self.env.context.items()
                        if k not in {'default_' + field for field in self._protected}}
        return super(MailClientAccount, self.with_context(safe_context)).create(prepared)

    def write(self, vals):
        if self._protected.intersection(vals):
            raise AccessError(_('Connection credentials and status are managed by the server.'))
        # Allow setting email_address for OAuth providers (gmail/outlook) when it was empty
        # This is needed for first-time OAuth connection where email comes from callback
        if 'email_address' in vals and self.provider in ('gmail', 'outlook') and not self.email_address:
            vals['email_address'] = str(vals['email_address']).strip().lower()
        elif self._identity.intersection(vals):
            raise UserError(_('Account identity cannot be changed. Create another account setup instead.'))
        return super().write(vals)

    def copy(self, default=None):
        raise UserError(_('Create a new account setup instead of duplicating an existing identity.'))

    @api.constrains('name', 'email_address')
    def _check_details(self):
        for account in self:
            if not account.name.strip():
                raise ValidationError(_('Enter an account name.'))
            if account.provider == 'pop3':
                if not account.email_address or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', account.email_address):
                    raise ValidationError(_('Enter one valid email address for POP3 + SMTP accounts.'))

    @api.model
    def list_client_accounts(self):
        # No sudo: record rules always apply. Managers see only their OWN accounts in the client.
        accounts = self.search([('owner_id', '=', self.env.uid)])
        result = []
        for a in accounts:
            if a.provider == 'pop3':
                # For POP3, SMTP is enabled if credentials exist in vault OR model fields are populated
                smtp_enabled = a._pop3_has_smtp_config()
            else:
                smtp_enabled = bool(a.sudo().credential_ref)
            result.append({
                'id': a.id, 'name': a.name, 'provider': a.provider,
                'email': a.email_address, 'state': a.connection_state,
                'capabilities': provider_capabilities(a.provider, smtp_enabled=smtp_enabled),
            })
        return result

    def _pop3_has_smtp_config(self):
        """Check if POP3 account has SMTP configuration available (vault or model fields)."""
        self.ensure_one()
        if self.provider != 'pop3':
            return False
        # Check vault first
        if self.sudo().credential_ref:
            return True
        # Fallback: check model fields (pop3_password is required for auth)
        return bool(self.pop3_host and self.pop3_password and self.smtp_host and self.smtp_port)

    def _require_owner(self):
        self.ensure_one()
        self.check_access('read')
        if not self.exists() or self.owner_id.id != self.env.uid:
            raise AccessError(_('You may operate only your own mail accounts.'))
        if not self.active:
            raise UserError(_('This account setup is archived.'))

    def action_connect(self):
        self._require_owner()
        if self.provider == 'pop3':
            try:
                provider = get_provider('pop3', email=self.email_address, pop3_config=self._pop3_config())
                provider.check_connection()
                if provider.smtp:
                    provider.smtp.check_connection()
                self._set_connection({'connection_state': 'connected', 'provider_account_id': self.email_address})
                return {'type': 'ir.actions.client', 'tag': 'display_notification',
                        'params': {'title': _('POP3 connected'), 'message': _('Inbox receiving is ready. Refresh your accounts in Mail Client.'), 'type': 'success', 'sticky': False}}
            except ProviderNotReady as error:
                raise UserError(str(error)) from None
        # Validate configuration before navigating away; never return credentials to the client.
        try:
            configuration(self.provider)
        except ProviderNotReady as error:
            raise UserError(str(error)) from None
        return {'type': 'ir.actions.act_url', 'url': '/mail/oauth/start?account_id=' + str(self.id), 'target': 'self'}

    def action_disconnect(self):
        self._require_owner()
        if self.provider == 'pop3':
            reference = self.sudo().credential_ref
            if reference:
                try:
                    vault = TokenVault(self.env.cr.dbname, server_configuration())
                    self.env.cr.postcommit.add(lambda: vault.remove(reference))
                except ProviderNotReady as error:
                    raise UserError(str(error)) from None
            self._set_connection({'connection_state': 'not_connected', 'credential_ref': False,
                                  'provider_account_id': False, 'token_expires_at': False})
            return True
        try:
            vault = TokenVault(self.env.cr.dbname, configuration(self.provider))
            vault.remove(self.sudo().credential_ref)
        except ProviderNotReady as error:
            raise UserError(str(error)) from None
        self._set_connection({'connection_state': 'not_connected', 'credential_ref': False,
                              'provider_account_id': False, 'token_expires_at': False})
        return True

    def _set_connection(self, values):
        # Private: inaccessible by ORM RPC. Owner is rechecked before a narrow elevation.
        self._require_owner()
        # Allow email_address for OAuth providers (gmail/outlook) when empty
        if 'email_address' in values and self.provider in ('gmail', 'outlook') and not self.email_address:
            values['email_address'] = str(values['email_address']).strip().lower()
        elif set(values) - self._protected:
            raise AccessError(_('Invalid connection update.'))
        return super(MailClientAccount, self.sudo()).write(values)

    def _complete_oauth(self, tokens, identity):
        self._require_owner()
        authorized_email = (identity.get('email') or '').strip().lower()
        # For OAuth providers (gmail/outlook), email comes from the authorized identity.
        # Allow updating email if user selected a different account during authorization.
        if self.provider in ('gmail', 'outlook'):
            if not authorized_email:
                raise UserError(_('Could not determine email address from provider. Please try again.'))
            if self.email_address != authorized_email:
                # Set/update email to match authorized identity
                self._set_connection({'email_address': authorized_email})
        vault = TokenVault(self.env.cr.dbname, configuration(self.provider))
        previous = self.sudo().credential_ref or None
        reference = vault.save(tokens, previous)
        try:
            self._set_connection({'connection_state': 'connected', 'provider_account_id': identity['id'],
                                  'credential_ref': reference,
                                  'token_expires_at': datetime.fromtimestamp(tokens['expires_at'], timezone.utc).replace(tzinfo=None)})
        except Exception:
            if not previous:
                vault.remove(reference)
            raise
        # Reuse the verified account reference on reconnect; no historical token copies.
        # New metadata never contains token values.
        return True

    def _pop3_config(self):
        self._require_owner()
        reference = self.sudo().credential_ref
        if reference:
            config = server_configuration()
            data = TokenVault(self.env.cr.dbname, config).read(reference)
            if (data.get('email'), data.get('owner_id'), data.get('company_id')) != (self.email_address, self.owner_id.id, self.company_id.id):
                raise ProviderNotReady('Mail-server credentials do not match this account owner/company.')
            return pop_config(validate_mail_settings(data.get('settings'), config))
        # Fallback to model fields (for UI display before first connect)
        if not self.pop3_host or not self.pop3_password:
            raise ProviderNotReady('POP3/SMTP server settings not configured.')
        return pop_config({
            'pop_host': self.pop3_host,
            'pop_port': self.pop3_port,
            'username': self.pop3_username or self.email_address,
            'password': self.pop3_password,
            'smtp_host': self.smtp_host,
            'smtp_port': self.smtp_port,
            'smtp_security': self.smtp_security
        })

    @api.model
    def create_pop3_account(self, name, email, settings):
        account = self.create({'name': name, 'email_address': email, 'provider': 'pop3',
                                'pop3_username': settings.get('username'),
                                'pop3_password': settings.get('password'),
                                'pop3_host': settings.get('pop_host'),
                                'pop3_port': settings.get('pop_port'),
                                'smtp_host': settings.get('smtp_host'),
                                'smtp_port': settings.get('smtp_port'),
                                'smtp_security': settings.get('smtp_security')})
        account.configure_pop3_account(settings)
        return account.id

    def configure_pop3_account(self, settings):
        self._require_owner()
        if self.provider != 'pop3':
            raise UserError(_('Server credentials apply only to POP3 + SMTP accounts.'))
        try:
            config = server_configuration()
            validated = validate_mail_settings(settings, config)
            provider = get_provider('pop3', email=self.email_address, pop3_config=pop_config(validated))
            # Authenticate both services before saving; no test message is sent.
            provider.check_connection()
            provider.smtp.check_connection()
            vault = TokenVault(self.env.cr.dbname, config)
            previous = self.sudo().credential_ref
            reference = vault.save({'settings': validated, 'email': self.email_address,
                                    'owner_id': self.owner_id.id, 'company_id': self.company_id.id})
            try:
                self._set_connection({'credential_ref': reference, 'connection_state': 'connected',
                                      'provider_account_id': self.email_address, 'token_expires_at': False})
            except Exception:
                vault.remove(reference)
                raise
            # File writes are outside PostgreSQL: clean a new file on rollback,
            # and keep the old reference until this transaction commits.
            self.env.cr.postrollback.add(lambda: vault.remove(reference))
            if previous:
                self.env.cr.postcommit.add(lambda: vault.remove(previous))
            # Also update the model fields for UI display
            self.write({
                'pop3_username': settings.get('username'),
                'pop3_password': settings.get('password'),
                'pop3_host': settings.get('pop_host'),
                'pop3_port': settings.get('pop_port'),
                'smtp_host': settings.get('smtp_host'),
                'smtp_port': settings.get('smtp_port'),
                'smtp_security': settings.get('smtp_security')
            })
            return True
        except ProviderNotReady as error:
            raise UserError(str(error)) from None

    def _provider(self):
        self._require_owner()
        if self.connection_state != 'connected':
            raise ProviderNotReady('This account is not connected. Select Connect first.')
        if self.provider == 'pop3':
            return get_provider('pop3', email=self.email_address, pop3_config=self._pop3_config())
        config = configuration(self.provider)
        vault = TokenVault(self.env.cr.dbname, config)
        token = active_token(self.provider, config, vault, self.sudo().credential_ref)
        return get_provider(self.provider, token, self.email_address)

    def _invoke_provider(self, operation, **kwargs):
        try:
            self._require_owner()
            if self.provider == 'pop3' and operation not in ('list_messages', 'get_message', 'send_message'):
                raise ProviderNotReady('POP3 does not support provider drafts, Sent folders, attachments or read-state synchronization.')
            provider = self._provider()
            return getattr(provider, operation)(**kwargs)
        except ProviderNotReady as error:
            raise UserError(str(error)) from None

    @api.model
    def _page(self, cursor, limit):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            raise ValidationError(_('Page size must be between 1 and 50.'))
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 8192):
            raise ValidationError(_('Invalid pagination cursor.'))

    @api.model
    def _identifier(self, value):
        if not isinstance(value, str) or not value or len(value) > 2048:
            raise ValidationError(_('Invalid provider identifier.'))

    @api.model
    def _draft(self, draft):
        if not isinstance(draft, dict) or set(draft) - {'to', 'subject', 'body'}:
            raise ValidationError(_('Invalid draft fields.'))
        for key, maximum in [('to', 4096), ('subject', 998), ('body', 200000)]:
            if not isinstance(draft.get(key, ''), str) or len(draft.get(key, '')) > maximum:
                raise ValidationError(_('Draft field is invalid or too long.'))
        return {key: draft.get(key, '') for key in ('to', 'subject', 'body')}

    def list_messages(self, folder='inbox', cursor=None, limit=25, query='', unread=False):
        self._require_owner()
        self._page(cursor, limit)
        if folder not in ('inbox', 'sent') or not isinstance(query, str) or len(query) > 512 or not isinstance(unread, bool):
            raise ValidationError(_('Invalid folder or search.'))
        return self._invoke_provider('list_messages', folder=folder, cursor=cursor, limit=limit, query=query, unread=unread)

    def get_message(self, message_id):
        self._require_owner()
        self._identifier(message_id)
        return self._invoke_provider('get_message', message_id=message_id)

    def send_message(self, draft, reply_to_id=None):
        self._require_owner()
        if self.provider != 'pop3':
            raise UserError(_('Use provider drafts to send Gmail and Outlook messages.'))
        if reply_to_id is not None:
            self._identifier(reply_to_id)
        return self._invoke_provider('send_message', draft=self._draft(draft), reply_to_id=reply_to_id)

    def list_drafts(self, cursor=None, limit=25):
        self._require_owner()
        self._page(cursor, limit)
        return self._invoke_provider('list_drafts', cursor=cursor, limit=limit)

    def get_draft(self, draft_id):
        self._require_owner()
        self._identifier(draft_id)
        return self._invoke_provider('get_draft', draft_id=draft_id)

    def save_draft(self, draft, draft_id=None, reply_to_id=None):
        self._require_owner()
        if draft_id is not None:
            self._identifier(draft_id)
        if reply_to_id is not None:
            self._identifier(reply_to_id)
        return self._invoke_provider('save_draft', draft=self._draft(draft), draft_id=draft_id, reply_to_id=reply_to_id)

    def send_draft(self, draft_id):
        self._require_owner()
        self._identifier(draft_id)
        return self._invoke_provider('send_draft', draft_id=draft_id)

    def reply(self, message_id, draft):
        self._require_owner()
        self._identifier(message_id)
        return self._invoke_provider('reply', message_id=message_id, draft=self._draft(draft))

    def set_read(self, message_id, is_read):
        self._require_owner()
        self._identifier(message_id)
        if not isinstance(is_read, bool):
            raise ValidationError(_('Read state must be a boolean.'))
        return self._invoke_provider('set_read', message_id=message_id, is_read=is_read)

    def get_attachment(self, message_id, attachment_id):
        self._require_owner()
        self._identifier(message_id)
        self._identifier(attachment_id)
        return self._invoke_provider('get_attachment', message_id=message_id, attachment_id=attachment_id)