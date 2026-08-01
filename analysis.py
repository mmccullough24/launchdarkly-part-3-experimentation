"""Read an experiment's results and say whether it has decided anything.

**This is not how you analyse a LaunchDarkly experiment.** LaunchDarkly does
that for you, on the Experiments tab, using a Bayesian model. This module exists
for three narrower reasons:

1. **To make the numbers auditable.** Everything the simulator sends is counted
   here too, so you can see that the figures LaunchDarkly reports are the ones
   your app actually produced, and diagnose the difference if they are not.
2. **To answer "is it done yet?" before you have the data.** `--sample-size`
   computes how many visitors an experiment needs *before* you start it, which
   is the question the "run it long enough" part of the brief is really about.
3. **To be readable.** The arithmetic behind every number below is a dozen lines
   of standard-library Python, not a library call.

The method here is frequentist (a two-proportion z-test) because that is what is
simplest to verify by hand. LaunchDarkly reports a Bayesian probability instead.
On a clean experiment with a few thousand subjects per arm the two approaches
agree about the direction and about whether there is signal; they answer subtly
different questions, and the README explains where that matters.

Run it directly:

    python analysis.py                      # analyse the most recent run
    python analysis.py results/run-xyz.json # analyse a specific run
    python analysis.py --sample-size        # how many visitors do I need?
"""

import argparse
import glob
import json
import math
import os
import sys

import config

# Two-sided 95% is the convention this module reports at. Change it here and
# every printed interval and p-value threshold moves with it.
DEFAULT_ALPHA = 0.05
DEFAULT_POWER = 0.80


# ---------------------------------------------------------------------------
# The normal distribution, from scratch
# ---------------------------------------------------------------------------


def normal_cdf(z: float) -> float:
    """P(Z <= z) for a standard normal, via the error function."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def normal_quantile(p: float) -> float:
    """The inverse of `normal_cdf` — the z with P(Z <= z) = p.

    Acklam's rational approximation, accurate to about 1.15e-9 across the whole
    range. Used only to turn a confidence level into a critical value, so even a
    much cruder approximation would do.
    """
    if not 0.0 < p < 1.0:
        raise ValueError("normal_quantile expects 0 < p < 1")

    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)

    plow, phigh = 0.02425, 1 - 0.02425

    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)

    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


# ---------------------------------------------------------------------------
# Conversion metrics — the two-proportion z-test
# ---------------------------------------------------------------------------


def two_proportion_test(
    control_n: int,
    control_x: int,
    treatment_n: int,
    treatment_x: int,
    alpha: float = DEFAULT_ALPHA,
) -> dict:
    """Compare two conversion rates.

    `*_n` is the number of visitors exposed to that arm, `*_x` the number who
    converted.

    Two different standard errors appear below, on purpose:

    * the **pooled** SE assumes the null hypothesis (both arms share one true
      rate) and is the right denominator for the test statistic;
    * the **unpooled** SE does not, and is the right one for a confidence
      interval around the observed difference.

    Using one for both is a common and mostly harmless error; they are separated
    here because this file is meant to be read.
    """
    if control_n == 0 or treatment_n == 0:
        return {"valid": False, "reason": "One of the arms has no exposures."}

    p_control = control_x / control_n
    p_treatment = treatment_x / treatment_n
    absolute_diff = p_treatment - p_control

    pooled = (control_x + treatment_x) / (control_n + treatment_n)
    se_pooled = math.sqrt(pooled * (1 - pooled) * (1 / control_n + 1 / treatment_n))
    se_unpooled = math.sqrt(
        p_control * (1 - p_control) / control_n
        + p_treatment * (1 - p_treatment) / treatment_n
    )

    if se_pooled == 0:
        return {"valid": False, "reason": "No conversions in either arm yet."}

    z = absolute_diff / se_pooled
    p_value = 2 * (1 - normal_cdf(abs(z)))
    critical = normal_quantile(1 - alpha / 2)

    return {
        "valid": True,
        "controlN": control_n,
        "controlX": control_x,
        "controlRate": p_control,
        "treatmentN": treatment_n,
        "treatmentX": treatment_x,
        "treatmentRate": p_treatment,
        "absoluteDiff": absolute_diff,
        "relativeLift": (absolute_diff / p_control) if p_control else float("nan"),
        "z": z,
        "pValue": p_value,
        "ciLow": absolute_diff - critical * se_unpooled,
        "ciHigh": absolute_diff + critical * se_unpooled,
        "significant": p_value < alpha,
        # A normal approximation to the posterior probability that the treatment
        # is genuinely better, under flat priors. This is the closest thing here
        # to the number LaunchDarkly shows, and it is only an approximation —
        # LaunchDarkly models the posterior properly rather than assuming it is
        # normal. Expect the same story, not the same digits.
        "probTreatmentBetter": normal_cdf(absolute_diff / se_unpooled) if se_unpooled else float("nan"),
        "alpha": alpha,
    }


def required_sample_size(
    baseline_rate: float,
    relative_lift: float,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_POWER,
) -> int:
    """Visitors needed **per arm** to detect `relative_lift` if it is real.

    This is the number that answers "how long do we run it?". Divide it by your
    daily traffic (times the share of traffic allocated to the experiment) and
    you have the answer in days — then round up to a whole number of weeks, for
    reasons the README goes into.

    Deciding this *before* starting is what stops an experiment being stopped
    the first afternoon it happens to look good.
    """
    treatment_rate = baseline_rate * (1 + relative_lift)
    difference = treatment_rate - baseline_rate
    if difference == 0:
        return 0

    z_alpha = normal_quantile(1 - alpha / 2)
    z_power = normal_quantile(power)
    numerator = (z_alpha + z_power) ** 2 * (
        baseline_rate * (1 - baseline_rate) + treatment_rate * (1 - treatment_rate)
    )
    return math.ceil(numerator / (difference ** 2))


# ---------------------------------------------------------------------------
# Numeric metrics — Welch's t-test
# ---------------------------------------------------------------------------


def welch_test(
    control_n: int,
    control_sum: float,
    control_sum_sq: float,
    treatment_n: int,
    treatment_sum: float,
    treatment_sum_sq: float,
    alpha: float = DEFAULT_ALPHA,
) -> dict:
    """Compare two means without assuming equal variances.

    Takes running totals rather than the raw values so the simulator never has
    to hold every order value in memory.

    The p-value uses a normal approximation to the t distribution. With the
    thousands of observations an experiment produces the difference is in the
    fourth decimal place; with a few dozen it would not be, and you would want a
    real t distribution.
    """
    if control_n < 2 or treatment_n < 2:
        return {"valid": False, "reason": "Not enough observations for a numeric comparison."}

    mean_control = control_sum / control_n
    mean_treatment = treatment_sum / treatment_n

    # Sample variance from running totals: (Σx² − nx̄²) / (n − 1).
    var_control = max(0.0, (control_sum_sq - control_n * mean_control ** 2) / (control_n - 1))
    var_treatment = max(0.0, (treatment_sum_sq - treatment_n * mean_treatment ** 2) / (treatment_n - 1))

    se = math.sqrt(var_control / control_n + var_treatment / treatment_n)
    if se == 0:
        return {"valid": False, "reason": "No variance in the observed values."}

    difference = mean_treatment - mean_control
    t = difference / se
    p_value = 2 * (1 - normal_cdf(abs(t)))
    critical = normal_quantile(1 - alpha / 2)

    return {
        "valid": True,
        "controlN": control_n,
        "controlMean": mean_control,
        "treatmentN": treatment_n,
        "treatmentMean": mean_treatment,
        "difference": difference,
        "relativeDiff": (difference / mean_control) if mean_control else float("nan"),
        "t": t,
        "pValue": p_value,
        "ciLow": difference - critical * se,
        "ciHigh": difference + critical * se,
        "significant": p_value < alpha,
        "alpha": alpha,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

_RULE = "=" * 78


def format_conversion_report(result: dict, control_name: str, treatment_name: str) -> str:
    """Render a two-proportion test as the table the demo prints."""
    if not result.get("valid"):
        return f"  Not enough data yet — {result.get('reason', 'unknown reason')}"

    lines = [
        f"  {'Arm':<12} {'Exposed':>10} {'Converted':>11} {'Rate':>9}",
        f"  {'-' * 12} {'-' * 10} {'-' * 11} {'-' * 9}",
        f"  {control_name:<12} {result['controlN']:>10,} {result['controlX']:>11,} "
        f"{result['controlRate'] * 100:>8.2f}%",
        f"  {treatment_name:<12} {result['treatmentN']:>10,} {result['treatmentX']:>11,} "
        f"{result['treatmentRate'] * 100:>8.2f}%",
        "",
        f"  Absolute difference : {result['absoluteDiff'] * 100:+.2f} percentage points",
        f"  Relative lift       : {result['relativeLift'] * 100:+.1f}%",
        f"  95% CI (absolute)   : [{result['ciLow'] * 100:+.2f}, {result['ciHigh'] * 100:+.2f}] pp",
        f"  p-value             : {result['pValue']:.5f}",
        f"  P(treatment better) : {result['probTreatmentBetter'] * 100:.1f}%",
    ]

    if result["significant"]:
        direction = "BETTER" if result["absoluteDiff"] > 0 else "WORSE"
        lines += [
            "",
            f"  VERDICT: significant at the {result['alpha']:.0%} level — the treatment is {direction}.",
            "           The confidence interval excludes zero, so 'no difference' is not a",
            "           plausible explanation for what was observed.",
        ]
    else:
        lines += [
            "",
            f"  VERDICT: not significant at the {result['alpha']:.0%} level.",
            "           The interval still contains zero. That is NOT the same as proving the",
            "           two heroes are equivalent — it means this much data cannot tell them",
            "           apart. Either collect more, or accept the effect is too small to chase.",
        ]
    return "\n".join(lines)


def format_numeric_report(result: dict, control_name: str, treatment_name: str, unit: str = "USD") -> str:
    """Render a Welch test as a short table."""
    if not result.get("valid"):
        return f"  Not enough data yet — {result.get('reason', 'unknown reason')}"

    lines = [
        f"  {'Arm':<12} {'Orders':>10} {'Mean':>12}",
        f"  {'-' * 12} {'-' * 10} {'-' * 12}",
        f"  {control_name:<12} {result['controlN']:>10,} {result['controlMean']:>11.2f} {unit}",
        f"  {treatment_name:<12} {result['treatmentN']:>10,} {result['treatmentMean']:>11.2f} {unit}",
        "",
        f"  Difference          : {result['difference']:+.2f} {unit} ({result['relativeDiff'] * 100:+.1f}%)",
        f"  95% CI              : [{result['ciLow']:+.2f}, {result['ciHigh']:+.2f}] {unit}",
        f"  p-value             : {result['pValue']:.5f}",
    ]
    if result["significant"] and result["difference"] < 0:
        lines += [
            "",
            "  GUARDRAIL WARNING: the treatment's average order value is significantly LOWER.",
            "  More clicks at a lower value each is not automatically a win — decide which of",
            "  these two metrics the business is actually optimising before you ship.",
        ]
    elif not result["significant"]:
        # Worth spelling out, because this metric is almost always the
        # underpowered one and it is not obvious why.
        smaller = min(result["controlN"], result["treatmentN"])
        lines += [
            "",
            "  NOT SIGNIFICANT — and note how few observations this metric has "
            f"({smaller:,} in the",
            "  smaller arm). Only visitors who CONVERTED contribute an order value, so a",
            "  numeric metric like this one sees a small fraction of the traffic the primary",
            "  conversion metric sees. It needs a much longer run to reach the same",
            "  confidence. Sizing an experiment on the primary metric alone routinely leaves",
            "  the guardrail metrics unable to detect the very regression they exist to catch.",
        ]
    return "\n".join(lines)


def report(data: dict) -> str:
    """Build the full report for a saved simulation run."""
    arms = data["arms"]
    control_key = data.get("controlVariation", config.EXPERIMENT_CONTROL)
    treatment_key = data.get("treatmentVariation", config.EXPERIMENT_TREATMENT)
    control = arms[control_key]
    treatment = arms[treatment_key]

    out = [
        _RULE,
        "  EXPERIMENT RESULTS (computed locally from what this app sent)",
        _RULE,
        f"  Flag          : {data.get('flagKey')}",
        f"  Experiment    : {data.get('experimentKey')}",
        f"  Run at        : {data.get('generatedAt')}",
        f"  Mode          : {'OFFLINE (nothing sent to LaunchDarkly)' if data.get('offline') else 'live'}",
        "",
        f"  PRIMARY METRIC — {data.get('primaryMetricKey')} (conversion)",
        "",
    ]

    conversion = two_proportion_test(
        control["exposures"], control["conversions"],
        treatment["exposures"], treatment["conversions"],
    )
    out.append(format_conversion_report(conversion, control_key, treatment_key))

    if data.get("secondaryMetricKey") and control.get("orderCount"):
        out += ["", f"  SECONDARY METRIC — {data['secondaryMetricKey']} (numeric, mean per order)", ""]
        numeric = welch_test(
            control["orderCount"], control["orderSum"], control["orderSumSquares"],
            treatment["orderCount"], treatment["orderSum"], treatment["orderSumSquares"],
        )
        out.append(format_numeric_report(numeric, control_key, treatment_key))

    excluded = data.get("excluded", {})
    total_excluded = sum(excluded.values())
    if total_excluded:
        out += [
            "",
            f"  EXCLUDED FROM THE EXPERIMENT — {total_excluded:,} visitors",
            "",
        ]
        labels = {
            "individual": "matched an individual target on the flag",
            "rule": "matched a targeting rule (evaluated before the default rule)",
            "notInExperiment": "reached the default rule, but it is not an experiment rollout",
            "off": "the flag is off",
            "error": "evaluation error — flag missing or SDK not ready",
        }
        for reason, count in excluded.items():
            if count:
                out.append(f"    {count:>8,}  {labels.get(reason, reason)}")
        out += [
            "",
            "  These visitors were served deliberately rather than randomly, so counting",
            "  them would bias the result. LaunchDarkly leaves them out for the same reason.",
        ]

    truth = data.get("groundTruth")
    if truth:
        out += [
            "",
            _RULE,
            "  GROUND TRUTH (simulator only — LaunchDarkly cannot know this)",
            _RULE,
            f"  True control conversion rate   : {truth['controlRate'] * 100:.2f}%",
            f"  True treatment conversion rate : {truth['treatmentRate'] * 100:.2f}%",
            f"  True relative lift             : {truth['relativeLift'] * 100:+.1f}%",
            "",
            "  Compare these with the measured figures above. The gap between them is",
            "  sampling error, and it is the reason an experiment needs a sample size",
            "  rather than a hunch. Re-run with a different --seed to see it move.",
        ]

    out.append(_RULE)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def latest_results_file() -> str | None:
    """The most recently written simulation summary, if there is one."""
    matches = sorted(glob.glob(os.path.join(config.RESULTS_DIR, "*.json")))
    return matches[-1] if matches else None


def print_sample_size(baseline: float, lift: float, alpha: float, power: float, daily_traffic: int) -> None:
    """Explain how much data the experiment needs, and how long that takes."""
    per_arm = required_sample_size(baseline, lift, alpha, power)
    total = per_arm * 2

    print(_RULE)
    print("  HOW MUCH DATA DOES THIS EXPERIMENT NEED?")
    print(_RULE)
    print(f"  Baseline conversion rate     : {baseline * 100:.2f}%")
    print(f"  Smallest lift worth detecting: {lift * 100:+.1f}% relative "
          f"({baseline * lift * 100:+.2f} pp, to {baseline * (1 + lift) * 100:.2f}%)")
    print(f"  Significance level (alpha)   : {alpha:.2f}")
    print(f"  Power (1 - beta)             : {power:.2f}")
    print()
    print(f"  Visitors needed PER ARM      : {per_arm:,}")
    print(f"  Visitors needed IN TOTAL     : {total:,}   (two arms, 50/50)")
    print()

    if daily_traffic > 0:
        days = total / daily_traffic
        weeks = math.ceil(days / 7)
        print(f"  At {daily_traffic:,} visitors/day reaching the experiment:")
        print(f"    {days:.1f} days to reach the sample size.")
        print(f"    Run it for {weeks} full week(s) — see 'How long is long enough?' in")
        print("    the README for why you round up to whole weeks rather than stopping")
        print("    the moment the counter is reached.")
    print(_RULE)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyse a simulated experiment run, or size one before you start.",
    )
    parser.add_argument(
        "results_file", nargs="?",
        help="A JSON summary written by simulate_traffic.py. Defaults to the most recent.",
    )
    parser.add_argument(
        "--sample-size", action="store_true",
        help="Do not analyse a run; compute how many visitors an experiment would need.",
    )
    parser.add_argument("--baseline", type=float, default=config.SIM_CONTROL_CONVERSION_RATE,
                        help="Baseline conversion rate for --sample-size (default: %(default)s)")
    parser.add_argument("--lift", type=float, default=config.SIM_LIFT,
                        help="Smallest relative lift worth detecting (default: %(default)s)")
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA,
                        help="Significance level (default: %(default)s)")
    parser.add_argument("--power", type=float, default=DEFAULT_POWER,
                        help="Statistical power (default: %(default)s)")
    parser.add_argument("--daily-traffic", type=int, default=40_000,
                        help="Visitors per day reaching the experiment (default: %(default)s)")
    args = parser.parse_args()

    if args.sample_size:
        print_sample_size(args.baseline, args.lift, args.alpha, args.power, args.daily_traffic)
        return 0

    path = args.results_file or latest_results_file()
    if not path:
        print(
            f"No results found in '{config.RESULTS_DIR}/'.\n\n"
            "  Run the simulator first:\n"
            "      python simulate_traffic.py\n\n"
            "  Or size an experiment without any data:\n"
            "      python analysis.py --sample-size",
            file=sys.stderr,
        )
        return 1

    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read '{path}': {exc}", file=sys.stderr)
        return 1

    print(f"Reading {path}\n")
    print(report(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
