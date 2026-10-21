{
    'name': "HR Resignation Management",
    'version': '20.0.1.0.3',
    'description': """
       A product of Bac Ha Software provides solution to resignation process for employees
    """,
    'author': 'Bac Ha Software',
    'company': 'Bac Ha Software',
    'maintainer': 'Bac Ha Software',
    'website': "https://bachasoftware.com",
    'category': 'Human Resources',
    'depends': ['hr', 'hr_holidays', 'hr_resignation'],
    # Same models/flow as bhs_resignation_workflow_controller: the two modules cannot be installed together
    'excludes': ['bhs_resignation_workflow_controller'],
    'data': [
        'data/departure_code_add.xml',
        'data/resignation_mail_template_data.xml',
        'views/departure_reason_extend.xml',
        'views/resignation_view.xml',
        'security/ir.access.csv',

    ],
    'assets': {
        'web.assets_tests': [
            'bhs_resignation/static/tests/tours/**/*',
        ],
    },
    'images': ['static/description/banner.gif'],
    'license': 'LGPL-3',
    'installable': True,
    'application': False
}