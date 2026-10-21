# -*- coding: utf-8 -*-
from odoo import models


class HrEmployeeDeparture(models.Model):
    _inherit = 'hr.employee.departure'

    def _cleanup_employee_departure_leaves(self):
        # An approved resignation refuses the future leaves itself: skip the hr_holidays cleanup (cancel/delete)
        if self.env.context.get('bhs_resignation_keep_leaves'):
            return
        return super()._cleanup_employee_departure_leaves()
