#!/usr/bin/env python3
"""Step 1: crawl a sitemap, extract page text and existing internal links.

Output: data/pages.json
"""
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from _env import WORKDIR
from bs4 import BeautifulSoup

if len(sys.argv) < 2:
    sys.exit("usage: python crawl.py https://yoursite.com/sitemap.xml")
SITEMAP = sys.argv[1]
OUT = WORKDIR / "data" / "pages.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; internal-link-auditor/1.0)"}
MIN_PASSAGE = 120  # chars; shorter paragraphs are not worth a link
DROP = re.compile(r"\b(cookie|newsletter|subscribe|all rights reserved)\b", re.I)


def canon(url):
    """Normalise to a comparable form: no scheme/www/query, trailing slash."""
    p = urlparse(url)
    path = p.path or "/"
    if not path.endswith("/"):
        path += "/"
    return p.netloc.replace("www.", "") + path


def fetch(url):
    try:
        r = requests.get(url, headers=UA, timeout=25)
        if r.status_code != 200:
            return {"url": url, "error": f"HTTP {r.status_code}"}
        return parse(url, r.text)
    except Exception as e:
        return {"url": url, "error": str(e)}


def parse(url, html):
    soup = BeautifulSoup(html, "lxml")
    lang = ((soup.html.get("lang") if soup.html else "") or "").split("-")[0].lower()
    for bad in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        bad.decompose()

    title = soup.title.get_text(strip=True) if soup.title else ""
    h1 = soup.h1.get_text(strip=True) if soup.h1 else ""
    md = soup.find("meta", attrs={"name": "description"})
    desc = md.get("content", "").strip() if md else ""

    main = soup.find("main") or soup.body or soup
    host = urlparse(url).netloc

    # Existing internal links, so we never propose a duplicate.
    outlinks = set()
    for a in main.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("mailto:", "tel:", "#")):
            continue
        full = urljoin(url, href)
        if urlparse(full).netloc.replace("www.", "") == host.replace("www.", ""):
            outlinks.add(canon(full))

    # Passages: paragraphs and list items long enough to host a link.
    passages = []
    for el in main.find_all(["p", "li"]):
        # Skip anything already containing a link: no room, and it is a signal
        # the author already routed the reader somewhere from here.
        if el.find("a"):
            continue
        txt = " ".join(el.get_text(" ", strip=True).split())
        if len(txt) < MIN_PASSAGE or DROP.search(txt):
            continue
        passages.append({"i": len(passages), "text": txt})

    body = " ".join(p["text"] for p in passages)
    return {
        "url": url,
        "canon": canon(url),
        "lang": lang,
        "title": title,
        "h1": h1,
        "description": desc,
        "passages": passages,
        "outlinks": sorted(outlinks),
        "word_count": len(body.split()),
    }


def sitemap_urls(url, depth=0):
    """Page URLs from a sitemap, following sitemap indexes (Astro, Yoast...)."""
    xml = requests.get(url, headers=UA, timeout=30).text
    locs = [l.strip() for l in re.findall(r"<loc>([^<]+)</loc>", xml)]
    if "<sitemapindex" in xml and depth < 3:
        return [u for child in locs for u in sitemap_urls(child, depth + 1)]
    return locs


def main():
    urls = list(dict.fromkeys(sitemap_urls(SITEMAP)))
    print(f"sitemap: {len(urls)} urls")

    pages = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for n, page in enumerate(ex.map(fetch, urls), 1):
            pages.append(page)
            if n % 25 == 0:
                print(f"  {n}/{len(urls)}")
            time.sleep(0.02)

    ok = [p for p in pages if "error" not in p]
    bad = [p for p in pages if "error" in p]

    # Inbound counts drive the "prioritise under-linked pages" rule later.
    inbound = {p["canon"]: 0 for p in ok}
    for p in ok:
        for t in p["outlinks"]:
            if t in inbound and t != p["canon"]:
                inbound[t] += 1
    for p in ok:
        p["inbound_links"] = inbound[p["canon"]]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(ok, indent=1))

    print(f"\nok: {len(ok)}  failed: {len(bad)}")
    for b in bad[:5]:
        print("  FAIL", b["url"], b["error"])
    print(f"passages: {sum(len(p['passages']) for p in ok):,}")
    print(f"orphans (0 inbound): {sum(1 for p in ok if p['inbound_links'] == 0)}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
