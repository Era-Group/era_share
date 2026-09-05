/**
 * Odoo's AI button stands down inside the law firm app.
 *
 * Pressed on a record, that button sends the record's whole field JSON and its
 * entire chatter to the model (ai/models/models.py:88, ai/models/mail_thread.py:52)
 * without the consent screen, the redaction of identity and account numbers,
 * the payload hash, the audit entry or the citation check that every
 * legal.ai.request passes through. On a list screen it opens a general agent
 * with no legal sources, which is the one failure this module exists to
 * prevent: a confident answer citing an article nobody attached.
 *
 * So inside this app it is hidden, and the two governed ways in take its place
 * — Legal Research in the navbar beside it, and Ask AI on every record of a
 * file. Everywhere else in the database it is untouched: it is Odoo's button,
 * for Odoo's apps.
 *
 * Hidden rather than removed from the registry, because removal would take it
 * out of every app at once.
 */
import { patch } from "@web/core/utils/patch";
import SystrayAction from "@ai/web/systray_action";
import { useInLawFirmApp } from "@era_law_firm_ai/js/era_app_watch";

patch(SystrayAction.prototype, {
    setup() {
        super.setup();
        this.eraInLawFirm = useInLawFirmApp();
    },
});
