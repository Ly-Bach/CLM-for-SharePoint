# CLM SharePoint List — Column Reference (v2)

The query layer behind all analytics. `provision_lists.py` creates these
idempotently. Power BI and Power Automate read these typed columns directly;
the orchestrator upserts rows keyed by the **Key** column. Only rows with
`ReviewStatus = Approved` should feed dashboards and comparisons.

## Contract Index — one row per contract

| Column | Type | Notes |
|---|---|---|
| ContractID | Text | **Key**. GUID minted by the orchestrator |
| Title | Text | Human-readable name (lives here, not in the folder path) |
| Counterparty | Text | |
| ContractType | Choice | Grant, Vendor, Subrecipient, MOU, Lease, Other |
| Status | Choice | Draft, Active, Expired, Terminated |
| EffectiveDate | Date | |
| ExpirationDate | Date | Drives renewal/expiry dashboards |
| CurrentValue | Number | Computed: TotalValue + Σ amendment ValueChanges |
| FundingSource | Choice | Federal, State, Local, Private |
| ReviewStatus | Choice | Pending → Reviewed → Approved |

## Amendment Index — one row per amendment

| Column | Type | Notes |
|---|---|---|
| AmendmentID | Text | **Key**. GUID minted by the orchestrator |
| ContractID | Text | Foreign key → Contract Index |
| AmendmentNumber | Number (0 dp) | |
| AmendmentType | Choice | Extension, Budget Revision, Scope Change, Legal Correction, Other |
| EffectiveDate | Date | |
| ValueChange | Number | +/–; rolls up into Contract CurrentValue |
| ReviewStatus | Choice | Pending → Reviewed → Approved |

## Clause Map Index — one row per clause↔term mapping

| Column | Type | Notes |
|---|---|---|
| MapID | Text | **Key**. GUID minted by the orchestrator |
| ContractID | Text | Foreign key → Contract Index |
| ClauseID | Text | Standard clause taxonomy ID |
| TermID | Text | Subject-matter taxonomy ID |
| RelevanceScore | Number | 0.0–1.0 |
| ExtractionConfidence | Number | 0.0–1.0; below threshold → priority review |
| ReviewStatus | Choice | Pending → Reviewed → Approved |

## Notes

- **Choice columns** give clean filters/slicers in Power BI without lookups.
- **ContractID as foreign key** lets Power BI relate the three Lists for
  cross-contract clause-coverage heatmaps and UpSet plots.
- For datasets beyond a few thousand rows, **index the key + ReviewStatus
  columns** in List settings so filtered queries stay fast.
- The `StandardClause` and `SubjectMatterTerm` taxonomies are seed/reference
  data — keep them as JSON/CSV (or their own Lists later); the Clause Map Index
  is what gets queried per contract.
