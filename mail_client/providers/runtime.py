"""Server-only OAuth configuration and encrypted token files. Never mail content."""
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import stat
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests
from cryptography.fernet import Fernet, InvalidToken

_logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """A deliberately sanitized, user-facing integration error."""


def _get_env(key, default=None):
    """Get env var, treating empty string as unset."""
    value = os.environ.get(key)
    return value if value else default


def _load_module_env():
    """Load .env file from module directory."""
    # Module root is parent of providers/
    module_root = Path(__file__).resolve().parent.parent
    env_path = module_root / '.env'
    if not env_path.exists():
        return {}
    try:
        config = {}
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            config[key.strip()] = value.strip().strip('"\'')
        return config
    except OSError:
        return {}


def _load_json_config():
    """Load JSON config file if exists and valid."""
    config_path = Path(_get_env('MC_CONFIG_FILE', '/var/lib/odoo/mail-client-config.json'))
    try:
        if config_path.is_symlink():
            raise ProviderError('Config file must not be a symlink.')
        mode = config_path.stat().st_mode
        if mode & 0o077:
            raise ProviderError('Config file must have 600 permissions.')
        content = config_path.read_text()
        return json.loads(content)
    except (OSError, json.JSONDecodeError):
        return None


def server_configuration():
    """Load base config: encryption_key, base_url from env vars or .env file or JSON file."""
    # Priority 1: System environment variables (highest)
    encryption_key = _get_env('MC_ENCRYPTION_KEY')
    base_url = _get_env('MC_BASE_URL')
    source = 'environment'

    # Priority 2: Module .env file
    if not encryption_key or not base_url:
        module_env = _load_module_env()
        encryption_key = encryption_key or module_env.get('MC_ENCRYPTION_KEY')
        base_url = base_url or module_env.get('MC_BASE_URL')
        if encryption_key or base_url:
            source = 'module_env'

    # Priority 3: JSON file fallback
    if not encryption_key or not base_url:
        file_config = _load_json_config()
        if file_config:
            encryption_key = encryption_key or file_config.get('encryption_key')
            base_url = base_url or file_config.get('base_url')
            source = 'file'

    # Validate
    if not encryption_key:
        raise ProviderError('Encryption key not configured. Set MC_ENCRYPTION_KEY in environment, .env file, or JSON config.')
    try:
        Fernet(encryption_key.encode())
    except Exception:
        raise ProviderError('Invalid encryption key format. Must be base64-encoded 32-byte Fernet key.')

    if not base_url:
        raise ProviderError('Base URL not configured. Set MC_BASE_URL in environment, .env file, or JSON config.')
    # Validate URL format (HTTPS or localhost)
    parsed = urlparse(base_url)
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1')):
        raise ProviderError('Base URL must be HTTPS (or http://localhost for development).')
    if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ProviderError('Base URL must not contain credentials, path, query, or fragment.')

    _logger.info("Mail Client config loaded from: %s", source)
    return {
        'encryption_key': encryption_key,
        'base_url': base_url.rstrip('/'),
        '_source': source,
    }


def configuration(provider):
    """Load OAuth provider config: client_id, client_secret, tenant, redirect_uri."""
    if provider not in ('gmail', 'outlook'):
        raise ProviderError('OAuth is available only for Gmail and Outlook.')

    base = server_configuration()
    source = base['_source']
    redirect_uri = base['base_url'] + '/mail/oauth/callback'

    # Helper to get value from priority sources
    def get_config(key, default=None):
        val = _get_env(key)
        if val:
            return val
        if source == 'module_env':
            module_env = _load_module_env()
            val = module_env.get(key)
            if val:
                return val
        if source == 'file':
            file_config = _load_json_config()
            if file_config:
                val = file_config.get('gmail', {}).get(key) or file_config.get('outlook', {}).get(key)
                if val:
                    return val
        return default

    if provider == 'gmail':
        client_id = get_config('MC_GMAIL_CLIENT_ID')
        client_secret = get_config('MC_GMAIL_CLIENT_SECRET')
        if not client_id or not client_secret:
            raise ProviderError('Gmail OAuth not configured. Set MC_GMAIL_CLIENT_ID and MC_GMAIL_CLIENT_SECRET.')
        return {
            **base,
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': redirect_uri,
            'tenant': 'common',
            '_source': source,
        }

    # Outlook
    client_id = get_config('MC_OUTLOOK_CLIENT_ID')
    client_secret = get_config('MC_OUTLOOK_CLIENT_SECRET')
    tenant = get_config('MC_OUTLOOK_TENANT', 'common')
    if not client_id or not client_secret:
        raise ProviderError('Outlook OAuth not configured. Set MC_OUTLOOK_CLIENT_ID and MC_OUTLOOK_CLIENT_SECRET.')
    return {
            **base,
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': redirect_uri,
            'tenant': tenant,
            '_source': source,
        }


def http_json(method, url, *, headers=None, params=None, data=None, payload=None):
    # URLs are built only from fixed provider origins; redirects are never followed.
    try:
        with requests.request(method, url, headers=headers, params=params, data=data, json=payload,
                              timeout=(5, 25), allow_redirects=False, stream=True) as response:
            if not 200 <= response.status_code < 300:
                status = response.status_code
                if status in (401, 403):
                    raise ProviderError('Provider authorization failed. Reconnect the account or check consent permissions.')
                if status == 404:
                    raise ProviderError('The message no longer exists at the provider. Refresh the inbox.')
                if status == 429:
                    raise ProviderError('Provider rate limit reached. Wait before trying again.')
                raise ProviderError(f'Provider request failed (HTTP {status}). No automatic retry was made.')
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 12 * 1024 * 1024:
                    raise ProviderError('This response is too large for the current mail client. Open it at the provider.')
                chunks.append(chunk)
            raw = b''.join(chunks)
            return json.loads(raw) if raw else {}
    except ProviderError:
        raise
    except (requests.RequestException, ValueError):
        raise ProviderError('Provider connection failed. If sending was in progress, check Sent before retrying; delivery may have succeeded.') from None


class TokenVault:
    def __init__(self, database, config, storage_root=None):
        self.key = Fernet(config['encryption_key'].encode())
        # Independent database namespace; the root is deliberately outside addon/assets paths.
        parent = Path(storage_root or '/var/lib/odoo/mail-client-secrets')
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(parent, 0o700)
        self.root = parent / hashlib.sha256(database.encode()).hexdigest()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _path(self, reference):
        if not isinstance(reference, str) or not re.fullmatch(r'[a-f0-9]{64}', reference):
            raise ProviderError('Account credentials are unavailable. Reconnect this account.')
        return self.root / (reference + '.token')

    def read(self, reference):
        path = self._path(reference)
        try:
            if path.is_symlink() or path.stat().st_mode & 0o077:
                raise ProviderError('Credential file permissions are invalid.')
            return json.loads(self.key.decrypt(path.read_bytes()))
        except (OSError, InvalidToken, ValueError):
            raise ProviderError('Account credentials are unavailable. Reconnect this account.') from None

    def save(self, tokens, reference=None):
        reference = reference or secrets.token_hex(32)
        path = self._path(reference)
        temporary = path.with_suffix('.' + secrets.token_hex(8) + '.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(self.key.encrypt(json.dumps(tokens).encode()))
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return reference

    def remove(self, reference):
        if reference:
            self._path(reference).unlink(missing_ok=True)


def endpoints(provider, config):
    if provider == 'gmail':
        return ('https://accounts.google.com/o/oauth2/v2/auth', 'https://oauth2.googleapis.com/token',
                'https://www.googleapis.com/auth/gmail.modify')
    base = 'https://login.microsoftonline.com/' + config['tenant'] + '/oauth2/v2.0/'
    return (base + 'authorize', base + 'token', 'offline_access User.Read Mail.ReadWrite Mail.Send')


def authorization_url(provider, config, state, verifier, email):
    authorize, _, scope = endpoints(provider, config)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    params = dict(client_id=config['client_id'], redirect_uri=config['redirect_uri'], response_type='code',
                  scope=scope, state=state, code_challenge=challenge, code_challenge_method='S256', login_hint=email)
    if provider == 'gmail':
        params.update(access_type='offline', prompt='select_account consent', include_granted_scopes='true')
    else:
        params.update(response_mode='query', prompt='select_account')
    return authorize + '?' + urlencode(params)


def exchange_tokens(provider, config, *, code=None, verifier=None, refresh_token=None):
    _, endpoint, scope = endpoints(provider, config)
    data = dict(client_id=config['client_id'], client_secret=config['client_secret'])
    if refresh_token:
        data.update(grant_type='refresh_token', refresh_token=refresh_token)
    else:
        data.update(grant_type='authorization_code', code=code, code_verifier=verifier, redirect_uri=config['redirect_uri'])
    if provider == 'outlook':
        data['scope'] = scope
    result = http_json('POST', endpoint, data=data)
    if not result.get('access_token'):
        raise ProviderError('Provider returned no access token. Reconnect the account.')
    if not result.get('refresh_token') and refresh_token:
        result['refresh_token'] = refresh_token
    if not result.get('refresh_token'):
        raise ProviderError('No refresh token was granted. Reconnect and grant offline access.')
    result['expires_at'] = time.time() + int(result.get('expires_in', 3600))
    return {k: result[k] for k in ('access_token', 'refresh_token', 'expires_at')}


def active_token(provider, config, vault, reference):
    # Serialized refresh across Odoo workers, not a global ORM transaction lock.
    import fcntl
    lock_path = vault._path(reference).with_suffix('.lock')
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'w') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        tokens = vault.read(reference)
        if tokens['expires_at'] < time.time() + 90:
            tokens = exchange_tokens(provider, config, refresh_token=tokens['refresh_token'])
            vault.save(tokens, reference)
        return tokens['access_token']


def valid_oauth_state(pending, state, uid, database, now=None):
    try:
        return bool(isinstance(state, str) and secrets.compare_digest(state, pending['state'])
                    and pending['uid'] == uid and pending['db'] == database
                    and pending['expires'] > (time.time() if now is None else now))
    except (TypeError, KeyError):
        return False