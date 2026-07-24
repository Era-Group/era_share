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
    const isRtl = document.documentElement.dir === "rtl"
        || document.documentElement.lang.startsWith("ar")
        || /[\u0600-\u06FF]/.test(text);
    if (isRtl && !document.documentElement.dir) {
        document.documentElement.dir = "rtl";
    }
    dialog.dir = isRtl ? "rtl" : "ltr";
    dialog.classList.toggle("o_cs_screen_guide_dialog_rtl", isRtl);
    if (isRtl) {
        dialog.style.direction = "rtl";
    }
    const lines = text.split("\n").filter(Boolean);
    const [intro, section, ...details] = lines;
    const startIndex = details.findIndex(
        (line) => line.startsWith("Start here:") || line.startsWith("ابدأ من هنا:")
    );
    const uses = startIndex === -1 ? details : details.slice(0, startIndex);
    const start = startIndex === -1 ? "" : details.slice(startIndex).join(" ");
    dialog.innerHTML = `
        <button type="button" class="o_cs_screen_guide_close" aria-label="إغلاق">×</button>
        <div class="o_cs_screen_guide_content">
            <p class="o_cs_screen_guide_intro"></p>
            <h3 class="o_cs_screen_guide_section"></h3>
            <ul class="o_cs_screen_guide_list"></ul>
            <p class="o_cs_screen_guide_start"></p>
        </div>
    `;
    dialog.querySelector(".o_cs_screen_guide_intro").textContent = intro || "";
    dialog.querySelector(".o_cs_screen_guide_section").textContent = section || "";
    for (const line of uses) {
        const item = document.createElement("li");
        item.textContent = line.replace(/^[-•]\s*/, "");
        dialog.querySelector(".o_cs_screen_guide_list").append(item);
    }
    dialog.querySelector(".o_cs_screen_guide_start").textContent = start.replace(/^Start here:\s*/, "ابدأ من هنا: ");
    if (isRtl) {
        for (const element of dialog.querySelectorAll(".o_cs_screen_guide_content, .o_cs_screen_guide_intro, .o_cs_screen_guide_section, .o_cs_screen_guide_list, .o_cs_screen_guide_start")) {
            element.style.setProperty("direction", "rtl", "important");
            element.style.setProperty("text-align", "right", "important");
        }
        const list = dialog.querySelector(".o_cs_screen_guide_list");
        list.style.setProperty("padding-right", "1.4rem", "important");
        list.style.setProperty("padding-left", "0", "important");
    }
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
