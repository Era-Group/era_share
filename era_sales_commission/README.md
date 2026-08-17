# Era Sales Commissions

Sales representative commissions, from what earns them to the journal entry --
or the payslip -- that pays them, in one module.

## What it does

- **Agents** (`era.commission.agent`) — an employee, an outside representative, or
  a manager who earns on what their team sells. A separate model, not
  `res.users`: an outside rep who never logs in still has to be paid.
- **Agent rates** (`era.commission.agent.rate`) — **what each agent earns, per
  commission type**: a percentage for a commission on sales or on collection, a
  unit price for a commission on quantity, optionally narrowed to one product.
  Any figure: nothing constrains the rate to a blessed list.
- **Plans** (`era.commission.plan`) — *when* a commission is earned:
  - `order` — the sales order is confirmed;
  - `sales` — the invoice is posted;
  - `collection` — **the money is actually collected**, in proportion to each
    payment;
  - `qty_sold` — the net units invoiced over the period;
  - `qty_collected` — the same units, in proportion to what was paid;
  - `margin` — the invoiced amount less the cost of the goods.
- **Rules** (`era.commission.rule`) — the older, richer way of saying *how much*:
  tiers, a fixed amount per unit or per document, a minimum and a cap, filtered
  by product, category, tag, customer, country, region, team or agent. Still
  supported, and still useful for anything a single percentage cannot express.
- **Unit price tiers** (`era.commission.unit.price.tier`) — what one unit earns,
  by how many of it were sold.
- **Targets** (`era.commission.target`) — central: they belong to an agent and a
  commission type, not to a plan.
- **Overrides** — every manager above an agent earns on what their team sold.
- **Settlements** (`era.commission.settlement`) — the document an agent is paid
  on: computed, approved by a Commission Manager, posted as a journal entry or a
  vendor bill, optionally pushed to a payslip, and printed as a bilingual AR/EN
  statement.

The menu lives under **Sales ▸ Commissions**. It is deliberately not an app of
its own: the people who run commissions already live in the Sales menu.

## The five commission types

| Type | Earned on | Rate read from |
|---|---|---|
| `sales` | posted invoices, less credit notes | `rate` on the agent |
| `collection` | money matched against those invoices | `rate` on the agent |
| `qty_sold` | net units invoiced over the period | `unit_price` |
| `qty_collected` | those units, times what was paid | `unit_price` |
| `adjustment` | nothing — a human decided it | `manual_amount` |

## The formula

Every commission line computes its own amount. Correcting the percentage on the
line is enough to correct the money; there is no need to re-run the engine.

```
net   = |base| - |tax deducted| - this line's share of the target      (never < 0)
        with the sign of the base kept, so a credit note stays negative

amount = manual_amount                       for an adjustment
       = (quantity - target qty) x unit_price for a quantity commission
       = rule(net, quantity, rate)            when a rule matched
       = net x rate / 100                     otherwise
```

### The four cases the business wrote down

| # | Setup | Result |
|---|---|---|
| 1 | `sales`, tax deducted, target deducted, agent rate 2% | `(100 000 − 10 000 returned − 50 000 target − tax) × 2%` |
| 2 | `collection`, tax deducted, no target, two reps at 2% and 2.5% | each rep on what they collected |
| 3 | `collection`, tax deducted, target deducted, 2% | `(100 000 collected − 50 000 target − tax) × 2%` |
| 4 | `qty_sold`, unit price 0.50 | `(15 000 − 1 000 returned) × 0.50 = 7 000` |

### Tax

With **Deduct Tax** on, the base is collected **with the tax in it** and the tax
is stored beside it, so `base − tax` is literally the net the rate applies to.
Collecting an untaxed base and then subtracting the tax would deduct it twice.

Two methods: *actual* takes the tax the invoice really carries, line by line;
*divide* takes `base − base / (1 + r)` with `r` the **Commission Tax Rate** set on
the company (15% by default, in Settings, not in the code). No tax is ever taken
off a quantity commission: a unit price is not an amount.

### Targets: deducted **or** a factor, never both

A plan reads its target one of two ways, set by **Target Mode**:

- **Deduct** (the default): the target is taken off the base before the rate,
  spread over the lines of the period in proportion to their base
  (`target × base(line) / Σ base`). The total of the statement is then exactly
  `(Σ base − target) × rate`, and every document stays traceable on its own.
- **Factor**: the achievement percentage picks a tier and the whole commission of
  the period is multiplied by it.

Running both would charge the same shortfall twice, so the plan picks one.

### Returns, and why zero is not negative

Two rules from the business meet here. *A line that falls below its share of the
target earns nothing* — so a positive line clamps at zero. *A credit note takes
back what the sale paid* — so a return stays negative for its own size. Both hold
because the clamp is applied to the size and the sign is put back afterwards.

For a quantity commission the rule is stated on the period, not the line: a
product whose returns outweigh its sales over the period counts as **zero units**,
never as a negative commission.

### Unit price, in order

1. `era.commission.unit.price.tier` matching the **net** quantity of the product
   over the period;
2. `unit_price` on the agent, for that product;
3. `unit_price` on the agent, for everything;
4. `commission_rate_per_unit` on the product;
5. zero — and the officer types it on the line.

The tier is read on the net quantity of the *period*, which is why a quantity
commission is **one line per agent, product and period**, not one per invoice
line: the slice cannot be known before the period is added up. Generating two
overlapping periods therefore produces two lines, so the wizard and the
settlements always work on whole periods.

### `era_collection_ratio` is an estimate

`(amount_total − amount_residual) / amount_total` of the whole invoice, clamped to
`[0, 1]`. Money is paid against a document, not against one product inside it, so
there is no honest way to follow a single line through a payment. The figure is
shown on the line so nobody mistakes it for a measurement.

## The two rules that make it auditable

**First match wins.** The rules of a plan are read in `sequence` order and the
first one that matches a line is the one applied. They are never added up on the
same line. The rate on the **agent** wins over the rate of a matching rule; a
rule computing on tiers, on a fixed amount or under a cap keeps the last word,
because no single percentage could stand in for it.

**Approved means frozen.** A recomputation only ever touches `draft` lines. A
line that is `confirmed` or `settled` is what an agent was paid and what a
printed statement shows; it is never rewritten — not even by editing the rule it
fell under afterwards, which the computation reads back rather than recomputes.
When the document behind it is cancelled, or a collection is un-reconciled, the
engine writes a negative `reversal` line dated today. A claw-back is a document,
not a silent edit.

Both are backed by `origin_key`: every line carries a key built from its plan,
rule, type, source record and agent, unique per company. Running the engine twice
over the same period updates the draft lines it already produced.

## How commission on collection is computed

For every `account.partial.reconcile` whose `max_date` falls in the period, the
engine takes the side that is a customer invoice or credit note — but only when
the *other* side is not. A credit note reconciled against an invoice moves no
cash, and counting both sides of it would pay the commission twice.

```
ratio    = partial.amount / |invoice.amount_total_signed|
base(l)  = gross(l) x ratio      for each product line l
date     = partial.max_date
```

When the **payment** names a commission agent, that agent is the one credited
instead of the agent of the invoice — that is how a rep who collects someone
else's invoice gets paid for collecting it.

## Payroll (optional)

`Push to Payroll` on an approved settlement writes **one** `hr.payslip.input` of
type `EXT_COMM` on a draft payslip of the agent's employee, carrying
`era_rep_total_commission` — the net owed, all commission types together, after
the achievement factor and the bonuses and deductions. A salary rule of the same
code reads it as an allowance.

It is a button, never a hook on `action_post`: paying an agent through payroll
and paying them through the accounts are two different decisions and a company
makes only one of them. Pushing twice is refused rather than doubled, and a
pushed settlement refuses to be cancelled until the input is removed by hand.

The rule ships on the country-less `hr_payroll.structure_002` ("Regular Pay")
because the Saudi structures come from a localization module. **If you install
`l10n_sa_hr_payroll` later, copy the `EXT_COMM` rule onto the structure it
ships** — nothing does that for you.

## Computation is a batch, never a hook

Nothing is computed inside `action_confirm`, `_post` or bank reconciliation. It
runs from the daily scheduled action, from the **Generate Settlements** wizard,
or from the **Compute** button on a settlement. A bad commission rule must never
be able to block the posting of an invoice, and every line has to stay
reproducible from the source documents alone.

**Purchases are out of scope.** No commission is ever earned on a
`purchase.order`, by decision of the business. There is no field, no rule and no
code path touching it.

## Configuration

Settings ▸ Sales Commissions:

| Setting | Used for |
|---|---|
| Default Payout | journal entry (employee) or vendor bill (outside agent) |
| Commission Journal | where the settlement entry is posted |
| Commission Expense Account | debited when a settlement is posted |
| Commission Payable Account | credited when a settlement is posted |
| Commission Tax Rate | the divisor of the *divide* tax method |
| Default Plan | proposed for a new agent |
| Daily Recomputation | turn the nightly scheduled action off |

No account is hardcoded. `action_post` refuses to run and names what is missing.

Targets are entered under **Sales ▸ Commissions ▸ Targets**, in an editable list.
There is no dedicated `.xlsx` importer: Odoo's standard CSV/XLSX import works on
the model as it stands.

## Security

| Group | Can |
|---|---|
| Agent | read their own lines, statements, rates and targets — nothing of anyone else |
| Commission Officer | compute, prepare settlements, correct rates on lines, record bonuses and deductions |
| Commission Manager | write plans, rules and agent rates, set targets, approve, post, push to payroll, delete |

The "own records" rules are paired with an officer rule of `[(1, '=', 1)]`
because Odoo ORs the rules of the groups a user belongs to.

Because the menu now hangs under Sales, a commission agent also needs the Sales
user group to see it — the Sales root menu is what carries them.

## The monthly run

1. **Generate Settlements** for the period — pick a commission type to settle one
   kind on its own, or leave it empty for all of them. The tax and target
   switches on the wizard override what the plans say for that run.
2. Review, correct a rate on a line if it is wrong, add bonuses and deductions,
   **Send for Approval**.
3. A Commission Manager **Approves** — the lines freeze — then **Posts**, or
   **Pushes to Payroll**.
4. **Register Payment**, and **Print Statement** for the agent.

## Tests

```
odoo-bin -c odoo.conf -d <db> -u era_sales_commission \
  --test-enable --test-tags /era_sales_commission --stop-after-init
```

`tests/` covers the rule arithmetic (percentage, fixed, tiers, cap, exclusions,
splits), the four cases of the business with their own numbers and both tax
methods, quantity commissions (net units, collected units, the four sources of a
unit price, a negative net), collection (partial payments, a second payment, the
agent on the payment, credit-note matching, idempotency), targets in both modes,
the settlement cycle including the accounting entry and the claw-back, the
payroll push, the printed statement, and the access rights.

---

Era Group · <https://era.net.sa> · info@era.net.sa · LGPL-3
