"""Repair calls whose SIP recording proves they ended earlier."""


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        WITH last_recording AS (
            SELECT DISTINCT ON (res_id)
                   res_id AS call_id,
                   date_trunc('second', create_date) AS ended_at
              FROM ir_attachment
             WHERE res_model = 'voip.call'
               AND name = 'recording.webm'
             ORDER BY res_id, id DESC
        )
        UPDATE voip_call AS call
           SET end_date = recording.ended_at,
               state = 'terminated',
               write_date = NOW()
          FROM last_recording AS recording
         WHERE call.id = recording.call_id
           AND call.start_date IS NOT NULL
           AND recording.ended_at >= call.start_date
           AND (
                call.state = 'ongoing'
                OR call.end_date > recording.ended_at + INTERVAL '10 seconds'
           )
        """
    )
