"""Environment configuration for the ABC Company landing page experiment.

Every value is read from an environment variable, loaded from a local `.env`
file (see `.env.example`). Nothing in this repository should ever contain a real
credential — `.env` is git-ignored for exactly that reason.

Any setting here can also be overridden inline for a single run:

    LD_FLAG_KEY=my-other-flag python simulate_traffic.py
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: str = "false") -> bool:
    """Read a boolean-ish environment variable."""
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# LaunchDarkly connection
# ---------------------------------------------------------------------------

# ┌─────────────────────────────────────────────────────────────────────────┐
# │ REPLACE ME — put your own server-side SDK key in `.env`:                │
# │                                                                         │
# │     LAUNCHDARKLY_SDK_KEY=sdk-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx       │
# │                                                                         │
# │ Find it in LaunchDarkly under:                                          │
# │     Project settings -> Environments -> the "..." menu on your          │
# │     environment -> SDK key -> Copy                                      │
# │                                                                         │
# │ It MUST start with `sdk-`. A mobile key (`mob-`) or a client-side ID    │
# │ will not work: this demo uses the server-side Python SDK, which         │
# │ evaluates flags on the server and never exposes the key to the browser. │
# └─────────────────────────────────────────────────────────────────────────┘
SDK_KEY = os.environ.get("LAUNCHDARKLY_SDK_KEY", "").strip()


# ---------------------------------------------------------------------------
# The feature flag
# ---------------------------------------------------------------------------

# ┌─────────────────────────────────────────────────────────────────────────┐
# │ RE-USE ME — this is the SAME flag created in the "Part 2 Target" demo.  │
# │                                                                         │
# │ If you still have it, you do not need to create anything: this project  │
# │ measures the flag you already built. If you are starting fresh, create  │
# │ a *string* flag with this exact key and three variations:               │
# │                                                                         │
# │     control     — the hero currently in production                      │
# │     spotlight   — the redesign being measured                           │
# │     conversion  — a third hero, individually targeted only              │
# │                                                                         │
# │ Full click-by-click steps are in README.md -> "Step 4".                 │
# │ To use a different key, set LD_FLAG_KEY in `.env` rather than editing   │
# │ this file.                                                              │
# └─────────────────────────────────────────────────────────────────────────┘
FLAG_KEY = os.environ.get("LD_FLAG_KEY", "landing-page-hero").strip()

# The three variations the flag defines, in the order it defines them.
# Index 0 is the control, which is also the off variation and the fallback.
VARIATIONS = ["control", "spotlight", "conversion"]

# The two variations the EXPERIMENT compares. `conversion` is deliberately left
# out: it is served only to named individuals, and mixing a hand-picked audience
# into a randomised test would break the randomisation. An experiment does not
# have to include every variation of a flag.
EXPERIMENT_CONTROL = "control"
EXPERIMENT_TREATMENT = "spotlight"

# Served when LaunchDarkly cannot be reached at all: a bad SDK key, no network,
# or a LaunchDarkly outage. Deliberately the control — the fail-safe direction
# is always the experience that is already in production.
FLAG_FALLBACK_VARIATION = "control"


# ---------------------------------------------------------------------------
# The metrics
# ---------------------------------------------------------------------------
# Two different things share these strings, and the distinction matters:
#
#   * the EVENT KEY is what `client.track()` sends from this code;
#   * the METRIC KEY identifies the metric object you create in LaunchDarkly,
#     which says "watch for this event key and analyse it this way".
#
# They are kept identical here to remove one thing that can be mistyped. In a
# real codebase they are often different, and LaunchDarkly does not require any
# relationship between them.

# PRIMARY metric — a conversion (binary) metric. Did this visitor click the
# landing page's main call to action, yes or no? This is the metric the
# experiment is decided on.
PRIMARY_METRIC_KEY = os.environ.get("LD_PRIMARY_METRIC_KEY", "landing-page-cta-click").strip()

# SECONDARY metric — a numeric metric, average order value in USD. Optional.
# It is here to show the second kind of metric LaunchDarkly supports, and to
# make a point about guardrails: a hero that lifts click-through but drops
# order value has not necessarily won.
SECONDARY_METRIC_KEY = os.environ.get("LD_SECONDARY_METRIC_KEY", "landing-page-order-value").strip()

# Set to 0 in `.env` to skip the numeric metric entirely — the demo works fine
# with only the primary metric, and the assignment only asks for one.
TRACK_SECONDARY_METRIC = _flag("LD_TRACK_SECONDARY_METRIC", "true")


# ---------------------------------------------------------------------------
# The experiment
# ---------------------------------------------------------------------------

# The key of the experiment object in LaunchDarkly. Only used by
# scripts/setup_launchdarkly.py — the SDK never needs to know it, because the
# SDK just evaluates the flag and LaunchDarkly attributes the result.
EXPERIMENT_KEY = os.environ.get("LD_EXPERIMENT_KEY", "landing-page-hero-revamp").strip()

# The randomisation unit: what "one subject" means in this experiment. `user`
# matches the context kind the app sends, which is what makes each visitor land
# in one arm and stay there.
RANDOMIZATION_UNIT = os.environ.get("LD_RANDOMIZATION_UNIT", "user").strip()


# ---------------------------------------------------------------------------
# Optional: LaunchDarkly REST API access
# ---------------------------------------------------------------------------
# Only needed for `scripts/setup_launchdarkly.py`, which can create the metrics
# and the experiment for you instead of clicking through the UI. The app and the
# simulator run fine without any of this.
#
# Create a token under: Account settings -> Authorization -> Create token,
# with the built-in "Writer" role.
LD_API_TOKEN = os.environ.get("LD_API_TOKEN", "").strip()

# The project and environment *keys* — the short URL-safe ones, not the display
# names. Find them under Project settings -> Environments.
LD_PROJECT_KEY = os.environ.get("LD_PROJECT_KEY", "default").strip()
LD_ENVIRONMENT_KEY = os.environ.get("LD_ENVIRONMENT_KEY", "test").strip()

# Only change this if you are on a LaunchDarkly federal or dedicated instance.
LD_API_BASE_URL = os.environ.get("LD_API_BASE_URL", "https://app.launchdarkly.com").strip()

# Left empty for the current GA API. If LaunchDarkly ever moves the experiment
# endpoints behind a version header again, set LD_API_VERSION=beta in `.env`
# rather than editing the script.
LD_API_VERSION = os.environ.get("LD_API_VERSION", "").strip()


# ---------------------------------------------------------------------------
# Traffic simulation defaults
# ---------------------------------------------------------------------------
# All of these are also command-line flags on simulate_traffic.py; the flag
# wins over the environment variable. See README.md -> "Step 8".

# How many visitors to simulate in one run. 8,000 is comfortably more than the
# ~4,800 needed to detect the default effect size — see README.md ->
# "How long is long enough?".
SIM_VISITORS = int(os.environ.get("SIM_VISITORS", "8000"))

# Visitors per second. This throttles the SDK's event pipeline so a run looks
# like traffic rather than a burst, and keeps well inside LaunchDarkly's
# ingestion limits. ABC Company's real landing page sees ~40,000 visitors a day,
# which averages out to well under one per second.
SIM_RATE_PER_SECOND = float(os.environ.get("SIM_RATE_PER_SECOND", "250"))

# The simulated *true* behaviour of each hero. These numbers exist only inside
# the simulator: they are the ground truth the experiment is trying to recover,
# and nothing in LaunchDarkly knows about them. Set SIM_LIFT to 0 to simulate a
# hero that makes no difference at all, and watch the experiment correctly
# decline to call a winner.
SIM_CONTROL_CONVERSION_RATE = float(os.environ.get("SIM_CONTROL_CONVERSION_RATE", "0.080"))
SIM_LIFT = float(os.environ.get("SIM_LIFT", "0.30"))

# Mean order value in USD for a visitor who converts, per variation. The
# treatment is given a slightly *lower* mean on purpose: it draws more clicks
# from lower-intent visitors. This is the guardrail story — see README.
SIM_CONTROL_ORDER_VALUE = float(os.environ.get("SIM_CONTROL_ORDER_VALUE", "120.0"))
SIM_TREATMENT_ORDER_VALUE = float(os.environ.get("SIM_TREATMENT_ORDER_VALUE", "112.0"))

# Fixed seed so a run is reproducible. Change it to draw a different sample from
# the same underlying truth — a good way to see sampling noise for yourself.
SIM_SEED = int(os.environ.get("SIM_SEED", "20260801"))

# Where simulation summaries are written.
RESULTS_DIR = os.environ.get("RESULTS_DIR", "results").strip()


# ---------------------------------------------------------------------------
# Local web server settings (app.py only)
# ---------------------------------------------------------------------------

HOST = os.environ.get("HOST", "127.0.0.1").strip()
PORT = int(os.environ.get("PORT", "5000"))


# ---------------------------------------------------------------------------
# Offline demo mode
# ---------------------------------------------------------------------------
# When enabled, the flag — including a running 50/50 experiment rollout — is
# served from an in-process data source, so you can see the whole demo with no
# account, no key, and no network. Events are not sent anywhere, but every
# allocation you see is a genuine verdict from the SDK's own targeting engine.
# See README.md -> "Offline demo mode".
OFFLINE_DEMO = _flag("OFFLINE_DEMO")
