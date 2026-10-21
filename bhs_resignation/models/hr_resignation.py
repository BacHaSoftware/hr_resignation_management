# -*- coding: utf-8 -*-
import calendar
import logging
from datetime import date, datetime, timedelta
from odoo import models, fields, api, _
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)

date_format = "%Y-%m-%d"
# Leaves refused when the request is applied (the others are already refused or cancelled)
REFUSABLE_LEAVE_STATES = ('confirm', 'validate1', 'validate')
# Odoo 20: time off types are work entry types (hr_holidays.leave_type_paid_time_off is gone)
PAID_TIME_OFF = 'hr_work_entry.generic_work_entry_type_legal_leave'
RESIGNATION_TYPE = [('resigned', 'Normal Resignation'),
                    ('fired', 'Fired by the company'),
                    ('retired', 'Retired')]
# A plain employee can write its own request (hr_resignation access): only HR or the guarded steps set these
FLOW_FIELDS = {'state', 'approved_revealing_date', 'resign_confirm_date'}
# State changes a plain employee may do on its own request: confirm/cancel the draft, hand over
EMPLOYEE_STATE_CHANGES = {('draft', 'confirm'), ('draft', 'cancel'), ('manager_approved', 'handed_over'),
                          ('handed_over', 'handed_over')}

class ResignationChecklist(models.Model):
    _name = 'hr.resignation.checklist'
    _description = 'Checklist by employee for the resignation'

    resignation_id = fields.Many2one('hr.resignation')
    check_box = fields.Boolean(default=False)
    name = fields.Char(string='Title', required=True, index=True)
    sequence = fields.Integer('Sequence', default=0)
    type = fields.Selection([('employee', 'By employee'),
                    ('manager', 'By manager'),
                    ('hr', 'By HR')], string='Type', default='employee')

    def write(self, vals):
        for checklist in self:
            if checklist.type == 'manager':
                if not self.env.user.has_group('hr.group_hr_user'):
                    raise ValidationError(_('You are not authorized to complete the checklist for manager!'))

            if checklist.type == 'hr':
                if not self.env.user.has_group('hr.group_hr_manager'):
                    raise ValidationError(_('You are not authorized to complete the checklist for HR manager!'))
        return super(ResignationChecklist, self).write(vals)

class ResignationChecklistTemplate(models.Model):
    _name = 'hr.resignation.checklist.template'
    _description = 'Template Checklist by employee for the resignation'

    name = fields.Char(string='Title', required=True, index=True)
    sequence = fields.Integer('Sequence', default=0)
    use_for = fields.Selection([('employee', 'For employee'),
                             ('trainee', 'For trainee')], string='Type', default='employee')
    type = fields.Selection([('employee', 'By employee'),
                    ('manager', 'By manager'),
                    ('hr', 'By HR')], string='Type', default='employee')

class HrResignation(models.Model):
    _inherit = 'hr.resignation'

    resignation_type = fields.Selection(selection_add=RESIGNATION_TYPE, help="Select the type of resignation: normal "
                                        "resignation or fired by the company", default='resigned')

    checklist_ids = fields.One2many('hr.resignation.checklist', 'resignation_id', required=True)

    state = fields.Selection(
        selection_add=[('draft', 'Draft'), ('confirm', 'Confirm'), ('manager_approved', 'Manager Approved'), ('handed_over', 'Handed over'),('approved', 'HR Approved'), ('cancel', 'Rejected')],
        string='Status', default='draft')

    complete_employee_checklist = fields.Boolean('Complete Employee Checklist')
    complete_manager_checklist = fields.Boolean('Complete Manager Checklist')
    complete_checklist = fields.Boolean('Complete Checklist')

    revoked_allocation = fields.Float('Revoked Allocation')
    worked_month = fields.Float('Worked Months')
    used_allocation = fields.Float('Used Allocation')
    # {leave id: previous state} of the leaves refused on apply, restored by Set to Draft
    refused_leave_states = fields.Json('Refused Leaves', groups='hr.group_hr_user', copy=False)

    def _check_group(self, group, message):
        if not self.env.su and not self.env.user.has_group(group):
            raise AccessError(message)

    def _check_state(self, states):
        for rec in self:
            if rec.state not in states:
                raise UserError(_('This action cannot be done at this step of the resignation request %s.',
                                  rec.display_name))

    def _check_flow_write(self, vals):
        """Plain employees: only the state changes of their own steps, never the approved last day."""
        if self.env.su or not FLOW_FIELDS & set(vals) or self.env.user.has_group('hr.group_hr_user'):
            return
        message = _('Only HR officers can change the status or the dates of a resignation request.')
        if 'approved_revealing_date' in vals:
            raise AccessError(message)
        for rec in self:
            if 'state' in vals and (rec.state, vals['state']) not in EMPLOYEE_STATE_CHANGES:
                raise AccessError(message)
            if 'resign_confirm_date' in vals and rec.state not in ('draft', 'confirm'):
                raise AccessError(message)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and not self.env.user.has_group('hr.group_hr_user'):
            for vals in vals_list:
                if (vals.get('state', 'draft') != 'draft' or vals.get('approved_revealing_date')
                        or vals.get('resign_confirm_date')):
                    raise AccessError(_('Only HR officers can change the status or the dates of a resignation '
                                        'request.'))
        for vals in vals_list:
            if not vals.get('reason_html'):
                raise ValidationError(_('Reason is required!'))

            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('hr.resignation') or _('New')
        res = super(HrResignation, self).create(vals_list)

        # Paid time off used beyond the worked months is revoked, per request
        for rec in res:
            allocations = rec._paid_time_off_allocations()
            if not allocations or not rec.expected_revealing_date:
                continue
            total_worked_months = self._worked_months(allocations, rec.expected_revealing_date)
            total_duration = rec._used_paid_time_off()
            data = {
                'worked_month': total_worked_months,
                'used_allocation': total_duration
            }
            if total_duration > total_worked_months:
                data['revoked_allocation'] = total_duration - total_worked_months
            rec.write(data)
        return res

    def _used_paid_time_off(self):
        """Validated paid time off of the employee this year, plus the week days in the year of leaves crossing it."""
        self.ensure_one()
        current_year = datetime.now().year
        employee_id = self.employee_id.id
        time_off_type = self.env.ref(PAID_TIME_OFF)
        time_off_basics = self.env['hr.leave'].sudo().search([
            ('employee_id', '=', employee_id),
            ('state', '=', 'validate'),
            ('work_entry_type_id', '=', time_off_type.id),
            ('request_date_from', '>=', datetime(current_year, 1, 1)),
            ('request_date_to', '<=', datetime(current_year, 12, 31))
        ])
        new_year_time_off = self.env['hr.leave'].sudo().search([
            ('employee_id', '=', employee_id),
            ('state', '=', 'validate'),
            ('work_entry_type_id', '=', time_off_type.id),
            ('request_date_from', '<', datetime(current_year, 1, 1)),
            ('request_date_to', '>=', datetime(current_year, 1, 1))
        ], limit=1)
        end_year_time_off = self.env['hr.leave'].sudo().search([
            ('employee_id', '=', employee_id),
            ('state', '=', 'validate'),
            ('work_entry_type_id', '=', time_off_type.id),
            ('request_date_from', '<=', datetime(current_year, 12, 31)),
            ('request_date_to', '>', datetime(current_year, 12, 31))
        ], limit=1)
        total_duration = 0.0
        if time_off_basics:
            for time_off_basic in time_off_basics:
                total_duration += time_off_basic.number_of_days
        if new_year_time_off:
            current_day = datetime(current_year, 1, 1).date()
            while current_day <= new_year_time_off.request_date_to:
                if current_day.weekday() < 5:
                    total_duration += 1
                current_day = current_day + timedelta(days=1)
        if end_year_time_off:
            current_day = end_year_time_off.request_date_from
            while current_day <= datetime(current_year, 12, 31).date():
                if current_day.weekday() < 5:
                    total_duration += 1
                current_day = current_day + timedelta(days=1)
        return total_duration

    def _paid_time_off_allocations(self):
        """Validated paid time off allocations of the employee inside the current year."""
        self.ensure_one()
        current_year = datetime.now().year
        time_off_type = self.env.ref(PAID_TIME_OFF)
        return self.env['hr.leave.allocation'].sudo().search(
            [('employee_id', '=', self.employee_id.id),
             ('date_from', '>=', datetime(current_year, 1, 1)),
             ('date_to', '<=', datetime(current_year, 12, 31)),
             ('work_entry_type_id', '=', time_off_type.id),
             ('state', '=', 'validate')
             ])

    def write(self, vals):
        self._check_flow_write(vals)
        if not vals.get('expected_revealing_date'):
            return super(HrResignation, self).write(vals)
        # counted on the new last day, per request
        expected_revealing_date = fields.Date.to_date(vals['expected_revealing_date'])
        for record in self:
            record_vals = dict(vals)
            leave_allocation_record = record._paid_time_off_allocations()
            if leave_allocation_record:
                total_worked_months = self._worked_months(leave_allocation_record, expected_revealing_date)
                total_duration = record.used_allocation
                record_vals['worked_month'] = total_worked_months
                record_vals['revoked_allocation'] = max(total_duration - total_worked_months, 0)
            super(HrResignation, record).write(record_vals)
        return True

    @api.model
    def _worked_months(self, allocation, last_day):
        """Months since the earliest allocation start, + 1 when over half of the last month's week days are worked."""
        days_in_month = calendar.monthrange(last_day.year, last_day.month)[1]
        week_days = [day for day in (date(last_day.year, last_day.month, d) for d in range(1, days_in_month + 1))
                     if day.weekday() < 5]
        worked_days = [day for day in week_days if day <= last_day]
        worked_months = last_day.month - min(allocation.mapped('date_from')).month
        if len(worked_days) / len(week_days) > 0.5:
            worked_months += 1
        return worked_months

    def _checklist_use_for(self):
        """'employee' templates for type Employee (or no type), else 'trainee'.
        Odoo 20: employee_type_id is HR manager data, read with sudo so the HR officer can still approve."""
        self.ensure_one()
        employee_type = self.employee_id.sudo().employee_type_id
        if not employee_type or employee_type == self.env.ref('hr.contract_type_employee', raise_if_not_found=False):
            return 'employee'
        return 'trainee'

    def _send_confirm_mail(self):
        """Send the request of the employee to its manager (no manager work email: no mail)."""
        template = self.env.ref('bhs_resignation.mail_template_confirm_resignation', raise_if_not_found=False)
        if not template:
            return
        for rec in self:
            manager_email = rec.employee_id.sudo().parent_id.work_email
            if manager_email:
                template.sudo().send_mail(rec.id, email_values={'email_to': manager_email}, force_send=True)

    def action_confirm_resignation(self):
        # Base Confirm button: mail the manager, only for the requests confirmed now
        drafts = self.filtered(lambda r: r.state == 'draft')
        res = super().action_confirm_resignation()
        drafts._send_confirm_mail()
        return res

    def confirm_resignation(self):
        if self.joined_date:
            if self.joined_date >= self.expected_revealing_date:
                raise ValidationError(_('Last date of the Employee must be anterior to Joining date'))
            for rec in self:
                rec._check_state(['draft'])
                rec.state = 'confirm'
                rec.resign_confirm_date = str(datetime.now())
            self._send_confirm_mail()
        else:
            raise ValidationError(_('Please set joining date for employee'))

    def manager_approved_resignation(self):
        # HR officer step
        if not self.env.user.has_group('hr.group_hr_user'):
            raise AccessError(_('Only HR officers can approve a resignation request as manager.'))
        if self.joined_date:
            if self.joined_date >= self.expected_revealing_date:
                raise ValidationError(_('Last date of the Employee must be anterior to Joining date'))
            for rec in self:
                rec._check_state(['confirm'])
                rec.state = 'manager_approved'
                use_for = rec._checklist_use_for()

                checklist = self.env['hr.resignation.checklist.template'].search([('use_for','=',use_for),('type','=','employee')])
                checlist_vals = []
                for chk in checklist:
                    checlist_value = {
                        "name": chk.name,
                        "sequence": chk.sequence,
                        "type": chk.type
                    }
                    checlist_vals.append((0, 0, checlist_value))

                rec.checklist_ids = checlist_vals
                # Tell the employee the request is approved
                resignation_mail = self.env.ref('bhs_resignation.mail_template_resignation_manager_approved',
                                                        raise_if_not_found=False)
                if resignation_mail:
                    resignation_mail.sudo().send_mail(rec.id,
                                                              email_values={
                                                                  'email_to': rec.employee_id.work_email},
                                                              force_send=True)
        else:
            raise ValidationError(_('Please set joining date for employee'))

    def complete_hand_over_resignation(self):
        if self.joined_date:
            if self.joined_date >= self.expected_revealing_date:
                raise ValidationError(_('Last date of the Employee must be anterior to Joining date'))
            for rec in self:
                # a second hand over only completes the employee items again
                rec._check_state(['manager_approved', 'handed_over'])
                rec.complete_employee_checklist = True
                for chk_emp in rec.checklist_ids:
                    if chk_emp.type == "employee":
                        chk_emp.check_box = True

                rec.state = 'handed_over'
                if not rec.checklist_ids.filtered(lambda r: r.type != 'employee'):
                    use_for = rec._checklist_use_for()

                    checklist = self.env['hr.resignation.checklist.template'].search(
                        [('use_for', '=', use_for), ('type', 'in', ('manager', 'hr'))])
                    checlist_vals = []
                    for chk in checklist:
                        checlist_value = {
                            "name": chk.name,
                            "sequence": chk.sequence,
                            "type": chk.type,
                            "check_box": False
                        }
                        checlist_vals.append((0, 0, checlist_value))

                    rec.checklist_ids = checlist_vals
                    # Tell the manager the handover is done (manager and HR complete the remaining items)
                    resignation_mail = self.env.ref('bhs_resignation.mail_template_resignation_handed_over',
                                                    raise_if_not_found=False)
                    if resignation_mail:
                        resignation_mail.sudo().send_mail(rec.id,
                                                          email_values={
                                                              'email_to': rec.employee_id.parent_id.work_email},
                                                          force_send=True)
        else:
            raise ValidationError(_('Please set joining date for employee'))

    @api.onchange("complete_employee_checklist")
    def onchange_complete_employee_checklist(self):
        for rec in self:
            if rec.complete_employee_checklist:
                for chk_old in rec.checklist_ids:
                    if chk_old.type == "employee":
                        chk_old.check_box = True
            else:
                for chk1 in rec.checklist_ids:
                    if chk1.type == "employee":
                        chk1.check_box = False

    @api.onchange("complete_checklist")
    def onchange_complete_checklist(self):
        for rec in self:
            if rec.complete_checklist:
                for chk in rec.checklist_ids:
                    chk.check_box = True

    def confirm_complete_manager_checklist(self):
        self._check_group('hr.group_hr_user', _('Only HR officers can complete the manager checklist.'))
        for rec in self:
            rec.complete_manager_checklist = True

    def action_reset_to_draft(self):
        # Odoo 20: hr_resignation cancels the departure, unarchives the employee, clears resigned/fired and
        # restores its user
        self._check_group('hr.group_hr_user', _('Only HR officers can reset a resignation request to draft.'))
        res = super().action_reset_to_draft()
        for rec in self:
            rec.employee_id.retired = False
            rec.checklist_ids = False
            # the employee is active again (super): give back the leaves refused when the request was applied
            rec._restore_refused_leaves()
        return res

    def _restore_refused_leaves(self):
        """Put each leave refused by this request back to its previous state. A leave that no longer exists or is no
        longer refused is skipped; a leave that cannot be restored is logged and noted on the request."""
        self.ensure_one()
        stored = self.sudo().refused_leave_states
        if not stored:
            return
        # leave_fast_create: the approval checks of hr.leave.write (double validation rules) ignore sudo, the user
        # resetting the request may have no Time Off rights
        Leave = self.env['hr.leave'].sudo().with_context(leave_fast_create=True)
        failed = restored = Leave
        for leave_id, state in stored.items():
            leave = Leave.browse(int(leave_id)).exists()
            if not leave or leave.state != 'refuse' or state not in REFUSABLE_LEAVE_STATES:
                continue
            try:
                with self.env.cr.savepoint():
                    if state == 'validate':
                        # validated again through the core step (time off on the calendar, allocations)
                        leave.write({'state': 'confirm'})
                        leave._action_validate(check_state=False)
                    else:
                        leave.write({'state': state})
                        # the refusal removed the approval activity: give it back to the approver
                        leave.activity_update()
                restored |= leave
            except Exception:
                _logger.exception('Resignation %s: leave %s could not be set back to %s', self.display_name,
                                  leave.id, state)
                failed |= leave
        # keep the previous state of the leaves not restored, so it is not lost
        self.sudo().refused_leave_states = {str(leave.id): stored[str(leave.id)] for leave in failed} or False
        _logger.info('Resignation %s set to draft: leaves %s set back to their previous state', self.display_name,
                     restored.ids)
        if failed:
            self.message_post(body=_('These time off could not be set back to their previous state: %s',
                                     ', '.join(failed.mapped('display_name'))))

    def action_approve_resignation(self):
        # Base Approve button (replaced in the form): the final approval goes through the HR manager step
        return self.approve_resignation()

    def approve_resignation(self):
        self._check_group('hr.group_hr_manager', _('Only HR managers can approve a resignation request.'))
        for rec in self:
            if rec.expected_revealing_date and rec.resign_confirm_date:
                rec._check_state(['handed_over'])
                rec.complete_checklist = True
                for chk in rec.checklist_ids:
                    chk.check_box = True

                rec.state = 'approved'
                rec.approved_revealing_date = rec.expected_revealing_date if not rec.approved_revealing_date else rec.approved_revealing_date

                contracts = self.sudo().env['hr.version'].sudo().search([
                    ('employee_id', '=', rec.employee_id.id),
                    '&',
                    '|',
                    ('contract_date_start', '=', False),
                    ('contract_date_start', '<', fields.Date.today()),
                    '|',
                    ('contract_date_end', '=', False),
                    ('contract_date_end', '>=', fields.Date.today()),
                ])

                for contract in contracts:
                    # Odoo 20: employee_contract holds the contract template of the version (hr_resignation 20)
                    rec.employee_contract = contract.contract_template_id.name or contract.display_name
                    rec.state = 'approved'
                rec._update_employee_status()
            else:
                raise ValidationError(_('Please enter valid dates.'))

    @api.model
    def update_employee_status(self):
        """Daily cron of hr_resignation: apply the approved requests whose approved last day is reached."""
        resignation = self.env['hr.resignation'].search([('state', '=', 'approved')])
        for rec in resignation:
            # one failing request must not stop the others: it is rolled back, logged and tried again next run
            try:
                with self.env.cr.savepoint():
                    rec._update_employee_status()
            except Exception:
                _logger.exception('Resignation %s (id %s) could not be applied by the daily cron', rec.display_name,
                                  rec.id)
        return True

    def _update_employee_status(self):
        self.ensure_one()
        self.approved_revealing_date = self.approved_revealing_date or self.expected_revealing_date
        employee = self.employee_id
        # The last day is reached on the UTC date: core hr.employee.departure refuses a departure before it (a local
        # date such as Asia/Ho_Chi_Minh is one day ahead between 00:00 and 07:00)
        utc_self = self.with_context(tz='UTC')
        if self.approved_revealing_date <= fields.Date.context_today(utc_self) and employee.active:
            self._refuse_leaves_after(self.approved_revealing_date)

            # Odoo 20: an hr.employee.departure archives the employee and its user and ends the contract. The future
            # leaves were refused above, so the hr_holidays departure cleanup is skipped.
            utc_self.with_context(bhs_resignation_keep_leaves=True)._register_departure(
                self.approved_revealing_date, self._departure_reason())

    def _refuse_leaves_after(self, last_day):
        """Refuse the leaves of the employee after the last day through the Time Off refusal (sudo: the HR manager
        approving the request or the cron user may have no Time Off rights). A leave crossing the last day is cut as
        the core departure does: the part up to the last day keeps its state, the part from the day after is refused.
        The previous state of each refused leave is stored to be restored by Set to Draft."""
        self.ensure_one()
        leaves = self.env['hr.leave'].sudo().search([
            ('employee_id', '=', self.employee_id.id),
            ('request_date_to', '>', last_day),
            ('state', 'in', REFUSABLE_LEAVE_STATES),
        ])
        if not leaves:
            return
        straddling = leaves.filtered(lambda leave: leave.request_date_from <= last_day)
        tails = straddling._split_leaves(last_day + timedelta(days=1))
        # a cut part without working time is not created: the leave left then starts after the last day (refused)
        # or ends on it (kept)
        to_refuse = (leaves - straddling) | tails | straddling.filtered(
            lambda leave: leave.request_date_from > last_day)
        to_refuse = to_refuse.filtered(lambda leave: leave.state in REFUSABLE_LEAVE_STATES)
        if not to_refuse:
            return
        states = {str(leave.id): leave.state for leave in to_refuse}
        to_refuse.action_refuse()
        self.sudo().refused_leave_states = dict(self.sudo().refused_leave_states or {}, **states)
        _logger.info('Resignation %s: leaves after the last day %s refused: %s (cut: %s)', self.display_name,
                     last_day, to_refuse.ids, straddling.ids)


    @api.onchange('employee_id')
    @api.depends('employee_id')
    def check_request_existence(self):
        for rec in self:
            if rec.employee_id:
                resignation_request = self.env['hr.resignation'].search([('employee_id', '=', rec.employee_id.id),
                                                                         ('state', 'in', ['confirm', 'approved'])])
                if resignation_request:
                    raise ValidationError(_('There is a resignation request in confirmed or'
                                            ' approved state for this employee'))

                no_of_contract = self.env['hr.version'].sudo().search([
                    ('employee_id', '=', self.employee_id.id),
                    ('active', '=', True),
                    '|', ('contract_date_start', '=', False), ('contract_date_start', '<=', fields.Date.today()),
                    '|', ('contract_date_end', '=', False), ('contract_date_end', '>', fields.Date.today()),
                ], limit=1)
                if no_of_contract:
                    rec.employee_contract = no_of_contract.contract_template_id.name or no_of_contract.display_name
                    rec.notice_period = no_of_contract.notice_days
                else:
                    rec.employee_contract = False
                    rec.notice_period = False
            else:
                rec.employee_contract = False
                rec.notice_period = False

    def get_resignation_url(self):
        result = self.env['ir.actions.act_window']._for_xml_id('hr_resignation.hr_resignation_action')
        web_base_url = self.env['ir.config_parameter'].sudo().get_str(
            'web.base.url')  # must be https if not localhost
        if result['path']:
            return web_base_url + '/odoo/%s/%s' % (result['path'], self.id)
        else:
            return web_base_url + '/odoo/action-%s/%s' % (result['id'], self.id)

    def cancel_resignation(self):
        self._check_group('hr.group_hr_user', _('Only HR officers can reject a resignation request.'))
        for rec in self:
            rec.state = 'cancel'
            # Tell the employee the request is rejected
            resignation_mail = self.env.ref('bhs_resignation.mail_template_resignation_reject',
                                            raise_if_not_found=False)
            if resignation_mail:
                resignation_mail.sudo().send_mail(rec.id,
                                                  email_values={
                                                      'email_to': rec.employee_id.work_email},
                                                  force_send=True)

    def action_reject_resignation(self):
        self._check_group('hr.group_hr_user', _('Only HR officers can reject a resignation request.'))
        for rec in self:
            rec.state = 'cancel'
            # Tell the employee the request is rejected
            resignation_mail = self.env.ref('bhs_resignation.mail_template_resignation_reject',
                                            raise_if_not_found=False)
            if resignation_mail:
                resignation_mail.sudo().send_mail(rec.id,
                                                  email_values={
                                                      'email_to': rec.employee_id.work_email},
                                                  force_send=True)


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    retired = fields.Boolean(string="Retired", default=False, store=True, help="If checked then employee has retired",
                             groups="hr.group_hr_user")
