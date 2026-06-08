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
- **Input variables** — `fixed`, `uniform`, `normal`, `triangular` or
  `discrete` distributions, each validated before a run.
- **Simulation engine** — vectorised NumPy sampling over 10,000+ iterations.
- **Results** — summary statistics, percentiles (P5…P95) and the probability of
  reaching a success threshold, explained in business language.

## Requirements

- Odoo 19
- Python package: `numpy` (declared in `external_dependencies`)

Install numpy in the Odoo environment if needed:

```bash
pip install numpy
```

## Installation

1. Copy `era_monte_carlo_simulation` into your addons path.
2. Update the apps list and install **Monte Carlo Simulation**.
3. Open the **Monte Carlo** menu.

## Quick start (revenue forecast)

1. **Monte Carlo → Simulation Models → New**.
2. Name it, keep objective *Revenue Forecast* and formula *Simple Revenue*.
3. In the **Variables** tab add three variables with these exact codes:
   - `leads` — triangular, min 100, mode 180, max 300
   - `conversion_rate` — uniform, min 0.10, max 0.35
   - `average_deal_value` — normal, mean 50000, std dev 12000
4. Click **New Run**, set *Iterations* to 10000 and a *Success Threshold*
   (e.g. 2,000,000), then **Run Simulation**.
5. Read the **What this means** section, the summary statistics and the
   probability of reaching your threshold. Open **Results** for the full
   distribution (list / graph / pivot).

> Install with demo data to get the *Revenue Forecast* and *Product Launch
> Profit* examples preloaded.

## Custom formulas

Set the formula to *Custom (Limited Expression)* and write an arithmetic
expression over the variable codes, for example:

```text
leads * conversion_rate * average_deal_value
```

Expressions are evaluated with Odoo's `safe_eval` over NumPy arrays. Only
arithmetic and a small set of helpers are available — `min`, `max`, `abs`,
`where`, `exp`, `log`, `sqrt` — and no arbitrary Python or server internals.

## Security

- **Monte Carlo / User** — create models, variables and runs; read results.
- **Monte Carlo / Manager** — full access including result management.

## License

LGPL-3
