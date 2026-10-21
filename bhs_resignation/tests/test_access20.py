# -*- coding: utf-8 -*-
"""Access rights per role.

- checklist: every internal user read/write/create (no domain), HR officer full;
- checklist template: every internal user read, HR manager full;
- hr.resignation: HR manager full (adds unlink on top of hr_resignation's create/read/write), own requests for the
  employee, other employees' requests for the HR officer (hr_resignation 20);
- portal: nothing.
"""
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .common import ResignationCommon

MODELS = ('hr.resignation', 'hr.resignation.checklist', 'hr.resignation.checklist.template')


@tagged('post_install', '-at_install')
class TestResignationAccess(ResignationCommon):

    def test_checklist_template_rights(self):
        Template = self.env['hr.resignation.checklist.template']
        for user in (self.user, self.officer):
            self.assertTrue(Template.with_user(user).search([('id', 'in', self.templates.ids)]))
            with self.assertRaises(AccessError):
                Template.with_user(user).create({'name': 'RSG denied'})
            with self.assertRaises(AccessError):
                self.templates[0].with_user(user).write({'name': 'RSG denied'})
            with self.assertRaises(AccessError):
                self.templates[0].with_user(user).unlink()
        template = Template.with_user(self.manager).create({'name': 'RSG by manager', 'use_for': 'trainee'})
        template.write({'type': 'hr'})
        template.unlink()

    def test_checklist_rights(self):
        res = self._confirmed()
        res.with_user(self.officer).manager_approved_resignation()
        item = res.checklist_ids[:1]
        item.with_user(self.user).write({'check_box': True})
        self.assertTrue(item.check_box)
        Checklist = self.env['hr.resignation.checklist']
        own = Checklist.with_user(self.user).create({'name': 'RSG own item', 'resignation_id': res.id})
        with self.assertRaises(AccessError):
            own.with_user(self.user).unlink()
        own.with_user(self.officer).unlink()

    def test_resignation_unlink_only_hr_manager(self):
        res = self._confirmed()
        for user in (self.user, self.officer):
            with self.assertRaises(AccessError):
                res.with_user(user).unlink()
        res.with_user(self.manager).unlink()
        self.assertFalse(res.exists())

    def test_employee_sees_only_own_request(self):
        res = self._confirmed()
        res_other = self.env['hr.resignation'].create(self._vals(employee_id=self.other.id))
        both = res | res_other
        self.assertEqual(self.env['hr.resignation'].with_user(self.user).search([('id', 'in', both.ids)]), res)
        self.assertEqual(self.env['hr.resignation'].with_user(self.other_user).search([('id', 'in', both.ids)]),
                         res_other)
        with self.assertRaises(AccessError):
            res.with_user(self.other_user).read(['state'])
        with self.assertRaises(AccessError):
            res.with_user(self.other_user).write({'reason_html': '<p>changed</p>'})
        for user in (self.officer, self.manager):
            self.assertEqual(self.env['hr.resignation'].with_user(user).search([('id', 'in', both.ids)]), both)

    def test_employee_cannot_file_for_another_employee(self):
        with self.assertRaises((AccessError, ValidationError)):
            self.env['hr.resignation'].with_user(self.other_user).create(self._vals())
        res = self.env['hr.resignation'].with_user(self.officer).create(self._vals())
        self.assertEqual(res.employee_id, self.employee)

    def test_portal_has_no_access(self):
        res = self.env['hr.resignation'].create(self._vals())
        for model in MODELS:
            with self.subTest(model=model), self.assertRaises(AccessError):
                self.env[model].with_user(self.portal).search([])
        with self.assertRaises(AccessError):
            res.with_user(self.portal).read(['state'])
        with self.assertRaises(AccessError):
            self.env['hr.resignation'].with_user(self.portal).create(self._vals())

    def test_admin_full_access(self):
        admin = self.env.ref('base.user_admin')
        res = self.env['hr.resignation'].with_user(admin).create(self._vals())
        res.with_user(admin).action_confirm_resignation()
        res.with_user(admin).manager_approved_resignation()
        self.assertEqual(res.state, 'manager_approved')
        res.with_user(admin).unlink()

    def test_retired_is_hr_data(self):
        """retired is HR data like resigned/fired (hr_resignation 20): HR officer reads it, a plain user does not."""
        self.assertEqual(self.env['hr.employee']._fields['retired'].groups, 'hr.group_hr_user')
        self.assertFalse(self.employee.with_user(self.officer).retired)
        with self.assertRaises(AccessError):
            self.employee.with_user(self.user).read(['retired'])

    def test_departure_code(self):
        self.assertEqual(self.env.ref('hr.departure_fired').departure_code, 'fired')
        self.assertEqual(self.env.ref('hr.departure_resigned').departure_code, 'resigned')
        self.assertEqual(self.env.ref('hr.departure_retired').departure_code, 'retired')


@tagged('post_install', '-at_install')
class TestResignationViews(ResignationCommon):
    """List/form views open for every role that may use them."""

    def test_views_open(self):
        admin = self.env.ref('base.user_admin')
        cases = [
            (admin, 'hr.resignation', ['list', 'form', 'search']),
            (self.manager, 'hr.resignation', ['list', 'form']),
            (self.officer, 'hr.resignation', ['list', 'form']),
            (self.user, 'hr.resignation', ['list', 'form']),
            (admin, 'hr.resignation.checklist.template', ['list']),
            (self.manager, 'hr.resignation.checklist.template', ['list']),
            (self.officer, 'hr.resignation.checklist.template', ['list']),
            (self.user, 'hr.resignation.checklist.template', ['list']),
        ]
        for user, model, view_types in cases:
            with self.subTest(user=user.login, model=model):
                views = self.env[model].with_user(user).get_views([(False, vt) for vt in view_types])
                self.assertEqual(set(views['views']), set(view_types))
        form = self.env['hr.resignation'].with_user(self.manager).get_views([(False, 'form')])['views']['form']['arch']
        for name in ('reason_html', 'checklist_ids', 'used_allocation', 'worked_month', 'revoked_allocation',
                     'manager_approved_resignation', 'complete_hand_over_resignation', 'approve_resignation'):
            self.assertIn(f'name="{name}"', form)
        self.assertNotIn('name="action_approve_resignation"', form)
        # the plain employee does not get the HR buttons
        form_user = self.env['hr.resignation'].with_user(self.user).get_views([(False, 'form')])['views']['form']['arch']
        self.assertNotIn('name="approve_resignation"', form_user)
        self.assertNotIn('name="manager_approved_resignation"', form_user)
        self.assertIn('name="complete_hand_over_resignation"', form_user)

    def test_actions(self):
        action = self.env['ir.actions.act_window']._for_xml_id('hr_resignation.hr_resignation_action')
        self.assertEqual(action['path'], 'resignation')
        approved = self.env['ir.actions.act_window']._for_xml_id('hr_resignation.hr_resignation_approved_action')
        self.assertEqual(approved['path'], 'resignation-approved')
        menu = self.env.ref('hr_resignation.hr_resignation_menu_root')
        self.assertEqual(menu.parent_id, self.env.ref('hr.menu_hr_root'))
        self.assertEqual(self.env.ref('bhs_resignation.employee_resignation_checklist_template_menu').parent_id,
                         self.env.ref('hr.menu_config_employee'))
