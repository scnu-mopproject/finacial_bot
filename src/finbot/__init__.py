"""finbot — A-share intelligent agent toolkit.

Layers
------
- ``finbot.data``      : ingest news & market history (AkShare, mock fallback)
- ``finbot.features``  : factor / feature engineering
- ``finbot.models``    : limit-up probability ranker
- ``finbot.strategy``  : portfolio-aware action planning
- ``finbot.pipeline``  : daily end-to-end orchestration
- ``finbot.cli``       : command-line surface consumed by the Claude Code Skills

The heavy reasoning (news interpretation, regime read, final stock picks and
strategy narrative) is delegated to Claude Code *Agents* defined under
``.claude/agents``; this package provides the deterministic data/model plumbing
those agents call through ``finbot.cli``.
"""

__version__ = "0.1.0"
