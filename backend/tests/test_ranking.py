"""The shared ranker. Step 7's Dashboard depends on these same guarantees."""

from app.ranking import band, rank_keyed, rank_topics


def topic(id_: int, name: str, understanding: int) -> dict:
    return {"id": id_, "name": name, "user_understanding": understanding}


def test_bands_match_the_frontend_thresholds():
    assert band(-1) == "unknown"
    assert band(0) == "weak"
    assert band(39) == "weak"
    assert band(40) == "fair"
    assert band(69) == "fair"
    assert band(70) == "strong"


def test_weak_outranks_strong():
    ranked = rank_topics([topic(1, "Entropy", 20), topic(2, "Heat transfer", 90)])
    assert [s.topic_name for s in ranked] == ["Entropy", "Heat transfer"]


def test_unscored_is_not_treated_as_weak():
    """-1 means no signal. It should rank above a measured-strong topic but
    below a measured-weak one -- never as if it were the weakest thing we know."""
    ranked = rank_topics(
        [topic(1, "Entropy", 15), topic(2, "Second law", -1), topic(3, "Cycles", 85)]
    )
    assert [s.topic_name for s in ranked] == ["Entropy", "Second law", "Cycles"]
    assert ranked[1].band == "unknown"
    assert "no check-in signal yet" in ranked[1].reason


def test_reasons_cite_only_the_check_in_signal():
    """Deadline ranking is cut, so no reason may imply a due date."""
    ranked = rank_topics(
        [topic(1, "Entropy", 20), topic(2, "Cycles", 30), topic(3, "Second law", -1)]
    )
    assert all("due" not in s.reason for s in ranked)
    assert all(set(s.evidence) == {"need"} for s in ranked)


def test_score_is_the_need_alone():
    ranked = rank_topics([topic(1, "Entropy", 20)])
    assert ranked[0].score == 0.8
    assert ranked[0].evidence == {"need": 0.8}


def test_rank_keyed_carries_the_reason_of_the_topic_that_moved_it():
    signals = rank_topics([topic(1, "Entropy", 10), topic(2, "Cycles", 80)])
    ordered = rank_keyed([("m1", [2]), ("m2", [1, 2])], signals)
    assert [key for key, _, _ in ordered] == ["m2", "m1"]
    assert "Entropy" in ordered[0][2]


def test_rank_keyed_is_stable_for_unmatched_items():
    """Milestones the model ordered deliberately must not be shuffled just
    because they reference no ranked topic."""
    signals = rank_topics([topic(1, "Entropy", 10)])
    ordered = rank_keyed(
        [("a", []), ("b", []), ("c", [1]), ("d", [])], signals
    )
    assert [key for key, _, _ in ordered] == ["c", "a", "b", "d"]
    assert ordered[1][2] is None
