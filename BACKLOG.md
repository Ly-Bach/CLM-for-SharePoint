## CLM Backlog

Prioritized work items for the CLM pipeline, organized by phase. Items at the
top unblock the Phase 1 end-to-end smoke test; items lower down deliver the
automation and analytics layers described in the v2 design doc. The final
section is a longer-term idea, not yet scheduled.

### Phase 1 - Finish the local orchestrator (validated output + List writes)

1. Finish the contract orchestrator body in process_contract.py: download
   original, extract text, call ai.extract_metadata / extract_clauses /
   map_subject_terms, build Contract and ClauseTermMap models, write JSON to
   04_AI_Outputs/Metadata and /ClauseExtraction, upsert the Contract Index and
   Clause Map Index rows, write the AIExtractionRun, and flag below-threshold
   confidence for priority review.

2. Finish the amendment orchestrator body in process_amendment.py: load the
   approved original, extract amendment metadata + modified clauses, apply
   contract_changes to the Contract model, recompute CurrentValue, write the
   MetadataDiff and the updated contract JSON, upsert the Amendment Index row,
   and update the Contract Index CurrentValue.

3. Finish text_extract.extract_text() routing: PDF text-layer first, fall back
   to OCR when under _MIN_TEXT_CHARS, route DOCX to _docx_text, route image
   extensions to _ocr_image, and implement _ocr_pdf by rendering each page at
   _OCR_DPI and feeding to Tesseract.

4. Finish azure_client._get_client(): build the AzureOpenAI client with a
   bearer token provider via azure-identity when no key is set, fall back to
   api_key for local dev.

5. Finish sharepoint_io.ensure_folder() (recurse to parents on 404, then POST
   a new folder) and upsert_list_item() (POST when no match, PATCH fields/{id}
   when matched).

6. Finish provision_lists.ensure_list(): add a _add_column helper and loop the
   column definitions, skipping any name already in _existing_columns.

7. Restore the missing Contract fields and the current_value computed field in
   schema.py, and make sure the alias names match what provision_lists.py and
   LISTS_REFERENCE.md expect.

8. Fix the empty `evidenced` sets and `contract_changes` dict in ai_extract.py
   so the derived confidence and the contract-roll-up actually work.

### Phase 2 - Real AI + human review

9. Seed the SubjectMatterTerm candidate list (scoped by domain: HUD CoC/HOPWA,
   SAMHSA, CalAIM, DBH, HCAI) so the grounded mapper has something to map
   against. Without this, every mapping abstains.

10. Wire requirement-atom extraction into the pipeline. The schema exists
    (RequirementAtom, RequirementRelationship) but nothing extracts or stores
    them, so the planned clause-comparison views have no data.

11. Add document chunking by clause/section before sending to Azure OpenAI,
    with reassembly of extractions, so contracts that exceed the model context
    window do not fail.

12. Build the review UI: a Power Apps form or a JSON-formatted SharePoint List
    view that lets a reviewer see the evidence spans and approve or correct
    extractions before ReviewStatus flips to Approved.

### Phase 3 - Automation

13. Phase 2 automation: a Power Automate flow on file-created in
    /Contracts/.../01_Original/ that calls a secured Azure Function which runs
    the orchestrator. The auth and retry plumbing already supports this; the
    Function wrapper itself is not in the code.

### Phase 4 - Analytics

14. Power BI model over the three Lists (Contract Index, Amendment Index,
    Clause Map Index), joined on ContractID, filtered to ReviewStatus =
    Approved. Deliver the heatmaps, UpSet plots, and risk dashboards described
    in the design doc.

### Batch redaction + export

Copy all documents from a source folder, redact sensitive content, and write the
redacted copies to a destination folder of the user's choice. Originals are never
modified.
Why it's non-trivial:
- Redaction is compliance-sensitive (federal/HUD/SAMHSA data), so detection must
  run in-tenant and a human should verify before anything is shared externally,
  consistent with the project's review-gate principle.
- "Redact" means removing content from the rendered document (true redaction),
  not just hiding text that can be copied back out.
Likely building blocks:
- Reuse text_extract.py (incl. OCR) to read each document.
- Detect sensitive spans with an in-tenant service (Azure AI Language PII
  detection or Presidio) plus contract-specific patterns (parties, $ amounts,
  account numbers, addresses).
- Apply redactions at the document level (PyMuPDF for PDF, docx for Word) and
  preserve folder structure in the destination.
- Write an audit log per file: what categories were redacted and how many spans.
- Let the user pick the destination folder; default to a sibling like
  Redacted/ so originals and outputs never mix.
Open questions:
- Which categories are redacted by default vs. opt-in.
- Whether redaction is reviewed/approved before export, like other AI output.
