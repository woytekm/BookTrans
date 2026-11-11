# Data model

## Principles

- The document model is hierarchical but must retain fine-grained source evidence.
- OCR line objects are durable records. Paragraphs and semantic regions group/reference lines; they do not replace them.
- Geometry is explicit and coordinate systems are documented.
- Original OCR text and translated text are separate fields/records.
- Schema versions are mandatory for serialized JSON.

## Conceptual entities (target)

```text
Book
  pages[]
    Page
      geometry
      regions[]
        Region
          child_regions[]       # optional nested/associated structures
          lines[]               # OCR line objects, when text-bearing
            OCRLine
          paragraphs[]          # groupings/references over OCR lines
            Paragraph
          image_asset           # for image/drawing regions
      reading_order[]
      metadata

TranslationTransaction
  source_unit_id
  source_language
  target_language
  source_text
  translated_text
  context
  service/model metadata
  review status

StyleProfile
  role
  relative size and measured characteristics
  configured output font mapping
```

## Geometry conventions

- Bounding boxes use `[x1, y1, x2, y2]` (left, top, right, bottom), with right/bottom as exclusive-style edges where produced by image slicing; code should standardize this convention across future modules.
- Stage 2 region and OCR-line boxes are page-local pixel coordinates.
- Stage 1 page entries carry `x_offset`, which is the page's horizontal origin in the original spread. For the current untransformed split, global x can be calculated as `page_x + x_offset`.
- Original spread dimensions and page dimensions are in pixels. DPI metadata should be stored separately; pixel measurements must not be confused with PDF points.
- Future rotation/dewarp operations require an explicit transform rather than silently rewriting coordinate meaning.

## Current Stage 1 JSON (observed prototype schema)

Top-level fields:

- `schema_version`
- `source_file`
- `pages[]`

Page fields include:

- `page`, `side` (`L`/`R`), `x_offset`, `width`, `height`
- `regions[]`

Region fields vary by candidate type and currently include:

- `id`, `type`, `bbox`
- text-related values such as `text`, `ocr_confidence`, `ocr_line_height`
- image-related candidate values such as `ink_density`
- `bbox_global` for source-spread geometry in Stage 1 outputs

## Current Stage 2 JSON (schema version 0.2.1)

Top-level:

```json
{
  "schema_version": "0.2.1",
  "source_file": "...",
  "pages": []
}
```

Page entries include:

- `page`, `side`, `x_offset`, `width`, `height`
- `estimated_body_glyph_height`
- `line_count`
- `regions[]`

Semantic region entries currently include:

- `id`
- `type` (examples: `paragraph`, `heading`, `caption`, `footnote_or_footer`, `illustration`)
- `bbox` (page-local pixels)
- `text` (where applicable)
- `line_count`
- `median_relative_size`
- `lines[]`

OCR line entries currently include:

- `bbox`
- `text`
- `glyph_height`
- `confidence`
- `uppercase_ratio`
- `word_count`
- `type`
- `classification_reason`
- `classification_confidence`
- `relative_size`
- `style` (currently `unknown`)
- paragraph diagnostics such as `paragraph_break`, `paragraph_gap` and `paragraph_gap_threshold` when available

The precise fields are still prototype-level and may vary by region/line classification. A future schema revision should define strict typed models and validation (e.g. Pydantic or dataclasses + JSON Schema).

## Important distinction: region versus paragraph

A coarse text region is a spatial/layout container. A paragraph is a semantic text unit. One coarse region may contain several paragraphs, headings or other text roles. A drawing may contain labels that are text-bearing but associated with the drawing rather than ordinary prose.

## Proposed future identifiers

Use stable IDs rather than relying on array position:

- `book_id`
- `page_id`
- `region_id`
- `line_id`
- `paragraph_id`
- `asset_id`
- `translation_id`

IDs should remain stable when unrelated regions are inserted or sorted. This is not yet implemented in the prototype.

## Stage 3 classification and association (schema 0.3.0)

Stage 3 adds explicit object/context classification without modifying source
illustrations.

### Illustration objects

Illustrations are represented separately from semantic text regions:

- `object_id` — current-stage identifier such as `p1-ill-1`
- `type`: `illustration`
- `bbox` — page-local geometry
- `object_class`: `illustration`
- `classification_confidence`
- `classification_reason`
- `translation_status`: `exclude`

Stage 1 `image_or_drawing` candidates are accepted directly. A conservative
fallback also recognizes a very large Stage 1 region with unusually low OCR
confidence as an illustration candidate. This is intentionally a hypothesis
and remains subject to later human review.

### OCR-line association

Every OCR line receives:

- `content_context`: `page_text` or `illustration_text`
- `translation_status`: `translate` or `exclude`
- `associated_illustration_id` when associated with an illustration
- `association_reason` for illustration-associated lines

Association is currently geometric: a line is associated when its center lies
inside an illustration or when at least the configured fraction of its bbox
overlaps the illustration bbox. OCR text is not interpreted semantically.

### Region association

Semantic regions receive:

- `object_id`
- `content_context`
- `translation_status`
- `associated_illustration_ids` where applicable

A region containing both normal and illustration-associated OCR lines is marked
`mixed` with `translation_status: review`. No line-level information is removed.

### Current illustration policy

OCR detected inside illustrations is **kept as source evidence but excluded from
translation and page reconstruction**. The original illustration remains
unchanged. Editing labels inside drawings is explicitly deferred to a later,
advanced feature.
