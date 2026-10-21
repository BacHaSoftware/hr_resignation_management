# -*- coding: utf-8 -*-
"""Shared fixtures: the resigning employee, its manager, another employee, HR officer, HR manager, portal user."""
from datetime import date, timedelta

from odoo import fields
from odoo.tests import TransactionCase, new_test_user

MODULE = 'bhs_resignation'
REASON = '<p>Leaving for personal reasons</p>'


class ResignationCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.today()
        cls.user = cls._user('rsg_user', 'base.group_user')
        cls.boss_user = cls._user('rsg_boss', 'base.group_user')
        cls.other_user = cls._user('rsg_other', 'base.group_user')
        cls.officer = cls._user('rsg_officer', 'base.group_user,hr.group_hr_user')
        cls.manager = cls._user('rsg_manager', 'base.group_user,hr.group_hr_manager')
        cls.portal = cls._user('rsg_portal', 'base.group_portal')
        cls.boss = cls._employee('RSG Boss', cls.boss_user)
        cls.employee = cls._employee('RSG Employee', cls.user, parent_id=cls.boss.id)
        cls.other = cls._employee('RSG Other', cls.other_user)
        Template = cls.env['hr.resignation.checklist.template']
        cls.templates = Template.create([
            {'name': 'RSG emp: return laptop', 'use_for': 'employee', 'type': 'employee', 'sequence': 1},
            {'name': 'RSG emp: handover docs', 'use_for': 'employee', 'type': 'employee', 'sequence': 2},
            {'name': 'RSG emp: manager review', 'use_for': 'employee', 'type': 'manager', 'sequence': 3},
            {'name': 'RSG emp: HR exit interview', 'use_for': 'employee', 'type': 'hr', 'sequence': 4},
            {'name': 'RSG trainee: return badge', 'use_for': 'trainee', 'type': 'employee', 'sequence': 1},
            {'name': 'RSG trainee: mentor review', 'use_for': 'trainee', 'type': 'manager', 'sequence': 2},
        ])
        # Only the templates of the test take part (a real DB may hold its own templates)
        (Template.search([]) - cls.templates).unlink()

    @classmethod
    def _user(cls, login, groups):
        return new_test_user(cls.env, login=login, groups=groups, email=f'{login}@example.com',
                             name=login.replace('_', ' ').title())

    @classmethod
    def _employee(cls, name, user=None, **extra):
        vals = {
            'name': name,
            'work_email': f'{name.lower().replace(" ", ".")}@example.com',
            'date_version': date(2020, 1, 1),
            'contract_date_start': date(2020, 1, 1),
        }
        if user:
            vals['user_id'] = user.id
        vals.update(extra)
        return cls.env['hr.employee'].create(vals)

    def _vals(self, **extra):
        vals = {
            'employee_id': self.employee.id,
            'expected_revealing_date': self.today - timedelta(days=1),
            'reason_html': REASON,
            'resignation_type': 'resigned',
        }
        vals.update(extra)
        return vals

    def _confirmed(self, **extra):
        """Request filed by the employee and confirmed with the base Confirm button."""
        vals = self._vals(**extra)
        vals.pop('resignation_type')
        res = self.env['hr.resignation'].with_user(self.user).create(vals)
        res.with_user(self.user).action_confirm_resignation()
        if 'resignation_type' in extra:
            res.sudo().resignation_type = extra['resignation_type']
        # back to the test environment: the archived user can no longer read the request after approval
        return res.with_env(self.env)

    def _handed_over(self, **extra):
        res = self._confirmed(**extra)
        res.with_user(self.officer).manager_approved_resignation()
        res.with_user(self.user).complete_hand_over_resignation()
        return res
