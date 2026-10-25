"""Ghost Data Still Exists - deletion-audit agent.

Package layout follows the plan-execute-verify design:

- ``ghost.data``      evidence parsers, the Observation model and the SQLite index
- ``ghost.identity``  subject resolution (current + historical identifiers)
- ``ghost.policy``    deletion rules (category -> required treatment)
- ``ghost.portfolio`` PLAN: deterministic per-case portfolio
- ``ghost.agent``     EXECUTE / VERIFY: bounded agent loop, guards, baseline
- ``ghost.compose``   ordered step composition
- ``ghost.evaluation``metrics against the sample answers and custom cases
"""
