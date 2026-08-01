-- ===========================================================================
-- Charging Assurance dashboard — verification queries
--
-- One query per panel of GET /api/assurance-dashboard?assurance=charging.
-- Run them all against  rafms_rating  on 10.200.36.69.
--
-- EVERY FIGURE COMES FROM TWO TABLES AND NOTHING ELSE:
--     assurance.recon_ca910   AIR raw       vs AIR processed
--     assurance.recon_ca913   AIR processed vs SDP processed
--
-- Nothing else is read — not the source feeds, not air_file_seq_check or
-- sdp_file_seq_check, not the duplicate or record-sequence rules.
--
-- EXECUTION SCOPE
-- These queries count EVERY row in both tables, matching the dashboard. The
-- tables retain several runs (recon_keep_executions), so a transaction is
-- counted once per run that produced it — see query 7 for what that costs. To
-- count each transaction once instead, set SCOPE_TO_LATEST_EXECUTION = True in
-- app/modules/assurance_dashboard/charging.py and add to each query:
--     WHERE execution_id = (SELECT execution_id FROM <table>
--                           ORDER BY execution_time DESC LIMIT 1)
--
-- WHY THE CASTS ARE GUARDED
-- The AIR/SDP feeds land amounts as text, blank on whichever side of a
-- missing-record row is absent, so a bare ::numeric fails on real rows.
--
-- WHAT EXPOSURE MEANS
-- A MISMATCH risks the DIFFERENCE (both sides billed something); a missing
-- record risks the WHOLE amount, on whichever side it exists; a MATCH risks 0.
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- 1. KPI ROW
--    Expect: 1144 evaluated · 219 matched · 925 exceptions
--            80.86% exception rate · 19.14% reconciliation success
--            1478.32 revenue at risk
-- ---------------------------------------------------------------------------
WITH recon AS (
    SELECT status,
           CASE WHEN status = 'MATCH' THEN 0
                WHEN air_proc_tran_amt   ~ '^-?[0-9]+(\.[0-9]+)?$'
                 AND air_rfl_rec_txn_amt ~ '^-?[0-9]+(\.[0-9]+)?$'
                     THEN abs(air_proc_tran_amt::numeric - air_rfl_rec_txn_amt::numeric)
                ELSE coalesce(
                    CASE WHEN air_proc_tran_amt   ~ '^-?[0-9]+(\.[0-9]+)?$' THEN air_proc_tran_amt::numeric   END,
                    CASE WHEN air_rfl_rec_txn_amt ~ '^-?[0-9]+(\.[0-9]+)?$' THEN air_rfl_rec_txn_amt::numeric END,
                    0)
           END AS exposure
    FROM assurance.recon_ca910
    UNION ALL
    SELECT status,
           CASE WHEN status = 'MATCH' THEN 0
                WHEN air_proc_tran_amt            ~ '^-?[0-9]+(\.[0-9]+)?$'
                 AND sdp_proc_maadj_adjust_amount ~ '^-?[0-9]+(\.[0-9]+)?$'
                     THEN abs(air_proc_tran_amt::numeric - sdp_proc_maadj_adjust_amount::numeric)
                ELSE coalesce(
                    CASE WHEN air_proc_tran_amt            ~ '^-?[0-9]+(\.[0-9]+)?$' THEN air_proc_tran_amt::numeric            END,
                    CASE WHEN sdp_proc_maadj_adjust_amount ~ '^-?[0-9]+(\.[0-9]+)?$' THEN sdp_proc_maadj_adjust_amount::numeric END,
                    0)
           END
    FROM assurance.recon_ca913
)
SELECT count(*)                                                             AS records_evaluated,
       count(*) FILTER (WHERE status =  'MATCH')                            AS matched,
       count(*) FILTER (WHERE status <> 'MATCH')                            AS exceptions,
       round(count(*) FILTER (WHERE status <> 'MATCH') * 100.0 / count(*), 2) AS exception_rate,
       round(count(*) FILTER (WHERE status =  'MATCH') * 100.0 / count(*), 2) AS recon_success_pct,
       round(sum(exposure) FILTER (WHERE status <> 'MATCH'), 2)             AS revenue_at_risk
FROM recon;


-- ---------------------------------------------------------------------------
-- 1b. EXCEPTION RATE — the DUPLICATE-FILE rate, from assurance.recon_ca912
--     This one card does NOT come from ca910/ca913. It reports how much of the
--     delivered AIR raw feed was a redundant copy.
--
--     recon_ca912 holds ONLY the duplicates — every row is status DUPLICATE —
--     so a percentage taken from that table alone is 100%. The denominator is
--     the file count the rule scanned to find them, on its execution record.
--     Expect: 11 duplicates / 23 files scanned = 47.83%
-- ---------------------------------------------------------------------------
SELECT e.rows_scanned                                            AS files_examined,
       (SELECT count(*) FROM assurance.recon_ca912
        WHERE execution_id = d.last_execution_id)                AS duplicate_files,
       round((SELECT count(*) FROM assurance.recon_ca912
              WHERE execution_id = d.last_execution_id) * 100.0
             / nullif(e.rows_scanned, 0), 2)                     AS duplicate_rate_pct
FROM application_schema.recon_definition d
JOIN application_schema.recon_execution  e ON e.execution_id = d.last_execution_id
WHERE d.rule_id = 'CA912';

-- Independent cross-check against the source file log: 23 files delivered but
-- only 12 distinct content hashes, so 11 are redundant copies. Same 11.
SELECT count(*)                                  AS files_delivered,
       count(DISTINCT md5_hash)                  AS distinct_content,
       count(*) - count(DISTINCT md5_hash)       AS redundant_copies,
       round((count(*) - count(DISTINCT md5_hash)) * 100.0 / count(*), 2) AS duplicate_rate_pct
FROM air_schema.air_raw_file_log;

-- The duplicate groups behind it. `remarks` reads "occurrence 3 of 4", so a
-- group of N shows up as N-1 rows: one group of 4 (3 rows) and four of 3
-- (8 rows) = 11 duplicates across 16 files.
SELECT (regexp_match(remarks, 'of ([0-9]+)'))[1]::int AS group_size,
       count(*)                                       AS duplicate_rows
FROM assurance.recon_ca912
GROUP BY group_size ORDER BY group_size;


-- ---------------------------------------------------------------------------
-- 2. EXCEPTION DISTRIBUTION — the donut, by count
--    Each status named after the table the record is missing FROM, which is the
--    system that would have to be corrected. TABLE1_MISSING is air_processed in
--    BOTH rules — it is table 1 of each — so the two merge into one category.
--    Expect: air_processed 544 · sdp_processed 184
--            air_raw_refill_record 184 · Amount mismatch 13   (= 925)
-- ---------------------------------------------------------------------------
WITH recon AS (
    SELECT CASE status
               WHEN 'MISMATCH'       THEN 'Amount mismatch'
               WHEN 'TABLE1_MISSING' THEN 'Not found in air_processed'
               WHEN 'TABLE2_MISSING' THEN 'Not found in air_raw_refill_record'
           END AS category
    FROM assurance.recon_ca910 WHERE status <> 'MATCH'
    UNION ALL
    SELECT CASE status
               WHEN 'MISMATCH'       THEN 'Amount mismatch'
               WHEN 'TABLE1_MISSING' THEN 'Not found in air_processed'
               WHEN 'TABLE2_MISSING' THEN 'Not found in sdp_processed'
           END
    FROM assurance.recon_ca913 WHERE status <> 'MATCH'
)
SELECT category, count(*) AS exceptions
FROM recon GROUP BY category ORDER BY exceptions DESC;

-- The raw status split behind it, if a category looks wrong.
SELECT 'CA910' AS rule, status, count(*) FROM assurance.recon_ca910 GROUP BY status
UNION ALL
SELECT 'CA913', status, count(*) FROM assurance.recon_ca913 GROUP BY status
ORDER BY 1, 3 DESC;


-- ---------------------------------------------------------------------------
-- 3. TOP LEAKAGE CATEGORIES — the same categories weighted by money
--    These four values must SUM TO revenue_at_risk in query 1.
--    Expect: air_raw_refill_record 602.58 · sdp_processed 485.56
--            air_processed 388.18 · Amount mismatch 2.00   (= 1478.32)
-- ---------------------------------------------------------------------------
WITH recon AS (
    SELECT CASE status
               WHEN 'MISMATCH'       THEN 'Amount mismatch'
               WHEN 'TABLE1_MISSING' THEN 'Not found in air_processed'
               WHEN 'TABLE2_MISSING' THEN 'Not found in air_raw_refill_record'
           END AS category,
           CASE WHEN air_proc_tran_amt   ~ '^-?[0-9]+(\.[0-9]+)?$'
                 AND air_rfl_rec_txn_amt ~ '^-?[0-9]+(\.[0-9]+)?$'
                     THEN abs(air_proc_tran_amt::numeric - air_rfl_rec_txn_amt::numeric)
                ELSE coalesce(
                    CASE WHEN air_proc_tran_amt   ~ '^-?[0-9]+(\.[0-9]+)?$' THEN air_proc_tran_amt::numeric   END,
                    CASE WHEN air_rfl_rec_txn_amt ~ '^-?[0-9]+(\.[0-9]+)?$' THEN air_rfl_rec_txn_amt::numeric END,
                    0)
           END AS exposure
    FROM assurance.recon_ca910 WHERE status <> 'MATCH'
    UNION ALL
    SELECT CASE status
               WHEN 'MISMATCH'       THEN 'Amount mismatch'
               WHEN 'TABLE1_MISSING' THEN 'Not found in air_processed'
               WHEN 'TABLE2_MISSING' THEN 'Not found in sdp_processed'
           END,
           CASE WHEN air_proc_tran_amt            ~ '^-?[0-9]+(\.[0-9]+)?$'
                 AND sdp_proc_maadj_adjust_amount ~ '^-?[0-9]+(\.[0-9]+)?$'
                     THEN abs(air_proc_tran_amt::numeric - sdp_proc_maadj_adjust_amount::numeric)
                ELSE coalesce(
                    CASE WHEN air_proc_tran_amt            ~ '^-?[0-9]+(\.[0-9]+)?$' THEN air_proc_tran_amt::numeric            END,
                    CASE WHEN sdp_proc_maadj_adjust_amount ~ '^-?[0-9]+(\.[0-9]+)?$' THEN sdp_proc_maadj_adjust_amount::numeric END,
                    0)
           END
    FROM assurance.recon_ca913 WHERE status <> 'MATCH'
)
SELECT category, round(sum(exposure), 2) AS revenue_impact
FROM recon GROUP BY category ORDER BY revenue_impact DESC LIMIT 6;


-- ---------------------------------------------------------------------------
-- 4. CHARGING VALIDATION TREND and REVENUE AT RISK TREND
--    Both panels come from this one query. Bucketed on the event timestamp the
--    rules joined on, NOT on when the rule ran — the series is about the
--    charging data, not about the engine.
--
--    CA913 has no rows before 02 Jul; CA910 carries some raw records dated
--    1970-01-02, whose timestamp never survived decoding. The dashboard shows
--    the newest 30 buckets, which excludes 1970. Those rows still count in the
--    KPI totals in query 1.
--    Expect: 30 days, 02 Jul .. 31 Jul.
-- ---------------------------------------------------------------------------
WITH recon AS (
    SELECT status,
           CASE WHEN air_proc_time_stamp   ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN substr(air_proc_time_stamp,   1, 10)
                WHEN air_rfl_rec_timestamp ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN substr(air_rfl_rec_timestamp, 1, 10)
           END AS day,
           CASE WHEN status = 'MATCH' THEN 0
                WHEN air_proc_tran_amt   ~ '^-?[0-9]+(\.[0-9]+)?$'
                 AND air_rfl_rec_txn_amt ~ '^-?[0-9]+(\.[0-9]+)?$'
                     THEN abs(air_proc_tran_amt::numeric - air_rfl_rec_txn_amt::numeric)
                ELSE coalesce(
                    CASE WHEN air_proc_tran_amt   ~ '^-?[0-9]+(\.[0-9]+)?$' THEN air_proc_tran_amt::numeric   END,
                    CASE WHEN air_rfl_rec_txn_amt ~ '^-?[0-9]+(\.[0-9]+)?$' THEN air_rfl_rec_txn_amt::numeric END,
                    0)
           END AS exposure
    FROM assurance.recon_ca910
    UNION ALL
    SELECT status,
           CASE WHEN air_proc_time_stamp ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN substr(air_proc_time_stamp, 1, 10)
                WHEN sdp_proc_timestamp  ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN substr(sdp_proc_timestamp,  1, 10)
           END,
           CASE WHEN status = 'MATCH' THEN 0
                WHEN air_proc_tran_amt            ~ '^-?[0-9]+(\.[0-9]+)?$'
                 AND sdp_proc_maadj_adjust_amount ~ '^-?[0-9]+(\.[0-9]+)?$'
                     THEN abs(air_proc_tran_amt::numeric - sdp_proc_maadj_adjust_amount::numeric)
                ELSE coalesce(
                    CASE WHEN air_proc_tran_amt            ~ '^-?[0-9]+(\.[0-9]+)?$' THEN air_proc_tran_amt::numeric            END,
                    CASE WHEN sdp_proc_maadj_adjust_amount ~ '^-?[0-9]+(\.[0-9]+)?$' THEN sdp_proc_maadj_adjust_amount::numeric END,
                    0)
           END
    FROM assurance.recon_ca913
)
SELECT day,
       count(*) FILTER (WHERE status =  'MATCH') AS healthy,        -- trend, green
       count(*) FILTER (WHERE status <> 'MATCH') AS exceptions,     -- trend, orange
       round(sum(exposure), 2)                   AS revenue_at_risk -- trend leakage + risk panel
FROM recon
WHERE day IS NOT NULL
GROUP BY day ORDER BY day DESC LIMIT 30;


-- ---------------------------------------------------------------------------
-- 5. TOP CHARGING SYSTEMS — exceptions by AIR node
--    Only CA910 can attribute anything: it joins on the host name, so its
--    output carries the node. CA913 joins on account and subscriber, has no
--    node column, and contributes nothing rather than an "unknown" bucket that
--    would dominate the panel.
--    Expect: ATAIR29 237 · P57N17S 52 · then a tail of single-digit nodes.
-- ---------------------------------------------------------------------------
SELECT coalesce(nullif(air_proc_host_name, ''), nullif(air_rfl_rec_host_name, '')) AS node,
       count(*) AS exceptions
FROM assurance.recon_ca910
WHERE status <> 'MATCH'
  AND coalesce(nullif(air_proc_host_name, ''), nullif(air_rfl_rec_host_name, '')) IS NOT NULL
GROUP BY node ORDER BY exceptions DESC LIMIT 6;


-- ---------------------------------------------------------------------------
-- 6. BUSINESS DISTRIBUTION — records evaluated per reconciliation
--    Which comparison the evaluated records came from. Sums to query 1's
--    records_evaluated.
--    Expect: air_processed ↔ sdp_processed 584 · air_processed ↔ air_raw_refill_record 560
-- ---------------------------------------------------------------------------
SELECT 'air_processed ↔ air_raw_refill_record' AS segment, count(*) AS records
FROM assurance.recon_ca910
UNION ALL
SELECT 'air_processed ↔ sdp_processed', count(*)
FROM assurance.recon_ca913
ORDER BY records DESC;


-- ---------------------------------------------------------------------------
-- 6b. HIGHEST REVENUE IMPACT FINDINGS — the individual rows costing the most.
--     Subject is the rule's first key pair: transaction id for CA910, account
--     number for CA913.
-- ---------------------------------------------------------------------------
WITH recon AS (
    SELECT 'AIR RAW VS AIR PROCESSED' AS rule_title, 'air_processed' AS source,
           CASE status WHEN 'MISMATCH' THEN 'Amount mismatch'
                       WHEN 'TABLE1_MISSING' THEN 'Not found in air_processed'
                       WHEN 'TABLE2_MISSING' THEN 'Not found in air_raw_refill_record' END AS phrase,
           coalesce(nullif(air_proc_origin_tran_id, ''), nullif(air_rfl_rec_origin_txn_id, '')) AS subject,
           CASE WHEN air_proc_tran_amt   ~ '^-?[0-9]+(\.[0-9]+)?$'
                 AND air_rfl_rec_txn_amt ~ '^-?[0-9]+(\.[0-9]+)?$'
                     THEN abs(air_proc_tran_amt::numeric - air_rfl_rec_txn_amt::numeric)
                ELSE coalesce(
                    CASE WHEN air_proc_tran_amt   ~ '^-?[0-9]+(\.[0-9]+)?$' THEN air_proc_tran_amt::numeric   END,
                    CASE WHEN air_rfl_rec_txn_amt ~ '^-?[0-9]+(\.[0-9]+)?$' THEN air_rfl_rec_txn_amt::numeric END,
                    0)
           END AS exposure
    FROM assurance.recon_ca910 WHERE status <> 'MATCH'
    UNION ALL
    SELECT 'AIR PROCESSED VS SDP PROCESSED', 'air_processed',
           CASE status WHEN 'MISMATCH' THEN 'Amount mismatch'
                       WHEN 'TABLE1_MISSING' THEN 'Not found in air_processed'
                       WHEN 'TABLE2_MISSING' THEN 'Not found in sdp_processed' END,
           coalesce(nullif(air_proc_acc_no, ''), nullif(sdp_proc_account_num, '')),
           CASE WHEN air_proc_tran_amt            ~ '^-?[0-9]+(\.[0-9]+)?$'
                 AND sdp_proc_maadj_adjust_amount ~ '^-?[0-9]+(\.[0-9]+)?$'
                     THEN abs(air_proc_tran_amt::numeric - sdp_proc_maadj_adjust_amount::numeric)
                ELSE coalesce(
                    CASE WHEN air_proc_tran_amt            ~ '^-?[0-9]+(\.[0-9]+)?$' THEN air_proc_tran_amt::numeric            END,
                    CASE WHEN sdp_proc_maadj_adjust_amount ~ '^-?[0-9]+(\.[0-9]+)?$' THEN sdp_proc_maadj_adjust_amount::numeric END,
                    0)
           END
    FROM assurance.recon_ca913 WHERE status <> 'MATCH'
)
SELECT phrase || ' · ' || subject AS finding, source, rule_title AS category,
       round(exposure, 2) AS revenue_impact
FROM recon WHERE exposure > 0 ORDER BY exposure DESC LIMIT 5;


-- ===========================================================================
-- 7. WHAT THE EXECUTION SCOPE COSTS — read this before quoting the KPIs
--
-- Both tables retain several runs. Every query above counts all of them, so a
-- transaction that has been reconciled four times is counted four times.
--
-- Expect: recon_ca913 holds TWO runs of the SAME 292 rows, and recon_ca910
-- four runs (122 + 194 + 122 + 122). 1,144 rows, 414 distinct records.
-- ===========================================================================
SELECT 'recon_ca910' AS table_name, execution_id, count(*) AS rows,
       min(execution_time) AS ran_at
FROM assurance.recon_ca910 GROUP BY execution_id
UNION ALL
SELECT 'recon_ca913', execution_id, count(*), min(execution_time)
FROM assurance.recon_ca913 GROUP BY execution_id
ORDER BY table_name, ran_at;

-- The KPI row counting each transaction ONCE, for comparison with query 1.
-- Expect: 414 evaluated · 73 matched · 341 exceptions · 82.37% / 17.63% · 433.38
WITH recon AS (
    SELECT status FROM assurance.recon_ca910
    WHERE execution_id = (SELECT execution_id FROM assurance.recon_ca910
                          ORDER BY execution_time DESC LIMIT 1)
    UNION ALL
    SELECT status FROM assurance.recon_ca913
    WHERE execution_id = (SELECT execution_id FROM assurance.recon_ca913
                          ORDER BY execution_time DESC LIMIT 1)
)
SELECT count(*)                                                             AS records_evaluated,
       count(*) FILTER (WHERE status =  'MATCH')                            AS matched,
       count(*) FILTER (WHERE status <> 'MATCH')                            AS exceptions,
       round(count(*) FILTER (WHERE status <> 'MATCH') * 100.0 / count(*), 2) AS exception_rate,
       round(count(*) FILTER (WHERE status =  'MATCH') * 100.0 / count(*), 2) AS recon_success_pct
FROM recon;
