# -*- coding: utf-8 -*-
"""Paid time off figures stored on the request.

With a validated paid time off allocation of the current year:
- used_allocation = validated paid time off of the year (number_of_days), plus the week days inside the year of the
  leaves crossing the 1st of January / the 31st of December;
- worked_month = month of the last day - month of the allocation start, + 1 when more than half of the week days
  of that month are worked;
- revoked_allocation = used - worked when positive.
Odoo 20: the paid time off type is the work entry type hr_work_entry.generic_work_entry_type_legal_leave
(hr_holidays.leave_type_paid_time_off and hr.leave.holiday_status_id are gone).
"""
from datetime import date

from freezegun import freeze_time

from odoo.tests import tagged

from .common import ResignationCommon

FROZEN = '2026-06-10 10:00:00'


@tagged('post_install', '-at_install')
class TestLeaveFigures(ResignationCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pto = cls.env.ref('hr_work_entry.generic_work_entry_type_legal_leave')

    def _allocation(self, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31), work_entry_type=None, employee=None):
        allocation = self.env['hr.leave.allocation'].create({
            'name': 'RSG PTO 2026', 'employee_id': (employee or self.employee).id,
            'work_entry_type_id': (work_entry_type or self.pto).id,
            'date_from': date_from, 'date_to': date_to, 'number_of_days': 12,
        })
        if allocation.state != 'validate':
            allocation.sudo().action_approve()
        self.assertEqual(allocation.state, 'validate')
        return allocation

    def _leave(self, date_from, date_to, work_entry_type=None, employee=None):
        # leaves crossing the year bounds are not covered by one allocation: skip the allocation checks
        Leave = self.env['hr.leave'].sudo().with_context(
            leave_fast_create=True, skip_check_validity=True, skip_allocation_check=True)
        leave = Leave.create({
            'name': 'RSG PTO', 'employee_id': (employee or self.employee).id,
            'work_entry_type_id': (work_entry_type or self.pto).id,
            'request_date_from': date_from, 'request_date_to': date_to,
        })
        leave.write({'state': 'validate'})
        return leave

    def _create(self, last_day):
        return self.env['hr.resignation'].with_user(self.user).create(self._vals(expected_revealing_date=last_day))

    @freeze_time(FROZEN)
    def test_figures_with_revocation(self):
        self._allocation()
        leaves = self._leave(date(2026, 2, 2), date(2026, 2, 6)) | self._leave(date(2026, 3, 2), date(2026, 3, 6))
        # crossing the year bounds: only the week days inside 2026 count (Thu 1 + Fri 2 Jan, Wed 30 + Thu 31 Dec)
        self._leave(date(2025, 12, 29), date(2026, 1, 2))
        self._leave(date(2026, 12, 30), date(2027, 1, 1))
        # another type does not count
        other_type = self.env['hr.work.entry.type'].create({'name': 'RSG Other', 'code': 'RSGOTHER'})
        self._leave(date(2026, 4, 6), date(2026, 4, 7), work_entry_type=other_type)
        used = sum(leaves.mapped('number_of_days')) + 4
        # June 2026: 22 week days, 8 worked up to the 10th (< half) -> 6 - 1 = 5 months
        res = self._create(date(2026, 6, 10))
        self.assertEqual(res.used_allocation, used)
        self.assertEqual(res.worked_month, 5)
        self.assertEqual(res.revoked_allocation, used - 5)
        # later last day in the same month: 18 of 22 week days worked (> half) -> 6 months
        res.with_user(self.user).write({'expected_revealing_date': '2026-06-24'})
        self.assertEqual(res.expected_revealing_date, date(2026, 6, 24))
        self.assertEqual(res.worked_month, 6)
        self.assertEqual(res.revoked_allocation, used - 6)

    @freeze_time(FROZEN)
    def test_figures_without_revocation(self):
        self._allocation()
        res = self._create(date(2026, 6, 10))
        self.assertEqual((res.worked_month, res.used_allocation, res.revoked_allocation), (5, 0, 0))
        res.with_user(self.user).write({'expected_revealing_date': '2026-06-24'})
        self.assertEqual((res.worked_month, res.revoked_allocation), (6, 0))

    @freeze_time(FROZEN)
    def test_figures_last_day_in_december(self):
        """A last day in December is counted without overflowing the month."""
        self._allocation()
        # December 2026: 23 week days, 11 worked up to the 15th (< half) -> 12 - 1 = 11 months
        res = self._create(date(2026, 12, 15))
        self.assertEqual((res.worked_month, res.used_allocation, res.revoked_allocation), (11, 0, 0))
        # 20 of 23 week days worked up to the 28th (> half) -> 12 months
        res.with_user(self.user).write({'expected_revealing_date': '2026-12-28'})
        self.assertEqual((res.worked_month, res.revoked_allocation), (12, 0))

    @freeze_time(FROZEN)
    def test_figures_follow_the_new_last_day_month(self):
        """A new last day in another month is counted on its own month, not on the month of the previous last day."""
        self._allocation()
        leaves = self._leave(date(2026, 2, 2), date(2026, 2, 6)) | self._leave(date(2026, 3, 2), date(2026, 3, 6))
        used = sum(leaves.mapped('number_of_days'))
        res = self._create(date(2026, 6, 10))
        self.assertEqual(res.worked_month, 5)
        # March 2026: 22 week days, 2 worked up to the 3rd (< half) -> 3 - 1 = 2 months
        res.with_user(self.user).write({'expected_revealing_date': '2026-03-03'})
        self.assertEqual(res.worked_month, 2)
        self.assertEqual(res.revoked_allocation, used - 2)

    @freeze_time(FROZEN)
    def test_no_figures_without_allocation_of_the_year(self):
        self._allocation(date_from=date(2025, 1, 1), date_to=date(2025, 12, 31))
        self._leave(date(2026, 2, 2), date(2026, 2, 6))
        res = self._create(date(2026, 6, 10))
        self.assertEqual((res.worked_month, res.used_allocation, res.revoked_allocation), (0, 0, 0))
        res.with_user(self.user).write({'expected_revealing_date': '2026-06-24'})
        self.assertEqual(res.worked_month, 0)

    @freeze_time(FROZEN)
    def test_figures_batch_create(self):
        """Several requests created in one call: each request gets the figures of its own employee."""
        self._allocation()
        self._allocation(employee=self.other)
        leaves = self._leave(date(2026, 2, 2), date(2026, 2, 6)) | self._leave(date(2026, 3, 2), date(2026, 3, 6))
        used = sum(leaves.mapped('number_of_days'))
        res, other_res = self.env['hr.resignation'].create([
            self._vals(expected_revealing_date=date(2026, 6, 10)),
            self._vals(employee_id=self.other.id, expected_revealing_date=date(2026, 6, 24)),
        ])
        self.assertEqual((res.worked_month, res.used_allocation, res.revoked_allocation), (5, used, used - 5))
        self.assertEqual((other_res.worked_month, other_res.used_allocation, other_res.revoked_allocation), (6, 0, 0))

    @freeze_time(FROZEN)
    def test_figures_two_allocations_count_from_the_earliest_start(self):
        """Two validated paid time off allocations in the year: the worked months count from the earliest start."""
        self._allocation(date_from=date(2026, 3, 1))
        self._allocation()
        res = self._create(date(2026, 6, 10))
        # June 2026: less than half worked -> 6 - 1 = 5 months from January
        self.assertEqual(res.worked_month, 5)
        res.with_user(self.user).write({'expected_revealing_date': '2026-06-24'})
        self.assertEqual(res.worked_month, 6)

    @freeze_time(FROZEN)
    def test_figures_write_several_requests(self):
        """A new last day written on several requests: each keeps the figures of its own employee, also one without
        allocation placed after one with an allocation."""
        third = self._employee('RSG Third')
        self._allocation()
        self._allocation(employee=self.other)
        leaves = self._leave(date(2026, 2, 2), date(2026, 2, 6)) | self._leave(date(2026, 3, 2), date(2026, 3, 6))
        used = sum(leaves.mapped('number_of_days'))
        other_leaves = (self._leave(date(2026, 4, 6), date(2026, 4, 10), employee=self.other)
                        | self._leave(date(2026, 5, 4), date(2026, 5, 8), employee=self.other)
                        | self._leave(date(2026, 5, 11), date(2026, 5, 12), employee=self.other))
        other_used = sum(other_leaves.mapped('number_of_days'))
        self.assertNotEqual(used, other_used)
        requests = self.env['hr.resignation'].create([
            self._vals(expected_revealing_date=date(2026, 6, 10)),
            self._vals(employee_id=third.id, expected_revealing_date=date(2026, 6, 10)),
            self._vals(employee_id=self.other.id, expected_revealing_date=date(2026, 6, 10)),
        ])
        res, third_res, other_res = requests
        requests.write({'expected_revealing_date': '2026-06-24'})
        self.assertEqual((res.worked_month, res.used_allocation, res.revoked_allocation), (6, used, used - 6))
        self.assertEqual((third_res.worked_month, third_res.used_allocation, third_res.revoked_allocation),
                         (0, 0, 0))
        self.assertEqual((other_res.worked_month, other_res.used_allocation, other_res.revoked_allocation),
                         (6, other_used, other_used - 6))
