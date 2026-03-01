"""Disable and remove AI Task Assistant automation rule to prevent automatic backend calls.

The backend must only be called from the manual "Generate sub-tasks with AI" button.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Automation = env["base.automation"]
    # Find and remove any AI Task Assistant automation on project.task
    rules = Automation.search([
        ("model_id.model", "=", "project.task"),
        "|",
        ("name", "ilike", "AI Task Assistant"),
        ("name", "ilike", "Trigger on user assignment"),
    ])
    for rule in rules:
        rule.action_server_ids = [(5, 0, 0)]
        rule.active = False
        rule.unlink()
        _logger.info("AI Task Assistant: Removed automation rule %s", rule.name)
