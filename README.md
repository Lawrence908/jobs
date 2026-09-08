# A Century of Work

US and Canadian unemployment and jobs history on one timeline.
Live at [jobs.chrislawrence.ca](https://jobs.chrislawrence.ca).

No framework, no build step, no package manager. Plain HTML, CSS and vanilla JS
with `fetch`, the same shape as [debt](https://github.com/Lawrence908/debt) and [diesel](https://github.com/Lawrence908/diesel). It has
to still work in three years when nobody has run an install in as long.

## Layout

```
src/index.html          markup, styling, charts and every render function
data/historical.json    curated annual extensions, human-owned, primary-sourced
data/meta.json          figure tokens the prose renders from
data/recessions.json    vendored from econ-core, never edited here
data/series.json        machine-fetched, rewritten wholesale, never hand-edited
api/server.py           updater and read-only status API
api/econcore.py         vendored from econ-core, never edited here
scripts/build-historical.py   reproducible genesis of historical.json
```

## What it shows

Fifteen series against every recession since 1921, shaded per country (NBER
via USREC for the US, the C.D. Howe Business Cycle Council chronology for
Canada):

- **Unemployment, 1921 to today.** Monthly official series (BLS from 1948,
  StatCan LFS from 1976) with curated annual extensions: US 1929-1947 from
  Census Historical Statistics Series D85-86, Canada 1921-1945 derived from
  Historical Statistics of Canada D124-133 and 1946-1975 from D223-235.
  Estimates draw dashed; splices are labelled, never smoothed over.
- **The Sahm rule**, real-time for the US, plus a computed Canadian analogue
  that is labelled an analogue because Sahm validated the 0.50 trigger on US
  data only.
- **Payrolls and employment growth**, jobs versus people, stated as such.
- **Weekly claims** (initial and continued, 1967) and Canada's EI
  beneficiaries (1997), the high-frequency canaries.
- **Participation** and **JOLTS openings/quits**.

## The contract

Every series follows the shared [econ-core](https://github.com/Lawrence908/econ-core) contract: id,
provenance, units, frequency, confidence, `[date, value]` observations,
splice notes, and a vintages field reserved for ALFRED as-published views.
The one rule inherited from debt: **the page contains no figures.** Prose
numbers render from data via tokens; an unknown token renders visibly.

Where sources disagree, the disagreement is content: Series D85-86 counts
relief workers as unemployed, Darby (1976) counts them as employed, and the
page shows the Census series while saying exactly that.

## The updater

Series data is machine-owned and refreshed by host cron; curated files are
never touched by automation.

```bash
docker exec jobs-updater python /app/server.py --refresh   # what cron runs
docker exec jobs-updater python /app/server.py --once      # dry run
```

```cron
45 6 * * * docker exec jobs-updater python /app/server.py --refresh >> /home/chris/logs/jobs-updater.log 2>&1
9 4 1 * * : > /home/chris/logs/jobs-updater.log
```

Guardrails, all of which exist because labour data actually does these things:
an upstream serving a truncated file cannot roll a series backwards or shrink
it past 10%; a fetch failure carries the previous data forward and degrades
one chart instead of blanking the site; and every revision to an
already-published observation is appended to `changelog.jsonl`, because
benchmark and seasonal revisions are routine and the log is the honest record.

StatCan vectors are fetched with `expect_title` verification: a renumbered
vector fails loudly rather than charting someone else's numbers.

Fetch policy is keyless-primary (FRED CSV, StatCan WDS) with the keyed FRED
API as fallback and as the only route for future ALFRED vintages. One learned
quirk lives in econcore: `fred.stlouisfed.org` tarpits unrecognised
User-Agents, so the CSV endpoint gets urllib's honest default UA.

## Rebuilding the curated file

```bash
python3 scripts/build-historical.py --write
```

Re-downloads the two Historical Statistics of Canada tables, re-derives the
Canadian rates (self-checked against the published June 1933 = 19.3% anchor),
and carries the US table as transcribed from the scanned Census page, which
has no machine-readable form. Run it to verify or to apply a source
correction; the output is human-owned.

## Data and attribution

The MIT licence covers this repository's code. It does not cover the data, which is not
mine: every series belongs to the body that publishes it and carries that body's own terms.
Each series names its `source` and `source_url` so the original is always one click away.

US series are works of the Bureau of Labor Statistics and the Employment and Training
Administration, not subject to copyright. The real-time Sahm rule is published by FRED and
credited to Claudia Sahm (2019), cited in full on the page.

Statistics Canada data is used under the [Open Licence](https://www.statcan.gc.ca/en/reference/licence),
which requires this acknowledgement: *Adapted from Statistics Canada, the tables and vectors
named per series above. This does not constitute an endorsement by Statistics Canada of this
product.*

Recession bands come from econ-core: the US from the NBER chronology via FRED `USREC`,
Canada from the C.D. Howe Institute Business Cycle Council chronology.

Series reached through FRED are redistributed by the Federal Reserve Bank of St. Louis
under [its terms of use](https://fred.stlouisfed.org/legal/), which ask that you cite the
original source and note that it was accessed via FRED.
