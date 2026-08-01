# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Part 3 of a three-repo LaunchDarkly demo series, covering **experimentation**. It re-uses the
string flag `landing-page-hero` (`control` / `spotlight` / `conversion`) created in Part 2 rather
than defining its own, attaches a conversion metric and an experiment to it, and drives enough
simulated traffic through it to reach statistical significance.

Two entry points. `simulate_traffic.py` is the important one — it generates the data volume an
experiment needs. `app.py` is an interactive landing page that shows the same SDK calls serving one
human at a time.

`README.md` carries the LaunchDarkly-side setup (metric, experiment, starting the iteration) and
the statistical guidance. It is written for a prospective customer, not for a maintainer.

Python 3.10 is the floor (`X | None` syntax); developed against 3.13.5. Sibling repos:
`launchdarkly-part-1-release-and-remediate`, `launchdarkly-part-2-target`. Default branch is `main`;
`results/*.json` is git-ignored.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # then set LAUNCHDARKLY_SDK_KEY (must start with `sdk-`)

python simulate_traffic.py --check     # preflight: is an experiment actually running?
python simulate_traffic.py             # 8,000 visitors, ~30s
python analysis.py                     # re-analyse the most recent run
python analysis.py results/run-X.json  # ...or a specific one
python analysis.py --sample-size       # how many visitors are needed?
python app.py                          # http://127.0.0.1:5000/

python scripts/setup_launchdarkly.py            # create metrics + experiment
python scripts/setup_launchdarkly.py --start    # start the iteration
python scripts/setup_launchdarkly.py --status   # flag / metric / experiment state
python scripts/setup_launchdarkly.py --stop

OFFLINE_DEMO=1 python simulate_traffic.py       # no account, no network
PORT=5050 python app.py                         # any config.py setting overrides inline
curl localhost:5000/healthz
```

**There is no test suite, linter, or CI.** `OFFLINE_DEMO=1` is the verification path, and it is a
real end-to-end exercise of the SDK's evaluator rather than a mock. Use it to check any change to
the flag payload, contexts, or event plumbing.

Two checks worth running after touching anything in the evaluation or simulation path:

```bash
# 1. All five named visitors land in the expected experiment state.
OFFLINE_DEMO=1 python -c "
import ld_client, contexts
ld_client.initialize()
for vid, v in contexts.VISITORS.items():
    ev = ld_client.evaluate(contexts.context_from_attributes(v))
    ok = ev['inExperiment'] == v['expected_in_experiment']
    print(f\"{vid:8} {ev['variation']:10} inExp={ev['inExperiment']!s:5} {ev['reasonKind']:12} {'OK' if ok else 'MISMATCH'}\")
"

# 2. The estimator is unbiased — measured rates must converge on --control-rate
#    and control-rate*(1+lift). More than ~1 SE off at this size is a bug, not
#    noise. (60k visitors, ~3s with --rate 0.)
OFFLINE_DEMO=1 python simulate_traffic.py --visitors 60000 --rate 0 --no-check \
  | grep -E '^  (control|spotlight|True )'
```

`app.py` exposes `/api/state?visitor=<id>` returning the same JSON the inspector renders. That is
the way to verify UI behaviour here — there is no browser in this environment, and no headless one
installed:

```bash
OFFLINE_DEMO=1 PORT=5099 python app.py &
curl -s 'localhost:5099/api/state?visitor=jordan' | python -m json.tool | head -20
```

Running against a real account requires the flag to already exist in the same environment as the
SDK key, plus a **started** experiment iteration. `--check` diagnoses the latter; it is the failure
mode that is invisible from the application's point of view.

## Architecture

```
contexts.generate_population(n, seed)      deterministic synthetic visitors
        │
        ▼
ld_client.evaluate(context)  ──────────►  EXPOSURE (allocates the arm)
        │  reason.inExperiment decides whether this visitor counts
        ▼
simulate_traffic.conversion_probability()  the simulated human (ground truth)
        │
        ▼
ld_client.track_conversion(context, …)  ─►  METRIC EVENT (same context key)
        │
        ▼
analysis.two_proportion_test(...)          local audit of what was sent
```

- **`ld_client.py`** — the only module that imports `ldclient`. Init (with a `high_volume` mode for
  the simulator), `variation_detail` evaluation, reason→mechanism mapping, the two `track` helpers,
  and `_OfflineDataSource`.
- **`contexts.py`** — the five named visitors and `generate_population()`. The context `key` is the
  randomisation unit; `context_from_attributes()` is the single shared construction path.
- **`simulate_traffic.py`** — the driver. Preflight check, the behavioural model, pacing, periodic
  flushing, the exclusion tally, and the run summary written to `results/`.
- **`analysis.py`** — standard-library statistics: normal CDF/quantile, two-proportion z-test,
  Welch's t-test, sample-size calculation, and the report formatting. Also a CLI.
- **`app.py`** — Flask. Full page loads, no SSE: an experiment's allocation is fixed for a given
  key, so there is nothing to push.
- **`scripts/setup_launchdarkly.py`** — REST API automation for metrics and the experiment. Never
  creates or modifies the flag.

### Invariants worth preserving

Load-bearing; changing them breaks the demo subtly rather than loudly.

- **This repo never creates the flag.** It re-uses Part 2's, and the setup script errors with a
  pointer to Part 2 if it is missing. That re-use is an explicit requirement of the brief.
- **Evaluate before track, with the same context object.** That ordering is what makes an event
  attributable. Any refactor that caches a variation and tracks later must carry the context, not
  just the variation string.
- **`inExperiment` is the only correct test for "is this visitor a subject".** Being served the
  treatment's variation is not sufficient — a targeting rule can serve `spotlight` to someone who
  was never randomised. `app._describe_arm()` exists to keep that distinction visible.
- **The offline fallthrough must be `"kind": "experiment"`, not `"kind": "rollout"`.** Both bucket
  traffic identically; only the former sets `inExperiment` and marks events as exposures. The
  offline seed is `2` so Riley lands in the control and Sam in the treatment.
- **Per-visitor RNG, seeded from the context key.** `_visitor_rng()` deliberately replaces a single
  shared generator: with one stream, converters draw an extra number for their order value, which
  shifts every later visitor's position and biases the measured effect toward zero. This was a real
  bug, caught by comparing a 60,000-visitor run against ground truth. Check 2 above is the
  regression test.
- **The simulator flushes every N visitors and always calls `shutdown()`**, including after
  `KeyboardInterrupt`. The SDK drops buffered events silently once its buffer fills, which would
  bias results rather than raise.
- **The device multiplier is normalised against the device mix** so the population-wide conversion
  rate equals `--control-rate` exactly. Changing `_DEVICE_MIX` without changing
  `contexts._DEVICES` silently moves the baseline.
- **`components/hero.py` must never import the SDK.** The flag is evaluated at the edge and the
  variation passed down as a plain string.

### Behaviour that looks like a bug but is not

Two results are counterintuitive enough that a future reader may try to "fix" them. Both are
deliberate, documented in README.md, and should be left alone.

- **The secondary numeric metric is underpowered at the default run size, and will often report
  "not significant" with the wrong sign.** Only visitors who *converted* emit an order value, so at
  8,000 visitors it has ~700 observations against the primary metric's ~7,800. The −8 USD
  regression is genuinely there and appears reliably at `--visitors 40000`. This is a teaching
  point about sizing an experiment for its guardrail metrics, not a defect — do not shrink
  `_ORDER_VALUE_SIGMA` or widen the gap to make the demo look tidier. `analysis.format_numeric_report()`
  explains the shortfall in its output when the result is non-significant.
- **`--lift 0` occasionally produces a significant result.** That is an A/A test, and roughly 1 run
  in 20 will look significant by chance. It is what a 5% false-positive rate means.

## Conventions

Module docstrings carry the teaching load — they explain *why* a LaunchDarkly or statistical choice
was made, not just what the code does, and inline comments flag the places a user has to substitute
their own values. This repo is a sales artifact read by prospective customers as much as it is run;
match that density when editing, and keep the vocabulary identical to the LaunchDarkly console
("exposure", "arm", "treatment", "iteration", "randomisation unit").

Be honest in the copy about what is simulated and what is real. The distinction between "every SDK
call here is genuine" and "the human clicking is invented" is stated in several places on purpose —
it is what makes the demo credible rather than a mock. The same applies to what has been verified:
the offline path and the statistics are tested end to end, but `scripts/setup_launchdarkly.py` has
never been run against a live LaunchDarkly account, and README.md says so rather than implying
otherwise.
