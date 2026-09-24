"""StateGraph wiring + the checkpointer for quiz creation.

Same shape as `app.goals.graph`: nodes strung together by conditional edges
that read a field the node itself set, and its own `SqliteSaver` file --
LangGraph owns that schema, so it gets a file of its own rather than sharing
the roadmap checkpointer's.
"""

import logging
from functools import lru_cache
from typing import Any, Literal

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.quizzes.nodes.choose_format import choose_format
from app.quizzes.nodes.choose_topic import choose_topic
from app.quizzes.nodes.commit import commit_quiz
from app.quizzes.nodes.generate import generate_questions
from app.quizzes.nodes.review import present_quiz
from app.quizzes.state import QuizDraftState

log = logging.getLogger(__name__)


def after_choose_topic(state: QuizDraftState) -> Literal["choose_format", "choose_topic"]:
    return "choose_format" if state.get("topic_id") else "choose_topic"


def after_choose_format(state: QuizDraftState) -> Literal["generate_questions", "choose_format"]:
    return "generate_questions" if state.get("question_format") else "choose_format"


def after_review(
    state: QuizDraftState,
) -> Literal["commit_quiz", "generate_questions", "present_quiz", "__end__"]:
    if state.get("status") == "abandoned":
        return END
    if state.get("status") == "generating":
        return "generate_questions"
    return "commit_quiz" if state.get("ready_to_commit") else "present_quiz"


def build() -> StateGraph:
    graph = StateGraph(QuizDraftState)

    graph.add_node("choose_topic", choose_topic)
    graph.add_node("choose_format", choose_format)
    graph.add_node("generate_questions", generate_questions)
    graph.add_node("present_quiz", present_quiz)
    graph.add_node("commit_quiz", commit_quiz)

    graph.add_edge(START, "choose_topic")
    graph.add_conditional_edges("choose_topic", after_choose_topic)
    graph.add_conditional_edges("choose_format", after_choose_format)
    graph.add_edge("generate_questions", "present_quiz")
    graph.add_conditional_edges("present_quiz", after_review)
    graph.add_edge("commit_quiz", END)

    return graph


@lru_cache
def _saver() -> SqliteSaver:
    """One long-lived checkpointer connection, in its own SQLite file --
    same reasoning as `app.goals.graph._saver`."""
    import sqlite3

    path = get_settings().sqlite_path.parent / "quiz_creation_checkpoints.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


@lru_cache
def compiled() -> Any:
    """The compiled graph. Cached -- compiling per request would also mean a
    new checkpointer connection per request."""
    return build().compile(checkpointer=_saver())
