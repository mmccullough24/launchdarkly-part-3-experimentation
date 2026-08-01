# Measuring the landing page revamp — a LaunchDarkly experiment for ABC Company

A small, complete Python application that runs a real LaunchDarkly
**experiment** on the landing page hero: one feature flag, one primary metric,
two arms, and enough traffic to actually decide the thing.

> **The situation.** ABC Company's landing page revamp is built and shipped
> behind a flag. The team can already choose *who* sees the new hero — that was
> the previous exercise. What nobody can answer yet is whether the new hero is
> any **better**. The page takes about **40,000 visitors a day**, so the
> difference between "it converts 8% better" and "it converts 3% worse" is a lot
> of money either way, and right now the loudest opinion in the room is winning.
>
> **What this app shows.** The same flag from the targeting demo, wired to a
> conversion metric and a LaunchDarkly experiment. Half the traffic gets the
> control hero, half gets the redesign, LaunchDarkly randomises and attributes,
> and after enough visitors there is a defensible answer instead of a preference.

Everything runs on your laptop against your own LaunchDarkly account. There is
no build step, no database, and no cloud infrastructure. If you do not have an
account yet, there is a fully working
[offline mode](#offline-demo-mode-no-launchdarkly-account-needed).

This is **Part 3** of a series. It re-uses the feature flag created in
[Part 2 — Target](https://github.com/mmccullough24/launchdarkly-part-2-target)
rather than creating a new one, exactly as the brief asks.

---

## Table of contents

1. [What this demonstrates](#what-this-demonstrates)
2. [Assumptions about your environment](#assumptions-about-your-environment)
3. [Setup, step by step](#setup-step-by-step)
   - [Step 1 — Get the code](#step-1--get-the-code)
   - [Step 2 — Create a virtual environment](#step-2--create-a-virtual-environment)
   - [Step 3 — Install the dependencies](#step-3--install-the-dependencies)
   - [Step 4 — The feature flag (re-used from Part 2)](#step-4--the-feature-flag-re-used-from-part-2)
   - [Step 5 — Create the metric](#step-5--create-the-metric)
   - [Step 6 — Create the experiment](#step-6--create-the-experiment)
   - [Step 7 — Start the experiment](#step-7--start-the-experiment)
   - [Step 8 — Copy your SDK key and configure the app](#step-8--copy-your-sdk-key-and-configure-the-app)
   - [Step 9 — Preflight check](#step-9--preflight-check)
   - [Step 10 — Generate the traffic](#step-10--generate-the-traffic)
   - [Step 11 — Read the results in LaunchDarkly](#step-11--read-the-results-in-launchdarkly)
   - [Automating steps 5 to 7](#automating-steps-5-to-7)
4. [The interactive landing page](#the-interactive-landing-page)
5. [How long is long enough?](#how-long-is-long-enough)
6. [Reading the results](#reading-the-results)
7. [How it works](#how-it-works)
8. [Offline demo mode (no LaunchDarkly account needed)](#offline-demo-mode-no-launchdarkly-account-needed)
9. [Troubleshooting](#troubleshooting)
10. [Notes for production use](#notes-for-production-use)

---

## What this demonstrates

| Requirement | How this app satisfies it |
| --- | --- |
| **Use the same feature flag from Part 2** | The string flag `landing-page-hero` (`control` / `spotlight` / `conversion`) is re-used unchanged. Nothing here creates a flag; `scripts/setup_launchdarkly.py` refuses to run if it is missing and points you at Part 2. |
| **Create a metric** | `landing-page-cta-click` — a **conversion** metric, "higher is better", fed by `client.track()` from both the web app and the simulator. A second **numeric** metric, `landing-page-order-value`, is included as an optional guardrail. |
| **Create an experiment** | `landing-page-hero-revamp` — `control` vs `spotlight`, 50/50, attached to the flag's **default rule**, randomised on the `user` context, decided on the conversion metric. |
| **Run it long enough to decide** | `simulate_traffic.py` drives thousands of distinct visitors through the flag and sends their metric events, so the experiment reaches statistical significance in minutes instead of days. `analysis.py --sample-size` tells you how much data you need *before* you start, and [How long is long enough?](#how-long-is-long-enough) covers the part sample size alone does not answer. |

### Why `conversion` is not in the experiment

The flag has three variations but the experiment compares two. The third,
`conversion`, is served only to named individuals for internal testing. Mixing a
hand-picked audience into a randomised test breaks the randomisation that makes
the result meaningful — so those visitors are excluded, along with anyone caught
by the Part 2 targeting rule. The app shows you this happening rather than
asserting it; roughly 2–3% of simulated traffic is excluded on every run, and
the report breaks down exactly why.

---

## Assumptions about your environment

This guide assumes all of the following. If any is not true, see
[Troubleshooting](#troubleshooting).

**Software**

- **Python 3.10 or newer** on your `PATH`. Check with `python3 --version`.
  (Developed and tested against Python 3.13.5 on Linux. 3.10 is the floor
  because the code uses the `X | None` type syntax.)
- **`pip` and the `venv` module** — both ship with python.org builds and most
  distro Python packages. On Debian/Ubuntu you may need
  `sudo apt install python3-venv`.
- **Git**, to clone the repository.
- A **modern browser**, only if you want the interactive landing page. The
  simulator and the analysis are pure terminal.
- Optional: **`curl`**, if you want to poke the JSON endpoints by hand.

**Network**

- Outbound HTTPS on port 443 to `stream.launchdarkly.com`,
  `events.launchdarkly.com`, and `app.launchdarkly.com`.
- Nothing needs to reach *into* your machine. The web app listens on `127.0.0.1`
  only, and the simulator listens on nothing at all.
- No outbound access? Use
  [offline demo mode](#offline-demo-mode-no-launchdarkly-account-needed).

**LaunchDarkly**

- A LaunchDarkly account **with Experimentation enabled.** This is the one
  assumption that can genuinely block you: Experimentation is a paid add-on and
  is *not* included in every plan or every free trial. If you do not see an
  **Experiments** item in the left-hand nav, you do not have it, and no amount
  of correct configuration will produce results. Offline mode still works.
- The flag `landing-page-hero` from Part 2, or five minutes to
  [create it](#step-4--the-feature-flag-re-used-from-part-2).
- Permission to create metrics and experiments, and to read an SDK key.
- A **non-production environment** — the "Test" environment that every new
  LaunchDarkly project ships with is ideal.

**Cost and data**

- The simulator sends real analytics events to your account. The default run is
  8,000 visitors, which produces roughly 8,000 evaluation events and about 1,600
  metric events. That is small, but it is not nothing: **experiment traffic can
  count toward your plan's event or MAU limits.** The 8,000 simulated context
  keys are distinct users as far as billing is concerned. If your account is
  metered tightly, start with `--visitors 2000`.

**What is deliberately not production-grade**

This is a demonstration, not a deployment template. It uses Flask's development
server, keeps session state in process memory, and has no authentication. See
[Notes for production use](#notes-for-production-use).

---

## Setup, step by step

### Step 1 — Get the code

```bash
git clone https://github.com/mmccullough24/launchdarkly-part-3-experimentation.git
cd launchdarkly-part-3-experimentation
```

### Step 2 — Create a virtual environment

A virtual environment keeps these dependencies away from your system Python.

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows (PowerShell)**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Your prompt should now be prefixed with `(.venv)`. Everything below assumes the
environment is active. To leave it later, run `deactivate`.

### Step 3 — Install the dependencies

```bash
pip install -r requirements.txt
```

Four packages: the LaunchDarkly server-side Python SDK, Flask, `python-dotenv`,
and `requests`. There is deliberately no numpy or scipy — the statistics are
plain standard-library `math`, so you can read them.

> Using [uv](https://github.com/astral-sh/uv) instead?
> `uv venv && uv pip install -r requirements.txt`.

**Check your Python setup before going further.** Offline mode needs no account
and no network:

```bash
OFFLINE_DEMO=1 python simulate_traffic.py --visitors 2000
```

You should see a progress bar and then a results table. That confirms the whole
pipeline works locally; the rest of the setup wires it to your account.

### Step 4 — The feature flag (re-used from Part 2)

**If you still have the flag from the Part 2 targeting demo, skip this step.**
That is the point of it — this project measures the flag you already have.

Otherwise, create it:

1. Sign in to [app.launchdarkly.com](https://app.launchdarkly.com).
2. Pick the project and environment you want to demo in. The default project's
   **Test** environment is a good choice.
3. **Flags → Create flag.**
4. Fill in:
   - **Name:** `Landing page hero`
   - **Key:** `landing-page-hero` — must match exactly. The app reads it from
     `LD_FLAG_KEY` in `.env`, which defaults to this value.
   - **Configuration / Flag type:** **Custom**, then **String**. *Not* boolean.
   - **Variations:** three, with these exact **values**:

     | # | Value | Name |
     | --- | --- | --- |
     | 1 | `control` | Control |
     | 2 | `spotlight` | Spotlight |
     | 3 | `conversion` | Conversion |

   - **Default variations:** serve **`control`** both when targeting is on and
     when it is off. The fail-safe direction is always the experience that
     already worked.
   - **Client-side SDK availability:** leave every box unchecked. This app
     evaluates server-side; the browser never sees the SDK key.
5. **Create flag**, and make sure the flag's top toggle is **on**.

> **Do I need the Part 2 targeting rules?** No. The experiment works with or
> without them. Keeping them makes the demo better, because you get to watch
> those visitors being correctly *excluded* from the results.

### Step 5 — Create the metric

A metric tells LaunchDarkly what to watch for and how to interpret it.

1. Go to **Metrics** in the left-hand nav, then **Create metric**.
2. Fill in:
   - **Name:** `Landing page CTA click-through`
   - **Event kind / What do you want to measure:** **Custom**
     (not Click or Page view — those are for the JavaScript SDK's autocapture;
     this app sends its own events from the server).
   - **Event key:** `landing-page-cta-click`

     > This is the single most important field on the page. It must match, byte
     > for byte, the string this app passes to `client.track()`. In this repo
     > that string comes from `LD_PRIMARY_METRIC_KEY` in `.env`. A typo here
     > produces an experiment that runs forever at zero conversions — which
     > looks exactly like a null result rather than a bug.

   - **Measure:** **Count of occurrences** / conversion — *not* numeric. You are
     asking "did this visitor click?", not "how much?".
   - **Success criteria / Analysis:** **Higher than baseline is better.**
   - **Randomisation / analysis unit:** `user`. It must match the context kind
     the app sends, which is `user`.
3. **Save.**

**Optional — the second, numeric metric.** The brief asks for one metric and the
experiment is decided on the one above. This second one exists to show the other
kind of metric and to make a point about guardrails:

- **Name:** `Order value per visitor`
- **Event kind:** Custom
- **Event key:** `landing-page-order-value`
- **Measure:** **Numeric**, unit `USD`, aggregated as **average**
- **Success criteria:** higher is better

To skip it entirely, set `LD_TRACK_SECONDARY_METRIC=0` in `.env`.

### Step 6 — Create the experiment

1. Go to **Experiments → Create experiment**.
2. **Name:** `Landing page hero — control vs spotlight`.
   **Key:** `landing-page-hero-revamp` (this is what `LD_EXPERIMENT_KEY`
   defaults to; only the setup script and the console links use it).
3. **Hypothesis** — write one. LaunchDarkly requires it, and it is the thing
   that stops the result being reinterpreted after the fact:

   > Leading with the value proposition and moving social proof above the fold
   > (the "spotlight" hero) will increase landing page click-through versus the
   > current control hero, without reducing average order value.

4. **Experiment type:** a standard **Feature change** experiment.
5. **Randomisation unit:** `user`.
6. **Metrics:** add `Landing page CTA click-through` and mark it the **primary**
   metric. Add `Order value per visitor` as a secondary if you created it.

   > Choosing the primary metric *now*, before any data exists, is the whole
   > game. With three metrics and no primary, something will always have moved,
   > and you will have no principled way to say whether the test won.

7. **Feature flag:** choose `landing-page-hero`.
8. **Rule to experiment on:** the flag's **Default rule**.

   > This matters. Attaching the experiment to the default rule means everyone
   > caught by an individual target or a targeting rule *above* it never reaches
   > the experiment. That is why the Part 2 targeting can stay exactly as it is:
   > Avery is still pinned by name, beta testers still get the redesign, and
   > none of them contaminate the results.

9. **Variations and traffic:**
   - Include `control` — mark it the **baseline**.
   - Include `spotlight`.
   - Leave `conversion` **out** of the experiment.
   - Split **50/50**. Even splits detect a difference with the fewest total
     visitors, so unless you have a reason to be cautious with the treatment,
     50/50 is the efficient choice.
   - **Audience / traffic allocation:** 100% of eligible traffic. Allocating
     less is a way to limit exposure, but it directly multiplies how long the
     experiment must run.
10. **Save.** The experiment now exists as a **draft**. Nothing is being
    measured yet.

### Step 7 — Start the experiment

On the experiment page, click **Start**.

**This step is not optional and it is the one people skip.** Creating an
experiment does nothing on its own. Starting the iteration is what rewrites the
flag's default rule into an *experiment rollout*, and only an experiment rollout
produces the `inExperiment` allocations that LaunchDarkly counts.

Confirm it worked: go back to the flag's **Targeting** tab. The default rule
should now show as an experiment rollout rather than "serve `control`".

### Step 8 — Copy your SDK key and configure the app

1. **Project settings → Environments.**
2. Find the environment you built the experiment in.
3. Open its **⋯** menu → **SDK key → Copy**.

The value starts with `sdk-`. If it starts with `mob-`, or is labelled
"client-side ID", it is the wrong one.

> **It must be the same environment as the experiment.** The SDK key is what
> decides which environment your app talks to. The environment selector in the
> LaunchDarkly UI is independent of it, and mixing them up is the single most
> common cause of an experiment that stays empty.

Then:

```bash
cp .env.example .env
```

Open `.env` and paste your key:

```dotenv
LAUNCHDARKLY_SDK_KEY=sdk-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

That is the only value you must change. Everything else is optional and
documented inline in that file.

> **Treat the SDK key as a secret.** It grants read access to every flag in that
> environment. `.env` is git-ignored so it cannot be committed by accident.

### Step 9 — Preflight check

Before sending thousands of events, confirm the experiment is actually running
**from the application's point of view** — which is the only view that matters:

```bash
python simulate_traffic.py --check
```

Expected output:

```
  Flag              : landing-page-hero
  Sampled           : 20 visitors
  Allocated by an experiment : 20/20
  Variations seen   : control x13, spotlight x7
  Example reason    : FALLTHROUGH (inExperiment=True)

  Experiment is running. Ready to simulate traffic.
```

If it reports `Allocated by an experiment : 0/20`, the check prints a numbered
list of the four things that cause it. Work down that list before continuing —
the flag will evaluate perfectly and collect nothing, and you would not
otherwise notice for hours.

### Step 10 — Generate the traffic

```bash
python simulate_traffic.py
```

This evaluates the flag for 8,000 distinct simulated visitors, decides for each
whether they would have clicked, and sends the metric events. It takes about
half a minute at the default rate.

```
  [############################] 8,000/8,000  250/s   control 3,871/7.9%   spotlight 3,913/10.3%
```

Then it prints its own analysis, the exclusion breakdown, and the ground truth
it was working from. Useful variations:

```bash
python simulate_traffic.py --visitors 20000     # more data, tighter interval
python simulate_traffic.py --lift 0             # an A/A test: no real effect
python simulate_traffic.py --seed 7             # a different draw, same truth
python simulate_traffic.py --rate 50            # slower, more like real traffic
python analysis.py                              # re-analyse the last run
python analysis.py --sample-size                # how much data do I need?
```

**`--lift 0` is worth running once.** It simulates a redesign that makes no
difference whatsoever, and the experiment correctly fails to find one. An
experiment that can only ever say "ship it" is not measuring anything.

### Step 11 — Read the results in LaunchDarkly

Open **Experiments → Landing page hero — control vs spotlight**.

Events are ingested in batches, so give it a few minutes. You should see the two
arms, their exposure counts, their conversion rates, and LaunchDarkly's
**probability to beat control**.

Cross-check the exposure counts against what the simulator reported. They should
be very close. If LaunchDarkly shows far fewer, events are being dropped — see
[Troubleshooting](#troubleshooting).

See [Reading the results](#reading-the-results) for what the numbers mean and
why LaunchDarkly's figures will not match `analysis.py` digit for digit.

### Automating steps 5 to 7

If you would rather not click through the UI, the repository includes a script
that creates the metrics and the experiment through the LaunchDarkly REST API:

```bash
python scripts/setup_launchdarkly.py            # create metrics + experiment
python scripts/setup_launchdarkly.py --start    # start collecting data
python scripts/setup_launchdarkly.py --status   # what exists right now
python scripts/setup_launchdarkly.py --stop     # stop the current iteration
```

It needs an API token, which is a **different credential from the SDK key** —
the SDK key reads flags, an API token writes them. Create one under
**Account settings → Authorization → Create token** with the built-in **Writer**
role, then add it to `.env`:

```dotenv
LD_API_TOKEN=api-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
LD_PROJECT_KEY=default
LD_ENVIRONMENT_KEY=test
```

`LD_PROJECT_KEY` and `LD_ENVIRONMENT_KEY` are the short URL keys, not the
display names — find them under **Project settings → Environments**.

`--status` is useful on its own even if you built everything by hand: it prints
the flag's default rule, whether it is an experiment rollout, which metrics
exist, and whether an iteration is running.

> **A note on this script.** It is written against the documented LaunchDarkly
> REST API and is provided as a convenience. The step-by-step UI instructions
> above are the primary, guaranteed path — if the API rejects something on your
> account (plan differences and API versioning both vary), use the UI and carry
> on. The script never touches the flag's variations and never deletes
> anything.

---

## The interactive landing page

The simulator is what produces enough data to decide the experiment. The web app
is the other half: it shows that those bulk events are the same events a real
visitor produces by clicking a button.

```bash
python app.py
```

Open <http://127.0.0.1:5000/>.

You get the ABC Company landing page with an **experiment inspector** docked to
the right. For the current visitor it shows, in large type, whether they are in
the experiment — and if not, why not.

Five named visitors ship with the demo, carried over from Part 2. Between them
they cover every path:

| View as | Gets | In the experiment? |
| --- | --- | --- |
| **Riley Torres** | randomised | **Yes** — reached the default rule |
| **Sam Okafor** | randomised | **Yes** — enterprise, but not a beta tester, so no rule catches them |
| **Jordan Blake** | `spotlight` | **No** — matched the Part 2 targeting rule |
| **Priya Raman** | `spotlight` | **No** — same rule |
| **Avery Chen** | `conversion` | **No** — pinned by name as an individual target |

Jordan is the instructive one. Jordan is *served the same hero as the treatment
arm* but is **not in the treatment arm**, because Jordan was chosen deliberately
rather than randomly. The inspector says `no arm — not randomised` for exactly
that reason. Confusing "was served the new thing" with "is in the experiment" is
the most common way a set of experiment results turns out to be wrong.

**+ New visitor** generates a fresh context key each time. Click it repeatedly
and watch the same flag hand out different heroes — that is the randomisation,
live. Reload the page and the hero does not change, because the key did not.

Clicking the hero's primary button (or **Send a conversion event**) fires the
real metric events for that exact context.

---

## How long is long enough?

The brief says to run the experiment long enough to make an informed decision.
That is two separate questions, and sample size only answers the first.

### 1. Enough data — the statistical question

```bash
python analysis.py --sample-size
```

```
  Baseline conversion rate     : 8.00%
  Smallest lift worth detecting: +30.0% relative (+2.40 pp, to 10.40%)
  Significance level (alpha)   : 0.05
  Power (1 - beta)             : 0.80

  Visitors needed PER ARM      : 2,273
  Visitors needed IN TOTAL     : 4,546   (two arms, 50/50)
```

Three inputs drive that number, and only one of them is a statistical choice:

- **Baseline conversion rate.** Look it up; do not guess. A lower baseline needs
  more traffic.
- **The smallest lift worth detecting.** This is a *business* decision, not a
  statistical one. It is not "how big do we hope the effect is" — it is "how
  small an improvement would still be worth shipping and maintaining?" Halving
  it roughly quadruples the traffic you need.
- **Significance and power**, conventionally 0.05 and 0.80.

Decide this **before** you start. Sample size computed after you have seen the
data is not a sample size, it is a justification.

### 2. Enough time — the validity question

Reaching the visitor count is necessary, not sufficient. ABC Company's 40,000
visitors a day hit that 4,546 in under three hours — and stopping there would be
a mistake, because those three hours are not a representative sample of anything.

- **Run at least one full week, and always whole weeks.** Tuesday-morning
  traffic converts differently from Saturday-night traffic. An experiment that
  starts on a Wednesday and ends on the following Monday has weighted Mondays
  twice. Two weeks is a better default: it covers a fortnightly pay cycle and
  survives one bad day.
- **Cover a full business cycle**, including any weekly email send, ad flight,
  or release that changes the traffic mix.
- **Account for delayed conversions.** If a visitor typically converts three
  days after first landing, an experiment stopped on day seven has censored
  data: the treatment's late converters have not arrived yet.
- **Do not stop the moment it looks significant.** This is the big one. Checking
  a fixed-horizon test repeatedly and stopping at the first significant reading
  inflates the false-positive rate dramatically — with daily peeking over two
  weeks, a nominal 5% error rate can exceed 25%. Either fix the end date in
  advance and honour it, or enable LaunchDarkly's **sequential testing**, which
  is designed to be monitored continuously and adjusts its thresholds
  accordingly. Pick one; do not peek at a fixed-horizon test.

### What the simulator does and does not compress

`simulate_traffic.py` compresses the *volume* — thousands of visitors in
minutes. It cannot compress the *calendar*: every event it sends is timestamped
now, so a simulated run contains no day-of-week variation, no delayed
conversions, and no novelty effect. It gets you a statistically valid result on
a realistic data volume, which is what makes it a useful rehearsal. It is not a
substitute for letting a real experiment run for two weeks.

---

## Reading the results

### LaunchDarkly's numbers and this app's will differ. That is expected.

LaunchDarkly's Experimentation is **Bayesian** by default. It reports things
like *"Spotlight has a 96% probability to beat control"* and a credible interval
for the effect.

`analysis.py` is **frequentist**. It reports a p-value and a confidence interval.

They answer different questions:

| | Question it answers |
| --- | --- |
| **Bayesian** (LaunchDarkly) | Given the data, how likely is it that the treatment is genuinely better? |
| **Frequentist** (`analysis.py`) | If the two heroes were truly identical, how surprising would this data be? |

On a clean experiment with a few thousand subjects per arm they agree about the
direction and about whether there is real signal. They will not agree to the
decimal, and neither is "the right one" — LaunchDarkly's is the one to quote,
because it is computed from the events it actually ingested. `analysis.py` is
there so you can audit that ingestion and see the arithmetic.

### What a result actually licenses you to do

- **Significant, positive, and larger than your minimum worthwhile effect** →
  ship it. Roll the winner out by editing the flag's default rule to serve it to
  everyone. An experiment decides; a rollout ships.
- **Significant but smaller than your minimum worthwhile effect** → it is real
  and it is not worth the maintenance. This is a legitimate outcome.
- **Not significant** → you have *not* proven the heroes are equivalent. You
  have shown this much data cannot tell them apart. Look at the confidence
  interval: `[-0.2, +2.4] pp` still admits a meaningful improvement, and means
  "collect more data". `[-0.2, +0.3] pp` has genuinely ruled out anything worth
  chasing.
- **Significant, but the guardrail metric moved the wrong way** → this is the
  interesting conversation. The simulation is deliberately rigged this way: the
  treatment genuinely wins on click-through (+30%) and genuinely loses on
  average order value (−7%). More clicks at a lower value each is not
  automatically a win, and which metric the business is optimising should have
  been settled in Step 6.

  **You will not see that at the default run size**, and the reason is worth
  more than the result itself. Only visitors who *converted* contribute an order
  value, so at 8,000 visitors the numeric metric has ~700 observations against
  the primary metric's ~7,800. It is badly underpowered and reports "not
  significant" on a regression that is really there. Run:

  ```bash
  python simulate_traffic.py --visitors 40000
  ```

  and the −8 USD difference appears with a p-value in the thousandths.

  This is a real trap, not a quirk of the simulator. **An experiment sized on
  its primary metric will routinely be too small for its guardrail metrics** —
  precisely the metrics you added to catch a regression before shipping. If a
  guardrail matters enough to block a launch, size for it too.

### The exclusion report

Every run ends with a breakdown of who did not make it into the experiment:

```
  EXCLUDED FROM THE EXPERIMENT — 216 visitors

       216  matched a targeting rule (evaluated before the default rule)
```

This is healthy. It is the Part 2 targeting doing its job, and LaunchDarkly
leaves those visitors out of the analysis for the same reason this report does:
they were served deliberately, not randomly. What you do *not* want to see is a
large `reached the default rule, but it is not an experiment rollout` count —
that means the experiment is not running.

---

## How it works

### The flow, end to end

```
   ┌────────────────────────────────────────────────────────────┐
   │  LaunchDarkly                                              │
   │    flag: landing-page-hero                                 │
   │      individual targets ─┐                                 │
   │      targeting rules ────┼─ evaluated FIRST → excluded     │
   │      default rule ───────┴─ EXPERIMENT ROLLOUT (50/50)     │
   └───────────────┬─────────────────────────▲──────────────────┘
                   │ streaming flag config   │ analytics events
                   ▼                         │
   ┌────────────────────────────────────────────────────────────┐
   │  Your Python process                                       │
   │                                                            │
   │    ld_client.evaluate(context)                             │
   │      └─ variation + reason.inExperiment   ── EXPOSURE ─────┤
   │                                                            │
   │    ld_client.track_conversion(context, …)  ── METRIC ──────┘
   │                                                            │
   │    (same context key for both — this is the join)          │
   └────────────────────────────────────────────────────────────┘
```

There is no experimentation API. The two calls above are ordinary flag
evaluation and ordinary custom events; what makes them an experiment is the
configuration on LaunchDarkly's side. That is the single most important thing to
take from this repository: **instrumenting for experimentation is the same work
as instrumenting for a rollout.** If your app already evaluates flags and tracks
events, you can experiment without touching application code.

### The three rules that decide whether an event counts

1. **Evaluate before you track.** An event for a context that was never exposed
   to the flag has no arm to be attributed to.
2. **Use the same context key for both.** LaunchDarkly joins on the key.
   Evaluate as `user-1234` and track as `session-abcd` and the event is orphaned.
3. **Match the event key exactly.** The string in `client.track()` must equal
   the metric's event key, including case.

All three are enforced by construction in `ld_client.py`, and all three are easy
to break in a real codebase where evaluation and conversion happen in different
services.

### Why `inExperiment` matters

`evaluate()` uses `variation_detail()` rather than plain `variation()`, so it
gets the evaluation **reason** back. Both calls send the identical exposure
event; the reason is purely for this demo's benefit, and it is what lets the app
distinguish three situations that look the same from the outside:

| Reason | Served | In the experiment? |
| --- | --- | --- |
| `FALLTHROUGH` + `inExperiment: true` | randomly | **Yes** — counted |
| `FALLTHROUGH` + `inExperiment: false` | the default rule's variation | No — no experiment is running |
| `RULE_MATCH` / `TARGET_MATCH` | deliberately | No — correctly excluded |

The middle row is the dangerous one: the app works perfectly and measures
nothing. `simulate_traffic.py --check` exists to catch exactly that.

In ordinary production code you would call `variation()` and ignore the reason.

### File map

| File | What it does |
| --- | --- |
| `simulate_traffic.py` | **Start here.** Drives the experiment: population → evaluate → convert → track → report. |
| `ld_client.py` | Every LaunchDarkly SDK call, plus the offline data source. The only module that imports `ldclient`. |
| `analysis.py` | The statistics, from scratch. Also `--sample-size`. |
| `contexts.py` | The five named visitors and the synthetic population. The randomisation unit is defined here. |
| `app.py` | The interactive landing page and the experiment inspector. |
| `components/hero.py` | The flagged component — all three heroes. Knows nothing about LaunchDarkly. |
| `scripts/setup_launchdarkly.py` | Optional: creates the metrics and the experiment through the REST API. |
| `config.py` | Reads `.env`. Every setting is documented in place. |

---

## Offline demo mode (no LaunchDarkly account needed)

To see the whole demo with no account, no key, and no network:

```bash
OFFLINE_DEMO=1 python simulate_traffic.py
OFFLINE_DEMO=1 python app.py
```

The flag — including a running 50/50 experiment rollout, the Part 2 individual
target, and the Part 2 targeting rule — is served from an in-process data
source, and the page grows two buttons that start and stop the experiment.

**This is a faithful demo, not a mock.** The in-process source publishes the real
LaunchDarkly flag payload through the same change-broadcasting layer the
streaming connection uses, and the SDK's own evaluator does the bucketing. Every
`inExperiment: true`, every 50/50 split, and every exclusion you see offline is a
genuine verdict from the SDK — only the transport differs.

**What it cannot do is analyse anything.** There is no LaunchDarkly to receive
the events, so `send_events` is off and the Experiments tab stays empty. That is
why `analysis.py` computes the statistics locally. Use offline mode to rehearse
the demo and to verify your Python setup — not as a substitute for a real run.

---

## Troubleshooting

**`simulate_traffic.py --check` says no experiment is running.**
Work down the list it prints. In order of likelihood: the experiment iteration
was never **started** (creating it is not enough — Step 7); the experiment is
attached to a different rule than the default rule; your SDK key is from a
different environment than the experiment; or the flag's top toggle is off.

**The experiment is running but LaunchDarkly shows no results.**
Give it five minutes — events are ingested in batches. Then check, in order:

1. Does the metric's **event key** exactly match `LD_PRIMARY_METRIC_KEY`?
   `python scripts/setup_launchdarkly.py --status` prints both.
2. Is the metric actually attached to the experiment as a metric to analyse?
3. Did the process exit cleanly? A script killed with `kill -9` loses whatever
   was still in the SDK's event buffer.

**LaunchDarkly's exposure count is much lower than the simulator reported.**
Events are being dropped. The SDK buffers them in memory and silently discards
the excess once the buffer fills. The simulator already raises the buffer and
flushes every 500 visitors; if you have raised `--rate` a long way, lower it or
lower `--flush-every`.

**`*** LaunchDarkly did not initialize.`**
The SDK could not fetch flags within its startup window. Usually: the key in
`.env` is a client-side ID or mobile key rather than an `sdk-` key; it was copied
with a trailing space; or outbound HTTPS to `stream.launchdarkly.com` is blocked.
Test the last one with `curl -sSf -o /dev/null https://app.launchdarkly.com`.

**Every visitor is excluded with `RULE_MATCH`.**
A Part 2 targeting rule is matching everyone. Check the rule's clauses — a rule
with no conditions matches all traffic. The simulated population is built so
only ~2–3% match the intended rule.

**There is no Experiments item in the LaunchDarkly nav.**
Experimentation is a paid add-on and is not on every plan or trial. Nothing in
the app can work around that. Offline mode still demonstrates the full mechanism.

**The results are significant in one direction, then the other, on re-runs.**
Check `--lift`. If you are running with `--lift 0` you are running an A/A test,
and roughly 1 run in 20 will look significant by chance. That is not a bug —
it is what a 5% false-positive rate means, and it is the best argument there is
for fixing your sample size in advance.

**`ModuleNotFoundError: No module named 'ldclient'`.**
The virtual environment is not active. Re-run the activate command from Step 2 —
your prompt should show `(.venv)`.

**`Address already in use` on startup.**
Something else owns port 5000 — on macOS, usually AirPlay Receiver. Turn it off
in **System Settings → General → AirDrop & Handoff**, or use another port:
`PORT=5050 python app.py`.

**`scripts/setup_launchdarkly.py` returns HTTP 401 or 403.**
The API token is missing, mistyped, or lacks write access. It must be an API
token (`api-…`), not the SDK key. HTTP 404 usually means `LD_PROJECT_KEY` or
`LD_ENVIRONMENT_KEY` is a display name rather than the short key.

---

## Notes for production use

What this demo does that a real deployment should do differently:

- **Never simulate traffic into a production experiment.** It is the fastest way
  to make a real decision from fake data. The simulator exists because a demo
  cannot wait two weeks; it has no place pointed at an environment anyone
  reports from.
- **Context keys.** Use a stable identifier. Both the arm assignment and the
  event attribution depend on the same person presenting the same key on every
  visit. For anonymous landing page traffic, generate a key once, persist it in
  a cookie, and set `anonymous: true` so those contexts do not count toward MAU.
- **Web server.** Flask's development server is single-process and not hardened.
  Use gunicorn or uvicorn behind a real proxy. Each worker gets its own SDK
  client, which is fine and expected.
- **One client, forever.** The SDK client is created once at startup and reused.
  Creating one per request would open a streaming connection per request.
- **Flush before exit.** Long-running servers can rely on the SDK's periodic
  flush. Short-lived jobs, serverless functions, and CLI tools must call
  `close()` or lose their last batch of events.
- **Don't request detail you do not use.** `variation_detail()` is used here so
  the inspector can explain itself. Production code should call `variation()`.
- **Decide the primary metric and the stopping rule before you start.** Every
  statistical guarantee in this README assumes you did.
- **Secrets.** The SDK key belongs in your secret manager, not a `.env` file on
  disk. The API token is more powerful still — it can write flags — and should
  never be deployed with the app.
- **Flag lifecycle.** Once the experiment decides, roll the winner out through
  the flag's default rule, then delete the losing branch and archive the flag.
  LaunchDarkly's code references and flag status views exist to stop temporary
  flags becoming permanent debt.

---

## Related

| Part | Repository | Covers |
| --- | --- | --- |
| 1 | [launchdarkly-part-1-release-and-remediate](https://github.com/mmccullough24/launchdarkly-part-1-release-and-remediate) | Releasing behind a flag, and instant rollback |
| 2 | [launchdarkly-part-2-target](https://github.com/mmccullough24/launchdarkly-part-2-target) | Individual and rule-based targeting — **creates the flag this project measures** |
| 3 | this repository | Metrics, experiments, and deciding with data |

## License

MIT — see [LICENSE](LICENSE).
