#!/usr/bin/env python3
"""Step 5: write anchor text for the allocated links only.

Jev cannot produce text, so a writing model handles this stage. It runs on the
368 survivors, not the 12,421 pairs we judged, which is where the saving is.

The anchor must already exist verbatim in the paragraph: we are placing a link,
not rewriting anyone's copy. Anything that fails that check is dropped.

Output: data/links.json
"""
import hashlib
import json
import os
import re
import unicodedata
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from _env import WORKDIR, WRITER_MODEL

HERE = WORKDIR  # data/, cache/, out/ and config.yaml live where you run it
ALLOC = HERE / "data" / "allocation.json"
OUT = HERE / "data" / "links.json"
CACHE = HERE / "cache" / "anchors"
PAGES = HERE / "data" / "pages.json"
URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = WRITER_MODEL
WORKERS = 8

lock = threading.Lock()
stats = {"ok": 0, "no_anchor": 0, "not_verbatim": 0, "bad_shape": 0,
         "errors": 0, "cost": 0.0, "topic_downgrade": 0}

BAD_OPENERS = {
    # English
    "is", "are", "was", "how", "use", "using", "optimizing", "getting", "the",
    "a", "an", "to", "and", "that", "this", "these", "it", "you", "your", "we",
    "see", "read", "learn", "click", "check", "find", "make", "build",
    # Spanish / Portuguese
    "el", "la", "los", "las", "un", "una", "es", "son", "se", "que", "para",
    "con", "y", "o", "este", "esta", "estos", "estas", "tiene", "hay", "haz",
    "o", "os", "um", "uma", "e", "sao", "com", "cómo", "como", "qué", "por",
    "sin", "del", "al", "su", "sus", "mi", "tu", "nuestro", "nuestra",
    # French / German / Italian
    "le", "les", "une", "est", "der", "die", "das", "ist", "il", "lo", "gli",
}
BANNED = {"click here", "read more", "this guide", "learn more", "here",
          "haz clic aquí", "aquí", "leer más", "más información"}
STOP = {"the", "and", "for", "with", "your", "you", "how", "what", "que",
        "con", "para", "los", "las", "del", "una", "por", "como", "des", "les"}


def stems(text):
    """Loose topic words: accents stripped, short prefixes so plurals match."""
    t = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return {w[:5] for w in re.findall(r"[a-z0-9]+", t) if len(w) >= 3 and w not in STOP}

PROMPT = """You are placing one internal link inside an existing paragraph.

PARAGRAPH:
{text}

LINK TARGET:
Title: {title}
About: {desc}

Choose the span of text in the paragraph that should become the clickable link.

Rules:
- The paragraph may be in any language. The anchor stays in that language.
- The anchor MUST be copied character-for-character from the paragraph above.
- It must be a NOUN PHRASE naming the thing the target page is about, for
  example "keyword research tool" or "Google AI Overviews".
- 2 to 6 words.
- It must NOT be a clause or a sentence fragment containing a verb, and must
  NOT start with a verb, an article or "the practice of". It must read
  naturally as a link on its own.
- It must name what the TARGET page is about, not just any noun in the text.
- Never "click here", "this guide", "here" or "read more".
- Do not pick a span inside the first 4 words of the paragraph.
- If the paragraph contains no clean noun phrase that matches the target,
  return null. Returning null is much better than a clumsy anchor.

Reply with only JSON: {{"anchor": "<exact span>"}} or {{"anchor": null}}"""


def ask(c, key):
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT.format(
            text=c["text"],
            title=c["target_title"],
            desc=(c.get("target_desc") or c["target_title"])[:200],
        )}],
        "max_tokens": 120,
        "temperature": 0,
    }
    h = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:20]
    cached = CACHE / f"{h}.json"
    try:
        d = None
        if cached.exists():
            try:
                d = json.loads(cached.read_text())
            except ValueError:
                cached.unlink(missing_ok=True)
        if d is None:
            r = requests.post(
                URL,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json=body, timeout=60,
            )
            r.raise_for_status()
            d = r.json()
            tmp = cached.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(d))
            tmp.replace(cached)
        # Content can come back null (refusal, truncation, filtered), so treat
        # a missing string as "no anchor" rather than letting it crash the run.
        txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        with lock:
            stats["cost"] += (d.get("usage", {}) or {}).get("cost", 0) or 0
    except Exception as e:
        with lock:
            stats["errors"] += 1
        return None

    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        with lock:
            stats["no_anchor"] += 1
        return None
    try:
        anchor = json.loads(m.group(0)).get("anchor")
    except Exception:
        with lock:
            stats["no_anchor"] += 1
        return None

    if not anchor:
        with lock:
            stats["no_anchor"] += 1
        return None
    if anchor not in c["text"]:
        with lock:
            stats["not_verbatim"] += 1
        return None

    # Enforce the rules in code, not just in the prompt.
    words = anchor.split()
    if not 2 <= len(words) <= 6:
        with lock:
            stats["bad_shape"] += 1
        return None
    if words[0].lower() in BAD_OPENERS or anchor.lower() in BANNED:
        with lock:
            stats["bad_shape"] += 1
        return None

    with lock:
        stats["ok"] += 1
    return anchor


def main():
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY not set")

    alloc = json.loads(ALLOC.read_text())
    CACHE.mkdir(parents=True, exist_ok=True)
    pages = {p["canon"]: p for p in json.loads(PAGES.read_text())}
    print(f"writing anchors for {len(alloc)} links with {MODEL}")

    t0 = time.time()
    out = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for n, (c, anchor) in enumerate(
            zip(alloc, ex.map(lambda x: ask(x, key), alloc)), 1
        ):
            if anchor:
                c = dict(c, anchor=anchor)
                # An anchor sharing no topic word with the target is often a
                # loose match. Keep it, but a human looks at it first.
                t = pages.get(c["target_canon"], {})
                about = " ".join([c["target_title"], t.get("h1", ""),
                                  t.get("description", ""),
                                  c["target_canon"].replace("-", " ")])
                if c["action"] == "apply" and not stems(anchor) & stems(about):
                    c["action"] = "review"
                    c["review_reason"] = "anchor shares no topic word with the target"
                    stats["topic_downgrade"] += 1
                # Show the edit exactly as it would appear.
                c["preview"] = c["text"].replace(
                    anchor, f"[{anchor}]({c['target']})", 1
                )
                out.append(c)
            if n % 100 == 0:
                print(f"  {n}/{len(alloc)}  ok={stats['ok']}  ${stats['cost']:.3f}")

    OUT.write_text(json.dumps(out, indent=1))
    print(f"\nanchors written:  {stats['ok']}")
    print(f"no natural span:  {stats['no_anchor']}")
    print(f"not verbatim:     {stats['not_verbatim']} (dropped)")
    print(f"bad shape:        {stats['bad_shape']} (dropped)")
    print(f"moved to review:  {stats['topic_downgrade']} (anchor off-topic for target)")
    print(f"errors:           {stats['errors']}")
    print(f"cost:             ${stats['cost']:.3f}")
    print(f"elapsed:          {time.time() - t0:.0f}s")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
