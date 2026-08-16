/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component } from "@odoo/owl";

export class BrdWorkflowHelpDialog extends Component {
    static template = "era_project_brd.BrdWorkflowHelpDialog";
    static components = { Dialog };
    static props = { close: Function };
}

export class BrdWorkflowHelpField extends Component {
    static template = "era_project_brd.BrdWorkflowHelpField";
    static props = { ...standardFieldProps };

    setup() {
        this.dialog = useService("dialog");
    }

    openHelp() {
        this.dialog.add(BrdWorkflowHelpDialog);
    }
}

export const brdWorkflowHelpField = {
    component: BrdWorkflowHelpField,
    displayName: _t("BRD workflow help"),
    supportedTypes: ["boolean"],
};

registry.category("fields").add("brd_workflow_help", brdWorkflowHelpField);
