/**
 * A citation opens over the answer, not instead of it.
 *
 * Odoo numbers the sources behind an answer and links each to the file it came
 * from, in a new tab. For a lawyer checking the article an answer leaned on,
 * that trades the answer for the source — so the click is caught here and the
 * text is shown in a dialog, with the answer still underneath it.
 *
 * Two selectors, deliberately: the class this module marks its own citations
 * with, and the shape Odoo builds for any of them. The second is what makes
 * conversations that are already on screen behave the same way.
 */
import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const CITATION_SELECTOR = 'a.o_era_citation, sup > a[href*="/web/content/"]';

export class EraCitationDialog extends Component {
    static template = "era_law_firm_ai.CitationDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        document: Object,
    };

    setup() {
        this.notification = useService("notification");
    }

    get hasText() {
        return Boolean(this.props.document.text);
    }

    get reference() {
        return this.props.document.reference || [];
    }

    async onCopyReference() {
        // What goes into the memorandum: the instrument, its kind, whether it
        // still stands, and the day our copy of it was taken.
        await navigator.clipboard.writeText(this.props.document.citation_line);
        this.notification.add(_t("Reference copied"), { type: "success" });
    }
}

export const eraCitationPopupService = {
    dependencies: ["dialog", "orm"],
    start(env, { dialog, orm }) {
        const onClick = async (ev) => {
            const link = ev.target.closest(CITATION_SELECTOR);
            if (!link) {
                return;
            }
            const attachmentId = link.getAttribute("href")?.match(/\/web\/content\/(\d+)/)?.[1];
            if (!attachmentId) {
                return; // an outside source: let the browser have it
            }
            ev.preventDefault();
            ev.stopPropagation();
            const document = await orm.call("ai.agent.source", "era_citation_document", [
                Number(attachmentId),
            ]);
            dialog.add(EraCitationDialog, { document });
        };
        // Capture: the chat window handles clicks of its own, and this one is
        // about the link rather than about the message it sits in.
        window.document.addEventListener("click", onClick, true);
    },
};

registry.category("services").add("era_citation_popup", eraCitationPopupService);
