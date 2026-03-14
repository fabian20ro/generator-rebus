---
name: get-api-docs
description: >
  Use this skill when you need documentation for a third-party library, SDK, or API
  before writing code that uses it. Fetch the docs with chub before answering.
---

# Get API Docs via chub

When you need documentation for a library or API, fetch it with the `chub` CLI
rather than guessing from training data.

## Step 1 — Find the right doc ID

```bash
chub search "<library name>" --json
```

## Step 2 — Fetch the docs

```bash
chub get <id> --lang py    # or --lang js, --lang ts
```

## Step 3 — Annotate what you learned

```bash
chub annotate <id> "Note about a gotcha or workaround"
```

## Step 4 — Give feedback

```bash
chub feedback <id> up    # or down --label outdated
```

## Quick reference

| Goal | Command |
|------|---------|
| List everything | `chub search` |
| Find a doc | `chub search "stripe"` |
| Fetch Python docs | `chub get stripe/api --lang py` |
| Save a note | `chub annotate stripe/api "needs raw body"` |
| Rate a doc | `chub feedback stripe/api up` |
