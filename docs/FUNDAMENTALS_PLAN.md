# FUNDAMENTALS — factor spec

Two halves. The first is measurable per company; the second is the part no
data vendor sells, and is where the edge is.

## 1. The five (Damodaran), all as RATE OF CHANGE

Levels are priced; the change in the level is what re-rates a stock.

| # | measure | why |
|---|---|---|
| 1 | revenue growth | is the top line accelerating or decelerating |
| 2 | margin growth | is that growth getting cheaper or more expensive to buy |
| 3 | return to shareholders | buybacks + dividends: what actually comes back |
| 4 | interest rate risk | duration of the cash flows, refinancing wall, floating debt |
| 5 | risk of ruin | leverage, covenant headroom, cash burn vs runway |

Second derivative is the read throughout: a company going from +30% to +20%
revenue growth is decelerating even while growing fast. This is what
separates "the theme is working" from "this company is capturing it."

Source: FMP MCP (`statements`, `analyst`, `discountedCashFlow`) free tier
where it reaches; SEC filings otherwise.

## 2. Inter-company context — the "who pays whom" map

Not available from any vendor. Earnings from one company must be readable
through to its suppliers and customers: if CRM reports strength driven by
messaging/automation, that implies spend with TWLO. Direction and importance
matter more than exact dollars.

### Data model — one row per link
- payer (customer) -> payee (vendor/supplier)
- what for (product / service category)
- importance (>10% revenue disclosed | key vendor | minor)
- source (10-K, call transcript, press release, vendor case study, job post)
- date found / last confirmed
- confidence (confirmed | likely | speculative)

### Sources, free-first
- SEC 10-K/10-Q/8-K — the 10%-customer disclosure is the only hard number
- earnings call transcripts, for key names only
- vendor customer lists and case studies (TWLO, SNOW, DDOG)
- press releases (partnerships, contracts)
- job postings (tool-usage signals)
- TradingView MCP, FMP MCP free tier

### Approach
1. seed 10-20 core names plus their key customers/suppliers
2. build the map from latest filings
3. each earnings season: update links for the major reporters, flag changes,
   backfill the rest as the season winds down
4. link to CGI: when a company reports, surface affected upstream/downstream
   names -- especially those that have NOT yet reported
5. later: an artifact view -- pick a company, see who it pays and who pays it

### Constraints
- spend amounts are rarely disclosed: verify and cite, never assume a link
- keep cost near zero; FMP Premium (~$49/mo) only if the free route blocks
- FMP data shown to anyone else needs a commercial licence -- display is a
  later problem, ingestion is fine

## Ties to the rest of CGI
- the "news failure" idea belongs here: price reaction against the direction
  of the news is the signal, and it needs both the news and the numbers
- feeds layer 3 of the LIVE brief ("who actually earns it")
- the morning brief routine consumes this map for context, so fundamentals
  must land before that routine is built
