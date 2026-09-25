# FUNDAMENTALS universe — what each theme actually contains

Generated 2026-09-25 from real ETF holdings. **Nothing here was typed by hand.**

Constituents are the top 5 by weight after unioning a theme's ETF proxies.
Source per fund is State Street's daily holdings file where one exists, and
SEC N-PORT (the fund's own filing) everywhere else.

## How to correct this

This is the list to check against your team file. Two known distortions,
both properties of the ETF rather than bugs:

- **N-PORT names holdings but never tickers.** Foreign listings that do not
  resolve to a US symbol are dropped. EWY is 30.9% SK Hynix and 23.9%
  Samsung Electronics — 55% of the fund — and neither can be reached this way,
  so Korea/DRAM derives Korean *banks* instead.
- **An ETF does not always mean what its label says.** KBE and KRE are
  equal-weighted REGIONAL bank funds, so `banks` derives small regionals
  rather than the majors. AIQ/ROBO/BOTZ are industrial-robotics funds, so
  `AI` leans hardware.

If a theme's list looks wrong, the fix is usually the ETF proxy in
`themes_data.THEMES`, not this file.

## Themes

| theme | derived constituents | ETFs used |
|---|---|---|
| AI | ABBNY NVDA SKHY ISRG MU | AIQ (12/84), ROBO (12/77), BOTZ (12/61) |
| semis / memory | NVDA TSM MU AMD INTC | SMH (12/25), SOXX (12/30) |
| cloud / software | AMZN META CSCO GOOG ANET | WCLD (12/64), SKYY (12/63), FDN (12/41) |
| cyber | PANW FTNT CRWD CSCO AVGO | CIBR (12/42), HACK — |
| copper | GLCNF FCX HBM BHP SCCO | COPX (12/39), CPER — |
| gold | AEM B CAHPF EQX AGI | GDX (12/61), GDXJ (12/116), GLD — |
| silver | WPM PAAS CDE AG FNLPF | SIL (12/38), SLV — |
| steel / metals | BHP RIO NUE STLD CLF | SLX (12/39), XME (12/38) |
| uranium / nuclear | CCJ CEG OKLO PEG BWXT | URA (12/52), NLR (12/25) |
| power / grid | NEE ETN PWR JCI SO | GRID (12/120), XLU (12/32) |
| defense | GE RTX BA TDG HWM | ITA (12/49), XAR (12/50) |
| energy: upstream | SLB COP BKR MPC FTI | XOP (12/52), IEO (12/46), OIH (12/25) |
| energy: refiners | MPC PSX DINO PBF CEHCF | CRAK (7/25) |
| energy: midstream | TRP ENB KMI OKE LNG | MLPX (12/28) |
| biotech / healthcare | ABT LLY ISRG SYK JNJ | IBB (12/239), IHI (12/46), XLV (12/62) |
| banks | UBSI FIBK ONB FULT HWC | KBE (12/107), KRE (12/167) |
| retail / consumer | AMZN TSLA HD MCD TJX | XRT (12/74), IBUY (12/82), XLY (12/48) |
| homebuilders | MOD SN SKY MTH DHI | XHB (12/35) |
| real estate | WELL PLD EQIX AMT SPG | VNQ (12/146), XLRE (12/31) |
| shipping / logistics | UNP UBER CSX UPS DHT | SEA (12/29), IYT (12/43) |
| infrastructure | NEE ENB SO DUK TRP | IGF (12/64) |
| EV / battery | TSLA BHP CTATF FCX BE | IDRV (12/52), KARS (11/77), BATT (12/51) |
| solar / clean | FSLR NXT ENPH ENLT BE | TAN (12/31), ICLN (12/101), FAN (10/47) |
| crypto equities | CIFR FGRS GLXY WULF HUT | BLOK (12/53), BITO (0/0) |
| China | BABA NTES BIDU BEKE JD | KWEB (7/33), FXI (12/50) |
| Japan | MUFG SMFG TM SFBQF HTHIF | DXJ (12/426), EWJ (12/178) |
| Korea / DRAM | SKHY KB SHG PKX WF | EWY (5/79) |
| agriculture | _none resolved_ | DBA —, CORN —, WEAT — |

## Funds that yield nothing, and why

| fund | reason |
|---|---|
| BITO | no equity holdings -- 8 non-equity positions; this fund holds bullion, futures or other funds |
| CORN | not reachable — no SEC fund-ticker entry or filing fetch failed |
| CPER | not reachable — no SEC fund-ticker entry or filing fetch failed |
| DBA | not reachable — no SEC fund-ticker entry or filing fetch failed |
| GLD | not reachable — no SEC fund-ticker entry or filing fetch failed |
| HACK | not reachable — no SEC fund-ticker entry or filing fetch failed |
| SLV | not reachable — no SEC fund-ticker entry or filing fetch failed |
| WEAT | not reachable — no SEC fund-ticker entry or filing fetch failed |
