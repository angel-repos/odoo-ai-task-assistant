import logging
import requests

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Production backend URL (hardcoded)
AI_BACKEND_URL = "https://odoo.lokeai.es"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # ------------------------------------------------------------------
    # Backend connection (api_key stored in ir.config_parameter from activation)
    # ------------------------------------------------------------------

    ai_backend_url = fields.Char(
        string="AI Backend URL",
        config_parameter="ai_task_assistant.backend_url",
        default="https://odoo.lokeai.es",
        readonly=True,
    )
    ai_api_key = fields.Char(
        string="AI API Key",
        config_parameter="ai_task_assistant.api_key",
    )

    # ------------------------------------------------------------------
    # License (read from ir.config_parameter, shown in UI)
    # ------------------------------------------------------------------

    ai_license_tier = fields.Char(
        string="Plan",
        config_parameter="ai_task_assistant.license_tier",
        readonly=True,
    )
    ai_license_tasks_used = fields.Integer(
        string="Tasks used this month",
        config_parameter="ai_task_assistant.license_tasks_used",
        readonly=True,
    )
    ai_license_tasks_limit = fields.Integer(
        string="Monthly task limit",
        config_parameter="ai_task_assistant.license_tasks_limit",
        readonly=True,
    )
    ai_license_reset_date = fields.Char(
        string="Resets on",
        config_parameter="ai_task_assistant.license_reset_date",
        readonly=True,
    )
    ai_upgrade_url = fields.Char(
        string="Upgrade URL",
        config_parameter="ai_task_assistant.upgrade_url",
        default="https://apps.odoo.com/apps/modules/browse?search=ai+task+assistant",
        help="URL to the paid app on Odoo App Store. Shown when license limit is reached.",
    )

    # ------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------

    ai_auto_trigger = fields.Boolean(
        string="Auto-trigger on assignment",
        config_parameter="ai_task_assistant.auto_trigger",
        default=False,
    )
    ai_llm_provider = fields.Selection(
        selection=[
            ("perplexity", "Perplexity"),
            ("openai", "OpenAI"),
            ("azure", "Azure OpenAI"),
            ("ollama", "Ollama (self-hosted)"),
        ],
        string="LLM Provider",
        config_parameter="ai_task_assistant.llm_provider",
        default="ollama",
    )
    ai_llm_api_key = fields.Char(
        string="LLM API Key",
        config_parameter="ai_task_assistant.llm_api_key",
        help="API key for the selected LLM provider. Overrides the backend .env when set.",
    )

    # ------------------------------------------------------------------
    # Odoo callback
    # ------------------------------------------------------------------

    ai_odoo_callback_url = fields.Char(
        string="Odoo URL for callback",
        config_parameter="ai_task_assistant.odoo_callback_url",
        help="URL the backend must use to reach Odoo (e.g. http://host.docker.internal:8069). If empty, system base URL is used.",
    )
    ai_odoo_callback_login = fields.Char(
        string="Odoo callback login",
        config_parameter="ai_task_assistant.odoo_callback_login",
        help="User login for the backend to connect back to Odoo (e.g. admin).",
    )
    ai_odoo_callback_api_key = fields.Char(
        string="Odoo callback API key",
        config_parameter="ai_task_assistant.odoo_callback_api_key",
        help="API key generated in Odoo (User → Account Security → New API Key). Used instead of password for callback.",
    )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _get_backend_url(self):
        return AI_BACKEND_URL.rstrip("/")

    def _get_db_uuid(self):
        return self.env["ir.config_parameter"].sudo().get_param("database.uuid", "")

    def _get_purchased_tier(self):
        """
        Detect which paid tier module is installed (from Odoo App Store purchase).
        Returns: 'enterprise' | 'business' | 'starter' | 'free'
        """
        Module = self.env["ir.module.module"].sudo()
        installed = Module.search([
            ("state", "=", "installed"),
            ("name", "in", [
                "ai_task_assistant_enterprise",
                "ai_task_assistant_business",
                "ai_task_assistant_starter",
            ]),
        ]).mapped("name")
        if "ai_task_assistant_enterprise" in installed:
            return "enterprise"
        if "ai_task_assistant_business" in installed:
            return "business"
        if "ai_task_assistant_starter" in installed:
            return "starter"
        return "free"

    @api.model
    def _sync_license_from_backend(self, api_key):
        """
        Fetch license from backend (GET /license-status) and update ir.config_parameter.
        Returns True on success, False on failure. Used when limit_reached to refresh license data.
        """
        backend_url = AI_BACKEND_URL.rstrip("/")
        try:
            resp = requests.get(
                f"{backend_url}/license-status",
                headers={"X-API-Key": api_key},
                timeout=10,
            )
            if resp.status_code == 404:
                _logger.warning("AI Task Assistant: Sync license - license not found (404)")
                return False
            if resp.status_code not in (200, 429):
                _logger.warning("AI Task Assistant: Sync license failed: %s %s", resp.status_code, resp.text[:100])
                return False
            data = resp.json()
            icp = self.env["ir.config_parameter"].sudo()
            icp.set_param("ai_task_assistant.license_tier", data.get("tier", ""))
            icp.set_param("ai_task_assistant.license_tasks_used", str(data.get("tasks_used", 0)))
            icp.set_param("ai_task_assistant.license_tasks_limit", str(data.get("tasks_limit", 0)))
            icp.set_param("ai_task_assistant.license_reset_date", data.get("reset_date") or "")
            _logger.info(
                "AI Task Assistant: Synced license from limit_reached (tier=%s, used=%s, limit=%s)",
                data.get("tier"),
                data.get("tasks_used"),
                data.get("tasks_limit"),
            )
            return True
        except Exception as e:
            _logger.warning("AI Task Assistant: Sync license failed: %s", e)
            return False

    def set_values(self):
        """Override to avoid overwriting api_key when form has empty value (e.g. form didn't reload)."""
        _logger.info("AI Task Assistant: set_values called (ai_api_key from form: %s)", "set" if (self.ai_api_key or "").strip() else "empty")
        icp = self.env["ir.config_parameter"].sudo()
        stored_api_key = icp.get_param("ai_task_assistant.api_key", "")
        form_api_key = (self.ai_api_key or "").strip()
        if not form_api_key and stored_api_key:
            _logger.info(
                "AI Task Assistant: Preserving stored api_key (form had empty value, likely didn't reload)"
            )
            self.ai_api_key = stored_api_key
        super().set_values()

    def _save_license(self, data: dict):
        """Persist license fields returned by the backend into ir.config_parameter."""
        icp = self.env["ir.config_parameter"].sudo()
        api_key = data.get("api_key", "")
        icp.set_param("ai_task_assistant.api_key", api_key)
        icp.set_param("ai_task_assistant.license_tier", data.get("tier", "free"))
        icp.set_param("ai_task_assistant.license_tasks_used", str(data.get("tasks_used", 0)))
        icp.set_param("ai_task_assistant.license_tasks_limit", str(data.get("tasks_limit", 10)))
        icp.set_param("ai_task_assistant.license_reset_date", data.get("reset_date") or "")
        _logger.info(
            "AI Task Assistant: Saved license to ir.config_parameter (api_key=%s..., tier=%s)",
            (api_key[:12] + "..." if api_key else "empty"),
            data.get("tier", ""),
        )

    def action_activate_license(self):
        """
        Called when the user clicks 'Activate free license'.
        Registers this Odoo instance with the backend and stores the API key.
        """
        backend_url = self._get_backend_url()
        db_uuid = self._get_db_uuid()
        _logger.info("AI Task Assistant: action_activate_license started (db_uuid=%s)", db_uuid[:8] if db_uuid else "?")
        if not db_uuid:
            _logger.error("AI Task Assistant: No database UUID available")
            raise UserError("Could not determine database UUID. Please check your Odoo installation.")

        odoo_url = (
            self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        )
        try:
            from odoo.release import version as odoo_ver
        except ImportError:
            odoo_ver = ""

        purchased_tier = self._get_purchased_tier()
        payload = {
            "db_uuid": db_uuid,
            "odoo_url": odoo_url,
            "odoo_version": odoo_ver,
            "purchased_tier": purchased_tier,
        }
        _logger.info(
            "AI Task Assistant: POST %s/register (odoo_url=%s, purchased_tier=%s)",
            backend_url,
            odoo_url,
            purchased_tier,
        )

        try:
            resp = requests.post(
                f"{backend_url}/register",
                json=payload,
                timeout=15,
            )
            _logger.info("AI Task Assistant: Backend responded %s", resp.status_code)
            if resp.status_code not in (200, 201):
                _logger.error("AI Task Assistant: Registration failed: %s", resp.text[:200])
                raise UserError(f"Registration failed ({resp.status_code}): {resp.text[:300]}")
            data = resp.json()
        except requests.exceptions.Timeout:
            _logger.exception("AI Task Assistant: Registration timed out")
            raise UserError("Registration timed out. Check the backend URL and network.")
        except requests.exceptions.RequestException as e:
            _logger.exception("License activation failed: %s", e)
            raise UserError(f"Registration failed: {e}") from e

        api_key = data.get("api_key", "")
        if not api_key:
            _logger.error("AI Task Assistant: Backend returned no api_key: %s", data)
            raise UserError("Backend did not return an API key. Please try again.")

        self._save_license(data)
        _logger.info("AI Task Assistant: License saved, triggering reload")

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "License activated",
                "message": "API key saved. Reloading page to show license status.",
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_sync_license(self):
        """
        Called when the user clicks 'Sync license'.
        Fetches current tier and usage from the backend.
        """
        backend_url = self._get_backend_url()
        api_key = (self.ai_api_key or "").strip()
        _logger.info("AI Task Assistant: action_sync_license started")
        if not api_key:
            _logger.warning("AI Task Assistant: Sync aborted - no API key")
            raise UserError("No API key configured. Activate your license first.")

        try:
            resp = requests.get(
                f"{backend_url}/license-status",
                headers={"X-API-Key": api_key},
                timeout=10,
            )
            _logger.info("AI Task Assistant: license-status responded %s", resp.status_code)
            if resp.status_code == 404:
                raise UserError("License not found. Please activate your license again.")
            if resp.status_code == 429:
                _logger.warning("AI Task Assistant: Rate limit (429) - updating local data anyway")
            if resp.status_code not in (200, 429):
                _logger.error("AI Task Assistant: Sync failed: %s", resp.text[:200])
                raise UserError(f"Sync failed ({resp.status_code}): {resp.text[:300]}")
            data = resp.json()
        except requests.exceptions.Timeout:
            _logger.exception("AI Task Assistant: Sync timed out")
            raise UserError("Sync timed out. Check the backend URL and network.")
        except requests.exceptions.RequestException as e:
            _logger.exception("License sync failed: %s", e)
            raise UserError(f"Sync failed: {e}") from e

        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("ai_task_assistant.license_tier", data.get("tier", ""))
        icp.set_param("ai_task_assistant.license_tasks_used", str(data.get("tasks_used", 0)))
        icp.set_param("ai_task_assistant.license_tasks_limit", str(data.get("tasks_limit", 0)))
        icp.set_param("ai_task_assistant.license_reset_date", data.get("reset_date") or "")
        _logger.info(
            "AI Task Assistant: Synced license (tier=%s, used=%s, limit=%s)",
            data.get("tier"),
            data.get("tasks_used"),
            data.get("tasks_limit"),
        )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "License synced",
                "message": "Tier and usage updated. Reloading page.",
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_test_connection(self):
        """Test both directions: Odoo → Backend and Backend → Odoo."""
        backend_url = self._get_backend_url()
        api_key = (self.ai_api_key or "").strip()
        _logger.info("AI Task Assistant: action_test_connection started")
        if not api_key:
            _logger.warning("AI Task Assistant: Test connection aborted - no API key")
            raise UserError("No API key configured. Activate your license first.")

        # 1) Odoo → Backend: GET /test-connection
        try:
            resp = requests.get(
                f"{backend_url}/test-connection",
                headers={"X-API-Key": api_key},
                params={"db_uuid": self._get_db_uuid()},
                timeout=10,
            )
            if resp.status_code == 401:
                raise UserError("Invalid API key. The license key is not recognized by the backend.")
            if resp.status_code == 403:
                raise UserError("API key does not belong to this Odoo database or license is inactive.")
            if resp.status_code != 200:
                raise UserError(f"Backend returned {resp.status_code}: {resp.text[:200]}")
            _logger.info("AI Task Assistant: Odoo→Backend OK")
        except requests.exceptions.Timeout:
            raise UserError("Odoo → Backend: Connection timed out. Check the backend URL and network.")
        except requests.exceptions.RequestException as e:
            _logger.exception("Test Odoo→Backend failed: %s", e)
            raise UserError(f"Odoo → Backend failed: {e}") from e

        # 2) Backend → Odoo: POST /test-callback
        odoo_url = (
            (self.ai_odoo_callback_url or "").strip().rstrip("/")
            or self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        )
        _logger.info("AI Task Assistant: Testing Backend→Odoo (odoo_url=%s)", odoo_url)
        if not odoo_url:
            raise UserError("Configure Odoo URL for callback (or set system parameter web.base.url).")
        callback_payload = {
            "odoo_url": odoo_url,
            "odoo_db": self.env.cr.dbname,
            "odoo_callback_login": self.ai_odoo_callback_login or "",
            "odoo_callback_api_key": self.ai_odoo_callback_api_key or "",
        }
        try:
            resp = requests.post(
                f"{backend_url}/test-callback",
                json=callback_payload,
                headers={"X-API-Key": api_key, "Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code == 200:
                _logger.info("AI Task Assistant: Backend→Odoo OK - connection test passed")
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": "Connection OK",
                        "message": "Odoo → Backend and Backend → Odoo both work.",
                        "type": "success",
                        "sticky": False,
                    },
                }
            try:
                err = resp.json()
                msg = err.get("detail", err.get("message", resp.text[:200]))
            except Exception:
                msg = resp.text[:200]
            raise UserError(f"Backend → Odoo failed ({resp.status_code}): {msg}")
        except requests.exceptions.Timeout:
            raise UserError("Backend → Odoo: Connection timed out. Check Odoo URL for callback and network.")
        except requests.exceptions.RequestException as e:
            _logger.exception("Test Backend→Odoo failed: %s", e)
            raise UserError(f"Backend → Odoo failed: {e}") from e
