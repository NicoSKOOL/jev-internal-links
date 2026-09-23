#!/usr/bin/env python3
"""Step 3: ask Jev, per passage, which shortlisted page the reader needs next.

One call per passage, several questions evaluated in parallel against the same
state. Every raw response is cached to disk, so re-runs cost nothing and the
judgements stay auditable (Jev is explicitly non-deterministic).

Usage: python score.py [--limit N]
Output: data/judgements.json, cache/<hash>.json
"""
import argparse
import hashlib
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from _env import WORKDIR, JEV_MODEL

HERE = WORKDIR  # data/, cache/, out/ and config.yaml live where you run it
CANDIDATES = HERE / "data" / "candidates.json"
OUT = HERE / "data" / "judgements.json"
CACHE = HERE / "cache"

URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = JEV_MODEL
WORKERS = 10

lock = threading.Lock()
stats = {"calls": 0, "cached": 0, "cost": 0.0, "in_tok": 0, "errors": 0}


def build_state(c):
    """Small, focused state. Jev degrades on large irrelevant context."""
    lines = [
        f"PAGE: {c['source_title']}",
        "",
        "PARAGRAPH ON THAT PAGE:",
        c["text"],
        "",
        "CANDIDATE PAGES TO LINK TO:",
    ]
    for n, t in enumerate(c["targets"], 1):
        desc = (t["description"] or t["h1"] or "")[:200]
        lines.append(f"{n}. {t['title']} — {desc}")
    return "\n".join(lines)


def build_questions(c):
    opts = {
        str(n): f"{t['title']}"[:120] for n, t in enumerate(c["targets"], 1)
    }
    opts["none"] = "None of these pages is a genuinely useful next step here"
    return {
        "best_target": {
            "type": "choice",
            "instructions": (
                "A reader has just read this paragraph. Which candidate page "
                "is the most useful next step for them? Choose 'none' unless "
                "one page clearly deepens the specific topic of THIS paragraph."
            ),
            "criteria": opts,
        },
        "link_warranted": {
            "type": "noul",
            "instructions": (
                "This paragraph raises a specific topic, tool or claim that a "
                "reader would plausibly want to explore on another page."
            ),
        },
        "anchor_available": {
            "type": "noul",
            "instructions": (
                "This paragraph contains a specific noun phrase that could "
                "become natural anchor text without rewriting the sentence."
            ),
        },
        "reader_stage": {
            "type": "choice",
            "instructions": "Where is the reader of this paragraph?",
            "criteria": {
                "learning": "Understanding a concept for the first time",
                "comparing": "Weighing options, tools or approaches",
                "ready": "Ready to act, buy or sign up",
            },
        },
        "commercial": {
            "type": "noul",
            "instructions": (
                "This paragraph is promotional or sales-oriented rather than "
                "educational."
            ),
        },
    }


def call(c, key, attempt=0):
    body = {
        "model": MODEL,
        "state": build_state(c),
        "questions": build_questions(c),
    }
    h = hashlib.sha256(
        json.dumps(body, sort_keys=True).encode()
    ).hexdigest()[:20]
    cached = CACHE / f"{h}.json"
    data = None
    if cached.exists():
        try:
            data = json.loads(cached.read_text())
        except ValueError:
            cached.unlink(missing_ok=True)  # half-written by an interrupted run
    if data is not None:
        with lock:
            stats["cached"] += 1
            stats["cost"] += (data.get("usage") or {}).get("cost", 0) or 0
            stats["in_tok"] += (data.get("usage") or {}).get("input_tokens", 0) or 0
        return data

    try:
        r = requests.post(
            URL,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        if r.status_code in (429, 500, 502, 503) and attempt < 5:
            time.sleep((2 ** attempt) + random.random())
            return call(c, key, attempt + 1)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        with lock:
            stats["errors"] += 1
        return {"error": str(e)}

    tmp = cached.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(cached)  # atomic: a reader never sees a half-written file
    with lock:
        stats["calls"] += 1
        u = data.get("usage", {})
        stats["cost"] += u.get("cost", 0) or 0
        stats["in_tok"] += u.get("input_tokens", 0) or 0
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY not set")

    CACHE.mkdir(exist_ok=True)
    cands = json.loads(CANDIDATES.read_text())
    if args.limit:
        cands = cands[: args.limit]
    print(f"judging {len(cands):,} passages with {MODEL}")

    results = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for n, (c, resp) in enumerate(
            zip(cands, ex.map(lambda x: call(x, key), cands)), 1
        ):
            results.append({"candidate": c, "response": resp})
            if n % 250 == 0:
                el = time.time() - t0
                print(f"  {n:,}/{len(cands):,}  ${stats['cost']:.4f}  "
                      f"{el:.0f}s  errors={stats['errors']}")

    OUT.write_text(json.dumps(results, indent=1))
    print(f"\ncalls: {stats['calls']:,}  from cache: {stats['cached']:,}  "
          f"errors: {stats['errors']}")
    print(f"input tokens: {stats['in_tok']:,}")
    print(f"cost: ${stats['cost']:.4f} (includes cached calls, paid on an earlier run)")
    print(f"elapsed: {time.time() - t0:.0f}s")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
