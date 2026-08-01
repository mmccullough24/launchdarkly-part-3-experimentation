#!/usr/bin/env python3
"""Create the metrics and the experiment in LaunchDarkly, via the REST API.

This is an OPTIONAL convenience. Everything it does can be done by clicking
through the LaunchDarkly UI, and README.md -> Steps 5, 6 and 7 describe exactly
how. Use this if you would rather not click, or if you re-run the demo often and
want a repeatable starting point.

What it creates:

  * a CONVERSION metric  — landing-page-cta-click, "higher is better"
  * a NUMERIC metric     — landing-page-order-value, mean USD per order
  * an EXPERIMENT        — control vs spotlight on the flag's default rule,
                           50/50, randomised on the `user` context
It does NOT start the experiment unless you ask it to. Creating and starting are
separate on purpose: an experiment that is running is collecting data you will
later make a decision from, and that should be a deliberate act.

Usage:

    python scripts/setup_launchdarkly.py            # create metrics + experiment
    python scripts/setup_launchdarkly.py --start    # start collecting data
    python scripts/setup_launchdarkly.py --status   # what exists right now
    python scripts/setup_launchdarkly.py --stop     # stop the current iteration

Requires `LD_API_TOKEN` in your `.env` (Account settings -> Authorization ->
Create token, "Writer" role). This is a *different* credential from the SDK key:
the SDK key reads flags, the API token writes them. Neither the app nor the
simulator ever uses the API token.

The flag itself is NOT created here — it is the flag from the "Part 2 Target"
demo and this project deliberately re-uses it. If it does not exist yet, see
README.md -> Step 4.
"""

import argparse
import sys
from pathlib import Path

import requests

# Make the project root importable so this script shares the app's config.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

TIMEOUT = 30

# LaunchDarkly's "semantic patch" content type. It lets you describe the change
# you want ("start the iteration") rather than a JSON Patch document, so the
# request cannot clobber a concurrent edit by someone else on your team.
SEMANTIC_PATCH = "application/json; domain-model=launchdarkly.semanticpatch"


# ---------------------------------------------------------------------------
# What we are creating
# ---------------------------------------------------------------------------

# The PRIMARY metric: a conversion metric. LaunchDarkly counts, per arm, how
# many *unique subjects* fired this event at least once — not how many events
# arrived. A visitor who clicks the CTA five times counts once, which is what
# you want from a conversion rate.
PRIMARY_METRIC = {
    "key": config.PRIMARY_METRIC_KEY,
    "name": "Landing page CTA click-through",
    "kind": "custom",
    "description": (
        "A visitor clicked the landing page hero's primary call to action. "
        "The primary success metric for the landing page revamp."
    ),
    # False = conversion metric (did it happen?) rather than numeric (how much?).
    "isNumeric": False,
    # The event key the SDK sends via client.track(). This is the join between
    # your code and this metric; a mismatch here produces an experiment that
    # runs forever at zero conversions.
    "eventKey": config.PRIMARY_METRIC_KEY,
    # More clicks is better. Set LowerThanBaseline for metrics like error rate
    # or page load time, where a decrease is the win.
    "successCriteria": "HigherThanBaseline",
    "tags": ["demo", "landing-page"],
}

# The SECONDARY metric: numeric. Included as a guardrail — a hero that wins on
# clicks while quietly lowering order value has not necessarily won.
SECONDARY_METRIC = {
    "key": config.SECONDARY_METRIC_KEY,
    "name": "Order value per visitor",
    "kind": "custom",
    "description": (
        "The value of the order a converting visitor placed, in USD. A guardrail "
        "for the landing page revamp: watch this does not fall while clicks rise."
    ),
    "isNumeric": True,
    "eventKey": config.SECONDARY_METRIC_KEY,
    "unit": "USD",
    "successCriteria": "HigherThanBaseline",
    # Average the values per subject, rather than summing them.
    "unitAggregationType": "average",
    "tags": ["demo", "landing-page"],
}

EXPERIMENT_NAME = "Landing page hero — control vs spotlight"
EXPERIMENT_HYPOTHESIS = (
    "Leading with the value proposition and moving social proof above the fold "
    "(the 'spotlight' hero) will increase landing page click-through versus the "
    "current control hero, without reducing average order value."
)


class SetupError(RuntimeError):
    """Anything that should stop the script with a readable message."""


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def _headers(semantic: bool = False) -> dict:
    headers = {
        "Authorization": config.LD_API_TOKEN,
        "Content-Type": SEMANTIC_PATCH if semantic else "application/json",
    }
    # Normally unset. Only needed if LaunchDarkly puts these endpoints behind a
    # version header again; set LD_API_VERSION in .env rather than editing here.
    if config.LD_API_VERSION:
        headers["LD-API-Version"] = config.LD_API_VERSION
    return headers


def _url(path: str) -> str:
    return f"{config.LD_API_BASE_URL}/api/v2{path}"


def _check(response: requests.Response, what: str) -> dict:
    if response.ok:
        return response.json() if response.content else {}
    # The API's error bodies explain the problem (bad token, wrong project key,
    # flag already exists) and never echo the token back.
    raise SetupError(f"{what} failed — HTTP {response.status_code}: {response.text[:600]}")


def _env_path(suffix: str = "") -> str:
    return f"/projects/{config.LD_PROJECT_KEY}/environments/{config.LD_ENVIRONMENT_KEY}/experiments{suffix}"


# ---------------------------------------------------------------------------
# The flag (read only — this project re-uses the Part 2 flag)
# ---------------------------------------------------------------------------


def get_flag() -> dict | None:
    """Fetch the flag, or None if it does not exist."""
    response = requests.get(
        _url(f"/flags/{config.LD_PROJECT_KEY}/{config.FLAG_KEY}"),
        params={"env": config.LD_ENVIRONMENT_KEY},
        headers=_headers(),
        timeout=TIMEOUT,
    )
    if response.status_code == 404:
        return None
    return _check(response, "Reading the flag")


def require_flag() -> dict:
    """Fetch the flag, or explain how to create it."""
    flag = get_flag()
    if flag is None:
        raise SetupError(
            f"Flag '{config.FLAG_KEY}' does not exist in project "
            f"'{config.LD_PROJECT_KEY}'.\n\n"
            "  This project measures the flag from the 'Part 2 Target' demo rather\n"
            "  than creating its own. Create it first — README.md -> Step 4 has the\n"
            "  click-by-click steps — then re-run this script."
        )

    values = {str(v["value"]) for v in flag["variations"]}
    for needed in (config.EXPERIMENT_CONTROL, config.EXPERIMENT_TREATMENT):
        if needed not in values:
            raise SetupError(
                f"The flag exists but has no '{needed}' variation (it has: "
                f"{', '.join(sorted(values))}).\n\n"
                "  The experiment compares the 'control' and 'spotlight' variations.\n"
                "  Either add them to the flag, or point EXPERIMENT_CONTROL /\n"
                "  EXPERIMENT_TREATMENT in config.py at variations you do have."
            )
    return flag


def variation_ids(flag: dict) -> dict[str, str]:
    """Map each variation's value to the UUID LaunchDarkly assigned it.

    Experiment treatments reference variations by id, not by index or value, so
    we always have to read the flag before we can define the experiment.
    """
    return {str(v["value"]): v["_id"] for v in flag["variations"]}


def flag_environment(flag: dict) -> dict:
    env = flag.get("environments", {}).get(config.LD_ENVIRONMENT_KEY)
    if env is None:
        raise SetupError(
            f"Environment '{config.LD_ENVIRONMENT_KEY}' not found on this flag. "
            f"Set LD_ENVIRONMENT_KEY in .env to the environment's short key "
            f"(e.g. 'test' or 'production')."
        )
    return env


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def get_metric(key: str) -> dict | None:
    response = requests.get(
        _url(f"/metrics/{config.LD_PROJECT_KEY}/{key}"),
        headers=_headers(),
        timeout=TIMEOUT,
    )
    if response.status_code == 404:
        return None
    return _check(response, f"Reading metric '{key}'")


def create_metric(definition: dict) -> dict:
    """Create one metric, tolerating the two spellings of the analysis-unit field.

    LaunchDarkly has used both `analysisUnits` and `randomizationUnits` for this
    field. Rather than guess, this sends the current spelling and retries
    without it if the API rejects it — the field is optional and defaults to the
    project's default randomisation unit, which is `user`.
    """
    body = dict(definition)
    body["analysisUnits"] = [config.RANDOMIZATION_UNIT]

    response = requests.post(
        _url(f"/metrics/{config.LD_PROJECT_KEY}"),
        json=body, headers=_headers(), timeout=TIMEOUT,
    )
    if response.status_code == 400:
        body.pop("analysisUnits", None)
        response = requests.post(
            _url(f"/metrics/{config.LD_PROJECT_KEY}"),
            json=body, headers=_headers(), timeout=TIMEOUT,
        )
    return _check(response, f"Creating metric '{definition['key']}'")


def ensure_metrics() -> list[str]:
    """Create any metric that does not exist yet. Returns the keys in use."""
    wanted = [PRIMARY_METRIC]
    if config.TRACK_SECONDARY_METRIC:
        wanted.append(SECONDARY_METRIC)

    keys = []
    for definition in wanted:
        existing = get_metric(definition["key"])
        if existing:
            kind = "numeric" if existing.get("isNumeric") else "conversion"
            print(f"  Metric '{definition['key']}' already exists ({kind}) — leaving it alone.")
            if existing.get("eventKey") and existing["eventKey"] != definition["eventKey"]:
                print(f"    WARNING: its event key is '{existing['eventKey']}', but this app sends")
                print(f"             '{definition['eventKey']}'. Nothing will be recorded.")
        else:
            create_metric(definition)
            kind = "numeric" if definition["isNumeric"] else "conversion"
            print(f"  Created {kind} metric '{definition['key']}'.")
        keys.append(definition["key"])
    return keys


# ---------------------------------------------------------------------------
# The experiment
# ---------------------------------------------------------------------------


def get_experiment() -> dict | None:
    response = requests.get(
        _url(_env_path(f"/{config.EXPERIMENT_KEY}")),
        params={"expand": "previousIterations,draftIteration"},
        headers=_headers(),
        timeout=TIMEOUT,
    )
    if response.status_code == 404:
        return None
    return _check(response, "Reading the experiment")


def build_iteration(flag: dict, metric_keys: list[str]) -> dict:
    """The iteration definition: what is being compared, and how."""
    ids = variation_ids(flag)
    env = flag_environment(flag)

    metrics = [{"key": key, "isGroup": False, "primary": index == 0}
               for index, key in enumerate(metric_keys)]

    return {
        "hypothesis": EXPERIMENT_HYPOTHESIS,
        "metrics": metrics,
        # Which metric decides the experiment. The others are watched but do not
        # determine the winner — being explicit about this before you start is
        # what stops the result being chosen after the fact from whichever
        # metric happened to move.
        "primarySingleMetricKey": metric_keys[0],
        "treatments": [
            {
                "name": "Control",
                # The baseline every other arm is compared against.
                "baseline": True,
                "allocationPercent": "50",
                "parameters": [
                    {"flagKey": config.FLAG_KEY, "variationId": ids[config.EXPERIMENT_CONTROL]},
                ],
            },
            {
                "name": "Spotlight",
                "baseline": False,
                "allocationPercent": "50",
                "parameters": [
                    {"flagKey": config.FLAG_KEY, "variationId": ids[config.EXPERIMENT_TREATMENT]},
                ],
            },
        ],
        "flags": {
            config.FLAG_KEY: {
                # "fallthrough" attaches the experiment to the flag's DEFAULT
                # RULE. Everyone caught by an individual target or a targeting
                # rule above it never reaches the experiment — which is exactly
                # why the Part 2 targeting can stay in place.
                "ruleId": "fallthrough",
                # Guards against a concurrent edit: if someone changes the flag
                # between this read and this write, LaunchDarkly rejects it
                # rather than attaching the experiment to targeting you did not
                # look at.
                "flagConfigVersion": env["_version"],
                # What visitors get if they are excluded from the experiment
                # while it runs. The control, always — the fail-safe direction
                # is the experience that already worked.
                "notInExperimentVariationId": ids[config.EXPERIMENT_CONTROL],
            }
        },
        "randomizationUnit": config.RANDOMIZATION_UNIT,
        # False keeps every subject in the arm they were first assigned to.
        # Reshuffling mid-flight lets LaunchDarkly rebalance traffic, but it
        # means a returning visitor can switch heroes, which muddies a test
        # whose whole premise is a consistent experience.
        "canReshuffleTraffic": False,
    }


def create_experiment(flag: dict, metric_keys: list[str]) -> dict:
    body = {
        "key": config.EXPERIMENT_KEY,
        "name": EXPERIMENT_NAME,
        "description": (
            "Measures the impact of the landing page hero revamp. Created by "
            "scripts/setup_launchdarkly.py."
        ),
        "iteration": build_iteration(flag, metric_keys),
        # Bayesian is LaunchDarkly's default and what the Experiments tab shows
        # as "probability to beat control".
        "methodology": "bayesian",
        "tags": ["demo", "landing-page"],
    }
    return _check(
        requests.post(_url(_env_path()), json=body, headers=_headers(), timeout=TIMEOUT),
        "Creating the experiment",
    )


def create_iteration(flag: dict, metric_keys: list[str]) -> dict:
    """Create a fresh draft iteration on an existing experiment.

    Needed when the previous iteration has already been stopped: an experiment
    is a container, and each run of it is an iteration with its own bucketing
    seed and its own results.
    """
    body = build_iteration(flag, metric_keys)
    return _check(
        requests.post(
            _url(_env_path(f"/{config.EXPERIMENT_KEY}/iterations")),
            json=body, headers=_headers(), timeout=TIMEOUT,
        ),
        "Creating a new iteration",
    )


def patch_experiment(instructions: list[dict], comment: str) -> dict:
    body = {"instructions": instructions, "comment": comment}
    return _check(
        requests.patch(
            _url(_env_path(f"/{config.EXPERIMENT_KEY}")),
            json=body, headers=_headers(semantic=True), timeout=TIMEOUT,
        ),
        "Updating the experiment",
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _iteration_status(experiment: dict) -> str:
    current = experiment.get("currentIteration") or {}
    return current.get("status", "none")


def status() -> None:
    """Print what currently exists, and what is still missing."""
    print(f"Project     : {config.LD_PROJECT_KEY}")
    print(f"Environment : {config.LD_ENVIRONMENT_KEY}")
    print()

    flag = get_flag()
    if flag is None:
        print(f"Flag        : '{config.FLAG_KEY}' DOES NOT EXIST — see README Step 4.")
    else:
        env = flag_environment(flag)
        variations = ", ".join(str(v["value"]) for v in flag["variations"])
        print(f"Flag        : {config.FLAG_KEY}  (targeting {'ON' if env.get('on') else 'OFF'})")
        print(f"  variations: {variations}")

        fallthrough = env.get("fallthrough", {})
        rollout = fallthrough.get("rollout") or {}
        if rollout.get("kind") == "experiment":
            print("  default rule: EXPERIMENT ROLLOUT — an experiment is live on this flag.")
        elif rollout:
            print("  default rule: percentage rollout (NOT an experiment — nothing is measured)")
        else:
            index = fallthrough.get("variation")
            served = flag["variations"][index]["value"] if isinstance(index, int) else "?"
            print(f"  default rule: serves '{served}' outright (no experiment running)")

        rules = env.get("rules", [])
        targets = env.get("targets", []) + env.get("contextTargets", [])
        if rules or targets:
            print(f"  {len(targets)} individual target group(s), {len(rules)} rule(s) — "
                  "these are evaluated BEFORE the experiment and are excluded from it.")

    print()
    for definition in (PRIMARY_METRIC, SECONDARY_METRIC):
        metric = get_metric(definition["key"])
        if metric:
            kind = "numeric" if metric.get("isNumeric") else "conversion"
            print(f"Metric      : {definition['key']}  ({kind}, event '{metric.get('eventKey')}')")
        else:
            print(f"Metric      : {definition['key']}  MISSING")

    print()
    experiment = get_experiment()
    if experiment is None:
        print(f"Experiment  : '{config.EXPERIMENT_KEY}' does not exist yet.")
        print("              Run this script with no arguments to create it.")
        return

    state = _iteration_status(experiment)
    print(f"Experiment  : {experiment.get('name')}  [{config.EXPERIMENT_KEY}]")
    print(f"  iteration : {state}")
    current = experiment.get("currentIteration") or {}
    if current.get("startedAt"):
        print(f"  started   : {current['startedAt']}")
    if current.get("primarySingleMetricKey"):
        print(f"  decided on: {current['primarySingleMetricKey']}")
    for treatment in current.get("treatments", []):
        marker = " (baseline)" if treatment.get("baseline") else ""
        print(f"  arm       : {treatment.get('name')} @ {treatment.get('allocationPercent')}%{marker}")

    print()
    if state == "running":
        print("This experiment is RUNNING. Send it traffic:")
        print("    python simulate_traffic.py")
    else:
        print("This experiment is NOT running, so no data is being collected. Start it:")
        print("    python scripts/setup_launchdarkly.py --start")


def setup() -> None:
    """Create the metrics and the experiment, without starting it."""
    flag = require_flag()
    print(f"Flag '{config.FLAG_KEY}' found — re-using it, as the brief asks.\n")

    print("Metrics:")
    metric_keys = ensure_metrics()
    print()

    experiment = get_experiment()
    if experiment is not None:
        print(f"Experiment '{config.EXPERIMENT_KEY}' already exists "
              f"(iteration: {_iteration_status(experiment)}) — leaving it alone.")
    else:
        create_experiment(flag, metric_keys)
        print(f"Created experiment '{config.EXPERIMENT_KEY}':")
        print(f"  control   : {config.EXPERIMENT_CONTROL} @ 50%  (baseline)")
        print(f"  treatment : {config.EXPERIMENT_TREATMENT} @ 50%")
        print(f"  decided on: {metric_keys[0]}")
        print(f"  attached  : the flag's default rule, randomised on '{config.RANDOMIZATION_UNIT}'")

    print()
    print("NOT started yet — creating and starting are separate steps on purpose.")
    print("When you are ready to begin collecting data:")
    print("    python scripts/setup_launchdarkly.py --start")


def start() -> None:
    """Start collecting data.

    Starting the iteration is what rewrites the flag's default rule into an
    experiment rollout. Until this happens the SDK evaluates the flag perfectly
    and measures nothing.
    """
    experiment = get_experiment()
    if experiment is None:
        raise SetupError(
            f"Experiment '{config.EXPERIMENT_KEY}' does not exist. Create it first:\n"
            "      python scripts/setup_launchdarkly.py"
        )

    state = _iteration_status(experiment)
    if state == "running":
        print(f"Experiment '{config.EXPERIMENT_KEY}' is already running. Nothing to do.")
        return

    if state == "stopped":
        # A stopped iteration cannot be restarted; it needs a fresh one, with a
        # new bucketing seed. That is a feature — resuming a stopped test after
        # peeking at its results is how you talk yourself into a false positive.
        print("The previous iteration is stopped. Creating a new one…")
        flag = require_flag()
        metric_keys = [PRIMARY_METRIC["key"]]
        if config.TRACK_SECONDARY_METRIC:
            metric_keys.append(SECONDARY_METRIC["key"])
        create_iteration(flag, metric_keys)

    patch_experiment(
        [{"kind": "startIteration", "changeJustification": "Started by scripts/setup_launchdarkly.py"}],
        "Start the landing page hero experiment",
    )
    print(f"Experiment '{config.EXPERIMENT_KEY}' is now RUNNING.")
    print()
    print("The flag's default rule is now an experiment rollout. Verify from the")
    print("application's point of view, which is the only view that matters:")
    print("    python simulate_traffic.py --check")
    print()
    print("Then send it traffic:")
    print("    python simulate_traffic.py")


def stop() -> None:
    """Stop the current iteration and freeze its results."""
    experiment = get_experiment()
    if experiment is None:
        raise SetupError(f"Experiment '{config.EXPERIMENT_KEY}' does not exist.")

    state = _iteration_status(experiment)
    if state != "running":
        print(f"Experiment '{config.EXPERIMENT_KEY}' is not running (status: {state}).")
        return

    patch_experiment(
        [{
            "kind": "stopIteration",
            "winningReason": "Stopped by scripts/setup_launchdarkly.py",
        }],
        "Stop the landing page hero experiment",
    )
    print(f"Experiment '{config.EXPERIMENT_KEY}' stopped. Its results are preserved in")
    print("LaunchDarkly under the experiment's iteration history.")
    print()
    print("The flag's default rule reverts to serving the notInExperiment variation")
    print(f"('{config.EXPERIMENT_CONTROL}'). Roll the winner out properly by editing the")
    print("flag's default rule — an experiment decides, a rollout ships.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create, start, and stop the landing page experiment in LaunchDarkly.",
        epilog="Run with no arguments to create the metrics and the experiment.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--start", action="store_true", help="Start collecting data")
    group.add_argument("--stop", action="store_true", help="Stop the current iteration")
    group.add_argument("--status", action="store_true", help="Show what exists right now")
    args = parser.parse_args()

    if not config.LD_API_TOKEN:
        print(
            "error: LD_API_TOKEN is not set.\n\n"
            "  This script uses the LaunchDarkly REST API, which needs an access\n"
            "  token — a different credential from the SDK key.\n\n"
            "  LaunchDarkly UI: Account settings -> Authorization -> Create token,\n"
            "  with the built-in 'Writer' role. Then add it to your .env:\n\n"
            "      LD_API_TOKEN=api-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx\n\n"
            "  You can skip this script entirely and build the metrics and the\n"
            "  experiment by hand — see README.md, Steps 5 to 7.",
            file=sys.stderr,
        )
        return 1

    try:
        if args.status:
            status()
        elif args.start:
            start()
        elif args.stop:
            stop()
        else:
            setup()
    except SetupError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"\nerror: could not reach the LaunchDarkly API: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
