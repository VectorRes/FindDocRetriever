# FinDoc_Retriever_User_Stories

# FinDoc Retriever — User Stories@

---

## FRONTEND

### ID-HU-FE-001

- Name: Natural Language Query Interface with Cited Answers
- Description: As a financial analyst, I want to ask questions about financial documents in plain language and receive an answer with a citation to its exact source, so that I can get reliable information quickly without manually searching through long reports or Excel models.
- Acceptance criteria:
    - Given I am logged into FinDoc Retriever, when I type a financial question and submit it, then the system returns an answer along with a citation (document name, page/sheet/cell reference) linked to the original source.
    - Given the system cannot find a reliable answer, when I submit a question, then the system explicitly states it could not find a confident answer instead of guessing.
    - Given I ask a follow-up question, when I submit it, then the system keeps the context of the previous question and answer.
- Priority: High
- Story points:
- Subtasks:
    - Design and build the chat-style question input component.
    - Build the answer display component with inline citation badges/links.
    - Implement “no confident answer” empty-state messaging.
    - Implement conversation context/history panel.
    - Connect the UI to the backend Q&A API.
    - Handle loading, timeout, and error states.

**Main Use Case:**

```gherkin
Feature: Answering a financial question with a citation
  As a financial analyst
  I want to ask a question in natural language
  So that I get an answer traceable to its original source

  Scenario: Successful answer with citation
    Given the corpus contains the Q3 income statement, approved and stored in the system
    When the analyst asks "What was the EBITDA margin in Q3?"
    Then the system displays the calculated EBITDA margin
    And the system shows a citation pointing to the exact document, page, and line item used
    And the analyst can click the citation to open the original source at that location
```

**Alternate Case 1 — No Confident Answer Found:**

```gherkin
Feature: Handling unanswerable questions
  As a financial analyst
  I want to be told when the system is not sure of an answer
  So that I do not rely on an unsupported figure

  Scenario: Question with insufficient source data
    Given the corpus does not contain information about "covenant thresholds for the 2024 credit facility"
    When the analyst asks about that topic
    Then the system responds that it could not find a reliable source
    And the system suggests escalating the question to the relevant subject matter expert
    And no fabricated figure is shown
```

**Alternate Case 2 — Ambiguous Question Requiring Clarification:**

```gherkin
Feature: Clarifying ambiguous questions
  As a financial analyst
  I want the system to ask for clarification when a question is ambiguous
  So that I get the answer for the document/period I actually need

  Scenario: Question missing a required filter
    Given the corpus contains income statements for multiple subsidiaries and periods
    When the analyst asks "What was revenue?" without specifying entity or period
    Then the system asks the analyst to specify the entity and/or period
    And the system does not return a default answer without confirmation
```

---

### ID-HU-FE-002

- Name: Source Verification Panel
- Description: As a financial analyst, I want to open the exact original document location behind any answer, so that I can verify every figure against its source before using it in a report or decision.
- Acceptance criteria:
    - Given an answer with a citation, when I click the citation, then the system opens a side-by-side viewer showing the original document (PDF page or Excel sheet/cell) highlighted at the referenced location.
    - Given the source document is an Excel file, when I open the citation, then the system displays the specific sheet and cell(s), including the formula if applicable.
    - Given the source document has been superseded by a newer approved version, when I open the citation, then the system warns me that a newer version exists and offers to show it.
- Priority: High
- Story points:
- Subtasks:
    - Build side-by-side viewer layout (answer + source).
    - Implement PDF page-highlight rendering.
    - Implement Excel sheet/cell highlight rendering, including formula display.
    - Implement “newer version available” warning banner.
    - Add “open original file” download/external-open action.

**Main Use Case:**

```gherkin
Feature: Verifying an answer against its original source
  As a financial analyst
  I want to see the exact location a figure came from
  So that I can confirm it before using it

  Scenario: Verifying a figure sourced from an Excel model
    Given an answer cites "FY24_CashFlow_Model.xlsx, sheet 'FCF', cell C42"
    When the analyst clicks the citation
    Then the system opens the Excel viewer showing sheet "FCF"
    And cell C42 is highlighted
    And the underlying formula for C42 is displayed
```

**Alternate Case 1 — Citation Points to a Superseded Version:**

```gherkin
Feature: Warning about outdated source versions
  As a financial analyst
  I want to know if I'm viewing an outdated file
  So that I don't base a decision on stale data

  Scenario: Newer approved version exists
    Given the cited file "Q2_BudgetVsActual_v3.xlsx" has been replaced by "v4" in the repository
    When the analyst opens the citation for the v3 file
    Then the system displays a warning that a newer approved version exists
    And offers a button to switch to the current version
```

**Alternate Case 2 — Source Document Access Restricted:**

```gherkin
Feature: Restricting access to confidential source citations
  As a financial analyst without clearance for a restricted document
  I want to be informed I lack access
  So that confidentiality boundaries are respected

  Scenario: Citation references a restricted compensation report
    Given the cited document is tagged as "Restricted: Compensation"
    When an analyst without the required permission clicks the citation
    Then the system denies access to the source view
    And displays a message indicating the document requires elevated permissions
```

---

### ID-HU-FE-003

- Name: Cross-Document Comparison View
- Description: As a financial analyst, I want to compare the same section or figure across multiple documents or periods side by side, so that I can spot inconsistencies without manually reconciling files.
- Acceptance criteria:
    - Given I select two or more documents, when I choose a section or metric to compare, then the system displays the values side by side with the source citation for each.
    - Given the compared values do not match, when the comparison is displayed, then the system highlights the discrepancy visually.
    - Given I compare figures across periods, when the comparison is displayed, then the system shows the variance (absolute and percentage).
- Priority: High
- Story points:
- Subtasks:
    - Design multi-document/multi-period selector UI.
    - Build side-by-side comparison table component.
    - Implement discrepancy highlighting logic in the UI.
    - Implement variance (absolute/%) column.
    - Add export-to-Excel/PDF option for the comparison table.

**Main Use Case:**

```gherkin
Feature: Comparing a metric across documents
  As a financial analyst
  I want to compare the same line item across two documents
  So that I can validate consistency

  Scenario: Comparing net income across the income statement and cash flow statement
    Given the Q1 income statement and Q1 cash flow statement are both in the system
    When the analyst compares "net income" between both documents
    Then the system displays both values side by side with their citations
    And indicates whether the values reconcile
```

**Alternate Case 1 — Discrepancy Detected Between Documents:**

```gherkin
Feature: Highlighting inconsistent figures
  As a financial analyst
  I want discrepancies flagged automatically
  So that I investigate before relying on the numbers

  Scenario: Mismatched revenue figures between BI dashboard export and ERP report
    Given the BI export reports revenue of 4,820,000 and the ERP report reports 4,795,000 for the same period
    When the analyst runs the comparison
    Then the system flags the discrepancy in red
    And suggests escalating to Accounting/Consolidation for reconciliation
```

**Alternate Case 2 — Comparing Figures Across Currencies:**

```gherkin
Feature: Handling multi-currency comparisons
  As a financial analyst at a multinational
  I want currency differences handled explicitly
  So that I don't misread a comparison

  Scenario: Comparing revenue reported in different local currencies
    Given one document reports figures in EUR and another in USD
    When the analyst compares the revenue line
    Then the system displays both original currency values
    And shows a converted value using the documented exchange rate and its source
```

---

### ID-HU-FE-004

- Name: Key Financial Figures & Ratios Dashboard
- Description: As a financial analyst, I want a dashboard showing extracted key figures and ratios (liquidity, solvency, profitability, efficiency) with their sources, so that I don’t have to recalculate them manually from raw statements.
- Acceptance criteria:
    - Given a set of financial statements has been processed, when I open the dashboard, then the system displays key ratios (e.g., ROE, ROA, EBITDA margin, current ratio) with their calculated values and source citations.
    - Given I select a prior period, when I view the dashboard, then the system shows the ratio trend over time.
    - Given a ratio calculation depends on figures from more than one document, when I view the ratio, then the system lists all contributing sources.
- Priority: Medium
- Story points:
- Subtasks:
    - Design dashboard layout with ratio cards/charts.
    - Implement trend chart component (period over period).
    - Implement multi-source citation display per ratio.
    - Add filter by entity/segment/period.
    - Add export dashboard to PDF/Excel.

**Main Use Case:**

```gherkin
Feature: Displaying key financial ratios
  As a financial analyst
  I want to see calculated ratios with sources
  So that I can trust and reuse them without recalculating

  Scenario: Viewing EBITDA margin on the dashboard
    Given the income statement for FY24 has been ingested
    When the analyst opens the ratios dashboard
    Then the system shows the EBITDA margin value
    And shows citations to the revenue and EBITDA line items used in the calculation
```

**Alternate Case 1 — Insufficient Data to Calculate a Ratio:**

```gherkin
Feature: Handling missing data for ratio calculation
  As a financial analyst
  I want to know when a ratio cannot be calculated
  So that I don't assume a blank value means "no activity"

  Scenario: Missing balance sheet data for a solvency ratio
    Given the balance sheet for FY24 has not yet been ingested
    When the analyst opens the ratios dashboard
    Then the solvency ratio card shows "insufficient data"
    And indicates which document is missing
```

**Alternate Case 2 — Ratio Trend Reveals an Outlier:**

```gherkin
Feature: Flagging outlier ratio values
  As a financial analyst
  I want unusual jumps in a ratio flagged
  So that I investigate before presenting it

  Scenario: Sudden change in ROE between periods
    Given ROE was 12% in Q1 and jumps to 34% in Q2
    When the analyst views the trend chart
    Then the system visually flags the Q2 value as an outlier
    And prompts the analyst to review the underlying drivers (e.g., leverage change)
```

---

### ID-HU-FE-005

- Name: Document Upload & Version Status Interface
- Description: As a financial analyst, I want to upload financial documents, including Excel workbooks, and see their processing and version status, so that I know what is available for querying and whether it is the current approved version.
- Acceptance criteria:
    - Given I have a financial document (PDF or Excel), when I upload it, then the system confirms receipt and shows a processing status (queued, processing, ready, failed).
    - Given a document is an Excel workbook with multiple sheets, when it finishes processing, then the system lists the sheets detected and any parsing warnings (e.g., unresolved formulas, macros).
    - Given I upload a new version of an existing document, when the upload completes, then the system marks the previous version as superseded and links both versions.
- Priority: High
- Story points:
- Subtasks:
    - Build upload UI with drag-and-drop and file type validation.
    - Build processing status indicator component.
    - Display parsing summary for Excel workbooks (sheets detected, warnings).
    - Implement version linking UI (previous/current version badges).
    - Implement failed-upload error messaging with retry option.

**Main Use Case:**

```gherkin
Feature: Uploading and tracking a financial document
  As a financial analyst
  I want to upload a document and track its processing
  So that I know when it's ready to query

  Scenario: Uploading a new Excel budget model
    Given the analyst has a file "FY25_Budget_Model.xlsx"
    When the analyst uploads the file
    Then the system confirms the upload
    And shows the status progressing from "queued" to "ready"
    And lists the sheets detected in the workbook
```

**Alternate Case 1 — Upload of Unsupported or Corrupted File:**

```gherkin
Feature: Rejecting invalid uploads
  As a financial analyst
  I want clear feedback when a file cannot be processed
  So that I know to fix or resubmit it

  Scenario: Corrupted Excel file uploaded
    Given the analyst uploads a file that fails to open correctly
    When the system attempts to process it
    Then the status shows "failed"
    And an error message explains the file could not be parsed
    And the analyst is given the option to re-upload
```

**Alternate Case 2 — New Version Uploaded for an Existing Document:**

```gherkin
Feature: Managing document versions
  As a financial analyst
  I want prior versions preserved and linked
  So that historical answers remain traceable

  Scenario: Replacing a prior quarter's cash flow model
    Given "Q2_CashFlow_v1.xlsx" is already in the system
    When the analyst uploads "Q2_CashFlow_v2.xlsx" as a new version of the same document
    Then the system marks v1 as superseded
    And links v1 and v2 together
    And future queries default to v2 while v1 remains accessible for audit purposes
```

---

## BACKEND

### Group: Excel Workbook Ingestion (split from original “Excel Workbook Ingestion & Parsing Engine”)

### ID-HU-BE-001

- Name: Excel Ingestion — Sheet & Cell Value Extraction
- Description: As a financial analyst, I want the system to extract every sheet and cell value from the Excel workbooks I upload, so that the raw data in my models becomes queryable.
- Acceptance criteria:
    - Given an Excel file (.xlsx) is uploaded, when it is processed, then the system extracts all sheets and their cell values into a queryable structure.
    - Given a workbook has merged cells, when it is processed, then the system correctly associates the merged value with all cells it spans.
    - Given a sheet is empty or contains no data, when it is processed, then the system indexes it without error and marks it as empty.
- Priority: High
- Story points:
- Subtasks:
    - Implement Excel file reader for sheets and cell values (e.g., openpyxl or equivalent).
    - Implement merged-cell value association logic.
    - Implement empty-sheet detection and handling.
    - Store cell values in the document index with sheet/cell addressability.
    - Write unit tests for standard, merged-cell, and empty-sheet workbooks.

**Main Use Case:**

```gherkin
Feature: Extracting sheet and cell values from an Excel workbook
  As a financial analyst
  I want every sheet and cell value in my workbook extracted
  So that the raw data becomes queryable

  Scenario: Ingesting a single-sheet budget file
    Given I upload a file "Q3_Budget.xlsx" with one sheet "Budget"
    When the system processes the file
    Then every cell value in "Budget" is extracted and indexed
    And I can see the workbook status marked "ready"
```

**Alternate Case 1 — Merged Cells:**

```gherkin
Feature: Handling merged cells during extraction
  As a financial analyst
  I want merged cell values correctly associated with every cell they span
  So that I don't get a blank or wrong value when I query a merged region

  Scenario: Workbook with a merged header cell
    Given my workbook has cells B2:D2 merged with the value "Q3 Actuals"
    When the system processes the file
    Then the value "Q3 Actuals" is indexed for cells B2, C2, and D2
```

**Alternate Case 2 — Empty Sheet:**

```gherkin
Feature: Handling empty sheets
  As a financial analyst
  I want empty sheets ingested without errors
  So that a blank tab in my workbook doesn't break the whole file's processing

  Scenario: Workbook contains an unused template sheet
    Given my workbook includes an empty sheet named "Template"
    When the system processes the file
    Then "Template" is indexed as empty
    And the rest of the workbook is processed normally
```

---

### ID-HU-BE-002

- Name: Excel Ingestion — Formulas & Cross-Sheet References
- Description: As a financial analyst, I want the system to preserve formulas, named ranges, and cross-sheet references in my workbook, so that I can trace how a figure was calculated, not just see its final value.
- Acceptance criteria:
    - Given a workbook contains formulas, when it is processed, then the system stores each formula alongside its calculated value.
    - Given a workbook contains named ranges, when it is processed, then the system resolves and indexes them.
    - Given a formula in one sheet references cells in another sheet, when it is processed, then the system preserves that cross-sheet link for traceability.
- Priority: High
- Story points:
- Subtasks:
    - Implement formula extraction alongside calculated cell values.
    - Implement named-range resolution and indexing.
    - Implement cross-sheet reference resolution and linking.
    - Write unit tests for formulas, named ranges, and cross-sheet references.

**Main Use Case:**

```gherkin
Feature: Preserving formulas and cross-sheet references
  As a financial analyst
  I want formulas and their cross-sheet references preserved
  So that I can trace how a figure was calculated

  Scenario: Ingesting a projection model with linked sheets
    Given I upload "FY25_Projection_Model.xlsx" with sheets "Assumptions" and "Revenue"
    And a cell in "Revenue" references a growth-rate cell in "Assumptions"
    When the system processes the file
    Then the formula and its cross-sheet reference are preserved
    And I can trace the "Revenue" figure back to the "Assumptions" cell
```

**Alternate Case 1 — Named Ranges:**

```gherkin
Feature: Resolving named ranges
  As a financial analyst
  I want named ranges resolved to their underlying cells
  So that formulas using named ranges remain traceable

  Scenario: Formula using a named range
    Given my workbook defines the named range "TaxRate" pointing to cell "Assumptions!B5"
    When the system processes a formula that uses "TaxRate"
    Then the system resolves "TaxRate" to "Assumptions!B5" in the traceability chain
```

**Alternate Case 2 — Circular Reference:**

```gherkin
Feature: Handling circular references during formula parsing
  As a financial analyst
  I want circular references detected instead of causing a failure
  So that my file still gets ingested with a clear warning I can review

  Scenario: Circular reference detected between two cells
    Given my workbook contains a circular reference between cells in "CashFlow"
    When the system parses the workbook
    Then ingestion completes successfully
    And I see a warning identifying the circular reference location
```

---

### ID-HU-BE-003

- Name: Excel Ingestion — Unsupported Elements & Warnings
- Description: As a financial analyst, I want to be warned about parts of my workbook the system couldn’t fully interpret (macros, external links, protected sheets), so that I know which sections may need manual review instead of assuming everything was captured.
- Acceptance criteria:
    - Given a workbook contains macros, when it is processed, then the macros are not executed and a warning is attached to the document.
    - Given a workbook contains links to external files, when it is processed, then the system flags the external link instead of silently failing.
    - Given a sheet is password-protected, when it is processed, then the system flags that sheet as not fully parsed rather than skipping it silently.
- Priority: Medium
- Story points:
- Subtasks:
    - Implement macro detection and warning logging.
    - Implement external-link detection and flagging.
    - Implement protected-sheet detection and flagging.
    - Build a per-document warnings summary visible to the analyst.
    - Write unit tests for macros, external links, and protected sheets.

**Main Use Case:**

```gherkin
Feature: Flagging unsupported macro content
  As a financial analyst
  I want to be told when my workbook contains macros the system can't interpret
  So that I know a manual review may be needed before I trust that section

  Scenario: Workbook with VBA macros
    Given my uploaded workbook contains VBA macros
    When the system processes the file
    Then the macros are not executed
    And I see a warning attached to the document indicating manual review may be required
```

**Alternate Case 1 — External Links:**

```gherkin
Feature: Flagging external file links
  As a financial analyst
  I want to know if my workbook depends on another file
  So that I understand a figure may be incomplete without that external source

  Scenario: Formula referencing another workbook
    Given a cell in my workbook references "[Q2_Actuals.xlsx]Sheet1!A1"
    When the system processes the file
    Then the external reference is flagged as unresolved
    And the warning identifies which cell depends on the external file
```

**Alternate Case 2 — Password-Protected Sheet:**

```gherkin
Feature: Flagging protected sheets
  As a financial analyst
  I want to know if part of my workbook couldn't be read due to protection
  So that I don't assume that sheet's data is included in my answers

  Scenario: Workbook contains a password-protected sheet
    Given my workbook has a sheet protected with a password
    When the system processes the file
    Then that sheet is flagged as "not parsed: protected"
    And the rest of the workbook is processed normally
```

---

### Group: PDF Ingestion (split from original “PDF Ingestion & Parsing Engine for Financial Reports”)

### ID-HU-BE-004

- Name: PDF Ingestion — Text & Page Extraction
- Description: As a financial analyst, I want the system to extract text and page numbers from the PDF financial reports I upload, so that answers can point me to the exact page instead of the whole document.
- Acceptance criteria:
    - Given a PDF financial document is uploaded, when it is processed, then the system extracts the text of every page with its page number.
    - Given the PDF is a long document (e.g., notes to financial statements), when it is processed, then the system preserves paragraph order and page boundaries for citation purposes.
    - Given the PDF fails to open or is corrupted, when it is processed, then the system marks it as failed with a clear error instead of silently producing an empty result.
- Priority: High
- Story points:
- Subtasks:
    - Implement PDF text extraction pipeline with page-level offsets.
    - Implement paragraph-order preservation for citation mapping.
    - Implement corrupted-file detection and failure status.
    - Write tests using sample multi-page financial reports.

**Main Use Case:**

```gherkin
Feature: Extracting text and page references from a PDF report
  As a financial analyst
  I want the text of my PDF extracted with page references preserved
  So that citations point me to the exact page

  Scenario: Ingesting a quarterly report
    Given I upload a 20-page PDF "Q3_Report.pdf"
    When the system processes the file
    Then the text of every page is extracted
    And each passage is indexed with its page number
```

**Alternate Case 1 — Long Document:**

```gherkin
Feature: Preserving structure in long PDF documents
  As a financial analyst
  I want paragraph order preserved across a long document
  So that a citation reflects the passage exactly as it reads in context

  Scenario: Ingesting the notes to financial statements
    Given I upload a 120-page PDF "Notes_to_FS_FY24.pdf"
    When the system processes the file
    Then paragraph order is preserved within each page
    And I can retrieve any passage together with its surrounding context
```

**Alternate Case 2 — Corrupted PDF:**

```gherkin
Feature: Handling corrupted PDF files
  As a financial analyst
  I want to be told clearly when a PDF can't be opened
  So that I know to re-upload it instead of assuming it was ingested

  Scenario: Corrupted PDF uploaded
    Given I upload a PDF file that fails to open
    When the system attempts to process it
    Then the document status shows "failed"
    And an error message explains the file could not be parsed
```

---

### ID-HU-BE-005

- Name: PDF Ingestion — OCR for Scanned Pages
- Description: As a financial analyst, I want scanned (image-based) pages in my PDFs automatically converted to searchable text, so that scanned audit reports and management letters are just as queryable as native PDFs.
- Acceptance criteria:
    - Given a PDF page has no embedded text layer, when it is processed, then the system applies OCR to extract the text.
    - Given OCR is applied, when the text is indexed, then it is tagged with a lower confidence level than natively extracted text.
    - Given a PDF mixes scanned and native-text pages, when it is processed, then each page is handled with the correct method (OCR or direct extraction).
- Priority: Medium
- Story points:
- Subtasks:
    - Integrate an OCR engine for image-based PDF pages.
    - Implement per-page detection of scanned vs. native-text content.
    - Implement confidence tagging for OCR-derived text.
    - Write tests using sample scanned audit reports and management letters.

**Main Use Case:**

```gherkin
Feature: Extracting text from scanned pages via OCR
  As a financial analyst
  I want scanned pages to be OCR'd automatically
  So that scanned documents remain searchable like any other file I upload

  Scenario: Scanned management letter uploaded
    Given I upload a scanned management letter with no embedded text layer
    When the system processes the file
    Then OCR is applied to extract the text
    And I see the extracted text indexed with a lower confidence flag
```

**Alternate Case 1 — Mixed Document:**

```gherkin
Feature: Handling documents with both scanned and native-text pages
  As a financial analyst
  I want each page processed with the right method automatically
  So that I don't have to know or specify which pages are scanned

  Scenario: PDF with a scanned cover page and native-text remaining pages
    Given my PDF has a scanned cover page and 19 native-text pages
    When the system processes the file
    Then the cover page is OCR'd
    And the remaining pages are extracted directly
    And all 20 pages are indexed and searchable
```

**Alternate Case 2 — Low-Quality Scan:**

```gherkin
Feature: Flagging low-confidence OCR results
  As a financial analyst
  I want to know when an OCR result is unreliable
  So that I verify it against the original scan before trusting it

  Scenario: Poor-quality scanned page
    Given a scanned page produces OCR text with very low confidence
    When the system indexes that page
    Then the passage is tagged with a "low confidence — verify against scan" flag
```

---

### ID-HU-BE-006

- Name: PDF Ingestion — Tables & Section/Note Structure
- Description: As a financial analyst, I want tables and note/section numbering in my PDFs parsed into structured data, so that I can query individual figures and specific notes instead of only free text.
- Acceptance criteria:
    - Given a PDF contains a financial table, when it is processed, then the system extracts it into structured rows and columns with row labels and column headers.
    - Given a PDF contains numbered notes or sections, when it is processed, then the system indexes each note/section number with its page reference.
    - Given a table spans multiple periods (e.g., current and prior year columns), when it is processed, then each value is correctly associated with its period column.
- Priority: High
- Story points:
- Subtasks:
    - Implement table detection and structured extraction (rows/columns).
    - Implement note/section-number detection and indexing.
    - Implement multi-period column association logic.
    - Write tests using sample balance sheets and notes to financial statements.

**Main Use Case:**

```gherkin
Feature: Extracting tabular data accurately
  As a financial analyst
  I want tables in my PDF parsed into structured rows and columns
  So that I can query individual figures instead of only full pages

  Scenario: Balance sheet table spanning two columns of figures (current and prior year)
    Given my PDF contains a balance sheet table with two year columns
    When the system processes the page
    Then each line item is extracted with both year values correctly associated
    And each value is indexed with its row label and column header
```

**Alternate Case 1 — Note Numbering:**

```gherkin
Feature: Indexing note numbers for citation
  As a financial analyst
  I want each numbered note indexed with its page
  So that I can jump directly to a specific note when I get an answer

  Scenario: Ingesting the notes to financial statements
    Given a 120-page PDF "Notes_to_FS_FY24.pdf" contains numbered notes 1 through 35
    When the system processes the file
    Then each note number and its page reference are indexed for me to search
```

**Alternate Case 2 — Malformed Table:**

```gherkin
Feature: Handling tables that don't extract cleanly
  As a financial analyst
  I want to be told when a table couldn't be structured reliably
  So that I verify it manually instead of trusting a garbled extraction

  Scenario: Table with irregular formatting
    Given a table in the PDF has merged or irregular cells that prevent clean extraction
    When the system processes that page
    Then the table is flagged as "low-confidence structure"
    And the raw page text remains available as a fallback
```

---

### Group: Question Answering Engine (split from original “Retrieval-Augmented Question Answering Engine”)

### ID-HU-BE-007

- Name: QA Engine — Relevant Source Retrieval
- Description: As a financial analyst, I want the system to find the passages and cells most relevant to my question across all my documents, so that the answer I get is built from the right sources instead of a generic search.
- Acceptance criteria:
    - Given a natural language question, when it is submitted, then the system retrieves the most relevant passages/cells ranked by relevance.
    - Given a question spans multiple documents (e.g., income statement and cash flow statement), when it is submitted, then the system retrieves relevant content from all applicable documents.
    - Given I have access restrictions, when retrieval runs, then only content I’m authorized to see is considered.
- Priority: High
- Story points:
- Subtasks:
    - Implement embedding/indexing pipeline for parsed documents (text and Excel cells).
    - Implement retrieval logic with relevance ranking.
    - Implement multi-document retrieval for cross-statement questions.
    - Integrate permission filtering into the retrieval step.
    - Write tests for single- and multi-document retrieval accuracy.

**Main Use Case:**

```gherkin
Feature: Retrieving relevant sources for a question
  As a financial analyst
  I want the most relevant passages and cells retrieved for my question
  So that my answer is built on the right sources

  Scenario: Retrieving sources spanning two documents
    Given the Q1 income statement and Q1 cash flow statement are indexed
    When I ask "Does net income reconcile with the starting point of the cash flow statement?"
    Then the system retrieves the relevant figures from both documents
```

**Alternate Case 1 — Restricted Content Excluded:**

```gherkin
Feature: Filtering retrieval by access permissions
  As a financial analyst without special clearance
  I want restricted content excluded from what gets retrieved for me
  So that I never see something I'm not authorized to see, even indirectly

  Scenario: Question that would touch a restricted section
    Given a relevant passage is tagged "Restricted: Compensation"
    And I don't have the "Compensation-Access" role
    When I ask a question that would otherwise retrieve that passage
    Then that passage is excluded from my retrieval results
```

**Alternate Case 2 — No Relevant Source Found:**

```gherkin
Feature: Returning no results when nothing is relevant
  As a financial analyst
  I want to be told when nothing relevant was found
  So that I know to check whether the document even exists in the system

  Scenario: Question about a topic not covered by any ingested document
    Given no document I've uploaded mentions "supplier concentration risk"
    When I ask a question about it
    Then the retrieval step returns no relevant sources
```

---

### ID-HU-BE-008

- Name: QA Engine — Grounded Answer Generation with Citations
- Description: As a financial analyst, I want the answer to my question generated strictly from the retrieved sources, with a citation attached to every claim, so that I can trust and verify each statement instead of receiving unsupported text.
- Acceptance criteria:
    - Given relevant sources have been retrieved, when an answer is generated, then every factual statement in the answer is backed by at least one citation.
    - Given the answer draws from more than one document, when it is generated, then all contributing sources are cited.
    - Given two sources conflict, when the answer is generated, then both values are presented with their respective citations rather than one being silently chosen.
- Priority: High
- Story points:
- Subtasks:
    - Implement answer-generation step constrained to retrieved sources (grounded generation).
    - Implement citation attachment linking generated statements to source spans.
    - Implement multi-source citation aggregation.
    - Implement conflict-surfacing logic when sources disagree.
    - Write tests validating that every generated statement has a citation.

**Main Use Case:**

```gherkin
Feature: Generating a grounded, cited answer
  As a financial analyst
  I want my answer generated only from retrieved sources
  So that every answer I receive is verifiable

  Scenario: Answering a question spanning two documents
    Given relevant figures have been retrieved from the Q1 income statement and cash flow statement
    When the system generates the answer
    Then it states whether net income reconciles with the cash flow starting point
    And cites both source documents
```

**Alternate Case 1 — Conflicting Sources:**

```gherkin
Feature: Surfacing conflicting source data
  As a financial analyst
  I want conflicting figures across my documents flagged to me
  So that I'm aware of the discrepancy instead of getting a single silently-picked value

  Scenario: Two documents report different revenue figures for the same period
    Given the BI export and the ERP report show different revenue values for Q2
    When I ask about Q2 revenue
    Then I see both values with their respective citations
    And the conflict is flagged explicitly in the answer
```

**Alternate Case 2 — Multi-Source Answer:**

```gherkin
Feature: Citing all contributing sources
  As a financial analyst
  I want every document that contributed to my answer cited
  So that I can verify each part of a combined figure

  Scenario: Answer combining data from three documents
    Given my question requires data from the income statement, balance sheet, and a segment report
    When the answer is generated
    Then all three documents are cited in the answer
```

---

### ID-HU-BE-009

- Name: QA Engine — Low-Confidence & Ambiguous Question Handling
- Description: As a financial analyst, I want the system to tell me plainly when it isn’t confident in an answer or when my question is ambiguous, so that I never mistake an uncertain response for a verified fact.
- Acceptance criteria:
    - Given no sufficiently relevant source is found, when a question is submitted, then the system returns a “no confident answer” response instead of fabricating one.
    - Given a question is ambiguous (e.g., missing entity or period), when it is submitted, then the system asks me to clarify instead of guessing.
    - Given the system suggests escalation, when a low-confidence answer is returned, then it recommends the appropriate team to consult.
- Priority: High
- Story points:
- Subtasks:
    - Implement confidence scoring for generated answers.
    - Implement “no confident answer” fallback response.
    - Implement ambiguity detection and clarification prompts.
    - Implement escalation-team suggestion logic tied to question topic.
    - Write tests for low-confidence and ambiguous-question scenarios.

**Main Use Case:**

```gherkin
Feature: Avoiding fabricated answers
  As a financial analyst
  I want the system to tell me when it isn't confident instead of guessing
  So that I never mistake a fabricated figure for a fact

  Scenario: Question about a topic not covered by any ingested document
    Given no document I've uploaded mentions "supplier concentration risk"
    When I ask a question about it
    Then the system tells me it could not find a reliable source
    And does not show me a fabricated figure or claim
```

**Alternate Case 1 — Ambiguous Question:**

```gherkin
Feature: Clarifying ambiguous questions
  As a financial analyst
  I want the system to ask for clarification when a question is ambiguous
  So that I get the answer for the document/period I actually need

  Scenario: Question missing a required filter
    Given the corpus contains income statements for multiple subsidiaries and periods
    When I ask "What was revenue?" without specifying entity or period
    Then the system asks me to specify the entity and/or period
    And it does not return a default answer without confirmation
```

**Alternate Case 2 — Suggesting Escalation:**

```gherkin
Feature: Recommending escalation on low confidence
  As a financial analyst
  I want to be pointed to the right team when the system can't answer confidently
  So that I know who to ask instead of guessing myself

  Scenario: Low-confidence answer about an accounting treatment
    Given my question concerns a contract's revenue recognition treatment
    And the system's confidence in the answer is low
    When the answer is returned
    Then the system suggests escalating to Accounting/Consolidation
```

---

### Group: Ratio Extraction (split from original “Key Figure & Financial Ratio Extraction Engine”)

### ID-HU-BE-010

- Name: Ratio Engine — Financial Ratio Calculation
- Description: As a financial analyst, I want the system to calculate standard financial ratios directly from my ingested statements, so that I get consistent, sourced figures instead of recomputing them by hand.
- Acceptance criteria:
    - Given ingested income statement and balance sheet data for a period, when ratio extraction runs, then the system calculates liquidity, solvency, profitability, and efficiency ratios.
    - Given a ratio requires data from more than one document, when extraction runs, then the system combines the required sources automatically.
    - Given a ratio is calculated, when I view it, then the system shows citations to every line item used in the calculation.
- Priority: High
- Story points:
- Subtasks:
    - Define the standard ratio formula library (ROE, ROA, EBITDA margin, current ratio, debt-to-equity, etc.).
    - Implement line-item mapping from parsed statements to formula inputs.
    - Implement cross-document ratio calculation logic.
    - Implement citation attachment for each ratio’s contributing line items.
    - Write tests for each ratio formula against sample statements.

**Main Use Case:**

```gherkin
Feature: Calculating standard financial ratios
  As a financial analyst
  I want ratios calculated automatically from my ingested statements
  So that they're ready to use with full traceability

  Scenario: Calculating EBITDA margin
    Given the FY24 income statement has been ingested with revenue and EBITDA line items identified
    When I open the ratios view
    Then I see the calculated EBITDA margin
    And citations to the revenue and EBITDA line items used
```

**Alternate Case 1 — Cross-Document Ratio:**

```gherkin
Feature: Cross-document ratio calculation
  As a financial analyst
  I want ratios that need data from more than one document calculated automatically
  So that I don't have to manually combine figures from separate statements

  Scenario: Calculating return on assets using income statement and balance sheet
    Given net income is in the income statement and total assets are in the balance sheet
    When I open the ratios view
    Then I see ROA calculated by combining both sources
    And citations to both documents
```

**Alternate Case 2 — Ratio Definition Mismatch:**

```gherkin
Feature: Handling ambiguous line-item mapping
  As a financial analyst
  I want to be warned if a ratio input maps to an unexpected line item
  So that I don't trust a ratio calculated on the wrong figure

  Scenario: Statement uses a non-standard label for a required input
    Given the balance sheet labels current liabilities as "Short-Term Obligations" instead of a standard term
    When the system maps inputs for the current ratio
    Then it flags the mapping as low-confidence for my review before displaying the ratio
```

---

### ID-HU-BE-011

- Name: Ratio Engine — Trend Storage & Missing-Data Handling
- Description: As a financial analyst, I want ratios stored per period and clearly marked when they can’t be calculated, so that I can see trends over time and know exactly what’s missing instead of seeing a blank or misleading value.
- Acceptance criteria:
    - Given historical periods are available, when extraction runs, then the system stores ratio values per period to support trend analysis.
    - Given required line items for a ratio are missing, when extraction runs, then the system marks the ratio as “not calculable” and specifies the missing input.
    - Given ratio values are stored across periods, when I query a trend, then the system returns the values in chronological order with their sources.
- Priority: Medium
- Story points:
- Subtasks:
    - Implement per-period storage for ratio values.
    - Implement “not calculable” handling and missing-input reporting.
    - Implement chronological trend query logic.
    - Write tests for missing-data scenarios and multi-period trend retrieval.

**Main Use Case:**

```gherkin
Feature: Storing ratios per period for trend analysis
  As a financial analyst
  I want ratio values stored across periods
  So that I can see how they trend over time

  Scenario: Storing EBITDA margin across four quarters
    Given EBITDA margin has been calculated for Q1 through Q4 of FY24
    When I request the trend for EBITDA margin
    Then I receive the four values in chronological order with their sources
```

**Alternate Case 1 — Missing Line Item:**

```gherkin
Feature: Reporting non-calculable ratios
  As a financial analyst
  I want to be told why a ratio couldn't be calculated
  So that I know what's missing instead of seeing a blank or misleading value

  Scenario: Balance sheet missing current liabilities
    Given the current ratio requires current assets and current liabilities
    And current liabilities have not been identified in the ingested balance sheet
    When I open the ratios view
    Then the current ratio is marked "not calculable"
    And I see the missing input "current liabilities" reported
```

**Alternate Case 2 — Partial Trend Data:**

```gherkin
Feature: Handling incomplete trend history
  As a financial analyst
  I want a trend to show which periods are missing
  So that I don't misread a gap in data as a value of zero

  Scenario: One quarter missing from a four-quarter trend
    Given EBITDA margin is calculated for Q1, Q2, and Q4 but not Q3
    When I request the trend for EBITDA margin
    Then Q3 is shown as "not calculable" instead of being omitted or shown as zero
```

---

### Group: Access Control (split from original “Role-Based Access Control & Confidentiality Enforcement”)

### ID-HU-BE-012

- Name: Access Control — Confidentiality Tagging & Retrieval Filtering
- Description: As a financial analyst, I want restricted content automatically excluded from my search results based on its confidentiality tag, so that I never see information I’m not cleared for, without having to know what’s restricted myself.
- Acceptance criteria:
    - Given a document or section is tagged as restricted (e.g., compensation, related parties, legal contingencies), when I query it without the required permission, then the system excludes that content from my results.
    - Given content has no confidentiality tag, when it is ingested, then the system defaults it to the most restrictive applicable tier until reviewed.
    - Given restricted content is excluded from my results, when I receive my answer, then it states that part of the information is restricted rather than staying silent about it.
- Priority: High
- Story points:
- Subtasks:
    - Design confidentiality tagging schema for documents/sections (e.g., public, internal, restricted).
    - Implement default-to-restrictive tagging for untagged content.
    - Implement retrieval-layer filtering based on user permissions.
    - Implement “partially restricted” disclosure messaging in answers.
    - Write tests covering each confidentiality tier.

**Main Use Case:**

```gherkin
Feature: Enforcing confidentiality tiers on retrieval
  As a financial analyst without special clearance
  I want restricted content excluded from my answers
  So that access boundaries are respected without me having to know what's off-limits

  Scenario: Analyst without clearance queries restricted compensation data
    Given a document section is tagged "Restricted: Compensation"
    And I don't have the "Compensation-Access" role
    When I ask a question that would rely on that section
    Then that section is excluded from my results
    And my answer states that part of the information is restricted
```

**Alternate Case 1 — Untagged Content:**

```gherkin
Feature: Defaulting untagged content to the most restrictive tier
  As a financial analyst
  I want newly ingested content to be safe by default
  So that a labeling gap never accidentally exposes sensitive data

  Scenario: New document ingested without a confidentiality tag
    Given a newly ingested document has no confidentiality tag assigned
    When it becomes available in the system
    Then it defaults to the most restrictive tier until a reviewer classifies it
```

**Alternate Case 2 — Partial Restriction Disclosure:**

```gherkin
Feature: Disclosing that part of an answer was restricted
  As a financial analyst
  I want to know when part of my answer was withheld
  So that I don't assume I've received the complete picture

  Scenario: Answer partially built from a restricted section
    Given my question would normally draw from a restricted related-party note
    And I don't have the required permission
    When I receive my answer
    Then it explicitly states that part of the relevant information is restricted
```

---

### ID-HU-BE-013

- Name: Access Control — Authorized Access to Restricted Content
- Description: As a financial controller or other authorized user, I want to retrieve restricted content I hold clearance for, so that my legitimate work isn’t blocked by the confidentiality controls that protect my colleagues without that clearance.
- Acceptance criteria:
    - Given a user has the required role/permission, when they query restricted content, then the system includes it in the answer with the appropriate citation.
    - Given a user’s permissions cover only some of the restricted tiers involved in a question, when they query it, then the system includes only the tiers they’re cleared for.
    - Given an authorized user views restricted content, when they do, then the access is still logged like any other retrieval.
- Priority: High
- Story points:
- Subtasks:
    - Implement role/permission model and mapping to confidentiality tiers.
    - Implement partial-tier access logic for multi-tier questions.
    - Ensure logging captures authorized access events, not just denials.
    - Write tests for authorized single-tier and multi-tier access.

**Main Use Case:**

```gherkin
Feature: Granting access to authorized roles
  As a financial controller with the right clearance
  I want to retrieve restricted content I'm authorized to see
  So that my legitimate work isn't blocked by the confidentiality controls

  Scenario: Controller with compensation access asks about executive compensation
    Given I have the "Compensation-Access" role
    When I ask about executive compensation disclosed in the notes
    Then the system retrieves the restricted section
    And includes it in my answer with its citation
```

**Alternate Case 1 — Partial Clearance:**

```gherkin
Feature: Granting partial access across multiple confidentiality tiers
  As a financial analyst cleared for some but not all relevant tiers
  I want to receive only the parts of an answer I'm cleared for
  So that I still get partial value without violating access boundaries

  Scenario: Question touches both compensation and legal-contingency content
    Given I have the "Compensation-Access" role but not "Legal-Access"
    When I ask a question that draws on both types of content
    Then my answer includes the compensation data
    And excludes the legal-contingency data with a note that it's restricted
```

**Alternate Case 2 — Authorized Access Logged:**

```gherkin
Feature: Logging authorized access to restricted content
  As a financial analyst with clearance
  I want my access to restricted content logged
  So that there's an audit trail even for legitimate use

  Scenario: Authorized user retrieves a restricted section
    Given I have the required permission for the restricted section I queried
    When my answer is generated using that section
    Then the access is recorded in the audit log with my identity and the content accessed
```

---

### ID-HU-BE-014

- Name: Access Control — Denial Logging & Audit Trail
- Description: As a financial analyst, I want every denied access attempt logged with enough detail for an audit, so that our confidentiality controls are demonstrably enforced, not just assumed.
- Acceptance criteria:
    - Given an access denial occurs, when it happens, then the system logs the denied attempt with the user, timestamp, document, and confidentiality tier involved.
    - Given pre-release insider information is queried by an unauthorized user, when it happens, then the denial is logged with the same rigor as any other restricted access attempt.
    - Given an administrator reviews the audit trail, when they query it, then they can filter by user, document, or time period.
- Priority: Medium
- Story points:
- Subtasks:
    - Implement access-denial logging with full context (user, document, tier, timestamp).
    - Implement insider-information-specific logging for pre-release content.
    - Build an audit trail query/filter interface for administrators.
    - Write tests verifying log completeness for denial scenarios.

**Main Use Case:**

```gherkin
Feature: Logging denied access attempts
  As a financial analyst
  I want every denied access attempt logged
  So that our confidentiality controls are auditable

  Scenario: Analyst without clearance is denied access
    Given a document section is tagged "Restricted: Compensation"
    And I don't have the "Compensation-Access" role
    When I ask a question that would rely on that section
    Then the denial is logged with my identity, the document, and the timestamp
```

**Alternate Case 1 — Insider Information Denial:**

```gherkin
Feature: Protecting pre-release insider information
  As a financial analyst without insider disclosure clearance
  I want to be prevented from seeing unreleased results
  So that I don't inadvertently access or act on insider information

  Scenario: Query about unreleased quarterly results
    Given the Q3 results are marked "Pre-release: Insider" and not yet public
    And I am not in the pre-release disclosure group
    When I ask about Q3 results
    Then the system does not disclose the figures to me
    And my access attempt is logged
```

**Alternate Case 2 — Audit Trail Review:**

```gherkin
Feature: Reviewing the audit trail
  As a system administrator
  I want to filter the access log by user, document, or period
  So that I can investigate a specific access pattern or respond to an audit request

  Scenario: Administrator investigates access to a restricted document
    Given multiple access attempts to "Legal_Contingencies_FY24.pdf" are logged
    When the administrator filters the audit trail by that document
    Then they see every access attempt, whether granted or denied, with user and timestamp
```

---

### Group: Version Control (split from original “Version Control & Source-of-Truth Validation Engine”)

### ID-HU-BE-015

- Name: Version Control — Supersession & Default Version Selection
- Description: As a financial analyst, I want the system to automatically use the most recent approved version of a document when answering my questions, so that I don’t have to manually track which version is current.
- Acceptance criteria:
    - Given multiple versions of the same document exist, when a query is answered, then the system uses the most recent approved version by default.
    - Given a new version of a document is uploaded, when it is marked approved, then the system supersedes the prior version and links both together.
    - Given I ask about a prior version specifically, when I request it, then the system still allows me to view it for audit purposes.
- Priority: High
- Story points:
- Subtasks:
    - Design document metadata schema for version and approval status.
    - Implement version supersession logic and linking.
    - Implement default-to-latest-approved retrieval logic.
    - Implement access to superseded versions for audit purposes.
    - Write tests for version conflicts and default selection.

**Main Use Case:**

```gherkin
Feature: Prioritizing the current approved version
  As a financial analyst
  I want my answers to always use the current approved version
  So that I can trust the figures reflect the source of truth

  Scenario: Two versions of the same budget file exist
    Given "Budget_v1.xlsx" and "Budget_v2.xlsx" (approved) both exist for the same period
    When I ask a question about the budget
    Then my answer is based on "Budget_v2.xlsx"
    And the answer tells me which version was used
```

**Alternate Case 1 — New Version Supersedes Prior:**

```gherkin
Feature: Superseding a prior version on approval
  As a financial analyst
  I want a newly approved version to automatically replace the old one as the default
  So that I don't have to manually switch which version is used

  Scenario: A new approved version is uploaded
    Given "Q2_CashFlow_v1.xlsx" is the current default
    When "Q2_CashFlow_v2.xlsx" is uploaded and marked approved
    Then v2 becomes the default for future queries
    And v1 remains linked and accessible for audit purposes
```

**Alternate Case 2 — Viewing a Prior Version:**

```gherkin
Feature: Accessing superseded versions on request
  As a financial analyst
  I want to be able to open a prior version when I need to
  So that I can review historical figures for an audit or investigation

  Scenario: Analyst requests a superseded version
    Given "Budget_v1.xlsx" has been superseded by "Budget_v2.xlsx"
    When I explicitly request to view v1
    Then the system shows me v1 clearly labeled as superseded
```

---

### ID-HU-BE-016

- Name: Version Control — Source-of-Truth Tier Prioritization & Draft Flagging
- Description: As a financial analyst, I want the system to prefer higher-tier sources (ERP/EPM over official reports over internal reports) and warn me when it had to use a draft or lower-tier source, so that I always know how much to trust the figure behind my answer.
- Acceptance criteria:
    - Given a query could be answered from more than one tier of the source hierarchy, when it is answered, then the system prioritizes the higher-tier source.
    - Given a document has not been marked as approved/signed, when it is used as a source, then the system flags the answer as based on an unapproved/draft document.
    - Given a lower-tier source was used because a higher-tier source wasn’t available, when the answer is given, then the system notes this explicitly.
- Priority: High
- Story points:
- Subtasks:
    - Implement source-tier metadata (ERP/EPM, approved report, internal report) on ingested documents.
    - Implement source-tier prioritization logic in the retrieval/answer engine.
    - Implement “draft/unapproved” flagging in generated answers.
    - Implement “lower-tier source used” disclosure logic.
    - Write tests for tier prioritization and fallback scenarios.

**Main Use Case:**

```gherkin
Feature: Noting fallback to a lower-tier source
  As a financial analyst
  I want to know when my answer relied on a lower-tier source
  So that I know it may still need reconciliation against the ERP before I rely on it

  Scenario: Internal management report used because the ERP export is not yet ingested
    Given the ERP export for the current month has not been ingested
    And an internal management report with the same figures is available
    When I ask a question covered by that report
    Then my answer is based on the internal management report
    And it notes that the figures haven't been reconciled against the ERP/EPM source of truth
```

**Alternate Case 1 — Draft Document Used:**

```gherkin
Feature: Flagging draft-based answers
  As a financial analyst
  I want to be warned when no approved version exists
  So that I don't mistake a draft for a final, approved figure

  Scenario: Answer generated from an unapproved forecast file
    Given only a draft (unapproved) forecast file exists for the period I ask about
    When I receive an answer based on that file
    Then my answer includes a warning that the source is a draft, not an approved document
```

**Alternate Case 2 — Higher-Tier Source Available:**

```gherkin
Feature: Prioritizing the highest available tier
  As a financial analyst
  I want the highest-tier source used whenever it's available
  So that I get the most authoritative answer by default

  Scenario: Both ERP data and an internal report cover the same figure
    Given ERP/EPM data and an internal management report both cover Q2 revenue
    When I ask about Q2 revenue
    Then the system answers using the ERP/EPM data
    And notes that the internal report was available but not used as primary
```

---

## INTEGRATION

### Group: Corporate Repository Integration (split from original “Corporate Repository Integration (SharePoint / Google Drive)”)

### ID-HU-INT-001

- Name: Repository Integration — Automatic Document Sync
- Description: As a financial analyst, I want documents added or updated in our SharePoint or Google Drive repository to appear automatically in FinDoc Retriever, so that I don’t have to manually re-upload files to keep my answers current.
- Acceptance criteria:
    - Given a connected SharePoint folder, when a new or updated financial document is added, then the system automatically ingests it within the configured sync interval.
    - Given a connected Google Drive folder, when a new or updated financial document is added, then the system automatically ingests it within the configured sync interval.
    - Given a sync interval is configured, when it elapses, then the system checks both connected repositories for changes.
- Priority: Medium
- Story points:
- Subtasks:
    - Implement SharePoint API connector (authentication, folder listing, change detection).
    - Implement Google Drive API connector (authentication, folder listing, change detection).
    - Implement incremental sync scheduler covering both connectors.
    - Write tests for detecting new and updated files in each repository.

**Main Use Case:**

```gherkin
Feature: Automatic sync of approved documents from SharePoint
  As a financial analyst
  I want new files in our shared repository to appear automatically
  So that I always query current documents without uploading them myself

  Scenario: New approved Excel report added to SharePoint
    Given the "Corporate Finance/Q3 Reports" SharePoint folder is connected
    When a new file "Q3_Consolidated_Report.xlsx" is added to that folder
    Then the system detects and ingests it within the sync interval
    And I can query it right away
```

**Alternate Case 1 — Google Drive Sync:**

```gherkin
Feature: Automatic sync from Google Drive
  As a financial analyst
  I want files added to our Google Drive folder to sync the same way as SharePoint
  So that it doesn't matter which repository my team uses

  Scenario: New file added to a connected Google Drive folder
    Given a Google Drive folder "Finance/Management Reports" is connected
    When a new file "Aug_Management_Report.xlsx" is added to that folder
    Then the system detects and ingests it within the sync interval
```

**Alternate Case 2 — Updated File Detected:**

```gherkin
Feature: Detecting updates to an existing file
  As a financial analyst
  I want an edited file in the repository to trigger re-ingestion
  So that my answers reflect the latest edit, not a stale copy

  Scenario: Existing file is edited in place
    Given "Q3_Consolidated_Report.xlsx" was previously ingested
    When the file is edited and saved in SharePoint
    Then the next sync detects the change and re-ingests the updated content
```

---

### ID-HU-INT-002

- Name: Repository Integration — Permission Propagation
- Description: As a financial analyst, I want access changes made in our SharePoint or Google Drive repository to be reflected automatically in FinDoc Retriever, so that access boundaries stay consistent no matter which system I’m using.
- Acceptance criteria:
    - Given a document’s permissions change in the source repository, when the next sync runs, then FinDoc Retriever updates the document’s access to match.
    - Given a document is removed from the source repository, when the next sync runs, then it becomes unavailable for new queries in FinDoc Retriever.
    - Given a permission change restricts a document I previously had access to, when it takes effect, then I lose access without needing to be told why by the system.
- Priority: Medium
- Story points:
- Subtasks:
    - Implement permission-change detection during sync.
    - Implement permission propagation from source repository to FinDoc Retriever’s access model.
    - Implement handling for documents removed from the source repository.
    - Write tests for permission tightening, loosening, and removal scenarios.

**Main Use Case:**

```gherkin
Feature: Propagating permission changes from the repository
  As a financial analyst
  I want access changes made in our repository to be reflected in FinDoc Retriever
  So that I never see a document I've lost access to elsewhere, or lose access to one I should still see

  Scenario: Document access restricted in SharePoint
    Given "Legal_Contingencies_FY24.pdf" was previously accessible to my Finance group
    And access is now restricted to Legal Corporate only in SharePoint
    When the next sync runs
    Then FinDoc Retriever updates the document's permissions to match
    And I lose access to it in the system
```

**Alternate Case 1 — Document Removed from Repository:**

```gherkin
Feature: Handling document removal
  As a financial analyst
  I want a document deleted from our repository to stop appearing in my results
  So that I never rely on a file that no longer officially exists

  Scenario: Document deleted from SharePoint
    Given "Draft_Budget_2023.xlsx" was previously synced
    And it is deleted from the SharePoint folder
    When the next sync runs
    Then the document becomes unavailable for new queries in FinDoc Retriever
```

**Alternate Case 2 — Permissions Loosened:**

```gherkin
Feature: Reflecting expanded access
  As a financial analyst
  I want to gain access automatically when my repository permissions expand
  So that I don't have to request access separately in two systems

  Scenario: A previously restricted document is opened up to my group
    Given "Segment_Report_FY24.xlsx" was restricted to Controllers only
    And it is now shared with the broader Finance group in SharePoint
    When the next sync runs
    Then my Finance-group access to the document is granted in FinDoc Retriever
```

---

### ID-HU-INT-003

- Name: Repository Integration — Sync Failure Handling & Retry
- Description: As a financial analyst, I want a single failed or corrupted file to never block the rest of my documents from syncing, so that one bad file doesn’t delay my access to everything else in the repository.
- Acceptance criteria:
    - Given a sync fails for a specific file, when it happens, then the system logs the failure and continues syncing the remaining files.
    - Given a file sync fails, when it happens, then the system retries according to a defined retry policy.
    - Given retries are exhausted for a file, when that happens, then the system flags it for manual review instead of retrying indefinitely.
- Priority: Medium
- Story points:
- Subtasks:
    - Implement per-file failure isolation during batch sync.
    - Implement retry policy (attempts, backoff) for failed files.
    - Implement “manual review needed” flagging after retries are exhausted.
    - Build a sync failure log visible to administrators.
    - Write tests for partial batch failures and retry exhaustion.

**Main Use Case:**

```gherkin
Feature: Handling sync failures gracefully
  As a financial analyst
  I want a single corrupted or failed file to not block the rest of my documents from syncing
  So that one bad file doesn't delay my access to everything else

  Scenario: One file fails to download during sync
    Given a sync batch includes 20 files and one file is corrupted at the source
    When the sync runs
    Then the other 19 files are ingested successfully and available to me
    And I can see the corrupted file logged as failed with a retry scheduled
```

**Alternate Case 1 — Retry Succeeds:**

```gherkin
Feature: Retrying a failed sync
  As a financial analyst
  I want a temporarily failed file to be retried automatically
  So that a transient issue doesn't require me to intervene

  Scenario: File fails due to a temporary network issue
    Given a file failed to sync due to a temporary connectivity issue
    When the system retries according to its policy
    Then the file syncs successfully on the retry
    And the failure log is updated to reflect the successful retry
```

**Alternate Case 2 — Retries Exhausted:**

```gherkin
Feature: Flagging persistent sync failures
  As a financial analyst
  I want to be told when a file couldn't be synced after multiple attempts
  So that I know to follow up manually instead of assuming it will resolve itself

  Scenario: File fails on every retry attempt
    Given a file has failed to sync on all retry attempts allowed by policy
    When the final retry fails
    Then the file is flagged for manual review
    And an alert is logged for the administrator
```

---

### Group: ERP/EPM Integration (split from original “ERP/EPM System Integration (SAP / Oracle Hyperion)”)

### ID-HU-INT-004

- Name: ERP/EPM Integration — Scheduled Data Extraction
- Description: As a financial analyst, I want the system to pull consolidated data from our ERP/EPM systems (SAP, Hyperion) on a schedule and mark it as the authoritative source, so that my answers are grounded in the same numbers Finance treats as the source of truth.
- Acceptance criteria:
    - Given valid ERP/EPM credentials and scope are configured, when a scheduled extract runs, then the system pulls consolidated general ledger and reporting data into FinDoc Retriever’s index.
    - Given ERP/EPM data is ingested, when it is indexed, then it is tagged with source tier “ERP/EPM”.
    - Given ERP/EPM data and an internal report cover the same figure, when a question is answered, then the system treats the ERP/EPM data as authoritative.
- Priority: Medium
- Story points:
- Subtasks:
    - Implement ERP/EPM API or extract-file connector (e.g., SAP BAPI/OData, Hyperion export).
    - Map ERP/EPM data structures (accounts, cost centers, entities) to FinDoc Retriever’s data model.
    - Implement scheduled extraction job.
    - Implement source-tier tagging for ERP/EPM-origin data.
    - Write tests for extraction accuracy and tier tagging.

**Main Use Case:**

```gherkin
Feature: Pulling consolidated data from the ERP/EPM system
  As a financial analyst
  I want ERP/EPM data ingested on a schedule
  So that it's available as the authoritative source for my questions

  Scenario: Scheduled extract from SAP
    Given the SAP connection is configured with valid credentials
    When the nightly extraction job runs
    Then consolidated general ledger data is pulled into the system
    And I can see it marked with source tier "ERP/EPM" when I use it
```

**Alternate Case 1 — ERP Precedence in Conflicts:**

```gherkin
Feature: Resolving conflicts in favor of the ERP source
  As a financial analyst
  I want ERP data to take precedence when it conflicts with an internal report
  So that my answer aligns with our source-of-truth hierarchy

  Scenario: Internal report figure differs from ERP figure
    Given the ERP reports Q2 revenue of 4,795,000 and an internal report shows 4,820,000
    When I ask about Q2 revenue
    Then I see the ERP figure presented as the primary answer
    And the discrepancy with the internal report noted for reference
```

**Alternate Case 2 — Multi-Entity Extraction:**

```gherkin
Feature: Extracting data across multiple entities
  As a financial analyst at a multinational
  I want ERP data pulled for all connected subsidiaries
  So that I can query consolidated or entity-level figures consistently

  Scenario: Extract covering multiple subsidiaries
    Given the ERP connection is configured for three subsidiaries
    When the scheduled extract runs
    Then data for all three subsidiaries is ingested and tagged by entity
```

---

### ID-HU-INT-005

- Name: ERP/EPM Integration — Connection Failure Handling
- Description: As a financial analyst, I want to be clearly warned when our ERP/EPM data might be stale due to a connection issue, so that I never unknowingly rely on outdated figures.
- Acceptance criteria:
    - Given the ERP/EPM connection fails, when a scheduled extract runs, then the system alerts the administrator.
    - Given a connection failure occurs, when it happens, then the system continues serving the last successfully synced data with a staleness indicator.
    - Given the connection is restored, when the next extract succeeds, then the staleness indicator is cleared automatically.
- Priority: Medium
- Story points:
- Subtasks:
    - Implement connection failure detection and administrator alerting.
    - Implement staleness indicator on ERP/EPM-sourced answers.
    - Implement automatic staleness-indicator clearing on successful reconnection.
    - Write tests for connection failure, staleness display, and recovery.

**Main Use Case:**

```gherkin
Feature: Handling ERP/EPM connection failures
  As a financial analyst
  I want to know if ERP data might be stale because of a connection issue
  So that I don't unknowingly rely on outdated figures

  Scenario: Nightly extract fails due to expired credentials
    Given the SAP connection credentials have expired
    When the nightly extraction job runs
    Then the extraction fails and an alert is sent to the system administrator
    And any answer relying on ERP data shows me a "data as of [last successful sync date]" indicator
```

**Alternate Case 1 — Connection Restored:**

```gherkin
Feature: Clearing the staleness indicator on recovery
  As a financial analyst
  I want the staleness warning removed automatically once the connection is fixed
  So that I don't keep seeing an outdated warning after the issue is resolved

  Scenario: Credentials are renewed and the next extract succeeds
    Given the SAP connection credentials have been renewed
    When the next scheduled extract completes successfully
    Then the staleness indicator is cleared from answers using ERP data
```

**Alternate Case 2 — Extended Outage:**

```gherkin
Feature: Escalating a prolonged connection failure
  As a financial analyst
  I want to be told if ERP data has been stale for an extended period
  So that I know to treat ERP-sourced answers with extra caution

  Scenario: Connection remains down across multiple scheduled extracts
    Given the ERP connection has failed for three consecutive scheduled extracts
    When I view an answer relying on ERP data
    Then the staleness indicator specifies how long the data has been unrefreshed
```

---

### Group: Single Sign-On Integration (split from original “Single Sign-On (SSO) / Identity Provider Integration”)

### ID-HU-INT-006

- Name: SSO Integration — Login & Role Mapping
- Description: As a financial analyst, I want to log into FinDoc Retriever using my existing corporate credentials and have my permissions set from my identity provider group, so that I don’t manage a separate password and my access matches my actual role.
- Acceptance criteria:
    - Given a user attempts to log in, when SSO is configured, then the system authenticates the user through the corporate identity provider without a separate password.
    - Given authentication succeeds, when the session is created, then the user’s roles/groups from the identity provider are mapped to FinDoc Retriever permissions.
    - Given a user has no mapped role in the identity provider, when they log in, then they receive the minimum default access rather than being denied entry entirely.
- Priority: Medium
- Story points:
- Subtasks:
    - Implement SSO protocol integration (SAML/OIDC) with the identity provider.
    - Map identity provider groups/roles to FinDoc Retriever permission roles.
    - Implement default minimum-access handling for unmapped users.
    - Write security tests for the authentication and role-mapping flow.

**Main Use Case:**

```gherkin
Feature: Authenticating via corporate SSO
  As a financial analyst
  I want to log in with my corporate credentials
  So that my access in FinDoc Retriever matches my identity and role across the company

  Scenario: Analyst logs in via SSO
    Given I have valid corporate credentials
    When I attempt to log into FinDoc Retriever
    Then I am redirected to our identity provider to authenticate
    And once successful, I get a session with permissions mapped from my identity provider groups
```

**Alternate Case 1 — Unmapped User:**

```gherkin
Feature: Handling users without a mapped role
  As a financial analyst new to the system
  I want to still be able to log in even if my role isn't mapped yet
  So that I'm not completely locked out while access is being configured

  Scenario: User authenticates but has no mapped FinDoc Retriever role
    Given my identity provider groups have no corresponding FinDoc Retriever role mapping
    When I log in successfully via SSO
    Then I receive the minimum default access level
    And I see a message indicating my full access is pending role assignment
```

**Alternate Case 2 — Multiple Group Membership:**

```gherkin
Feature: Combining permissions from multiple groups
  As a financial analyst who belongs to more than one identity provider group
  I want my permissions combined from all my groups
  So that I get the full access I'm entitled to, not just one group's subset

  Scenario: User belongs to both "Finance-Analyst" and "Compensation-Access" groups
    Given my identity provider groups include "Finance-Analyst" and "Compensation-Access"
    When I log in via SSO
    Then my FinDoc Retriever session includes permissions from both mapped roles
```

---

### ID-HU-INT-007

- Name: SSO Integration — Outage Handling & Role Change Propagation
- Description: As a financial analyst, I want to be told clearly if I can’t log in due to an identity provider outage, and I want my access to update promptly when my role changes, so that authentication issues and permission changes never leave me with the wrong access.
- Acceptance criteria:
    - Given the identity provider is unreachable, when a user attempts to log in, then the system displays a clear service-unavailable message rather than granting default access.
    - Given a user’s roles/groups change in the identity provider, when they next log in or the session refreshes, then FinDoc Retriever reflects the updated permissions.
    - Given a session is active when a role is revoked, when the session next refreshes, then access tied to the revoked role is removed without requiring a full re-login.
- Priority: Medium
- Story points:
- Subtasks:
    - Implement fallback/error handling for identity provider outages.
    - Implement session refresh logic to pick up role changes.
    - Implement mid-session permission revocation on refresh.
    - Write tests for outage handling and role-change propagation.

**Main Use Case:**

```gherkin
Feature: Handling identity provider outages
  As a financial analyst
  I want to be told clearly if I can't log in due to an outage
  So that I know it's a temporary issue rather than a broken or insecure experience

  Scenario: Login attempted during an identity provider outage
    Given our identity provider service is down
    When I attempt to log in
    Then I see a service-unavailable message
    And I am not granted a default or cached session
```

**Alternate Case 1 — Role Change on Refresh:**

```gherkin
Feature: Reflecting updated roles from the identity provider
  As a financial analyst whose role recently changed
  I want my permissions in FinDoc Retriever to update promptly
  So that my access always matches my current responsibilities

  Scenario: User loses the "Compensation-Access" role
    Given I previously had the "Compensation-Access" role
    And that role has been removed for me in the identity provider
    When my session refreshes
    Then my access to compensation-restricted content is revoked
```

**Alternate Case 2 — Mid-Session Revocation:**

```gherkin
Feature: Revoking access mid-session without forcing logout
  As a financial analyst
  I want a revoked permission to take effect without me needing to log out and back in
  So that access changes apply promptly and reliably

  Scenario: Role revoked while the user has an active session
    Given I am actively logged in with the "Legal-Access" role
    And that role is revoked in the identity provider mid-session
    When my session performs its next refresh check
    Then my access to legal-restricted content is removed for the remainder of my session
```

---

### Group: Escalation Notification Integration (split from original “Escalation Notification Integration (Email / Teams / Slack)”)

### ID-HU-INT-008

- Name: Escalation Integration — Creation & Topic-Based Routing
- Description: As a financial analyst, I want to send a question I’m not confident about to the right team with one action, so that I get expert validation without having to figure out who to contact myself.
- Acceptance criteria:
    - Given the system flags a question as low-confidence or requiring validation, when I choose to escalate, then a notification is sent to the configured team/channel with the question and relevant citations attached.
    - Given an escalation is created, when it is sent, then the system routes it to the correct team based on the topic (accounting treatment → Accounting/Consolidation, legal/contingencies → Legal Corporate, subsidiary-specific data → local controller).
    - Given I create an escalation, when it is submitted, then I receive confirmation that it was sent successfully.
- Priority: Medium
- Story points:
- Subtasks:
    - Implement the “escalate this answer” action in the query flow.
    - Implement topic-based routing rules to the correct escalation team.
    - Attach the original question and citations to the escalation payload.
    - Implement submission confirmation for the analyst.
    - Write tests for routing accuracy across topic categories.

**Main Use Case:**

```gherkin
Feature: Escalating a low-confidence answer
  As a financial analyst
  I want to send a low-confidence answer to the right team for validation
  So that I get expert confirmation before relying on it

  Scenario: Escalating an accounting treatment question
    Given my answer about revenue recognition is flagged as low-confidence
    When I choose to escalate it
    Then a notification with my question and its citations is sent to the Accounting/Consolidation team
    And I can see the escalation status set to "pending"
```

**Alternate Case 1 — Legal Routing:**

```gherkin
Feature: Routing legal matters to Legal Corporate
  As a financial analyst
  I want a legal or contingency-related question routed to Legal automatically
  So that I don't have to know which team handles which topic

  Scenario: Escalating a question about a contract contingency
    Given my question concerns a legal contingency disclosed in a note
    When I choose to escalate it
    Then the notification is routed to Legal Corporate
```

**Alternate Case 2 — Subsidiary Routing:**

```gherkin
Feature: Routing subsidiary-specific questions to the local controller
  As a financial analyst
  I want a question about a specific subsidiary routed to that subsidiary's controller
  So that the person with the most context reviews it first

  Scenario: Escalating a question about a specific subsidiary's figures
    Given my question concerns data specific to the Brazil subsidiary
    When I choose to escalate it
    Then the notification is routed to the Brazil local controller
```

---

### ID-HU-INT-009

- Name: Escalation Integration — Status Tracking & Re-Routing
- Description: As a financial analyst, I want to see the status of my escalation and have it corrected if it was routed to the wrong team, so that my question doesn’t stall without me knowing where it stands.
- Acceptance criteria:
    - Given an escalation has been sent, when I check its status, then the system shows whether it is pending, acknowledged, or resolved.
    - Given an escalation was routed to the wrong team, when a reviewer reclassifies it, then the system re-routes the notification to the correct team.
    - Given an escalation is re-routed, when it happens, then the escalation history reflects the change.
- Priority: Medium
- Story points:
- Subtasks:
    - Implement escalation status tracking (pending/acknowledged/resolved).
    - Implement a reclassification/re-routing action for reviewers.
    - Build escalation history log tied to the original question and citations.
    - Write tests for status transitions and re-routing.

**Main Use Case:**

```gherkin
Feature: Tracking escalation status
  As a financial analyst
  I want to check the status of an escalation I sent
  So that I know whether it's still waiting on a response

  Scenario: Checking the status of a pending escalation
    Given I escalated a question about revenue recognition
    When I check its status
    Then I see whether it is pending, acknowledged, or resolved
```

**Alternate Case 1 — Re-Routing a Misclassified Escalation:**

```gherkin
Feature: Re-routing a misclassified escalation
  As a financial analyst
  I want my escalation to reach the right team even if it was first routed incorrectly
  So that my question doesn't stall with the wrong reviewer

  Scenario: Escalation initially routed to Accounting is actually a legal matter
    Given my escalation about a contract clause was routed to Accounting/Consolidation
    When a reviewer reclassifies it as a legal matter
    Then the notification is re-routed to Legal Corporate
    And I can see the change reflected in the escalation history
```

**Alternate Case 2 — Escalation Resolved:**

```gherkin
Feature: Marking an escalation as resolved
  As a financial analyst
  I want to be notified when my escalation is resolved
  So that I know I can rely on the validated answer

  Scenario: Reviewer resolves an escalation
    Given my escalation about revenue recognition is marked "acknowledged"
    When the Accounting/Consolidation reviewer provides a validated answer and marks it resolved
    Then I see the status change to "resolved"
    And I can view the reviewer's response
```

---

### ID-HU-INT-010

- Name: Escalation Integration — Delivery Reliability
- Description: As a financial analyst, I want my escalation to still get through even if a notification channel fails, so that a request for expert validation is never silently lost.
- Acceptance criteria:
    - Given a notification fails to send, when it happens, then the system logs the delivery failure.
    - Given a notification delivery fails, when it happens, then the system retries delivery according to a defined policy.
    - Given retries are exhausted on the primary channel, when that happens, then the system falls back to an alternate channel (e.g., email) rather than dropping the escalation.
- Priority: Medium
- Story points:
- Subtasks:
    - Implement delivery failure detection and logging for each notification channel.
    - Implement retry policy for failed notification deliveries.
    - Implement fallback-channel logic (e.g., Teams/Slack → email) after retries are exhausted.
    - Write tests for delivery failure, retry, and fallback scenarios.

**Main Use Case:**

```gherkin
Feature: Handling failed escalation delivery
  As a financial analyst
  I want my escalation to still get through if a notification fails to send
  So that my request for expert validation is never silently lost

  Scenario: Teams channel notification fails to send
    Given the configured Teams channel webhook is temporarily unavailable
    When the system attempts to send my escalation notification
    Then the delivery failure is logged
    And the system retries delivery and falls back to email if retries are exhausted
```

**Alternate Case 1 — Retry Succeeds:**

```gherkin
Feature: Recovering from a transient delivery failure
  As a financial analyst
  I want a temporarily failed notification to be retried automatically
  So that a brief outage doesn't require me to resend my escalation manually

  Scenario: Notification fails once due to a transient issue
    Given the first delivery attempt to the Teams channel fails
    When the system retries per its policy
    Then the notification is delivered successfully on the retry
    And no fallback is needed
```

**Alternate Case 2 — Fallback Channel Used:**

```gherkin
Feature: Falling back to an alternate channel
  As a financial analyst
  I want my escalation delivered by email if the chat channel keeps failing
  So that my request always reaches the team through some channel

  Scenario: All retries on the primary channel fail
    Given delivery to the Teams channel has failed on every retry attempt
    When retries are exhausted
    Then the system sends the escalation via email instead
    And logs that the fallback channel was used
```