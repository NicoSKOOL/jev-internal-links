#!/usr/bin/env python3
"""Step 2: for each passage, shortlist the few pages it might sensibly link to.

Local embeddings, no API cost. Output: data/candidates.json
"""
import json
from pathlib import Path

from _env import WORKDIR

import re

import numpy as np
import yaml
from sentence_transformers import SentenceTransformer

HERE = WORKDIR  # data/, cache/, out/ and config.yaml live where you run it
PAGES = HERE / "data" / "pages.json"
OUT = HERE / "data" / "candidates.json"

CFG = yaml.safe_load((HERE / "config.yaml").read_text()) if (HERE / "config.yaml").exists() else {}
SL = CFG.get("shortlist") or {}
TOP_K = SL.get("candidates_per_paragraph", 4)   # candidate targets shown to Jev
MIN_SIM = SL.get("min_similarity", 0.25)        # below this, not worth asking about
MIN_WORDS = SL.get("min_words", 25)             # passage must be substantial enough
# English sites: all-MiniLM-L6-v2. Any other language:
# paraphrase-multilingual-MiniLM-L12-v2 (English-only embeddings rate every
# foreign paragraph as "similar", so the similarity cutoff stops filtering).
EMBED_MODEL = SL.get("embedding_model", "all-MiniLM-L6-v2")
EXCLUDE = [re.compile(p) for p in (CFG.get("exclude_sources") or [])]
JEV_COST_PER_CALL = 0.00004  # measured: ~900 input tokens at $0.042/M


def target_text(p):
    """What a target page is 'about', for matching purposes."""
    bits = [p["title"], p["h1"], p["description"]]
    if p["passages"]:
        bits.append(p["passages"][0]["text"][:300])
    return " ".join(b for b in bits if b)


def main():
    pages = json.loads(PAGES.read_text())
    model = SentenceTransformer(EMBED_MODEL)
    print(f"embedding model: {EMBED_MODEL}")

    # Embed every page as a potential link target.
    tgt_vecs = model.encode(
        [target_text(p) for p in pages], normalize_embeddings=True,
        batch_size=64, show_progress_bar=False,
    )

    # Flatten every passage as a potential link source.
    src = []
    skipped = 0
    for pi, p in enumerate(pages):
        path = "/" + p["canon"].split("/", 1)[1]
        if any(rx.search(path) for rx in EXCLUDE):
            skipped += 1
            continue  # still a valid target, just never a source
        for ps in p["passages"]:
            if len(ps["text"].split()) >= MIN_WORDS:
                src.append({"page_i": pi, "p_i": ps["i"], "text": ps["text"]})
    if EXCLUDE:
        print(f"source pages excluded by exclude_sources: {skipped}")
    print(f"passages eligible: {len(src):,} of "
          f"{sum(len(p['passages']) for p in pages):,}")

    src_vecs = model.encode(
        [s["text"] for s in src], normalize_embeddings=True,
        batch_size=64, show_progress_bar=False,
    )

    sims = src_vecs @ tgt_vecs.T  # cosine, both normalised

    candidates = []
    for n, s in enumerate(src):
        page = pages[s["page_i"]]
        row = sims[n].copy()

        # Never propose: the page itself, or a target it already links to.
        row[s["page_i"]] = -1
        for ti, t in enumerate(pages):
            if t["canon"] in page["outlinks"]:
                row[ti] = -1

        order = np.argsort(-row)[:TOP_K]
        picks = [int(i) for i in order if row[i] >= MIN_SIM]
        if not picks:
            continue

        candidates.append({
            "source_url": page["url"],
            "source_canon": page["canon"],
            "source_title": page["title"],
            "p_i": s["p_i"],
            "text": s["text"],
            "targets": [
                {
                    "url": pages[i]["url"],
                    "canon": pages[i]["canon"],
                    "title": pages[i]["title"],
                    "h1": pages[i]["h1"],
                    "description": pages[i]["description"],
                    "inbound_links": pages[i]["inbound_links"],
                    "sim": round(float(sims[n][i]), 3),
                }
                for i in picks
            ],
        })

    OUT.write_text(json.dumps(candidates, indent=1))
    tot = sum(len(c["targets"]) for c in candidates)
    print(f"passages with candidates: {len(candidates):,}")
    print(f"total pairs to judge: {tot:,}")
    print(f"one Jev call per passage = {len(candidates):,} calls, "
          f"about ${len(candidates) * JEV_COST_PER_CALL:.2f} for score.py")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
