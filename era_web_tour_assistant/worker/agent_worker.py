#!/usr/bin/env python3
"""Build walkthroughs by actually performing the task in a browser.

This runs *outside* Odoo. An Odoo request cannot hold a browser open for the
minutes an agent needs, and the agent must not run against the database real
users work in: it does not describe the task, it performs it, so asking it
"how do I create an order" creates an order. It therefore drives a scratch
copy and hands the resulting steps back over JSON-RPC.

    export ODOO_URL=http://127.0.0.1:8069
    export ODOO_DB=<live database>          # where questions are queued
    export ODOO_LOGIN=tour_worker
    export ODOO_PASSWORD=...
    export SCRATCH_URL=http://127.0.0.1:8070
    export SCRATCH_DB=sheet_test            # where the agent may break things
    export SCRATCH_LOGIN=... SCRATCH_PASSWORD=...

    export PLANNER=openrouter               # or anthropic, or openai
    export OPENROUTER_API_KEY=...
    export PLANNER_MODEL=...                # --list-models shows the choices

    python3 agent_worker.py --list-models   # what your key can reach
    python3 agent_worker.py --once

A subscription to claude.ai or chatgpt.com is not an API key — those are
separate products with separate billing. OpenRouter fronts both vendors
behind one key, which is why it is the default here.

The planner is swappable so the browser and submission halves can be tested
without spending a model call: see ScriptedPlanner.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

MODEL = "claude-opus-5"
MAX_STEPS = 25
BROWSER_TIMEOUT = 90

# Thinking is on by default on this model, and max_tokens caps thinking and the
# reply together — so a ceiling sized for the JSON alone truncates the answer
# rather than the reasoning. A page snapshot is long enough to think about.
MAX_TOKENS = 16000

# Anything the agent proposes must survive the module's own validation, so
# keep this list in step with ALLOWED_RUNS in models/tour_request.py.
ACTIONS = ("click", "fill", "done", "give_up")


# ----------------------------------------------------------------------
# Talking to Odoo
# ----------------------------------------------------------------------

class Odoo:
    """The smallest JSON-RPC client that can drive the worker endpoints."""

    def __init__(self, url, db, login, password):
        self.url = url.rstrip("/")
        self.db = db
        self.uid = self._call("common", "login", [db, login, password])
        if not self.uid:
            raise SystemExit("Odoo rejected the worker's credentials.")
        self.password = password

    def _call(self, service, method, args):
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {"service": service, "method": method, "args": args},
            "id": 1,
        }
        request = urllib.request.Request(
            self.url + "/jsonrpc",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read())
        if "error" in body:
            message = body["error"].get("data", {}).get("message")
            raise RuntimeError(message or json.dumps(body["error"])[:400])
        return body["result"]

    def execute(self, model, method, *args):
        return self._call(
            "object", "execute_kw",
            [self.db, self.uid, self.password, model, method, list(args)],
        )


# ----------------------------------------------------------------------
# Driving the browser
# ----------------------------------------------------------------------

class Browser:
    """A thin wrapper over the agent-browser CLI.

    Deliberately drives by CSS selector rather than by the CLI's own element
    refs: the selector that works here is the one recorded as the tour's
    trigger, so a step is only ever written after it has been proven against
    a real page.
    """

    def __init__(self, binary=None, session="tour-agent"):
        # Where it lands differs per install — npm's global prefix, a container
        # image, a developer's own PATH — so ask PATH before guessing, and let
        # AGENT_BROWSER override both.
        self.binary = (
            binary
            or os.environ.get("AGENT_BROWSER")
            or shutil.which("agent-browser")
            or "agent-browser"
        )
        self.session = session

    def run(self, *args, check=True):
        result = subprocess.run(
            [self.binary, "--session", self.session, *args],
            capture_output=True, text=True, timeout=BROWSER_TIMEOUT,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                "browser %s failed: %s" % (args[0], result.stderr.strip()[:300])
            )
        return result.stdout.strip()

    def open(self, url):
        self.run("open", url)
        self.run("wait", "--load", "networkidle", check=False)

    def snapshot(self):
        return self.run("snapshot", "-i", "-c", check=False)

    def click(self, selector):
        self.run("click", selector)
        self.run("wait", "--load", "networkidle", check=False)

    def fill(self, selector, value):
        self.run("fill", selector, value)

    def exists(self, selector):
        script = (
            "!!document.querySelector(%s)" % json.dumps(selector)
        )
        return "true" in self.run("eval", script, check=False).lower()

    def close(self):
        self.run("close", check=False)

    def login(self, url, login, password):
        self.open(url.rstrip("/") + "/web/login")
        self.fill("input[name=login]", login)
        self.fill("input[name=password]", password)
        self.click("button[type=submit]")


# ----------------------------------------------------------------------
# Deciding what to do next
# ----------------------------------------------------------------------

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "selector": {
            "type": "string",
            "description": "A CSS selector for the element to act on. Prefer "
                           "stable attributes: [data-menu-xmlid=...], "
                           "[name=...], a button's class. Empty for done or "
                           "give_up.",
        },
        "value": {
            "type": "string",
            "description": "Text to type, for fill. Empty otherwise.",
        },
        "explain": {
            "type": "string",
            "description": "One short sentence telling the user what to do "
                           "at this step, in the language of the question.",
        },
        "reason": {
            "type": "string",
            "description": "Why this is the right next action, or why you "
                           "are giving up.",
        },
    },
    "required": ["action", "selector", "value", "explain", "reason"],
    "additionalProperties": False,
}

SYSTEM = """You are building a walkthrough for an Odoo user who asked how to \
do something. You are driving a real browser on a scratch database, so you may \
perform the task for real.

Work one action at a time. After each action you are shown the page again.

Rules that matter:
- Every selector you give is recorded as a step in the walkthrough and will \
later be matched in a different user's browser. Prefer selectors that survive \
a different database and a different theme: [data-menu-xmlid="..."], \
[name="field_name"], .o_list_button_add, .o_form_button_save. Avoid selectors \
built from record ids, row positions, or generated class names.
- Write `explain` for the person following the walkthrough, in the same \
language as their question. One short sentence, imperative.
- Answer `done` once the task has been carried through, `give_up` if the task \
cannot be done from this screen or you cannot find the way.
- Do not delete records, change settings unrelated to the task, or install \
anything."""


class ClaudePlanner:
    """Asks Claude for the next browser action."""

    def __init__(self, model=MODEL, effort="high"):
        import anthropic  # imported here so the stub planner needs no key

        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort

    def next_action(self, question, snapshot, history):
        done = "\n".join(
            "%s. %s -> %s" % (index + 1, step["action"], step["selector"])
            for index, step in enumerate(history)
        ) or "(nothing yet)"
        response = self.client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": ACTION_SCHEMA},
            },
            messages=[{
                "role": "user",
                "content": (
                    "The user asked: %s\n\n"
                    "Actions so far:\n%s\n\n"
                    "The page now looks like this:\n%s"
                ) % (question, done, snapshot),
            }],
        )
        # A refusal carries no text block and a truncated reply carries no
        # closing brace, so both would surface as an unrelated error further
        # down. Say which one happened instead.
        if response.stop_reason == "refusal":
            raise RuntimeError("the planner declined this question")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("the planner's reply was cut off")
        text = next((b.text for b in response.content if b.type == "text"), "")
        if not text:
            raise RuntimeError("the planner returned no answer")
        return json.loads(text)


class ChatCompletionsPlanner:
    """The same planner against any OpenAI-compatible endpoint.

    OpenRouter speaks this shape and fronts both Anthropic and OpenAI models,
    so one key covers whichever you want to compare. Written against raw HTTP
    rather than a vendor SDK: the worker already needs urllib, and this keeps
    the OpenAI-compatible path free of a dependency the Anthropic path owns.
    """

    def __init__(self, base_url, api_key, model, referer=None, title=None):
        if not api_key:
            raise SystemExit("no API key for the planner — see README")
        if not model:
            raise SystemExit(
                "set the model to use (try --list-models to see what your "
                "key can reach)"
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.referer = referer
        self.title = title

    def _post(self, path, payload):
        headers = {
            "Authorization": "Bearer %s" % self.api_key,
            "Content-Type": "application/json",
        }
        # OpenRouter uses these for attribution; harmless elsewhere.
        if self.referer:
            headers["HTTP-Referer"] = self.referer
        if self.title:
            headers["X-Title"] = self.title
        request = urllib.request.Request(
            self.base_url + path, data=json.dumps(payload).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read())

    def next_action(self, question, snapshot, history):
        done = "\n".join(
            "%s. %s -> %s" % (index + 1, step["action"], step["selector"])
            for index, step in enumerate(history)
        ) or "(nothing yet)"
        body = self._post("/chat/completions", {
            "model": self.model,
            "max_tokens": 2000,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": (
                    "The user asked: %s\n\n"
                    "Actions so far:\n%s\n\n"
                    "The page now looks like this:\n%s\n\n"
                    "Reply with a single JSON object matching this schema, "
                    "and nothing else:\n%s"
                ) % (question, done, snapshot, json.dumps(ACTION_SCHEMA))},
            ],
            "response_format": {"type": "json_object"},
        })
        return _parse_json_object(body["choices"][0]["message"]["content"])


def _parse_json_object(text):
    """Read the model's reply, tolerating a markdown fence around it.

    Models behind an OpenAI-compatible endpoint honour json_object to varying
    degrees, and a fenced block is the usual way it slips.
    """
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError("the planner did not return JSON: %s" % text[:200])
    return json.loads(text[start:end + 1])


def make_planner():
    """Build the planner named by PLANNER (openrouter | anthropic | openai)."""
    provider = os.environ.get("PLANNER", "openrouter").strip().lower()
    if provider == "anthropic":
        return ClaudePlanner(
            model=os.environ.get("PLANNER_MODEL", MODEL)
        )
    if provider == "openrouter":
        return ChatCompletionsPlanner(
            "https://openrouter.ai/api/v1",
            os.environ.get("OPENROUTER_API_KEY"),
            os.environ.get("PLANNER_MODEL"),
            referer=os.environ.get("OPENROUTER_REFERER"),
            title="Odoo Tour Assistant",
        )
    if provider == "openai":
        return ChatCompletionsPlanner(
            os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            os.environ.get("OPENAI_API_KEY"),
            os.environ.get("PLANNER_MODEL"),
        )
    raise SystemExit("unknown PLANNER: %s" % provider)


def list_models():
    """Print the models the configured key can actually reach.

    Saves guessing at a provider's naming — OpenRouter's ids are prefixed by
    vendor, and they change as models come and go.
    """
    provider = os.environ.get("PLANNER", "openrouter").strip().lower()
    if provider != "openrouter":
        raise SystemExit("--list-models only knows how to ask OpenRouter")
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": "Bearer %s"
                 % os.environ.get("OPENROUTER_API_KEY", "")},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        models = json.loads(response.read())["data"]
    for entry in sorted(models, key=lambda m: m["id"]):
        if entry["id"].startswith(("anthropic/", "openai/")):
            print(entry["id"])
    return 0


class ScriptedPlanner:
    """Replays a fixed list of actions — for testing everything but the model."""

    def __init__(self, actions):
        self.actions = list(actions)

    def next_action(self, question, snapshot, history):
        if not self.actions:
            return {"action": "done", "selector": "", "value": "",
                    "explain": "", "reason": "script exhausted"}
        return self.actions.pop(0)


# ----------------------------------------------------------------------
# The loop
# ----------------------------------------------------------------------

def build_one(question, browser, planner, scratch_url, max_steps=MAX_STEPS):
    """Perform the task, returning the steps that worked.

    A step is recorded only after its selector has been acted on successfully,
    so a walkthrough never contains a trigger that was never proven to resolve.
    """
    browser.open(scratch_url.rstrip("/") + "/odoo")
    steps, history = [], []

    for _ in range(max_steps):
        decision = planner.next_action(question, browser.snapshot(), history)
        action = decision.get("action")
        selector = (decision.get("selector") or "").strip()

        if action == "done":
            return steps, None
        if action == "give_up":
            return [], decision.get("reason") or "the agent gave up"
        if action not in ("click", "fill") or not selector:
            return [], "the agent proposed an action that makes no sense"

        if not browser.exists(selector):
            history.append({"action": "missing", "selector": selector})
            continue

        try:
            if action == "click":
                browser.click(selector)
                run = "click"
            else:
                browser.fill(selector, decision.get("value") or "")
                run = "edit %s" % (decision.get("value") or "")
        except RuntimeError as error:
            history.append({"action": "failed", "selector": selector})
            if len(history) > max_steps:
                return [], str(error)
            continue

        history.append({"action": action, "selector": selector})
        steps.append({
            "trigger": selector,
            "content": decision.get("explain") or "",
            "run": run,
        })

    return steps, ("the agent ran out of steps" if not steps else None)


def work(odoo, browser, planner, scratch_url, scratch_login, scratch_password,
         limit=1):
    claimed = odoo.execute(
        "tour.assistant.request", "worker_claim", limit
    )
    if not claimed:
        return 0

    browser.login(scratch_url, scratch_login, scratch_password)
    for item in claimed:
        print("building: %s" % item["question"], flush=True)
        try:
            steps, error = build_one(
                item["question"], browser, planner, scratch_url
            )
        except Exception as exc:  # a worker must never die on one question
            steps, error = [], "%s: %s" % (type(exc).__name__, exc)

        if steps and not error:
            result = odoo.execute(
                "tour.assistant.request", "worker_submit",
                item["id"], steps, "/odoo",
            )
            print("  published %s (%s steps)" % (result["tour"], result["steps"]))
        else:
            odoo.execute(
                "tour.assistant.request", "worker_fail",
                item["id"], error or "no steps",
            )
            print("  gave up: %s" % error)
    return len(claimed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true",
                        help="Take one batch and exit, instead of looping.")
    parser.add_argument("--interval", type=int, default=60,
                        help="Seconds to wait when the queue is empty.")
    parser.add_argument("--limit", type=int, default=1,
                        help="Questions to claim per batch.")
    parser.add_argument("--list-models", action="store_true",
                        help="Print the models your key can reach, and exit.")
    args = parser.parse_args()

    if args.list_models:
        return list_models()

    required = ["ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_PASSWORD",
                "SCRATCH_URL", "SCRATCH_LOGIN", "SCRATCH_PASSWORD"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit("missing environment: %s" % ", ".join(missing))

    if os.environ["ODOO_DB"] == os.environ.get("SCRATCH_DB"):
        raise SystemExit(
            "refusing to run: the agent performs the tasks it is asked "
            "about, so it must not explore the database that holds the "
            "questions."
        )

    odoo = Odoo(os.environ["ODOO_URL"], os.environ["ODOO_DB"],
                os.environ["ODOO_LOGIN"], os.environ["ODOO_PASSWORD"])
    browser = Browser()
    planner = make_planner()

    try:
        while True:
            count = work(
                odoo, browser, planner,
                os.environ["SCRATCH_URL"],
                os.environ["SCRATCH_LOGIN"], os.environ["SCRATCH_PASSWORD"],
                limit=args.limit,
            )
            if args.once:
                return 0
            if not count:
                time.sleep(args.interval)
    finally:
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
