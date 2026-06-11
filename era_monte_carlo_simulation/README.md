# Monte Carlo Simulation

Model uncertainty and run Monte Carlo simulations inside Odoo 19. Instead of a
single fixed estimate, describe each uncertain input with a probability
distribution, run thousands of scenarios, and read the result in plain business
language.

## What it does

- **Simulation models** — a business question with a predefined or custom
  formula (`Revenue = Leads × Conversion Rate × Average Deal Value`,
  `Profit = Units × (Unit Price − Unit Cost) − Fixed Cost`, or a safe custom
  expression).
- **Input variables** — `fixed`, `uniform`, `normal`, `log-normal`,
  `triangular` or `discrete` distributions, each validated before a run.
  Parameters can be **fitted from your own Odoo data** ("Refresh from data":
  sample a field, count records per period, or a per-period ratio such as a
  conversion rate) or estimated through **Ask AI**.
- **Input correlations** — declare that two inputs move together (e.g. price
  vs. demand); the engine reproduces the requested Spearman correlation with
  the Iman–Conover method without changing any input's own distribution.
- **Simulation engine** — vectorised NumPy sampling, up to 1,000,000
  iterations, reproducible with an optional random seed.
- **Results** — summary statistics, percentiles (P5…P95), threshold
  probabilities, tail risk (VaR 95% / CVaR 95%), per-input sensitivity
  (tornado chart), a distribution chart (histogram + cumulative curve) and a
  plain-language interpretation. Optional **AI narrative** through the
  configurable "Monte Carlo Narrator" agent.
- **Boards & exports** — scenario comparison board (overlaid CDF curves),
  manager risk dashboard (KPIs + heatmap, riskiest first), PDF report and
  Excel export.
- **CRM bridge** — a one-click "reality check" on each opportunity: the share
  of comparable confirmed sale orders (by product, then similar size) that
  reach the quoted value.
- **Storage control** — keep every scenario row, a representative sample, or
  summary statistics only; CRM one-click forecasts are summary-only and
  cleaned up daily.

## Requirements

- Odoo 19 with `crm` and `sale_crm` (the CRM value forecast reads confirmed
  sale orders)
- Python packages: `numpy`, `xlsxwriter` (declared in
  `external_dependencies`; Odoo 19 core already pins XlsxWriter)

```bash
pip install numpy xlsxwriter
```

## Installation

1. Copy `era_monte_carlo_simulation` into your addons path.
2. Update the apps list and install **ERA Monte Carlo Simulation**.
3. Open the **Monte Carlo** menu. Seven ready-made example models (one per
   objective, in Arabic) are loaded on install; delete any you do not need —
   they stay deleted across upgrades.

## Quick start (revenue forecast)

1. **Monte Carlo → Simulation Models → New**.
2. Name it, keep objective *Revenue Forecast* and formula *Simple Revenue*.
3. In the **Variables** tab add three variables with these exact codes:
   - `leads` — triangular, min 100, mode 180, max 300
   - `conversion_rate` — uniform, min 0.10, max 0.35
   - `average_deal_value` — log-normal, mean 50000, std dev 12000
4. Click **New Run**, set *Iterations* to 10000 and a *Success Threshold*
   (e.g. 2,000,000), then **Run Simulation**.
5. Read the **What this means** section, the charts, the tail-risk figures and
   the probability of reaching your threshold. Open **Results** for the raw
   scenario rows (list / graph / pivot).

## Custom formulas

Set the formula to *Custom (Limited Expression)* and write an arithmetic
expression over the variable codes, for example:

```text
leads * conversion_rate * average_deal_value
```

Expressions are evaluated with Odoo's `safe_eval` over NumPy arrays. Only
arithmetic and a small set of helpers are available — `min`, `max`, `abs`,
`where`, `exp`, `log`, `sqrt` — no loops, comprehensions, bitwise operators,
huge constant exponents, or arbitrary Python.

## Security

- **Monte Carlo / User** — create and edit models, variables and runs; read
  results. Cannot delete models or runs.
- **Monte Carlo / Manager** — full access including deletion and result
  management.
- Multi-company record rules isolate models, runs, results, variables and
  correlations per company.

## Configuration parameters

- `era_monte_carlo_simulation.ai_auto` — enable/disable background AI
  summaries (default on).
- `era_monte_carlo_simulation.mc_growth_pct` — yearly growth uplift applied to
  historical deal values in the CRM forecast (default 7).
- `era_monte_carlo_simulation.narrator_agent_id` — the dedicated AI agent used
  for run narration (created automatically; edit the agent in the AI app to
  switch provider).

## Tests

```bash
odoo-bin -d <db> --test-tags /era_monte_carlo_simulation --stop-after-init
```

## License

LGPL-3
