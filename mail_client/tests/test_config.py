"""Tests for environment-based configuration loading."""
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import UserError

from ..providers.runtime import server_configuration, configuration, ProviderError


@tagged('post_install', '-at_install', 'mail_client_config')
class TestConfigLoading(TransactionCase):

    def setUp(self):
        super().setUp()
        # Save original env vars
        self.original_env = {}
        for key in ['MC_ENCRYPTION_KEY', 'MC_BASE_URL', 'MC_GMAIL_CLIENT_ID',
                    'MC_GMAIL_CLIENT_SECRET', 'MC_OUTLOOK_CLIENT_ID',
                    'MC_OUTLOOK_CLIENT_SECRET', 'MC_OUTLOOK_TENANT',
                    'MC_CONFIG_FILE']:
            self.original_env[key] = os.environ.get(key)
            if key in os.environ:
                del os.environ[key]

    def tearDown(self):
        # Restore original env vars
        for key, value in self.original_env.items():
            if value is not None:
                os.environ[key] = value
            elif key in os.environ:
                del os.environ[key]
        super().tearDown()

    def _make_fernet_key(self):
        from cryptography.fernet import Fernet
        return Fernet.generate_key().decode()

    # --- Test server_configuration() ---

    def test_config_env_vars_basic(self):
        """server_configuration() reads encryption_key and base_url from env vars."""
        key = self._make_fernet_key()
        os.environ['MC_ENCRYPTION_KEY'] = key
        os.environ['MC_BASE_URL'] = 'https://odoo.example.com'

        config = server_configuration()

        self.assertEqual(config['encryption_key'], key)
        self.assertEqual(config['base_url'], 'https://odoo.example.com')
        self.assertEqual(config['_source'], 'environment')

    def test_config_env_vars_partial_fallback(self):
        """Partial env vars fall back to JSON file for missing values."""
        key = self._make_fernet_key()
        os.environ['MC_ENCRYPTION_KEY'] = key
        # MC_BASE_URL not set - should fall back to file

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({'base_url': 'https://file.example.com'}, f)
            config_file = f.name
        try:
            os.environ['MC_CONFIG_FILE'] = config_file
            config = server_configuration()
            self.assertEqual(config['encryption_key'], key)
            self.assertEqual(config['base_url'], 'https://file.example.com')
            self.assertEqual(config['_source'], 'file')  # base_url from file
        finally:
            Path(config_file).unlink(missing_ok=True)

    def test_config_json_fallback(self):
        """Falls back to JSON file when no env vars set."""
        key = self._make_fernet_key()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({'encryption_key': key, 'base_url': 'https://file.example.com'}, f)
            config_file = f.name
        try:
            os.environ['MC_CONFIG_FILE'] = config_file
            config = server_configuration()
            self.assertEqual(config['encryption_key'], key)
            self.assertEqual(config['base_url'], 'https://file.example.com')
            self.assertEqual(config['_source'], 'file')
        finally:
            Path(config_file).unlink(missing_ok=True)

    def test_config_precedence_env_over_file(self):
        """Env vars take precedence over JSON file."""
        key_env = self._make_fernet_key()
        key_file = self._make_fernet_key()
        os.environ['MC_ENCRYPTION_KEY'] = key_env
        os.environ['MC_BASE_URL'] = 'https://env.example.com'

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({'encryption_key': key_file, 'base_url': 'https://file.example.com'}, f)
            config_file = f.name
        try:
            os.environ['MC_CONFIG_FILE'] = config_file
            config = server_configuration()
            self.assertEqual(config['encryption_key'], key_env)
            self.assertEqual(config['base_url'], 'https://env.example.com')
            self.assertEqual(config['_source'], 'environment')
        finally:
            Path(config_file).unlink(missing_ok=True)

    def test_config_missing_encryption_key_raises(self):
        """Missing encryption_key raises ProviderError."""
        os.environ['MC_BASE_URL'] = 'https://example.com'
        with self.assertRaises(ProviderError) as cm:
            server_configuration()
        self.assertIn('Encryption key not configured', str(cm.exception))

    def test_config_invalid_fernet_key_raises(self):
        """Invalid Fernet key format raises ProviderError."""
        os.environ['MC_ENCRYPTION_KEY'] = 'not-a-valid-key'
        os.environ['MC_BASE_URL'] = 'https://example.com'
        with self.assertRaises(ProviderError) as cm:
            server_configuration()
        self.assertIn('Invalid encryption key format', str(cm.exception))

    def test_config_missing_base_url_raises(self):
        """Missing base_url raises ProviderError."""
        key = self._make_fernet_key()
        os.environ['MC_ENCRYPTION_KEY'] = key
        with self.assertRaises(ProviderError) as cm:
            server_configuration()
        self.assertIn('Base URL not configured', str(cm.exception))

    def test_config_invalid_base_url_raises(self):
        """Invalid base_url (non-HTTPS, non-localhost) raises ProviderError."""
        key = self._make_fernet_key()
        os.environ['MC_ENCRYPTION_KEY'] = key
        os.environ['MC_BASE_URL'] = 'http://example.com'  # Not HTTPS
        with self.assertRaises(ProviderError) as cm:
            server_configuration()
        self.assertIn('must be HTTPS', str(cm.exception))

    def test_config_localhost_allowed(self):
        """http://localhost is allowed for development."""
        key = self._make_fernet_key()
        os.environ['MC_ENCRYPTION_KEY'] = key
        os.environ['MC_BASE_URL'] = 'http://localhost:8069'
        config = server_configuration()
        self.assertEqual(config['base_url'], 'http://localhost:8069')

    def test_config_file_permissions_checked(self):
        """JSON file with wrong permissions raises ProviderError."""
        key = self._make_fernet_key()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({'encryption_key': key, 'base_url': 'https://example.com'}, f)
            config_file = f.name
        try:
            # Make file world-readable (not 600)
            os.chmod(config_file, 0o644)
            os.environ['MC_CONFIG_FILE'] = config_file
            with self.assertRaises(ProviderError) as cm:
                server_configuration()
            self.assertIn('600 permissions', str(cm.exception))
        finally:
            Path(config_file).unlink(missing_ok=True)

    # --- Test configuration(provider) ---

    def test_gmail_config_env_vars(self):
        """Gmail OAuth config reads from env vars."""
        key = self._make_fernet_key()
        os.environ['MC_ENCRYPTION_KEY'] = key
        os.environ['MC_BASE_URL'] = 'https://odoo.example.com'
        os.environ['MC_GMAIL_CLIENT_ID'] = 'gmail-client-id'
        os.environ['MC_GMAIL_CLIENT_SECRET'] = 'gmail-secret'

        config = configuration('gmail')

        self.assertEqual(config['client_id'], 'gmail-client-id')
        self.assertEqual(config['client_secret'], 'gmail-secret')
        self.assertEqual(config['redirect_uri'], 'https://odoo.example.com/mail/oauth/callback')
        self.assertEqual(config['tenant'], 'common')
        self.assertEqual(config['_source'], 'environment')

    def test_outlook_config_env_vars(self):
        """Outlook OAuth config reads from env vars."""
        key = self._make_fernet_key()
        os.environ['MC_ENCRYPTION_KEY'] = key
        os.environ['MC_BASE_URL'] = 'https://odoo.example.com'
        os.environ['MC_OUTLOOK_CLIENT_ID'] = 'outlook-client-id'
        os.environ['MC_OUTLOOK_CLIENT_SECRET'] = 'outlook-secret'
        os.environ['MC_OUTLOOK_TENANT'] = 'my-tenant'

        config = configuration('outlook')

        self.assertEqual(config['client_id'], 'outlook-client-id')
        self.assertEqual(config['client_secret'], 'outlook-secret')
        self.assertEqual(config['redirect_uri'], 'https://odoo.example.com/mail/oauth/callback')
        self.assertEqual(config['tenant'], 'my-tenant')
        self.assertEqual(config['_source'], 'environment')

    def test_outlook_config_default_tenant(self):
        """Outlook tenant defaults to 'common' when not set."""
        key = self._make_fernet_key()
        os.environ['MC_ENCRYPTION_KEY'] = key
        os.environ['MC_BASE_URL'] = 'https://odoo.example.com'
        os.environ['MC_OUTLOOK_CLIENT_ID'] = 'outlook-client-id'
        os.environ['MC_OUTLOOK_CLIENT_SECRET'] = 'outlook-secret'
        # MC_OUTLOOK_TENANT not set

        config = configuration('outlook')
        self.assertEqual(config['tenant'], 'common')

    def test_gmail_config_json_fallback(self):
        """Gmail config falls back to JSON file."""
        key = self._make_fernet_key()
        os.environ['MC_ENCRYPTION_KEY'] = key
        os.environ['MC_BASE_URL'] = 'https://odoo.example.com'
        # No Gmail env vars

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({
                'encryption_key': key,
                'base_url': 'https://odoo.example.com',
                'gmail': {'client_id': 'file-gmail-id', 'client_secret': 'file-gmail-secret'}
            }, f)
            config_file = f.name
        try:
            os.environ['MC_CONFIG_FILE'] = config_file
            config = configuration('gmail')
            self.assertEqual(config['client_id'], 'file-gmail-id')
            self.assertEqual(config['client_secret'], 'file-gmail-secret')
            self.assertEqual(config['_source'], 'file')
        finally:
            Path(config_file).unlink(missing_ok=True)

    def test_gmail_missing_credentials_raises(self):
        """Missing Gmail credentials raises ProviderError."""
        key = self._make_fernet_key()
        os.environ['MC_ENCRYPTION_KEY'] = key
        os.environ['MC_BASE_URL'] = 'https://odoo.example.com'
        # No Gmail env vars, no JSON file

        with self.assertRaises(ProviderError) as cm:
            configuration('gmail')
        self.assertIn('Gmail OAuth not configured', str(cm.exception))

    def test_outlook_missing_credentials_raises(self):
        """Missing Outlook credentials raises ProviderError."""
        key = self._make_fernet_key()
        os.environ['MC_ENCRYPTION_KEY'] = key
        os.environ['MC_BASE_URL'] = 'https://odoo.example.com'

        with self.assertRaises(ProviderError) as cm:
            configuration('outlook')
        self.assertIn('Outlook OAuth not configured', str(cm.exception))

    def test_invalid_provider_raises(self):
        """Invalid provider name raises ProviderError."""
        with self.assertRaises(ProviderError) as cm:
            configuration('invalid')
        self.assertIn('available only for Gmail and Outlook', str(cm.exception))

    # --- Test empty string handling ---

    def test_empty_env_var_treated_as_unset(self):
        """Empty string env var treated as unset (falls back to file)."""
        key = self._make_fernet_key()
        os.environ['MC_ENCRYPTION_KEY'] = ''  # Empty
        os.environ['MC_BASE_URL'] = ''

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({'encryption_key': key, 'base_url': 'https://file.example.com'}, f)
            config_file = f.name
        try:
            os.environ['MC_CONFIG_FILE'] = config_file
            config = server_configuration()
            self.assertEqual(config['encryption_key'], key)
            self.assertEqual(config['_source'], 'file')
        finally:
            Path(config_file).unlink(missing_ok=True)