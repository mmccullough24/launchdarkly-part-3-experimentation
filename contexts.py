"""The visitors ABC Company's landing page is evaluated for.

A LaunchDarkly **context** describes who a flag is being evaluated for. For an
experiment the context does one extra job beyond targeting: its `key` is the
randomisation unit. LaunchDarkly hashes that key to decide which arm the visitor
lands in, and it uses the same key to join metric events back to that arm.

Two consequences follow, and both are easy to get wrong in real code:

* **The key must be stable across visits.** A key that changes — a fresh UUID
  per request, a session id — re-randomises the same person on every visit,
  which quietly destroys the experiment. Half their pageviews land in each arm
  and the measured difference collapses toward zero.
* **The key must be the same at exposure and at conversion.** Evaluate the flag
  as `user-1234` and then track the purchase as `session-abcd` and the event has
  no arm to be attributed to.

This module provides two populations:

* `VISITORS` — five fixed, named visitors, carried over unchanged from the
  "Part 2 Target" demo. `app.py` uses them so you can click through the
  interesting cases by hand, including the two that the experiment must exclude.
* `generate_population()` — a large synthetic population for the traffic
  simulator, because an experiment needs thousands of subjects before it can say
  anything.
"""

import random
from typing import Iterator

from ldclient import Context

# The context kind. "user" is LaunchDarkly's default kind, and it must match the
# experiment's randomisation unit (`config.RANDOMIZATION_UNIT`). If the
# experiment randomises on `user` and the app sends an `organization` context,
# LaunchDarkly has nothing to bucket and the experiment collects no data.
CONTEXT_KIND = "user"


# ---------------------------------------------------------------------------
# The five named visitors (for the interactive app)
# ---------------------------------------------------------------------------
# `expected_in_experiment` is demo metadata, not something the SDK reads. The UI
# shows it beside the actual result so you can confirm your experiment is
# configured the way the README describes.

VISITORS = {
    "riley": {
        "key": "user-riley-torres",
        "name": "Riley Torres",
        "email": "riley.torres@harborlight.example",
        "title": "Owner, Harborlight Supply",
        "role": "customer",
        "plan": "free",
        "betaTester": False,
        "region": "AMER",
        "accountAgeDays": 23,
        "deviceType": "mobile",
        "blurb": "A typical free-plan visitor. Reaches the default rule, so the "
                 "experiment randomises them into an arm — this is what almost "
                 "all 40,000 daily visitors look like.",
        "expected_in_experiment": True,
    },
    "sam": {
        "key": "user-sam-okafor",
        "name": "Sam Okafor",
        "email": "sam.okafor@meridian.example",
        "title": "Director of Ops, Meridian Foods",
        "role": "customer",
        "plan": "enterprise",
        "betaTester": False,
        "region": "EMEA",
        "accountAgeDays": 1240,
        "deviceType": "desktop",
        "blurb": "Enterprise, but not a beta tester, so the Part 2 targeting "
                 "rule does not catch them. Also randomised into the experiment.",
        "expected_in_experiment": True,
    },
    "jordan": {
        "key": "user-jordan-blake",
        "name": "Jordan Blake",
        "email": "jordan.blake@northwind.example",
        "title": "Operations Lead, Northwind Trading",
        "role": "customer",
        "plan": "enterprise",
        "betaTester": True,
        "region": "EMEA",
        "accountAgeDays": 612,
        "deviceType": "desktop",
        "blurb": "Matches the Part 2 targeting rule, which is evaluated BEFORE "
                 "the default rule. Served deliberately rather than randomly, "
                 "so correctly excluded from the experiment.",
        "expected_in_experiment": False,
    },
    "priya": {
        "key": "user-priya-raman",
        "name": "Priya Raman",
        "email": "priya.raman@lumen.example",
        "title": "Founder, Lumen Analytics",
        "role": "customer",
        "plan": "pro",
        "betaTester": True,
        "region": "APAC",
        "accountAgeDays": 154,
        "deviceType": "mobile",
        "blurb": "Matches the same targeting rule as Jordan. Also excluded — "
                 "an opted-in beta audience is a biased sample by definition.",
        "expected_in_experiment": False,
    },
    "avery": {
        "key": "user-avery-chen",
        "name": "Avery Chen",
        "email": "avery.chen@abccompany.example",
        "title": "QA Engineer, ABC Company",
        "role": "internal-qa",
        "plan": "internal",
        "betaTester": True,
        "region": "AMER",
        "accountAgeDays": 980,
        "deviceType": "desktop",
        "blurb": "Pinned by name to a specific variation for internal testing. "
                 "Individual targets are evaluated first of all, so Avery never "
                 "reaches the experiment — exactly what you want, or your own "
                 "QA team's clicks would end up in the results.",
        "expected_in_experiment": False,
    },
}

DEFAULT_VISITOR_ID = "riley"

# The attributes shown as chips in the app's inspector, in order.
INSPECTED_ATTRIBUTES = ("role", "plan", "betaTester", "region", "accountAgeDays", "deviceType")


def resolve_visitor_id(visitor_id: str | None) -> str:
    """Fall back to the default visitor for unknown or missing ids."""
    if visitor_id in VISITORS:
        return visitor_id
    return DEFAULT_VISITOR_ID


def build_context(visitor_id: str) -> Context:
    """Turn one of the five named demo visitors into a LaunchDarkly Context."""
    return context_from_attributes(VISITORS[visitor_id])


def context_summary(visitor_id: str) -> dict:
    """The attribute name/value pairs the app's inspector shows."""
    visitor = VISITORS[visitor_id]
    return {name: visitor[name] for name in INSPECTED_ATTRIBUTES}


# ---------------------------------------------------------------------------
# Building a context
# ---------------------------------------------------------------------------


def context_from_attributes(visitor: dict) -> Context:
    """Build a LaunchDarkly Context from a plain visitor dict.

    Shared by the named visitors and by the simulated population so both go
    through exactly the same code path — the simulator is not a special case
    with its own shortcut.

    Every attribute set here is something the LaunchDarkly rule builder can
    target on, and something an experiment can be segmented by after the fact.
    Attributes you never send cannot be analysed later, so it is worth sending
    the handful you might want to slice on: here, plan tier and device type.
    """
    builder = (
        Context.builder(visitor["key"])
        .kind(CONTEXT_KIND)
        .name(visitor["name"])
        .set("role", visitor["role"])
        .set("plan", visitor["plan"])
        .set("betaTester", visitor["betaTester"])
        .set("region", visitor["region"])
        .set("accountAgeDays", visitor["accountAgeDays"])
        .set("deviceType", visitor["deviceType"])
    )
    # Email is set as a private attribute: LaunchDarkly uses it for targeting
    # but does not store it. Real landing page traffic is a good place to be
    # careful about what personal data leaves your servers.
    if visitor.get("email"):
        builder.private("email").set("email", visitor["email"])
    return builder.build()


# ---------------------------------------------------------------------------
# The simulated population (for the traffic simulator)
# ---------------------------------------------------------------------------
# Drawn from fixed distributions so a run is reproducible from its seed. The
# mix is chosen to look like a real landing page audience AND to make the
# experiment's exclusion behaviour visible: roughly 2-3% of these visitors match
# the Part 2 targeting rule and are therefore correctly kept out of the results.

_PLANS = (("free", 0.70), ("pro", 0.20), ("enterprise", 0.10))
_REGIONS = (("AMER", 0.55), ("EMEA", 0.30), ("APAC", 0.15))
_DEVICES = (("mobile", 0.62), ("desktop", 0.33), ("tablet", 0.05))

# Share of visitors who have opted in to beta. Combined with the plan mix, this
# is what produces the ~2.4% who match the Part 2 rule (betaTester AND a paid
# plan) and are excluded from the experiment.
_BETA_TESTER_SHARE = 0.08


def _weighted(rng: random.Random, choices: tuple) -> str:
    """Pick one value from ((value, probability), ...)."""
    roll = rng.random()
    cumulative = 0.0
    for value, probability in choices:
        cumulative += probability
        if roll < cumulative:
            return value
    return choices[-1][0]


def generate_population(count: int, seed: int) -> Iterator[dict]:
    """Yield `count` synthetic visitors, deterministically from `seed`.

    A generator rather than a list: 40,000 visitors is a realistic day for this
    landing page, and there is no reason to hold them all in memory at once.

    The keys look like `sim-user-000001`. Sequential keys are fine — the SDK
    hashes the key with a per-iteration seed before bucketing, so orderly keys
    do not produce orderly arm assignments. What matters is that each key is
    distinct and stable, which is why they are derived from the index rather
    than generated randomly.
    """
    rng = random.Random(seed)

    for index in range(count):
        plan = _weighted(rng, _PLANS)
        beta_tester = rng.random() < _BETA_TESTER_SHARE
        yield {
            "key": f"sim-user-{index:06d}",
            "name": f"Simulated Visitor {index:06d}",
            "email": None,
            "role": "customer",
            "plan": plan,
            "betaTester": beta_tester,
            "region": _weighted(rng, _REGIONS),
            # Skewed toward new accounts, as landing page traffic tends to be.
            "accountAgeDays": int(rng.expovariate(1 / 180.0)),
            "deviceType": _weighted(rng, _DEVICES),
        }
