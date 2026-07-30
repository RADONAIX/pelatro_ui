"""Split-DB (per-stream Postgres) tests.

Consolidated mode (no STREAM_*_PG_* env) must be byte-identical to before; split
mode must run one query per connection group and merge. These are DB-free: they
exercise the connection grouping + per-group SQL assembly, plus the worker's
count/KPI merge helpers.
"""

from __future__ import annotations

from app.core.data_sources import DataStream, StreamPg, _stream_pg
from app.modules.reporting import service, sources


def _stream(key: str, pg: StreamPg | None = None) -> DataStream:
    return DataStream(
        key=key, label=key.upper(), schema=f"{key}_schema", bi_schema="bi_reports",
        recon_record_type="record_type", raw_file_variant="refill",
        tables={"file_log_raw": f"{key}_raw_file_log"}, pg=pg,
    )


_OVERRIDE = StreamPg(
    host="10.200.37.133", port=5432, name="rafms", bi_name="rafms",
    user="postgres", password="pw",
)


# --- env parsing -----------------------------------------------------------
def test_stream_pg_none_without_env(monkeypatch):
    monkeypatch.delenv("STREAM_AIR_PG_HOST", raising=False)
    assert _stream_pg("air") is None


def test_stream_pg_from_env_falls_back_to_shared(monkeypatch):
    monkeypatch.setenv("STREAM_AIR_PG_HOST", "10.200.37.133")
    monkeypatch.delenv("STREAM_AIR_PG_PORT", raising=False)
    pg = _stream_pg("air")
    assert pg is not None
    assert pg.host == "10.200.37.133"
    assert pg.port == 5432  # fell back to the shared RA_PG_PORT default


# --- grouping --------------------------------------------------------------
def test_consolidated_is_one_group():
    air, sdp = _stream("air"), _stream("sdp")
    assert len(sources.group_streams([air, sdp], "ra_pg")) == 1


def test_split_is_one_group_per_connection():
    air, sdp = _stream("air", _OVERRIDE), _stream("sdp")  # air on 133, sdp shared
    groups = sources.group_streams([air, sdp], "ra_pg")
    assert len(groups) == 2
    assert [s.key for g in groups for s in g] == ["air", "sdp"]
    assert sources.pg_target(air, "ra").host == "10.200.37.133"


def test_clickhouse_never_splits():
    air, sdp = _stream("air", _OVERRIDE), _stream("sdp")
    # A ClickHouse report ignores PG overrides (one server on 69) → one group.
    assert len(sources.group_streams([air, sdp], "clickhouse")) == 1


# --- per-group base SQL ----------------------------------------------------
def _fake_report(streams) -> dict:
    arms = {s.key: f'SELECT * FROM {s.schema}."{s.key}_raw_file_log"' for s in streams}
    return {
        "key": "pipeline_files", "source": "ra_pg", "date_column": "file_timestamp",
        "columns": ["dag"], "stream_objs": streams,
        "detail_arms": arms, "count_arms": arms,
        "detail_sql": f"SELECT * FROM ({' UNION ALL '.join(arms.values())}) AS _u",
    }


def test_group_bases_split_isolates_each_stream():
    air, sdp = _stream("air", _OVERRIDE), _stream("sdp")
    bases = service._group_bases(_fake_report([air, sdp]))
    assert len(bases) == 2
    (t0, k0, sql0), (t1, k1, sql1) = bases
    assert k0 == ("air",) and t0 is not None and t0.host == "10.200.37.133"
    assert "air_raw_file_log" in sql0 and "sdp_raw_file_log" not in sql0
    assert k1 == ("sdp",) and "sdp_raw_file_log" in sql1


def test_group_bases_consolidated_uses_detail_sql():
    air, sdp = _stream("air"), _stream("sdp")
    bases = service._group_bases(_fake_report([air, sdp]))
    assert len(bases) == 1
    assert "air_raw_file_log" in bases[0][2] and "sdp_raw_file_log" in bases[0][2]


# --- worker merge helpers --------------------------------------------------
def test_count_all_groups_sums(monkeypatch):
    from app.workers import streaming, tasks

    counts = iter([100, 250])
    monkeypatch.setattr(streaming, "count_with_timeout", lambda *a, **k: next(counts))
    groups = [sources.QueryGroup("ra_pg", None, "", {}) for _ in range(2)]
    assert tasks._count_all_groups(groups) == 350


def test_count_all_groups_none_when_a_group_times_out(monkeypatch):
    from app.workers import streaming, tasks

    vals = iter([100, None])
    monkeypatch.setattr(streaming, "count_with_timeout", lambda *a, **k: next(vals))
    groups = [sources.QueryGroup("ra_pg", None, "", {}) for _ in range(2)]
    assert tasks._count_all_groups(groups) is None


def test_kpis_all_groups_sums_additive(monkeypatch):
    from app.workers import streaming, tasks

    rows = iter([{"rows": 10, "bad": 3}, {"rows": 5, "bad": 1}])
    monkeypatch.setattr(streaming, "query_one", lambda *a, **k: next(rows))
    groups = [sources.QueryGroup("ra_pg", None, "", {}) for _ in range(2)]
    assert tasks._kpis_all_groups(groups) == {"rows": 15, "bad": 4}
