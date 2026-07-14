"""The belief-sharing gossip fabric (Stage 3).

Turns single-node triage into decentralized incident reasoning. Nodes exchange
provenance-bearing *diagnostic hypotheses* (beliefs), fuse corroborating and
contradicting evidence with the bounded rule of Eq. (4), and cross into a
*provisional consensus* when fused confidence over a domain quorum exceeds a
threshold. That consensus is what lets a node tell an *isolated* fault (just me)
from a *systemic* one (my whole domain) -- research question RQ2.

As in Stage 2 the reasoning is deterministic: this demonstrates the mechanism
(corroboration changes classification), not a calibrated consensus model. The
fusion rule is explicitly the paper's preliminary heuristic, not subjective
logic or Dempster-Shafer.
"""

from __future__ import annotations

from .belief import Belief
from .fusion import fuse
from .node import NodeAgent
from .gossip import Fabric

__all__ = ["Belief", "fuse", "NodeAgent", "Fabric"]
