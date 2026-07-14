"""The gossip fabric: synchronous, seeded-random dissemination and fusion.

This is an in-process simulation of Algorithm 1. Peer selection is randomized
(the epidemic model under which the O(log N) dissemination bound holds) but
seeded, so runs are reproducible and testable; same-failure-domain peers are
preferred, biasing corroboration to where consensus is formed. Rounds run to
quiescence (no new belief ingested), so results are order-independent. A real
deployment would run this asynchronously over a network substrate; the fusion
and consensus logic is identical.
"""

from __future__ import annotations

import random

from .node import NodeAgent


class Fabric:
    """A set of node agents that gossip beliefs to quiescence."""

    def __init__(
        self,
        nodes: list[NodeAgent],
        *,
        fanout: int = 2,
        gossip: bool = True,
        seed: int = 1337,
    ) -> None:
        self.nodes = nodes
        self.fanout = fanout
        self.gossip = gossip
        self._by_id = {n.id: n for n in nodes}
        self._rng = random.Random(seed)

    def node(self, instance_id: str) -> NodeAgent:
        return self._by_id[instance_id]

    def _select_peers(self, node: NodeAgent) -> list[NodeAgent]:
        others = [n for n in self.nodes if n.id != node.id]
        if not others:
            return []
        same = [n for n in others if n.domain == node.domain]
        diff = [n for n in others if n.domain != node.domain]
        # Prefer same-domain peers (shuffled), fall back to cross-domain. This
        # keeps the cluster connected while concentrating exchange where a
        # domain quorum can form.
        self._rng.shuffle(same)
        self._rng.shuffle(diff)
        ordered = same + diff
        return ordered[: self.fanout]

    def run(self, max_rounds: int = 64) -> int:
        """Gossip until no node ingests a new belief. Returns rounds executed.

        With ``gossip=False`` no exchange happens: each node keeps only what it
        observed, so it can never see peer corroboration -- the honest baseline
        against which belief gossip is measured.
        """

        if not self.gossip:
            return 0

        rounds = 0
        for _ in range(max_rounds):
            # Snapshot each node's outbound payload before applying, so the
            # round is synchronous and order-independent.
            outbound = {n.id: (self._select_peers(n), n.digest()) for n in self.nodes}
            changed = False
            for peers, beliefs in outbound.values():
                for peer in peers:
                    for belief in beliefs:
                        if peer.ingest(belief):
                            changed = True
            rounds += 1
            if not changed:
                break
        return rounds

    def coverage(self, subject: str) -> int:
        """How many nodes currently hold any belief about ``subject``."""

        return sum(1 for n in self.nodes if subject in n.fused)
