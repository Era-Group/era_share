/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component } from "@odoo/owl";

export class BrdWorkflowHelpField extends Component {
    static template = "era_project_brd.BrdWorkflowHelpField";
    static props = { ...standardFieldProps };
}

export const brdWorkflowHelpField = {
    component: BrdWorkflowHelpField,
    displayName: _t("BRD workflow help"),
    supportedTypes: ["boolean"],
};

registry.category("fields").add("brd_workflow_help", brdWorkflowHelpField);
