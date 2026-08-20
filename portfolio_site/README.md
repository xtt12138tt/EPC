# EPC Reimbursement Submission Agent

An AI-assisted workflow that converts semi-structured sign-in sheets into standardized reimbursement drafts and helps users complete an internal EPC payment application through browser automation.

## What it demonstrates

- AI-assisted extraction from messy spreadsheet text;
- Domain rules encoded as a reusable Skill;
- Structured JSON as the contract between reasoning and execution;
- Human-in-the-loop review for financial and payee-sensitive operations;
- Playwright browser automation with verification and recovery states;
- Batch processing with isolated order IDs and persisted event history.

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
3. Generate standardized expense notes and payee rules.
4. Validate totals and surface unresolved assumptions.
5. Let the user review and confirm the draft.
6. Fill EPC page 1, match payees on page 2, and read back the actual results.
7. Stop before final submission until the user explicitly confirms.

## Local preview

Open `index.html` directly, or run a static server in this directory.

## Static interactive workbench demo

`static_workbench_demo.html` is generated from the real 5000 workbench template with fully sanitized preset data. It includes three mock orders in different states and supports clicking the batch table, workbench tabs, draft view and status controls without calling any backend.

## Publish to GitHub Pages

1. Create a sanitized portfolio repository.
2. Upload this directory's `index.html` and `assets` folder to the repository root.
3. In GitHub Pages settings, select the `main` branch and root directory.
4. Add the generated Pages link to your resume.

## Privacy note

This is a sanitized portfolio representation. Do not upload real project IDs, names, phone numbers, screenshots, internal URLs, cookies, credentials, databases or logs.
