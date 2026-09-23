#!/usr/bin/env python3
"""One command: find internal linking opportunities on a site and build the report.

Usage:
    python run.py https://example.com                 # finds the sitemap itself
    python run.py https://example.com/sitemap.xml
    python run.py https://example.com --max-cost 5    # refuse to spend more than $5
    python run.py https://example.com --refresh       # re-crawl and re-ask Jev

Everything lands in ./jev-links/<domain>/ (or --dir). Re-running is cheap:
Jev answers and anchor texts are cached, so only new or changed paragraphs cost money.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import requests

SCRIPTS = Path(__file__).parent
SKILL = SCRIPTS.parent
UA = {"User-Agent": "Mozilla/5.0 (compatible; internal-link-auditor/1.0)"}
MULTILINGUAL = "paraphrase-multilingual-MiniLM-L12-v2"
JEV_PER_CALL, ANCHOR_PER_LINK = 0.00004, 0.0017


def find_sitemap(url):
    if url.endswith(".xml"):
        return url
    base = f"{urlparse(url).scheme or 'https'}://{urlparse(url).netloc or url}"
    try:
        robots = requests.get(base + "/robots.txt", headers=UA, timeout=15).text
        m = re.search(r"(?im)^sitemap:\s*(\S+)", robots)
        if m:
            return m.group(1)
    except requests.RequestException:
        pass
    for path in ("/sitemap.xml", "/sitemap-index.xml", "/sitemap_index.xml"):
        try:
            r = requests.get(base + path, headers=UA, timeout=15)
            if r.ok and "<loc>" in r.text:
                return base + path
        except requests.RequestException:
            pass
    sys.exit(f"No sitemap found for {base}. Pass the sitemap URL directly.")


def load_key(workdir):
    if os.environ.get("OPENROUTER_API_KEY"):
        return
    for f in (workdir / ".env", Path.home() / ".config" / "jev-internal-links" / ".env"):
        if f.exists():
            for line in f.read_text().splitlines():
                if line.startswith("OPENROUTER_API_KEY="):
                    os.environ["OPENROUTER_API_KEY"] = line.split("=", 1)[1].strip().strip("'\"")
                    return
    sys.exit(
        "No OpenRouter key found. Create one at https://openrouter.ai/keys, then run:\n"
        "  mkdir -p ~/.config/jev-internal-links && \\\n"
        "  echo 'OPENROUTER_API_KEY=sk-or-...' > ~/.config/jev-internal-links/.env && \\\n"
        "  chmod 600 ~/.config/jev-internal-links/.env"
    )


def step(name, *args):
    print(f"\n=== {name} ===", flush=True)
    r = subprocess.run([sys.executable, str(SCRIPTS / name), *args], env=os.environ)
    if r.returncode:
        sys.exit(f"{name} failed (exit {r.returncode}). Fix the error above, then re-run: "
                 f"finished steps are cached.")


def set_config(cfg_path, key_path, value):
    """Minimal YAML edit that keeps the comments in config.yaml."""
    import yaml
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    node = cfg
    for k in key_path[:-1]:
        node = node.setdefault(k, {})
    if node.get(key_path[-1]) == value:
        return
    text = cfg_path.read_text()
    if re.search(r"(?m)^\s*embedding_model:", text):
        text = re.sub(r"(?m)^(\s*embedding_model:).*$", rf"\1 {value}", text)
    elif re.search(r"(?m)^shortlist:", text):
        text = re.sub(r"(?m)^shortlist:\s*$", f"shortlist:\n  embedding_model: {value}", text)
    else:
        text += f"\nshortlist:\n  embedding_model: {value}\n"
    cfg_path.write_text(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("site", help="site URL or sitemap URL")
    ap.add_argument("--dir", help="project folder (default ./jev-links/<domain>)")
    ap.add_argument("--max-cost", type=float, default=3.0, help="stop before spending more than this (USD)")
    ap.add_argument("--refresh", action="store_true", help="re-crawl even if pages were crawled before")
    ap.add_argument("--no-open", action="store_true", help="don't open the dashboard at the end")
    a = ap.parse_args()

    domain = urlparse(a.site if "//" in a.site else "https://" + a.site).netloc.replace("www.", "")
    workdir = Path(a.dir or Path.cwd() / "jev-links" / domain).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    os.environ["JEV_WORKDIR"] = str(workdir)
    print(f"project folder: {workdir}")
    load_key(workdir)

    cfg = workdir / "config.yaml"
    if not cfg.exists():
        shutil.copy(SKILL / "config.template.yaml", cfg)
        print("created config.yaml from the template (edit money_pages for better results)")

    pages_f = workdir / "data" / "pages.json"
    if a.refresh or not pages_f.exists():
        step("crawl.py", find_sitemap(a.site if "//" in a.site else "https://" + a.site))
    pages = json.loads(pages_f.read_text())

    langs = Counter(p.get("lang") or "?" for p in pages)
    main_lang = langs.most_common(1)[0][0]
    print(f"\nsite language: {main_lang} ({dict(langs.most_common(3))})")
    if main_lang not in ("en", "?"):
        set_config(cfg, ["shortlist", "embedding_model"], MULTILINGUAL)
        print(f"non-English site: using {MULTILINGUAL} for the shortlist")

    step("shortlist.py")
    n_calls = len(json.loads((workdir / "data" / "candidates.json").read_text()))
    # Allocation usually keeps ~12% of paragraphs; budget anchors on that.
    est = n_calls * JEV_PER_CALL + n_calls * 0.12 * ANCHOR_PER_LINK
    print(f"\nestimated cost: ${est:.2f} (Jev ${n_calls * JEV_PER_CALL:.2f} + anchors), "
          f"cap ${a.max_cost:.2f}")
    if est > a.max_cost:
        sys.exit(f"Estimate is over --max-cost. Re-run with --max-cost {est * 1.3:.0f} "
                 f"or add exclude_sources patterns to config.yaml.")

    step("score.py")
    step("allocate.py")
    step("anchors.py")
    step("report.py")
    step("decisions.py")

    links = json.loads((workdir / "data" / "links.json").read_text())
    dash = workdir / "out" / "jev-dashboard.html"
    print(f"""
=== done ===
{len(links)} internal links proposed across {len({l['source_canon'] for l in links})} pages
  {sum(l['action'] == 'apply' for l in links)} high confidence, {sum(l['action'] == 'review' for l in links)} to review

report:      {dash}
spreadsheet: {workdir / 'out' / 'jev-decisions.xlsx'}
approve:     {workdir / 'out' / 'internal-links.csv'}""")
    if not a.no_open:
        webbrowser.open(dash.as_uri())


if __name__ == "__main__":
    main()
