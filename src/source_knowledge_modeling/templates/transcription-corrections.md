# Original-source transcription corrections v1

`skm revise-transcription FOUNDATION --root ROOT --corrections PATCH --output-dir NEW_DIR`

Use this after an actual original-source check identifies a missing figure/table or transcription error. The packaged JSON Schema is `schemas/knowledge-transcription-corrections-v1.schema.json`. A patch binds exact source/snapshot hashes and identifies the actual reviewer, reviewer type, model (`unknown` if unavailable), check time and method. PDFs require `original_visual`. The command records that supplied assertion; it does not perform the visual check.

Each operation provides a complete source unit (`locator`, `kind`, `text`, normalized `bbox` or null, `visual_ref` or null, raw content, and continuation metadata). `replace` names an existing locator; `insert` names a new locator and an explicit `after_locator`. Preserve literal author wording/rows/columns and mark unresolved text; this input is not a semantic proposal. A locator can be touched once per patch. Missing predecessors, stale hashes and malformed rectangles are rejected before writing.

The command creates a new transcription, foundation, correction record and revision manifest. Unchanged units retain their stable IDs. Old files remain unchanged. New or corrected units carry only the supplied transcription verification; all semantic annotations remain solely in the old artifact and are not inherited as new acceptance. Use the normal prepare/independent assessment/review/publish lifecycle for the new snapshot. Retrying identical input/output is idempotent; changed bytes require another directory. The manifest records the changed scope and zero new semantic-review events.
