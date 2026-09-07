#!/usr/bin/env python3
"""Rebuild data/historical.json from primary sources.

This is genesis code, kept so the curated file stays reproducible: it
re-downloads the two Historical Statistics of Canada tables and re-derives
the Canadian rates, and carries the US table as transcribed from the scanned
Census page (there is no machine-readable primary for Series D85-86; the
scan of p.135 is the source of record and was transcribed by hand,
2026-09-06).

Run it only to verify or to apply a source correction; the output is a
curated, human-owned file. `python3 scripts/build-historical.py --write`.
"""

import csv
import io
import json
import os
import sys
import urllib.request

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "historical.json")

SECTION_D = "https://www150.statcan.gc.ca/n1/pub/11-516-x/sectiond/"
CENSUS_PDF = ("https://www2.census.gov/library/publications/1975/compendia/"
              "hist_stats_colonial-1970/hist_stats_colonial-1970p1-chD.pdf")

# US Census Bureau, Historical Statistics of the United States, Colonial
# Times to 1970, Series D85-86 (unemployment, percent of civilian labor
# force, annual averages), p.135. 14 and over through 1946, 16 and over
# from 1947. Transcribed from the scanned table.
US_D86 = {
    1929: 3.2, 1930: 8.7, 1931: 15.9, 1932: 23.6, 1933: 24.9, 1934: 21.7,
    1935: 20.1, 1936: 16.9, 1937: 14.3, 1938: 19.0, 1939: 17.2, 1940: 14.6,
    1941: 9.9, 1942: 4.7, 1943: 1.9, 1944: 1.2, 1945: 1.9, 1946: 3.9,
    1947: 3.9,
}


def _csv_rows(name):
    with urllib.request.urlopen(SECTION_D + name, timeout=60) as resp:
        return list(csv.reader(io.StringIO(resp.read().decode("latin-1"))))


def _numbers(cells):
    out = []
    for cell in cells:
        cell = cell.strip().replace(",", "")
        if cell and cell.replace(".", "", 1).isdigit():
            out.append(float(cell))
    return out


def ca_interwar():
    """June unemployment rate 1921-1945 from Series D124-133 levels:
    persons without jobs and seeking work over the civilian labour force.
    Checks itself against the published anchor (June 1933 = 19.3%)."""
    obs = []
    for row in _csv_rows("D124_133-eng.csv"):
        cells = [c.strip() for c in row]
        if len(cells) > 13 and cells[1].isdigit() and 1920 < int(cells[1]) < 1946:
            numbers = _numbers(cells[2:])
            if len(numbers) == 11 and numbers[0] in (1.0, 2.0, 3.0):
                numbers = numbers[1:]  # footnote marker column
            assert len(numbers) == 10, (cells[1], numbers)
            civilian_lf, unemployed = numbers[4], numbers[8]
            obs.append(["%s-06-01" % cells[1],
                        round(unemployed / civilian_lf * 100, 1)])
    obs.sort()
    anchor = dict(obs)["1933-06-01"]
    assert anchor == 19.3, "derivation drifted: 1933 = %s, expected 19.3" % anchor
    return obs


def ca_lfs_annual():
    """Total unemployment rate 1946-1975, Series D233."""
    obs = []
    for row in _csv_rows("D223_235-eng.csv"):
        cells = [c.strip() for c in row]
        if len(cells) > 18 and cells[1].isdigit() and 1945 < int(cells[1]) < 1976:
            obs.append(["%s-01-01" % cells[1], float(cells[17])])
    obs.sort()
    assert len(obs) == 30 and dict(obs)["1958-01-01"] == 7.0
    return obs


def build():
    return {
        "series_group": "historical_extensions",
        "update_cadence": "Historical. Changes only if a source correction is found; regenerate with scripts/build-historical.py.",
        "series": [
            {
                "id": "us_unemployment_rate_annual_prewar",
                "label": "US unemployment rate, annual (1929-1947)",
                "source": "US Census Bureau, Historical Statistics of the United States, Colonial Times to 1970, Series D85-86, p.135",
                "source_url": CENSUS_PDF,
                "units": "percent", "freq": "annual",
                "confidence": "estimate",
                "as_of": "1947-01-01",
                "obs": [["%d-01-01" % year, US_D86[year]]
                        for year in sorted(US_D86)],
                "note": "Annual averages, persons 14 and over (16 and over for 1947). 1929-1939 are Lebergott's reconstructions as published by the Census Bureau; the CPS household survey begins in 1940. Relief workers on federal emergency programmes count as unemployed here; see disputed.",
                "splices": [
                    {"at": "1940-01-01",
                     "note": "The CPS household survey begins March 1940; earlier years are census-benchmark reconstructions."},
                    {"at": "1947-01-01",
                     "note": "Revised postwar basis, 16 and over."},
                ],
                "disputed": {
                    "note": "Whether a WPA relief job was a job. Counting federal emergency relief workers as employed, Darby (1976) puts 1933 at 20.6% against the 24.9% shown, and 1941 at 6.6% against 9.9%. The gap is definitional, not arithmetic, and it is the difference between 'a quarter of the labour force idle' and 'a fifth'.",
                    "source": "Michael Darby, 'Three-and-a-half million U.S. employees have been mislaid', Journal of Political Economy 84(1), 1976",
                    "source_url": "https://www.journals.uchicago.edu/doi/10.1086/260407",
                },
            },
            {
                "id": "ca_unemployment_rate_june_interwar",
                "label": "Canada unemployment rate, June (1921-1945)",
                "source": "Statistics Canada, Historical Statistics of Canada (11-516-X), Series D124-133; rate derived as persons without jobs and seeking work over the civilian labour force",
                "source_url": SECTION_D + "4057750-eng.htm",
                "units": "percent", "freq": "annual",
                "confidence": "estimate",
                "as_of": "1945-06-01",
                "obs": ca_interwar(),
                "note": "1 June of each year, persons 14 and over, excludes Newfoundland (joined Confederation in 1949). Census benchmarks (1921, 1931, 1941) extrapolated between censuses with trade-union and employer reports; the Dominion Bureau of Statistics' own reconstruction, and the best available measure of the period. June readings run slightly below annual averages.",
            },
            {
                "id": "ca_unemployment_rate_annual_lfs",
                "label": "Canada unemployment rate, annual (1946-1975)",
                "source": "Statistics Canada, Historical Statistics of Canada (11-516-X), Series D223-235 (total, series D233)",
                "source_url": SECTION_D + "4057750-eng.htm",
                "units": "percent", "freq": "annual",
                "confidence": "reported",
                "as_of": "1975-01-01",
                "obs": ca_lfs_annual(),
                "note": "Labour Force Survey annual averages on the original 14-and-over basis (the survey ran quarterly 1946-1952, monthly after). The post-1976 revised 15-and-over basis reads one to three tenths lower where the two overlap; the monthly series on this page begins in 1976 on the current basis.",
                "splices": [
                    {"at": "1953-01-01",
                     "note": "LFS moves from quarterly to monthly collection; annual averages either side."},
                ],
            },
        ],
    }


def main():
    doc = build()
    if "--write" not in sys.argv:
        counts = {s["id"]: len(s["obs"]) for s in doc["series"]}
        print("dry run, derivations verified:", json.dumps(counts, indent=1))
        print("pass --write to apply")
        return
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, OUT)
    print("wrote", os.path.normpath(OUT))


if __name__ == "__main__":
    main()
