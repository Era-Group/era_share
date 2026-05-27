/** @odoo-module **/
/**
 * ERA SEO website-builder snippets — Phase 8.
 *
 * Structured-data injectors for two builder snippets. These run only on the
 * PUBLISHED page (public interactions are not started in the website editor),
 * so the injected <script type="application/ld+json"> is never saved into the
 * page arch — it is rebuilt live and always reflects the current snippet
 * content / URL.
 *
 *   - s_era_faq          -> FAQPage        (from the accordion Q&A)
 *   - s_era_breadcrumbs  -> BreadcrumbList  (from window.location)
 */
import { Interaction } from "@web/public/interaction";
import { registry } from "@web/core/registry";

function injectJsonLd(interaction, data) {
    // Idempotent: never inject twice into the same snippet.
    if (interaction.el.querySelector(":scope > .s_era_jsonld")) {
        return;
    }
    const script = document.createElement("script");
    script.type = "application/ld+json";
    script.className = "s_era_jsonld";
    script.textContent = JSON.stringify(data);
    interaction.el.appendChild(script);
    interaction.registerCleanup(() => script.remove());
}

export class EraFaqSchema extends Interaction {
    static selector = ".s_era_faq";

    start() {
        const entities = [];
        for (const item of this.el.querySelectorAll(".s_era_faq_item")) {
            const q = item.querySelector(".s_era_faq_q");
            const a = item.querySelector(".s_era_faq_a");
            const question = q ? q.textContent.trim() : "";
            const answer = a ? a.textContent.trim() : "";
            if (question && answer) {
                entities.push({
                    "@type": "Question",
                    name: question,
                    acceptedAnswer: { "@type": "Answer", text: answer },
                });
            }
        }
        if (!entities.length) {
            return;
        }
        injectJsonLd(this, {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            mainEntity: entities,
        });
    }
}

export class EraBreadcrumbsSchema extends Interaction {
    static selector = ".s_era_breadcrumbs";

    start() {
        const origin = window.location.origin;
        const path = window.location.pathname.replace(/\/+$/, "");
        const segments = path.split("/").filter(Boolean);

        const crumbs = [{ name: "Home", url: origin + "/" }];
        let acc = "";
        for (const seg of segments) {
            acc += "/" + seg;
            const name = decodeURIComponent(seg)
                .replace(/[-_]+/g, " ")
                .replace(/\b\w/g, (c) => c.toUpperCase());
            crumbs.push({ name, url: origin + acc });
        }

        // Render the visible breadcrumb from the URL chain.
        const ol = this.el.querySelector(".s_era_breadcrumbs_list");
        if (ol) {
            const lis = crumbs.map((c, i) => {
                const li = document.createElement("li");
                const isLast = i === crumbs.length - 1;
                li.className = "breadcrumb-item" + (isLast ? " active" : "");
                if (isLast) {
                    li.setAttribute("aria-current", "page");
                    li.textContent = c.name;
                } else {
                    const a = document.createElement("a");
                    a.href = c.url;
                    a.textContent = c.name;
                    li.appendChild(a);
                }
                return li;
            });
            ol.replaceChildren(...lis);
        }

        injectJsonLd(this, {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            itemListElement: crumbs.map((c, i) => ({
                "@type": "ListItem",
                position: i + 1,
                name: c.name,
                item: c.url,
            })),
        });
    }
}

registry.category("public.interactions").add("era_seo_manager.faq_schema", EraFaqSchema);
registry
    .category("public.interactions")
    .add("era_seo_manager.breadcrumbs_schema", EraBreadcrumbsSchema);
