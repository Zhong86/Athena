"""The research tool -- the spec's branch for milestones Materials cannot ground.

Deliberately a stub for now (decision §9.4): the *branch* and the `source:
"research"` tagging are real, the fetching is not. When Step 9 lands
`goal.allow_external_sources` and a web fetcher, only `investigate` changes.

Tagging honestly now is what makes that a drop-in. The alternative -- pointing an
ungroundable milestone at the nearest topic anyway -- is precisely the failure
mode the spec's branch exists to prevent, and it would be invisible afterwards:
nothing on screen distinguishes a real materials link from a forced one.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ResearchNote:
    """What the research path can say about a milestone today."""

    reason: str
    reason_long: str
    # Empty until a real fetcher lands. Kept in the shape the eventual
    # implementation needs so callers do not change when it does.
    sources: tuple[str, ...] = ()

    @property
    def grounded(self) -> bool:
        return bool(self.sources)


def available() -> bool:
    """Whether real research can run. False until Step 9's setting and fetcher
    exist; callers should still take the branch and still tag `"research"`."""
    return False


def investigate(title: str, description: str) -> ResearchNote:
    """Explain a milestone that no uploaded material covers.

    The copy says what is actually true -- that nothing uploaded covers this --
    rather than implying a source was consulted.
    """
    return ResearchNote(
        reason="Not covered by anything you've uploaded yet",
        reason_long=(
            f"Nothing in your Materials matches “{title}”, so Hermes kept it in the "
            "roadmap on the strength of the goal alone. Upload material for this and "
            "it will start being ranked against your check-ins like the rest."
        ),
    )
