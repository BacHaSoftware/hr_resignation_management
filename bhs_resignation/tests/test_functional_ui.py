# -*- coding: utf-8 -*-
"""UI-level functional tests.

- Odoo's clickbot clicks every menu of the Employees app (the module moves the Resignation menu under it and adds the
  checklist template menu in its configuration), switches views, opens forms, creates records and clicks filters as
  administrator, HR officer and plain employee. Runs only when invoked explicitly (slow):
  $T test --db <test db> --clickbot bhs_resignation
- Browser tours (standard test suite): the main flow create request -> Confirm -> Manager Approve -> Complete Hand
  over -> Approve, the employee filing its own request, and the checklist template list.
"""
from datetime import date

from odoo.tests import HttpCase, tagged

MODULE = 'bhs_resignation'
APPS: list[str] = ['hr.menu_hr_root']
# No base.default_user_group: a DB may make it imply HR manager rights, the lower roles would then click as HR
# manager
ROLES = {
    'click_rsg_admin': ['base.group_user', 'base.group_system', 'hr.group_hr_manager'],
    'click_rsg_hr_officer': ['base.group_user', 'hr.group_hr_user'],
    'click_rsg_employee': ['base.group_user'],
}
TZ = 'Asia/Ho_Chi_Minh'


class ClickbotMixin:
    module = MODULE

    def _make_user(self, login, groups):
        group_ids = [self.env.ref(g).id for g in groups]
        existing = self.env['res.users'].search([('login', '=', login)])
        if existing:
            # login already exists (previous run): reset the role's groups and password
            existing.write({'group_ids': [(6, 0, group_ids)], 'password': login})
            return existing
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login, 'password': login, 'email': f'{login}@click.example.com', 'tz': TZ,
            'group_ids': [(6, 0, group_ids)],
        })

    def _employee(self, name, **extra):
        return self.env['hr.employee'].create(dict({
            'name': name, 'work_email': f'{name.lower().replace(" ", ".")}@example.com',
            'date_version': date(2020, 1, 1), 'contract_date_start': date(2020, 1, 1),
        }, **extra))

    def _templates(self):
        Template = self.env['hr.resignation.checklist.template']
        Template.search([]).unlink()
        return Template.create([
            {'name': 'Tour: return laptop', 'use_for': 'employee', 'type': 'employee'},
            {'name': 'Tour: manager review', 'use_for': 'employee', 'type': 'manager'},
            {'name': 'Tour: HR exit interview', 'use_for': 'employee', 'type': 'hr'},
            {'name': 'Tour: return badge', 'use_for': 'trainee', 'type': 'employee'},
        ])

    def run_clickbot(self, roles, apps=None):
        apps = apps or APPS
        for login, groups in roles.items():
            user = self._make_user(login, groups)
            visible = self.env['ir.ui.menu'].with_user(user)
            if 'hr.group_hr_manager' not in groups:
                self.assertFalse(user.has_group('hr.group_hr_manager'), f"Role {login} must not be HR manager")
            for xmlid in apps:
                self.assertIn(self.env.ref(xmlid).id, visible._visible_menu_ids(),
                              f"Role {login} must see app {xmlid}")
                with self.subTest(role=login, app=xmlid):
                    self.browser_js(
                        '/odoo',
                        "odoo.loader.modules.get('@web/webclient/clickbot/clickbot_loader')"
                        f".startClickEverywhere({{ xmlId: '{xmlid}', logger: true }});",
                        login=login, timeout=1800, success_signal='clickbot test succeeded')


@tagged('click_all', 'post_install', '-at_install', '-standard')
class TestFunctionalUI(ClickbotMixin, HttpCase):

    def _seed_data(self):
        """Templates + requests in several states so the resignation lists/forms have data."""
        self._templates()
        Resignation = self.env['hr.resignation']
        for idx, state in enumerate(('draft', 'confirm', 'manager_approved', 'handed_over')):
            employee = self._employee(f'Click RSG {idx}')
            res = Resignation.create({'employee_id': employee.id, 'reason_html': '<p>Click</p>',
                                      'expected_revealing_date': date(2026, 1, 15)})
            if state != 'draft':
                res.action_confirm_resignation()
            if state in ('manager_approved', 'handed_over'):
                res.manager_approved_resignation()
            if state == 'handed_over':
                res.complete_hand_over_resignation()
        # the employee role files its own request
        employee_user = self._make_user('click_rsg_employee', ROLES['click_rsg_employee'])
        own = self._employee('Click RSG Own', user_id=employee_user.id)
        Resignation.create({'employee_id': own.id, 'reason_html': '<p>Own</p>',
                            'expected_revealing_date': date(2026, 1, 15)})

    def test_clickbot_module_apps(self):
        self._seed_data()
        self.run_clickbot(ROLES)


@tagged('post_install', '-at_install')
class TestResignationTour(ClickbotMixin, HttpCase):
    """Main flows through a real browser."""

    def setUp(self):
        super().setUp()
        self.hr_manager = self._make_user('tour_rsg_hr_manager', ['base.group_user', 'hr.group_hr_manager'])
        self._templates()

    def test_resignation_main_flow(self):
        employee = self._employee('RSG Tour Employee')
        self.start_tour('/odoo/resignation/new', 'bhs_rsg_main_flow_tour', login=self.hr_manager.login)
        res = self.env['hr.resignation'].search([('employee_id', '=', employee.id)])
        self.assertEqual(len(res), 1)
        self.assertEqual(res.state, 'approved')
        self.assertEqual(res.expected_revealing_date, date(2026, 1, 15))
        self.assertEqual(sorted(res.checklist_ids.mapped('name')),
                         ['Tour: HR exit interview', 'Tour: manager review', 'Tour: return laptop'])
        self.assertTrue(all(res.checklist_ids.mapped('check_box')))
        self.assertFalse(employee.active)
        self.assertEqual(employee.departure_id.departure_date, date(2026, 1, 15))
        self.assertEqual(employee.departure_reason_id, self.env.ref('hr.departure_resigned'))

    def test_employee_files_own_request(self):
        user = self._make_user('tour_rsg_employee', ['base.group_user'])
        employee = self._employee('RSG Tour Own', user_id=user.id)
        self.start_tour('/odoo/resignation/new', 'bhs_rsg_employee_request_tour', login=user.login)
        res = self.env['hr.resignation'].search([('employee_id', '=', employee.id)])
        self.assertEqual(len(res), 1)
        self.assertEqual(res.state, 'confirm')
        self.assertIn('Moving abroad', str(res.reason_html))

    def test_checklist_template_list(self):
        self.start_tour('/odoo/action-bhs_resignation.view_employee_resignation_checklist_template_act',
                        'bhs_rsg_checklist_template_tour', login=self.hr_manager.login)
        template = self.env['hr.resignation.checklist.template'].search([('name', '=', 'Tour: sign NDA')])
        self.assertEqual(len(template), 1)
        self.assertEqual((template.type, template.use_for), ('employee', 'employee'))
