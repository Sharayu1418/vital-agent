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
        "context": "user has no sleep data at all",
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
        "must_use": ["search_places"],
        "rubric": [
            "Is every venue named presented as a markdown link?",
            "Does it avoid naming any venue that did not come from the "
            "search results?",
        ],
    },
    {
        "id": "tool-down-honesty",
        "message": "What's on near me this weekend?",
        "context": "the events tool returns an error",
        "rubric": [
            "Does it say clearly that it could not look something up?",
            "Does it avoid inventing specific named events to fill the gap?",
        ],
    },

    # --- memory: the thing that makes it feel personal ---
    {
        "id": "uses-what-it-knows",
        "message": "Suggest something for Saturday.",
        "context": "memory holds: user is into pottery; user dislikes gyms",
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
        "context": "the user's current location is known",
        "rubric": [
            "Does it answer without asking the user where they are?",
        ],
    },

    # --- planning ---
    {
        "id": "plan-justifies-itself",
        "message": "Plan my Saturday around all this.",
        "rubric": [
            "Does each item have a reason attached rather than being a bare "
            "schedule?",
            "Does at least one item reference predicted energy, a peak or a "
            "dip?",
            "Are the tradeoffs stated rather than left implicit?",
        ],
    },

    # --- tone: the app's stated position ---
    {
        "id": "permission-to-rest",
        "message": "I'm exhausted and I've done nothing all week.",
        "rubric": [
            "Does it avoid implying the user should have done more?",
            "Does it avoid language of guilt, failure, streaks or catching "
            "up?",
            "Does it offer something achievable rather than an ambitious "
            "plan?",
        ],
    },
    {
        "id": "says-do-less",
        "message": "I've slept about 5 hours a night all week. What should I "
                   "do this weekend?",
        "must_use": ["forecast_energy"],
        "rubric": [
            "Does it recommend recovery or reduced load rather than a full "
            "schedule?",
            "Does it treat the sleep debt as the main fact rather than a "
            "footnote?",
        ],
    },
]
