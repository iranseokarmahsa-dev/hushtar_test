# FINAL RELATIONSHIP RESULT

## METADATA

- schema_version: 1.0
- run_id: RUN_ID
- stage_key: relationship_synthesis
- input_hash: INPUT_HASH
- source_phase_version: VERSION
- phenomenon_version: VERSION

## RELATIONSHIPS

##### RELATIONSHIP: AI_RELATIONSHIP_001

- relationship_id: AI_RELATIONSHIP_001
- source_node_id: شناسه نود مبدأ
- target_node_id: شناسه نود مقصد
- relationship_type: direct
- direction: source_to_target
- description: شرح رابطه
- reasoning: دلیل تحلیلی
- supporting_source_relationship_ids:
  - شناسه رابطه مستقل یا NONE
- supporting_source_phenomenon_ids:
  - شناسه پدیده مستقل یا NONE
- supporting_source_item_ids:
  - شناسه Source Item یا NONE
- supporting_category_unit_ids:
  - شناسه Category Unit یا NONE
- evidence_ref_ids:
  - شناسه شاهد
- confidence: 0.8
- needs_researcher_review: true

## UNLINKED NODES

##### UNLINKED: node_id

- node_id: شناسه نود
- component_key: causal_conditions
- reason: دلیل نداشتن رابطه قابل دفاع
- needs_researcher_review: true

## CONTRADICTIONS

##### CONTRADICTION: AI_RELATIONSHIP_CONTRADICTION_001

- contradiction_id: AI_RELATIONSHIP_CONTRADICTION_001
- source_node_id: شناسه نود مبدأ
- target_node_id: شناسه نود مقصد
- source_relationship_ids:
  - شناسه رابطه مستقل یا NONE
- source_item_ids:
  - شناسه Source Item یا NONE
- evidence_ref_ids:
  - شناسه شاهد
- description: شرح تناقض
- reasoning: دلیل تشخیص تناقض
- needs_researcher_review: true

## QUALITY AUDIT

- input_node_count: COUNT
- proposed_relationship_count: COUNT
- direct_relationship_count: COUNT
- interpretive_relationship_count: COUNT
- hypothetical_relationship_count: COUNT
- unlinked_node_count: COUNT
- missing_supporting_references:
  - NONE
- unsupported_relationships:
  - NONE
- duplicate_relationships:
  - NONE
- invalid_node_references:
  - NONE
- invalid_evidence_references:
  - NONE
- contradictions: COUNT
- analysis_limitations:
  - NONE
- final_status: complete
