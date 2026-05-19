"""Signal-strategy configuration.

``SignalConfig`` is defined in :mod:`relay_v2.harness.protocol` because
it is part of the harness contract (``Harness.spawn`` accepts it,
spec.md §4.1). It is re-exported here so the ``signaling`` package has
the import location plan.md's Phase 1 layout expects.
"""

from relay_v2.harness.protocol import SignalConfig

__all__ = ["SignalConfig"]
