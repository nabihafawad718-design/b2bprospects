# Candidate directories for live acceptance

These are **public roster URLs surfaced by a web search on 2026-11-08**. The build
sandbox cannot fetch third-party pages (the proxy returns HTTP 403 and the egress
allowlist is empty), so **none of these was opened, assessed, scraped or tested**. The
notes below are URL-shape observations only, not a claim about markup, terms,
robots.txt or extractability.

Use them as starting points for `scripts/assess_site.py` and
`scripts/verify_live.py` on your machine. Before contacting any of them, confirm that
your collection and the customer's use are permitted by the site's terms and by
applicable law. robots.txt is one input; it is not permission.

| Candidate | Roster URL shape | Why it is interesting | Status here |
|---|---|---|---|
| CBAM Homes (Coldwell Banker, Long Island) | `/getagent/list.php?limit=50&offset=400` | Traditional brokerage roster with limit/offset pagination; search results describe name, title, designations and office location, with links toward individual agents | Not opened; not tested |
| IQI Global | `/cy/agents/global?scope=global&search=&page=500` | Large international network; searchable roster with licence/registration numbers; likely JavaScript-heavy | Not opened; not tested |
| GharPulse | `/agents?city=&society=` | Filterable agent directory with firm and registration numbers | Not opened; not tested |
| Dhapage | `/dealers/dha-dealers?page=9` | Paginated dealer roster; search results describe name, title, phone and office address | Not opened; not tested |
| DHA Plus | `/search-agencies?page=87` | Large agency roster with CEO/listings/staff counts | Not opened; not tested |
| SabzProperty | `/find-agents/page/47` | Path pagination (`/find-agents/page/N`) — directly relevant to the v4 path-pagination layer | Not opened; not tested |
| BusinessList.pk | `/category/estate-agents/114` | Numbered category pagination; business directory layout | Not opened; not tested |

## How to evaluate each candidate

```bash
python scripts/assess_site.py https://candidate.example/agents
python scripts/verify_live.py https://candidate.example/agents --inspect-only \
  --json-out candidate-inspect.json
```

Record: HTTP status, final URL, robots detail, JSON-LD/embedded/sitemap presence,
profile-link count, pagination type, sample fields and whether a login/CAPTCHA/paywall
appears. If access is refused, stop; do not retry in a loop or evade it.

Then run a bounded extraction:

```bash
python scripts/verify_live.py https://candidate.example/agents \
  --max-pages 2 --max-records 10 --enrich --json-out candidate-run.json
```

Compare five records by hand against the live page and open the exported files before
calling the target supported.

## What would count as a useful result

- A layer table showing which method produced records.
- 10 bounded records with a name and at least one contact field.
- A page count that matches the visible pager.
- A re-run against a second layout (different brokerage or directory).
- A recorded refusal/negative result for any target that blocks access.

See `LIVE_ACCEPTANCE.md` for the full evidence checklist.

## Sources for the candidate list

- [CBAM Homes agent list](https://www.cbamhomes.com/getagent/list.php?limit=50&offset=400&name_first=&name_last=&byLetter=&odoor=&searchoid=&language=&region_id=&division=&submit1=&search=yes&temp=)
- [IQI Global agents](https://iqiglobal.com/cy/agents/global?scope=global&search=&page=500#agents)
- [GharPulse agents](https://gharpulse.com/agents?city=&society=)
- [Dhapage dealers](https://www.dhapage.com/dealers/dha-dealers?page=9)
- [DHA Plus agencies](https://dhaplus.com/search-agencies?page=87)
- [SabzProperty find agents](https://www.sabzproperty.com/find-agents/page/47)
- [BusinessList.pk estate agents](https://www.businesslist.pk/category/estate-agents/114)
