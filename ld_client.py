"""Everything this project does with the LaunchDarkly Python SDK.

Experimentation adds surprisingly little to the SDK surface area. There is no
"experimentation SDK" and no experiment-specific call: you evaluate the flag as
you always did, you send your metric events as you always did, and LaunchDarkly
does the attribution. Concretely, only three things matter:

1. `evaluate()`  — a normal flag evaluation. When the visitor was allocated by a
                   running experiment, LaunchDarkly marks the evaluation event
                   as an experiment *exposure*. That happens automatically; the
                   `inExperiment` field this module surfaces is how you can
                   *see* it, not how you cause it.
2. `track()`     — the custom metric events the experiment is measured on. An
                   event is attributed to an arm only if the same context was
                   exposed to the flag first, which is why order matters.
3. `flush()`     — events are batched in memory. A short-lived script that exits
                   without flushing throws its experiment data away.

The one genuinely important rule is at the bottom of that list: **evaluate
before you track, with the same context key.**
"""

import logging
from typing import Any, Optional

import ldclient
from ldclient import Context
from ldclient.config import Config

import config

log = logging.getLogger(__name__)

# Keeps the SDK's own (fairly chatty) logging out of the demo output. Set this
# to logging.DEBUG if you need to watch the streaming connection handshake or
# the event payloads leaving the process.
logging.getLogger("ldclient").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def initialize(high_volume: bool = False) -> bool:
    """Start the shared LaunchDarkly client. Returns True if it connected.

    `ldclient.set_config()` creates a process-wide singleton and blocks for up
    to five seconds while it downloads the initial flag payload. Create it once
    per process and share it.

    `high_volume=True` is used by the traffic simulator. It enlarges the
    in-memory event buffer, because the default (10,000 pending events) is sized
    for a web server that trickles events out continuously, not for a script
    that generates tens of thousands in a few minutes. Overflowing that buffer
    does not crash anything — the SDK logs a warning and *silently drops* the
    excess, which would quietly bias the experiment. The simulator also flushes
    on a fixed cadence for the same reason.
    """
    if config.OFFLINE_DEMO:
        _init_offline_demo()
    else:
        if not config.SDK_KEY:
            log.error("LAUNCHDARKLY_SDK_KEY is not set — see .env.example")
            return False

        sdk_config = Config(
            config.SDK_KEY,
            # Room for a large simulated burst. Real applications should leave
            # this at its default.
            events_max_pending=100_000 if high_volume else 10_000,
            # Send batches more often than the 5s default while simulating, so
            # data starts appearing in LaunchDarkly almost immediately.
            flush_interval=2.0 if high_volume else 5.0,
        )
        ldclient.set_config(sdk_config)

    return ldclient.get().is_initialized()


def flush() -> None:
    """Push buffered analytics events to LaunchDarkly now.

    Asynchronous: it asks the event processor to deliver the current batch and
    returns immediately. Call it periodically during a long run, and always
    before the process exits.
    """
    ldclient.get().flush()


def shutdown() -> None:
    """Flush pending analytics events and close the connection cleanly.

    `close()` blocks until the final batch has been handed off. Skipping it in a
    short-lived script is the single most common reason experiment data never
    shows up in LaunchDarkly.
    """
    client = ldclient.get()
    client.flush()
    client.close()


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

# Maps LaunchDarkly's evaluation reason onto how this visitor was decided. For
# an experiment the interesting distinction is not which rule matched, but
# whether the visitor reached the experiment's rollout at all.
_MECHANISM_BY_REASON = {
    "TARGET_MATCH": "individual",
    "RULE_MATCH": "rule",
    "FALLTHROUGH": "default",
    "OFF": "off",
    "PREREQUISITE_FAILED": "off",
    "ERROR": "error",
}


def evaluate(context: Context) -> dict:
    """Evaluate the landing page flag for one visitor.

    Calling this is what enrols the visitor in the experiment. There is no
    separate "enrol" API: LaunchDarkly's experiment rollout lives inside the
    flag's targeting, so a plain flag evaluation *is* the allocation, and the
    resulting evaluation event *is* the exposure record.

    `variation_detail()` is used rather than plain `variation()` because the
    reason is what tells you whether this visitor is in the experiment. Both
    calls produce the same exposure event; `variation_detail()` additionally
    hands the reason back to your code, which is how the simulator can report
    that (say) 4% of traffic was excluded because a targeting rule caught it
    first.

    In ordinary production code you would call `variation()` and ignore the
    reason. It is used throughout this demo because the reason *is* the demo.
    """
    detail = ldclient.get().variation_detail(
        config.FLAG_KEY,
        context,
        # The fallback, used only if LaunchDarkly is unreachable or the flag
        # does not exist. Always the safe, already-released experience.
        config.FLAG_FALLBACK_VARIATION,
    )

    reason = detail.reason or {}
    kind = reason.get("kind", "UNKNOWN")

    return {
        "variation": str(detail.value),
        "variationIndex": detail.variation_index,
        "reasonKind": kind,
        "mechanism": _MECHANISM_BY_REASON.get(kind, "error"),
        # THE field that matters for experimentation. True only when this
        # evaluation was decided by an experiment rollout — i.e. the visitor is
        # a subject in a running experiment and this exposure will be counted.
        "inExperiment": bool(reason.get("inExperiment")),
        "reasonText": describe_reason(reason, detail.is_default_value()),
        "ruleId": reason.get("ruleId"),
        "isFallback": detail.is_default_value(),
    }


def describe_reason(reason: dict, is_default: bool) -> str:
    """Turn LaunchDarkly's evaluation reason into a sentence for the UI."""
    kind = reason.get("kind", "UNKNOWN")
    in_experiment = bool(reason.get("inExperiment"))

    if kind == "FALLTHROUGH" and in_experiment:
        return (
            "IN THE EXPERIMENT — the flag's default rule is an experiment "
            "rollout, so this visitor was randomly allocated to an arm and this "
            "evaluation counts as an exposure."
        )
    if kind == "FALLTHROUGH":
        return (
            "DEFAULT RULE — this visitor reached the flag's default rule, but "
            "it is not an experiment rollout, so nothing is being measured. "
            "Start the experiment iteration in LaunchDarkly."
        )
    if kind == "TARGET_MATCH":
        return (
            "EXCLUDED — this visitor's key is listed directly on the flag as an "
            "individual target. Individual targets are evaluated before the "
            "default rule, so this visitor never reaches the experiment and is "
            "correctly left out of the results."
        )
    if kind == "RULE_MATCH":
        rule = reason.get("ruleId") or "unnamed"
        return (
            f"EXCLUDED — matched targeting rule '{rule}', which is evaluated "
            "before the default rule. This visitor is served deliberately, not "
            "randomly, so counting them would bias the experiment."
        )
    if kind == "OFF":
        return (
            "FLAG IS OFF — every visitor gets the off variation and no "
            "experiment data is collected. Turn targeting on."
        )
    if kind == "PREREQUISITE_FAILED":
        return f"A prerequisite flag ({reason.get('prerequisiteKey')}) is not satisfied."
    if kind == "ERROR":
        error_kind = reason.get("errorKind", "UNKNOWN")
        if error_kind == "FLAG_NOT_FOUND":
            return (
                f"Flag '{config.FLAG_KEY}' was not found in this environment — "
                "serving the safe fallback. Create the flag first (README, Step 4)."
            )
        if error_kind == "CLIENT_NOT_READY":
            return "The SDK is not connected yet — serving the safe fallback."
        return f"Evaluation error ({error_kind}) — serving the safe fallback."
    if is_default:
        return "Serving the code-level fallback value."
    return f"Served by LaunchDarkly ({kind})."


# ---------------------------------------------------------------------------
# Metric events
# ---------------------------------------------------------------------------


def track(
    event_key: str,
    context: Context,
    data: Optional[dict] = None,
    metric_value: Optional[float] = None,
) -> None:
    """Send one custom metric event to LaunchDarkly.

    Three rules decide whether this event is counted in your experiment:

    1. **The context must match the exposure.** LaunchDarkly joins events to
       arms by the context key that was evaluated. Track with a different key —
       a session id, an anonymous placeholder — and the event is orphaned.
    2. **The evaluation must come first.** An event that arrives for a context
       that was never exposed to the flag has no arm to be attributed to.
    3. **The event key must match the metric's event key**, exactly, including
       case. A typo here produces an experiment that runs forever at zero
       conversions, which looks like a null result rather than a bug.

    `metric_value` is only meaningful for *numeric* metrics; conversion metrics
    ignore it and simply count occurrences.
    """
    ldclient.get().track(event_key, context, data, metric_value)


def track_conversion(context: Context, variation: str) -> None:
    """Record the primary conversion event: the visitor clicked the CTA.

    The `variation` in `data` is *not* how LaunchDarkly attributes the event —
    it works that out from the exposure. It is attached only so the raw event
    stream is readable by a human debugging the setup.
    """
    track(config.PRIMARY_METRIC_KEY, context, {"variation": variation})


def track_order_value(context: Context, variation: str, amount: float) -> None:
    """Record the secondary numeric metric: order value in USD."""
    if not config.TRACK_SECONDARY_METRIC:
        return
    track(
        config.SECONDARY_METRIC_KEY,
        context,
        {"variation": variation},
        metric_value=round(float(amount), 2),
    )


# ---------------------------------------------------------------------------
# Offline demo support (OFFLINE_DEMO=1 only)
# ---------------------------------------------------------------------------
# The in-process stand-in for LaunchDarkly. It publishes a real LaunchDarkly
# flag payload — including a genuine experiment rollout — through the SDK's own
# data source plumbing, so the SDK's evaluator does the bucketing and every
# `inExperiment` reason you see offline is a real verdict, not a simulated one.
#
# What offline mode cannot do is analyse the results: there is no LaunchDarkly
# to receive the events. That is why analysis.py computes the statistics
# locally, and why the offline path is a way to rehearse the demo rather than a
# replacement for running it against your own account.

# Mirrors the targeting the "Part 2 Target" demo asks you to build, so a visitor
# who was excluded there is still excluded here.
_OFFLINE_INDIVIDUAL_TARGET_KEY = "user-avery-chen"
_OFFLINE_RULE_ID = "beta-testers-on-paid-plans"

# LaunchDarkly expresses rollout weights in hundred-thousandths: 50000 = 50%.
_HALF = 50_000


class _OfflineDataSource:
    """A stand-in for LaunchDarkly's streaming connection, for offline demos.

    The flag payload below is the real LaunchDarkly wire format. The critical
    line is `"kind": "experiment"` in the fallthrough rollout — that, and only
    that, is what makes the SDK stamp `inExperiment: true` on the evaluation
    reason and mark the event as an experiment exposure. A `"kind": "rollout"`
    percentage rollout buckets traffic identically but measures nothing, which
    is exactly the difference between a staged rollout and an experiment.

    The SDK instantiates this for us because it is passed as
    `Config(update_processor_class=...)`.
    """

    instance: "Optional[_OfflineDataSource]" = None

    def __init__(self, sdk_config: Config, store, ready):
        # The sink broadcasts flag changes; the raw store is only a fallback.
        self._sink = sdk_config.data_source_update_sink or store
        self._ready = ready
        self._version = 0
        self._experiment_running = True
        _OfflineDataSource.instance = self

    def start(self) -> None:
        from ldclient.versioned_data_kind import FEATURES

        self._sink.init({FEATURES: {config.FLAG_KEY: self._flag_data()}})
        self._ready.set()

    def stop(self) -> None:
        pass

    def initialized(self) -> bool:
        return True

    def set_experiment_running(self, running: bool) -> None:
        """Start/stop the offline experiment, as the LaunchDarkly UI would.

        Stopping it reverts the default rule to serving the control outright,
        which is precisely what LaunchDarkly does when you stop an iteration.
        """
        from ldclient.versioned_data_kind import FEATURES

        self._experiment_running = running
        self._sink.upsert(FEATURES, self._flag_data())

    def is_experiment_running(self) -> bool:
        return self._experiment_running

    def _fallthrough(self) -> dict:
        """The flag's default rule: an experiment rollout, or a plain variation."""
        if not self._experiment_running:
            # Experiment stopped — everyone gets the control, nothing measured.
            return {"variation": 0}

        control = config.VARIATIONS.index(config.EXPERIMENT_CONTROL)
        treatment = config.VARIATIONS.index(config.EXPERIMENT_TREATMENT)
        return {
            "rollout": {
                # "experiment" (not "rollout") is what turns bucketing into
                # measurement. See the class docstring.
                "kind": "experiment",
                # The seed fixes the bucketing. LaunchDarkly generates one per
                # iteration, which is why starting a *new* iteration reshuffles
                # who is in which arm.
                #
                # This particular value is chosen so that of the two named demo
                # visitors the experiment does include, Riley lands in the
                # control and Sam in the treatment — so the offline demo shows
                # both arms without needing to click "New visitor". Any seed
                # would be equally valid; against a real account LaunchDarkly
                # picks its own.
                "seed": 2,
                "bucketBy": "key",
                "variations": [
                    {"variation": control, "weight": _HALF, "untracked": False},
                    {"variation": treatment, "weight": _HALF, "untracked": False},
                ],
            }
        }

    def _flag_data(self) -> dict:
        """The wire format LaunchDarkly sends for this flag, fully configured."""
        self._version += 1
        return {
            "key": config.FLAG_KEY,
            "version": self._version,
            "on": True,
            "variations": config.VARIATIONS,
            # --- individual targeting: matched before the experiment ---------
            "targets": [
                {"variation": 2, "values": [_OFFLINE_INDIVIDUAL_TARGET_KEY]},
            ],
            "contextTargets": [],
            # --- rule-based targeting: also matched before the experiment ----
            "rules": [
                {
                    "id": _OFFLINE_RULE_ID,
                    "variation": 1,
                    "clauses": [
                        {
                            "contextKind": "user",
                            "attribute": "betaTester",
                            "op": "in",
                            "values": [True],
                            "negate": False,
                        },
                        {
                            "contextKind": "user",
                            "attribute": "plan",
                            "op": "in",
                            "values": ["enterprise", "pro"],
                            "negate": False,
                        },
                    ],
                    "trackEvents": False,
                }
            ],
            # --- everyone else: the experiment -------------------------------
            "fallthrough": self._fallthrough(),
            "offVariation": 0,
            "prerequisites": [],
            "salt": "offline-demo",
            "deleted": False,
            "trackEvents": False,
            "clientSide": False,
        }


def _init_offline_demo() -> None:
    """Run with an in-process data source instead of LaunchDarkly."""
    ldclient.set_config(
        Config(
            "sdk-offline-demo",  # not a real key; never leaves the process
            update_processor_class=_OfflineDataSource,
            # Nothing to send events to, and sending them would only produce
            # connection errors in the console.
            send_events=False,
        )
    )
    log.warning("OFFLINE_DEMO is on — not connected to LaunchDarkly.")


def offline_set_experiment_running(running: bool) -> None:
    """Start/stop the in-memory experiment. Only valid when OFFLINE_DEMO=1."""
    if _OfflineDataSource.instance is None:
        raise RuntimeError("Offline demo mode is not enabled (set OFFLINE_DEMO=1).")
    _OfflineDataSource.instance.set_experiment_running(running)


def offline_is_experiment_running() -> bool:
    if _OfflineDataSource.instance is None:
        return False
    return _OfflineDataSource.instance.is_experiment_running()


def all_flags_state(context: Context) -> dict:
    """Every flag value for this context. Not used by the demo's core loop."""
    state = ldclient.get().all_flags_state(context)
    return state.to_values_map() if state.valid else {}


def describe_connection() -> dict[str, Any]:
    """A small summary of how the SDK is running, for startup banners."""
    return {
        "mode": "OFFLINE DEMO (not connected)" if config.OFFLINE_DEMO else "connected to LaunchDarkly",
        "flagKey": config.FLAG_KEY,
        "initialized": ldclient.get().is_initialized(),
    }
