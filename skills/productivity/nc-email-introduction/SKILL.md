---
name: nc-email-introduction
description: "Use when a prospecting row's Accion is NC (no contesta — didn't pick up): draft an intro/presentation email for the contact. Drafts only — sending goes through the approval-gated himalaya email skill and never happens automatically. Prefers Work email, falls back to Work email 2, routes rows with no usable address to manual review. Template is a placeholder pending final copy."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [prospecting, nc, email, draft, himalaya, sales]
    related_skills: [prospecting-action-router, himalaya, prospecting-excel-reader]
---

# NC Email Introduction

## Overview

`NC` means the contact **did not answer the phone** — the fallback touch is a
short intro/presentation email. This skill *drafts* that email; it never
sends. Draft construction: `tools/prospecting_excel.py::nc_email_draft`.
Sending, if approved, uses the repo's approval-gated email skill
(`skills/email/himalaya` — IMAP/SMTP CLI); there is no other email tooling and
none is improvised.

## When to Use

- Routed here by `prospecting-action-router` for rows with
  `normalized_action == "NC"`.
- Don't use for: cold outreach outside the prospecting workflow, bulk sending,
  or any send without an explicit per-batch approval.

## Recipient selection

1. **Work email** — used when present and plausible (`@`, no spaces, dotted
   domain).
2. **Work email 2** — only when Work email is missing or fails the check.
3. **Neither usable** → the row goes to manual review. Never guess or
   construct an address.

## The draft object

```json
{
  "recipient": "...",
  "subject": "[PLACEHOLDER] Presentación — {company}",
  "body": "[PLACEHOLDER — pending final template] ...",
  "company": "...",
  "source": {"sheet_name": "...", "row_number": 0},
  "approval_status": "approval_required"
}
```

The template lives in `tools/prospecting_excel.py::NC_EMAIL_TEMPLATE` and is a
**placeholder by design**:

> TODO: Define final NC intro/presentation email template with Manuel.

Until that TODO is resolved, drafts exist to be reviewed, not sent — surface
the placeholder status whenever presenting them.

## Sending (only after approval)

The router proposes each draft as a Cerberus `approval_required` action
(`type: send_email`). Only after the user explicitly approves does the draft
move to the himalaya skill for delivery — one approval per batch, no implied
carry-over to later batches. No approval → the draft is simply kept in the
summary.

Done when: every NC row is either a draft with a recipient, or a
manual-review entry ("NC without usable email") — and zero emails were sent
without approval.

## Common Pitfalls

1. **Auto-sending.** Drafting and sending are separate steps with an approval
   gate between them. No exceptions for "just one email".
2. **Repairing addresses.** `ana(at)acme.com` or a bare `acme.com` is not an
   email; manual review, not creative fixing.
3. **Skipping the fallback order.** Work email 2 is a fallback, not an
   additional recipient — one recipient per draft.
4. **Filling the placeholder ad hoc.** Don't invent final copy; the template
   is pending sign-off (see the TODO). Ad-hoc wording changes hide that
   status.
5. **Dropping provenance.** The draft's `source` (sheet, row) is how results
   get written back to the Excel afterwards.

## Verification Checklist

- [ ] Every NC row → draft or manual-review entry, none silently dropped
- [ ] Recipient chosen by the Work email → Work email 2 → manual review order
- [ ] Drafts carry `approval_status: approval_required` and source provenance
- [ ] Placeholder/TODO status visible in the draft body
- [ ] Zero emails sent without explicit approval (himalaya only, post-gate)
