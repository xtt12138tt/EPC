# EPC Reimbursement Submission Agent

An AI-assisted workflow that converts semi-structured sign-in sheets into standardized reimbursement drafts and helps users complete an internal EPC payment application through browser automation.

## What it demonstrates

- AI-assisted information extraction from messy spreadsheet text
- Domain-specific rules encoded as a reusable skill
- Structured JSON as the contract between reasoning and execution
- Human-in-the-loop review for financial and payee-sensitive operations
- Playwright browser automation with verification and recovery states
- Batch processing with isolated order IDs and persisted event history

## Architecture

```text
Sign-in sheet / Excel / pasted table
                ↓
Skill + Codex reasoning layer
                ↓
Standardized EPC JSON
                ↓
5000 review web app
                ↓
Local Edge browser executor
                ↓
EPC form filling and payee verification
```

## Core workflow

1. Parse players, interfaces, part-time staff, exclusions, proxy relationships, amounts and notes.
2. Map amounts to EPC reimbursement categories.
3. Generate a standardized expense note and payee rules.
4. Validate totals and surface unresolved assumptions.
5. Let the user review and confirm the draft.
6. Fill EPC page 1 through browser automation.
7. Match and fill payees on page 2.
8. Read back actual values and reconcile differences.
9. Stop before final submission until the user explicitly confirms.

## Key engineering decisions

### Structured contract before automation

The model does not directly manipulate the financial form. It produces a normalized JSON draft that can be reviewed, edited, persisted and replayed by the executor.

### Explicit amounts first

Source values take priority over rate-table inference. Standard rates are used for completing missing fields and validating conflicts, not for silently replacing explicit values.

### Human-controlled risk gates

The workflow pauses for cost ownership, screenshots, page 1 review, payee differences, red validation errors and final submission.

### Payee reconciliation

Payees are matched by phone number first and normalized name second. Proxy collection, missing payees, extra platform rows and amount mismatches are surfaced for review.

## Privacy note

This repository is a sanitized portfolio representation. Real project IDs, names, phone numbers, screenshots, internal URLs, cookies and credentials must not be committed.

## Portfolio case study

See the accompanying case study for product context, decision rationale, business rules, limitations and interview talking points.

