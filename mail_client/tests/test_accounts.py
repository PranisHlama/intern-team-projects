"""Run on a disposable Odoo 20 database with --test-enable.
These integration tests require Odoo/PostgreSQL; see TESTING.md.
"""
from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'mail_client_foundation')
class TestMailAccounts(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env.ref('mail_client.group_mail_client_user')
        manager_group = cls.env.ref('mail_client.group_mail_client_manager')
        cls.alice, cls.bob, cls.manager = cls.env['res.users'].create([
            {'name': name, 'login': login, 'group_ids': [Command.set(groups)],
             'company_id': cls.env.company.id, 'company_ids': [Command.set(cls.env.company.ids)]}
            for name, login, groups in [
                ('Alice Mail Test', 'mmc_alice_test', [group.id]),
                ('Bob Mail Test', 'mmc_bob_test', [group.id]),
                ('Manager Mail Test', 'mmc_manager_test', [manager_group.id]),
            ]
        ])
        cls.Account = cls.env['mail_client.account']
        cls.alice_account = cls.Account.with_user(cls.alice).create({
            'name': 'Alice Gmail', 'provider': 'gmail', 'email_address': 'alice@example.test',
        })
        cls.bob_account = cls.Account.with_user(cls.bob).create({
            'name': 'Bob Outlook', 'provider': 'outlook', 'email_address': 'bob@example.test',
        })

    def test_account_list_is_private(self):
        rows = self.Account.with_user(self.alice).list_client_accounts()
        self.assertEqual([r['id'] for r in rows], [self.alice_account.id])
        self.assertNotIn('credential_ref', rows[0])
        self.assertNotIn('body', rows[0])

    def test_pop3_receive_only_is_enforced_by_server(self):
        account = self.Account.with_user(self.alice).create({
            'name': 'Alice POP3', 'provider': 'pop3', 'email_address': 'pop@example.test',
        })
        for operation in ('save_draft', 'send_draft', 'reply', 'set_read', 'get_attachment'):
            with self.assertRaisesRegex(UserError, 'POP3 does not support'):
                account._invoke_provider(operation)
        rows = account.list_client_accounts()
        profile = next(r for r in rows if r['id'] == account.id)
        self.assertFalse(profile['capabilities']['send'])
        self.assertNotIn('password', profile)

    def test_pop3_cross_owner_connection_denied(self):
        account = self.Account.with_user(self.alice).create({
            'name': 'Alice POP3', 'provider': 'pop3', 'email_address': 'pop@example.test',
        })
        with self.assertRaises(AccessError):
            account.with_user(self.bob).action_connect()

    def test_cross_owner_read_and_write_denied(self):
        account = self.bob_account.with_user(self.alice)
        with self.assertRaises(AccessError):
            account.read(['email_address'])
        with self.assertRaises(AccessError):
            account.write({'name': 'Not allowed'})
        with self.assertRaises(AccessError):
            account.get_message('message-id')

    def test_no_ownership_spoofing(self):
        with self.assertRaises(AccessError):
            self.Account.with_user(self.alice).create({
                'name': 'Spoof', 'email_address': 'spoof@example.test',
                'provider': 'gmail', 'owner_id': self.bob.id,
            })
        with self.assertRaises(UserError):
            self.alice_account.with_user(self.alice).write({'owner_id': self.bob.id})

    def test_no_forged_connection_or_credentials(self):
        with self.assertRaises(AccessError):
            self.alice_account.with_user(self.alice).write({'connection_state': 'connected'})
        with self.assertRaises(AccessError):
            self.Account.with_user(self.alice).create({
                'name': 'Forged', 'provider': 'gmail', 'email_address': 'forged@example.test',
                'credential_ref': 'not-an-allowed-value',
            })
        with self.assertRaises(AccessError):
            self.alice_account.with_user(self.alice).read(['credential_ref'])

    def test_context_cannot_forge_oauth_state(self):
        account = self.Account.with_user(self.alice).with_context(
            default_connection_state='connected', default_provider_account_id='forged',
            default_credential_ref='forged-token',
        ).create({'name': 'Safe defaults', 'provider': 'gmail', 'email_address': 'safe@example.test'})
        self.assertEqual(account.connection_state, 'not_connected')
        self.assertFalse(account.provider_account_id)
        self.assertFalse(account.sudo().credential_ref)

    def test_manager_metadata_does_not_grant_mailbox_access(self):
        account = self.alice_account.with_user(self.manager)
        self.assertEqual(account.read(['name'])[0]['name'], 'Alice Gmail')
        with self.assertRaises(AccessError):
            account.list_messages()

    def test_unconnected_provider_calls_fail_explicitly(self):
        account = self.alice_account.with_user(self.alice)
        with self.assertRaisesRegex(UserError, 'not connected'):
            account.list_messages()
        with self.assertRaises(UserError):
            account.save_draft({'to': 'x@example.test', 'subject': 'Test', 'body': 'Not persisted'})
        self.assertEqual(account.connection_state, 'not_connected')

    def test_pagination_and_draft_validation(self):
        account = self.alice_account.with_user(self.alice)
        with self.assertRaises(ValidationError):
            account.list_messages(limit=1000)
        with self.assertRaises(ValidationError):
            account.list_messages(limit=True)
        with self.assertRaises(ValidationError):
            account.save_draft({'credential_ref': 'unexpected'})

    def test_oauth_identity_and_private_update_ownership(self):
        account = self.alice_account.with_user(self.alice)
        with self.assertRaises(UserError):
            account._complete_oauth({}, {'id': 'other', 'email': 'other@example.test'})
        with self.assertRaises(AccessError):
            self.alice_account.with_user(self.manager)._set_connection({'connection_state': 'connected'})
        with self.assertRaises(AccessError):
            account._set_connection({'owner_id': self.bob.id})
        self.assertEqual(account.connection_state, 'not_connected')

    def test_company_isolation(self):
        other_company = self.env['res.company'].create({'name': 'Mail isolated company'})
        self.alice.company_ids = [Command.link(other_company.id)]
        other = self.Account.with_user(self.alice).with_context(allowed_company_ids=[other_company.id]).create({
            'name': 'Other Company', 'provider': 'gmail', 'email_address': 'other@example.test',
        })
        with self.assertRaises(AccessError):
            other.with_user(self.alice).with_context(allowed_company_ids=[self.env.company.id]).read(['name'])
        with self.assertRaises(AccessError):
            self.Account.with_user(self.bob).create({
                'name': 'Blocked Company', 'provider': 'gmail', 'email_address': 'blocked@example.test',
                'company_id': other_company.id,
            })

    def test_pop3_configuration_and_send_require_owner(self):
        account = self.Account.with_user(self.alice).create({
            'name': 'Private server mailbox', 'provider': 'pop3', 'email_address': 'private@example.test',
        })
        with self.assertRaises(AccessError):
            account.with_user(self.bob).configure_pop3_account({})
        with self.assertRaises(AccessError):
            account.with_user(self.bob).send_message({'to': 'r@example.test', 'subject': 'S', 'body': 'B'})
        with self.assertRaises(UserError):
            self.alice_account.with_user(self.alice).configure_pop3_account({})
        with self.assertRaises(UserError):
            self.alice_account.with_user(self.alice).send_message({'to': 'r@example.test', 'subject': 'S', 'body': 'B'})

    def test_pop3_encrypted_payload_binding_and_private_account_listing(self):
        from unittest.mock import patch
        from ..models import mail_account as module
        from ..providers.runtime import ProviderError
        account = self.Account.with_user(self.alice).create({
            'name': 'Bound server mailbox', 'provider': 'pop3', 'email_address': 'bound@example.test',
        })
        account._set_connection({'credential_ref': 'fixture-reference'})
        settings = dict(username='bound@example.test', password='fixture-password', pop_host='pop.gmail.com', pop_port=995,
                        smtp_host='smtp.gmail.com', smtp_port=465, smtp_security='ssl')
        payload = dict(settings=settings, email=account.email_address, owner_id=self.alice.id, company_id=account.company_id.id)
        with patch.object(module, 'server_configuration', return_value={}), patch.object(module, 'TokenVault') as vault:
            vault.return_value.read.return_value = payload
            self.assertEqual(account._pop3_config()['smtp']['password'], 'fixture-password')
            vault.return_value.read.return_value = dict(payload, owner_id=self.bob.id)
            with self.assertRaises(ProviderError): account._pop3_config()
        row = next(r for r in account.list_client_accounts() if r['id'] == account.id)
        self.assertTrue(row['capabilities']['send'])
        self.assertNotIn('credential_ref', row)
        self.assertNotIn('password', row)
