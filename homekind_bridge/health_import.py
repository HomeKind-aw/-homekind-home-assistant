"""HomeKind-owned local report import. No Garmin login, SDK, or cloud client.

Only selected daily aggregates are retained. Raw report files never reach disk.
Home Assistant publication is explicit; personal HomeKind consent remains separate.
"""
import csv
import datetime as dt
import io
import json
import math
from pathlib import Path
import re
import secrets
import threading
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_FILE = 131072
METRICS = {
    "steps": ("Steps", "health_steps", "steps", 200000),
    "sleep": ("Sleep duration", "health_sleep", "min", 1440),
    "resting_hr": ("Resting heart rate", "health_resting_hr", "bpm", 300),
}
SOURCES = {"garmin_export": "Garmin Connect export", "health_file": "Health report file"}
JSON_KEYS = {"steps": "steps", "sleep": "sleep_minutes", "resting_hr": "resting_heart_rate"}


class HealthImportError(Exception):
    pass


def source_date(value, date_format):
    if not isinstance(value, str) or len(value) > 32:
        raise HealthImportError("Choose the report's date column. Each reading needs a date.")
    value = value.strip()
    pattern = {"iso": "%Y-%m-%d", "dmy": "%d/%m/%Y", "mdy": "%m/%d/%Y"}.get(date_format) if isinstance(date_format, str) else None
    if pattern is None:
        raise HealthImportError("Choose the date format used by your report.")
    try:
        result = dt.datetime.strptime(value, pattern).date()
    except ValueError:
        raise HealthImportError("A report date does not match the selected format. Use full four-digit years.") from None
    return result


def number(value, metric, sleep_unit):
    if isinstance(value, bool) or value is None:
        raise HealthImportError("A selected reading is not a number.")
    if isinstance(value, str):
        value = value.strip()
        if metric == "sleep" and re.fullmatch(r"\d{1,2}:\d{2}", value):
            hours, minutes = map(int, value.split(":"))
            if minutes >= 60:
                raise HealthImportError("Check the sleep duration. Minutes must be below 60.")
            value, sleep_unit = hours * 60 + minutes, "minutes"
        elif re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", value):
            value = value.replace(",", "")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise HealthImportError("A selected reading is not a plain number or sleep duration.") from None
    if metric == "sleep":
        factors = {"minutes": 1, "hours": 60, "seconds": 1 / 60}
        if not isinstance(sleep_unit, str) or sleep_unit not in factors:
            raise HealthImportError("Choose the report's sleep unit.")
        result *= factors[sleep_unit]
    if not math.isfinite(result) or not 0 <= result <= METRICS[metric][3] or metric == "steps" and not result.is_integer() or metric == "resting_hr" and result < 10:
        raise HealthImportError("A selected reading is outside the supported range. Check its column and unit.")
    return int(result) if result.is_integer() else round(result, 3)


def read_report(content, file_format):
    try:
        valid_content = isinstance(content, str) and len(content.encode("utf-8")) <= MAX_FILE and "\x00" not in content
    except UnicodeError:
        valid_content = False
    if not valid_content:
        raise HealthImportError("Choose a UTF-8 CSV or JSON report up to 128 KB. ZIP, FIT and activity files are not supported.")
    content = content.lstrip("\ufeff")
    try:
        if file_format == "csv":
            reader = csv.DictReader(io.StringIO(content), strict=True)
            columns = reader.fieldnames
            if not columns or len(columns) > 50 or len(set(columns)) != len(columns) or any(not isinstance(c, str) or not c or len(c) > 100 for c in columns):
                raise HealthImportError("Use a report with one unique header row and up to 50 columns.")
            rows = []
            for row in reader:
                if len(rows) >= 4000 or None in row or any(v is None or len(v) > 1000 for v in row.values()):
                    raise HealthImportError("The report has too many rows, a missing cell or an oversized field.")
                rows.append(row)
        elif file_format == "json":
            value = json.loads(content, parse_constant=lambda _: None)
            rows = value.get("readings") if isinstance(value, dict) else value
            if not isinstance(rows, list) or len(rows) > 4000 or any(not isinstance(r, dict) for r in rows):
                raise HealthImportError("Use an array of dated readings in the HomeKind JSON format.")
            columns = ["date", "steps", "sleep_minutes", "resting_heart_rate"]
        else:
            raise HealthImportError("Choose CSV or HomeKind JSON.")
    except (ValueError, csv.Error, RecursionError, OverflowError):
        raise HealthImportError("The report could not be read. Choose a valid UTF-8 CSV or JSON file.") from None
    if not rows:
        raise HealthImportError("This report has no data rows.")
    return columns, rows


def parse_report(data, now):
    columns, rows = read_report(data.get("content"), data.get("format"))
    try:
        zone = ZoneInfo(data.get("timezone", ""))
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise HealthImportError("Choose a valid time zone for the report dates.") from None
    today = dt.datetime.fromtimestamp(now, zone).date()
    mapping = data.get("mapping")
    if data.get("format") == "json":
        mapping = {"date": "date", **JSON_KEYS}
    if not isinstance(mapping, dict) or set(mapping) - {"date", *METRICS} or mapping.get("date") not in columns:
        raise HealthImportError("Choose the date column and at least one reading column.")
    selected = {m: c for m, c in mapping.items() if m in METRICS and c}
    if not selected or any(not isinstance(c, str) or c not in columns for c in selected.values()) or len(set(selected.values())) != len(selected) or mapping["date"] in selected.values():
        raise HealthImportError("Choose a different column for each selected reading and its date.")
    latest, dated = {}, {}
    skipped = 0
    for row in rows:
        if not any(row.get(c) not in (None, "") for c in selected.values()):
            continue
        day = source_date(row.get(mapping["date"]), "iso" if data.get("format") == "json" else data.get("date_format"))
        if day > today:
            raise HealthImportError("A reading is dated in the future. Check the date format and time zone.")
        if day < today - dt.timedelta(days=6):
            skipped += 1
            continue
        for metric, column in selected.items():
            if row.get(column) in (None, ""):
                continue
            value = number(row[column], metric, "minutes" if data.get("format") == "json" else data.get("sleep_unit"))
            key = (metric, day.isoformat())
            if key in dated and dated[key] != value:
                raise HealthImportError("This report has conflicting readings for the same day. Choose a daily summary report.")
            dated[key] = value
            reading = {"metric": metric, "date": day.isoformat(), "value": value, "unit": METRICS[metric][2]}
            if metric not in latest or reading["date"] > latest[metric]["date"]:
                latest[metric] = reading
    if not latest:
        raise HealthImportError("No selected readings are from the last seven calendar days. Older reports are not imported.")
    return list(latest.values()), skipped, zone.key


class HealthImports:
    def __init__(self, directory, publish, save, clock=time.time):
        self.directory, self.publish, self.save, self.clock = Path(directory), publish, save, clock
        self.file = self.directory / "health-imports.json"
        self.lock = threading.RLock()
        self.previews = {}
        self.message = "No health file imported. Automatic Garmin sync requires approved API access."
        self.store = {"revision": 0, "profiles": {}}
        if self.file.exists():
            if self.file.stat().st_size > 64000:
                raise HealthImportError("The local health store needs to be checked.")
            self.store = json.loads(self.file.read_text())
            if not isinstance(self.store, dict) or not isinstance(self.store.get("profiles"), dict) or len(self.store["profiles"]) > 5:
                raise HealthImportError("The local health store needs to be checked.")
            self.file.chmod(0o600)
        if type(self.store.get("revision")) is not int or self.store["revision"] < 0:
            raise HealthImportError("The saved health revision needs to be checked.")
        for profile_id, p in self.store["profiles"].items():
            try:
                if not isinstance(profile_id, str) or not re.fullmatch(r"[0-9a-f]{12}", profile_id):
                    raise ValueError()
                if not isinstance(p, dict) or not isinstance(p.get("label"), str) or not 1 <= len(p["label"]) <= 40 or p.get("source") not in SOURCES or not isinstance(p.get("readings"), dict) or len(p["readings"]) > 3:
                    raise ValueError()
                zone = ZoneInfo(p["timezone"])
                for metric, reading in p["readings"].items():
                    self.entity(profile_id, metric)
                    if not isinstance(reading, dict) or reading.get("metric") != metric or reading.get("unit") != METRICS[metric][2] or number(reading.get("value"), metric, "minutes") != reading["value"] or source_date(reading.get("date"), "iso") > dt.datetime.fromtimestamp(self.clock(), zone).date():
                        raise ValueError()
            except (ValueError, TypeError, KeyError, ZoneInfoNotFoundError):
                raise HealthImportError("The saved health source needs to be checked.") from None
        self.store.setdefault("removed", {})
        if not isinstance(self.store["removed"], dict) or len(self.store["removed"]) > 50:
            raise HealthImportError("The local health cleanup list needs to be checked.")
        for entity, metric in self.store["removed"].items():
            if not isinstance(entity, str) or not re.fullmatch(r"sensor\.homekind_health_[0-9a-f]{12}_(steps|sleep|resting_hr)", entity) or metric not in METRICS:
                raise HealthImportError("The local health cleanup list needs to be checked.")
        self.tombstones = self.store["removed"]

    def status(self):
        with self.lock:
            return {"automatic_sync": False, "message": self.message, "profiles": [{"id": i, "label": p["label"], "source": p["source"], "timezone": p["timezone"], "readings": [{"metric": r["metric"], "date": r["date"]} for r in p["readings"].values()]} for i, p in self.store["profiles"].items()]}

    def inspect(self, data):
        columns, rows = read_report(data.get("content"), data.get("format"))
        return {"columns": columns, "rows": len(rows)}

    def preview(self, data):
        readings, skipped, timezone = parse_report(data, self.clock())
        label, source, profile_id = data.get("label"), data.get("source"), data.get("profile_id")
        if not isinstance(label, str) or not re.fullmatch(r"[^\x00-\x1f\x7f]{1,40}", label.strip()) or not isinstance(source, str) or source not in SOURCES:
            raise HealthImportError("Give this person's source a short label and choose its provider.")
        if profile_id is not None and (not isinstance(profile_id, str) or not re.fullmatch(r"[0-9a-f]{12}", profile_id)):
            raise HealthImportError("Choose a saved source again.")
        with self.lock:
            if profile_id and profile_id not in self.store["profiles"]:
                raise HealthImportError("Choose a saved source again.")
            if not profile_id and len(self.store["profiles"]) >= 5:
                raise HealthImportError("Up to five local health sources are supported. Remove an unused source first.")
            old = self.store["profiles"].get(profile_id, {})
            if len(self.tombstones) + sum(len(p["readings"]) for p in self.store["profiles"].values()) + sum(r["metric"] not in old.get("readings", {}) for r in readings) > 50:
                raise HealthImportError("Waiting for Home Assistant to clear removed readings. Restore its connection before adding more.")
            if old and (old["label"] != label.strip() or old["source"] != source or old["timezone"] != timezone):
                raise HealthImportError("Keep this source's person, provider and time zone, or create a separate source.")
            for r in readings:
                previous = old.get("readings", {}).get(r["metric"])
                if previous and previous["date"] > r["date"]:
                    raise HealthImportError("This report is older than a reading already saved for this person.")
            self.previews = {k: v for k, v in self.previews.items() if v["expires"] > self.clock()}
            if len(self.previews) >= 5:
                self.previews.clear()
            token = secrets.token_urlsafe(32)
            self.previews[token] = {"profile_id": profile_id or secrets.token_hex(6), "label": label.strip(), "source": source, "timezone": timezone, "readings": readings, "expires": self.clock() + 600, "revision": self.store["revision"]}
            return {"preview_id": token, "label": label.strip(), "source": SOURCES[source], "readings": readings, "older_rows_skipped": skipped}

    def commit(self, data):
        with self.lock:
            token = data.get("preview_id")
            preview = self.previews.get(token) if isinstance(token, str) else None
            if not preview or preview["expires"] <= self.clock() or preview["revision"] != self.store["revision"]:
                raise HealthImportError("This preview expired or another import changed. Preview the file again.")
            if data.get("publish_to_ha") is not True:
                raise HealthImportError("Confirm these selected readings may be stored in this Home Assistant home.")
            # Recheck expiry after the preview; midnight cannot extend an old report.
            today = dt.datetime.fromtimestamp(self.clock(), ZoneInfo(preview["timezone"])).date()
            if any(dt.date.fromisoformat(r["date"]) < today - dt.timedelta(days=6) for r in preview["readings"]):
                raise HealthImportError("A reading expired since this preview. Preview a newer report.")
            profile_id = preview["profile_id"]
            saved = self.store["profiles"].get(profile_id, {"readings": {}})
            merged = {**saved["readings"], **{r["metric"]: r for r in preview["readings"]}}
            self.store["profiles"][profile_id] = {"label": preview["label"], "source": preview["source"], "timezone": preview["timezone"], "readings": merged}
            self.store["revision"] += 1
            self.save(self.file, self.store)
            self.previews.clear()
        self.refresh()
        return {"ok": True, "message": self.message}

    def remove(self, data):
        with self.lock:
            profile_id = data.get("profile_id")
            if not isinstance(profile_id, str) or profile_id not in self.store["profiles"]:
                raise HealthImportError("Choose a saved source to remove.")
            for metric in self.store["profiles"][profile_id]["readings"]:
                self.tombstones[self.entity(profile_id, metric)] = metric
            del self.store["profiles"][profile_id]
            self.store["revision"] += 1
            self.save(self.file, self.store)
            self.previews.clear()
        self.refresh()
        return {"ok": True, "message": self.message}

    @staticmethod
    def entity(profile_id, metric):
        if not re.fullmatch(r"[0-9a-f]{12}", profile_id) or metric not in METRICS:
            raise HealthImportError("The saved health source needs to be checked.")
        return "sensor.homekind_health_" + profile_id + "_" + metric

    def refresh(self):
        with self.lock:
            self.previews = {k:v for k,v in self.previews.items() if v["expires"] > self.clock()}
            changed = False
            for profile_id, p in self.store["profiles"].items():
                today = dt.datetime.fromtimestamp(self.clock(), ZoneInfo(p["timezone"])).date()
                for metric, r in list(p["readings"].items()):
                    if dt.date.fromisoformat(r["date"]) < today - dt.timedelta(days=6):
                        self.tombstones[self.entity(profile_id, metric)] = metric
                        del p["readings"][metric]
                        changed = True
            if changed:
                self.store["revision"] += 1
                self.save(self.file, self.store)
            try:
                # Unavailable clears the old value even when HA is reconnecting.
                for entity, metric in list(self.tombstones.items()):
                    self.publish(entity, {"state": "unavailable", "attributes": {"device_class": METRICS[metric][1], "unit_of_measurement": METRICS[metric][2], "friendly_name": "Removed HomeKind health reading"}})
                    del self.tombstones[entity]
                    self.save(self.file, self.store)
                for profile_id, p in self.store["profiles"].items():
                    for metric, r in p["readings"].items():
                        label, klass, unit, _ = METRICS[metric]
                        self.publish(self.entity(profile_id, metric), {"state": str(r["value"]), "attributes": {"friendly_name": p["label"] + " " + label, "device_class": klass, "unit_of_measurement": unit, "homekind_health_source": p["source"], "homekind_health_date": r["date"], "homekind_health_timezone": p["timezone"], "homekind_health_reported_at": dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc).isoformat()}})
                self.message = "Selected file readings are available in Home Assistant. Add them in HomeKind Devices, then connect them to yourself in My sharing. This is a file import, not automatic Garmin sync." if any(p["readings"] for p in self.store["profiles"].values()) else "No current imported readings. Automatic Garmin sync requires approved API access."
            except Exception:
                # No raw report, provider exception or credentials enter logs/status.
                self.message = "The local import is saved. Waiting to publish readings to Home Assistant; retrying automatically."

    def run(self, stopped):
        while not stopped.is_set():
            self.refresh()
            stopped.wait(60)
