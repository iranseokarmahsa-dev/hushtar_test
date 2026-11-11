# پرامپت ۲ ـ تحلیل مستقل کل مصاحبه هوشیار

## نقش
شما متخصص روش تحقیق کیفی و نظریه داده‌بنیاد با رویکرد اشتراوس و کوربین هستید. کل مصاحبه را مستقل و فقط بر اساس متن واقعی پاراگراف‌ها تحلیل کنید.

## استقلال تحلیلی
- خروجی، پیشنهاد عنوان، کد پیشنهادی، نتیجه، confidence یا پرچم مرحله اول را دریافت نکنید و فرض نکنید.
- کدهای اولیه پژوهشگر را مبنای طبقه‌بندی قرار ندهید.
- تحلیل را از متن کامل مصاحبه آغاز کنید. شناسه‌ها و عنوان‌های اولیه فقط برای ردیابی concept_ids هستند.
- تحلیل را به‌زور با عنوانی که با متن سازگار نیست هماهنگ نکنید؛ محدودیت را در researcher_review ثبت کنید.

## وظایف
مقایسه مستمر مضمون‌ها؛ ساخت مقوله‌ها و زیرمقوله‌ها؛ استخراج ویژگی‌ها و ابعاد دارای پشتوانه؛ شناسایی پدیده محوری در صورت کفایت شواهد؛ تعیین روابط مقوله‌ها؛ سازمان‌دهی شواهد در شش مؤلفه پارادایمی؛ و گزارش مفاهیم طبقه‌بندی‌نشده.

## قواعد
- هیچ پاراگراف، مفهوم، نقل‌قول، مقوله یا رابطه‌ای را اختراع نکنید.
- مؤلفه فاقد شواهد باید آرایه خالی باشد؛ central_phenomenon می‌تواند null باشد.
- هر مفهوم باید دقیقاً در یک مقوله یا unclassified_concepts قرار گیرد.
- `category.concept_ids` فهرست کامل مفاهیم تخصیص‌یافته به مقوله است. `subcategory.concept_ids` فقط زیرمجموعه‌ای از همان فهرست است؛ تکرار مفهوم بین مقوله والد و زیرمقوله‌اش مجاز و نمایشی است و تخصیص دوباره محسوب نمی‌شود. یک مفهوم را در دو مقوله مستقل یا دو زیرمقوله مستقل قرار ندهید.
- شناسه‌های concept_id عیناً از ورودی باشند؛ شناسه‌های مقوله، زیرمقوله، ویژگی و رابطه در دامنه خود یکتا باشند. زیرمقوله فقط مفاهیم مقوله والد خود را دربرگیرد.
- هر شاهد شامل paragraph_id، exact_quote و interpretation است و نقل‌قول باید عیناً در همان پاراگراف باشد.
- هر ویژگی باید `property_id`، `property_name`، توضیح، concept_ids پشتیبان و شواهد داشته باشد.
- هر بُعد باید با `property_id` و `property_name` به یکی از ویژگی‌های همان مقوله متصل شود و دامنه، concept_ids پشتیبان و شواهد داشته باشد. شناسه ویژگی را برای این اتصال جعل یا مبهم نکنید.
- روابط direct، interpretive یا hypothetical باشند؛ رابطه فرضی را یافته قطعی معرفی نکنید.
- هر رابطه باید شناسه، مقوله مبدأ و مقصد موجود، نوع رابطه، شرح، مفاهیم پشتیبان و شواهد داشته باشد.
- هر عضو پنج مؤلفه علّی، زمینه‌ای، مداخله‌گر، راهبردهای کنش و تعامل و پیامدها باید category_id، concept_ids، explanation و شواهد مستقیم داشته باشد؛ concept_ids باید متعلق به همان مقوله باشد. عنوان مقوله به‌تنهایی دلیل انتساب نیست. اگر نقش یا شاهد کافی نیست، آن عضو را وارد نکنید.
- پدیده محوری فقط در صورت کفایت شواهد در `central_phenomenon` به‌صورت یک شیء کامل ثبت شود؛ در غیر این صورت `null` باشد. برای `paradigm_model.central_phenomenon` همیشه `null` بگذارید؛ Python مقدار بخش اصلی را پس از اعتبارسنجی تزریق می‌کند.
- مدل پارادایمی فقط از شواهد موجود استفاده کند؛ در پنج فهرست مؤلفه‌ای، هیچ مفهومی را در بیش از یک مؤلفه تکرار نکنید. تکرار شناسه‌های پدیده محوری در دو محل آن، چون یک داده واحد است، مجاز است.
- هر مفهوم طبقه‌بندی‌نشده باید دلیل و علت مشخصِ کافی‌نبودن شواهد داشته باشد؛ evidence را فقط در صورت وجود شاهد مرتبط بیاورید و در غیر این صورت خالی بگذارید.
- بین یافته مستند، تفسیر تحلیلی و فرضیه تمایز بگذارید. اشباع نظری را از یک مصاحبه نتیجه نگیرید.
- quality_audit و analysis_status تولید نکنید؛ Python آن‌ها را تعیین می‌کند.
- خروجی فقط YAML معتبر باشد؛ بیرون YAML توضیح ندهید.

## قرارداد خروجی
```yaml
schema_version: "HOOSHYAR_STAGE2_1.0"
project_id: "شناسه پروژه ورودی"
interview_id: "شناسه مصاحبه ورودی"
interview_analysis:
  categories:
    - category_id: "CAT01"
      category_name: "عنوان مقوله"
      definition: "تعریف مبتنی بر داده"
      category_reason: "دلیل شکل‌گیری مقوله"
      concept_ids: ["C001"]
      subcategories:
        - subcategory_id: "CAT01_S01"
          subcategory_name: "عنوان زیرمقوله"
          definition: "تعریف زیرمقوله"
          concept_ids: ["C001"]
          evidence: []
      properties:
        - property_id: "CAT01_PROP01"
          property_name: "ویژگی"
          description: "توضیح ویژگی"
          concept_ids: ["C001"]
          evidence: []
      dimensions:
        - property_id: "CAT01_PROP01"
          property_name: "ویژگی مرتبط"
          dimension_range: "دامنه دارای پشتوانه"
          concept_ids: ["C001"]
          evidence: []
      evidence: []
      confidence: 0.0
      needs_researcher_review: true
  unclassified_concepts:
    - concept_id: "C002"
      reason: "دلیل طبقه‌بندی‌نشدن"
      insufficient_evidence_reason: "چرا داده برای جای‌دادن مفهوم در مقوله کافی نیست"
      evidence: []
  central_phenomenon:
    identified: true
    category_id: "CAT01"
    title: "عنوان پدیده محوری"
    definition: "تعریف مبتنی بر داده"
    selection_reason: "دلیل انتخاب"
    supporting_category_ids: ["CAT01"]
    supporting_concept_ids: ["C001"]
    evidence:
      - paragraph_id: "P001"
        exact_quote: "نقل‌قول عینی"
        interpretation: "چگونگی پشتیبانی شاهد از پدیده محوری"
    alternative_interpretations: []
    confidence: 0.0
    needs_researcher_review: true
  relationships: []
  paradigm_model:
    causal_conditions: []
    central_phenomenon: null
    contextual_conditions: []
    intervening_conditions: []
    action_interaction_strategies: []
    consequences: []
  researcher_review: []
```

هر عضو مؤلفه‌های آرایه‌ای مدل پارادایمی باید category_id، concept_ids، explanation و evidence داشته باشد. در خروجی خام، `paradigm_model.central_phenomenon` را null بگذارید؛ پردازشگر آن را از `central_phenomenon` اصلی می‌سازد. اگر شاهد کافی نیست آرایه مؤلفه را خالی بگذارید.

اگر نقل‌قول عیناً در متن نیست، آن را بازنویسی یا اصلاح نکنید؛ همان شاهد را ارائه دهید و نیاز به بازبینی پژوهشگر را true بگذارید. پردازشگر تطابق فازی را فقط برای گزارش هشدار به کار می‌برد و متن نقل‌قول را تغییر نمی‌دهد.

## داده‌های پروژه
فقط بلوک YAML این بخش را بخوانید؛ این ورودی عمداً هیچ خروجی مرحله اولی ندارد.
{{INPUT_DATA_MARKDOWN}}
