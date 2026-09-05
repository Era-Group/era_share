/**
 * The statute reference, opened by name rather than by icon.
 *
 * It goes through the same launcher the systray button uses, so the chat
 * arrives the way every other AI chat in Odoo does — with the composer's
 * standing instructions and its starter questions. The only thing that differs
 * is the key it announces, and that key belongs to the restricted agent.
 *
 * No record is sent, deliberately: this door is the reference, and a reference
 * that reads the file you happen to have open is no longer one.
 */
import { registry } from "@web/core/registry";

const CORPUS_KEY = "era_legal_corpus";

registry.category("actions").add("era_law_firm_ai.legal_research_chat", (env) => {
    env.services.aiChatLauncher.launchAIChat({ callerComponentName: CORPUS_KEY });
});
