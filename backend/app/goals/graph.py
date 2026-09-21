"""StateGraph wiring + the checkpointer.

Edges are exactly the spec's two conditionals:

    clarify_intent      -> decompose_goal if clarified_goal is set, else itself
    present_for_approval -> commit_roadmap if approval_complete, else apply_edits

`load_context` is the one addition: the spec has `materials_context` and
`calendar_context` in state without saying who fills them, and doing it in a node
of its own keeps `personalize_decomposition` a pure function of its inputs (and
so testable without a database).
"""

import logging
from functools import lru_cache
from typing import Any, Literal

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.db import connection
from app.goals.context import calendar_context, materials_context
from app.goals.nodes.approve import apply_edits, present_for_approval
from app.goals.nodes.clarify import clarify_intent
from app.goals.nodes.commit import commit_roadmap
from app.goals.nodes.decompose import decompose_goal
from app.goals.nodes.personalize import personalize_decomposition
from app.goals.state import RoadmapState

log = logging.getLogger(__name__)


def load_context(state: RoadmapState) -> dict[str, Any]:
    with connection() as conn:
        return {
            "materials_context": materials_context(conn),
            "calendar_context": calendar_context(conn),
        }


def after_clarify(state: RoadmapState) -> Literal["decompose_goal", "clarify_intent"]:
    return "decompose_goal" if state.get("clarified_goal") else "clarify_intent"


def after_approval(state: RoadmapState) -> Literal["commit_roadmap", "apply_edits"]:
    return "commit_roadmap" if state.get("approval_complete") else "apply_edits"


def build() -> StateGraph:
    graph = StateGraph(RoadmapState)

    graph.add_node("clarify_intent", clarify_intent)
    graph.add_node("load_context", load_context)
    graph.add_node("decompose_goal", decompose_goal)
    graph.add_node("personalize_decomposition", personalize_decomposition)
    graph.add_node("present_for_approval", present_for_approval)
    graph.add_node("apply_edits", apply_edits)
    graph.add_node("commit_roadmap", commit_roadmap)

    graph.add_edge(START, "clarify_intent")
    graph.add_conditional_edges("clarify_intent", after_clarify)
    # Context is loaded after decomposition, not before: the spec is explicit
    # that decompose_goal must not see Materials or Calendar.
    graph.add_edge("decompose_goal", "load_context")
    graph.add_edge("load_context", "personalize_decomposition")
    graph.add_edge("personalize_decomposition", "present_for_approval")
    graph.add_conditional_edges("present_for_approval", after_approval)
    # No silent-acceptance path: an edit always returns to approval.
    graph.add_edge("apply_edits", "present_for_approval")
    graph.add_edge("commit_roadmap", END)

    return graph


@lru_cache
def _saver() -> SqliteSaver:
    """One long-lived checkpointer connection.

    Its own SQLite file, not the app database: LangGraph owns that schema and
    migrating it is not our business. `check_same_thread=False` because the
    router runs the graph in a worker thread.
    """
    import sqlite3

    path = get_settings().sqlite_path.parent / "roadmap_checkpoints.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


@lru_cache
def compiled():
    """The compiled graph. Cached -- compiling per request would also mean a new
    checkpointer connection per request."""
    return build().compile(checkpointer=_saver())
