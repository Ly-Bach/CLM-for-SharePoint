# Contract Lifecycle Management (CLM)

A contract lifecycle management system built on SharePoint, Python, and Power
Automate. SharePoint is the document store and source of truth, a SharePoint
List is the query layer, Python is the processing brain, and Azure OpenAI does
the per-document language work. Human review gates everything before it reaches
analytics.

## How it works

1. The orchestrator mints a `ContractID`, creates a standardized folder, and
   adds a Contract Index row (`ReviewStatus = Pending`).
2. A document is dropped into `01_Original/`. Python extracts text (PDF/DOCX,
   with a local Tesseract OCR fallback for scans).
3. Azure OpenAI extracts metadata, clauses, and subject-matter mappings using
   strict structured outputs. Every result carries an evidence span; the mapper
   abstains rather than guess.
4. Output is validated against the Pydantic schema. Invalid runs are recorded as
   failed and nothing is written downstream.
5. Validated JSON is saved to `04_AI_Outputs/`, and the SharePoint Lists are
   updated. A reviewer approves; only `Approved` records feed dashboards.

Amendments follow the same path, plus clause diffing against the approved
original and a roll-up of value changes into the contract's `CurrentValue`.

## Repository layout

```
6-20-26/
  clm design v1.docx        Original design
  clm design v2.docx        Revised design (current)
  code/
    schema.py               Pydantic storage models + requirement atoms
    ai_schemas.py           LLM extraction models (evidence spans, abstention)
    config.py               Settings (SharePoint + Azure OpenAI)
    auth.py                 Microsoft Graph app token (certificate or secret)
    sharepoint_io.py        Graph I/O: folders, files, List upserts, retry/backoff
    text_extract.py         PDF/DOCX text + Tesseract OCR fallback
    azure_client.py         Azure OpenAI strict structured outputs (extract/judge)
    ai_extract.py           Grounded extraction with derived confidence
    ai_dummy.py             Offline placeholder (no API calls)
    process_contract.py     Contract orchestrator
    process_amendment.py    Amendment orchestrator (diff + value roll-up)
    provision_lists.py      Creates the SharePoint Lists
    LISTS_REFERENCE.md      Column reference for the query-layer Lists
```

## SharePoint folder convention

```
/Contracts/{ContractID}/
    01_Original/
    02_Amendments/Amendment_01/ ...
    03_SupportingDocs/
    04_AI_Outputs/{Metadata, ClauseExtraction, Summaries, Visualizations}/
```

## Setup

Requires Python 3.10+.

```bash
pip install "pydantic>=2" pydantic-settings requests msal \
            openai azure-identity pymupdf python-docx pytesseract pillow
# OCR fallback also needs the Tesseract binary, installed locally/in-tenant:
#   Windows: install Tesseract OCR and add it to PATH
#   Debian/Ubuntu: apt-get install -y tesseract-ocr
```

Configure a `.env` file in `code/`:

```ini
# Microsoft Graph / SharePoint
TENANT_ID=...
CLIENT_ID=...
SITE_ID=...                # the single site granted via Sites.Selected
DRIVE_ID=...               # the document library's drive ID (not its name)
CERT_PATH=...              # preferred
CERT_THUMBPRINT=...
# CLIENT_SECRET=...        # local-dev fallback only

# Azure OpenAI (in-tenant; keeps contract text in your region)
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_DEPLOYMENT_EXTRACT=gpt-4.1
AZURE_OPENAI_DEPLOYMENT_JUDGE=o4-mini
# AZURE_OPENAI_API_KEY=... # local-dev fallback; prefer Entra ID

# Backend selector: true = offline ai_dummy (no API calls), false = real Azure.
USE_DUMMY_AI=false
```

Set `USE_DUMMY_AI=true` to run the pipeline against the offline placeholder
backend (no Azure deployment required) while still exercising the SharePoint and
schema-validation wiring. The orchestrators import `ai_provider`, which selects
`ai_dummy` or `ai_extract` from this single flag — no code edits needed to swap.

The Azure AD app should be granted `Sites.Selected` on the Contracts site only
(not tenant-wide `Sites.ReadWrite.All`).

## Usage

```bash
# One-time: create the query-layer Lists
python provision_lists.py

# Process a contract (mints a new ContractID)
python process_contract.py --filename contract.pdf

# Process an amendment against an existing contract
python process_amendment.py --contract-id <guid> --amendment-number 2 --filename amendment_02.pdf
```

## Design principles

- The orchestrator owns identity; the AI returns attributes only, never IDs.
- Strict JSON schema is generated from the Pydantic models, so AI output cannot
  break the schema. Validation failures are recorded, not silently written.
- Subject-matter mapping is grounded against a candidate term list and abstains
  when context is insufficient. Every extraction cites an evidence span.
- Confidence is derived from evidence coverage and abstention, not a number the
  model reports about itself.
- Risk is human-owned: the system surfaces and explains; it does not auto-score.
- The SharePoint List is the query layer; JSON files are the audit record.

## Status and roadmap

Phase 1 (local orchestrator, validated output, List + JSON writes) is built and
tested with the Azure layer mocked.

Next:
- Seed the `SubjectMatterTerm` candidate list (scoped by domain) for grounding.
- Wire requirement-atom extraction into the pipeline for the comparison views.
- Smoke-test the strict-schema builder against the live reasoning deployment.
- Phase 2 automation (Power Automate trigger to a secured Azure Function) and
  Phase 3 analytics (Power BI over the Contract Index).
