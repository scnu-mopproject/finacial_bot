"""finbot — A-share intelligent agent toolkit.

Layers
------
- ``finbot.data``      : data-source abstraction + local incremental warehouse
- ``finbot.features``  : economically-grounded factor library + neutralization
- ``finbot.labels``    : forward-return labels + panel dataset
- ``finbot.models``    : cross-sectional forward-return ranker
- ``finbot.backtest``  : walk-forward validation with A-share frictions
- ``finbot.portfolio`` : risk model + target-portfolio construction + orders
- ``finbot.regime``    : market-state classification
- ``finbot.pipeline``  : end-to-end orchestration
- ``finbot.cli``       : command-line surface consumed by the Claude Code Skills

The heavy reasoning (news interpretation, regime read, final stock picks and
strategy narrative) is delegated to Claude Code *Agents* defined under
``.claude/agents``; this package provides the deterministic data/model plumbing
those agents call through ``finbot.cli``.
"""

__version__ = "0.1.0"
