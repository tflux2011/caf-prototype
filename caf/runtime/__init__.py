"""Runtime layer for the CAF prototype.

Everything in this package is about *running* the system for real: emitting live
telemetry from the services, turning that telemetry into fault signals, and
running sidecar agents that gossip beliefs over real TCP. None of it is used by
the static RST compiler (which only ever parses source).
"""
