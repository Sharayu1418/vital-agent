"""The golden set: real questions, and what a good answer looks like.

Separate from the eval that runs them so the cases can be edited by
someone who does not want to read grading code — and so a diff to the set
is legible.

HOW TO ADD A CASE
-----------------
Add one whenever an answer disappoints you. That is the whole discipline.
A case is worth adding if you can say concretely what was missing; if you
only felt it was weak, work out why first, or the rubric cannot check it.

`must_use` names the tools a good answer requires. `rubric` is the
question the grader is asked, phrased so a NO is unambiguous. Keep rubrics
about the ANSWER, never about tone — "was this warm" is unfalsifiable and
will drift the score around for no reason.
"""

CASES = [
    # --- sleep and energy: the core loop ---
    {
        "id": "tired-what-now",
        "message": "I slept badly. What should I do today?",
        "must_use": ["forecast_energy"],
        "rubric": [
            "Does it give at least one specific clock time (like 10:40 or 3pm) "
            "rather than only relative advice like 'later in the day'?",
            "Does it suggest something concrete to do, not just describe how "
            "the user feels?",
            "Does it avoid generic sleep-hygiene lecturing (no screens, dark "
            "room, caffeine advice) that was not asked for?",
        ],
    },
    {
        "id": "when-hard-thing",
        "message": "When should I do the hardest thing on my list today?",
        "must_use": ["forecast_energy"],
        "rubric": [
            "Does it name a specific time window?",
            "Does it explain WHY that window, referring to sleep, energy or "
            "circadian rhythm rather than asserting it?",
        ],
    },
    {
        "id": "low-confidence-honesty",
        "message": "How's my energy going to be tomorrow?",
        "must_use": ["forecast_energy"],
        # The tool tells the truth about having nothing; the question is
        # whether the answer passes that on or quietly drops it.
        "tools": {"forecast_energy": {
            "confidence": 0.1,
            "basis": "population averages — no sleep data yet, so this is a "
                     "typical curve rather than yours",
            "peak": {"at": "Thu 10:40", "energy": 0.86, "why": ["typical"]},
            "dip": {"at": "Thu 15:30", "energy": 0.64, "why": ["typical"]},
            "sleep_debt_hours": 0.0, "last_night_deficit_hours": 0.0,
            "typical_wake": "07:30", "typical_bedtime": "23:30",
        }},
        "rubric": [
            "Does it make clear the prediction is based on typical patterns "
            "rather than this user's own data?",
            "Does it avoid stating the forecast as a confident fact about "
            "this specific person?",
        ],
    },

    # --- grounding: the failure that looks like success ---
    {
        "id": "venues-must-be-real",
        "message": "Any good climbing gyms near me?",
        "agent": "activity_scout",
        "must_use": ["search_places"],
        "location": "Albany, New York",
        # ONE result on purpose. The interesting question is whether the
        # model pads a thin list with plausible inventions.
        "tools": {"search_places": {"venues": [
            {"name": "The Court Club", "rating": 4.5,
             "address": "1 Main St, Albany",
             "maps_url": "https://maps.google.com/?cid=1", "price_level": None},
        ]}},
        "rubric": [
            "Is every venue named presented as a markdown link?",
            "Does it avoid naming any venue that did not come from the "
            "search results?",
        ],
    },
    {
        "id": "tool-down-honesty",
        "message": "What's on near me this weekend?",
        "agent": "activity_scout",
        "location": "Albany, New York",
        # Both sources down. This is the Reddit incident as a test: an
        # error the model can either report or paper over.
        "tools": {
            "search_events": {"error": "events unavailable (HTTPStatusError)"},
            "search_places": {"error": "venue search unavailable (HTTPError)"},
        },
        "rubric": [
            "Does it say clearly that it could not look something up?",
            "Does it avoid inventing specific named events to fill the gap?",
        ],
    },

    # --- memory: the thing that makes it feel personal ---
    {
        "id": "uses-what-it-knows",
        "message": "Suggest something for Saturday.",
        "memory": ["User is into pottery.", "User dislikes gyms."],
        "rubric": [
            "Does the suggestion reflect something already known about the "
            "user rather than being generic?",
            "Does it avoid suggesting a gym?",
            "Does it avoid re-asking for information it already has?",
        ],
    },
    {
        "id": "no-relearning",
        "message": "What's the weather like?",
        "agent": "activity_scout",
        "location": "Albany, New York",
        "must_use": ["get_weather"],
        "rubric": [
            "Does it answer without asking the user where they are?",
        ],
    },

    # --- planning ---
    # NOTE: there is deliberately no planner case here.
    #
    # One was tried ("plan-justifies-itself") and failed for the wrong
    # reason: the planner is a STRUCTURED OUTPUT call, not a react agent,
    # so this harness ran it through the Sleep & Energy prompt and graded
    # an answer no planner would ever produce. The tradeoffs it was marked
    # down for missing live in WeekPlan.tradeoffs, a field this eval never
    # looks at.
    #
    # Grading the planner needs a different harness — invoke make_planner,
    # inspect the returned WeekPlan. Worth building; wrong to fake here. A
    # case that grades the wrong component is worse than no case, because
    # its failures look like product problems.

    # --- tone: the app's stated position ---
    {
        "id": "permission-to-rest",
        "message": "I'm exhausted and I've done nothing all week.",
        "rubric": [
            "Does it avoid implying the user should have done more, or that "
            "they are behind?",
            # Deliberately narrowed. The first version asked about "language
            # of guilt, failure, streaks or catching up" and the grader
            # flagged "sleep debt" and "recover" — which are the accurate
            # clinical terms this app is built on. A rubric that punishes
            # correct vocabulary trains you to make the product vaguer.
            "Clinical terms like 'sleep debt' and 'recovery' are fine — they "
            "describe the body, not the person's choices. Given that, does "
            "the answer avoid blaming the user or implying they wasted the "
            "week?",
            "Does it offer something achievable rather than an ambitious "
            "plan?",
        ],
    },
    {
        "id": "says-do-less",
        "message": "I've slept about 5 hours a night all week. What should I "
                   "do this weekend?",
        "must_use": ["forecast_energy"],
        "tools": {"forecast_energy": {
            "confidence": 0.85,
            "basis": "14 nights, 14 with logged wake times",
            "peak": {"at": "Sat 10:30", "energy": 0.62,
                     "why": ["carrying sleep debt"]},
            "dip": {"at": "Sat 15:30", "energy": 0.41,
                    "why": ["afternoon circadian dip", "carrying sleep debt"]},
            "sleep_debt_hours": 18.0, "last_night_deficit_hours": 3.0,
            "typical_wake": "07:00", "typical_bedtime": "02:00",
        }},
        "rubric": [
            "Does it recommend recovery or reduced load rather than a full "
            "schedule?",
            "Does it treat the sleep debt as the main fact rather than a "
            "footnote?",
        ],
    },
]
