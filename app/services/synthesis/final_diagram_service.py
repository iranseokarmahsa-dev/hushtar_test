"""Create diagrams exclusively from a validated final synthesis model."""

from __future__ import annotations

from typing import Any

from graphviz import Digraph


_COMPONENT_IDS = {
    "causal_conditions": "causal",
    "central_phenomenon": "phenomenon",
    "contextual_conditions": "contextual",
    "intervening_conditions": "intervening",
    "action_interaction_strategies": "strategies",
    "consequences": "consequences",
}

_COMPONENT_LABELS = {
    "causal_conditions": "شرایط علّی",
    "central_phenomenon": "پدیده محوری",
    "contextual_conditions": "شرایط زمینه‌ای",
    "intervening_conditions": "شرایط مداخله‌گر",
    "action_interaction_strategies": "راهبردهای کنش و تعامل",
    "consequences": "پیامدها",
}

_STRUCTURAL_LAYOUT = (
    ("causal_conditions", "central_phenomenon"),
    ("contextual_conditions", "action_interaction_strategies"),
    ("intervening_conditions", "action_interaction_strategies"),
    ("central_phenomenon", "action_interaction_strategies"),
    ("action_interaction_strategies", "consequences"),
)


def prepare_final_model_diagram(model: dict[str, Any]) -> dict[str, Any]:
    """Adapt model nodes and empirical edges without inventing relationships."""
    evidence_index = model.get("evidence_index", {})
    entity_nodes = []
    for item in model.get("nodes", []):
        evidence = [
            {
                "paragraph_id": evidence_index[ref_id].get("paragraph_id"),
                "exact_quote": evidence_index[ref_id].get("evidence_text", ""),
                "interpretation": evidence_index[ref_id].get("interpretation", ""),
                "source_title": evidence_index[ref_id].get("source_title", ""),
                "source_interview_id": evidence_index[ref_id].get("source_interview_id", ""),
                "source_project_id": evidence_index[ref_id].get("source_project_id", ""),
            }
            for ref_id in item.get("evidence_ref_ids", [])
            if ref_id in evidence_index
        ]
        entity_nodes.append({
            "id": item["node_id"],
            "node_type": item["node_type"],
            "component_key": item["component_key"],
            "title": item["title"],
            "description": item["definition"],
            "source_item_ids": item.get("source_item_ids", []),
            "source_project_ids": item.get("source_project_ids", []),
            "source_concept_ids": item.get("source_concept_ids", []),
            "source_subcategory_ids": item.get("source_subcategory_ids", []),
            "category_unit_ids": item.get("category_unit_ids", []),
            "theme_id": item.get("theme_id"),
            "selection_reason": item.get("selection_reason", ""),
            "evidence_ref_ids": item.get("evidence_ref_ids", []),
            "evidence": evidence,
        })

    nodes = []
    for component in model.get("components", []):
        component_key = component["component_key"]
        items = [
            {
                "id": node["id"],
                "title": node["title"],
                "description": node["description"],
                "node_type": node["node_type"],
            }
            for node in entity_nodes
            if node["component_key"] == component_key
        ]
        nodes.append({
            "id": _COMPONENT_IDS[component_key],
            "model_key": component_key,
            "label": component.get("title") or _COMPONENT_LABELS[component_key],
            "items": items,
            "category_ids": [],
            "concept_ids": [],
            "evidence": [],
        })

    layout_edges = [
        {"source": source, "target": target}
        for source, target in _STRUCTURAL_LAYOUT
        if source in _COMPONENT_IDS and target in _COMPONENT_IDS
    ]
    edges = [
        {
            "source": _COMPONENT_IDS[source],
            "target": _COMPONENT_IDS[target],
            "label": "پیوند ساختاری صرفاً برای چیدمان",
            "kind": "structural",
            "structural": True,
        }
        for source, target in _STRUCTURAL_LAYOUT
        if source in _COMPONENT_IDS and target in _COMPONENT_IDS
    ]

    entity_by_id = {node["id"]: node for node in entity_nodes}
    relationships = []
    for edge in model.get("edges", []):
        source, target = edge["source_node_id"], edge["target_node_id"]
        if edge["direction"] == "target_to_source":
            source, target = target, source
        relationship = {
            "id": edge["edge_id"],
            "source": source,
            "target": target,
            "source_title": entity_by_id.get(source, {}).get("title", source),
            "target_title": entity_by_id.get(target, {}).get("title", target),
            "label": edge["description"] or edge["relationship_type"],
            "kind": edge["relationship_type"],
            "structural": False,
            "relationship_id": edge["relationship_id"],
            "relationship_type": edge["relationship_type"],
            "direction": edge["direction"],
            "description": edge["description"],
            "reasoning": edge["reasoning"],
            "supporting_source_relationship_ids": edge["supporting_source_relationship_ids"],
            "evidence_ref_ids": edge["evidence_ref_ids"],
            "evidence": [
                {
                    "paragraph_id": evidence_index[ref_id].get("paragraph_id"),
                    "exact_quote": evidence_index[ref_id].get("evidence_text", ""),
                    "interpretation": evidence_index[ref_id].get("interpretation", ""),
                    "source_title": evidence_index[ref_id].get("source_title", ""),
                    "source_interview_id": evidence_index[ref_id].get("source_interview_id", ""),
                    "source_project_id": evidence_index[ref_id].get("source_project_id", ""),
                }
                for ref_id in edge.get("evidence_ref_ids", [])
                if ref_id in evidence_index
            ],
        }
        edges.append(relationship)
        relationships.append(relationship)

    return {
        "model_mode": "final",
        "model_version": model.get("model_version"),
        "nodes": nodes,
        "entity_nodes": entity_nodes,
        "edges": edges,
        "relationships": relationships,
        "layout_edges": layout_edges,
        "layout": {"rankdir": "RL", "fontname": "Vazirmatn"},
    }


def _build_final_model_graph(diagram: dict[str, Any], output_format: str) -> Digraph:
    if output_format not in {"png", "svg"}:
        raise ValueError("خروجی نمودار باید PNG یا SVG باشد.")

    dot = Digraph(comment="Validated Final Paradigm Model - Hooshyar", format=output_format)
    dot.attr(
        rankdir="RL",
        size="16,12",
        dpi="300",
        bgcolor="white",
        fontname="Vazirmatn",
        labelloc="b",
        label=(
            "روابط ساختاریِ خاکستری فقط چیدمان را نشان می‌دهند؛ "
            "روابط مستقیم پیوسته، تفسیری خط‌چین و فرضی نقطه‌چین‌اند."
        ),
    )
    dot.attr(
        "node",
        fontname="Vazirmatn",
        fontsize="10",
        margin="0.12",
        style="filled",
        fillcolor="white",
        color="#111111",
    )
    dot.attr("edge", fontname="Vazirmatn", arrowsize="0.7", color="#111111")
    component_keys = {node["model_key"] for node in diagram["nodes"]}
    entity_ids = {
        node["id"]: f"model_node_{index}"
        for index, node in enumerate(diagram["entity_nodes"])
    }

    for component in diagram["nodes"]:
        component_key = component["model_key"]
        with dot.subgraph(name=f"cluster_{component_key}") as cluster:
            cluster.attr(label=component["label"], fontname="Vazirmatn", fontsize="12")
            cluster.node(
                f"anchor_{component_key}",
                "",
                style="invis",
                width="0.01",
                height="0.01",
            )
            if not component["items"]:
                cluster.node(
                    f"empty_{component_key}",
                    "",
                    style="invis",
                    width="0.01",
                    height="0.01",
                )
            for entity in diagram["entity_nodes"]:
                if entity["component_key"] != component_key:
                    continue
                label = entity["title"]
                if entity["description"]:
                    label += f"\n{entity['description']}"
                cluster.node(
                    entity_ids[entity["id"]],
                    label,
                    shape="ellipse" if entity["node_type"] == "central_phenomenon" else "box",
                    tooltip=" · ".join(entity.get("evidence_ref_ids", [])),
                )

    for edge in diagram["layout_edges"]:
        source_key, target_key = edge["source"], edge["target"]
        if source_key in component_keys and target_key in component_keys:
            dot.edge(
                f"anchor_{source_key}",
                f"anchor_{target_key}",
                style="invis",
                weight="10",
                constraint="true",
            )
            dot.edge(
                f"anchor_{source_key}",
                f"anchor_{target_key}",
                label="ساختاری / چیدمان",
                style="dashed",
                color="#8a918c",
                fontcolor="#707873",
                arrowhead="none",
                constraint="false",
            )

    styles = {"direct": "solid", "interpretive": "dashed", "hypothetical": "dotted"}
    for edge in diagram["relationships"]:
        source = entity_ids.get(edge["source"])
        target = entity_ids.get(edge["target"])
        if not source or not target:
            continue
        attrs = {
            "label": f"{edge['relationship_type']}: {edge['description']}",
            "style": styles[edge["relationship_type"]],
            "tooltip": edge["description"],
            "constraint": "false",
        }
        if edge["direction"] == "bidirectional":
            attrs.update(dir="both", arrowtail="normal")
        dot.edge(source, target, **attrs)
    return dot


def render_final_model_diagram(
    diagram: dict[str, Any],
    output_format: str = "png",
) -> bytes:
    """Render a structured final-model diagram as SVG or PNG."""
    return _build_final_model_graph(diagram, output_format).pipe(format=output_format)
