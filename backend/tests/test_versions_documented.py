"""v8.7: Single source of truth for agent version assertions.

Replaces the 15 hardcoded `test_version_bumped` methods that drift each
time an agent's version is bumped without updating the test (which was
producing 8 permanently-failing tests masking 2 real regressions —
SMA200 and pipeline graceful degradation).

This test parses the "Versions actuelles" markdown table in CLAUDE.md
and asserts that each documented version matches the agent's runtime
`version` class attribute. A bump that misses the doc is caught here;
no per-agent test maintenance required.

If you bump an agent version, update CLAUDE.md table — that's it.
"""

import importlib
import re
from pathlib import Path

import pytest


CLAUDE_MD = Path(__file__).resolve().parent.parent.parent / "CLAUDE.md"

# Maps the human-readable agent name (left column of the CLAUDE.md table)
# to the (module path, class name) tuple for runtime introspection.
# Only documented agents that expose a `version` class attribute are listed.
AGENT_REGISTRY: dict[str, tuple[str, str]] = {
    "News": ("backend.app.agents.agent_news", "AgentNews"),
    "Scoring": ("backend.app.agents.agent_scoring", "AgentScoring"),
    "Scoring 2": ("backend.app.agents.agent_scoring_2", "AgentScoring2"),
    "Scoring 3": ("backend.app.agents.agent_scoring_3", "AgentScoring3"),
    "Scoring 4": ("backend.app.agents.agent_scoring_4", "AgentScoring4"),
    "Trader 1": ("backend.app.agents.agent_trader", "AgentTrader"),
    "Trader 2": ("backend.app.agents.agent_trader_2", "AgentTrader2"),
    "Trader 3": ("backend.app.agents.agent_trader_3", "AgentTrader3"),
    "Trader 4": ("backend.app.agents.agent_trader_4", "AgentTrader4"),
    "Journal 1": ("backend.app.agents.agent_journal", "AgentJournal"),
    "Journal 2": ("backend.app.agents.agent_journal_2", "AgentJournal2"),
    "Journal 3": ("backend.app.agents.agent_journal_3", "AgentJournal3"),
    "Journal 4": ("backend.app.agents.agent_journal_4", "AgentJournal4"),
    "Learning 1": ("backend.app.agents.agent_learning", "AgentLearning"),
    "Learning 2": ("backend.app.agents.agent_learning_2", "AgentLearning2"),
    "Learning 3": ("backend.app.agents.agent_learning_3", "AgentLearning3"),
    "Learning 4": ("backend.app.agents.agent_learning_4", "AgentLearning4"),
    "Infrastructure": ("backend.app.agents.agent_infrastructure", "AgentInfrastructure"),
    "Performance": ("backend.app.agents.agent_performance", "AgentPerformance"),
    "Auditor": ("backend.app.agents.agent_auditor", "AgentAuditor"),
}


def _parse_versions_table() -> dict[str, str]:
    """Extract the (Agent → Version) mapping from CLAUDE.md.

    Looks for the markdown table under the heading "## Versions actuelles".
    Skips header and separator rows. Returns {agent_name: version}.
    """
    text = CLAUDE_MD.read_text()
    # Find the "Versions actuelles" section (in the version philosophy block)
    marker = "Versions actuelles"
    idx = text.find(marker)
    assert idx != -1, "CLAUDE.md missing 'Versions actuelles' table"

    # Capture lines starting with `|` after the marker, until a blank line
    section = text[idx:]
    rows = re.findall(r"^\|\s*([^|]+?)\s*\|\s*([\d.]+)\s*\|.*$", section, flags=re.MULTILINE)
    # First row will be the header "Agent | Version | ..." — filter
    versions: dict[str, str] = {}
    for name, version in rows:
        if name.strip().lower() in {"agent", "---"}:
            continue
        versions[name.strip()] = version.strip()
    return versions


def _runtime_version(module_path: str, class_name: str) -> str:
    """Import the agent class lazily and return its `version` attribute as str."""
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return str(cls.version)


@pytest.mark.parametrize("agent_name", sorted(AGENT_REGISTRY))
def test_documented_version_matches_runtime(agent_name: str):
    """Each documented version in CLAUDE.md matches the agent's runtime version."""
    versions = _parse_versions_table()
    if agent_name not in versions:
        pytest.skip(
            f"{agent_name} not documented in CLAUDE.md 'Versions actuelles' — "
            f"add it or remove from AGENT_REGISTRY"
        )
    documented = versions[agent_name]
    module_path, class_name = AGENT_REGISTRY[agent_name]
    runtime = _runtime_version(module_path, class_name)
    assert documented == runtime, (
        f"{agent_name}: CLAUDE.md says {documented!r}, runtime is {runtime!r} — "
        f"update CLAUDE.md 'Versions actuelles' table OR revert the bump"
    )


def test_versions_table_parses():
    """Sanity check: the table itself can be parsed and is not empty."""
    versions = _parse_versions_table()
    assert versions, "Failed to parse 'Versions actuelles' table from CLAUDE.md"
    assert len(versions) >= 5, f"Suspiciously few versions parsed: {versions}"
