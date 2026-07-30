"""Unit tests for the bulk-export query builders (pure, no I/O) + the
EXPORTS_ENABLED gate."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest

from app.modules.reporting import service as reporting


def test_get_report_unknown():
    assert reporting.get_report("nope") is None
    assert reporting.get_report("air_reconciliation") is not None


def _one(groups):
    """Consolidated mode → exactly one connection group."""
    assert len(groups) == 1
    return groups[0]


def test_export_query_without_dates_is_unfiltered():
    g = _one(reporting.export_groups("air_reconciliation"))
    assert g.source == "clickhouse"
    assert g.params == {}
    assert "WHERE reconciliation_status != 'MATCHED'" in g.sql
    assert "LIMIT" not in g.sql.upper()


def test_export_query_clickhouse_date_filter():
    d0, d1 = dt.date(2026, 1, 1), dt.date(2026, 2, 1)
    g = _one(reporting.export_groups("air_reconciliation", date_from=d0, date_to=d1))
    assert g.source == "clickhouse"
    assert g.params == {"date_from": d0, "date_to": d1}
    # wrapped in a subquery + ClickHouse param + Date typing
    assert "AS _e WHERE" in g.sql
    assert "toDate(_e.created_time) >= {date_from:Date}" in g.sql
    assert "toDate(_e.created_time) < {date_to:Date}" in g.sql


def test_export_query_bi_pg_date_filter():
    d0, d1 = dt.date(2026, 1, 1), dt.date(2026, 2, 1)
    g = _one(reporting.export_groups("file_sequence_check", date_from=d0, date_to=d1))
    assert g.source == "bi_pg"
    assert g.params == {"date_from": d0, "date_to": d1}
    assert "_e.file_date::date >= :date_from" in g.sql
    assert "_e.file_date::date < :date_to" in g.sql


def test_count_and_kpi_wrap_the_export_select():
    d0, d1 = dt.date(2026, 1, 1), dt.date(2026, 2, 1)
    csql = _one(reporting.count_groups("air_reconciliation", date_from=d0, date_to=d1)).sql
    assert csql.startswith("SELECT count(*) AS n FROM (")
    ksql = _one(reporting.kpi_groups("air_reconciliation", date_from=d0, date_to=d1)).sql
    assert "countIf(reconciliation_status = 'AMOUNT_MISMATCH')" in ksql
    assert "FROM (" in ksql and ") AS _k" in ksql


# --- Partition planning (pure packers) -------------------------------------
def test_pack_parts_row_and_day_caps():
    from app.modules.exports.planning import _pack_parts

    hist = [(dt.date(2026, 1, d), 10) for d in range(1, 5)]  # 4 days × 10 rows
    # All fit one part (40 ≤ 100, span 4 ≤ 7 days).
    p = _pack_parts(hist, target_rows=100, max_days=7)
    assert len(p) == 1
    assert (p[0].date_from, p[0].date_to, p[0].est_rows) == (
        dt.date(2026, 1, 1), dt.date(2026, 1, 5), 40)
    # Row cap: adding a 2nd day (20 > 15) overflows → one day per part.
    assert len(_pack_parts(hist, target_rows=15, max_days=7)) == 4
    # Day cap: max 2 calendar days per part → 2 parts.
    p = _pack_parts(hist, target_rows=10_000, max_days=2)
    assert len(p) == 2 and p[0].date_to == dt.date(2026, 1, 3)


def test_pack_parts_hot_day_is_standalone():
    from app.modules.exports.planning import _pack_parts

    hist = [(dt.date(2026, 1, 1), 5), (dt.date(2026, 1, 2), 1000), (dt.date(2026, 1, 3), 5)]
    parts = _pack_parts(hist, target_rows=100, max_days=7)
    # The hot day (1000 > target) is its own part; every part is non-empty span.
    assert (dt.date(2026, 1, 2), dt.date(2026, 1, 3), 1000) in [
        (p.date_from, p.date_to, p.est_rows) for p in parts]
    assert all(p.date_to > p.date_from for p in parts)


def test_uniform_split_and_hard_cap():
    from app.modules.exports.planning import _uniform_split

    parts = _uniform_split(dt.date(2026, 1, 1), dt.date(2026, 1, 8), max_days=2, hard_max_parts=100)
    assert parts is not None
    assert len(parts) == 4 and parts[-1].date_to == dt.date(2026, 1, 8)  # [1,3)[3,5)[5,7)[7,8)
    # A year of 1-day blocks blows the cap → None.
    assert _uniform_split(dt.date(2026, 1, 1), dt.date(2027, 1, 1),
                          max_days=1, hard_max_parts=100) is None


@pytest.mark.asyncio
async def test_plan_export_single_vs_multipart_vs_reject(monkeypatch):
    from app.core.config import settings
    from app.modules.exports import planning
    from app.modules.reporting import service as reporting

    meta_ch = reporting.ReportMeta("clickhouse", "created_time", ["a"], True)
    meta_nodate = reporting.ReportMeta("bi_pg", None, ["a"], True)
    monkeypatch.setattr(settings, "export_multipart_row_threshold", 1_000)
    monkeypatch.setattr(settings, "export_target_part_rows", 5_000)
    monkeypatch.setattr(settings, "export_days_per_part", 7)
    monkeypatch.setattr(settings, "export_single_file_hard_max_rows", 10_000)

    # (a) date column, total below threshold → single
    monkeypatch.setattr(reporting, "report_meta", lambda k: meta_ch)

    async def small_hist(*a, **k):
        return [(dt.date(2026, 1, 1), 500)]
    monkeypatch.setattr(reporting, "date_histogram", small_hist)
    plan = await planning.plan_export(
        "r", date_from=None, date_to=None, categories=None, search=None)
    assert plan.mode == "single" and plan.est_total_rows == 500

    # (b) date column, big total → multipart with parts
    async def big_hist(*a, **k):
        return [(dt.date(2026, 1, d), 4_000) for d in range(1, 6)]  # 20k over 5 days
    monkeypatch.setattr(reporting, "date_histogram", big_hist)
    plan = await planning.plan_export(
        "r", date_from=None, date_to=None, categories=None, search=None)
    assert plan.mode == "multipart" and plan.est_total_rows == 20_000
    assert len(plan.parts) >= 4 and all(p.date_to > p.date_from for p in plan.parts)

    # (b2) big total but all on ONE day → single-file (day-granular; no intra-day split)
    async def one_day(*a, **k):
        return [(dt.date(2026, 1, 1), 50_000)]
    monkeypatch.setattr(reporting, "date_histogram", one_day)
    plan = await planning.plan_export(
        "r", date_from=None, date_to=None, categories=None, search=None)
    assert plan.mode == "single" and plan.est_total_rows == 50_000

    # (c) no date column + oversize → reject (can't partition)
    monkeypatch.setattr(reporting, "report_meta", lambda k: meta_nodate)

    async def big_count(*a, **k):
        return 999_999
    monkeypatch.setattr(reporting, "bounded_count", big_count)
    plan = await planning.plan_export(
        "r", date_from=None, date_to=None, categories=None, search=None)
    assert plan.rejected and "no date column" in plan.reason


@pytest.mark.asyncio
async def test_plan_export_histogram_timeout_falls_back(monkeypatch):
    from app.modules.exports import planning
    from app.modules.reporting import service as reporting

    monkeypatch.setattr(reporting, "report_meta",
                        lambda k: reporting.ReportMeta("bi_pg", "file_date", ["a"], True))

    async def hist_timeout(*a, **k):
        return None  # histogram gave up
    monkeypatch.setattr(reporting, "date_histogram", hist_timeout)

    async def count_none(*a, **k):
        return None  # count also gave up
    monkeypatch.setattr(reporting, "bounded_count", count_none)
    # explicit window → uniform calendar split despite no density
    plan = await planning.plan_export(
        "r", date_from=dt.date(2026, 1, 1), date_to=dt.date(2026, 1, 10),
        categories=None, search=None)
    assert plan.mode == "multipart" and plan.parts
    # no window + no sizing → reject rather than an unbounded single task
    plan = await planning.plan_export("r", date_from=None, date_to=None,
                                      categories=None, search=None)
    assert plan.rejected


def test_part_file_naming_matches_worker():
    from app.modules.exports.service import _part_file
    from app.workers.tasks import _part_key

    assert _part_file("EXP-ABC", 0) == "EXP-ABC.part000.csv.gz"
    assert _part_file("EXP-ABC", 12) == _part_key("EXP-ABC", 12)


# --- Size-aware disk guard -------------------------------------------------
def test_estimated_peak_bytes():
    from app.modules.exports import service

    assert service._estimated_peak_bytes(None, multipart=True) == 0
    single = service._estimated_peak_bytes(50_000_000, multipart=False)
    multi = service._estimated_peak_bytes(50_000_000, multipart=True)
    assert single > 0
    assert multi == 2 * single  # multipart peak ≈ parts + concat


class _FakeResult:
    def scalar_one(self):
        return 0  # no live-artifact storage used


class _FakeDB:
    async def execute(self, *a, **k):
        return _FakeResult()


@pytest.mark.asyncio
async def test_check_capacity_rejects_oversize(monkeypatch):
    from app.core.errors import ValidationFailedError
    from app.modules.exports import service

    monkeypatch.setattr(service.os, "makedirs", lambda *a, **k: None)
    monkeypatch.setattr(service.shutil, "disk_usage",
                        lambda p: type("DU", (), {"free": 10 * 1024**3})())  # 10 GB free
    # ~1B rows multipart needs ~74 GB → rejected before any DB use.
    with pytest.raises(ValidationFailedError):
        await service._check_capacity(_FakeDB(), est_rows=1_000_000_000, multipart=True)


@pytest.mark.asyncio
async def test_check_capacity_ok_when_ample(monkeypatch):
    from app.modules.exports import service

    monkeypatch.setattr(service.os, "makedirs", lambda *a, **k: None)
    monkeypatch.setattr(service.shutil, "disk_usage",
                        lambda p: type("DU", (), {"free": 500 * 1024**3})())  # 500 GB free
    # A small job on a roomy disk is accepted (no raise).
    await service._check_capacity(_FakeDB(), est_rows=1_000_000, multipart=False)


# --- The shared gzip writer: header placement, hashing, cancel --------------
class _MemStorage:
    """In-memory Storage stand-in for _write_report_gzip tests."""

    def __init__(self):
        self.files: dict[str, bytes] = {}

    def open_write(self, key):
        import io

        store = self.files

        class _Sink(io.BytesIO):
            def close(self):
                store[key] = self.getvalue()
                super().close()

        return _Sink()

    def delete(self, key):
        self.files.pop(key, None)


def _fake_stream(header, *chunks):
    def _gen(source, sql, params, chunk, target=None):
        yield header
        yield from chunks

    return _gen


def _grp(source="clickhouse"):
    """A minimal single-connection query group for the writer tests."""
    from app.modules.reporting.sources import QueryGroup

    return [QueryGroup(source=source, target=None, sql="", params={})]


def _gunzip_text(raw: bytes) -> str:
    import gzip

    return gzip.decompress(raw).decode()


def test_write_report_gzip_header_and_hash(monkeypatch):
    from app.workers import streaming, tasks

    monkeypatch.setattr(
        streaming, "stream_rows", _fake_stream(["a", "b"], [[1, 2], [3, 4]], [[5, 6]])
    )
    storage = _MemStorage()
    rows, sha = tasks._write_report_gzip(
        storage, "f.csv.gz", groups=_grp(),
        write_header=True, should_cancel=lambda: False, on_progress=lambda _r: None,
        compute_hash=True,
    )
    assert rows == 3
    text = _gunzip_text(storage.files["f.csv.gz"])
    assert text.splitlines()[0] == "a,b"  # header present
    assert text.strip().splitlines()[-1] == "5,6"
    # incremental hash == hash of the written bytes
    import hashlib

    assert sha == hashlib.sha256(storage.files["f.csv.gz"]).hexdigest()


def test_write_report_gzip_dataonly_for_parts(monkeypatch):
    from app.workers import streaming, tasks

    monkeypatch.setattr(streaming, "stream_rows", _fake_stream(["a", "b"], [[1, 2]]))
    storage = _MemStorage()
    rows, sha = tasks._write_report_gzip(
        storage, "p.csv.gz", groups=_grp(),
        write_header=False, should_cancel=lambda: False, on_progress=lambda _r: None,
        compute_hash=False,
    )
    assert rows == 1
    assert sha is None  # parts skip hashing (finalize hashes the concatenated file)
    text = _gunzip_text(storage.files["p.csv.gz"])
    assert text.splitlines() == ["1,2"]  # NO header row


def test_write_report_gzip_cancel_removes_partial(monkeypatch):
    from app.workers import streaming, tasks

    monkeypatch.setattr(streaming, "stream_rows", _fake_stream(["a"], [[1]], [[2]]))
    storage = _MemStorage()
    with pytest.raises(tasks._Cancelled):
        tasks._write_report_gzip(
            storage, "c.csv.gz", groups=_grp(),
            write_header=True, should_cancel=lambda: True, on_progress=lambda _r: None,
            compute_hash=True,
        )
    assert "c.csv.gz" not in storage.files  # partial file deleted


# --- ZIP-of-CSVs writer (Excel-friendly split) -----------------------------
def test_zipcsv_splits_with_header_per_file():
    import csv as _csv
    import io as _io
    import zipfile

    from app.workers.zipcsv import ZipCsvWriter

    buf = _io.BytesIO()
    zw = ZipCsvWriter(buf, ["a", "b"], rows_per_file=3, base_name="rpt", total_rows=6)
    zw.write_rows([[1, 2], [3, 4]])                       # 2 rows
    zw.write_rows([[5, 6], [7, 8], [9, 10], [11, 12]])   # 4 rows, straddles the cap
    zw.close()
    assert zw.total_rows == 6

    buf.seek(0)
    z = zipfile.ZipFile(buf)
    names = sorted(z.namelist())
    # named {base}_{firstRow}-{lastRow}.csv, 7-digit, 1-indexed
    assert names == ["rpt_0000001-0000003.csv", "rpt_0000004-0000006.csv"]
    total = 0
    for n in names:
        rows = list(_csv.reader(_io.TextIOWrapper(z.open(n), encoding="utf-8")))
        assert rows[0] == ["a", "b"]                     # header in EVERY file
        data = rows[1:]
        assert len(data) <= 3                            # never exceeds the cap
        total += len(data)
    assert total == 6


def test_zipcsv_partial_last_file_exact_range_with_total():
    import csv as _csv
    import io as _io
    import zipfile

    from app.workers.zipcsv import ZipCsvWriter

    buf = _io.BytesIO()
    zw = ZipCsvWriter(buf, ["x"], rows_per_file=3, base_name="rpt", total_rows=5)
    zw.write_rows([[i] for i in range(5)])   # 5 rows → 3 + 2
    zw.close()
    buf.seek(0)
    z = zipfile.ZipFile(buf)
    # last file's end-of-range is EXACT (5), not the nominal block end (6)
    assert sorted(z.namelist()) == ["rpt_0000001-0000003.csv", "rpt_0000004-0000005.csv"]
    last = list(_csv.reader(_io.TextIOWrapper(z.open("rpt_0000004-0000005.csv"), encoding="utf-8")))
    assert last[0] == ["x"] and len(last[1:]) == 2       # partial last file, still headered


@pytest.mark.asyncio
async def test_exports_routes_503_when_disabled(monkeypatch):
    """With EXPORTS_ENABLED=false the whole /exports module returns 503."""
    from app.core.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "exports_enabled", False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/api/exports")
        created = await client.post("/api/exports", json={})
    for resp in (listed, created):
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "exports_disabled"
