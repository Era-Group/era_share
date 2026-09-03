# Setting it up so it answers everything

The goal: a user asks anything, and gets a walkthrough — with nobody recording
tours by hand. This is the arrangement that gets there, and the honest limits
of each part.

## What answers what

**Out of the box, every source may answer.** A freshly installed database
has no agent output and no worker running, so a module that admitted only the
agent would answer nothing at all on its first day. So `answer_source`
defaults to `all`: tours a person recorded by hand answer, and a walkthrough
is assembled from the menu tree this user can reach when nothing else does.

Matching also runs over the agent's own output, which is the agent
remembering its work rather than a competing source: without it the same
question would pay for a fresh multi-minute run every time anyone asked it.

Set `answer_source` to `agent` once a worker runs and has built up answers. A
menu tree says where something is but never how the work is done, and once
real walkthroughs exist, half an answer competing with one of them is worse
than the wait.

| | Answers | Speed |
| --- | --- | --- |
| The agent, first time | Anything | Minutes, once |
| The agent's stored answer | The same question, forever after | Instant |

**Without a worker, answers come from the menu tree.** With no API key the
module still answers: it reads the menus and views the asker can reach and
assembles a route to the right screen. That is a real answer to "where is
this" and no answer at all to "how is this done" — for the second you need a
worker.

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

### 2. Optional: teach it about tours you recorded by hand

Recorded tours answer as soon as they are marked for the assistant — nothing
else has to be switched on. For each one open **Settings → Technical → Tours**
and set:

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
| `era_web_tour_assistant.answer_source` | `all` | `all` — hand-recorded tours and the menu builder answer too. `agent` — only what the agent built may answer |
| `era_web_tour_assistant.match_threshold` | 0.5 | How close a stored answer must be before it is started |
| `era_web_tour_assistant.build_threshold` | 0.6 | How sure the menu builder must be before it builds. Ignored where `answer_source` is `agent` |
| `era_web_tour_assistant.generate_tours` | True | Turn the menu builder off entirely. Ignored where `answer_source` is `agent` |
| `era_web_tour_assistant.worker_running` | False | Set to True once a worker watches the queue — it only changes what a waiting user is told |

`build_threshold` has no effect where `answer_source` is `agent`. Lower it to 0.5 and more questions get a built walkthrough; some of
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
else. Then set `era_web_tour_assistant.worker_running` to `True`, so a waiting
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
