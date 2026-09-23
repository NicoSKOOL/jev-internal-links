#!/usr/bin/env python3
"""Step 6: build the review deliverables.

Output: out/internal-links.csv  (approve/reject in a spreadsheet)
        out/internal-links.html (read the proposed edit in context)
"""
import csv
import html
import json
from collections import defaultdict
from pathlib import Path

from _env import WORKDIR

HERE = WORKDIR  # data/, cache/, out/ and config.yaml live where you run it
LINKS = HERE / "data" / "links.json"
PAGES = HERE / "data" / "pages.json"
OUT = HERE / "out"


def main():
    links = json.loads(LINKS.read_text())
    pages = {p["canon"]: p for p in json.loads(PAGES.read_text())}
    OUT.mkdir(exist_ok=True)

    links.sort(key=lambda x: (x["source_canon"], x["p_i"]))

    # CSV for approval.
    csv_path = OUT / "internal-links.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["approve", "action", "confidence", "source_url",
                    "paragraph_no", "anchor_text", "target_url",
                    "target_inbound_before", "reader_stage", "score"])
        for c in links:
            w.writerow(["", c["action"], f"{c['confidence']:.2f}", c["source"],
                        c["p_i"], c["anchor"], c["target"],
                        c["target_inbound_before"], c["reader_stage"],
                        f"{c['final_score']:.2f}"])

    # Inbound before/after, the clearest picture of what changes.
    gain = defaultdict(int)
    for c in links:
        gain[c["target_canon"]] += 1
    movers = sorted(gain.items(), key=lambda kv: -kv[1])[:15]

    by_source = defaultdict(list)
    for c in links:
        by_source[c["source_canon"]].append(c)

    rows = []
    for src in sorted(by_source):
        items = by_source[src]
        rows.append(f'<h3>{html.escape(src)} '
                    f'<span class="n">{len(items)} new link'
                    f'{"s" if len(items) > 1 else ""}</span></h3>')
        for c in items:
            a = html.escape(c["anchor"])
            para = html.escape(c["text"])
            para = para.replace(
                a, f'<a href="{html.escape(c["target"])}">{a}</a>', 1)
            badge = ("apply" if c["action"] == "apply" else "review")
            rows.append(f"""
<div class="card {badge}">
  <div class="meta">
    <span class="badge {badge}">{badge}</span>
    <span>confidence {c['confidence']:.2f}</span>
    <span>para #{c['p_i']}</span>
    <span>reader: {c['reader_stage']}</span>
    <span>target had {c['target_inbound_before']} inbound</span>
  </div>
  <p class="para">{para}</p>
  <div class="tgt">&rarr; {html.escape(c['target'])}</div>
</div>""")

    mover_rows = "".join(
        f"<tr><td>{html.escape(u)}</td>"
        f"<td class='num'>{pages[u]['inbound_links'] if u in pages else '?'}</td>"
        f"<td class='num'>+{n}</td>"
        f"<td class='num'><b>{(pages[u]['inbound_links'] if u in pages else 0) + n}"
        f"</b></td></tr>"
        for u, n in movers
    )

    doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Internal link plan</title><style>
body{{font:15px/1.6 -apple-system,Segoe UI,sans-serif;max-width:920px;margin:40px auto;padding:0 20px;color:#16201c}}
h1{{font-size:26px;color:#0b3d2c;margin-bottom:4px}}
.sub{{color:#6b7c74;margin-top:0}}
h3{{font-size:14px;font-family:ui-monospace,Menlo,monospace;margin:26px 0 8px;color:#0b3d2c;border-bottom:1px solid #dce5e0;padding-bottom:5px}}
.n{{float:right;color:#6b7c74;font-weight:400}}
.card{{border:1px solid #dce5e0;border-left:4px solid #9fb8ae;border-radius:6px;padding:12px 14px;margin:8px 0;background:#fbfdfc}}
.card.apply{{border-left-color:#12604a}}
.card.review{{border-left-color:#c9962c}}
.meta{{font-size:11.5px;color:#6b7c74;display:flex;gap:14px;flex-wrap:wrap;margin-bottom:7px}}
.badge{{font-weight:700;text-transform:uppercase;letter-spacing:.4px}}
.badge.apply{{color:#12604a}} .badge.review{{color:#c9962c}}
.para{{margin:0}} a{{color:#12604a;font-weight:600}}
.tgt{{font-family:ui-monospace,Menlo,monospace;font-size:11.5px;color:#6b7c74;margin-top:7px}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin:14px 0 30px}}
th{{background:#0b3d2c;color:#fff;text-align:left;padding:7px}}
td{{border:1px solid #dce5e0;padding:6px 7px}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
.stats{{display:flex;gap:26px;flex-wrap:wrap;background:#f1f6f3;padding:14px 18px;border-radius:8px;margin:18px 0}}
.stat b{{display:block;font-size:22px;color:#0b3d2c}}
.stat span{{font-size:11.5px;color:#6b7c74}}
</style></head><body>
<h1>Internal link plan</h1>
<p class="sub">{html.escape(links[0]['source_canon'].split('/')[0]) if links else ''} &middot; {len(links)} proposed links across
{len(by_source)} pages</p>

<div class="stats">
  <div class="stat"><b>{len(links)}</b><span>proposed links</span></div>
  <div class="stat"><b>{sum(1 for c in links if c['action'] == 'apply')}</b>
    <span>high confidence</span></div>
  <div class="stat"><b>{sum(1 for c in links if c['action'] == 'review')}</b>
    <span>needs your call</span></div>
  <div class="stat"><b>{len(by_source)}</b><span>pages edited</span></div>
  <div class="stat"><b>{len(gain)}</b><span>pages receiving links</span></div>
  <div class="stat"><b>{sum(1 for c in links if c['target_inbound_before'] <= 1)}</b>
    <span>to near-orphan pages</span></div>
</div>

<h2>Biggest gains</h2>
<table><tr><th>Page</th><th class="num">Inbound now</th>
<th class="num">New</th><th class="num">After</th></tr>{mover_rows}</table>

<h2>Every proposed link</h2>
<p class="sub">The paragraph is shown exactly as it would read. Nothing is
reworded: the anchor is text already on the page.</p>
{''.join(rows)}
</body></html>"""

    (OUT / "internal-links.html").write_text(doc)
    print(f"wrote {csv_path}")
    print(f"wrote {OUT / 'internal-links.html'}")
    print(f"{len(links)} links across {len(by_source)} source pages")


if __name__ == "__main__":
    main()
