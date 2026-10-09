# Instructional source profile 1.0.0

This profile records what a source says. It does not establish scientific truth, personal suitability, or the reader's cognitive state.

Read every informative source span in the declared scope. A sentence can contain several elements and an element can span blocks. Give each element and relation its own exact snapshot text anchor, then compare that wording with the original. A locator alone proves location, not that the whole block was interpreted.

Split a statement when a condition changes the action, an exception replaces it, an ordered step matters, or a reason supports only part of the statement. `conditions` points from a condition to the constrained action; `exception_to` from an exception to the default action; `replaces` from the alternative action to the default; `supports` from a reason to the supported claim or action; `precedes` from the earlier to the later action; `quantifies` from a parameter to its target object (action, assertion, example, or exception). A relation cites the wording that supports that connection. Do not manufacture a missing branch or reason.

Keep an author's example as `source_example`, including its population, context, approximation and hypothetical status. A number records its value or range, unit, target and reference basis; if one of these is unknown, record the uncertainty rather than silently generalizing it. Preserve negation and modal wording. A short summary is for reading, not the whole semantic record.

Screening statuses are `modeled` when the informative span is represented and checked, `partial` when some but not all information is represented, `unmodeled` when it has no semantic representation, `uncertain` when extraction or interpretation prevents a decision, and `not_applicable` for structural or blank units. Every status has a reason. A changed modeling rule conservatively reopens screening of all source units, including units with no old annotations.

Source transcription checks, semantic fidelity checks, scientific assessment and individual applicability are separate judgments. AI and user review identities stay distinct. Existing source checks may be reused only for the exact unchanged span, snapshot and checking method; a changed semantic judgment needs new review. Retain old revisions and explicit supersession mappings.
