# Site adapter configurations

Each `*.json` file in this folder is loaded at startup as a site-specific adapter.
No Python is required. The app's adapter registry lists every enabled config in the
Adapter Health workspace.

A config is enabled when `domains` is a non-empty list. A file with
`"domains": []` is a template and is ignored.

## Minimal config

```json
{
  "id": "acme",
  "label": "Acme Realty",
  "domains": ["acme.example"],
  "listing": {
    "row": "div.agent-card",
    "profile_link": "a.agent-card__link",
    "next": "a.pagination__next",
    "page_param": "page"
  },
  "profile": {
    "fields": {
      "agent_name": "h1.agent-name",
      "brokerage_name": ".office-name",
      "phone": "a[href^='tel:']",
      "email": "a[href^='mailto:']",
      "street_address": "address",
      "website": ".website a@href",
      "license_number": ".license"
    }
  }
}
```

## Selector syntax

| Form | Meaning |
|---|---|
| `div.card h3` | text of the first matching element |
| `a.profile@href` | value of an attribute |
| `meta[name=description]@content` | meta content |
| `h1.name \|\| .profile-name` | first selector that yields a value |

Any field name outside the catalogue (`agent_name`, `brokerage_name`, `phone`,
`email`, `street_address`, `city`, `state`, `postal_code`, `country`, `website`,
`profile_url`) becomes an extra exported column.

## Listing records without profile visits

If the listing already contains every field, add `listing.record_fields`. The
engine will use those records directly when they cover enough contact data, which
avoids hundreds of profile requests:

```json
"listing": {
  "row": "article.agent-card",
  "profile_link": "a.agent-card__link",
  "record_fields": {
    "agent_name": ".agent-card__name",
    "phone": ".agent-card__phone",
    "email": ".agent-card__email",
    "brokerage_name": ".agent-card__office"
  }
}
```

## Profile regex fallback

When a value has no stable selector but has a predictable shape:

```json
"profile": {
  "fields": {"agent_name": "h1"},
  "regex": {"phone": "\\(?\\d{3}\\)?[\\s.-]?\\d{3}-\\d{4}"}
}
```

Keep configurations in source control and add a fixture/site test when you add one.
