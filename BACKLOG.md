# CLM Backlog

Future features and ideas, not yet scheduled.

## Batch redaction + export

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
  `Redacted/` so originals and outputs never mix.

Open questions:
- Which categories are redacted by default vs. opt-in.
- Whether redaction is reviewed/approved before export, like other AI output.
