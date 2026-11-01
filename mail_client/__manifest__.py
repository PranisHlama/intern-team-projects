{
    'name': 'Mail Client',
    'summary': 'Gmail and Outlook API mail plus POP3 receiving and SMTP sending with a minimal interface',
    'version': '20.0.2.4.2',
    'category': 'Productivity',
    'license': 'LGPL-3',
    'external_dependencies': {'python': ['requests', 'cryptography']},
    'depends': ['base', 'web'],
    'data': [
        'security/mail_client_security.xml',
        'views/mail_account_views.xml',
        'views/mail_client_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'mail_client/static/src/js/sample_mail.js',
            'mail_client/static/src/js/mail_client.js',
            'mail_client/static/src/xml/mail_client.xml',
            'mail_client/static/src/scss/mail_client.scss',
        ],
    },
    'application': True,
    'installable': True,
}
