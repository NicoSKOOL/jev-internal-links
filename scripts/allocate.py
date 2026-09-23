#!/usr/bin/env python3
"""Step 4: decide which proposed links actually get made.

No model calls. Pure rules from config.yaml, applied to Jev's judgements.
This is where the value is: scoring each pair independently and taking the
top N would dogpile a handful of targets and starve everything else.

Output: data/allocation.json
"""
import heapq
import json
from collections import defaultdict
from pathlib import Path

import yaml

from _env import WORKDIR

HERE = WORKDIR  # data/, cache/, out/ and config.yaml live where you run it
CFG = yaml.safe_load((HERE / "config.yaml").read_text())
JUDGE = HERE / "data" / "judgements.json"
PAGES = HERE / "data" / "pages.json"
OUT = HERE / "data" / "allocation.json"
CAPLOG = HERE / "data" / "cap_log.json"


def is_money(canon, cfg):
    """Exact path match: '/pricing/' matches only that page, never '/x/pricing/'."""
    path = "/" + canon.split("/", 1)[1] if "/" in canon else "/"
    return any(path == "/" + m.strip("/") + "/" if m.strip("/") else path == "/"
               for m in cfg.get("money_pages") or [])


def main():
    judgements = json.loads(JUDGE.read_text())
    pages = {p["canon"]: p for p in json.loads(PAGES.read_text())}
    th, w, caps = CFG["thresholds"], CFG["weights"], CFG["caps"]

    max_inbound = max(p["inbound_links"] for p in pages.values()) or 1

    pool = []
    rejected = defaultdict(int)
    for r in judgements:
        ans = r.get("response", {}).get("answers")
        if not ans:
            rejected["api_error"] += 1
            continue
        bt = ans["best_target"]
        if bt["choice"] == "none":
            rejected["model_said_none"] += 1
            continue
        if bt["confidence"] < th["min_confidence"]:
            rejected["low_confidence"] += 1
            continue
        if ans["link_warranted"]["noul"] < th["min_link_warranted"]:
            rejected["not_warranted"] += 1
            continue
        if ans["anchor_available"]["noul"] < th["min_anchor_available"]:
            rejected["no_natural_anchor"] += 1
            continue

        c = r["candidate"]
        tgt = c["targets"][int(bt["choice"]) - 1]
        if tgt["canon"] == c["source_canon"]:
            rejected["self_link"] += 1
            continue

        # Fewer existing inbound links means a bigger win from a new one.
        orphan = 1 - (tgt["inbound_links"] / max_inbound)
        money = is_money(tgt["canon"], CFG)
        ready = ans["reader_stage"]["choice"] == "ready"

        score = (
            w["confidence"] * bt["confidence"]
            + w["link_warranted"] * ans["link_warranted"]["noul"]
            + w["anchor_available"] * ans["anchor_available"]["noul"]
            + w["similarity"] * tgt["sim"]
            + w["orphan_boost"] * orphan
            + (w["money_page_match"] if (money and ready) else 0)
            + (w["commercial_penalty"] * ans["commercial"]["noul"])
        )

        pool.append({
            "score": round(score, 4),
            "source": c["source_url"],
            "source_canon": c["source_canon"],
            "source_title": c["source_title"],
            "p_i": c["p_i"],
            "text": c["text"],
            "target": tgt["url"],
            "target_canon": tgt["canon"],
            "target_title": tgt["title"],
            "target_inbound_before": tgt["inbound_links"],
            "confidence": bt["confidence"],
            "link_warranted": ans["link_warranted"]["noul"],
            "anchor_available": ans["anchor_available"]["noul"],
            "commercial": ans["commercial"]["noul"],
            "reader_stage": ans["reader_stage"]["choice"],
            "similarity": tgt["sim"],
            "is_money_page": money,
        })

    # Greedy allocation with diminishing returns. A target's 2nd link is worth
    # decay x its 1st, so after a few the next candidate elsewhere wins.
    heap = [(-c["score"], n) for n, c in enumerate(pool)]
    heapq.heapify(heap)

    per_source = defaultdict(int)
    per_target = defaultdict(int)
    used_passages = set()
    used_pairs = set()  # a page links to a given target once, not once per paragraph
    placed_idx = defaultdict(list)
    chosen = []
    capped = defaultdict(int)
    cap_reason = {}  # (source_canon, p_i, target_canon) -> which cap dropped it

    while heap:
        neg, n = heapq.heappop(heap)
        c = pool[n]
        eff = -neg
        decayed = c["score"] * (caps["target_decay"] ** per_target[c["target_canon"]])
        # If decay changed its value, re-queue at the true value and continue,
        # so the heap always pops the genuinely best remaining candidate.
        if abs(decayed - eff) > 1e-9:
            heapq.heappush(heap, (-decayed, n))
            continue

        key = (c["source_canon"], c["p_i"])
        if key in used_passages:
            continue
        pair = (c["source_canon"], c["target_canon"])
        if pair in used_pairs:
            capped["duplicate_pair"] += 1
            cap_reason[f"{key[0]}|{key[1]}|{c['target_canon']}"] = "duplicate_pair"
            continue
        if per_source[c["source_canon"]] >= caps["max_new_links_per_source"]:
            capped["source_full"] += 1
            cap_reason[f"{key[0]}|{key[1]}|{c['target_canon']}"] = "source_full"
            continue
        if per_target[c["target_canon"]] >= caps["max_new_links_per_target"]:
            capped["target_full"] += 1
            cap_reason[f"{key[0]}|{key[1]}|{c['target_canon']}"] = "target_full"
            continue
        if any(abs(c["p_i"] - q) < caps["min_passages_between"]
               for q in placed_idx[c["source_canon"]]):
            capped["too_close"] += 1
            cap_reason[f"{key[0]}|{key[1]}|{c['target_canon']}"] = "too_close"
            continue

        c = dict(c, final_score=round(decayed, 4))
        c["action"] = ("apply" if c["confidence"] >= th["auto_approve_confidence"]
                       else "review")
        chosen.append(c)
        used_passages.add(key)
        used_pairs.add(pair)
        per_source[c["source_canon"]] += 1
        per_target[c["target_canon"]] += 1
        placed_idx[c["source_canon"]].append(c["p_i"])

    chosen.sort(key=lambda x: -x["final_score"])
    OUT.write_text(json.dumps(chosen, indent=1))
    CAPLOG.write_text(json.dumps(cap_reason, indent=1))

    print(f"judgements in:        {len(judgements):,}")
    print("filtered out:")
    for k, v in sorted(rejected.items(), key=lambda x: -x[1]):
        print(f"   {k:22} {v:,}")
    print(f"survived filters:     {len(pool):,}")
    print("dropped by caps:")
    for k, v in sorted(capped.items(), key=lambda x: -x[1]):
        print(f"   {k:22} {v:,}")
    print(f"\nLINKS ALLOCATED:      {len(chosen):,}")
    print(f"  auto-apply:         {sum(1 for c in chosen if c['action'] == 'apply'):,}")
    print(f"  needs review:       {sum(1 for c in chosen if c['action'] == 'review'):,}")
    print(f"  source pages:       {len(per_source):,}")
    print(f"  target pages:       {len(per_target):,}")
    print(f"  max per target:     {max(per_target.values(), default=0)}")
    print(f"next step anchors.py: about ${len(chosen) * 0.0017:.2f} "
          f"(writing model, ~$0.0017 per allocated link)")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
