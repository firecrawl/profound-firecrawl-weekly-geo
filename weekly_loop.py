#!/usr/bin/env python3
"""Weekly GEO list: Profound newly cited pages -> Firecrawl notes -> short list.

1. Pull: Profound citations report (v2), entity=page, last 7 days, with first_cited_at.
2. Select: pages first cited inside the window, not social, cited at least --min-count times.
3. Read: Firecrawl /scrape with Markdown plus a JSON schema, so every page gets the same notes.
4. Sort: plain rules decide what to do with each page.
5. Engines: Profound citations per AI engine (ChatGPT, Google AI Overviews, ...) for each page to act on.
6. Google demand: Firecrawl Alexandria (Serpstat) gives each site's Google traffic, the monthly
   Google searches for each page's target search, and whether the page itself ranks in Google.
Writes notes.json and weekly_list.md to --out. Sends nothing and contacts no one.
"""
import argparse, json, os, re, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

FC = "https://api.firecrawl.dev/v2/scrape"
PROFOUND = "https://api.tryprofound.com/v2/reports/citations"

SCHEMA = {
    "type": "object",
    "properties": {
        "page_type": {"type": "string", "enum": ["listicle", "comparison", "tutorial", "docs", "news", "product_page", "other"]},
        "main_topic": {"type": "string", "description": "What the page is about, in one short sentence"},
        "tools_named": {"type": "array", "items": {"type": "string"}, "description": "Products or tools the page names as options, in page order"},
        "target_search": {"type": "string", "description": "The Google search this page is written to rank for, 2 to 5 words, lowercase"},
        "published_date": {"type": ["string", "null"]},
        "updated_date": {"type": ["string", "null"]},
        "has_comparison_table": {"type": "boolean"},
        "has_faq": {"type": "boolean"},
    },
    "required": ["page_type", "main_topic", "tools_named", "target_search"],
}

PRIORITY = {"you are missing from this list": 3, "check how you are described": 2, "refresh your page": 1, "watch": 0, "skip": -1}


def post(url, body, headers, timeout=120):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **headers})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def profound(body):
    """Every row of a Profound v2 citations report, following next_cursor, 50 rows per call."""
    rows, cursor = [], None
    while True:
        r = post(PROFOUND, {**body, "limit": 50, **({"cursor": cursor} if cursor else {})}, {"X-API-Key": os.environ["PROFOUND_API_KEY"]})
        rows += r["data"]
        cursor = r["info"].get("next_cursor")
        if not cursor:
            return rows


def pull_pages(category_id, start, end):
    data = profound({"category_id": category_id, "start_date": start, "end_date": end, "entity": "page",
                     "metrics": ["count", "citation_share", "rank", "first_cited_at"]})
    return [[d["rank"], d["page"]["name"], d["citation_category"], d["count"], d["citation_share"], d["first_cited_at"]] for d in data]


def pull_engines(category_id, start, end, pages):
    """Citations per AI engine for each page, as [page, engine, count] rows."""
    rows = []
    for i in range(0, len(pages), 20):
        data = profound({"category_id": category_id, "start_date": start, "end_date": end, "entity": "page",
                         "group_by": ["model"], "metrics": ["count"], "page_filter": pages[i:i + 20]})
        rows += [[d["page"]["name"], d["model"]["name"], d["count"]] for d in data]
    return rows


def select(rows, start, min_count):
    return [r for r in rows if r[5][:10] >= start and r[2] != "social" and r[3] >= min_count]


def read_page(row, brand, key):
    body = {"url": "https://" + row[1], "onlyMainContent": True, "timeout": 45000,
            "formats": ["markdown", {"type": "json", "schema": SCHEMA, "prompt": "Notes on this page for a marketing team."}]}
    err = None
    for attempt in range(3):
        try:
            d = post(FC, body, {"Authorization": "Bearer " + key}).get("data") or {}
            notes = d.get("json") or {}
            md = d.get("markdown") or ""
            meta = d.get("metadata") or {}
            tools = notes.get("tools_named") or []
            hits = [i + 1 for i, t in enumerate(tools) if brand.lower() in t.lower()]
            return {"page": row[1], "category": row[2], "count": row[3], "share": row[4],
                    "first_cited_at": row[5][:10], "ok": bool(notes), "words": len(md.split()),
                    "status": meta.get("statusCode"), **notes,
                    "brand_mentions": len(re.findall(r"\b" + re.escape(brand) + r"\b", md, re.I)),
                    "brand_position": hits[0] if hits else None}
        except Exception as e:
            err = str(e)[:120]
            time.sleep(3 + attempt * 5)
    return {"page": row[1], "category": row[2], "count": row[3], "first_cited_at": row[5][:10], "ok": False, "error": err}


def action(n):
    if not n.get("ok") or (n.get("status") or 200) >= 400:
        return "skip"
    if n["category"] == "owned":
        return "refresh your page"
    if n.get("brand_mentions"):
        return "check how you are described"
    if n.get("page_type") in ("listicle", "comparison") and len(n.get("tools_named") or []) >= 3:
        return "you are missing from this list"
    return "watch"


def alexandria(provider, capability, options, key):
    """Retries rate limits and other failures. A 502 is Serpstat saying it has no data, so it returns at once."""
    body = {"alexandria": {"provider": provider, "capability": capability, "options": options}}
    for attempt in range(4):
        res = post(FC, body, {"Authorization": "Bearer " + key}).get("data", {}).get("alexandria", [{}])[0]
        error = res.get("error")
        if not error:
            return res.get("data") or {}
        if error.get("status") == 502:
            return {"no_data": True}
        time.sleep(max(num(error.get("retryAfterSeconds")), 2) + attempt * 5)
    return {"error": error}


def host(page):
    return re.sub(r"^www\.", "", page.split("/")[0])


def num(v):
    return v if isinstance(v, (int, float)) else 0


def site_traffic(domains, key):
    """Firecrawl Alexandria, Serpstat domains/info: estimated monthly US Google traffic, 30 domains per call."""
    out = {}
    for i in range(0, len(domains), 30):
        data = alexandria("serpstat", "domains/info", {"domains": domains[i:i + 30], "se": "g_us"}, key)
        for d in (data.get("result") or {}).get("data") or []:
            out[d["domain"]] = num(d.get("traff"))
    return out


def search_volume(keywords, key):
    """Firecrawl Alexandria, Serpstat keywords/info: monthly US Google searches, 150 keywords per call."""
    out = {}
    for i in range(0, len(keywords), 150):
        data = alexandria("serpstat", "keywords/info", {"keywords": keywords[i:i + 150], "se": "g_us"}, key)
        for d in (data.get("result") or {}).get("data") or []:
            out[d.get("keyword", "").lower()] = num(d.get("region_queries_count"))
    return out


def page_in_google(page, key):
    """Firecrawl Alexandria, Serpstat domains/top-pages: Google searches this page ranks for in the US top 100.
    Serpstat answers an error when it has no rankings recorded for the page, so that reads as zero."""
    slug = [part for part in page.split("?")[0].split("/") if part][-1]
    data = alexandria("serpstat", "domains/top-pages",
                      {"domain": host(page), "se": "g_us", "filters": {"url_contain": slug}, "size": 5}, key)
    if data.get("error"):
        return {"error": data["error"].get("code")}
    rows = (data.get("result") or {}).get("data") or []
    return {"ranked_keywords": sum(num(r.get("keywords")) for r in rows), "google_traffic": sum(num(r.get("traffic")) for r in rows)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", help="saved Profound page export (JSON rows); omit to pull with PROFOUND_API_KEY")
    ap.add_argument("--engine-rows", help="saved [page, engine, count] rows; omit to pull with PROFOUND_API_KEY")
    ap.add_argument("--category-id")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--brand", default="Firecrawl")
    ap.add_argument("--min-count", type=int, default=3)
    ap.add_argument("--short-list", type=int, default=10)
    ap.add_argument("--out", default="run")
    a = ap.parse_args()
    key = os.environ["FIRECRAWL_API_KEY"]
    os.makedirs(a.out, exist_ok=True)

    rows = json.load(open(a.rows)) if a.rows else pull_pages(a.category_id, a.start, a.end)
    picked = select(rows, a.start, a.min_count)
    with ThreadPoolExecutor(8) as ex:
        notes = list(ex.map(lambda r: read_page(r, a.brand, key), picked))
    for n in notes:
        n["action"] = action(n)

    acting = [n for n in notes if PRIORITY[n["action"]] > 0]
    pages = [n["page"] for n in acting]
    engine_rows = json.load(open(a.engine_rows)) if a.engine_rows else pull_engines(a.category_id, a.start, a.end, pages)
    for n in acting:
        n["engines"] = {e: c for p, e, c in engine_rows if p == n["page"]}

    traffic = site_traffic(sorted({host(p) for p in pages}), key)
    volume = search_volume(sorted({(n.get("target_search") or "").lower() for n in acting if n.get("target_search")}), key)
    for n in acting:
        n["site_traffic"] = traffic.get(host(n["page"]), 0)
        n["search_volume"] = volume.get((n.get("target_search") or "").lower())

    notes.sort(key=lambda n: (-PRIORITY[n["action"]], -n.get("site_traffic", 0), -n["count"]))
    for n in notes:
        if n["action"] == "you are missing from this list":
            n["google"] = page_in_google(n["page"], key)
    short = [n for n in notes if PRIORITY[n["action"]] > 0][: a.short_list]

    json.dump({"window": [a.start, a.end], "cited_pages": len(rows), "picked": len(picked), "notes": notes},
              open(os.path.join(a.out, "notes.json"), "w"), indent=1)
    lines = [f"# Pages to look at, {a.start} to {a.end}", "",
             f"{len(rows)} pages cited; {len(picked)} first cited this week, not social, cited {a.min_count}+ times.", "",
             "| Page | Action | ChatGPT | AI Overviews | Other engines | Site's US Google traffic | Target search | Its monthly Google searches | Page ranks in Google's top 100 for |",
             "|---|---|---|---|---|---|---|---|---|"]
    for n in short:
        e = n.get("engines") or {}
        other = sum(c for k, c in e.items() if k not in ("ChatGPT", "Google AI Overviews"))
        g = n.get("google") or {}
        ranks = "check failed" if g.get("error") else f"{g['ranked_keywords']} searches" if g.get("ranked_keywords") else "none recorded"
        lines.append(f"| {n['page']} | {n['action']} | {e.get('ChatGPT', 0)} | {e.get('Google AI Overviews', 0)} | {other} | "
                     f"{n['site_traffic']:,} | {n.get('target_search')} | {'no data' if n['search_volume'] is None else format(n['search_volume'], ',')} | {ranks} |")
    open(os.path.join(a.out, "weekly_list.md"), "w").write("\n".join(lines) + "\n")
    counts = {}
    for n in notes:
        counts[n["action"]] = counts.get(n["action"], 0) + 1
    print(json.dumps({"cited_pages": len(rows), "picked": len(picked), "read_ok": sum(n["ok"] for n in notes), "actions": counts}))


if __name__ == "__main__":
    sys.exit(main())
