"""
load_data.py — Build the in-memory knowledge graph from JSON entity files.

Reads the seven entity files in /data, instantiates a NetworkX graph,
adds nodes for each entity, then wires up edges to express relationships
(offer WITH_PARTNER partner, member HAS_TIER tier, etc.).

The graph is rebuilt from scratch on every run. For a corpus of this size
that's fast enough to be invisible. In a production AVIOS system this would
be replaced with a graph database (Neo4j, Neptune) — same conceptual model,
different operational picture.

Members are deliberately NOT loaded into the same graph as content. They are
returned separately as a lookup keyed by member ID. A member is query
context, not corpus.

Note on Offers: the graph holds one node per offer (the entity). The vector
store holds three chunks per offer (the retrievable slices). Chunks live in
Chroma; entities live here. The chunk IDs reference back to the offer ID
via a dedicated metadata field — see build_index.py for the chunking logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import networkx as nx


# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _load_json(filename: str) -> list[dict[str, Any]]:
    """Read a JSON file from /data and return it as a list of records."""
    path = DATA_DIR / filename
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# -----------------------------------------------------------------------------
# Graph build
# -----------------------------------------------------------------------------

def build_graph() -> tuple[nx.MultiDiGraph, dict[str, dict[str, Any]]]:
    """
    Build the AVIOS content graph and return it alongside the member lookup.

    Returns:
        (graph, members) where:
          - graph is a NetworkX MultiDiGraph with nodes for markets, tiers,
            partners, rewards, transfer partners and offers, plus typed
            edges between them.
          - members is a dict keyed by member ID. Members are NOT graph
            nodes — they are query context, supplied at runtime by the CLI.
    """
    graph = nx.MultiDiGraph()

    # -------------------------------------------------------------------------
    # 1. Load each JSON file into a Python list
    # -------------------------------------------------------------------------
    markets = _load_json("markets.json")
    tiers = _load_json("tiers.json")
    members = _load_json("members.json")
    partners = _load_json("partners.json")
    rewards = _load_json("rewards.json")
    transfer_partners = _load_json("transfer_partners.json")
    offers = _load_json("offers.json")

    # -------------------------------------------------------------------------
    # 2. Add each entity to the graph as a node, tagged with its kind.
    #    The node ID is the entity ID. The full record travels as node data
    #    so anything that needs the original fields can read them straight
    #    off the node.
    # -------------------------------------------------------------------------
    for record in markets:
        graph.add_node(record["id"], kind="market", **record)

    for record in tiers:
        graph.add_node(record["id"], kind="tier", **record)

    for record in partners:
        graph.add_node(record["id"], kind="partner", **record)

    for record in rewards:
        graph.add_node(record["id"], kind="reward", **record)

    for record in transfer_partners:
        graph.add_node(record["id"], kind="transfer_partner", **record)

    # Offers as graph nodes — one per offer, NOT one per chunk. The
    # chunks live in Chroma; the graph holds the canonical entity.
    for record in offers:
        graph.add_node(record["id"], kind="offer", **record)

    # -------------------------------------------------------------------------
    # 3. Wire up the relationships.
    #    Each block below corresponds to one row in the relationship table
    #    in docs/AVIOS_RAG_Demo_EntityModel.md. The 'relation' attribute on
    #    each edge is the verb you'd read it as: "offer WITH_PARTNER partner".
    # -------------------------------------------------------------------------

    # Partner — IN_MARKET — Market
    for partner in partners:
        for market_code in partner["markets"]:
            market_id = f"market_{market_code}"
            graph.add_edge(partner["id"], market_id, relation="IN_MARKET")

    # Reward — WITH_PARTNER — Partner
    # Reward — ELIGIBLE_FOR_TIER — Tier
    # Reward — IN_MARKET — Market
    for reward in rewards:
        graph.add_edge(reward["id"], reward["partner_id"], relation="WITH_PARTNER")
        for tier_slug in reward["eligible_tiers"]:
            tier_id = f"tier_{tier_slug}"
            graph.add_edge(reward["id"], tier_id, relation="ELIGIBLE_FOR_TIER")
        for market_code in reward["markets"]:
            market_id = f"market_{market_code}"
            graph.add_edge(reward["id"], market_id, relation="IN_MARKET")

    # Transfer Partner — IN_MARKET — Market
    for tp in transfer_partners:
        for market_code in tp["markets"]:
            market_id = f"market_{market_code}"
            graph.add_edge(tp["id"], market_id, relation="IN_MARKET")

    # Offer — WITH_PARTNER — Partner
    # Offer — ELIGIBLE_FOR_TIER — Tier
    # Offer — IN_MARKET — Market
    for offer in offers:
        graph.add_edge(offer["id"], offer["partner_id"], relation="WITH_PARTNER")
        for tier_slug in offer["eligible_tiers"]:
            tier_id = f"tier_{tier_slug}"
            graph.add_edge(offer["id"], tier_id, relation="ELIGIBLE_FOR_TIER")
        for market_code in offer["markets"]:
            market_id = f"market_{market_code}"
            graph.add_edge(offer["id"], market_id, relation="IN_MARKET")

    # -------------------------------------------------------------------------
    # 4. Build the member lookup separately.
    #    Members are query context, not corpus. They do NOT enter the graph.
    # -------------------------------------------------------------------------
    member_lookup = {member["id"]: member for member in members}

    return graph, member_lookup


# -----------------------------------------------------------------------------
# Inspection helpers — used by inspect.py and useful in development
# -----------------------------------------------------------------------------

def summarise(graph: nx.MultiDiGraph, members: dict[str, dict[str, Any]]) -> str:
    """Build a human-readable summary of what got loaded."""
    lines = []
    lines.append(f"Total nodes: {graph.number_of_nodes()}")
    lines.append(f"Total edges: {graph.number_of_edges()}")
    lines.append("")

    # Count nodes by kind
    counts: dict[str, int] = {}
    for _, data in graph.nodes(data=True):
        kind = data.get("kind", "unknown")
        counts[kind] = counts.get(kind, 0) + 1

    lines.append("Nodes by kind:")
    for kind, count in sorted(counts.items()):
        lines.append(f"  {kind}: {count}")
    lines.append("")

    # Count edges by relation
    relations: dict[str, int] = {}
    for _, _, data in graph.edges(data=True):
        relation = data.get("relation", "unknown")
        relations[relation] = relations.get(relation, 0) + 1

    lines.append("Edges by relation:")
    for relation, count in sorted(relations.items()):
        lines.append(f"  {relation}: {count}")
    lines.append("")

    lines.append(f"Members (held separately, not in graph): {len(members)}")

    return "\n".join(lines)


def find_orphans(graph: nx.MultiDiGraph) -> list[str]:
    """
    Return any node IDs that have no edges in or out.
    Orphans usually mean a broken reference somewhere — worth flagging.
    """
    return [node for node in graph.nodes() if graph.degree(node) == 0]


# -----------------------------------------------------------------------------
# Allow running this file directly for a quick smoke test
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    graph, members = build_graph()
    print(summarise(graph, members))
    print()
    orphans = find_orphans(graph)
    if orphans:
        print(f"⚠️  {len(orphans)} orphan node(s):")
        for node_id in orphans:
            print(f"   {node_id}")
    else:
        print("✓ No orphan nodes — every entity is connected.")
