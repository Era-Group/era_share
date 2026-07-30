# -*- coding: utf-8 -*-
"""Close out analyses that can never run.

voip.call.analysis_status defaulted to 'pending', so every call claimed to be
queued for AI analysis from the moment it was created. Measured 2026-07-30:
2,652 rows were stuck there, and every single one had transcription_status
'no_audio' (2,637) or 'unsupported' (15) with no transcript. They cannot
recover — the re-transcribe cron requires an ir_attachment audio file on the
call, which these by definition do not have.

The regression is recent: monthly pending counts were 87 in May, 306 in June,
2,259 in July, and rows older than 2026-07-18 have analysis_status NULL. So
the default was introduced around 2026-07-18 and this backfill only has to
undo twelve days of it.

The default is removed on the field and voip.call.write() now closes analysis
whenever transcription ends in a no-transcript state, so this cannot rebuild.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE voip_call
           SET analysis_status = 'skipped'
         WHERE analysis_status = 'pending'
           AND transcription_status IN ('no_audio', 'unsupported')
        """
    )
