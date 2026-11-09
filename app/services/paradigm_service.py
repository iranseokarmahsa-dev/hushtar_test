"""Build a traceable paradigm diagram from the saved interview analysis."""

import hashlib
import json
from typing import Any

from graphviz import Digraph


COMPONENTS = (
	("causal_conditions", "شرایط علی", "causal"),
	("central_phenomenon", "مقوله یا پدیده محوری", "phenomenon"),
	("contextual_conditions", "زمینه", "contextual"),
	("intervening_conditions", "شرایط مداخله‌گر", "intervening"),
	("action_interaction_strategies", "راهبردها", "strategies"),
	("consequences", "پیامدها", "consequences"),
	("categories", "مقوله", "categories"),
)

PARADIGM_EDGES = (
	("causal_conditions", "central_phenomenon"),
	("contextual_conditions", "action_interaction_strategies"),
	("intervening_conditions", "action_interaction_strategies"),
	("central_phenomenon", "action_interaction_strategies"),
	("action_interaction_strategies", "consequences"),
)


def prepare_paradigm_diagram(project: dict[str, Any]) -> dict[str, Any]:
	"""Resolve component references and evidence without running AI analysis."""
	analysis = project.get("analysis") or {}
	interview = analysis.get("interview_analysis", analysis) if analysis else project
	model = interview.get("paradigm_model") or {}
	edits = (project.get("researcher_edits") or {}).get("interview_analysis", {})
	category_edits = edits.get("categories", {})
	categories = {
		item["category_id"]: item
		for item in interview.get("categories", [])
		if item.get("category_id")
	}

	nodes = []
	for model_key, label, node_id in COMPONENTS:
		if model_key == "central_phenomenon":
			node = _phenomenon_node(
				interview, model, label, category_edits, categories,
				edits.get("central_phenomenon", {}),
			)
		elif model_key == "categories":
			node = _component_node(
				list(categories.values()), model_key, label, node_id,
				category_edits, categories,
			)
		else:
			value = model.get(model_key)
			if value is None:
				legacy_key = {
					"causal_conditions": "causal",
					"contextual_conditions": "context",
					"intervening_conditions": "intervening",
					"action_interaction_strategies": "strategies",
				}.get(model_key, model_key)
				value = interview.get(model_key)
				if value is None:
					value = interview.get(legacy_key, [])
			node = _component_node(
				value, model_key, label, node_id, category_edits, categories
			)
		nodes.append(node)

	edges = []
	for source, target in PARADIGM_EDGES:
		source_node = _node_by_model_key(nodes, source)
		target_node = _node_by_model_key(nodes, target)
		edges.append({
			"source": source_node["id"],
			"target": target_node["id"],
			"label": "پیوند ساختاری مدل پارادایمی",
			"kind": "structural",
		})

	relationships = []
	relationship_edits = edits.get("relationships", {})
	for relation in interview.get("relationships", []):
		row = _prepare_relationship(
			relation, relationship_edits.get(relation.get("relationship_id"), {})
		)
		relationships.append(row)

	return {
		"nodes": nodes,
		"edges": edges,
		"relationships": relationships,
		"layout": {"rankdir": "RL", "fontname": "Vazirmatn"},
		"source_hash": paradigm_source_hash(project),
	}


def _build_paradigm_graph(diagram: dict[str, Any], output_format: str) -> Digraph:
	if output_format not in {"png", "svg"}:
		raise ValueError("خروجی نمودار باید PNG یا SVG باشد.")

	dot = Digraph(
		comment="Strauss & Corbin Paradigm Model - Hooshyar",
		format=output_format,
	)
	dot.attr(
		rankdir="RL",
		size="16,12",
		dpi="300",
		bgcolor="white",
		fontname="Vazirmatn",
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
	nodes = {node["id"]: node for node in diagram.get("nodes", [])}
	categories = nodes.get("categories", {}).get("items", [])
	for node_id, node in nodes.items():
		if node_id == "categories":
			for index, item in enumerate(categories):
				category_id = f"category_{index}"
				dot.node(
					category_id,
					item["title"],
					shape="ellipse",
					width="1.3",
					height="0.7",
					fontsize="9",
				)
			continue

		lines = []
		for item in node.get("items", []):
			lines.append(item["title"])
			if item.get("description"):
				lines.append(item["description"])
		body = "\n".join(lines) or "در تحلیل ثبت نشده"
		dot.node(
			node_id,
			f"{node['label']}\n\n{body}",
			shape="box",
		)

	for index, _item in enumerate(categories):
		dot.edge(f"category_{index}", "causal", style="invis", weight="0")

	with dot.subgraph() as phenomenon_column:
		phenomenon_column.attr(rank="same")
		for node_id in ("contextual", "phenomenon", "intervening"):
			phenomenon_column.node(node_id)
	dot.edge("contextual", "phenomenon", style="invis", weight="10")
	dot.edge("phenomenon", "intervening", style="invis", weight="10")

	seen_edges: set[tuple[str, str]] = set()
	for edge in diagram.get("edges", []):
		source, target = edge["source"], edge["target"]
		if source not in nodes or target not in nodes:
			continue
		pair = (source, target)
		if pair in seen_edges:
			continue
		seen_edges.add(pair)
		dot.edge(source, target)

	return dot


def render_paradigm_diagram(diagram: dict[str, Any], output_format: str = "png") -> bytes:
	"""Render the structured paradigm graph as a PNG or SVG image."""
	return _build_paradigm_graph(diagram, output_format).pipe(format=output_format)


def paradigm_source_hash(project: dict[str, Any]) -> str:
	"""Fingerprint only the saved stage-two data used by this diagram."""
	analysis = project.get("analysis") or {}
	interview = analysis.get("interview_analysis", analysis)
	edits = (project.get("researcher_edits") or {}).get("interview_analysis", {})
	source = {
		"project_id": project.get("project_id"),
		"interview_id": analysis.get("interview_id", project.get("project_id")),
		"interview_analysis": interview,
		"interview_analysis_edits": edits,
	}
	encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
	return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _component_node(
	value: Any,
	model_key: str,
	label: str,
	node_id: str,
	category_edits: dict[str, Any],
	categories: dict[str, dict[str, Any]],
) -> dict[str, Any]:
	raw_items = value if isinstance(value, list) else ([value] if value else [])
	items = []
	for raw_item in raw_items:
		item = raw_item if isinstance(raw_item, dict) else {"title": str(raw_item)}
		category_ids = _category_ids(item)
		concept_ids = _unique(item.get("concept_ids", []))
		evidence = list(item.get("evidence", []))
		resolved = []
		for category_id in category_ids:
			category = categories.get(category_id)
			if category is None:
				continue
			edit = category_edits.get(category_id, {})
			resolved.append({
				"id": category_id,
				"title": edit.get("category_name") or category.get("category_name") or category_id,
				"definition": edit.get("definition") or category.get("definition", ""),
			})
			concept_ids = _unique(concept_ids + category.get("concept_ids", []))
			evidence.extend(category.get("evidence", []))

		if resolved:
			title = "، ".join(category["title"] for category in resolved)
			description = item.get("explanation", "")
		else:
			title = (
				item.get("title") or item.get("category_name") or item.get("name")
				or item.get("explanation") or "مقوله مرتبط"
			)
			description = item.get("explanation") or item.get("definition", "")
			if not category_ids and not concept_ids and not description and not item.get("title"):
				continue
		items.append({
			"title": title,
			"description": description,
			"categories": resolved,
			"category_ids": category_ids,
			"concept_ids": concept_ids,
			"evidence": _unique_evidence(evidence),
		})
	return _node(node_id, model_key, label, items)


def _phenomenon_node(
	interview: dict[str, Any],
	model: dict[str, Any],
	label: str,
	category_edits: dict[str, Any],
	categories: dict[str, dict[str, Any]],
	phenomenon_edit: dict[str, Any],
) -> dict[str, Any]:
	phenomenon = interview.get("central_phenomenon") or {}
	if not phenomenon and interview.get("central"):
		phenomenon = {"title": interview["central"]}
	model_item = model.get("central_phenomenon") or {}
	if not isinstance(phenomenon, dict):
		phenomenon = {"title": str(phenomenon)}
	if not isinstance(model_item, dict):
		model_item = {"title": str(model_item)} if model_item else {}
	category_ids = _unique(
		([phenomenon.get("category_id")] if phenomenon.get("category_id") else [])
		+ phenomenon.get("supporting_category_ids", [])
		+ _category_ids(model_item)
	)
	concept_ids = _unique(
		phenomenon.get("supporting_concept_ids", []) + model_item.get("concept_ids", [])
	)
	resolved = []
	evidence = list(phenomenon.get("evidence", [])) + list(model_item.get("evidence", []))
	for category_id in category_ids:
		category = categories.get(category_id)
		if category is None:
			continue
		edit = category_edits.get(category_id, {})
		resolved.append({
			"id": category_id,
			"title": edit.get("category_name") or category.get("category_name") or category_id,
			"definition": edit.get("definition") or category.get("definition", ""),
		})
		concept_ids = _unique(concept_ids + category.get("concept_ids", []))
		evidence.extend(category.get("evidence", []))

	title = (
		phenomenon.get("title")
		or model_item.get("title")
		or ("، ".join(item["title"] for item in resolved) if resolved else "")
	)
	definition = (
		phenomenon.get("definition")
		or model_item.get("explanation")
		or model_item.get("definition", "")
	)
	if phenomenon_edit.get("title"):
		title = phenomenon_edit["title"]
	if phenomenon_edit.get("definition"):
		definition = phenomenon_edit["definition"]
	items = []
	if title or definition or category_ids or concept_ids or evidence:
		items.append({
			"title": title or "پدیده محوری مشخص نشده است",
			"description": definition,
			"categories": resolved,
			"category_ids": category_ids,
			"concept_ids": concept_ids,
			"evidence": _unique_evidence(evidence),
		})
	node = _node("phenomenon", "central_phenomenon", label, items)
	node["relationship_category_ids"] = _unique(
		([phenomenon.get("category_id")] if phenomenon.get("category_id") else [])
		+ ([model_item.get("category_id")] if model_item.get("category_id") else [])
	)
	return node


def _node(node_id: str, model_key: str, label: str, items: list[dict[str, Any]]) -> dict[str, Any]:
	return {
		"id": node_id,
		"model_key": model_key,
		"label": label,
		"items": items,
		"category_ids": _unique(
			[category_id for item in items for category_id in item["category_ids"]]
		),
		"concept_ids": _unique(
			[concept_id for item in items for concept_id in item["concept_ids"]]
		),
		"evidence": _unique_evidence([entry for item in items for entry in item["evidence"]]),
	}


def _prepare_relationship(
	relation: dict[str, Any], edit: dict[str, Any] | None = None
) -> dict[str, Any]:
	edit = edit or {}
	evidence = relation.get("evidence", [])
	documented = (
		relation.get("relationship_type") != "hypothetical"
		and not relation.get("needs_researcher_review", False)
		and bool(evidence)
	)
	return {
		"relationship_id": relation.get("relationship_id", ""),
		"source_category_id": relation.get("source_category_id", ""),
		"target_category_id": relation.get("target_category_id", ""),
		"relationship_type": relation.get("relationship_type", ""),
		"description": edit.get("description") or relation.get("description", ""),
		"supporting_concept_ids": _unique(relation.get("supporting_concept_ids", [])),
		"evidence": evidence,
		"documented": documented,
		"needs_researcher_review": relation.get("needs_researcher_review", False),
	}


def _node_by_model_key(nodes: list[dict[str, Any]], model_key: str) -> dict[str, Any]:
	return next(node for node in nodes if node["model_key"] == model_key)


def _category_ids(item: dict[str, Any]) -> list[str]:
	singular = item.get("category_id")
	multiple = item.get("category_ids", [])
	return _unique(([singular] if isinstance(singular, str) and singular else []) + (
		multiple if isinstance(multiple, list) else []
	))


def _unique(values: Any) -> list[str]:
	if not isinstance(values, list):
		return []
	return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))


def _unique_evidence(evidence: list[Any]) -> list[dict[str, Any]]:
	unique = []
	seen = set()
	for item in evidence:
		if not isinstance(item, dict):
			continue
		key = (item.get("paragraph_id"), item.get("exact_quote"), item.get("interpretation"))
		if key in seen:
			continue
		seen.add(key)
		unique.append(item)
	return unique
