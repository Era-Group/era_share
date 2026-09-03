# Tour Assistant

An Odoo module that lets a user who does not know how to do something ask,
where they are, and be walked through it.

Every question becomes a record. A published tour that answers it starts
immediately; a question nothing answers is queued instead, ordered by how
often it was asked — so the queue is a ranked list of the walkthroughs worth
recording next, drawn from what staff actually could not work out.

The module knows nothing about any particular business. It matches against
whatever tours the database holds, so the same code serves a hospital, a
contractor and a retailer; only their tours differ.

## Requires

Odoo 19.0. Depends on `web_tour` alone.

## Install

Drop it on the addons path and install it from **Apps** (update the app list
first, since it is a new module). Installing from the UI reloads the registry
in the running server, so no restart is needed.

## Use

**For users.** A life-ring button appears in the top bar. Type what you are
trying to do; if a tour covers it, it starts.

**For administrators.** Each tour grows four fields:

| Field | What it does |
| --- | --- |
| Offered by the Assistant | Off by default. Nothing is matched until this is on, so half-written tours stay hidden. |
| Answers the Question | What the tour teaches, phrased the way a user would ask for it. |
| Also Matches | Extra words that should reach this tour — the wording staff really use, synonyms, the other language. |
| Restricted to Groups | Empty offers it to everyone. Otherwise only these groups see it. Set this: a tour whose starting page a user cannot open hands them an access error instead of help. |

Unanswered questions collect under **Settings → Technical → Assistant
Questions**, for members of *Tour Assistant Manager* (implied by
*Settings*). From a queued question you can record a tour and publish it.

## Matching

Word overlap between the question and the tour's name, description and
keywords, scored as the fraction of the question that was covered, with a
bonus when a keyword is what matched. Below the threshold the question is
queued rather than answered wrongly; the threshold is
`era_web_tour_assistant.match_threshold` in system parameters, 0.5 by default.

Arabic is folded before matching: harakat and tatweel are dropped, أ إ آ ٱ
collapse to ا, ة to ه, ى to ي, and the definite article is stripped. So
"الطلب" and "طلب" reach the same tour and count as one question rather than
two. English plurals fold the same way, and the broken plurals suffix
stripping can never reach — أسعار / سعر, عملاء / عميل, فواتير / فاتورة — are
folded by shape.

Function words go, verbs stay. "كيف", "وين", "show me" say nothing about what
is being asked for; "اطبع", "أنشئ", "create" carry most of the meaning of a
how-do-I question. The one trap is عرض, which on its own is a quotation rather
than a request to see something, so it stays.

## Whether a tour actually helps

`web_tour` reports a finished tour to the server, and the module counts it.
A question asked twenty times whose tour was completed twice is a tour that
does not answer it — visible without asking anyone to rate anything.

## Note on onboarding tours

Odoo discards a manual tour on redirect unless the user has onboarding
enabled, and it is disabled for everyone except administrators — so a tour
someone asked for would never survive the jump to its starting page. The
module turns the setting on for the length of the tour, keeps Odoo's own
onboarding tours from auto-starting over the top of it, and puts the user's
preference back when they finish.

## Building a walkthrough nobody recorded

When nothing matches, the module assembles a tour instead of queueing the
question — the menus the task passes through, and on each one, if the user is
allowed to create there, New, the required fields the form actually shows,
Save, and the header button that moves the record forward.

That last one is read the same way as everything else here: off the form's
arch, with its own display condition evaluated against the values a new record
starts with, including the defaults the action sets. Every header button in
Odoo carries a condition, so a rule that skipped conditional buttons skipped
all of them; and the first button that survives is "Send RFQ", which opens a
mail composer and leaves the walkthrough behind. What is wanted is the click
that finishes the task — Confirm, Validate, Post — so the choice is ranked on
the method name that advances the record, and a screen offering none gets no
step. A form marking nothing required at all gets the field the model names
its records by, which is what a vendor needs and what its arch does not say.

A real question is rarely one screen. "How do I make a manufactured product
from three raw components, each with a value" is three: the raw materials, the
finished product, the bill of materials that ties them together. Answering with
the bill of materials alone is not wrong — it is where the work ends up — but
it drops the user at the last step of a task whose first two steps are the ones
they did not know. So a walkthrough is planned as an ordered set of stages, each
one a screen with a sentence saying what it is for, and each stage's own New,
fields and Save.

A stage that visits a screen rather than filling one still has something to
point at: the record to open. Offered only when that list is counted as
non-empty for the asking user at build time, through whatever domain the
action states — Inventory has pickings and none of them receipts, and counting
the model would have promised a row the Receipts list does not have.

A stage after the first leaves its app before naming the next one: inside an
app Odoo's navbar carries that app's own menus and nothing else, so pointing
straight at another app's root waits forever. The first stage has the same
problem from the other end — people ask while stuck inside the app they are
stuck in, not from the home screen — so starting a walkthrough returns to the
app switcher first and waits for it to render. And a plan too long for one
walkthrough is cut between stages rather than inside one — ending between New
and Save leaves somebody in a half filled form with the pointer gone, which
reads as a fault rather than the end.

Nothing is invented: each trigger is an external id or a field name the
database already holds, so a step points at something that exists by
construction. Nothing is opened or written either — it reads metadata — which
is why it is safe to do on the live database while the user waits.

It is deliberately more cautious than matching a recorded tour, at
`era_web_tour_assistant.build_threshold` (0.6). A one-word menu name agrees
completely with any question containing that word, so "reconcile a bank
statement" would otherwise walk someone into a menu called "Banks"; agreement
is measured in both directions to stop that. Set
`era_web_tour_assistant.generate_tours` to `False` to switch building off and
leave the queue as the only outcome.

Built tours are flagged **Built by the Assistant**. They get someone to the
right screen; a recorded tour still explains the work better, so treat them
as a first answer and a list of what to record properly.

## When word overlap cannot decide

Two things no amount of matching fixes, both measured against a database with
221 modules and 293 menus a user could reach. A question can name several apps
equally well — "اضافة عميل" agrees completely with nine menus across six of
them — and a question can share no word at all with the menu that answers it,
as when somebody asks for a "تذكرة دعم" and the menu is called "مكتب
المساعدة". Both are questions about language.

So a model is asked to plan, never to invent. It is handed the menus this user
can reach in this database and answers with their numbers, in the order the
task passes through them, one sentence per stage and optionally the fields the
task turns on. Everything it says is checked before it becomes a step: a menu
number that was not offered is dropped, and so is a field name the form does
not draw — so a step points at something real because the code guarantees it,
not because the model was careful. Where the matcher already agrees outright
the model is not consulted at all and the answer is immediate.

It also answers when it can only do part of the task. Somebody asking how to
hire an employee and set their salary, on an Odoo with staff records and no
payroll, is walked through creating the employee and told plainly that the
salary needs the payroll app. Half the task and an honest sentence beats a
refusal. Where nothing on the list relates to the question at all, that is
said instead — "تطبيق الحضور غير مثبّت في هذه القاعدة" rather than a queue
entry for a walkthrough that is never coming. Those sentences are kept on the
record, where a column of them across many questions is a purchasing
conversation rather than a backlog.

Because a walkthrough can now span several apps, a generated tour records every
menu it visits and is only offered to somebody who can reach all of them. A
task crossing Purchase, Inventory and Accounting is no use to a viewer who has
one of the three, and would hand them an access error halfway rather than help.

The account comes from `era_ai_accounts`, which fronts a local CLI, an
OpenAI-compatible endpoint and several vendors behind one call — so which
provider answers, and whether it needs an API key at all, is a deployment
decision rather than something this module fixes. Name one in
`era_web_tour_assistant.ai_account`; with none configured the module behaves
exactly as it did before, at no cost.

Measured on the instance this was built against, over every question its users
have asked it — 228 of them, of ten kinds: plain navigation, single records,
multi-screen tasks, dialect, broken plurals, English, apps that are not
installed, vague, adversarial, and compound questions spanning four screens:

| | |
| --- | --- |
| Answered | 199 of 228, no exceptions |
| Refused with a reason | 29 — an app not installed, or a question about the software rather than a task |
| Built in the asker's language | 199 of 199 |
| Longest walkthrough | 30 steps over 4 screens |
| Walked in a browser, every step clicked | 136 of 151 reached their end |

The fifteen that did not are worth naming, since they are the honest edge of
what a walkthrough derived from metadata can promise. Every one of them points
at every field its form declares required — checked by asking the model and
the walkthrough the same question — and still cannot be finished, because the
screen wants something its metadata never says: a mass mailing needs a body no
arch marks required, a leave request needs an allocation before its type can
be chosen, a date range widget offers no box to type in. A walkthrough can
show somebody where the work is done. It cannot invent a rule the database
does not state.

"Delete everything in the system" and "cook me a kabsa" are refused with a
sentence, not answered with a walkthrough. Under a second for a question
somebody has asked before, five to fifteen for a task nobody has, since a plan
crossing three apps is one model call over every menu in the database.

## Building the rest with an agent

Menus and views only answer *where* something is. For *how* the work is done —
a delivery flow, connecting an integration — the steps have to come from
somebody performing the task. `worker/` holds a process that does exactly that:
it takes the most-asked unanswered question, drives a real browser through the
task on a **scratch database**, and hands the steps back over JSON-RPC. It is
optional; without it those questions simply stay in the queue.

Nothing it sends is trusted. `worker_submit` refuses an empty step list, a step
with no trigger, an implausibly long trigger, more than forty steps, a `run`
action outside `click` / `edit` / `hover` / `press`, and any start URL that is
not a path inside this Odoo. A question the agent fails three times stops being
handed out and waits for a person.

The worker logs in as a member of **Tour Assistant Worker**, a group that can
do nothing else. See `worker/README.md`.

## Keeping a second container up to date

The same module on two containers looks identical from every screen, right up
until somebody walks a walkthrough written by the older builder and meets a
fault that was fixed elsewhere weeks ago. Two things make that visible and
fixable:

**Settings › Tour Assistant** names the build this database is running, and how
many stored walkthroughs an older one wrote.

```bash
bash tools/update.sh              # pull, restart, drop what is stale
bash tools/update.sh --rebuild    # and answer those questions again now
```

Three things have to happen together, and doing two is worse than doing none:
the code has to arrive, the server has to restart — the builder is imported
once at start, so files on disk say nothing about what is answering — and the
walkthroughs an older builder wrote have to go. Skip the last and the next
person walks exactly the fault that was just fixed.

## Checking a walkthrough actually works

That every trigger names something the database holds is a guarantee about the
database, not about the screen: a menu can sit behind a group, a field can be
on a tab nobody opened, a button can be absent until a row is selected. Such a
step resolves perfectly in SQL and leaves the user staring at a pointer aimed
at nothing.

`worker/verify_tours.py` follows each generated walkthrough in a real browser
and reports the steps that never appear. It writes nothing to Odoo — but
following a walkthrough clicks what it points at, so it belongs on a scratch
copy.

With `--fill` it does more than look: it puts a value in each field the
walkthrough points at and presses Save, which is the difference between
measuring that the steps appear and measuring that the task can be finished.
A link field is chosen from its dropdown rather than typed into — free text
matching no record leaves the form refusing to save, and every walkthrough
that filled one was reported broken at its Save step. It also insists on a
clean home screen between walkthroughs: a walk ending in a form with unsaved
changes raises the discard prompt, the next navigation does not happen, and
the following walkthrough is then measured against the wrong page.

```bash
export SCRATCH_URL=http://127.0.0.1:8070 SCRATCH_DB=scratch
export SCRATCH_LOGIN=... SCRATCH_PASSWORD=...
python3 worker/verify_tours.py
```

## Tests

```bash
odoo-bin -d <database> -u era_web_tour_assistant --test-enable \
         --test-tags /era_web_tour_assistant --stop-after-init
```

They cover the matching, the Arabic folding and broken plurals, which parts of
a menu tree can be clicked, which fields a form really draws, how a plan of
stages is read and what is dropped out of it, which screens offer a New button,
and what a user is told when there is no answer.

## Author

Ahmed Aqlan, Era Group.

## Licence

LGPL-3.
