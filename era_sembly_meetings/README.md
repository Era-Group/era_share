# ERA Sembly.ai Meetings

Pulls Sembly.ai meetings into Odoo, links each one to the record it is about,
and posts the latest meeting's summary into that record's chatter.

## Four modules

This app is the **base**: it owns the meeting, the two Sembly channels, the AI
matching machinery, the security model and the chatter posting — and it links a
meeting to **nothing**. It depends on `base`, `mail` and `ai` only, so it
installs on any database.

Every link target is a separate module that plugs into the seams below. Each is
`auto_install`, so it appears by itself once both this app and its own app are
installed, and can be uninstalled without the others noticing.

| Module | Adds | Depends on |
|---|---|---|
| `era_sembly_meetings` | the meeting, MCP + webhook, AI plumbing, chatter, menus, crons | `base`, `mail`, `ai` |
| `era_sembly_meetings_crm` | `lead_id` | + `crm` |
| `era_sembly_meetings_tasks` | `project_id`, `task_id`, the `الاجتماعات` bucket task | + `project` |
| `era_sembly_meetings_tickets` | `ticket_id` | + `helpdesk` |

### The seams

All six live on `sembly.meeting` in `models/sembly_meeting.py`, and a link
module implements only the ones it needs:

| Seam | Returns | Used for |
|---|---|---|
| `_sembly_link_fields` | `set` of field names | a hand edit of one of these sets `link_state = 'manual'` |
| `_ai_candidate_pools` | `list` of pool dicts | the candidate blocks put in front of the model |
| `_ai_postprocess_links` | the links dict | deriving one link from another (a task implies its project) |
| `_ai_after_link` | — | side effects of a successful **automatic** link |
| `_has_external_link` | `bool` | "something more specific already claimed this meeting" |
| `_summary_targets` | `list` of records | who receives the chatter note |

A **pool** is `{'key', 'label', 'records', 'render', 'sequence', 'basis'}`.
`records` is doubly load-bearing: it is both what the model is shown and the
only set it may choose from, so a pool can never let the model link to
something it never saw. `sequence` fixes the prompt block order, so the prompt
does not change shape with the module install order.

Two rules keep the modules independent of one another, and **both are load
bearing**:

- **Views may only xpath onto an anchor the base declares** — never onto an
  element a sibling contributes. Odoo drops the inheriting views of modules it
  is not loading, and a missing anchor is a hard `ValidationError`, not a
  silent no-op. The base marks each anchor with an `ANCHOR:` comment, and they
  are all `position="inside"` or `position="before"`, which preserve order
  (`position="after"` reverses it).
- **No satellite ships a record rule.** Employees read every meeting (see
  *Permissions*), so a per-module rule would be dead weight. If visibility is
  ever narrowed again, the mechanism is one non-global `ir.rule` per module for
  `group_sembly_user`: Odoo OR-s together every rule matching the user's
  groups, so each one *widens*. A satellite rule must never have empty
  `groups` — a global rule is AND-ed and would shrink everyone's visibility.

## Permissions

Two groups, differing along both axes at once. Every internal user is an
employee (`base.group_user` implies `group_sembly_user`); managers are granted
deliberately, since that group also unlocks the token.

| | Employee | Manager |
|---|---|---|
| Which meetings | **all of them** | all of them |
| Link fields (opportunity / task / ticket) | may set | may set |
| Content — title, times, participants, summary, transcript, items | read-only | may edit |
| Create, delete | ✗ | ✓ |
| Settings, import wizard, sync log | ✗ | ✓ |

Employees read everything on purpose: a meeting is only useful once whoever
recognises what it was about can find it, and that is rarely the person who
attended or imported it.

The three restrictions are enforced in three different places, because no
single Odoo mechanism expresses all of them, and **all three are server-side**
(rule 19):

| Restriction | Enforced by |
|---|---|
| create / delete | `security/ir.model.access.csv` — plain ACL columns |
| which records | `security/sembly_record_rules.xml` — the record rule |
| which **fields** | `sembly.meeting._check_content_access` |

The last one is the interesting case: an ACL is per *model*, so it cannot say
"may write `lead_id` but not `summary`". `CONTENT_FIELDS` in
`models/sembly_meeting.py` is therefore a **deny** list, not an allow list —
which means a link field added by a new satellite is writable by employees
without that module registering anything, while everything the meeting
factually *is* stays manager-only. `sudo` is exempt, because the two Sembly
channels, the matcher and the crons are precisely what is supposed to write the
content.

`can_edit_content` drives the `readonly=` attributes in the form. It only
mirrors the server check — a readonly attribute is a rendering hint any RPC
client can ignore. It carries `@api.depends_context('uid')`, without which
Odoo's per-transaction field cache lets the first reader decide the answer for
everyone else.

## Upgrading a pre-split database

`era_sembly_meetings` is `19.0.1.1.0` and ships
`migrations/19.0.1.1.0/pre-migrate.py`. **A plain `-u` cannot survive the split
without it, and every failure is silent — all of them exit 0:**

- `auto_install` is only consulted by `button_install`, which `-u` never calls,
  so no satellite installs. `lead_id` / `project_id` / `task_id` become orphan
  xmlids of the base, and `_process_end` → `ir.model.fields.unlink()` runs
  `ALTER TABLE … DROP COLUMN … CASCADE`. **Every link a human ever made is
  gone**, and nothing re-creates it: the cron only revisits `unlinked`, and the
  matcher skips `manual`.
- The record rule ships `noupdate`, and `convert.py` skips noupdate records
  unless the module is being *installed*, so the pre-split domain referencing
  `lead_id.user_id` survives the column being dropped. Every non-manager then
  gets `ValueError: Invalid field sembly.meeting.lead_id` on any search — the
  whole app. Managers are unaffected, so it is invisible to anyone testing as
  admin. Flipping `noupdate="0"` would not help: the stored flag is never
  rewritten by the XML upsert.
- The renamed `era_sembly_meetings_integration` is never reaped — `update_list()`
  only walks manifests that exist on disk — so its views stay active, xpath-ing
  at `//field[@name='task_id']`, which the rewritten base no longer offers. The
  meeting form and list stop opening.

The migration moves the old bridge's records onto `…_tickets` (identical
xmlids, so its data load overwrites them), re-owns the moved field xmlids so
nothing is ever an orphan, marks each satellite `to install` when its app is
present, and force-writes the employee rule. The two bucket-task settings are
seeded by `_sembly_tasks_post_init` **only when absent**, rather than by a data
file, so a tuned value is never overwritten and the unique index on
`ir_config_parameter.key` is never hit.

Verified end to end against a database built from the pre-split commit and
seeded with real links: after `-u era_sembly_meetings`, all four modules are
installed, the four links and both tuned settings are intact, no orphan records
remain, and every view still renders.

## Why two channels

Sembly exposes meeting data two ways, and **neither one alone satisfies the
requirements**. They are merged onto a single `sembly.meeting` record keyed by
the Sembly meeting id, so arrival order does not matter.

| | MCP (`mcp.sembly.ai`) | Custom Automation webhook |
|---|---|---|
| Metadata (title, times, duration, platform, owner) | ✅ | ✅ |
| Participants | ✅ names | ✅ **emails** |
| Summary | ✅ `minutes[]` | ✅ `meeting_notes` |
| Decisions / issues / risks / requirements / highlights | ✅ | ❌ |
| **Transcript** | ❌ | ✅ |
| **Recording link** | ❌ | ✅ |
| Historical backfill | ✅ | ❌ future meetings only |
| On-demand re-fetch | ✅ | ❌ |
| Needs a public Odoo URL | ❌ | ✅ |

MCP is the primary channel; the webhook fills in the transcript, the recording
link and participant emails.

Verified against the live server (version 2.13.3): `tools/list` returns exactly
`list_meetings`, `get_meeting`, `list_tasks`, and their declared output schemas
contain no transcript field and no media URL.

### Two things the live server does that the handshake frames do not

Both were found only when the first real import ran, because the small frames
(`initialize`, `tools/list`) are pure ASCII and hide them. **Do not "simplify"
either of the fixes.**

- **The body is UTF-8 but declares no charset.** Sembly answers
  `Content-Type: text/event-stream` with no `charset=`, and for a charset-less
  `text/*` requests falls back to ISO-8859-1. Arabic then arrives as mojibake —
  and worse, **م** (one of the commonest Arabic letters) is `D9 85` in UTF-8, so
  the `85` decodes to U+0085 NEL, which `str.splitlines()` treats as a line
  break. A single 132 KB `data:` line shattered into 480 fragments, none of
  which parsed, surfacing as *"Could not parse Sembly MCP response"*. Hence
  `_body_text` decodes `response.content` itself, and `_parse_sse` splits on
  `_SSE_LINE_SPLIT` (CR / LF / CRLF only) rather than `str.splitlines()`.
  One Arabic meeting title anywhere in the workspace was enough to break the
  whole import.
- **Datetimes carry a timezone label:** `"2026-08-10 12:30 (UTC)"`. Neither
  `fromisoformat` nor any `strptime` format accepts the suffix, so `_parse_dt`
  strips it. Without that the date is dropped silently and the meeting lands
  with no `started_at`, which also removes it from the chatter window.

`tests/fixtures.py` now carries an Arabic title and the `(UTC)` suffix, and
`FakeResponse` reproduces the missing charset, so a regression fails the suite
instead of production.

## Setup

1. **Token.** Settings → Sembly → *رمز MCP*. Created in Sembly under
   *My Automations → MCP*; requires a PRO/MAX/ENTERPRISE workspace with MCP
   enabled by a Workspace Admin. The `SEMBLY_MCP_TOKEN` environment variable
   takes precedence over the stored value and keeps the secret out of the
   database entirely. The stored value is write-only — the field shows a mask.
2. **Webhook.** Copy the URL shown on the same settings page into Sembly:
   *My/Workspace Automations → Custom → New Automation*. Create **two**
   automations, one for **Transcription** and one for **Notes**, both pointing
   at that URL, rule *Apply to all meetings*. Authentication is the unguessable
   path token — Sembly's automations send no signature header. (A
   `_verify_signature` hook is in place should Sembly ever add HMAC signing.)
3. **Enable the crons.** The three crons that make a network call or spend AI
   budget ship **disabled** on purpose, so installing the module cannot fire an
   unauthenticated request or burn credit before a human turns them on:
   - `Sembly: مزامنة الاجتماعات (MCP)` — 30 min
   - `Sembly: ربط الاجتماعات بالذكاء الاصطناعي` — 15 min
   - `Sembly: نشر ملخص آخر اجتماع` — 1 h

   `Sembly: تنظيف السجلات` is local housekeeping and is active from install.
4. **Backfill.** الاجتماعات → الإعدادات → استيراد من Sembly. The webhook only
   ever sees future meetings, so this is the only way to bring history in.

## Go-live checklist

Run these four in order. Nothing before step 1 reaches Sembly, so the module is
inert until the token is entered.

1. Enter the MCP token — Settings → Sembly → *رمز MCP* (or set `SEMBLY_MCP_TOKEN`).
2. Press *اختبار الاتصال* and confirm it reports success.
3. Run الاجتماعات → الإعدادات → استيراد من Sembly once, to pull history in.
4. Enable the three network/AI crons listed in *Setup* step 3.

On a staging instance restored from production, note that Odoo's database
neutralisation disables **every** cron, including `Sembly: تنظيف السجلات`; step 4
is what turns the integration back on there.

## Linking

Candidates are narrowed **deterministically first**, by each link module for
its own model — by participant partners (and their commercial parents), by
significant title tokens, and by the ambient set of open records — then capped:
60 leads, 50 projects, 40 tasks, 40 tickets. Only that shortlist reaches the
model, and any id it returns that was not in the shortlist is discarded, so it
cannot invent a link. Whatever a cap dropped is recorded in `ai_matched_on`, so
a truncated candidate set is never invisible.

With no link module installed there are no pools, and the matcher returns
without calling the model at all rather than burning credit on a question with
no possible answer.

- confidence ≥ `sembly.ai_confidence_threshold` (0.7) → linked automatically
- below it → a suggestion with a one-click apply, nothing linked
- a project matched but no opportunity/task → filed under a per-project
  `الاجتماعات` bucket task, created once and reused
- **any hand edit sets `link_state = 'manual'` and the matcher then skips the
  record permanently** (the *ربط بالذكاء الاصطناعي* button forces a re-match)

### Data minimisation

**The transcript is never sent to the LLM.** Only the title, the date, the
participant names and the first 2 000 characters of the summary leave the
instance. `_build_match_prompt` has no access to `self.transcript` and must not
gain one — changing that is a deliberate policy decision, not a tweak.

## Importing the whole history

`الاجتماعات → الإعدادات → استيراد من Sembly` has two buttons, and the
difference is not cosmetic:

- **استيراد الآن** runs inline, for a bounded range. It is capped by the HTTP
  worker's `limit_time_real` — **240 s on this instance**.
- **استيراد كامل السجل** only ARMS `cron_sembly_backfill` and returns.

A full backfill cannot run inline. Every meeting costs one `get_meeting` round
trip, so a few hundred of them run for many minutes and the worker is killed
mid-way — which is exactly what happened before this existed: `WorkerHTTP
timeout after 240s`, and nothing saved, because the wizard committed only at
the end. Both paths now commit **per meeting**, so a killed run keeps what it
already fetched and the next attempt does not pay for it again.

The cron walks backwards in windows (`sembly.backfill_window_days`, default 7),
spends at most `sembly.backfill_seconds` (default 90) per tick — always
completing at least one window — stores its position in
`sembly.backfill_cursor`, and resumes there on the next tick. It stops and
**disables itself** at `sembly.backfill_floor`, or after
`sembly.backfill_empty_windows` (default 6) consecutive empty windows. Progress
and every failure land in `sembly.sync.log`.

Two details that are load bearing:

- **A window returning exactly 200 meetings is narrowed and re-listed.** 200 is
  the server-side cap on `list_meetings`, so a full window is
  indistinguishable from a truncated one; accepting it would skip meetings and
  nobody would ever notice the gap. Only a single day that still returns 200 is
  genuinely unresolvable, and that is logged as an error rather than hidden.
- **A failed window leaves the cursor alone**, so the next tick retries it
  instead of stepping over it.

Do not reintroduce `registry.in_test_mode()` — it was **removed in Odoo 19**
and raises `AttributeError`. `_may_commit()` uses `config['test_enable']`. The
old call survived unnoticed precisely because its only callers were crons that
no test ever ran.

## Chatter summaries

`_cron_post_recent_summaries` posts the **single most recent** meeting whose
`started_at` falls on today or yesterday **in the company timezone**
(`sembly.timezone`, default `Asia/Riyadh` — a 21:00 Riyadh meeting is 18:00 UTC
and must count as *today*). It posts an **internal note** (`mail.mt_note`) on
both the opportunity and the task, so meeting content never reaches a lead's
customer-side email followers. `summary_posted` guarantees it happens once.

The *نشر الملخص في الشاتر* button runs the same code for one record and
deliberately ignores the date window — an explicit request is an override.

## The recording link

Sembly publishes **no** direct audio/video file URL in either channel; the
recording plays on the Sembly meeting page. `meeting_url` is the webhook's
authoritative `meeting_link` when available, otherwise built from
`sembly.meeting_url_template`. `media_url` is a separate computed field so that
if Sembly ever exposes a real media URL, only that compute changes.

## Guest links — answered by Sembly support, 2026-08-11

Most people here have no Sembly account, so the workspace link
(`webapp.sembly.ai/meeting/<numeric id>`) is useless to them. The guest link
(`webapp.sembly.ai/guest-access/meeting/<base64 uuid>`) is the one that opens
without signing in. Sembly's support team answered our questions directly, and
the answers close the subject:

- **There is no programmatic way to obtain a meeting's UUID or guest link.**
  No MCP tool, no REST endpoint, no automation payload — "we do not have a
  public API". This matches what we measured: `list_meetings` and `get_meeting`
  return no URL field of any kind, and `meeting_link` in the webhook payload is
  the *workspace* link (verified live: it arrived as
  `https://webapp.sembly.ai/meeting/0` in a test payload, i.e. built from the
  numeric id).
- **The `guest-access/meeting/<base64(uuid)>` format is an internal
  implementation detail, explicitly NOT a supported contract.** Sembly: "we
  would not recommend building against it or depending on it, as it is not
  something we guarantee to keep stable." `_build_share_url` therefore exists
  only to consume a UUID that some future payload might carry — **do not add a
  code path that manufactures guest links from data we already have.** Today
  nothing delivers a UUID, so the method never fires.
- **Guest links never expire, and there is no API to revoke one.** Treat any
  guest link stored in `share_url` as a permanent, unauthenticated credential
  for that meeting's contents.
- **The supported route is Sembly's own post-meeting notification.** With
  workspace notifications set to *all invitees*, Sembly generates the guest
  link itself and emails it to unregistered participants. The condition is that
  they must be **actual invitees on the calendar event** — attending is not
  enough, and someone who was never invited gets nothing. Otherwise: give them
  workspace accounts.

So `share_url` is filled by a human pasting from Sembly's Share dialog, or by a
webhook that happens to carry a `guest-access` URL. Both are reading what
Sembly gives us; neither constructs anything.

Sembly also confirmed the Custom Automation payload schema we had already
matched, and took our two bug reports — the missing `charset` on
`text/event-stream` and the non-ISO-8601 `"… (UTC)"` datetimes — to their
engineering team.

## Adding a new link target

Everything needed to file meetings against, say, a sale order is one small
module — no change to this one:

1. `_inherit = 'sembly.meeting'`, add the `Many2one`.
2. Add its name to `_sembly_link_fields`.
3. Append a pool in `_ai_candidate_pools`, with a `sequence` that does not
   collide with 10 (leads), 20 (projects), 30 (tasks) or 40 (tickets).
4. Append the record to `_summary_targets` if it should receive the note, and
   return `True` from `_has_external_link` if it should suppress the project
   bucket task.
5. Inherit the base views, xpath-ing only onto the declared anchors, and ship
   one `ir.rule` for `group_sembly_user`.

## Branding

The icons follow the Era module-icon family (`era_crm_ai_agents_*`): a 140×140
RGBA PNG, transparent outside the shape, one rounded square (radius 30 ≈ 21 % of
the side) in a flat brand fill, a single flat white glyph, and exactly one accent
element.

| | `era_sembly_meetings` | `…_crm` | `…_tasks` | `…_tickets` |
|---|---|---|---|---|
| Plate | `#304375` navy | `#6B3FA0` plum | `#3F7D3A` moss | `#0D6E6E` teal |
| Glyph | transcript card + tail, 3 lines | same card, 3 lines | same card, checkmark + 3 lines | ticket card, left notch, 2 lines |
| Accent | `#7FB2FF` badge + white dot | `#FFC72C` badge + white dot | `#B7E36B` badge + white dot | `#FFC72C` badge + white dot |

`icon.svg` beside each PNG is the editable source and carries the full geometry
on a `viewBox="0 0 140 140"`. The PNG is **rendered from that same coordinate
system**, not from the SVG — no SVG rasteriser (`cairosvg`, `rsvg-convert`,
`inkscape`) exists in the Odoo container, and adding one is not worth a branding
change. To regenerate, transcribe the SVG shapes into Pillow calls, draw at 8×
(1120×1120) and downsample with `Image.LANCZOS` to 140×140 for clean edges:

```python
from PIL import Image, ImageDraw
S, N = 8, 140
u = lambda v: v * S
im = Image.new('RGBA', (u(N), u(N)), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.rounded_rectangle([0, 0, u(140) - 1, u(140) - 1], radius=u(30), fill='#304375')
...  # glyph, then the accent circle at (102, 94) r=13 with a white r=5 dot
im.resize((N, N), Image.LANCZOS).save(path, 'PNG', optimize=True)
```

Keep the SVG and the PNG in sync — the SVG is the spec, the PNG is what Odoo
serves. `views/sembly_menus.xml` points `web_icon` at the PNG, and
`ir.ui.menu.web_icon_data` is baked in at install/upgrade, so **replacing the
file only takes effect after the module is upgraded**.

`static/description/index.html` is the Apps-page description: self-contained (no
external asset, image or font) and bilingual — every heading and paragraph in
English, then in Arabic with `dir="rtl"`.

Its styling is **inline `style=` attributes, not a `<style>` block**. Odoo's
`html_sanitize` strips `<style>` out of `ir.module.module.description_html`, so a
scoped stylesheet renders unstyled on the Apps detail page — verified on this
instance, where `era_domain_industry` (which uses a `<style>` block) loses its CSS
the same way. Inline attributes survive. Do not "tidy" these into a stylesheet.

## Tests

```bash
MODULES=era_sembly_meetings,era_sembly_meetings_crm,era_sembly_meetings_tasks,era_sembly_meetings_tickets
/opt/odoo/venv/bin/python /opt/odoo/ce/odoo-bin -c /opt/odoo/odoo.conf \
  -d <db> --http-port=8899 --gevent-port=8898 --stop-after-init \
  --workers=0 --max-cron-threads=0 --logfile=/var/lib/odoo/sembly_tests.log \
  --log-level=test -u "$MODULES" --test-enable --test-tags "/${MODULES//,//}"
```

**Pass `--logfile` and `--log-level=test` explicitly.** `odoo.conf` sets
`logfile = /var/log/odoo/odoo.log` and `log_level = warn`, which silently
swallows the test summary — and odoo-bin can still exit 0 with failures, so an
exit code alone proves nothing. Read the `N failed, M error(s) of K tests` line.

207 tests with all four modules installed, 152 with the base alone — no network
and no credential required. `tests/fixtures.py` holds frames recorded verbatim
from the live MCP server plus webhook payloads shaped by Sembly's published
automation schema; the link modules import it from
`odoo.addons.era_sembly_meetings.tests.fixtures`.

The base suite must keep passing **with no link module installed** — that is the
claim the split rests on. It stubs the seams (`_summary_targets`,
`_ai_candidate_pools`) rather than depending on `crm` or `project`:

```bash
/opt/odoo/venv/bin/python /opt/odoo/ce/odoo-bin -c /opt/odoo/odoo.conf \
  -d <fresh-db> --stop-after-init --workers=0 --max-cron-threads=0 \
  --logfile=/var/lib/odoo/base_only.log --log-level=test \
  -i era_sembly_meetings --test-enable --test-tags /era_sembly_meetings
```

Two traps a test in this suite must avoid, both of which cost a debugging round
already:

- **Never assert an absolute chatter-note count.** A target record carries
  chatter of its own — creating a `res.partner` already logs one note — so
  filter to the notes this feature posted (the QWeb heading `Meeting summary`
  is the marker).
- **Never assert that a seam is empty.** The suite runs against whatever
  modules the database has, so `_sembly_link_fields()` legitimately returns all
  four field names there. Assert the base implementation explicitly
  (`BaseSemblyMeeting._sembly_link_fields(...)`) or assert the contract instead.
