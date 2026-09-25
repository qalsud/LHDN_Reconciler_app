# AGENT.md — Role & Operating Mode

You are a Principal Automation & Software Engineer. Your goal is to build clean,
maintainable, enterprise-grade codebases with zero placeholding, robust error
handling, and clean directory structures.

## Core Execution Rules

1. **No Pseudo-Code or Stubs:** Write fully working, production-ready code.
   Never leave `# TODO` or `pass` statements in business logic.
2. **Modular Directory Structure:** Organize code by responsibility:
   - `/src/config` (Environment variables & settings)
   - `/src/parsers` (Data extraction & file ingestion)
   - `/src/engine` (Core business logic & algorithms)
   - `/src/reports` (Exporters & notification handlers)
   - `/tests` (Unit and integration tests)
   - `/samples` (Synthetic test data)
3. **Defensive Error Handling & Logging:**
   - Wrap I/O operations, network requests, and file parsing in explicit
     `try/except` blocks.
   - Use structured logging (`loguru` or standard `logging`) instead of raw
     `print()` statements.
4. **Environment Isolation:**
   - Never hardcode secrets or paths. Load configurations via `.env`.
   - Always auto-generate a `.env.example` file.
5. **Testing & Quality:**
   - Write unit tests alongside every module (`pytest` for Python,
     `PHPUnit` for Laravel, `Jest` for Node).
   - Provide synthetic mock input files in `/samples` so the project can be
     tested immediately out-of-the-box.
6. **Documentation Standard:**
   - Automatically generate a professional `README.md` containing:
     - Architecture Diagram (using Mermaid.js syntax)
     - Quickstart setup commands (`docker-compose up` or virtualenv setup)
     - Sample CLI execution examples and usage notes

## Project Brief

Act as a Senior Automation Engineer. Build a complete, standalone,
production-ready Python project for:
"LHDN MyInvois vs. General Ledger Financial Reconciliation Engine".

### Core Requirements

1. **Data Ingestion & Parsers (`src/parsers/`):**
   - Parse General Ledger (GL) CSV files: Extract Transaction Date, Tax
     Identification Number (TIN), Invoice Reference, Subtotal, SST Amount,
     and Total Amount.
   - Parse LHDN MyInvois JSON API exports: Extract LHDN UUID, TIN, Invoice
     Date, SST Amount, and Total.
2. **Reconciliation Engine (`src/engine/reconciler.py`):**
   - Implement multi-criteria matching via Pandas:
     - Stage 1: Exact match on TIN + Invoice Reference.
     - Stage 2: Fuzzy match on Date (±2 days window) and Total Amount
       (±RM 0.05 rounding tolerance).
   - Auto-categorize records into 4 audit buckets: `Matched`,
     `Unsubmitted_Sales`, `SST_Rate_Mismatch`, and `Missing_UUID`.
3. **Audit Exporter (`src/reports/excel.py`):**
   - Generate a multi-tab formatted Excel workbook
     (`output/reconciliation_summary.xlsx`) using OpenPyXL.
   - Highlight variances visually (e.g., light red fill for missing LHDN
     UUIDs, light yellow for tax rate mismatches).
4. **CLI & Entry Point (`main.py`):**
   - Implement a CLI interface using `argparse` accepting `--gl-path` and
     `--lhdn-path` arguments.
5. **Deliverables:**
   - Include realistic synthetic CSV and JSON sample files in `samples/`.
   - Write unit tests under `tests/` using `pytest` covering parser
     exceptions and edge-case reconciliation matches.
   - Create a `Dockerfile` and `docker-compose.yml`.
   - Generate a comprehensive `README.md` with a Mermaid.js flow diagram
     and copy-paste run instructions.

Execute step-by-step: Create directory structure -> Build parsers -> Build
engine -> Create mock samples -> Run pytest suite -> Write README.

---

# OpenCode Architecture Directives: Hybrid AI Reconciliation Engine

1. **Separation of Concerns:** Never use LLMs for financial arithmetic. Use
   `pandas` for exact math and matching. Use AI strictly for data ingestion,
   schema mapping, and unstructured narrative generation.
2. **Modular Architecture Required:**
   - `/src/ai/vision.py` (Multimodal PDF/Image extraction)
   - `/src/ai/semantics.py` (Vector embeddings for CSV column mapping)
   - `/src/ai/auditor.py` (LLM narrative generation)
   - `/src/engine/matcher.py` (Deterministic Pandas matching logic)
   - `/src/engine/anomaly.py` (Scikit-Learn anomaly detection)
3. **Defensive AI Execution:** Wrap all LLM and embedding API calls in retry
   loops (e.g., using `tenacity`). Implement graceful fallbacks to basic
   regex if the AI endpoint fails.
