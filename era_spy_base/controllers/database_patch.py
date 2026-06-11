import logging

from odoo import http
from odoo.addons.web.controllers.database import Database

_logger = logging.getLogger(__name__)


class DatabasePatch(Database):

    @http.route('/web/database/backup', type='http', auth="none", methods=['POST'], csrf=False)
    def backup(self, master_pwd=None, name=None, backup_format='zip', filestore=True):
        if not master_pwd or not name:
            _logger.warning("Database backup request missing required parameters (master_pwd/name).")
            return self._render_template(error="Missing required parameters: master_pwd and name.")
        return super().backup(master_pwd=master_pwd, name=name, backup_format=backup_format, filestore=filestore)

    @http.route('/web/database/duplicate', type='http', auth="none", methods=['POST'], csrf=False)
    def duplicate(self, master_pwd=None, name=None, new_name=None, neutralize_database=False):
        if not master_pwd or not name or not new_name:
            _logger.warning("Database duplicate request missing required parameters (master_pwd/name/new_name).")
            return self._render_template(error="Missing required parameters: master_pwd, name and new_name.")
        return super().duplicate(master_pwd=master_pwd, name=name, new_name=new_name, neutralize_database=neutralize_database)
