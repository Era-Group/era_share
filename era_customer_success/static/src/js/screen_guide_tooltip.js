/** @odoo-module **/

function replaceGuides(root = document) {
    const selector = ".o_cs_screen_guide:not([data-tooltip-ready])";
    const guides = root.querySelectorAll ? [...root.querySelectorAll(selector)] : [];
    if (root.matches?.(selector)) {
        guides.unshift(root);
    }
    for (const guide of guides) {
        guide.dataset.tooltipReady = "1";
        const text = guide.innerText
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean)
            .join("\n");
        const icon = document.createElement("span");
        icon.className = "fa fa-question-circle o_cs_screen_guide_help";
        icon.setAttribute("role", "button");
        icon.setAttribute("tabindex", "0");
        icon.setAttribute("aria-label", "دليل الشاشة");
        icon.addEventListener("click", () => openGuide(text));
        icon.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                openGuide(text);
            }
        });
        guide.replaceWith(icon);
    }
}

function openGuide(text) {
    document.querySelector(".o_cs_screen_guide_dialog")?.remove();
    const dialog = document.createElement("dialog");
    dialog.className = "o_cs_screen_guide_dialog";
    dialog.dir = "rtl";
    dialog.innerHTML = `
        <button type="button" class="o_cs_screen_guide_close" aria-label="إغلاق">×</button>
        <div class="o_cs_screen_guide_content"></div>
    `;
    dialog.querySelector(".o_cs_screen_guide_content").textContent = text;
    dialog.querySelector(".o_cs_screen_guide_close").addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (event) => {
        if (event.target === dialog) {
            dialog.close();
        }
    });
    dialog.addEventListener("close", () => dialog.remove());
    document.body.append(dialog);
    dialog.showModal();
}

replaceGuides();
new MutationObserver((mutations) => {
    for (const mutation of mutations) {
        for (const node of mutation.addedNodes) {
            if (node.nodeType === Node.ELEMENT_NODE) {
                replaceGuides(node);
            }
        }
    }
}).observe(document.documentElement, {childList: true, subtree: true});
