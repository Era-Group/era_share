import { RecipientsInput } from "@mail/core/web/recipients_input";

import { _t } from "@web/core/l10n/translation";
import { uniqueId } from "@web/core/utils/functions";

/**
 * The "Cc:" line of the chatter composer. It reuses the whole autocomplete /
 * partner-creation behaviour of the "To:" line and only changes where the
 * recipients are read from and written to.
 */
export class CcRecipientsInput extends RecipientsInput {
    static template = "mail.RecipientsInput";

    /** @returns {Object[]} */
    getTagsFromMailThread() {
        const tags = [];
        for (const recipient of this.props.thread.ccRecipients) {
            const title = `${recipient.name || recipient.display_name || _t("Unnamed")} ${
                recipient.email ? "<" + recipient.email + ">" : ""
            }`.trim();
            tags.push({
                id: uniqueId("cc_tag_"),
                resId: recipient.partner_id,
                canEdit: true,
                text: recipient.name || recipient.display_name || recipient.email || _t("Unnamed"),
                name: recipient.name || recipient.display_name || _t("Unnamed"),
                email: recipient.email,
                title,
                onClick: (ev) => {
                    if (recipient.partner_id && recipient.email) {
                        const viewProfileBtnOverride = () => {
                            this.action.doAction({
                                type: "ir.actions.act_window",
                                res_model: "res.partner",
                                res_id: recipient.partner_id,
                                views: [[false, "form"]],
                                target: "current",
                            });
                        };
                        this.popover.open(ev.target, {
                            viewProfileBtnOverride,
                            id: recipient.partner_id,
                        });
                    }
                },
                onDelete: () => {
                    this.props.thread.ccRecipients = this.props.thread.ccRecipients.filter(
                        (ccRecipient) =>
                            ccRecipient.partner_id !== recipient.partner_id ||
                            ccRecipient.email !== recipient.email
                    );
                },
            });
        }
        return tags;
    }

    /** @returns {Object[]} */
    getAllMailThreadRecipients() {
        // include the "To:" recipients so that the autocomplete does not
        // propose, and 'hasRecipient' refuses, somebody already on that line.
        return [...super.getAllMailThreadRecipients(), ...this.props.thread.ccRecipients];
    }

    /** @param {Object} recipient */
    insertAdditionalRecipient(recipient) {
        if (this.hasRecipient(recipient)) {
            return;
        }
        this.props.thread.ccRecipients.push(recipient);
    }

    /** @returns {string} */
    getPlaceholder() {
        return this.props.thread.ccRecipients.length ? "" : _t("Add people in copy...");
    }
}
