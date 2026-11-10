# FINAL PHENOMENON RESULT

## METADATA

- schema_version: 1.0
- run_id: RUN_ID
- stage_key: phenomenon_synthesis
- input_hash: INPUT_HASH
- source_phase_version: VERSION

## CENTRAL PHENOMENON

- phenomenon_id: AI_PHENOMENON_001
- title: عنوان پیشنهادی
- definition: تعریف مستند
- selection_reason: دلیل انتخاب بر پایه مفهوم و شواهد
- supporting_node_ids:
  - شناسه نود یا NONE
- supporting_source_item_ids:
  - شناسه Source Item یا NONE
- supporting_category_unit_ids:
  - شناسه Category Unit یا NONE
- supporting_theme_ids:
  - شناسه تم یا NONE
- supporting_source_phenomenon_ids:
  - شناسه پدیده محوری مستقل یا NONE
- evidence_ref_ids:
  - شناسه شاهد یا NONE
- confidence: 0.85
- needs_researcher_review: true

## ALTERNATIVE INTERPRETATIONS

##### ALTERNATIVE: AI_ALTERNATIVE_001

- alternative_id: AI_ALTERNATIVE_001
- title: عنوان تفسیر جایگزین
- definition: تعریف تفسیر جایگزین
- supporting_node_ids:
  - شناسه نود یا NONE
- supporting_source_item_ids:
  - شناسه Source Item یا NONE
- supporting_category_unit_ids:
  - شناسه Category Unit یا NONE
- supporting_theme_ids:
  - شناسه تم یا NONE
- supporting_source_phenomenon_ids:
  - شناسه پدیده محوری مستقل یا NONE
- evidence_ref_ids:
  - شناسه شاهد یا NONE
- strengths:
  - نقطه قوت
- limitations:
  - محدودیت
- selection_comparison: دلیل انتخاب‌نشدن به‌عنوان گزینه اصلی
- confidence: 0.7
- needs_researcher_review: true

## CONTRADICTIONS

##### CONTRADICTION: AI_PHENOMENON_CONTRADICTION_001

- contradiction_id: AI_PHENOMENON_CONTRADICTION_001
- source_phenomenon_ids:
  - شناسه پدیده محوری مستقل
- node_ids:
  - شناسه نود یا NONE
- source_item_ids:
  - شناسه Source Item یا NONE
- evidence_ref_ids:
  - شناسه شاهد
- description: شرح مورد ناسازگار
- reasoning: دلیل تشخیص ناسازگاری
- needs_researcher_review: true

## QUALITY AUDIT

- input_source_count: COUNT
- input_phenomenon_count: COUNT
- reviewed_source_phenomenon_ids:
  - شناسه هر پدیده محوری مستقل دقیقاً یک‌بار
- reviewed_node_ids:
  - شناسه هر نود ورودی دقیقاً یک‌بار
- input_theme_count: COUNT
- input_category_unit_count: COUNT
- supporting_reference_count: COUNT
- missing_supporting_references:
  - NONE
- unsupported_claims:
  - NONE
- contradictions: COUNT
- analysis_limitations:
  - NONE
- final_status: complete
