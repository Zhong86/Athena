"""The shared ranker. Step 7's Dashboard depends on these same guarantees."""

from app.ranking import Deadline, band, match_deadline, rank_keyed, rank_topics


def topic(id_: int, name: str, understanding: int) -> dict:
    return {"id": id_, "name": name, "user_understanding": understanding}


def test_bands_match_the_frontend_thresholds():
    assert band(-1) == "unknown"
    assert band(0) == "weak"
    assert band(39) == "weak"
    assert band(40) == "fair"
    assert band(69) == "fair"
    assert band(70) == "strong"


def test_weak_outranks_strong_without_deadlines():
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


def test_empty_calendar_is_a_no_op():
    """Step 5 is not built, so this is the shape every call has today."""
    with_none = rank_topics([topic(1, "Entropy", 20), topic(2, "Cycles", 30)])
    with_empty = rank_topics([topic(1, "Entropy", 20), topic(2, "Cycles", 30)], [])
    assert [s.score for s in with_none] == [s.score for s in with_empty]
    assert all(s.deadline is None for s in with_none)
    # No invented dates in the copy.
    assert all("due" not in s.reason for s in with_none)


def test_near_deadline_moves_a_topic_up():
    deadlines = [Deadline(title="Titration lab", due_at="2026-09-24", days_until=3)]
    ranked = rank_topics(
        [topic(1, "Cycles", 35), topic(2, "Titration", 38)], deadlines
    )
    assert ranked[0].topic_name == "Titration"
    assert "due in 3 days" in ranked[0].reason


def test_deadline_cannot_promote_a_strong_topic_over_a_weak_one():
    deadlines = [Deadline(title="Heat transfer quiz", due_at="2026-09-22", days_until=1)]
    ranked = rank_topics(
        [topic(1, "Heat transfer", 95), topic(2, "Entropy", 10)], deadlines
    )
    assert ranked[0].topic_name == "Entropy"


def test_overdue_is_maximally_urgent():
    ranked = rank_topics(
        [topic(1, "Entropy", 50)],
        [Deadline(title="Entropy set", due_at="2026-09-18", days_until=-3)],
    )
    assert "was due 3d ago" in ranked[0].reason


def test_distant_deadline_exerts_no_pressure():
    far = rank_topics(
        [topic(1, "Entropy", 50)],
        [Deadline(title="Entropy set", due_at="2026-12-01", days_until=60)],
    )
    none = rank_topics([topic(1, "Entropy", 50)])
    assert far[0].score == none[0].score


def test_match_deadline_substring_and_fuzzy():
    deadlines = [
        Deadline(title="Week 4 entropy problem set", due_at="x", days_until=5),
        Deadline(title="Heat-transfer lab", due_at="x", days_until=2),
    ]
    assert match_deadline("Entropy", deadlines).days_until == 5
    assert match_deadline("Heat transfer", deadlines).days_until == 2
    assert match_deadline("Quantum tunnelling", deadlines) is None


def test_match_deadline_picks_the_nearest_of_several():
    deadlines = [
        Deadline(title="Entropy midterm", due_at="x", days_until=9),
        Deadline(title="Entropy quiz", due_at="x", days_until=2),
    ]
    assert match_deadline("Entropy", deadlines).title == "Entropy quiz"


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
