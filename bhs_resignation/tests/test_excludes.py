# -*- coding: utf-8 -*-
"""bhs_resignation_workflow_controller ships the same models, checklist and flow on hr_resignation:
Odoo must refuse to install it next to this module."""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

OTHER = 'bhs_resignation_workflow_controller'


@tagged('post_install', '-at_install')
class TestExcludes(TransactionCase):

    def test_manifest_excludes_the_twin_module(self):
        module = self.env['ir.module.module'].search([('name', '=', 'bhs_resignation')])
        self.assertIn(OTHER, module.exclusion_ids.mapped('name'))

    def test_installing_the_twin_module_is_refused(self):
        other = self.env['ir.module.module'].search([('name', '=', OTHER)])
        if not other or other.state != 'uninstalled':
            self.skipTest(f"{OTHER} is not available as an uninstalled module in this database")
        with self.assertRaisesRegex(UserError, 'incompatible'):
            other.button_install()
        self.assertEqual(other.state, 'uninstalled')
