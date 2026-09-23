---
name: jev-internal-links
description: Use when finding internal linking opportunities on a website, auditing orphan or under-linked pages, or building an internal link report from a sitemap. Also use when someone mentions Jev, TypeSafe, "~typesafe/jev-latest" or a decision model for SEO, or needs to call Jev through OpenRouter.
---

# Jev internal links

## Overview

Jev (TypeSafe AI) is a **decision model**: it never writes text. It gets a short `state` plus typed questions, and returns choices and probabilities. It costs $0.042 per million input tokens, and output is free.

This skill asks Jev, for every paragraph on a site, which of 4 shortlisted pages the reader should go to next (or none). Rules in `config.yaml` decide which links survive. A writing model then picks anchor text that already exists in the paragraph. **The rules make the calls, not the model.** The output is an interactive HTML report plus spreadsheets.

## Run it

One command, from any folder:

```bash
python <this skill>/scripts/run.py https://example.com
```

- It finds the sitemap itself (robots.txt, `/sitemap.xml`, sitemap indexes). You can also pass the sitemap URL directly.
- Output goes to `./jev-links/<domain>/`. The report opens when it finishes.
- It detects the site language and switches to a multilingual matching model for non-English sites.
- It prints a cost estimate and stops if the estimate is over `--max-cost` (default $3). A 150-page site costs about $0.75.

**Before the first run**, check two things:

1. **Python packages.** Run `python -c "import sentence_transformers, yaml, openpyxl, bs4"`. If it fails, run `pip install -r <this skill>/requirements.txt`, inside a venv if the system Python is managed.
2. **OpenRouter key.** It must be in `OPENROUTER_API_KEY`, in `./jev-links/<domain>/.env`, or in `~/.config/jev-internal-links/.env`. If there is none, ask the user for one (openrouter.ai/keys) and save it to the `~/.config` path with `chmod 600`. Never print the key.

## After the first run

1. Open `jev-links/<domain>/config.yaml` and set `money_pages` to the site's real offer pages: pricing, booking, signup, free trial. Use exact paths, and leave the list empty if the site has none.
2. Add `exclude_sources` patterns for pages that shouldn't receive links in their body, like hub listings, changelogs and legal pages.
3. Re-run the same command. Jev answers and anchors are cached, so a re-run costs only the few new calls.

Give the user the report path, the number of links (high confidence versus review), and 3 to 5 example links from `data/links.json`.

## Outputs (in `jev-links/<domain>/out/`)

| File | What it is |
|---|---|
| `jev-dashboard.html` | The report. Every decision, with the paragraph and the link in place, Jev's probability for each candidate page, why it was kept or dropped, a what-if confidence slider, approve and reject buttons, and CSV export. It's one file and needs no server. |
| `jev-decisions.xlsx` | Styled workbook with three tabs: links to approve, all decisions, summary. Uploading it to Google Drive keeps the formatting. |
| `jev-decisions.csv` | Every decision, flat |
| `internal-links.csv` / `.html` | Just the proposed links, grouped by page |

## Calling Jev directly

Jev is **not** on the chat completions endpoint.

```
POST https://openrouter.ai/api/alpha/decisions
Authorization: Bearer $OPENROUTER_API_KEY
{"model": "~typesafe/jev-latest",
 "state": "<short plain-text context>",
 "questions": {
   "best": {"type": "choice", "instructions": "...", "criteria": {"1": "...", "none": "..."}},
   "fits": {"type": "noul", "instructions": "<statement to rate true/false>"}}}
```

- The response is `answers.<name>`. A choice gives `{choice, probabilities, confidence}`, a noul gives `{noul}` (a 0 to 1 probability), and there is also `usage.cost`.
- `~typesafe/jev-latest` moves to new releases, and the response's `model` field shows which one it resolved to. Set `JEV_MODEL=typesafe/jev-1.13` to pin a version when runs must be comparable.
- Keep the state small and relevant. Do arithmetic, dates and metrics in Python, never in the state.

## Tuning (`config.yaml`, free to re-run)

- **Too few links:** lower `thresholds.min_confidence`, or raise `caps.max_new_links_per_source`.
- **Links piling onto one page:** lower `caps.max_new_links_per_target`.
- **Weak links from listing or changelog pages:** add them to `exclude_sources`.
- To compare two runs, pass `--compare <old>/data/judgements.json` to `scripts/decisions.py`.

## Common mistakes

- **Calling `/api/v1/chat/completions` with Jev.** Use `/api/alpha/decisions`.
- **Presenting confidence as calibrated.** It's Jev's own estimate. A human approves every link.
- **Claiming ranking gains.** Nothing here measures rankings. To test impact, compare linked pages against similar unlinked pages in Search Console after 60 to 90 days.
- **Running it on client sites without checking TypeSafe's terms.** Jev's API terms are still early-access.
