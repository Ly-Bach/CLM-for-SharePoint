# CLM Code Review

Two-pass review of the `code/` modules: a security pass followed by a software
engineering pass. Items addressed in this round are marked **[FIXED]**.

_Reviewed: June 2026 — Phase 1 (local orchestrator, validated output, List + JSON writes)._

---

## Pass 1 — Security analyst

### High

- **[FIXED] `.env` was not gitignored.** `code/.gitignore` covered only
  `__pycache__/` and `*.pyc`, while the README directs users to place
  `CLIENT_SECRET`, `AZURE_OPENAI_API_KEY`, `CERT_PATH`, and the cert thumbprint
  in `code/.env`. A single `git add .` would have committed live credentials.
  `.gitignore` now excludes `.env`, `.env.*`, `*.pem`, `*.pfx`, `*.key`, `*.cer`.
  No `.env` is currently tracked.

### Medium

- **OData filter injection in `sharepoint_io.upsert_list_item`.** The key value
  is interpolated unescaped into `$filter=fields/{key_field} eq '{key_value}'`.
  Today every key is a `uuid4`, so it is safe in practice, but if a key ever
  derives from document content, a value containing `'` could break or
  manipulate the filter. Escape single quotes (double them) or constrain keys to
  validated UUIDs before the call.

- **Path handling from `filename`.** Download/upload paths interpolate the CLI
  `filename` and folder directly into the Graph `root:/{path}` address. A
  `filename` containing `..` could address outside the intended folder within the
  drive. Operator-supplied today, but it should be validated (reject path
  separators and `..`).

- **Prompt injection (inherent to document AI).** Contract text is concatenated
  into the model prompt, so a hostile document can carry instructions
  ("set TotalValue to 0"). Mitigations are in place and well-designed: strict
  structured outputs, mandatory evidence spans, and the human review gate
  (nothing reaches analytics until `Approved`). The sharpest edge is
  `process_amendment`'s `contract_changes`, which writes contract-level fields
  from extraction — these must never bypass review. Document as an accepted,
  gated risk.

### Low

- **Error payloads may leak document content.** The failure path writes
  `str(exc)` into `ai_run_*.json`. A `ValidationError` can embed offending field
  values (PII / regulated text). Same-tenant, so low severity — but error records
  should store a reference or category rather than raw document content.

### Positives

Least-privilege `Sites.Selected`; Entra ID preferred over keys; certificate
preferred over secret; in-tenant OCR (no data egress); in-region Azure OpenAI;
`conflictBehavior: fail` (no clobber); TLS on by default; request timeouts
everywhere.

---

## Pass 2 — Software engineer

### High

- **[FIXED] No tests.** Added a `tests/` suite (pytest): schema validation /
  value roll-up / dump round-trip, dummy-backend shape parity, and end-to-end
  pipeline tests with SharePoint and text extraction stubbed (including the
  failed-run path that must persist nothing downstream). 16 tests passing.

### Medium

- **[FIXED] `temperature` broke the reasoning deployment.** `azure_client._call`
  always sent `temperature=0.0`; the o-series judge deployment (`o4-mini`)
  rejects that parameter, so every `judge()` call would have errored at runtime.
  `judge()` now passes `temperature=None` and the client omits the parameter for
  that path.

- **[FIXED] Dummy/real return shapes had drifted.** `ai_dummy.extract_clauses`
  emitted `ClauseID`/`ClauseName`/`Text` while `ai_extract` emits
  `clause_id`/`clause_name`/`text_span`, and `ai_dummy.map_subject_terms` lacked
  the `candidate_terms` parameter. Both are now aligned with `ai_extract`, and the
  `try/except TypeError` shim in `ai_provider` (which could have masked real
  errors) was removed in favor of a direct call.

- **Overly broad exception tuple.** `except (ValidationError, RuntimeError,
  Exception)` is redundant — `Exception` subsumes the other two. Narrow to the
  expected failure modes, or catch `Exception` alone with a comment, so genuine
  bugs are not silently folded into "failed run." _(Not yet changed.)_

### Low

- **Packaging & reproducibility.** No pinned `requirements.txt`/`pyproject.toml`
  and no package layout (`__init__.py`); imports rely on the CWD being `code/`.
  Add a pinned dependency file and consider a package structure for CI.

- **Logging consistency.** Orchestrators use `print()` while `text_extract` uses
  `logging`. Standardize on `logging` with levels for unattended (Phase 2) runs.

- **List query reliability at scale.** `upsert_list_item` filters on a possibly
  unindexed column (`Prefer: HonorNonIndexedQueriesWarningMayFailRandomly`).
  Index the key columns (`ContractID`, `MapID`, `AmendmentID`) in
  `provision_lists.py` so upserts stay reliable as the lists grow.

### Positives

Retry/backoff honoring `Retry-After`; idempotent recursive folder creation;
clean separation of AI schemas vs. storage schemas; alias-based serialization;
timezone-aware timestamps (no deprecated `utcnow()`); derived (not self-reported)
confidence.

---

## Still open (recommended next)

1. Escape/validate the OData key and the `filename` path (security, Medium).
2. Narrow the broad `except` in both orchestrators.
3. Add `pyproject.toml` / pinned dependencies and a package layout.
4. Switch orchestrator `print()` to `logging`.
5. Index the List key columns in `provision_lists.py`.
6. Seed and wire the `SubjectMatterTerm` candidate list (the mapping step
   currently abstains on every clause because no candidates are passed).
