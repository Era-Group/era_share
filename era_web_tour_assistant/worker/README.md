# The agent worker

Builds a walkthrough by performing the task in a real browser, then hands the
steps back to Odoo. It runs **outside** Odoo: a request cannot hold a browser
open for the minutes this takes, and the agent does not describe the task — it
carries it out, so asking it "how do I create an order" creates an order.

That is the whole reason for the split. The questions live in your live
database; the exploring happens on a scratch copy. The worker refuses to start
if you point both at the same database.

## What it needs

| Variable | What it is |
| --- | --- |
| `ODOO_URL`, `ODOO_DB` | Where questions are queued — your live instance |
| `ODOO_LOGIN`, `ODOO_PASSWORD` | An account in the **Tour Assistant Worker** group and nothing else |
| `SCRATCH_URL`, `SCRATCH_DB` | A throwaway copy the agent may break |
| `SCRATCH_LOGIN`, `SCRATCH_PASSWORD` | Who the agent explores as. Give it the same groups as the staff who ask, or it will learn a route they cannot walk |
| `AGENT_BROWSER` | Path to the `agent-browser` binary, if not on `PATH` |

## Choosing a planner

**A subscription is not an API key.** claude.ai and chatgpt.com are separate
products from the APIs, with separate billing — a Claude or ChatGPT plan does
not authenticate this worker. Create a key at `console.anthropic.com`,
`platform.openai.com`, or `openrouter.ai`.

OpenRouter is the default because one key reaches both vendors, so you can
compare models without a second account.

| `PLANNER` | Key | Notes |
| --- | --- | --- |
| `openrouter` (default) | `OPENROUTER_API_KEY` | Both vendors behind one key. Optional `OPENROUTER_REFERER` for attribution |
| `anthropic` | `ANTHROPIC_API_KEY` | Direct, via the official SDK (`pip install anthropic`) |
| `openai` | `OPENAI_API_KEY` | Direct. `OPENAI_BASE_URL` for a compatible gateway |

`PLANNER_MODEL` names the model. Ids differ per provider and change as models
come and go, so ask rather than guess:

```bash
export PLANNER=openrouter OPENROUTER_API_KEY=sk-or-...
python3 agent_worker.py --list-models    # prints anthropic/* and openai/*
export PLANNER_MODEL=<one of those>

python3 agent_worker.py --once           # take one question and stop
python3 agent_worker.py                  # keep watching the queue
```

Replies are parsed tolerantly — a JSON object wrapped in a markdown fence or
padded with prose still works, which OpenAI-compatible endpoints do to varying
degrees. A reply with no JSON in it at all is treated as a failed attempt.

## How it decides

One action at a time. It is shown the page, proposes a single click or fill as
a **CSS selector**, and only after that selector has been acted on successfully
is the step recorded. A selector that matches nothing is skipped rather than
written down, so a published walkthrough never contains a trigger that was
never proven to resolve against a real page.

The model is asked for selectors that survive a different database and a
different theme — `[data-menu-xmlid="…"]`, `[name="…"]`, a button's own class —
rather than anything built from record ids or row positions.

## What Odoo refuses

The worker is a separate process, so nothing it sends is trusted. Before any
step becomes a tour a real user will follow, `worker_submit` rejects: an empty
step list, a step with no trigger, an implausibly long trigger, more than forty
steps, a `run` action outside `click` / `edit` / `hover` / `press`, and any
start URL that is not a path inside this Odoo. A question the agent fails three
times stops being handed out and stays in the queue for a person to record.

## Swapping the planner

`ScriptedPlanner` replays a fixed list of actions instead of calling the model.
It exists so the browser and submission halves can be exercised without
spending a model call — which is how this worker was tested.
