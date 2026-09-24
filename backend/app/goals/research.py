"""The research tool -- the spec's branch for milestones Materials cannot ground.

Runs through Hermes's agent Runs API (`ask_agent_json`), not a bare completion:
a milestone nothing uploaded covers needs whatever real tools (search,
browsing) the gateway has, not the model's unaided guess. If the run cannot be
started, errors, or answers unusably, `investigate` falls back to a plain,
honest "not covered" note -- the branch and its `source: "research"` tag stay
real either way, they just carry no sources when Hermes could not look.
"""

import logging
from dataclasses import dataclass

from app.goals.llm import LLMUnavailable, ask_agent_json

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You research one study topic for a student's roadmap milestone. Use "
    "whatever tools you have (search, browsing) to find real, current "
    "information about it -- never fabricate a source or a fact you did not "
    "find. Reply with JSON only -- no prose, no code fences."
)

_FALLBACK_REASON = "Not covered by anything you've uploaded yet"
_FALLBACK_REASON_LONG = (
    "Nothing in your Materials matches this milestone, and Hermes could not look "
    "into it just now. Upload material for this and it will start being ranked "
    "against your check-ins like the rest."
)


@dataclass(frozen=True)
class ResearchNote:
    """What the research path can say about a milestone."""

    reason: str
    reason_long: str
    # One line per tool the run actually completed. Empty means the run did not
    # produce anything usable and `investigate` fell back to the plain
    # not-covered note -- never a placeholder for a source that wasn't real.
    sources: tuple[str, ...] = ()

    @property
    def grounded(self) -> bool:
        return bool(self.sources)


def _prompt(title: str, description: str) -> str:
    return f"""Milestone: "{title}"
What it involves: {description or '(no description)'}

Nothing the student has uploaded covers this. Research it and explain why it
matters and what to expect, grounded in what you actually find.

Reply with JSON only:
{{"reason": "one line, under 110 chars, no trailing period",
  "reason_long": "2-3 sentences addressed to the student, grounded in what you found"}}

If you cannot find anything trustworthy, say so plainly in reason_long instead of
inventing detail."""


def investigate(title: str, description: str) -> ResearchNote:
    """Ask Hermes to actually research a milestone nothing uploaded covers."""
    try:
        reply, sources = ask_agent_json(_prompt(title, description), instructions=SYSTEM_PROMPT)
    except LLMUnavailable as exc:
        log.warning("roadmap: research investigate failed for %r (%s)", title, exc)
        return ResearchNote(reason=_FALLBACK_REASON, reason_long=_FALLBACK_REASON_LONG)

    reason = str(reply.get("reason") or "").strip()[:160] or _FALLBACK_REASON
    reason_long = str(reply.get("reason_long") or "").strip() or _FALLBACK_REASON_LONG
    return ResearchNote(reason=reason, reason_long=reason_long, sources=tuple(sources))
