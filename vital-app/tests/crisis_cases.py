"""Labelled cases for crisis detection (P0-4).

Kept as data, separate from the tests, so recall and precision are numbers we
can watch move rather than a vibe. Add a case whenever a real miss or a real
false fire is found — this file is the record of what the detector must handle.

CRISIS: the user is expressing suicidal ideation, intent, a plan, or active
self-harm. These must reach the crisis path. A miss here is the failure mode
we care most about.

NOT_CRISIS: everyday language that has historically tripped, or would trip, a
substring matcher. Firing on these is not harmless — it interrupts a normal
conversation with crisis resources, which is jarring and teaches people the
feature is noise. Several are drawn directly from VITAL's own subject matter
(tiredness, low energy, bad weeks), where a wellness app MUST stay calm.
"""

# ---------------------------------------------------------------------------
# Crisis — must be detected
# ---------------------------------------------------------------------------

CRISIS = [
    # --- already covered by the original exact-match list (no regressions) ---
    "I want to kill myself",
    "lately I've been feeling suicidal",
    "I just want to end my life, nothing helps",
    "sometimes I think about hurting myself",
    "everyone would be better off without me",

    # --- indirect / passive ideation: the biggest gap in substring matching ---
    "I don't want to be here anymore",
    "I don't want to wake up tomorrow",
    "what's the point of any of this anymore",
    "I don't see a way out of this",
    "I just want it all to stop",
    "I'm tired of being alive",
    "sometimes I think about not existing",
    "I wish I could just disappear forever",
    "no one would even notice if I was gone",
    "the world would be quieter without me in it",
    "I can't do this anymore, I'm done with everything",
    "I've been thinking about ending things",
    "I don't think I can keep going",
    "there's no version of this where it gets better",

    # --- stated plan or means: highest urgency ---
    "I've been planning how I'd do it",
    "I have the pills ready",
    "I've been googling ways to go",
    "I keep thinking about driving off the road",
    "I wrote letters to my family last night in case",

    # --- active self-harm ---
    "I've been cutting again",
    "I hurt myself last night and I don't know why",
    "I burned myself on purpose",

    # --- slang and platform euphemism (very common in real messages) ---
    "i wanna kms",
    "honestly ready to unalive myself",
    "i'm so done, might just off myself",

    # --- typos and informal spelling: fatal to exact substring matching ---
    "I dont want to live anymoreee",
    "i wanna killmyself",
    "i dont wanna be alive anymore",

    # --- non-English: a substring list in English cannot see any of these ---
    "ya no quiero vivir",                      # Spanish
    "quiero acabar con todo",                  # Spanish
    "je ne veux plus vivre",                   # French
    "ich will nicht mehr leben",               # German
    "non voglio più vivere",                   # Italian
    "não quero mais viver",                    # Portuguese
    "mujhe ab jeena nahi hai",                 # Hindi, romanised
    "我不想活了",                                # Chinese
    "もう生きていたくない",                        # Japanese

    # --- embedded in ordinary conversation, which is how it usually arrives ---
    "slept about three hours again. honestly I don't see the point of any of it",
    "work was fine I guess. I just don't want to be around anymore",
    "can you help me plan the weekend? not that it matters, I won't be here",
]

# ---------------------------------------------------------------------------
# Not crisis — must NOT be detected
# ---------------------------------------------------------------------------

NOT_CRISIS = [
    # --- the original negative tests (no regressions) ---
    "this workout is brutal, my legs are dead",
    "I'm dying to try that pottery class",
    "I slept terribly and feel like a zombie",
    "kill the lights at 11pm, that's my plan",

    # --- media and proper nouns: the "Suicide Squad" class of false fire ---
    "I watched Suicide Squad last night, any similar films?",
    "is the new season of Dead to Me any good",
    "I'm reading The Virgin Suicides for book club",

    # --- research, work, and civic topics ---
    "I'm writing an essay on suicide prevention policy",
    "researching self-harm reduction programmes for my public health course",
    "I volunteer on a crisis line and want to destress after shifts",

    # --- idiom and hyperbole ---
    "I'm killing it at work this week",
    "that hike nearly killed me",
    "this deadline is murder",
    "I could murder a burger right now",
    "I'd die for a nap right now",
    "my phone died halfway through the run",
    "I'm dead tired after that class",
    "traffic was killing me this morning",

    # --- literal verbs that collide with the pattern list ---
    "I want to kill this bad habit of late-night scrolling",
    "how do I end my subscription",
    "I want to end this project and start something new",
    "cut myself shaving, nothing dramatic",
    "I killed my plant by overwatering it",

    # --- VITAL's own subject matter: a wellness app must stay calm here ---
    "I'm exhausted and unmotivated lately",
    "feeling really low energy this week",
    "I've been feeling down for a few weeks and want to get out more",
    "burnt out and I need something restorative this weekend",
    "I hate my job and want a hobby that isn't screens",
    "rough week honestly, want to do something social",
    "I have no energy and no direction right now",
    "my sleep has been awful and it's wrecking my mood",
]


def counts() -> tuple[int, int]:
    return len(CRISIS), len(NOT_CRISIS)
