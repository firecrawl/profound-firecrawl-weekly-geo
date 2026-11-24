# Weekly GEO list with Profound and Firecrawl

Find the pages AI engines started citing in your category this week, read each one with Firecrawl, and get a short list of pages to act on.

1. **Pull**: Profound citations report (v2), page level, with `first_cited_at`, for the last 7 days.
2. **Select**: pages first cited this week, not social, cited at least `--min-count` times.
3. **Read**: Firecrawl /scrape with a JSON schema, so every page gets the same notes (page type, tools named in order, target search, publish date).
4. **Sort**: plain rules: lists that leave your brand out, pages that mention you, your own pages.
5. **Size**: Firecrawl Alexandria (Serpstat) adds each site's US Google traffic and AI Overview citations.
6. **Check**: Firecrawl Alexandria (DataForSEO) checks whether the top pages rank in Google's top 10 for their target search.

The script writes `notes.json` and `weekly_list.md`. It sends nothing and contacts no one.

## Run it

Python 3.9+, standard library only.

```bash
export FIRECRAWL_API_KEY=fc-...
export PROFOUND_API_KEY=...
python3 weekly_loop.py --category-id <profound-category-id> --start 2026-11-17 --end 2026-11-23 --brand "Your Brand" --out run
```

To rerun on a saved export instead of the API, pass `--rows export.json`, a JSON list of `[rank, page, citation_category, count, citation_share, first_cited_at]` rows.

## Cost

Reading a page with JSON costs 5 Firecrawl credits. Firecrawl Alexandria calls bill in Firecrawl credits at the price listed on each tool (browse them at https://alexandria.firecrawl.dev/providers).

## Links

- Profound citations report: https://docs.tryprofound.com/rest-api/reports/query-citations-v2
- Firecrawl /scrape: https://docs.firecrawl.dev/features/scrape
- Firecrawl Alexandria: https://alexandria.firecrawl.dev/providers
