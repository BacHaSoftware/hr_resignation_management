# -*- coding: utf-8 -*-
"""Resignation workflow.

draft -> Confirm (base) -> Manager Approve (HR officer: employee checklist from templates, mail to the employee)
-> Complete Hand over (employee checklist done, manager + HR checklist added once, mail to the manager)
-> Approve (HR manager: when the last day is reached the future leaves are refused and the employee is archived
with its user; otherwise the daily cron does it on that date).
Odoo 20: the archive goes through an hr.employee.departure record (`_register_departure`).
"""
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from freezegun import freeze_time

from odoo.addons.mail.tests.common import MockEmail
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged

from .common import REASON, ResignationCommon


@tagged('post_install', '-at_install')
class TestResignationRequest(ResignationCommon):

    def test_create_requires_reason(self):
        with self.assertRaisesRegex(ValidationError, 'Reason is required!'):
            self.env['hr.resignation'].with_user(self.user).create(self._vals(reason_html=False))
        res = self.env['hr.resignation'].with_user(self.user).create(self._vals())
        self.assertTrue(res.name.startswith('RES'))
        self.assertEqual(res.state, 'draft')
        self.assertEqual(res.resignation_type, 'resigned')
        self.assertEqual(str(res.reason_html), REASON)
        self.assertEqual(res.employee_id, self.employee)
        # no paid time off allocation this year: no leave figures
        self.assertEqual((res.worked_month, res.used_allocation, res.revoked_allocation), (0, 0, 0))

    def test_resignation_url(self):
        self.env['ir.config_parameter'].sudo().set_str('web.base.url', 'https://rsg.example.com')
        res = self.env['hr.resignation'].create(self._vals())
        self.assertEqual(res.get_resignation_url(), f'https://rsg.example.com/odoo/resignation/{res.id}')

    def test_confirm(self):
        res = self._confirmed()
        self.assertEqual(res.state, 'confirm')
        self.assertTrue(res.resign_confirm_date)
        # joining date of hr_employee_updation 20 = start of the first contract (fixture: 2020-01-01)
        self.assertEqual(res.joined_date, date(2020, 1, 1))

    def test_confirm_date_checks(self):
        res = self.env['hr.resignation'].with_user(self.user).create(self._vals())
        res.sudo().joined_date = self.today
        with self.assertRaisesRegex(ValidationError, 'anterior to Joining date'):
            res.with_user(self.user).action_confirm_resignation()
        res.sudo().joined_date = False
        with self.assertRaisesRegex(ValidationError, 'Joining Date'):
            res.with_user(self.user).action_confirm_resignation()
        self.assertEqual(res.state, 'draft')

    def test_flow_date_checks(self):
        res = self._confirmed()
        res.joined_date = False
        for method in ('manager_approved_resignation', 'complete_hand_over_resignation', 'confirm_resignation'):
            with self.subTest(method=method), self.assertRaisesRegex(ValidationError, 'joining date'):
                getattr(res, method)()
        res.joined_date = res.expected_revealing_date
        for method in ('manager_approved_resignation', 'complete_hand_over_resignation', 'confirm_resignation'):
            with self.subTest(method=method), self.assertRaisesRegex(ValidationError, 'anterior to Joining date'):
                getattr(res, method)()

    def test_approve_requires_dates(self):
        res = self.env['hr.resignation'].create(self._vals())
        with self.assertRaisesRegex(ValidationError, 'valid dates'):
            res.with_user(self.manager).approve_resignation()

    def test_existing_request_onchange(self):
        """check_request_existence (onchange employee): refuses a second request while one is confirmed, fills the
        contract of the employee otherwise."""
        res = self.env['hr.resignation'].new({'employee_id': self.other.id})
        res.check_request_existence()
        self.assertEqual(res.employee_contract, self.other.version_id.display_name)
        self._confirmed()
        res = self.env['hr.resignation'].new({'employee_id': self.employee.id})
        with self.assertRaisesRegex(ValidationError, 'confirmed or approved'):
            res.check_request_existence()
        empty = self.env['hr.resignation'].new({})
        empty.check_request_existence()
        self.assertFalse(empty.employee_contract)


@tagged('post_install', '-at_install')
class TestResignationWorkflow(ResignationCommon, MockEmail):

    def _mails_to(self, email):
        return self._new_mails.filtered(lambda m: m.email_to == email)

    def test_confirm_resignation_mails_manager(self):
        """confirm_resignation (not the Confirm button of the form) still sends the request to the manager."""
        res = self.env['hr.resignation'].with_user(self.user).create(self._vals())
        with self.mock_mail_gateway():
            res.confirm_resignation()
        self.assertEqual(res.state, 'confirm')
        mail = self._mails_to(self.boss.work_email)
        self.assertEqual(len(mail), 1)
        self.assertIn('Resignation request of RSG Employee', mail.subject)

    def test_confirm_button_mails_manager(self):
        """The Confirm button of the form (base action_confirm_resignation) sends the request to the manager."""
        res = self.env['hr.resignation'].with_user(self.user).create(self._vals())
        with self.mock_mail_gateway():
            res.with_user(self.user).action_confirm_resignation()
        self.assertEqual(res.state, 'confirm')
        mail = self._mails_to(self.boss.work_email)
        self.assertEqual(len(mail), 1)
        self.assertIn('Resignation request of RSG Employee', mail.subject)
        # a second Confirm (HR officer) does not mail the manager again
        with self.mock_mail_gateway():
            res.with_user(self.officer).action_confirm_resignation()
        self.assertFalse(self._mails_to(self.boss.work_email))

    def test_confirm_button_without_manager_email(self):
        """No manager (or no manager work email): Confirm still succeeds, no mail is sent."""
        self.boss.work_email = False
        res = self.env['hr.resignation'].with_user(self.user).create(self._vals())
        with self.mock_mail_gateway():
            res.with_user(self.user).action_confirm_resignation()
        self.assertEqual(res.state, 'confirm')
        # RSG Other has no manager
        other = self.env['hr.resignation'].with_user(self.other_user).create(self._vals(employee_id=self.other.id))
        with self.mock_mail_gateway():
            other.with_user(self.other_user).action_confirm_resignation()
        self.assertEqual(other.state, 'confirm')
        self.assertFalse(self._new_mails.filtered(lambda m: 'Resignation request' in (m.subject or '')))

    def test_manager_approve_employee_checklist(self):
        res = self._confirmed()
        with self.mock_mail_gateway():
            res.with_user(self.officer).manager_approved_resignation()
        self.assertEqual(res.state, 'manager_approved')
        self.assertEqual(sorted(res.checklist_ids.mapped('name')),
                         ['RSG emp: handover docs', 'RSG emp: return laptop'])
        self.assertEqual(set(res.checklist_ids.mapped('type')), {'employee'})
        mail = self._mails_to(self.employee.work_email)
        self.assertEqual(len(mail), 1)
        self.assertIn('approved', mail.subject)
        self.assertIn(res.get_resignation_url(), str(mail.body_html))

    def test_manager_approve_employee_type_employee(self):
        self.employee.employee_type_id = self.env.ref('hr.contract_type_employee')
        res = self._confirmed()
        res.with_user(self.officer).manager_approved_resignation()
        self.assertEqual(sorted(res.checklist_ids.mapped('name')),
                         ['RSG emp: handover docs', 'RSG emp: return laptop'])

    def test_manager_approve_intern_by_hr_officer(self):
        """employee_type_id is HR manager data on 20: the HR officer must still get the trainee checklist."""
        self.employee.employee_type_id = self.env.ref('hr.contract_type_intern')
        res = self._confirmed()
        res.with_user(self.officer).manager_approved_resignation()
        self.assertEqual(res.state, 'manager_approved')
        self.assertEqual(res.checklist_ids.mapped('name'), ['RSG trainee: return badge'])

    def test_manager_approve_refused_to_plain_user(self):
        res = self._confirmed()
        with self.assertRaises(AccessError):
            res.with_user(self.user).manager_approved_resignation()
        self.assertEqual(res.state, 'confirm')
        self.assertFalse(res.checklist_ids)

    def test_hand_over_adds_manager_and_hr_items_once(self):
        res = self._confirmed()
        res.with_user(self.officer).manager_approved_resignation()
        with self.mock_mail_gateway():
            res.with_user(self.user).complete_hand_over_resignation()
        self.assertEqual(res.state, 'handed_over')
        self.assertTrue(res.complete_employee_checklist)
        employee_items = res.checklist_ids.filtered(lambda c: c.type == 'employee')
        self.assertEqual(len(employee_items), 2)
        self.assertTrue(all(employee_items.mapped('check_box')))
        other_items = res.checklist_ids - employee_items
        self.assertEqual(sorted(other_items.mapped('name')),
                         ['RSG emp: HR exit interview', 'RSG emp: manager review'])
        self.assertFalse(any(other_items.mapped('check_box')))
        mail = self._mails_to(self.boss.work_email)
        self.assertEqual(len(mail), 1)
        self.assertIn('handover', mail.subject)
        # a second hand over does not add the manager/HR items again
        res.with_user(self.user).complete_hand_over_resignation()
        self.assertEqual(len(res.checklist_ids), 4)

    def test_reject_mails_employee(self):
        res = self._confirmed()
        with self.mock_mail_gateway():
            res.with_user(self.officer).action_reject_resignation()
        self.assertEqual(res.state, 'cancel')
        mail = self._mails_to(self.employee.work_email)
        self.assertEqual(len(mail), 1)
        self.assertIn('rejected', mail.subject)

    def test_cancel_resignation_mails_employee(self):
        res = self._confirmed()
        with self.mock_mail_gateway():
            res.with_user(self.officer).cancel_resignation()
        self.assertEqual(res.state, 'cancel')
        self.assertEqual(len(self._mails_to(self.employee.work_email)), 1)

    def test_checklist_rights(self):
        res = self._handed_over()
        manager_item = res.checklist_ids.filtered(lambda c: c.type == 'manager')
        hr_item = res.checklist_ids.filtered(lambda c: c.type == 'hr')
        with self.assertRaisesRegex(ValidationError, 'checklist for manager'):
            manager_item.with_user(self.user).write({'check_box': True})
        with self.assertRaisesRegex(ValidationError, 'checklist for HR manager'):
            hr_item.with_user(self.officer).write({'check_box': True})
        manager_item.with_user(self.officer).write({'check_box': True})
        hr_item.with_user(self.manager).write({'check_box': True})
        self.assertTrue(manager_item.check_box and hr_item.check_box)

    def test_checklist_onchanges(self):
        res = self._handed_over()
        res.checklist_ids.filtered(lambda c: c.type == 'employee').check_box = False
        res.complete_employee_checklist = True
        res.onchange_complete_employee_checklist()
        self.assertTrue(all(res.checklist_ids.filtered(lambda c: c.type == 'employee').mapped('check_box')))
        res.complete_employee_checklist = False
        res.onchange_complete_employee_checklist()
        self.assertFalse(any(res.checklist_ids.filtered(lambda c: c.type == 'employee').mapped('check_box')))
        res.complete_checklist = True
        res.onchange_complete_checklist()
        self.assertTrue(all(res.checklist_ids.mapped('check_box')))
        res.confirm_complete_manager_checklist()
        self.assertTrue(res.complete_manager_checklist)


@tagged('post_install', '-at_install')
class TestResignationApproval(ResignationCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Time off: approving refuses the future leaves -> the approver also manages time off
        cls.manager.group_ids += cls.env.ref('hr_holidays.group_hr_holidays_user')
        cls.work_entry_type = cls.env['hr.work.entry.type'].create({
            'name': 'RSG Paid', 'code': 'RSGPAID', 'requires_allocation': True,
            'leave_validation_type': 'hr', 'allocation_validation_type': 'no_validation',
        })

    def _leave_and_allocation(self):
        allocation = self.env['hr.leave.allocation'].create({
            'employee_id': self.employee.id, 'work_entry_type_id': self.work_entry_type.id,
            'date_from': self.today - timedelta(days=200), 'number_of_days': 20,
        })
        if allocation.state != 'validate':
            allocation.action_approve()
        start = self.today + timedelta(days=20)
        leave = self.env['hr.leave'].with_context(leave_fast_create=True).create({
            'name': 'RSG future leave', 'employee_id': self.employee.id,
            'work_entry_type_id': self.work_entry_type.id,
            'request_date_from': start, 'request_date_to': start + timedelta(days=1),
        })
        leave._action_validate()
        return leave, allocation

    def _approve(self, res):
        res.with_user(self.manager).approve_resignation()

    def test_approve_last_day_passed(self):
        leave, allocation = self._leave_and_allocation()
        self.assertEqual((leave.state, allocation.state), ('validate', 'validate'))
        start = self.today + timedelta(days=30)
        pending = self.env['hr.leave'].with_context(leave_fast_create=True).create({
            'name': 'RSG pending leave', 'employee_id': self.employee.id,
            'work_entry_type_id': self.work_entry_type.id,
            'request_date_from': start, 'request_date_to': start + timedelta(days=1),
        })
        self.assertEqual(pending.state, 'confirm')
        self.assertFalse(self.employee.sudo().version_id.contract_date_end)
        res = self._handed_over()
        self._approve(res)
        self.assertEqual(res.state, 'approved')
        self.assertTrue(res.complete_checklist)
        self.assertTrue(all(res.checklist_ids.mapped('check_box')))
        self.assertEqual(res.approved_revealing_date, res.expected_revealing_date)
        self.assertFalse(self.employee.active)
        self.assertFalse(self.user.active)
        departure = self.employee.departure_id
        self.assertTrue(departure.apply_date)
        self.assertEqual(departure.departure_date, res.approved_revealing_date)
        self.assertEqual(departure.departure_reason_id, self.env.ref('hr.departure_resigned'))
        self.assertEqual(self.employee.departure_reason_id.departure_code, 'resigned')
        # future leaves refused (the hr_holidays departure cleanup would cancel/delete them)
        self.assertEqual(leave.exists().state, 'refuse')
        self.assertTrue(pending.exists())
        self.assertEqual(pending.state, 'refuse')
        self.assertEqual(self.employee.sudo().version_id.contract_date_end, res.approved_revealing_date)

    def test_plain_departure_keeps_core_leave_cleanup(self):
        """A departure registered outside the resignation keeps the hr_holidays cleanup (future leave cancelled)."""
        leave, _allocation = self._leave_and_allocation()
        self.env['hr.employee.departure'].create({
            'employee_id': self.employee.id, 'departure_reason_id': self.env.ref('hr.departure_resigned').id,
            'dismissal_date': self.today - timedelta(days=1),
        }).action_register()
        self.assertEqual(leave.state, 'cancel')

    def test_approve_departure_reason_by_type(self):
        res = self._handed_over(resignation_type='retired')
        self._approve(res)
        self.assertEqual(self.employee.departure_reason_id, self.env.ref('hr.departure_retired'))
        res.with_user(self.manager).action_reset_to_draft()
        res2 = self._handed_over(resignation_type='fired')
        self._approve(res2)
        self.assertEqual(self.employee.departure_reason_id, self.env.ref('hr.departure_fired'))

    def test_approve_by_hr_manager_without_time_off_rights(self):
        """HR manager only (no Time Off rights), employee without leaves: approving works."""
        self.manager.group_ids -= self.env.ref('hr_holidays.group_hr_holidays_user')
        self.assertFalse(self.manager.has_group('hr_holidays.group_hr_holidays_user'))
        res = self._handed_over()
        self._approve(res)
        self.assertEqual(res.state, 'approved')
        self.assertFalse(self.employee.active)

    def test_approve_refused_to_plain_user(self):
        """Last day in the future: only the access check can stop the approval."""
        res = self._handed_over(expected_revealing_date=self.today + timedelta(days=5))
        with self.assertRaises(AccessError):
            res.with_user(self.user).approve_resignation()
        self.assertEqual(res.state, 'handed_over')
        self.assertFalse(res.approved_revealing_date)
        self.assertTrue(self.employee.active)

    def test_approve_refused_to_hr_officer(self):
        """Final approval is for the HR manager only (the HR officer approves the manager step)."""
        res = self._handed_over(expected_revealing_date=self.today + timedelta(days=5))
        with self.assertRaises(AccessError):
            res.with_user(self.officer).approve_resignation()
        self.assertEqual(res.state, 'handed_over')

    def test_approve_several_requests(self):
        """Approving several requests at once: each request uses the contract of its own employee."""
        last_day = self.today + timedelta(days=5)
        res = self._handed_over(expected_revealing_date=last_day)
        other_res = self.env['hr.resignation'].with_user(self.other_user).create(
            {'employee_id': self.other.id, 'expected_revealing_date': last_day, 'reason_html': REASON})
        other_res.with_user(self.other_user).action_confirm_resignation()
        other_res = other_res.with_env(self.env)
        other_res.with_user(self.officer).manager_approved_resignation()
        other_res.with_user(self.other_user).complete_hand_over_resignation()
        self._approve(res | other_res)
        self.assertEqual((res | other_res).mapped('state'), ['approved', 'approved'])

    def test_approve_contract_name(self):
        """Odoo 20: employee_contract is the contract template of the running version. Last day in the future: the
        contract still runs when the request is approved."""
        template = self.env['hr.version'].create({'name': 'RSG Template', 'date_version': '2020-01-01'})
        self.employee.version_id.contract_template_id = template
        res = self._handed_over(expected_revealing_date=self.today + timedelta(days=5))
        res.employee_contract = False
        self._approve(res)
        self.assertEqual(res.employee_contract, 'RSG Template')

    def test_approve_last_day_future_then_cron(self):
        last_day = self.today + timedelta(days=5)
        res = self._handed_over(expected_revealing_date=last_day)
        self._approve(res)
        self.assertEqual(res.state, 'approved')
        self.assertEqual(res.approved_revealing_date, last_day)
        self.assertTrue(self.employee.active)
        self.assertTrue(self.user.active)
        self.assertFalse(self.employee.departure_id)
        # the daily cron of hr_resignation, run as its user (method_direct_trigger uses a new cursor, which does not
        # see the data of the test transaction)
        cron = self.env.ref('hr_resignation.ir_cron_employee_resignation')
        self.assertEqual(cron.code.strip(), 'model.update_employee_status()')
        cron_action = cron.ir_actions_server_id.with_user(cron.user_id)
        cron_action.run()
        self.assertTrue(self.employee.active)
        with freeze_time(datetime.combine(last_day, time(8))):
            cron_action.run()
        self.assertFalse(self.employee.active)
        self.assertFalse(self.user.active)
        self.assertEqual(self.employee.departure_id.departure_date, last_day)
        self.assertEqual(self.employee.sudo().version_id.contract_date_end, last_day)

    def test_cron_before_utc_midnight_in_local_time_zone(self):
        """The last day is reached on the UTC date (core hr.employee.departure checks the UTC date). At 05:00 in
        Asia/Ho_Chi_Minh (22:00 UTC the day before) the cron neither fails nor applies the departure yet."""
        last_day = self.today + timedelta(days=5)
        res = self._handed_over(expected_revealing_date=last_day)
        self._approve(res)
        cron = self.env.ref('hr_resignation.ir_cron_employee_resignation')
        cron.user_id.sudo().tz = 'Asia/Ho_Chi_Minh'
        cron_action = cron.ir_actions_server_id.with_user(cron.user_id)
        with freeze_time(datetime.combine(last_day - timedelta(days=1), time(22))):
            cron_action.run()
        self.assertTrue(self.employee.active)
        self.assertFalse(self.employee.departure_id)
        with freeze_time(datetime.combine(last_day, time(1))):
            cron_action.run()
        self.assertFalse(self.employee.active)
        self.assertEqual(self.employee.departure_id.departure_date, last_day)

    def test_approve_before_utc_midnight_in_local_time_zone(self):
        """Approving on the last day at 05:00 local time (UTC still the day before) approves without error; the
        departure is applied by the cron once the UTC date reaches the last day."""
        last_day = self.today + timedelta(days=5)
        res = self._handed_over(expected_revealing_date=last_day)
        self.manager.tz = 'Asia/Ho_Chi_Minh'
        with freeze_time(datetime.combine(last_day - timedelta(days=1), time(22))):
            self._approve(res)
        self.assertEqual(res.state, 'approved')
        self.assertTrue(self.employee.active)
        with freeze_time(datetime.combine(last_day, time(1))):
            self.env['hr.resignation'].with_user(self.manager).update_employee_status()
        self.assertFalse(self.employee.active)

    def test_reset_to_draft(self):
        res = self._handed_over(resignation_type='retired')
        self._approve(res)
        self.assertFalse(self.employee.active)
        self.assertEqual(self.employee.sudo().version_id.contract_date_end, res.approved_revealing_date)
        self.employee.write({'resigned': True, 'fired': True, 'retired': True})
        res.with_user(self.manager).action_reset_to_draft()
        self.assertFalse(self.employee.sudo().version_id.contract_date_end)
        self.assertEqual(res.state, 'draft')
        self.assertTrue(self.employee.active)
        self.assertFalse(self.employee.resigned or self.employee.fired or self.employee.retired)
        self.assertFalse(res.checklist_ids)
        self.assertFalse(self.employee.departure_id)
        self.assertEqual(self.employee.user_id, self.user)
        self.assertTrue(self.user.active)


@tagged('post_install', '-at_install')
class TestResignationGuards(ResignationCommon):
    """Each step is refused to the users who do not own it, and only from its own state: a plain employee can read
    and write its own request (hr_resignation access), so the buttons alone do not protect the flow."""

    def _future(self):
        return {'expected_revealing_date': self.today + timedelta(days=5)}

    def test_plain_user_cannot_approve_own_confirmed_request(self):
        res = self._confirmed(**self._future())
        for method in ('approve_resignation', 'action_approve_resignation'):
            with self.subTest(method=method), self.assertRaises(AccessError):
                getattr(res.with_user(self.user), method)()
        self.assertEqual(res.state, 'confirm')
        self.assertFalse(res.approved_revealing_date)
        self.assertTrue(self.employee.active)

    def test_plain_user_cannot_reject_or_reset(self):
        res = self._confirmed(**self._future())
        for method in ('action_reject_resignation', 'cancel_resignation', 'action_cancel_resignation',
                       'action_reset_to_draft', 'confirm_complete_manager_checklist'):
            with self.subTest(method=method), self.assertRaises(AccessError):
                getattr(res.with_user(self.user), method)()
        self.assertEqual(res.state, 'confirm')
        self.assertFalse(res.complete_manager_checklist)

    def test_plain_user_cannot_write_flow_fields(self):
        res = self._confirmed(**self._future())
        for vals in ({'state': 'approved'}, {'approved_revealing_date': self.today},
                     {'resign_confirm_date': '2020-01-01 00:00:00', 'state': 'handed_over'}):
            with self.subTest(vals=vals), self.assertRaises(AccessError):
                res.with_user(self.user).write(vals)
        self.assertEqual(res.state, 'confirm')
        with self.assertRaises(AccessError):
            self.env['hr.resignation'].with_user(self.other_user).create(
                {'employee_id': self.other.id, 'reason_html': REASON, 'state': 'approved',
                 'expected_revealing_date': self.today + timedelta(days=5)})

    def test_steps_follow_the_states(self):
        res = self._confirmed(**self._future())
        with self.assertRaisesRegex(UserError, 'at this step'):
            res.with_user(self.manager).approve_resignation()
        with self.assertRaisesRegex(UserError, 'at this step'):
            res.with_user(self.user).complete_hand_over_resignation()
        self.assertEqual(res.state, 'confirm')
        res.with_user(self.officer).manager_approved_resignation()
        with self.assertRaisesRegex(UserError, 'at this step'):
            res.with_user(self.officer).manager_approved_resignation()
        self.assertEqual(len(res.checklist_ids), 2)
        res.with_user(self.user).complete_hand_over_resignation()
        res.with_user(self.manager).approve_resignation()
        self.assertEqual(res.state, 'approved')
        # an approved request cannot be handed over or confirmed again by the employee
        for method in ('complete_hand_over_resignation', 'action_confirm_resignation', 'confirm_resignation'):
            with self.subTest(method=method), self.assertRaises((AccessError, UserError)):
                getattr(res.with_user(self.user), method)()
        self.assertEqual(res.state, 'approved')

    def test_deps_approve_follows_the_bhs_flow(self):
        """The base Approve button is replaced in the form; called directly it goes through the HR approval."""
        res = self._handed_over(**self._future())
        res.with_user(self.manager).action_approve_resignation()
        self.assertEqual(res.state, 'approved')
        self.assertTrue(res.complete_checklist)

    def test_employee_and_hr_steps_still_allowed(self):
        res = self.env['hr.resignation'].with_user(self.user).create(self._vals(**self._future()))
        res.with_user(self.user).action_cancel_resignation()
        self.assertEqual(res.state, 'cancel')
        res = self._confirmed(**self._future())
        res.with_user(self.officer).action_reject_resignation()
        self.assertEqual(res.state, 'cancel')
        res.with_user(self.officer).action_reset_to_draft()
        self.assertEqual(res.state, 'draft')


LOGGER = 'odoo.addons.bhs_resignation.models.hr_resignation'
# Friday 2026-07-10: the last day of the requests below
LAST_DAY = date(2026, 7, 10)
FROZEN_LAST_DAY = '2026-07-10 10:00:00'


@tagged('post_install', '-at_install')
class TestFutureLeaves(ResignationCommon):
    """The leaves after the last day are refused when the request is applied (approval on or after the last day, or
    the daily cron), by an HR manager who may have no Time Off rights. A leave crossing the last day is cut: the part
    up to the last day keeps its state, the part after it is refused. Set to Draft brings the refused leaves back to
    their previous state."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # the approver is an HR manager without Time Off rights
        assert not cls.manager.has_group('hr_holidays.group_hr_holidays_user')
        cls.work_entry_type = cls.env['hr.work.entry.type'].create({
            'name': 'RSG Leave', 'code': 'RSGLEAVE', 'requires_allocation': False, 'leave_validation_type': 'hr',
        })
        cls.both_type = cls.env['hr.work.entry.type'].create({
            'name': 'RSG Leave Both', 'code': 'RSGLEAVE2', 'requires_allocation': False,
            'leave_validation_type': 'both',
        })

    def _leave(self, date_from, date_to, state, work_entry_type=None):
        leave = self.env['hr.leave'].with_context(leave_fast_create=True).create({
            'name': f'RSG leave {date_from}', 'employee_id': self.employee.id,
            'work_entry_type_id': (work_entry_type or self.work_entry_type).id,
            'request_date_from': date_from, 'request_date_to': date_to,
        })
        if state == 'validate':
            leave._action_validate()
        elif state != 'confirm':
            leave.with_context(leave_fast_create=True).write({'state': state})
        self.assertEqual(leave.state, state)
        return leave

    def _leaves(self):
        return {
            'before': self._leave(date(2026, 7, 6), date(2026, 7, 7), 'validate'),
            # Thursday 9th - Tuesday 14th, crosses the last day
            'straddle': self._leave(date(2026, 7, 9), date(2026, 7, 14), 'validate'),
            'confirm': self._leave(date(2026, 7, 15), date(2026, 7, 15), 'confirm'),
            'validate1': self._leave(date(2026, 7, 16), date(2026, 7, 16), 'validate1', self.both_type),
            'cancel': self._leave(date(2026, 7, 17), date(2026, 7, 17), 'cancel'),
        }

    def _tail(self, straddle):
        return self.env['hr.leave'].search([
            ('employee_id', '=', self.employee.id), ('request_date_from', '=', date(2026, 7, 11)),
            ('id', '!=', straddle.id)])

    def _approved(self):
        res = self._handed_over(expected_revealing_date=LAST_DAY)
        res.with_user(self.manager).approve_resignation()
        self.assertEqual(res.state, 'approved')
        self.assertFalse(self.employee.active)
        return res

    @freeze_time(FROZEN_LAST_DAY)
    def test_approval_without_time_off_rights_refuses_the_leaves_after_the_last_day(self):
        leaves = self._leaves()
        res = self._approved()
        straddle = leaves['straddle']
        tail = self._tail(straddle)
        # the part up to the last day keeps its state, the part from the day after is refused
        self.assertEqual((straddle.request_date_from, straddle.request_date_to, straddle.state),
                         (date(2026, 7, 9), LAST_DAY, 'validate'))
        self.assertEqual(len(tail), 1)
        self.assertEqual((tail.request_date_to, tail.state), (date(2026, 7, 14), 'refuse'))
        self.assertEqual(leaves['before'].state, 'validate')
        self.assertEqual(leaves['confirm'].state, 'refuse')
        self.assertEqual(leaves['validate1'].state, 'refuse')
        self.assertEqual(leaves['cancel'].state, 'cancel')
        self.assertEqual(res.refused_leave_states, {
            str(tail.id): 'validate', str(leaves['confirm'].id): 'confirm',
            str(leaves['validate1'].id): 'validate1'})

    @freeze_time(FROZEN_LAST_DAY)
    def test_leave_on_the_last_day_is_kept(self):
        """A leave on the last day itself is kept."""
        leave = self._leave(LAST_DAY, LAST_DAY, 'validate')
        res = self._approved()
        self.assertEqual(leave.state, 'validate')
        self.assertFalse(res.refused_leave_states)

    @freeze_time(FROZEN_LAST_DAY)
    def test_reset_restores_the_refused_leaves(self):
        # the HR responsible gets the approval activities of the leaves waiting for approval
        self.employee.hr_responsible_id = self.officer
        leaves = self._leaves()
        res = self._approved()
        tail = self._tail(leaves['straddle'])
        res.with_user(self.manager).action_reset_to_draft()
        self.assertTrue(self.employee.active)
        self.assertEqual(tail.state, 'validate')
        self.assertEqual(leaves['confirm'].state, 'confirm')
        self.assertEqual(leaves['validate1'].state, 'validate1')
        self.assertEqual(leaves['cancel'].state, 'cancel')
        # the two parts are not merged again
        self.assertEqual((leaves['straddle'].request_date_to, leaves['straddle'].state), (LAST_DAY, 'validate'))
        self.assertFalse(res.refused_leave_states)
        # the approval activities removed by the refusal are back
        for key, xmlid in (('confirm', 'hr_holidays.mail_act_leave_approval'),
                           ('validate1', 'hr_holidays.mail_act_leave_second_approval')):
            self.assertEqual(leaves[key].activity_ids.activity_type_id, self.env.ref(xmlid), key)
            self.assertEqual(leaves[key].activity_ids.user_id, self.officer, key)

    @freeze_time('2026-07-11 10:00:00')
    def test_leave_from_a_last_day_without_work(self):
        """Last day on Saturday 11th, validated leave Saturday 11th - Tuesday 14th. Odoo 20 gives a day without work
        the hours of the nearest working day, so core keeps a Saturday part (validated) and the part from Sunday is
        refused."""
        last_day = date(2026, 7, 11)
        leave = self._leave(last_day, date(2026, 7, 14), 'validate')
        res = self._handed_over(expected_revealing_date=last_day)
        res.with_user(self.manager).approve_resignation()
        self.assertEqual(res.state, 'approved')
        tail = self.env['hr.leave'].search([('employee_id', '=', self.employee.id), ('id', '!=', leave.id)])
        self.assertEqual((leave.request_date_to, leave.state), (last_day, 'validate'))
        self.assertEqual((tail.request_date_from, tail.state), (date(2026, 7, 12), 'refuse'))
        self.assertEqual(res.refused_leave_states, {str(tail.id): 'validate'})

    @freeze_time('2026-07-11 10:00:00')
    def test_leave_whose_part_up_to_the_last_day_is_not_created(self):
        """When core creates no part up to the last day (no working time in it), _split_leaves moves the original
        leave after the last day and returns no new leave: that leave itself is refused and stored."""
        last_day = date(2026, 7, 11)
        leave = self._leave(last_day, date(2026, 7, 14), 'validate')
        Leave = type(self.env['hr.leave'])

        def split_without_head(leaves, split_date_from, split_date_to=False):
            # what core does when the part before split_date_from has no duration
            leaves.with_context(leave_skip_state_check=True).write({'request_date_from': split_date_from})
            return self.env['hr.leave']

        res = self._handed_over(expected_revealing_date=last_day)
        with patch.object(Leave, '_split_leaves', split_without_head):
            res.with_user(self.manager).approve_resignation()
        self.assertEqual(res.state, 'approved')
        self.assertEqual(self.env['hr.leave'].search([('employee_id', '=', self.employee.id)]), leave)
        self.assertEqual((leave.request_date_from, leave.state), (date(2026, 7, 12), 'refuse'))
        self.assertEqual(res.refused_leave_states, {str(leave.id): 'validate'})

    @freeze_time(FROZEN_LAST_DAY)
    def test_reset_skips_deleted_and_validated_again_leaves(self):
        """After the approval a refused leave is deleted and another one is validated again by Time Off: Set to
        Draft skips both and restores the others."""
        leaves = self._leaves()
        res = self._approved()
        tail = self._tail(leaves['straddle'])
        time_off_manager = new_test_user(self.env, login='rsg_time_off',
                                         groups='base.group_user,hr_holidays.group_hr_holidays_manager')
        leaves['confirm'].with_user(time_off_manager).unlink()
        tail.write({'state': 'confirm'})
        tail._action_validate()
        self.assertEqual(tail.state, 'validate')
        res.with_user(self.manager).action_reset_to_draft()
        self.assertEqual(res.state, 'draft')
        self.assertFalse(leaves['confirm'].exists())
        self.assertEqual(tail.state, 'validate')
        self.assertEqual(leaves['validate1'].state, 'validate1')
        self.assertFalse(res.refused_leave_states)

    @freeze_time(FROZEN_LAST_DAY)
    def test_reset_skips_a_leave_that_cannot_be_restored(self):
        leaves = self._leaves()
        res = self._approved()
        tail = self._tail(leaves['straddle'])
        Leave = type(self.env['hr.leave'])
        original = Leave._action_validate

        def failing_validate(leave, *args, **kwargs):
            if leave == tail:
                raise UserError('RSG broken leave')
            return original(leave, *args, **kwargs)

        with patch.object(Leave, '_action_validate', failing_validate), \
                self.assertLogs(LOGGER, level='ERROR'):
            res.with_user(self.manager).action_reset_to_draft()
        self.assertEqual(res.state, 'draft')
        self.assertEqual(tail.state, 'refuse')
        self.assertEqual(leaves['confirm'].state, 'confirm')
        self.assertEqual(leaves['validate1'].state, 'validate1')
        # the previous state of the leave not restored is kept
        self.assertEqual(res.refused_leave_states, {str(tail.id): 'validate'})
        self.assertTrue(any(tail.display_name in str(message.body) for message in res.message_ids))

    def test_cron_skips_a_failing_request(self):
        """One request that fails in the daily cron is logged and left approved, the others are still applied."""
        last_day = self.today + timedelta(days=5)
        res = self._handed_over(expected_revealing_date=last_day)
        other_res = self.env['hr.resignation'].with_user(self.other_user).create(
            {'employee_id': self.other.id, 'expected_revealing_date': last_day, 'reason_html': REASON})
        other_res.with_user(self.other_user).action_confirm_resignation()
        other_res = other_res.with_env(self.env)
        other_res.with_user(self.officer).manager_approved_resignation()
        other_res.with_user(self.other_user).complete_hand_over_resignation()
        (res | other_res).with_user(self.manager).approve_resignation()
        Resignation = type(self.env['hr.resignation'])
        original = Resignation._update_employee_status

        def failing_update(rec):
            if rec == res:
                raise UserError('RSG broken request')
            return original(rec)

        with freeze_time(datetime.combine(last_day, time(8))), \
                patch.object(Resignation, '_update_employee_status', failing_update), \
                self.assertLogs(LOGGER, level='ERROR'):
            self.env['hr.resignation'].update_employee_status()
        self.assertEqual(res.state, 'approved')
        self.assertTrue(self.employee.active)
        self.assertFalse(self.other.active)
        self.assertEqual(self.other.departure_id.departure_date, last_day)

    def test_cron_rolls_back_a_request_failing_after_its_leaves(self):
        """The departure of one request fails after its leaves were cut and refused: the cron rolls the whole request
        back (leaves, stored states) and still applies the other due request of the run."""
        with freeze_time('2026-07-06 10:00:00'):
            leaves = self._leaves()
            res = self._handed_over(expected_revealing_date=LAST_DAY)
            other_res = self.env['hr.resignation'].with_user(self.other_user).create(
                {'employee_id': self.other.id, 'expected_revealing_date': LAST_DAY, 'reason_html': REASON})
            other_res.with_user(self.other_user).action_confirm_resignation()
            other_res = other_res.with_env(self.env)
            other_res.with_user(self.officer).manager_approved_resignation()
            other_res.with_user(self.other_user).complete_hand_over_resignation()
            (res | other_res).with_user(self.manager).approve_resignation()
        self.assertTrue(self.employee.active)
        Resignation = type(self.env['hr.resignation'])
        original = Resignation._register_departure

        def failing_departure(rec, *args, **kwargs):
            if rec == res:
                raise UserError('RSG broken departure')
            return original(rec, *args, **kwargs)

        with freeze_time(FROZEN_LAST_DAY),                 patch.object(Resignation, '_register_departure', failing_departure),                 self.assertLogs(LOGGER, level='ERROR'):
            self.env['hr.resignation'].update_employee_status()
        straddle = leaves['straddle']
        self.assertEqual((straddle.request_date_to, straddle.state), (date(2026, 7, 14), 'validate'))
        self.assertFalse(self._tail(straddle))
        self.assertEqual(leaves['confirm'].state, 'confirm')
        self.assertEqual(leaves['validate1'].state, 'validate1')
        self.assertFalse(res.refused_leave_states)
        self.assertEqual(res.state, 'approved')
        self.assertTrue(self.employee.active)
        self.assertFalse(self.other.active)
        self.assertEqual(self.other.departure_id.departure_date, LAST_DAY)
