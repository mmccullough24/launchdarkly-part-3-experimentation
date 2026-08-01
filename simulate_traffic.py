"""Drive enough traffic through the experiment to actually decide something.

This is the "Measure" half of the brief. An experiment with fifty visitors tells
you nothing; the honest answer to "is the new hero better?" needs a few thousand
subjects per arm, and clicking a button in a browser that many times is not a
demo anyone wants to watch. So this script plays the part of ABC Company's
landing page traffic: it evaluates the flag for thousands of distinct visitors,
decides whether each one converts, and sends the metric events to LaunchDarkly
exactly as the real application would.

Everything it sends is real. The SDK calls here are the same two calls the Flask
app makes for a human clicking a button:

    evaluation = ld_client.evaluate(context)      # exposure — allocates the arm
    ld_client.track_conversion(context, ...)      # the metric event

What is *simulated* is only the human: whether a given visitor would have
clicked. That decision is a coin flip weighted by a conversion rate this script
invents (`--control-rate` and `--lift`) and LaunchDarkly knows nothing about.
Those numbers are the ground truth the experiment is trying to recover, which is
what makes this a useful rehearsal: you can check LaunchDarkly's answer against
the truth, because for once you know it.

Usage:

    python simulate_traffic.py                      # 8,000 visitors, +30% lift
    python simulate_traffic.py --visitors 20000     # more data, tighter interval
    python simulate_traffic.py --lift 0             # an A/A test: no real effect
    python simulate_traffic.py --check              # preflight: is it running?

Run `python analysis.py --sample-size` first if you want to choose `--visitors`
deliberately rather than accepting the default.
"""

import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
from datetime import datetime, timezone

import config
import contexts
import ld_client
from analysis import report

# How much more or less likely each device type is to convert, before
# normalisation. Landing pages really do convert worse on mobile; the effect is
# here so the simulated data has some structure in it rather than being a pure
# coin flip, and so there is something to segment by in LaunchDarkly.
#
# These are normalised at runtime against the actual device mix so that the
# population-wide conversion rate still comes out at exactly `--control-rate`.
# Without that, changing the device mix would silently move the baseline.
_DEVICE_MULTIPLIER = {"mobile": 0.90, "desktop": 1.17, "tablet": 1.05}
_DEVICE_MIX = {"mobile": 0.62, "desktop": 0.33, "tablet": 0.05}

# Spread of order values around the mean, as the sigma of a lognormal. Order
# values are right-skewed in practice — a few large orders, many small ones —
# and a lognormal is the usual cheap way to reproduce that.
_ORDER_VALUE_SIGMA = 0.55

_RULE = "=" * 78


# ---------------------------------------------------------------------------
# The simulated human
# ---------------------------------------------------------------------------


def _device_normaliser() -> float:
    """The constant that makes the device effect average out to 1.0."""
    return sum(_DEVICE_MIX[device] * multiplier for device, multiplier in _DEVICE_MULTIPLIER.items())


def _visitor_rng(key: str, seed: int) -> random.Random:
    """A private random stream for one visitor, derived from their key.

    Deliberately NOT one shared generator consumed in a loop. With a single
    stream, a visitor who converts draws an extra number (their order value),
    which shifts every later visitor's position in the sequence — so whether
    visitor N converts depends on how many visitors before them converted. That
    coupling is small but it is a real bias, and it is exactly the kind of
    artefact that makes a simulated experiment quietly under-report its own
    effect size.

    Seeding per visitor from a hash of their key makes each draw independent of
    every other, and independent of the order they are processed in. The run
    stays fully reproducible from `--seed`.
    """
    digest = hashlib.sha256(f"{seed}:{key}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def conversion_probability(
    variation: str,
    device_type: str,
    control_rate: float,
    lift: float,
    normaliser: float,
) -> float:
    """How likely this visitor is to click the CTA.

    This function is the entire "ground truth" of the simulation. Note what it
    does *not* depend on: anything LaunchDarkly knows. The experiment has to
    discover the effect of `variation` from noisy samples, exactly as a real one
    would.
    """
    base = control_rate * (1 + lift) if variation == config.EXPERIMENT_TREATMENT else control_rate
    adjusted = base * _DEVICE_MULTIPLIER.get(device_type, 1.0) / normaliser
    return min(max(adjusted, 0.0), 1.0)


def order_value(rng: random.Random, variation: str, control_mean: float, treatment_mean: float) -> float:
    """A right-skewed order value for a visitor who converted."""
    mean = treatment_mean if variation == config.EXPERIMENT_TREATMENT else control_mean
    # For a lognormal, E[X] = exp(mu + sigma^2/2); solve for mu to hit `mean`.
    mu = math.log(mean) - (_ORDER_VALUE_SIGMA ** 2) / 2
    return rng.lognormvariate(mu, _ORDER_VALUE_SIGMA)


# ---------------------------------------------------------------------------
# Accumulators
# ---------------------------------------------------------------------------


def _new_arm() -> dict:
    """Running totals for one arm. Sums rather than lists, so memory is flat."""
    return {
        "exposures": 0,
        "conversions": 0,
        "orderCount": 0,
        "orderSum": 0.0,
        "orderSumSquares": 0.0,
    }


def _new_exclusions() -> dict:
    """Why visitors did not make it into the experiment. All of these are
    legitimate reasons except `notInExperiment`, which means it is not running."""
    return {"individual": 0, "rule": 0, "notInExperiment": 0, "off": 0, "error": 0}


def _classify_exclusion(evaluation: dict) -> str:
    """Bucket a non-experiment evaluation by why it was excluded."""
    mechanism = evaluation["mechanism"]
    if mechanism == "individual":
        return "individual"
    if mechanism == "rule":
        return "rule"
    if mechanism == "off":
        return "off"
    if mechanism == "error":
        return "error"
    return "notInExperiment"


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def check_experiment(sample: int = 20) -> bool:
    """Evaluate a few visitors and report whether the experiment is running.

    Worth doing before a long run. The failure this catches — a flag that
    evaluates perfectly but is not attached to a started experiment iteration —
    is invisible from the application's point of view: every visitor gets a
    sensible hero, no errors are logged, and no data is collected. You would
    only notice hours later, looking at an empty Experiments tab.
    """
    print("Preflight check — evaluating a small sample…\n")

    in_experiment = 0
    variations: dict[str, int] = {}
    first_reason = None

    for visitor in contexts.generate_population(sample, seed=config.SIM_SEED):
        evaluation = ld_client.evaluate(contexts.context_from_attributes(visitor))
        variations[evaluation["variation"]] = variations.get(evaluation["variation"], 0) + 1
        if evaluation["inExperiment"]:
            in_experiment += 1
        if first_reason is None:
            first_reason = evaluation

    print(f"  Flag              : {config.FLAG_KEY}")
    print(f"  Sampled           : {sample} visitors")
    print(f"  Allocated by an experiment : {in_experiment}/{sample}")
    print(f"  Variations seen   : {', '.join(f'{k} x{v}' for k, v in sorted(variations.items()))}")
    print(f"  Example reason    : {first_reason['reasonKind']} "
          f"(inExperiment={first_reason['inExperiment']})")
    print()

    if in_experiment == 0:
        print(_RULE)
        print("  NO EXPERIMENT IS RUNNING ON THIS FLAG.")
        print(_RULE)
        print(f"  Reason given for the sample: {first_reason['reasonText']}")
        print()
        print("  The flag evaluates fine — visitors are being served a hero — but nothing")
        print("  is being measured. Work down this list:")
        print()
        print("    1. Has the experiment ITERATION been started? Creating an experiment is")
        print("       not enough. In LaunchDarkly: Experiments -> your experiment ->")
        print("       'Start'. Or run:  python scripts/setup_launchdarkly.py --start")
        print("    2. Is the experiment attached to the flag's DEFAULT RULE, and is that")
        print("       rule now showing as an experiment rollout on the Targeting tab?")
        print("    3. Are you pointed at the same ENVIRONMENT the experiment lives in?")
        print("       The SDK key decides that, not the environment selector in the UI.")
        print("    4. Is the flag's top toggle on?")
        print()
        print("  See README.md -> Troubleshooting.")
        return False

    if in_experiment < sample:
        print(f"  Note: {sample - in_experiment} of {sample} were excluded before reaching the")
        print("  experiment, which is expected — they matched an individual target or a")
        print("  targeting rule from the Part 2 demo. See the exclusion breakdown after a run.")

    print("  Experiment is running. Ready to simulate traffic.")
    return True


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def simulate(args: argparse.Namespace) -> dict:
    """Evaluate the flag for every simulated visitor and send their events."""
    normaliser = _device_normaliser()

    arms = {config.EXPERIMENT_CONTROL: _new_arm(), config.EXPERIMENT_TREATMENT: _new_arm()}
    other_variations: dict[str, int] = {}
    excluded = _new_exclusions()

    started = time.perf_counter()
    last_progress = started
    processed = 0

    print(f"Simulating {args.visitors:,} visitors at ~{args.rate:,.0f}/second…")
    print("(Ctrl-C stops early; everything sent so far is kept and analysed.)\n")

    try:
        for index, visitor in enumerate(contexts.generate_population(args.visitors, args.seed)):
            context = contexts.context_from_attributes(visitor)

            # ---- EXPOSURE -------------------------------------------------
            # This single call allocates the visitor to an arm and records the
            # exposure. Nothing else is needed to enrol them.
            evaluation = ld_client.evaluate(context)
            processed += 1

            if not evaluation["inExperiment"]:
                excluded[_classify_exclusion(evaluation)] += 1
            elif evaluation["variation"] in arms:
                arm = arms[evaluation["variation"]]
                arm["exposures"] += 1

                # ---- THE SIMULATED HUMAN ------------------------------------
                rng = _visitor_rng(visitor["key"], args.seed)
                probability = conversion_probability(
                    evaluation["variation"], visitor["deviceType"],
                    args.control_rate, args.lift, normaliser,
                )
                if rng.random() < probability:
                    # ---- CONVERSION EVENT -----------------------------------
                    # Same context object as the exposure. That is what lets
                    # LaunchDarkly attribute this click to the right arm.
                    ld_client.track_conversion(context, evaluation["variation"])
                    arm["conversions"] += 1

                    value = order_value(
                        rng, evaluation["variation"],
                        args.control_order_value, args.treatment_order_value,
                    )
                    ld_client.track_order_value(context, evaluation["variation"], value)
                    arm["orderCount"] += 1
                    arm["orderSum"] += value
                    arm["orderSumSquares"] += value * value
            else:
                # An experiment allocation to a variation outside the two we
                # expected — someone added a third treatment in the UI.
                other_variations[evaluation["variation"]] = \
                    other_variations.get(evaluation["variation"], 0) + 1

            # ---- KEEP THE EVENT BUFFER DRAINED -----------------------------
            # The SDK buffers events in memory and drops them silently once the
            # buffer is full. Flushing on a fixed cadence keeps it well under
            # that limit and makes data appear in LaunchDarkly as the run goes,
            # instead of all at the end.
            if (index + 1) % args.flush_every == 0:
                ld_client.flush()

            # ---- PACING ----------------------------------------------------
            if args.rate > 0:
                drift = (index + 1) / args.rate - (time.perf_counter() - started)
                if drift > 0.005:
                    time.sleep(drift)

            now = time.perf_counter()
            if now - last_progress >= 1.0 or index + 1 == args.visitors:
                _print_progress(index + 1, args.visitors, arms, now - started)
                last_progress = now

    except KeyboardInterrupt:
        print("\n\nStopped early. Analysing what was collected so far.\n")

    print("\n\nFlushing remaining events to LaunchDarkly…")
    ld_client.flush()

    elapsed = time.perf_counter() - started
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "offline": config.OFFLINE_DEMO,
        "flagKey": config.FLAG_KEY,
        "experimentKey": config.EXPERIMENT_KEY,
        "primaryMetricKey": config.PRIMARY_METRIC_KEY,
        "secondaryMetricKey": config.SECONDARY_METRIC_KEY if config.TRACK_SECONDARY_METRIC else None,
        "controlVariation": config.EXPERIMENT_CONTROL,
        "treatmentVariation": config.EXPERIMENT_TREATMENT,
        "visitorsProcessed": processed,
        "elapsedSeconds": round(elapsed, 1),
        "arms": arms,
        "excluded": excluded,
        "unexpectedVariations": other_variations,
        "settings": {
            "visitors": args.visitors,
            "rate": args.rate,
            "seed": args.seed,
            "controlRate": args.control_rate,
            "lift": args.lift,
            "controlOrderValue": args.control_order_value,
            "treatmentOrderValue": args.treatment_order_value,
        },
        # The truth the experiment is trying to recover. Reported so you can
        # compare it with what LaunchDarkly concluded.
        "groundTruth": {
            "controlRate": args.control_rate,
            "treatmentRate": args.control_rate * (1 + args.lift),
            "relativeLift": args.lift,
        },
    }


def _print_progress(done: int, total: int, arms: dict, elapsed: float) -> None:
    """A single rewriting progress line."""
    width = 28
    filled = int(width * done / total) if total else width
    bar = "#" * filled + "." * (width - filled)
    rate = done / elapsed if elapsed > 0 else 0

    parts = []
    for name, arm in arms.items():
        conversion_rate = (arm["conversions"] / arm["exposures"] * 100) if arm["exposures"] else 0.0
        parts.append(f"{name} {arm['exposures']:,}/{conversion_rate:.1f}%")

    sys.stdout.write(f"\r  [{bar}] {done:,}/{total:,}  {rate:,.0f}/s   " + "   ".join(parts) + "   ")
    sys.stdout.flush()


def save(results: dict) -> str:
    """Write the run summary so analysis.py can re-read it later."""
    os.makedirs(config.RESULTS_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = os.path.join(config.RESULTS_DIR, f"run-{stamp}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send simulated landing page traffic through a LaunchDarkly experiment.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--visitors", type=int, default=config.SIM_VISITORS,
                        help="How many distinct visitors to simulate")
    parser.add_argument("--rate", type=float, default=config.SIM_RATE_PER_SECOND,
                        help="Visitors per second; 0 for no throttling")
    parser.add_argument("--seed", type=int, default=config.SIM_SEED,
                        help="Random seed — same seed, same run")
    parser.add_argument("--control-rate", type=float, default=config.SIM_CONTROL_CONVERSION_RATE,
                        help="TRUE conversion rate of the control hero")
    parser.add_argument("--lift", type=float, default=config.SIM_LIFT,
                        help="TRUE relative lift of the treatment; 0 simulates no effect")
    parser.add_argument("--control-order-value", type=float, default=config.SIM_CONTROL_ORDER_VALUE,
                        help="TRUE mean order value (USD) for the control")
    parser.add_argument("--treatment-order-value", type=float, default=config.SIM_TREATMENT_ORDER_VALUE,
                        help="TRUE mean order value (USD) for the treatment")
    parser.add_argument("--flush-every", type=int, default=500,
                        help="Flush the SDK event buffer every N visitors")
    parser.add_argument("--check", action="store_true",
                        help="Only run the preflight check, then exit")
    parser.add_argument("--no-check", action="store_true",
                        help="Skip the preflight check and simulate regardless")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print(_RULE)
    print("  ABC Company — landing page experiment, simulated traffic")
    print(_RULE)

    if not ld_client.initialize(high_volume=True):
        print(
            "\n*** LaunchDarkly did not initialize.\n"
            "    Check that LAUNCHDARKLY_SDK_KEY in your .env is a valid server-side\n"
            "    SDK key (it starts with 'sdk-') and that this machine can reach\n"
            "    https://stream.launchdarkly.com.\n"
            "    No account handy? Run:  OFFLINE_DEMO=1 python simulate_traffic.py\n"
            "    See README.md -> Troubleshooting.\n",
            file=sys.stderr,
        )
        return 1

    connection = ld_client.describe_connection()
    print(f"  SDK status    : {connection['mode']}")
    print(f"  Feature flag  : {config.FLAG_KEY}")
    print(f"  Primary metric: {config.PRIMARY_METRIC_KEY}")
    if config.TRACK_SECONDARY_METRIC:
        print(f"  Secondary     : {config.SECONDARY_METRIC_KEY} (numeric)")
    print(_RULE)
    print()

    try:
        if not args.no_check:
            running = check_experiment()
            print()
            if args.check:
                return 0 if running else 1
            if not running:
                print("Refusing to simulate traffic into an experiment that is not running —")
                print("the events would be recorded against the flag but attributed to nothing.")
                print("Fix the above, or pass --no-check to simulate anyway.\n")
                return 1
        elif args.check:
            print("--check and --no-check are contradictory; nothing to do.\n", file=sys.stderr)
            return 2

        results = simulate(args)
        path = save(results)

        print(f"Saved to {path}\n")
        print(report(results))

        if not config.OFFLINE_DEMO:
            print()
            print("  NEXT: open LaunchDarkly -> Experiments -> "
                  f"{config.EXPERIMENT_KEY} to see its own analysis.")
            print("  Events are ingested in batches, so allow a few minutes before the")
            print("  numbers there settle. LaunchDarkly's Bayesian result will not match")
            print("  the frequentist figures above digit for digit — see README.md ->")
            print("  'Reading the results'.")
        return 0

    finally:
        # Always closes cleanly, including after Ctrl-C. `close()` blocks until
        # the final batch of events has been handed off — skipping it is the
        # most common reason experiment data never arrives.
        ld_client.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
