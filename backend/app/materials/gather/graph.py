"""StateGraph wiring for the gather graph.

No checkpointer, unlike `app.goals.graph`: that graph needs `SqliteSaver`
because `interrupt()` suspends it mid-run across separate HTTP requests (a
student answering a clarifying question). This graph never suspends -- it's
cron-triggered with no one to interrupt for -- so there's nothing to resume
from a checkpoint, and compiling without one keeps a single `POST
/materials/gather/run` a single, self-contained `invoke()`.
"""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.materials.gather.apply import apply_decisions
from app.materials.gather.relevance import decide_relevance
from app.materials.gather.scan_drive import scan_drive
from app.materials.gather.scan_inbox import scan_inbox
from app.materials.gather.state import GatherState


def build() -> StateGraph:
    graph = StateGraph(GatherState)

    graph.add_node("scan_inbox", scan_inbox)
    graph.add_node("scan_drive", scan_drive)
    graph.add_node("decide_relevance", decide_relevance)
    graph.add_node("apply_decisions", apply_decisions)

    graph.add_edge(START, "scan_inbox")
    graph.add_edge("scan_inbox", "scan_drive")
    graph.add_edge("scan_drive", "decide_relevance")
    graph.add_edge("decide_relevance", "apply_decisions")
    graph.add_edge("apply_decisions", END)

    return graph


@lru_cache
def compiled():
    return build().compile()
