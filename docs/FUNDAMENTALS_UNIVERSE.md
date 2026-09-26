# FUNDAMENTALS universe — what is covered, and why

Generated 2026-09-26. **152 of 162 names resolve.**

Constituents are derived from what each theme's ETFs actually hold. Personal
holdings are deliberately NOT included: this repository and `/api/fundamentals`
are both public, so a position list here would be a position list published.

| source | what it is |
|---|---|
| **theme** | top 5 by weight of what each theme's ETFs actually hold |
| **theme ETF holdings** | derived weekly from each fund's own filings |

## Themes

| theme | constituents | quarterly median | annual median |
|---|---|---|---|
| AI | ABBNY NVDA SKHY ISRG MU | +20.6pp (n=3) | — |
| semis / memory | NVDA TSM MU AMD INTC | +19.4pp (n=4) | — |
| cloud / software | AMZN META CSCO GOOG ANET | +2.6pp (n=5) | — |
| cyber | PANW FTNT CRWD CSCO AVGO | +5.5pp (n=5) | — |
| copper | FCX HBM BHP SCCO CSCCF | -7.9pp (n=2) | -10.8pp |
| gold | AEM B EQX AGI WPM | — | +18.7pp |
| silver | WPM PAAS CDE AG SSRM | -43.1pp (n=2) | +53.7pp |
| steel / metals | BHP RIO NUE STLD CLF | +1.7pp (n=3) | -1.6pp |
| uranium / nuclear | CCJ CEG OKLO PEG BWXT | -10.3pp (n=3) | -10.1pp |
| power / grid | NEE ETN PWR JCI SO | +1.4pp (n=5) | — |
| defense | GE RTX BA TDG HWM | +4.2pp (n=5) | — |
| energy: upstream | SLB COP BKR MPC FTI | +2.3pp (n=5) | — |
| energy: refiners | MPC PSX DINO PBF DK | +45.3pp (n=5) | — |
| energy: midstream | TRP ENB KMI OKE LNG | +0.2pp (n=4) | +6.9pp |
| biotech / healthcare | ABT LLY ISRG SYK JNJ | -3.3pp (n=5) | — |
| banks | UBSI FIBK ONB FULT HWC | +1.5pp (n=5) | — |
| retail / consumer | AMZN TSLA HD MCD TJX | +0.9pp (n=5) | — |
| homebuilders | MOD SN SKY DHI CVCO | -1.0pp (n=4) | -14.2pp |
| real estate | WELL PLD EQIX AMT SPG | +4.1pp (n=5) | — |
| shipping / logistics | UNP UBER CSX UPS DHT | +8.4pp (n=4) | -14.8pp |
| infrastructure | NEE ENB SO DUK TRP | -1.8pp (n=4) | +6.9pp |
| EV / battery | TSLA BHP FCX BE BYDDF | +9.7pp (n=3) | -11.3pp |
| solar / clean | FSLR NXT ENPH ENLT BE | +1.3pp (n=4) | -18.5pp |
| crypto equities | CIFR FGRS GLXY WULF HUT | -10.8pp (n=5) | — |
| China | BABA NTES BIDU BEKE JD | — | +5.0pp |
| Japan | MUFG SMFG HTHIF TKOMF MFG | — | +6.7pp |
| Korea / DRAM | SKHY KB SHG PKX WF | — | — |
| agriculture | CTVA DE NTR ZTS ADM | -1.5pp (n=4) | +14.1pp |

## Known limits

- No valuation: SEC filings carry no share price, so this says whether a business is capturing the theme, not whether it is cheap.
- Constituents are the top 5 by weight of what each theme's ETFs actually hold (State Street daily files, else SEC N-PORT).
- N-PORT names holdings but never tickers, so foreign listings that do not resolve to a US symbol are dropped -- EWY is 55% SK Hynix and Samsung, neither of which can be reached this way.
- Korea/DRAM has no readable constituent at all: SK Hynix's OTC line files no XBRL, and the Korean bank ADRs (KB, SHG) last filed usable figures in 2023 and 2024. This is a wall in what the SEC holds, not a gap in the code.
- Seasonal sequential growth is DESCRIPTIVE, not a signal: it was backtested over 4,949 company-quarters and does not predict year-on-year turning (0.91-1.03x lift vs a 50.5% base rate).
- 20-F/40-F filers are read ANNUALLY and marked so; their acceleration is reported as a separate annual median and never mixed into the quarterly one.
- MTH files no consolidated revenue concept in companyfacts at all -- its largest 2026 fact is operating cash flow -- so it cannot be read from this source.
- An ETF does not always mean what its theme label says: KBE/KRE are equal-weighted REGIONAL banks, so the banks theme derives small regionals rather than the majors.
- Return to shareholders is a share of operating cash flow, not a yield, for the same reason.
- Altman Z'' uses book equity and still flatters asset-light balance sheets.
- Q4 is derived from the annual figure where a filer never tagged it; that recovered 5 of MU's 35 quarters.
- Concept chains are MERGED, not chosen between: filers switch tags mid-history and reading only the first would truncate the series silently.
- Any series whose newest quarter is over 200 days old is refused as stale rather than reported -- IFRS filers (AEM) have no us-gaap revenue concept and drop out here.
