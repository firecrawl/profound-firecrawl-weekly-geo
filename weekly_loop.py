#!/usr/bin/env python3
"""Weekly loop: Profound newly cited pages -> Firecrawl notes -> short list.

1. Pull: Profound v2 citations report, entity=page, last 7 days, with first_cited_at.
2. Select: pages first cited inside the window, not social, cited at least --min-count times.
3. Read: Firecrawl /scrape with markdown plus a JSON schema, so every page gets the same notes.
4. Rank: transparent rules decide what to do with each page.
5. Size: Firecrawl Alexandria (Serpstat) gives each publisher's Google traffic and
   Google AI Overview citations, so bigger sites rise to the top of each action.
6. Search check: Firecrawl Alexandria (DataForSEO live Google results) shows whether each
   short-listed page also ranks in Google's top 10 for the search it targets.
Writes notes.json and weekly_list.md to --out.
"""
import argparse, json, os, re, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

FC = "https://api.firecrawl.dev/v2/scrape"
PROFOUND = "https://api.tryprofound.com/v2/reports/citations"

SCHEMA = {
    "type": "object",
    "properties": {
        "page_type": {"type": "string", "enum": ["listicle", "comparison", "tutorial", "docs", "news", "product_page", "other"]},
        "main_topic": {"type": "string", "description": "What the page is about, in one short sentence"},
        "tools_named": {"type": "array", "items": {"type": "string"}, "description": "Products or tools the page names as options, in page order"},
        "target_search": {"type": "string", "description": "The Google search this page is written to rank for, 2 to 5 words"},
        "published_date": {"type": ["string", "null"]},
        "updated_date": {"type": ["string", "null"]},
        "has_comparison_table": {"type": "boolean"},
        "has_faq": {"type": "boolean"},
    },
    "required": ["page_type", "main_topic", "tools_named", "target_search"],
}


def post(url, body, headers, timeout=120):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **headers})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def pull_profound(category_id, start, end):
    """Every page cited in the window, most cited first, 50 rows per call."""
    rows, cursor = [], None
    while True:
        body = {"category_id": category_id, "start_date": start, "end_date": end, "entity": "page",
                "metrics": ["count", "citation_share", "rank", "first_cited_at"], "limit": 50}
        if cursor:
            body["cursor"] = cursor
        r = post(PROFOUND, body, {"X-API-Key": os.environ["PROFOUND_API_KEY"]})
        rows += [[d["rank"], d["page"]["name"], d["citation_category"], d["count"], d["citation_share"], d["first_cited_at"]] for d in r["data"]]
        cursor = r["info"].get("next_cursor")
        if not cursor:
            return rows


def select(rows, start, min_count):
    return [r for r in rows if r[5][:10] >= start and r[2] != "social" and r[3] >= min_count]


def read_page(row, brand, key):
    body = {"url": "https://" + row[1], "onlyMainContent": True, "timeout": 45000,
            "formats": ["markdown", {"type": "json", "schema": SCHEMA, "prompt": "Notes on this page for a marketing team."}]}
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
                    "status": meta.get("statusCode"), "url": meta.get("url") or meta.get("sourceURL"), **notes,
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


PRIORITY = {"you are missing from this list": 3, "check how you are described": 2, "refresh your page": 1, "watch": 0, "skip": -1}


def alexandria(provider, capability, options, key):
    body = {"alexandria": {"provider": provider, "capability": capability, "options": options}}
    return post(FC, body, {"Authorization": "Bearer " + key}).get("data", {}).get("alexandria", [{}])[0]


def host(page):
    return re.sub(r"^www\.", "", page.split("/")[0])


def size_sites(domains, key):
    """Firecrawl Alexandria: Serpstat US Google traffic and AI Overview citations, 30 domains per call."""
    out = {}
    for i in range(0, len(domains), 30):
        res = alexandria("serpstat", "domains/info", {"domains": domains[i:i + 30], "se": "g_us"}, key)
        for d in ((res.get("data") or {}).get("result") or {}).get("data") or []:
            num = lambda v: v if isinstance(v, (int, float)) else 0
            out[d["domain"]] = {"google_traffic": num(d.get("traff")), "aio_citations": num(d.get("aio_citations"))}
    return out


def search_check(keyword, url, key):
    """Firecrawl Alexandria: live Google top 10 for the page's target search (DataForSEO, United States, English)."""
    site = re.sub(r"^www\.", "", urllib.parse.urlparse(url).netloc)
    try:
        for attempt in range(3):
            res = alexandria("dataforseo", "serp/google-organic", {"keyword": keyword, "location_code": 2840, "language_code": "en"}, key)
            if not res.get("error"):
                break
            time.sleep(5 + attempt * 10)
        items = ((res.get("data") or {}).get("tasks") or [{}])[0].get("result", [{}])[0].get("items") or []
        organic = [i for i in items if i.get("type") == "organic"]
        domains = [re.sub(r"^www\.", "", urllib.parse.urlparse((i.get("breadcrumb") or "").split(" ")[0]).netloc) for i in organic]
        pos = next((i["rank_group"] for i, dm in zip(organic, domains) if dm == site), None)
        return {"keyword": keyword, "google_position": pos, "top3": domains[:3], "credits": res.get("creditsCost"), "error": res.get("error")}
    except Exception as e:
        return {"keyword": keyword, "error": str(e)[:160]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", help="saved Profound export (JSON rows); omit to pull with PROFOUND_API_KEY")
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
    rows = json.load(open(a.rows)) if a.rows else pull_profound(a.category_id, a.start, a.end)
    picked = select(rows, a.start, a.min_count)
    with ThreadPoolExecutor(8) as ex:
        notes = list(ex.map(lambda r: read_page(r, a.brand, key), picked))
    for n in notes:
        n["action"] = action(n)
    acting = [n for n in notes if PRIORITY[n["action"]] > 0]
    sites = size_sites(sorted({host(n["page"]) for n in acting}), key)
    for n in acting:
        n["site"] = sites.get(host(n["page"]), {"google_traffic": 0, "aio_citations": 0})
    notes.sort(key=lambda n: (-PRIORITY[n["action"]], -(n.get("site") or {}).get("google_traffic", 0), -n["count"]))
    short = [n for n in notes if n["action"] != "watch" and n["action"] != "skip"][: a.short_list]
    for n in short:
        n["search"] = search_check(n.get("target_search") or n.get("main_topic", ""), n.get("url") or "https://" + n["page"], key)
    json.dump({"window": [a.start, a.end], "cited_pages": len(rows), "picked": len(picked), "notes": notes},
              open(os.path.join(a.out, "notes.json"), "w"), indent=1)
    lines = [f"# Pages to look at, {a.start} to {a.end}", "",
             f"{len(rows)} pages cited; {len(picked)} first cited this week, not social, cited {a.min_count}+ times.", "",
             "| Page | Cited | Type | Action | Site's US Google traffic | Site's AI Overview citations | Target search | Page in Google top 10 |",
             "|---|---|---|---|---|---|---|---|"]
    for n in short:
        g = n.get("search") or {}
        rank = "check failed" if g.get("error") else f"#{g['google_position']}" if g.get("google_position") else "not in top 10"
        site = n.get("site") or {}
        lines.append(f"| {n['page']} | {n['count']} | {n.get('page_type')} | {n['action']} | {site.get('google_traffic', 0):,} | {site.get('aio_citations', 0):,} | {g.get('keyword')} | {rank} |")
    open(os.path.join(a.out, "weekly_list.md"), "w").write("\n".join(lines) + "\n")
    counts = {}
    for n in notes:
        counts[n["action"]] = counts.get(n["action"], 0) + 1
    print(json.dumps({"cited_pages": len(rows), "picked": len(picked), "read_ok": sum(n["ok"] for n in notes), "actions": counts}))


if __name__ == "__main__":
    sys.exit(main())
