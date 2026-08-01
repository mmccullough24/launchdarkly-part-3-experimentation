"""ABC Company landing page — the experiment, one visitor at a time.

Run it with:  python app.py     (see README.md for full setup)

`simulate_traffic.py` is what produces enough data to decide the experiment.
This app is the other half: the real thing, for one person, so you can see that
the events the simulator sends in bulk are the same events a genuine visitor
produces by clicking a button.

It is also the clearest way to see what an experiment does to a flag. Load the
page as a series of different visitors and watch the same flag serve different
heroes — not because anyone was targeted, but because they were *randomised*.
The inspector on the right names the mechanism every time.

Routes:

* `/`                    the landing page for one visitor, flag evaluated
                         server-side, with the experiment inspector.
* `/api/cta-click`       records the primary conversion metric (and the
                         secondary numeric metric, if enabled).
* `/api/state`           the same JSON the inspector renders, for `curl`.
* `/api/offline/*`       start/stop the in-memory experiment; offline mode only.
* `/healthz`             liveness.
"""

import logging
import sys
import uuid
from collections import defaultdict
from datetime import datetime
from threading import Lock

from flask import Flask, jsonify, render_template, request

import config
import contexts
import ld_client
from components import hero

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("abc.landing")

app = Flask(__name__)

# A purely local tally of what this browser session has done, shown in the
# inspector so a click produces visible feedback immediately. LaunchDarkly's own
# numbers take a few minutes to appear and are the ones that actually matter;
# these are a receipt, not a result.
_tally_lock = Lock()
_tally: dict[str, dict[str, int]] = defaultdict(lambda: {"exposures": 0, "conversions": 0})


# ---------------------------------------------------------------------------
# Visitors
# ---------------------------------------------------------------------------
# Two kinds. The five named visitors from the "Part 2 Target" demo cover the
# interesting targeting cases, including the two the experiment must exclude.
# Anonymous visitors are generated on demand with a fresh key, which is how you
# watch the experiment randomise people in real time.


def anonymous_visitor(key: str) -> dict:
    """Build a plausible one-off visitor around a given key.

    The attributes are fixed rather than random so that reloading the page for
    the same key shows the same person. Only the key varies between anonymous
    visitors, which is the point: the key is the only thing the experiment's
    randomisation looks at.
    """
    return {
        "key": key,
        "name": "Anonymous visitor",
        "email": None,
        "title": "First-time visitor from search",
        "role": "prospect",
        "plan": "none",
        "betaTester": False,
        "region": "AMER",
        "accountAgeDays": 0,
        "deviceType": "mobile",
        "blurb": "A brand-new visitor with no account. Nothing targets them, so "
                 "they reach the default rule and the experiment randomises "
                 "them into an arm. Click 'New visitor' to draw another.",
        "expected_in_experiment": True,
    }


def resolve_visitor(visitor_param: str | None, anon_key: str | None) -> tuple[str, dict]:
    """Work out who is looking at the page. Returns (visitor_id, visitor dict)."""
    if visitor_param == "anon" or anon_key:
        key = anon_key or f"anon-{uuid.uuid4().hex[:12]}"
        return "anon", anonymous_visitor(key)
    visitor_id = contexts.resolve_visitor_id(visitor_param)
    return visitor_id, contexts.VISITORS[visitor_id]


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def _describe_arm(variation: str, in_experiment: bool) -> str:
    """Name the experiment arm, or say plainly that there isn't one.

    A visitor can be served the treatment's variation without being in the
    treatment arm — a targeting rule can serve `spotlight` to someone the
    experiment never randomised. Those visitors are not part of the result, so
    the inspector must not imply they are.
    """
    if not in_experiment:
        return "no arm — not randomised"
    if variation == config.EXPERIMENT_CONTROL:
        return "control (baseline)"
    if variation == config.EXPERIMENT_TREATMENT:
        return "treatment"
    return f"'{variation}' — not one of the experiment's two arms"


def build_state(visitor_id: str, visitor: dict) -> dict:
    """Everything the page needs to render itself and explain the decision."""
    context = contexts.context_from_attributes(visitor)

    # This call is the exposure. Loading the page enrols this visitor in the
    # experiment; there is no separate enrolment step.
    evaluation = ld_client.evaluate(context)
    served = evaluation["variation"]

    with _tally_lock:
        if evaluation["inExperiment"]:
            _tally[served]["exposures"] += 1
        snapshot = {name: dict(counts) for name, counts in _tally.items()}

    return {
        "flagKey": config.FLAG_KEY,
        "experimentKey": config.EXPERIMENT_KEY,
        "primaryMetricKey": config.PRIMARY_METRIC_KEY,
        "secondaryMetricKey": config.SECONDARY_METRIC_KEY if config.TRACK_SECONDARY_METRIC else None,
        "variation": served,
        "variationIndex": evaluation["variationIndex"],
        "mechanism": evaluation["mechanism"],
        "reasonKind": evaluation["reasonKind"],
        "reasonText": evaluation["reasonText"],
        "inExperiment": evaluation["inExperiment"],
        "isFallback": evaluation["isFallback"],
        # Which arm of the experiment this is, in the experiment's own language.
        # Only meaningful when the visitor was actually randomised: being served
        # the `spotlight` hero by a targeting rule does NOT put you in the
        # treatment arm, and labelling it that way would be exactly the
        # confusion this inspector exists to prevent.
        "arm": _describe_arm(served, evaluation["inExperiment"]),
        "visitor": {
            "id": visitor_id,
            "key": visitor["key"],
            "name": visitor["name"],
            "title": visitor["title"],
            "blurb": visitor["blurb"],
        },
        "attributes": {name: visitor[name] for name in contexts.INSPECTED_ATTRIBUTES},
        "expected": {
            "inExperiment": visitor["expected_in_experiment"],
            "matches": evaluation["inExperiment"] == visitor["expected_in_experiment"],
        },
        "tally": snapshot,
        "timestamp": datetime.now().strftime("%H:%M:%S"),
    }


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/")
def index():
    visitor_id, visitor = resolve_visitor(request.args.get("visitor"), request.args.get("key"))
    state = build_state(visitor_id, visitor)
    view = hero.build_hero(state["variation"])

    return render_template(
        "index.html",
        state=state,
        hero=view,
        visitors=contexts.VISITORS,
        offline_demo=config.OFFLINE_DEMO,
        offline_experiment_running=(
            ld_client.offline_is_experiment_running() if config.OFFLINE_DEMO else None
        ),
    )


@app.get("/api/state")
def api_state():
    """The inspector's JSON, for poking at with curl."""
    visitor_id, visitor = resolve_visitor(request.args.get("visitor"), request.args.get("key"))
    return jsonify(build_state(visitor_id, visitor))


# ---------------------------------------------------------------------------
# The metric event
# ---------------------------------------------------------------------------


@app.post("/api/cta-click")
def cta_click():
    """The visitor clicked the hero's primary call to action.

    This is the whole measurement path, and it is three lines. Note the order:
    the flag is evaluated *before* the event is tracked, using the *same*
    context. LaunchDarkly attributes the click to whichever arm that context was
    exposed to, so getting either of those wrong silently orphans the event.

    The click is tracked even when the visitor is not in the experiment. That is
    deliberate and it is what real code should do: your analytics should not
    have holes in it just because someone was excluded from a test.
    LaunchDarkly simply has no arm to attribute the event to, and ignores it for
    experiment purposes.
    """
    payload = request.get_json(silent=True) or {}
    visitor_id, visitor = resolve_visitor(payload.get("visitor"), payload.get("key"))
    context = contexts.context_from_attributes(visitor)

    evaluation = ld_client.evaluate(context)
    ld_client.track_conversion(context, evaluation["variation"])

    # The secondary numeric metric. A real app would send this when the order is
    # actually placed, with the real basket value — not at the same moment as
    # the click. It is fired here so one button exercises both metric types.
    order_value = 129.0 if evaluation["variation"] == config.EXPERIMENT_CONTROL else 118.0
    ld_client.track_order_value(context, evaluation["variation"], order_value)

    # Events are batched. Flushing here is not something a busy production
    # server should do on every request, but in a demo you want the event to
    # leave the process while you are still looking at the screen.
    ld_client.flush()

    with _tally_lock:
        if evaluation["inExperiment"]:
            _tally[evaluation["variation"]]["conversions"] += 1
        snapshot = {name: dict(counts) for name, counts in _tally.items()}

    log.info(
        "CTA click by %s (%s) on variation '%s' — inExperiment=%s",
        visitor_id, visitor["key"], evaluation["variation"], evaluation["inExperiment"],
    )

    if evaluation["inExperiment"]:
        message = (
            f"Conversion recorded for the '{evaluation['variation']}' arm. "
            f"It will appear in LaunchDarkly under the '{config.PRIMARY_METRIC_KEY}' metric."
        )
    else:
        message = (
            f"Event sent, but this visitor is not in the experiment "
            f"({evaluation['reasonKind']}), so it is not attributed to any arm."
        )

    return jsonify({
        "ok": True,
        "inExperiment": evaluation["inExperiment"],
        "variation": evaluation["variation"],
        "message": message,
        "tally": snapshot,
    })


# ---------------------------------------------------------------------------
# Offline demo controls (OFFLINE_DEMO=1 only)
# ---------------------------------------------------------------------------


@app.post("/api/offline/experiment")
def offline_experiment():
    """Start/stop the in-memory experiment, mimicking the LaunchDarkly UI."""
    if not config.OFFLINE_DEMO:
        return jsonify({"ok": False, "message": "Offline demo mode is not enabled."}), 404
    payload = request.get_json(silent=True) or {}
    running = bool(payload.get("running"))
    ld_client.offline_set_experiment_running(running)
    return jsonify({
        "ok": True,
        "running": running,
        "message": (
            "Experiment started — the default rule is now an experiment rollout."
            if running else
            "Experiment stopped — the default rule serves the control and nothing is measured."
        ),
    })


@app.get("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "flagKey": config.FLAG_KEY,
        "experimentKey": config.EXPERIMENT_KEY,
        "offline": config.OFFLINE_DEMO,
    })


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 78)
    print("  ABC Company — landing page experiment (LaunchDarkly demo)")
    print("=" * 78)

    if not ld_client.initialize():
        print(
            "\n*** LaunchDarkly did not initialize.\n"
            "    Check that LAUNCHDARKLY_SDK_KEY in your .env is a valid server-side\n"
            "    SDK key (it starts with 'sdk-') and that this machine can reach\n"
            "    https://stream.launchdarkly.com.\n"
            "    No account handy? Run:  OFFLINE_DEMO=1 python app.py\n"
            "    See README.md -> Troubleshooting.\n",
            file=sys.stderr,
        )
        return 1

    connection = ld_client.describe_connection()
    print(f"  SDK status    : {connection['mode']}")
    print(f"  Feature flag  : {config.FLAG_KEY}")
    print(f"  Experiment    : {config.EXPERIMENT_KEY}")
    print(f"  Primary metric: {config.PRIMARY_METRIC_KEY}")
    print(f"  Landing page  : http://{config.HOST}:{config.PORT}/")
    print("=" * 78, flush=True)

    try:
        # use_reloader=False keeps exactly one SDK client per process.
        app.run(host=config.HOST, port=config.PORT, threaded=True, use_reloader=False)
    finally:
        ld_client.shutdown()
        print("\nLaunchDarkly client closed. Goodbye.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
