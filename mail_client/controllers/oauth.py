import logging
import secrets
import time

from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import request
from ..providers import get_provider
from ..providers.runtime import configuration, authorization_url, exchange_tokens, ProviderError, valid_oauth_state

_logger = logging.getLogger(__name__)


class MailOAuth(http.Controller):
    def _response(self, success):
        # Deliberately no provider codes/tokens/errors in the HTML response.
        message = ('Account connected. Return to Mail Client and refresh your accounts.' if success else
                   'Connection failed or cancelled. Return to Odoo, check the setup and reconnect with the exact email address.')
        return request.make_response('<!doctype html><html><head><title>Mail connection</title></head><body><h2>' + message + '</h2><a href="/odoo">Return to Odoo</a></body></html>',
                                     headers=[('Content-Type', 'text/html; charset=utf-8'), ('Cache-Control', 'no-store'),
                                              ('Referrer-Policy', 'no-referrer'), ('X-Content-Type-Options', 'nosniff')])

    @http.route('/mail/oauth/start', type='http', auth='user', methods=['GET'])
    def start(self, account_id=None, **kwargs):
        try:
            account = request.env['mail_client.account'].browse(int(account_id))
            account._require_owner()
            if account.provider not in ('gmail', 'outlook'):
                return self._response(False)
            config = configuration(account.provider)
            state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
            request.session['mail_client_oauth'] = {'state': state, 'verifier': verifier, 'account_id': account.id,
                                                        'uid': request.env.uid, 'db': request.env.cr.dbname,
                                                        'company_ids': request.env.companies.ids, 'expires': time.time() + 600}
            # Explicit external redirect; only fixed Google/Microsoft origins are generated.
            return request.redirect(authorization_url(account.provider, config, state, verifier, account.email_address), local=False)
        except (ValueError, TypeError, AccessError, UserError, ProviderError):
            return self._response(False)

    @http.route('/mail/oauth/callback', type='http', auth='user', methods=['GET'])
    def callback(self, state=None, code=None, error=None, **kwargs):
        pending = request.session.pop('mail_client_oauth', None)
        if not valid_oauth_state(pending, state, request.env.uid, request.env.cr.dbname) or error or not code:
            _logger.warning('OAuth callback: invalid state or error param', extra={'state': state, 'error': error, 'has_code': bool(code)})
            return self._response(False)
        try:
            # Current user allowed-company validation occurs through the environment and ir.access.
            account = request.env['mail_client.account'].with_context(allowed_company_ids=pending['company_ids']).browse(pending['account_id'])
            account._require_owner()
            config = configuration(account.provider)
            tokens = exchange_tokens(account.provider, config, code=code, verifier=pending['verifier'])
            identity = get_provider(account.provider, tokens['access_token'], account.email_address).identity()
            account._complete_oauth(tokens, identity)
            _logger.info('OAuth callback: successfully connected account %s', account.id)
            return self._response(True)
        except (AccessError, UserError, ProviderError) as e:
            _logger.warning('OAuth callback: provider error for account %s: %s', pending.get('account_id'), e)
            return self._response(False)
        except Exception as e:
            _logger.exception('OAuth callback: unexpected error for account %s', pending.get('account_id'))
            return self._response(False)