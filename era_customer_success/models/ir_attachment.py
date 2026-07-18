# -*- coding: utf-8 -*-
import logging

from odoo import models
from odoo.http import Stream

_logger = logging.getLogger(__name__)


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    def _to_http_stream(self):
        """Degrade gracefully when the filestore file is missing.

        This deployment's filestore is largely out of sync with the database
        (the DB was restored without its matching filestore), so os.stat() on a
        missing path raised FileNotFoundError -> HTTP 500 + a full traceback on
        every image/document request. Instead of 500ing, we return an empty
        stream: image controllers then fall back to their placeholder, and the
        error noise is muted. Logged at debug only (invisible at the prod
        'warn' level) so this does not become noise of its own.
        """
        try:
            return super()._to_http_stream()
        except (FileNotFoundError, OSError) as e:
            _logger.debug("Filestore file missing for attachment %s (%s): %s",
                          self.id, self.store_fname, e)
            stream = Stream(
                mimetype=self.mimetype or 'application/octet-stream',
                download_name=self.name,
                public=self.public,
            )
            stream.type = 'data'
            stream.data = b''
            stream.size = 0
            return stream
