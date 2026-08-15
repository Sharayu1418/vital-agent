"""Labelled set for measuring memory retrieval.

Kept separate from the eval that runs it so the cases can be edited
without reading metric code, and so a diff to the set is legible.

TWO THINGS THAT MAKE A RETRIEVAL SET USEFUL
-------------------------------------------
**Size.** recall@5 against a corpus of four facts is meaningless — you
would score 100% by returning everything. The corpus below is deliberately
larger than any single query needs, so retrieval has to actually choose.

**Distractors.** Facts that are topically adjacent but wrong are what
separate a working retriever from one that returns anything vaguely
related. "User is a beginner at pottery" and "User is an experienced
runner" both mention skill level; a query about ceramics must find the
first and not the second.

The queries are phrased the way somebody actually talks, not the way the
facts are written. That is the whole point of semantic retrieval — if the
query shared words with the fact, keyword matching would do.
"""

# A plausible profile after a few weeks of use. Facts are phrased the way
# VITAL's extractor writes them, because that is what will be embedded in
# production — a set written in some cleaner style would measure a system
# nobody is running.
CORPUS = [
    "User is into pottery and ceramics.",
    "User dislikes gyms and organised fitness classes.",
    "User lives in Albany, New York.",
    "User works from home on Tuesdays and Thursdays.",
    "User has started rock climbing recently.",
    "User prefers mornings for anything demanding.",
    "User is on a tight budget.",
    "User has a dog called Mabel.",
    "User is vegetarian.",
    "User finds large groups draining.",
    "User used to play the piano and wants to start again.",
    "User has a bad knee and avoids running.",
    "User drinks coffee but not after 2pm.",
    "User is learning Spanish.",
    "User's partner works nights.",
    "User enjoys cooking but not washing up.",
    "User gets seasonal low mood in winter.",
    "User has a car but prefers to walk when possible.",
]

# (query, facts that SHOULD be retrieved). Order within the expected list
# does not matter; position in the results does, which is what MRR
# measures.
QUERIES = [
    # The canonical case: no shared words at all between query and fact.
    ("any pottery classes nearby?", ["User is into pottery and ceramics."]),
    ("where can I do some ceramics", ["User is into pottery and ceramics."]),

    # Should find the dislike, and specifically NOT the climbing fact just
    # because both are about exercise.
    ("should I sign up for a fitness class?",
     ["User dislikes gyms and organised fitness classes."]),

    # Synonym, no word overlap.
    ("I want to get better at bouldering",
     ["User has started rock climbing recently."]),

    # Location asked indirectly.
    ("what's on this weekend around here?", ["User lives in Albany, New York."]),

    # Constraint phrased as a question about money.
    ("is there anything free to do", ["User is on a tight budget."]),

    # Scheduling, which touches two facts — both are correct answers.
    ("when should I schedule deep work?",
     ["User prefers mornings for anything demanding.",
      "User works from home on Tuesdays and Thursdays."]),

    # Physical limitation, asked as an activity question. Must find the
    # knee, not the climbing.
    ("thinking about signing up for a 10k",
     ["User has a bad knee and avoids running."]),

    # Diet, asked about a restaurant.
    ("recommend somewhere for dinner", ["User is vegetarian."]),

    # Social energy, phrased as an event question.
    ("there's a big meetup on Friday, should I go?",
     ["User finds large groups draining."]),

    # Musical instrument, no shared words.
    ("I'd like a creative hobby to pick back up",
     ["User used to play the piano and wants to start again."]),

    # Time-of-day constraint phrased as a drink.
    ("fancy an espresso this evening?", ["User drinks coffee but not after 2pm."]),

    # Seasonal mood — the query is about feeling, the fact is about winter.
    ("I've been feeling flat since the clocks changed",
     ["User gets seasonal low mood in winter."]),

    # Pet, asked as a logistics question.
    ("can I go away for the weekend?", ["User has a dog called Mabel."]),

    # Transport preference.
    ("is it worth driving there?",
     ["User has a car but prefers to walk when possible."]),
]
