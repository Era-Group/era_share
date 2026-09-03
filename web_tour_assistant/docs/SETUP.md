# Setting it up so it answers everything

The goal: a user asks anything, and gets a walkthrough — with nobody recording
tours by hand. This is the arrangement that gets there, and the honest limits
of each part.

## What answers what

**The agent is the only thing that writes a walkthrough.** By default this
module ignores tours a person recorded by hand and will not assemble one from
the menu tree — half an answer from a menu tree undercuts the promise that the
user asks and the system works it out.

Matching still runs, over the agent's own output. That is the agent
remembering its work, not a competing source: without it the same question
would pay for a fresh multi-minute run every time anyone asked it.

| | Answers | Speed |
| --- | --- | --- |
| The agent, first time | Anything | Minutes, once |
| The agent's stored answer | The same question, forever after | Instant |

**Nothing is answered until a worker runs.** With no API key the module
collects questions and answers none of them — that is the direct consequence
of the agent being the only source. See `web_tour_assistant.answer_source`
below to widen it.

**There is no arrangement where an unseen workflow is explained instantly.**
The steps do not exist until something performs the task. What you can remove
is the human, not the minutes.

## "The database already has AI connected"

An AI chatbot inside Odoo is not what powers this. The agent does not answer
from knowledge — it drives a browser and performs the task, then reports what
it clicked. So it needs three things Odoo itself cannot provide:

1. **An API key.** `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, or
   `OPENAI_API_KEY`. A claude.ai or chatgpt.com subscription is a different
   product and will not authenticate it. A key already stored in another Odoo
   module is fine — copy the value, the worker reads it from the environment.
2. **A browser on the machine that runs the worker.** `agent-browser` plus
   Chrome.
3. **A scratch copy of the database.** The agent performs the task for real:
   asking it how to create an order creates an order. The worker refuses to
   start if you point it at the database holding the questions.

## Step by step

### 1. Install

Drop the module on the addons path, then **Apps → clear the "Apps" filter →
search "Tour Assistant" → Install**. Clearing the filter is the part people
miss: this is a technical module, and the Apps list hides those by default.

### 2. Only if you set `answer_source` to `all`

Tours already recorded on the database are ignored by default. To let them
answer as well, set `answer_source` to `all`, then for each tour open
**Settings → Technical → Tours** and set:

- **Offered by the Assistant** — on
- **Answers the Question** — what it teaches, in the words a user would ask
- **Also Matches** — the wording your staff really use, synonyms, and the
  other language. Arabic broken plurals matter here: "فاتورة" and "الفواتير"
  do not reduce to the same stem, so list both
- **Restricted to Groups** — the groups that can actually open those screens.
  Skipping this walks somebody into an access error instead of help

### 3. Tune how boldly it builds

Two system parameters (**Settings → Technical → System Parameters**):

| Parameter | Default | What it does |
| --- | --- | --- |
| `web_tour_assistant.answer_source` | `agent` | `agent` — only what the agent built may answer. `all` — hand-recorded tours and the menu builder answer too |
| `web_tour_assistant.match_threshold` | 0.5 | How close a stored answer must be before it is started |
| `web_tour_assistant.build_threshold` | 0.6 | `all` only: how sure the menu builder must be before it builds |
| `web_tour_assistant.generate_tours` | True | `all` only: turn the menu builder off entirely |
| `web_tour_assistant.worker_running` | False | Set to True once a worker watches the queue — it only changes what a waiting user is told |

`build_threshold` only applies with `answer_source` set to `all`. Lower it to 0.5 and more questions get a built walkthrough; some of
those will point at the wrong screen, because a one-word menu name agrees with
any question containing that word. Raise it and it stays quiet more often. The
completion counters tell you which way you have gone too far.

### 4. Prepare the scratch database

Duplicate the database the questions come from. Give the agent a login on the
copy whose groups match the staff who ask — an agent exploring as an
administrator learns routes an ordinary user cannot walk.

### 5. Run the worker

On a machine that can reach both databases and has the browser:

```bash
export ODOO_URL=https://your-odoo            ODOO_DB=live_database
export ODOO_LOGIN=tour_worker                ODOO_PASSWORD=...
export SCRATCH_URL=http://127.0.0.1:8070     SCRATCH_DB=live_database_copy
export SCRATCH_LOGIN=...                     SCRATCH_PASSWORD=...

export PLANNER=openrouter OPENROUTER_API_KEY=sk-or-...
python3 worker/agent_worker.py --list-models      # pick a model id
export PLANNER_MODEL=<one of those>

python3 worker/agent_worker.py                    # watch the queue
```

`ODOO_LOGIN` is an account in the **Tour Assistant Worker** group and nothing
else. Then set `web_tour_assistant.worker_running` to `True`, so a waiting
user is told a walkthrough is being put together rather than that their
question was written down.

### 6. Watch it, do not trust it

**Settings → Technical → Assistant Questions** is the whole picture: what was
asked, how often, what answered it, and how often that answer was run to the
end. A question asked twenty times whose tour is completed twice has a bad
answer — no survey needed. Tours the agent built are flagged **Built by the
Assistant**; those are the ones to spot-check first.

## What to expect in the first week

Turn the worker on and leave it. Early questions build slowly and some fail —
the failures are recorded with the agent's own reason, and a question it
defeats three times stops being retried and waits for a person. By the end of
the week the common questions are answered instantly from stored tours, and
the queue only holds genuinely new ones.
