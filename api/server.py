#!/usr/bin/env python3
"""jobs.chrislawrence.ca data updater and read-only status API.

Everything live on the page comes from series.json, which is machine-owned
and rewritten wholesale each run. The curated files (historical.json,
meta.json, recessions.json) are never touched by automation: the historical
extensions took primary-source research to assemble and change only when a
source correction is found, and recessions.json is vendored from econ-core.

Guardrails on the machine-owned side, each protecting against a real failure:

  * a series whose newest observation is older than the stored one is kept,
    not replaced -- an upstream serving a truncated file must not roll the
    page backwards;
  * a series that shrinks by more than 10% is kept, not replaced -- same
    reasoning, different symptom;
  * a fetch failure carries the previous data forward and records the error,
    so one dead endpoint degrades one chart instead of blanking the site;
  * revisions to already-published observations are appended to
    changelog.jsonl -- labour data is revised constantly (benchmark
    revisions, seasonal refits) and the log is the honest record of that.

HTTP here is read-only. Runs happen via host cron calling
`docker exec jobs-updater python /app/server.py --refresh`.
"""

import json
import os
import sys
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import econcore

FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")

SERIES_FILE = os.path.join(DATA_DIR, "series.json")
CHANGELOG = os.path.join(DATA_DIR, "changelog.jsonl")
STATE_FILE = os.path.join(DATA_DIR, "updater-state.json")

CURATED = ["meta", "historical", "recessions"]
SHRINK_TOLERANCE = 0.9
CHANGELOG_IN_PAYLOAD = 100

_payload_cache = {"stamp": None, "body": None}
_state = {"last_run": None, "results": []}
_lock = threading.Lock()


# --------------------------------------------------------------------------
# the series list
#
# Adding a series here is a human decision with a verified source; the
# updater only ever refreshes what is declared. StatCan vectors carry
# expect_title so a renumbered vector fails loudly instead of charting
# someone else's numbers.
# --------------------------------------------------------------------------

def _fred(series_id):
    return lambda: econcore.fred_series(series_id, FRED_KEY)


def _wds(vector_id, expect_title):
    return lambda: econcore.wds_vector(vector_id, expect_title=expect_title)


LFS_TABLE = "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1410028701"
EI_TABLE = "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1410001101"

FETCHED = [
    {
        "id": "us_unemployment_rate",
        "fetch": _fred("UNRATE"),
        "label": "US unemployment rate",
        "source": "BLS Current Population Survey, via FRED UNRATE",
        "source_url": "https://fred.stlouisfed.org/series/UNRATE",
        "units": "percent", "freq": "monthly",
        "note": "Civilian unemployment rate, 16 and over, seasonally adjusted. Monthly since January 1948.",
    },
    {
        "id": "us_nonfarm_payrolls",
        "fetch": _fred("PAYEMS"),
        "label": "US nonfarm payrolls",
        "source": "BLS Current Employment Statistics, via FRED PAYEMS",
        "source_url": "https://fred.stlouisfed.org/series/PAYEMS",
        "units": "thousands_of_jobs", "freq": "monthly",
        "note": "Total nonfarm payroll employment, seasonally adjusted. The establishment survey counts jobs, not people. Monthly since 1939.",
    },
    {
        "id": "us_participation_rate",
        "fetch": _fred("CIVPART"),
        "label": "US participation rate",
        "source": "BLS Current Population Survey, via FRED CIVPART",
        "source_url": "https://fred.stlouisfed.org/series/CIVPART",
        "units": "percent", "freq": "monthly",
        "note": "Civilian labour force participation rate, 16 and over, seasonally adjusted.",
    },
    {
        "id": "us_initial_claims",
        "fetch": _fred("ICSA"),
        "label": "US initial jobless claims",
        "source": "US Employment and Training Administration, via FRED ICSA",
        "source_url": "https://fred.stlouisfed.org/series/ICSA",
        "units": "persons", "freq": "weekly",
        "note": "Initial claims for unemployment insurance, seasonally adjusted, weekly since January 1967.",
    },
    {
        "id": "us_continued_claims",
        "fetch": _fred("CCSA"),
        "label": "US continued claims",
        "source": "US Employment and Training Administration, via FRED CCSA",
        "source_url": "https://fred.stlouisfed.org/series/CCSA",
        "units": "persons", "freq": "weekly",
        "note": "Insured unemployment (continued claims), seasonally adjusted, weekly since 1967.",
    },
    {
        "id": "us_sahm_rule",
        "fetch": _fred("SAHMREALTIME"),
        "label": "Sahm rule (US, real-time)",
        "source": "Sahm (2019) real-time recession indicator, via FRED SAHMREALTIME",
        "source_url": "https://fred.stlouisfed.org/series/SAHMREALTIME",
        "units": "percentage_points", "freq": "monthly",
        "note": "Three-month average unemployment rate minus its low over the prior twelve months, built from data as it stood at the time. Every US recession since 1970 pushed it past 0.50.",
    },
    {
        "id": "us_job_openings",
        "fetch": _fred("JTSJOL"),
        "label": "US job openings",
        "source": "BLS Job Openings and Labor Turnover Survey, via FRED JTSJOL",
        "source_url": "https://fred.stlouisfed.org/series/JTSJOL",
        "units": "thousands_of_jobs", "freq": "monthly",
        "note": "JOLTS begins December 2000. Twenty-five years is short history for a labour series; treated as such.",
    },
    {
        "id": "us_quits_rate",
        "fetch": _fred("JTSQUR"),
        "label": "US quits rate",
        "source": "BLS Job Openings and Labor Turnover Survey, via FRED JTSQUR",
        "source_url": "https://fred.stlouisfed.org/series/JTSQUR",
        "units": "percent", "freq": "monthly",
        "note": "Voluntary quits as a share of employment, seasonally adjusted. Workers quit when they are confident; the rate falls before recessions bite.",
    },
    {
        "id": "ca_unemployment_rate",
        "fetch": _wds(2062815, "Unemployment rate"),
        "label": "Canada unemployment rate",
        "source": "Statistics Canada Labour Force Survey, table 14-10-0287-01, vector v2062815",
        "source_url": LFS_TABLE,
        "units": "percent", "freq": "monthly",
        "note": "15 and over, seasonally adjusted, monthly since January 1976 on the current survey basis.",
    },
    {
        "id": "ca_employment",
        "fetch": _wds(2062811, "Employment"),
        "label": "Canada employment",
        "source": "Statistics Canada Labour Force Survey, table 14-10-0287-01, vector v2062811",
        "source_url": LFS_TABLE,
        "units": "thousands_of_persons", "freq": "monthly",
        "note": "Employed persons 15 and over, seasonally adjusted. The household survey counts people, unlike the US payroll series, which counts jobs.",
    },
    {
        "id": "ca_participation_rate",
        "fetch": _wds(2062816, "Participation rate"),
        "label": "Canada participation rate",
        "source": "Statistics Canada Labour Force Survey, table 14-10-0287-01, vector v2062816",
        "source_url": LFS_TABLE,
        "units": "percent", "freq": "monthly",
        "note": "Labour force as a share of the population 15 and over, seasonally adjusted.",
    },
    {
        "id": "ca_ei_beneficiaries",
        "fetch": _wds(64549350, "Regular benefits"),
        "label": "Canada EI beneficiaries",
        "source": "Statistics Canada, table 14-10-0011-01, vector v64549350",
        "source_url": EI_TABLE,
        "units": "persons", "freq": "monthly",
        "note": "Employment Insurance regular-benefit recipients, seasonally adjusted, monthly since January 1997. Twenty-nine years of depth against the US claims pair's sixty; stated, not hidden.",
    },
]


# --------------------------------------------------------------------------
# derived series
# --------------------------------------------------------------------------

def _sahm_style(obs):
    """Sahm's formula: 3-month average minus its minimum over the prior
    twelve months of 3-month averages."""
    averages = []
    for i in range(2, len(obs)):
        window = (obs[i][1] + obs[i - 1][1] + obs[i - 2][1]) / 3.0
        averages.append([obs[i][0], window])
    out = []
    for j in range(12, len(averages)):
        floor = min(value for _, value in averages[j - 12:j])
        out.append([averages[j][0], round(averages[j][1] - floor, 2)])
    return out


def build_derived(series):
    """Computed from sourced inputs rather than asserted; confidence is
    'estimate' and the derivation is stated so a reader can check the
    arithmetic instead of trusting it."""
    out = {}

    payrolls = series.get("us_nonfarm_payrolls")
    if payrolls:
        out["us_payrolls_yoy"] = econcore.make_series(
            "us_payrolls_yoy", "US payroll growth, year over year",
            "Derived: FRED PAYEMS, 12-month percent change",
            payrolls["source_url"], "percent", "monthly",
            [[d, round(v, 2)] for d, v in
             econcore.yoy_percent(payrolls["obs"], 12)],
            confidence="estimate",
            note="Computed here from the payroll level series.")

    employment = series.get("ca_employment")
    if employment:
        out["ca_employment_yoy"] = econcore.make_series(
            "ca_employment_yoy", "Canada employment growth, year over year",
            "Derived: StatCan v2062811, 12-month percent change",
            employment["source_url"], "percent", "monthly",
            [[d, round(v, 2)] for d, v in
             econcore.yoy_percent(employment["obs"], 12)],
            confidence="estimate",
            note="Computed here from the employment level series.")

    ca_rate = series.get("ca_unemployment_rate")
    if ca_rate:
        out["ca_sahm_style"] = econcore.make_series(
            "ca_sahm_style", "Sahm-style indicator (Canada)",
            "Derived: Sahm's formula applied to StatCan v2062815",
            ca_rate["source_url"], "percentage_points", "monthly",
            _sahm_style(ca_rate["obs"]),
            confidence="estimate",
            note="Same arithmetic as the US series but computed from today's revised LFS data, not real-time vintages. Sahm defined and validated the 0.50 trigger on US data only; for Canada this is an analogue, not the rule.")

    return out


# --------------------------------------------------------------------------
# analysis: the status block econ-core's hub reads
# --------------------------------------------------------------------------

# Sahm (2019), validated on US real-time vintages: the 3-month unemployment
# average rising half a point above its trailing-year low has marked the
# start of every US recession since 1970 without firing outside one. The
# threshold is hers, not ours, which is why it ships as a printed rule.
SAHM_RULE = {
    "id": "us_sahm_rule",
    "threshold": 0.50,
    "statement": "The Sahm rule fires when the 3-month average unemployment rate sits 0.50 points or more above its lowest 3-month average of the prior twelve months.",
    "source": "Sahm, Claudia (2019), Direct Stimulus Payments to Individuals",
    "source_url": "https://fred.stlouisfed.org/series/SAHMREALTIME",
}


def _signed(value, places=2):
    """The page's sign convention: U+2212 for negatives, never a hyphen.

    The Sahm gap goes negative for most of the cycle, so this is the one
    figure on this page that needs it.
    """
    sign = "+" if value > 0 else ("−" if value < 0 else "")
    return "%s%.*f" % (sign, places, abs(value))


def build_status(series):
    """What the labour data currently says, in the shape the hub reads.

    Keyed on the US real-time Sahm series rather than the Canadian analogue:
    Sahm defined and validated the 0.50 trigger on US data only, so applying
    it to StatCan's revised LFS would be borrowing her authority for an
    arithmetic she never tested."""
    status = {}
    for sid in ("us_unemployment_rate", "ca_unemployment_rate",
                "us_sahm_rule", "ca_sahm_style", "us_payrolls_yoy"):
        entry = series.get(sid)
        if entry and entry.get("obs"):
            status[sid] = {"latest": list(entry["obs"][-1])}

    sahm = series.get("us_sahm_rule")
    if not sahm or not sahm.get("obs"):
        return status

    as_of, value = sahm["obs"][-1]
    fired = value >= SAHM_RULE["threshold"]
    status["signal_active"] = fired

    # How long the current side of the trigger has held, so the chip can say
    # "and counting" rather than implying the reading appeared this month.
    streak = 0
    for _, v in reversed(sahm["obs"]):
        if (v >= SAHM_RULE["threshold"]) == fired:
            streak += 1
        else:
            break

    unrate = status.get("us_unemployment_rate")
    detail = "Sahm at %s against the 0.50 trigger" % _signed(value)
    if unrate:
        detail += ", unemployment %.1f%%" % unrate["latest"][1]
    if streak > 1:
        detail += " · %s for %d months" % ("fired" if fired else "clear", streak)

    status["headline"] = {
        "state": "signal" if fired else "normal",
        "label": "Sahm rule triggered" if fired else "Sahm rule clear",
        "detail": detail,
        "as_of": as_of,
        "rule": SAHM_RULE["statement"],
    }
    return status


def build_analysis(series):
    return {"status": build_status(series), "sahm_rule": SAHM_RULE}


# --------------------------------------------------------------------------
# refresh
# --------------------------------------------------------------------------

def load_old_series():
    try:
        with open(SERIES_FILE) as fh:
            return json.load(fh).get("series", {})
    except Exception:  # noqa: BLE001 - first run, or corrupt file: start clean
        return {}


def _diff_revisions(series_id, old_obs, new_obs):
    """Changed values at already-published dates. Labour data revises
    constantly; the log is the record, not an alarm."""
    old_map = dict(map(tuple, old_obs))
    changed = [(d, old_map[d], v) for d, v in new_obs
               if d in old_map and abs(old_map[d] - v) > 1e-9]
    if not changed:
        return None
    deltas = [abs(after - before) for _, before, after in changed]
    return {
        "series": series_id, "action": "revised",
        "changed": len(changed),
        "span": [changed[0][0], changed[-1][0]],
        "max_delta": round(max(deltas), 4),
        "sample": [{"date": d, "before": b, "after": a}
                   for d, b, a in changed[:3]],
    }


def refresh_series(dry=False):
    old = load_old_series()
    series, errors, results = {}, {}, []

    for spec in FETCHED:
        sid = spec["id"]
        prev = old.get(sid)
        rec = {"series": sid, "action": "fetched"}
        try:
            obs = spec["fetch"]()
            doc = econcore.make_series(
                sid, spec["label"], spec["source"], spec["source_url"],
                spec["units"], spec["freq"], obs, note=spec.get("note"))
            if prev and prev.get("obs"):
                if doc["as_of"] < prev["as_of"]:
                    rec.update(action="stale-upstream",
                               reason="upstream at %s, behind stored %s; kept"
                                      % (doc["as_of"], prev["as_of"]))
                    doc = prev
                elif len(obs) < len(prev["obs"]) * SHRINK_TOLERANCE:
                    rec.update(action="shrunk",
                               reason="%d obs against %d stored; kept"
                                      % (len(obs), len(prev["obs"])))
                    doc = prev
                else:
                    revision = _diff_revisions(sid, prev["obs"], obs)
                    if revision and not dry:
                        econcore.log_revision(CHANGELOG, revision)
                    if revision:
                        rec.update(action="revised",
                                   changed=revision["changed"])
                    added = len(obs) - len(prev["obs"])
                    if added > 0:
                        rec["added"] = added
            series[sid] = doc
        except Exception as exc:  # noqa: BLE001 - one dead endpoint, one chart
            errors[sid] = "%s: %s" % (type(exc).__name__, exc)
            rec.update(action="error", reason=errors[sid])
            if prev:
                series[sid] = prev
                rec["carried_forward"] = True
        results.append(rec)
        print("%-24s %-14s %s" % (sid, rec["action"], rec.get("reason", "")),
              flush=True)

    if not series:
        raise ValueError("nothing fetched and nothing stored; refusing to write")

    series.update(build_derived(series))

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "note": "Machine-fetched. Never hand-edited; the updater rewrites this file wholesale.",
        "econcore": econcore.VERSION,
        "fred_key_used": bool(FRED_KEY),
        "errors": errors,
        "series": series,
    }

    if dry:
        total = sum(len(s["obs"]) for s in series.values())
        print("dry run: %d series, %d observations, %d errors -- not written"
              % (len(series), total, len(errors)), flush=True)
        return payload

    tmp = SERIES_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, SERIES_FILE)

    with _lock:
        _state["last_run"] = datetime.now(timezone.utc).isoformat()
        _state["results"] = results
    _save_state()

    total = sum(len(s["obs"]) for s in series.values())
    print("series refreshed: %d series, %d observations, %d errors"
          % (len(series), total, len(errors)), flush=True)
    return payload


def _save_state():
    try:
        with _lock:
            snapshot = dict(_state)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(snapshot, fh, indent=2)
        os.chmod(tmp, 0o644)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


# --------------------------------------------------------------------------
# read-only HTTP
# --------------------------------------------------------------------------

def _load(name):
    with open(os.path.join(DATA_DIR, name)) as fh:
        return json.load(fh)


def data_stamp():
    newest = 0.0
    names = [n + ".json" for n in CURATED] + ["series.json", "changelog.jsonl"]
    for name in names:
        try:
            newest = max(newest, os.path.getmtime(os.path.join(DATA_DIR, name)))
        except OSError:
            continue
    return newest


def build_data_payload():
    """Composed from disk, cached on mtime: the refresh runs outside this
    process via docker exec, so an in-memory payload would keep serving
    superseded figures behind a healthy endpoint."""
    stamp = data_stamp()
    if _payload_cache["stamp"] == stamp and _payload_cache["body"] is not None:
        return _payload_cache["body"]

    payload = {"generated_at": datetime.now(timezone.utc).isoformat()}
    for name in CURATED:
        try:
            payload[name] = _load(name + ".json")
        except Exception as exc:  # noqa: BLE001 - reported, not fatal
            payload[name] = None
            payload.setdefault("errors", {})[name] = str(exc)
    try:
        doc = _load("series.json")
        payload["series"] = doc.get("series", {})
        payload["series_fetched_at"] = doc.get("fetched_at")
        payload["series_errors"] = doc.get("errors", {})
        payload["analysis"] = build_analysis(payload["series"])
    except Exception as exc:  # noqa: BLE001 - charts degrade, page renders
        payload["series"] = {}
        payload.setdefault("errors", {})["series"] = str(exc)

    recent, total = econcore.read_revisions(CHANGELOG, CHANGELOG_IN_PAYLOAD)
    payload["changelog"] = {"total": total, "recent": recent}

    _payload_cache["stamp"] = stamp
    _payload_cache["body"] = payload
    return payload


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, cache="no-cache"):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/health":
            # Probe the dependency, not the process: no data, not healthy.
            try:
                doc = _load("series.json")
                series = doc.get("series", {})
                st = build_status(series)
                self._send(200, {
                    "status": "ok",
                    "series": len(series),
                    "latest": (series.get("us_unemployment_rate") or {}).get("as_of"),
                    "signal_active": st.get("signal_active"),
                    "headline": st.get("headline"),
                    "errors": len(doc.get("errors", {})),
                    "fetched_at": doc.get("fetched_at"),
                })
            except Exception as exc:  # noqa: BLE001 - absent data IS the unhealthy case
                self._send(503, {"status": "no data", "error": str(exc)})
        elif path == "/api/data":
            self._send(200, build_data_payload(),
                       cache="public, max-age=300, must-revalidate")
        elif path == "/api/status":
            with _lock:
                snapshot = dict(_state)
            snapshot["fred_key"] = bool(FRED_KEY)
            snapshot["econcore"] = econcore.VERSION
            self._send(200, snapshot)
        elif path == "/api/changelog":
            recent, total = econcore.read_revisions(CHANGELOG, CHANGELOG_IN_PAYLOAD)
            self._send(200, {"total": total, "recent": recent})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        return


def main():
    if "--refresh" in sys.argv:
        refresh_series()
        return
    if "--once" in sys.argv:
        refresh_series(dry=True)
        return

    print("updater starting: fred_key=%s (schedule: host cron)"
          % bool(FRED_KEY), flush=True)

    def warm():
        try:
            refresh_series()
        except Exception as exc:  # noqa: BLE001 - server must come up regardless
            print("initial fetch failed: %s" % exc, flush=True)

    threading.Thread(target=warm, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()


if __name__ == "__main__":
    main()
