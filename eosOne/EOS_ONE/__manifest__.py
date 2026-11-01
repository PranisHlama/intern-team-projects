{
    'name': 'EOS One',
    'version': '20.0.1.0.0',
    'category': 'Services/Management',
    'summary': 'EOS Business Operating System',
    'description': 'EOS management and accountability system',
    'author': 'Custom Development',
    'license': 'LGPL-3',

    'depends': [
        'base',
        'mail',
        'hr',
    ],

    'data': [
        'security/eosone_groups.xml',
        'security/ir.model.access.csv',
        'security/eosone_rules.xml',

        'data/eosone_sequence.xml',

        'views/eos_team_views.xml',
        'views/eos_meeting_views.xml',
        'views/eos_scorecard_views.xml',
        'views/eos_rock_views.xml',
        'views/eos_issue_views.xml',
        'views/eos_todo_views.xml',
        'views/eos_headline_views.xml',
        'views/eosone_menus.xml',
    ],

    'application': True,
    'installable': True,
}