import { registry } from "@web/core/registry";

/**
 * Main flow as HR manager: create a request for an employee -> Confirm -> Manager Approve -> Complete Hand over
 * -> Approve. Results (state, checklist, archived employee) are checked on the Python side
 * (tests/test_functional_ui.py).
 */
registry.category("web_tour.tours").add("bhs_rsg_main_flow_tour", {
    steps: () => [
        {
            content: "Pick the employee",
            trigger: ".o_field_widget[name=employee_id] input",
            run: "edit RSG Tour Employee",
        },
        {
            content: "Select the employee in the dropdown",
            trigger: "a.dropdown-item:contains(RSG Tour Employee)",
            run: "click",
        },
        {
            content: "Last day of the employee",
            trigger: ".o_field_widget[name=expected_revealing_date] input",
            run: "edit 01/15/2026",
        },
        {
            content: "Reason (html, required)",
            trigger: ".o_field_html[name=reason_html] .odoo-editor-editable",
            run: "editor Leaving for personal reasons",
        },
        {
            content: "Leave figures are shown",
            trigger: ".o_field_widget[name=revoked_allocation]",
        },
        {
            content: "Confirm (base button, saves the request)",
            trigger: "button[name=action_confirm_resignation]",
            run: "click",
        },
        {
            content: "Manager Approve",
            trigger: "button[name=manager_approved_resignation]",
            run: "click",
        },
        {
            content: "Employee checklist created from the templates",
            trigger: ".o_field_widget[name=checklist_ids] .o_data_row:contains(Tour: return laptop)",
        },
        {
            content: "Complete Hand over",
            trigger: "button[name=complete_hand_over_resignation]",
            run: "click",
        },
        {
            content: "Manager and HR checklist added",
            trigger: ".o_field_widget[name=checklist_ids] .o_data_row:contains(Tour: HR exit interview)",
        },
        {
            content: "Approve (HR manager)",
            trigger: "button[name=approve_resignation]",
            run: "click",
        },
        {
            content: "Approved: Set to Draft is offered",
            trigger: "button[name=action_reset_to_draft]",
        },
    ],
});

/**
 * A plain employee files its own request (the employee is preset) and confirms it.
 */
registry.category("web_tour.tours").add("bhs_rsg_employee_request_tour", {
    steps: () => [
        {
            content: "The own employee is preset",
            trigger: ".o_field_widget[name=employee_id]:contains(RSG Tour Own)",
            timeout: 30000,
        },
        {
            content: "Last day of the employee",
            trigger: ".o_field_widget[name=expected_revealing_date] input",
            run: "edit 01/15/2026",
        },
        {
            content: "Reason (html, required)",
            trigger: ".o_field_html[name=reason_html] .odoo-editor-editable",
            run: "editor Moving abroad",
        },
        {
            content: "No HR button for the employee",
            trigger: "body:not(:has(button[name=manager_approved_resignation]))",
        },
        {
            content: "Confirm",
            trigger: "button[name=action_confirm_resignation]",
            run: "click",
        },
        {
            content: "Confirmed",
            trigger: ".o_statusbar_status button.o_arrow_button_current:contains(Confirm)",
        },
    ],
});

/**
 * HR manager adds a checklist template in the editable list (Employees > Configuration).
 */
registry.category("web_tour.tours").add("bhs_rsg_checklist_template_tour", {
    steps: () => [
        {
            content: "Existing templates are listed",
            trigger: ".o_list_view .o_data_row:contains(Tour: return laptop)",
        },
        {
            content: "New template",
            trigger: ".o_list_button_add",
            run: "click",
        },
        {
            content: "Template title",
            trigger: ".o_selected_row .o_field_widget[name=name] input",
            run: "edit Tour: sign NDA",
        },
        {
            content: "Save",
            trigger: ".o_list_button_save",
            run: "click",
        },
        {
            content: "Saved row",
            trigger: "body:not(:has(.o_selected_row)) .o_data_row:contains(Tour: sign NDA)",
        },
    ],
});
