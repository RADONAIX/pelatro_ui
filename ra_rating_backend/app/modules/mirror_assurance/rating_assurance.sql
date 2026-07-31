/* Read-only Rating Assurance query supplied by the operator.
   The CREATE TABLE wrapper is excluded so the trigger can stream its result. */
WITH RECURSIVE

params AS
(
    SELECT
        CAST(:window_start AS TIMESTAMPTZ) AS window_start,
        CAST(:window_end AS TIMESTAMPTZ) AS window_end,
        CAST(:reconciliation_tolerance AS NUMERIC) AS reconciliation_tolerance
),

source_events AS
(
    SELECT
    'E' || LPAD(
        ROW_NUMBER() OVER (ORDER BY m.id)::text,
        6,
        '0'
    ) AS event_id,
 
    m.record_number AS source_record_id,
    m.source_file AS file_name,
    m.served_msisdn AS msisdn,
    m.served_imsi AS imsi,
 
    s.account_type,
    s.primary_offer_id AS offer_code,
 
    CASE
        WHEN LOWER(COALESCE(m.record_kind, '')) LIKE '%ca' || 'll%' THEN 'VOICE'
        WHEN LOWER(COALESCE(m.record_kind, '')) LIKE '%sms%'  THEN 'SMS'
        WHEN LOWER(COALESCE(m.record_kind, '')) LIKE '%data%' THEN 'DATA'
        ELSE 'VOICE'
    END AS service_type,
 
    m.calling_number,
    m.called_number,
 
    COALESCE(m.call_duration::bigint, 0) AS duration_sec,
 
    /* Voice and SMS records normally do not have data volume */
    0::numeric(20,6) AS volume_kb,
 
    m.seizure_time_ts AS event_time,
 
    /* APN is mainly applicable for data events */
    NULL::varchar(200) AS apn,
 
    /* Subscriber bundle assigned to the event */
    s.bundle_id::text AS bundle_code,
 
    /* Remaining bundle balance based on the event service type */
    CASE
        WHEN LOWER(COALESCE(m.record_kind, '')) LIKE '%ca' || 'll%'
            THEN COALESCE(s.voice_balance_minutes, 0)
 
        WHEN LOWER(COALESCE(m.record_kind, '')) LIKE '%sms%'
            THEN COALESCE(s.sms_balance_count, 0)
 
        WHEN LOWER(COALESCE(m.record_kind, '')) LIKE '%data%'
            THEN COALESCE(s.data_balance_mb, 0)
 
        ELSE 0
    END::numeric(20,6) AS bundle_remaining,
 
    /* Type of balance used for the event */
    CASE
        WHEN LOWER(COALESCE(m.record_kind, '')) LIKE '%ca' || 'll%'
            THEN 'VOICE_BALANCE'
 
        WHEN LOWER(COALESCE(m.record_kind, '')) LIKE '%sms%'
            THEN 'SMS_BALANCE'
 
        WHEN LOWER(COALESCE(m.record_kind, '')) LIKE '%data%'
            THEN 'DATA_BALANCE'
 
        ELSE 'UNKNOWN'
    END::varchar(50) AS bundle_balance_type,
 
    /* Measurement unit of the selected bundle balance */
    CASE
        WHEN LOWER(COALESCE(m.record_kind, '')) LIKE '%ca' || 'll%'
            THEN 'MINUTES'
 
        WHEN LOWER(COALESCE(m.record_kind, '')) LIKE '%sms%'
            THEN 'COUNT'
 
        WHEN LOWER(COALESCE(m.record_kind, '')) LIKE '%data%'
            THEN 'MB'
 
        ELSE 'UNKNOWN'
    END::varchar(30) AS bundle_unit_of_measure,
 
    s.home_country AS tax_country,
 
    CASE
        WHEN COALESCE(s.tax_percentage, 0) = 0 THEN TRUE
        ELSE FALSE
    END AS tax_exempt,
 
    /* Customer classification from subscriber master */
    COALESCE(s.customer_segment, 'UNKNOWN') AS customer_segment,
 
    COALESCE(NULLIF(v.debit_amount, '')::numeric, 0)
        AS actual_charge,
 
    COALESCE(NULLIF(v.total_tax, '')::numeric, 0)
        AS actual_tax,
 
    s.currency AS actual_currency,
 
    CASE
        WHEN UPPER(TRIM(s.account_type)) = 'PREPAID'
            THEN 'OCS_PLATFORM'
 
        WHEN UPPER(TRIM(s.account_type)) = 'POSTPAID'
            THEN 'ORACLE_BRM'
 
        ELSE COALESCE(NULLIF(TRIM(s.charging_system), ''), 'UNKNOWN')
    END AS charging_system,
 
    /*
       Number of seconds elapsed since midnight.
 
       Examples:
       09:15:00 = 33300
       22:15:00 = 80100
    */
    CASE
        WHEN m.answer_time_ts IS NOT NULL THEN
            (
                EXTRACT(HOUR FROM m.answer_time_ts)::integer * 3600
                +
                EXTRACT(MINUTE FROM m.answer_time_ts)::integer * 60
                +
                FLOOR(EXTRACT(SECOND FROM m.answer_time_ts))::integer
            )
        ELSE NULL
    END AS event_second,
 
    /* Classify the event date as weekday or weekend */
    CASE
        WHEN m.answer_time_ts IS NULL THEN 'UNKNOWN'
        WHEN EXTRACT(ISODOW FROM m.answer_time_ts) IN (6, 7)
            THEN 'WEEKEND'
        ELSE 'WEEKDAY'
    END::text AS event_day_type
 
 
FROM msc_schema.sm_msc_demo AS m
 
JOIN public.subscriber_master AS s
    ON s.msisdn = m.served_msisdn
   AND COALESCE(s.is_current, TRUE) = TRUE
 
LEFT JOIN in_schema.in_voice AS v
    ON v.pri_identity = m.served_msisdn
)


,

/* ============================================================================
   2. Find the longest matching destination prefix
   ============================================================================ */

prefix_candidates AS
(
    SELECT
        e.event_id,
        dp.prefix,
        dp.zone_code,
        dp.destination_type,
        dp.country_code,
        dp.operator_code,
        dp.priority,
        length(dp.prefix) AS prefix_length,

        ROW_NUMBER() OVER
        (
            PARTITION BY e.event_id
            ORDER BY
                length(dp.prefix) DESC,
                dp.priority DESC,
                dp.effective_from DESC,
                dp.destination_prefix_id DESC
        ) AS prefix_rank

    FROM source_events e

    JOIN canonical_rating.destination_prefix dp
        ON e.called_number IS NOT NULL
       AND e.called_number <> ''
       AND e.called_number LIKE dp.prefix || '%'
       AND dp.status = 'ACTIVE'
       AND dp.effective_from <= e.event_time
       AND
       (
           dp.effective_to IS NULL
           OR dp.effective_to > e.event_time
       )
)

,

resolved_prefix AS
(
    SELECT
        event_id,
        prefix AS matched_prefix,
        zone_code AS destination_zone,
        destination_type,
        country_code AS destination_country,
        operator_code AS destination_operator
    FROM prefix_candidates
    WHERE prefix_rank = 1
),

/* ============================================================================
   3. Resolve all applicable time bands

   One event may be in more than one time band:
     23:30 -> OFF_PEAK and NIGHT
   ============================================================================ */

resolved_time_bands AS
(
    SELECT
        e.event_id,

        ARRAY_AGG(
            DISTINCT tb.time_band_code::TEXT
            ORDER BY tb.time_band_code::TEXT
        )::TEXT[] AS applicable_time_bands

    FROM source_events e

    JOIN canonical_rating.time_band tb
        ON tb.status = 'ACTIVE'
       AND tb.effective_from <= e.event_time
       AND
       (
           tb.effective_to IS NULL
           OR tb.effective_to > e.event_time
       )
       AND
       (
           tb.day_type = 'ANY'
           OR tb.day_type = e.event_day_type
           OR tb.day_type = UPPER(
                TO_CHAR(
                    e.event_time AT TIME ZONE tb.timezone_name,
                    'FMDay'
                )
              )
       )
       AND
       (
           EXTRACT(
               EPOCH FROM
               (
                   (e.event_time AT TIME ZONE tb.timezone_name)
                   -
                   date_trunc(
                       'day',
                       e.event_time AT TIME ZONE tb.timezone_name
                   )
               )
           )::INTEGER
       ) >= tb.start_second
       AND
       (
           EXTRACT(
               EPOCH FROM
               (
                   (e.event_time AT TIME ZONE tb.timezone_name)
                   -
                   date_trunc(
                       'day',
                       e.event_time AT TIME ZONE tb.timezone_name
                   )
               )
           )::INTEGER
       ) < tb.end_second

    GROUP BY e.event_id
)


,

/* ============================================================================
   4. Build a generic event attribute JSON object
   ============================================================================ */

enriched_events AS
(
    SELECT
        e.*,

        COALESCE(rp.destination_zone, 'UNKNOWN')
            AS destination_zone,

        COALESCE(rp.destination_type, 'UNKNOWN')
            AS destination_type,

        COALESCE(rp.destination_country, '')
            AS destination_country,

        COALESCE(rp.destination_operator, '')
            AS destination_operator,

        COALESCE(rp.matched_prefix, '')
            AS matched_prefix,

        COALESCE(
            rtb.applicable_time_bands,
            ARRAY[]::TEXT[]
        ) AS applicable_time_bands,

        jsonb_build_object
        (
            'service_type',
                COALESCE(e.service_type, ''),

            'destination_zone',
                COALESCE(rp.destination_zone, 'UNKNOWN'),

            'destination_type',
                COALESCE(rp.destination_type, 'UNKNOWN'),

            'destination_country',
                COALESCE(rp.destination_country, ''),

            'destination_operator',
                COALESCE(rp.destination_operator, ''),

            'account_type',
                COALESCE(e.account_type, ''),

            'offer_code',
                COALESCE(e.offer_code, ''),

            'customer_segment',
                COALESCE(e.customer_segment, ''),

            'apn',
                COALESCE(e.apn, ''),

            'bundle_code',
                COALESCE(e.bundle_code, ''),

            'bundle_remaining',
                COALESCE(e.bundle_remaining, 0)::TEXT,

            'tax_country',
                COALESCE(e.tax_country, ''),

            'tax_exempt',
                CASE
                    WHEN COALESCE(e.tax_exempt, FALSE)
                        THEN 'TRUE'
                    ELSE 'FALSE'
                END,

            'duration_sec',
                COALESCE(e.duration_sec, 0)::TEXT,

            'volume_kb',
                COALESCE(e.volume_kb, 0)::TEXT,

            'called_number',
                COALESCE(e.called_number, ''),

            'calling_number',
                COALESCE(e.calling_number, ''),

            'day_type',
                COALESCE(e.event_day_type, '')
        ) AS event_attributes

    FROM source_events e

    LEFT JOIN resolved_prefix rp
        ON rp.event_id = e.event_id

    LEFT JOIN resolved_time_bands rtb
        ON rtb.event_id = e.event_id
)

,

/* ============================================================================
   5. Select rule headers valid for each event
   ============================================================================ */

candidate_rules AS
(
    SELECT
        e.event_id,
        r.rule_id,
        r.rule_name,
        r.rule_stage,
        r.rule_type,
        r.priority,
        r.match_strategy,
        r.currency_code,
        r.version_no

    FROM enriched_events e

    JOIN canonical_rating.rating_rule r
        ON r.status = 'ACTIVE'
       AND r.effective_from <= e.event_time
       AND
       (
           r.effective_to IS NULL
           OR r.effective_to > e.event_time
       )
       AND
       (
           r.account_scope = 'BOTH'
           OR r.account_scope = e.account_type
           OR
           (
               r.account_scope = 'HYBRID'
               AND e.account_type = 'HYBRID'
           )
       )
)

,

/* ============================================================================
   6. Evaluate all rule conditions generically
   ============================================================================ */

condition_evaluation AS
(
    SELECT
        e.event_id,
        cr.rule_id,
        cr.rule_name,
        cr.rule_stage,
        cr.rule_type,
        cr.priority,
        cr.match_strategy,
        cr.currency_code,
        cr.version_no,

        c.condition_group,
        c.condition_id,
        c.sequence_no,
        c.attribute_name,
        c.operator_code,
        c.value_type,
        c.comparison_value,
        c.comparison_value_to,

        CASE

            /* ------------------------------------------------------------
               Special handling for array-based time_band
               ------------------------------------------------------------ */

            WHEN c.attribute_name = 'time_band'
             AND c.operator_code = 'EQ'
                THEN c.comparison_value = ANY(e.applicable_time_bands)

            WHEN c.attribute_name = 'time_band'
             AND c.operator_code = 'NE'
                THEN NOT (c.comparison_value = ANY(e.applicable_time_bands))

            WHEN c.attribute_name = 'time_band'
			 AND c.operator_code = 'IN'
				THEN e.applicable_time_bands::TEXT[]
					 &&
					 regexp_split_to_array(
						 COALESCE(c.comparison_value, ''),
						 '\s*,\s*'
					 )::TEXT[]

            WHEN c.attribute_name = 'time_band'
			 AND c.operator_code = 'NOT_IN'
			    THEN NOT
			    (
			        e.applicable_time_bands::TEXT[]
			        &&
			        regexp_split_to_array(
			            COALESCE(c.comparison_value, ''),
			            '\s*,\s*'
			        )::TEXT[]
			    )

            /* ------------------------------------------------------------
               Empty checks
               ------------------------------------------------------------ */

            WHEN c.operator_code = 'IS_EMPTY'
                THEN NULLIF(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     ) IS NULL

            WHEN c.operator_code = 'IS_NOT_EMPTY'
                THEN NULLIF(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     ) IS NOT NULL

            /* ------------------------------------------------------------
               Numeric comparisons
               ------------------------------------------------------------ */

            WHEN c.value_type IN ('INTEGER', 'DECIMAL')
             AND c.operator_code = 'EQ'
                THEN NULLIF(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     )::NUMERIC
                     =
                     NULLIF(c.comparison_value, '')::NUMERIC

            WHEN c.value_type IN ('INTEGER', 'DECIMAL')
             AND c.operator_code = 'NE'
                THEN NULLIF(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     )::NUMERIC
                     <>
                     NULLIF(c.comparison_value, '')::NUMERIC

            WHEN c.value_type IN ('INTEGER', 'DECIMAL')
             AND c.operator_code = 'GT'
                THEN NULLIF(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     )::NUMERIC
                     >
                     NULLIF(c.comparison_value, '')::NUMERIC

            WHEN c.value_type IN ('INTEGER', 'DECIMAL')
             AND c.operator_code = 'GE'
                THEN NULLIF(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     )::NUMERIC
                     >=
                     NULLIF(c.comparison_value, '')::NUMERIC

            WHEN c.value_type IN ('INTEGER', 'DECIMAL')
             AND c.operator_code = 'LT'
                THEN NULLIF(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     )::NUMERIC
                     <
                     NULLIF(c.comparison_value, '')::NUMERIC

            WHEN c.value_type IN ('INTEGER', 'DECIMAL')
             AND c.operator_code = 'LE'
                THEN NULLIF(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     )::NUMERIC
                     <=
                     NULLIF(c.comparison_value, '')::NUMERIC

            WHEN c.value_type IN ('INTEGER', 'DECIMAL')
             AND c.operator_code = 'BETWEEN'
                THEN NULLIF(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     )::NUMERIC
                     BETWEEN
                     NULLIF(c.comparison_value, '')::NUMERIC
                     AND
                     NULLIF(c.comparison_value_to, '')::NUMERIC

            WHEN c.value_type IN ('INTEGER', 'DECIMAL')
             AND c.operator_code = 'IN'
                THEN NULLIF(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     )::NUMERIC
                     =
                     ANY
                     (
                         ARRAY
                         (
                             SELECT value::NUMERIC
                             FROM unnest
                             (
                                 regexp_split_to_array(
                                     COALESCE(c.comparison_value, ''),
                                     '\s*,\s*'
                                 )
                             ) value
                             WHERE value <> ''
                         )
                     )

            WHEN c.value_type IN ('INTEGER', 'DECIMAL')
             AND c.operator_code = 'NOT_IN'
                THEN NOT
                (
                    NULLIF(
                        e.event_attributes ->> c.attribute_name,
                        ''
                    )::NUMERIC
                    =
                    ANY
                    (
                        ARRAY
                        (
                            SELECT value::NUMERIC
                            FROM unnest
                            (
                                regexp_split_to_array(
                                    COALESCE(c.comparison_value, ''),
                                    '\s*,\s*'
                                )
                            ) value
                            WHERE value <> ''
                        )
                    )
                )

            /* ------------------------------------------------------------
               Boolean comparisons
               ------------------------------------------------------------ */

            WHEN c.value_type = 'BOOLEAN'
             AND c.operator_code = 'EQ'
                THEN UPPER(
                         COALESCE(
                             e.event_attributes ->> c.attribute_name,
                             ''
                         )
                     )
                     =
                     UPPER(COALESCE(c.comparison_value, ''))

            WHEN c.value_type = 'BOOLEAN'
             AND c.operator_code = 'NE'
                THEN UPPER(
                         COALESCE(
                             e.event_attributes ->> c.attribute_name,
                             ''
                         )
                     )
                     <>
                     UPPER(COALESCE(c.comparison_value, ''))

            /* ------------------------------------------------------------
               Date and timestamp comparisons
               ------------------------------------------------------------ */

            WHEN c.value_type = 'DATE'
             AND c.operator_code = 'EQ'
                THEN NULLIF(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     )::DATE
                     =
                     NULLIF(c.comparison_value, '')::DATE

            WHEN c.value_type = 'DATE'
             AND c.operator_code = 'BETWEEN'
                THEN NULLIF(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     )::DATE
                     BETWEEN
                     NULLIF(c.comparison_value, '')::DATE
                     AND
                     NULLIF(c.comparison_value_to, '')::DATE

            WHEN c.value_type = 'TIMESTAMP'
             AND c.operator_code = 'EQ'
                THEN NULLIF(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     )::TIMESTAMPTZ
                     =
                     NULLIF(c.comparison_value, '')::TIMESTAMPTZ

            WHEN c.value_type = 'TIMESTAMP'
             AND c.operator_code = 'BETWEEN'
                THEN NULLIF(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     )::TIMESTAMPTZ
                     BETWEEN
                     NULLIF(c.comparison_value, '')::TIMESTAMPTZ
                     AND
                     NULLIF(c.comparison_value_to, '')::TIMESTAMPTZ

            /* ------------------------------------------------------------
               String comparisons
               ------------------------------------------------------------ */

            WHEN c.operator_code = 'EQ'
             AND c.case_sensitive
                THEN COALESCE(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     )
                     =
                     COALESCE(c.comparison_value, '')

            WHEN c.operator_code = 'EQ'
             AND NOT c.case_sensitive
                THEN UPPER(
                         COALESCE(
                             e.event_attributes ->> c.attribute_name,
                             ''
                         )
                     )
                     =
                     UPPER(COALESCE(c.comparison_value, ''))

            WHEN c.operator_code = 'NE'
             AND c.case_sensitive
                THEN COALESCE(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     )
                     <>
                     COALESCE(c.comparison_value, '')

            WHEN c.operator_code = 'NE'
             AND NOT c.case_sensitive
                THEN UPPER(
                         COALESCE(
                             e.event_attributes ->> c.attribute_name,
                             ''
                         )
                     )
                     <>
                     UPPER(COALESCE(c.comparison_value, ''))

            WHEN c.operator_code = 'IN'
                THEN
                (
                    CASE
                        WHEN c.case_sensitive
                            THEN COALESCE(
                                     e.event_attributes ->> c.attribute_name,
                                     ''
                                 )
                        ELSE UPPER(
                                 COALESCE(
                                     e.event_attributes ->> c.attribute_name,
                                     ''
                                 )
                             )
                    END
                )
                =
                ANY
                (
                    CASE
                        WHEN c.case_sensitive
                            THEN regexp_split_to_array(
                                     COALESCE(c.comparison_value, ''),
                                     '\s*,\s*'
                                 )
                        ELSE ARRAY
                             (
                                 SELECT UPPER(value)
                                 FROM unnest
                                 (
                                     regexp_split_to_array(
                                         COALESCE(c.comparison_value, ''),
                                         '\s*,\s*'
                                     )
                                 ) value
                             )
                    END
                )

            WHEN c.operator_code = 'NOT_IN'
                THEN NOT
                (
                    (
                        CASE
                            WHEN c.case_sensitive
                                THEN COALESCE(
                                         e.event_attributes ->> c.attribute_name,
                                         ''
                                     )
                            ELSE UPPER(
                                     COALESCE(
                                         e.event_attributes ->> c.attribute_name,
                                         ''
                                     )
                                 )
                        END
                    )
                    =
                    ANY
                    (
                        CASE
                            WHEN c.case_sensitive
                                THEN regexp_split_to_array(
                                         COALESCE(c.comparison_value, ''),
                                         '\s*,\s*'
                                     )
                            ELSE ARRAY
                                 (
                                     SELECT UPPER(value)
                                     FROM unnest
                                     (
                                         regexp_split_to_array(
                                             COALESCE(c.comparison_value, ''),
                                             '\s*,\s*'
                                         )
                                     ) value
                                 )
                        END
                    )
                )

            WHEN c.operator_code = 'STARTS_WITH'
             AND c.case_sensitive
                THEN COALESCE(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     )
                     LIKE COALESCE(c.comparison_value, '') || '%'

            WHEN c.operator_code = 'STARTS_WITH'
             AND NOT c.case_sensitive
                THEN COALESCE(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     )
                     ILIKE COALESCE(c.comparison_value, '') || '%'

            WHEN c.operator_code = 'ENDS_WITH'
             AND c.case_sensitive
                THEN COALESCE(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     )
                     LIKE '%' || COALESCE(c.comparison_value, '')

            WHEN c.operator_code = 'ENDS_WITH'
             AND NOT c.case_sensitive
                THEN COALESCE(
                         e.event_attributes ->> c.attribute_name,
                         ''
                     )
                     ILIKE '%' || COALESCE(c.comparison_value, '')

            WHEN c.operator_code = 'CONTAINS'
             AND c.case_sensitive
                THEN POSITION(
                         COALESCE(c.comparison_value, '')
                         IN
                         COALESCE(
                             e.event_attributes ->> c.attribute_name,
                             ''
                         )
                     ) > 0

            WHEN c.operator_code = 'CONTAINS'
             AND NOT c.case_sensitive
                THEN POSITION(
                         UPPER(COALESCE(c.comparison_value, ''))
                         IN
                         UPPER(
                             COALESCE(
                                 e.event_attributes ->> c.attribute_name,
                                 ''
                             )
                         )
                     ) > 0

            ELSE FALSE

        END AS condition_matched

    FROM enriched_events e

    JOIN candidate_rules cr
        ON cr.event_id = e.event_id

    JOIN canonical_rating.rating_rule_condition c
        ON c.rule_id = cr.rule_id
)
--select * from condition_evaluation
,

/* ============================================================================
   7. Conditions inside the same group are AND
   ============================================================================ */

condition_group_result AS
(
    SELECT
        event_id,
        rule_id,
        rule_name,
        rule_stage,
        rule_type,
        priority,
        match_strategy,
        currency_code,
        version_no,
        condition_group,

        BOOL_AND(condition_matched)
            AS condition_group_matched

    FROM condition_evaluation

    GROUP BY
        event_id,
        rule_id,
        rule_name,
        rule_stage,
        rule_type,
        priority,
        match_strategy,
        currency_code,
        version_no,
        condition_group
)  
--select * from condition_group_result
,

/* ============================================================================
   8. Different condition groups are OR
   ============================================================================ */

matched_rules AS
(
    SELECT
        event_id,
        rule_id,
        rule_name,
        rule_stage,
        rule_type,
        priority,
        match_strategy,
        currency_code,
        version_no

    FROM condition_group_result

    GROUP BY
        event_id,
        rule_id,
        rule_name,
        rule_stage,
        rule_type,
        priority,
        match_strategy,
        currency_code,
        version_no

    HAVING BOOL_OR(condition_group_matched)
) 

,

/* ============================================================================
   9. Rank matching rules inside each stage
   ============================================================================ */

ranked_rules AS
(
    SELECT
        mr.*,

        ROW_NUMBER() OVER
        (
            PARTITION BY mr.event_id, mr.rule_stage
            ORDER BY
                mr.priority DESC,
                mr.version_no DESC,
                mr.rule_id
        ) AS stage_rule_rank

    FROM matched_rules mr
) 

,

/* ============================================================================
   10. Select FIRST_MATCH or all ALL_MATCHES rules
   ============================================================================ */

selected_rules AS
(
    SELECT
        event_id,
        rule_id,
        rule_name,
        rule_stage,
        rule_type,
        priority,
        match_strategy,
        currency_code,
        version_no

    FROM ranked_rules

    WHERE match_strategy = 'ALL_MATCHES'
       OR stage_rule_rank = 1
)
,

/* ============================================================================
   11. Attach actions
   ============================================================================ */

selected_rule_actions AS
(
    SELECT
        sr.event_id,
        sr.rule_id,
        sr.rule_name,
        sr.rule_stage,
        sr.rule_type,
        sr.priority,
        sr.currency_code,

        a.sequence_no,
        a.action_type,
        a.parameter_name,
        a.parameter_value,
        a.value_type

    FROM selected_rules sr

    JOIN canonical_rating.rating_rule_action a
        ON a.rule_id = sr.rule_id
)

,

/* ============================================================================
   12. Base-rate action parameters
   ============================================================================ */

base_rate_parameters AS
(
    SELECT
        event_id,

        MAX(rule_id)
            AS base_rule_id,

        MAX(rule_name)
            AS base_rule_name,

        MAX(currency_code)
            AS expected_currency,

        MAX(parameter_value::NUMERIC)
            FILTER
            (
                WHERE parameter_name = 'rate'
            ) AS rate,

        MAX(parameter_value::BIGINT)
            FILTER
            (
                WHERE parameter_name = 'unit_seconds'
            ) AS unit_seconds,

        MAX(parameter_value::NUMERIC)
            FILTER
            (
                WHERE parameter_name = 'unit_kb'
            ) AS unit_kb,

        MAX(parameter_value::NUMERIC)
            FILTER
            (
                WHERE parameter_name = 'unit_count'
            ) AS unit_count,

        MAX(parameter_value::BIGINT)
            FILTER
            (
                WHERE parameter_name = 'pulse_seconds'
            ) AS pulse_seconds,

        MAX(parameter_value)
            FILTER
            (
                WHERE parameter_name IN
                (
                    'pulse_rounding',
                    'volume_rounding'
                )
            ) AS quantity_rounding,

        MAX(parameter_value::NUMERIC)
            FILTER
            (
                WHERE action_type = 'SET_MINIMUM_CHARGE'
                  AND parameter_name = 'amount'
            ) AS minimum_charge,

        MAX(parameter_value::NUMERIC)
            FILTER
            (
                WHERE action_type = 'SET_MAXIMUM_CHARGE'
                  AND parameter_name = 'amount'
            ) AS maximum_charge

    FROM selected_rule_actions

    WHERE rule_stage = 'BASE_RATE'

    GROUP BY event_id
)

,

/* ============================================================================
   13. Allowance parameters
   ============================================================================ */

allowance_parameters AS
(
    SELECT
        event_id,

        TRUE AS allowance_rule_applied,

        MAX(rule_id)
            AS allowance_rule_id,

        MAX(parameter_value)
            FILTER
            (
                WHERE parameter_name = 'balance_type'
            ) AS allowance_balance_type,

        MAX(parameter_value)
            FILTER
            (
                WHERE parameter_name = 'maximum_units'
            ) AS maximum_units_expression

    FROM selected_rule_actions

    WHERE rule_stage = 'ALLOWANCE'

    GROUP BY event_id
),

/* ============================================================================
   14. Discount parameters
   ============================================================================ */

discount_parameters AS
(
    SELECT
        event_id,

        COALESCE
        (
            SUM(parameter_value::NUMERIC)
            FILTER
            (
                WHERE action_type = 'APPLY_PERCENT_DISCOUNT'
            ),
            0
        ) AS total_discount_percent,

        COALESCE
        (
            SUM(parameter_value::NUMERIC)
            FILTER
            (
                WHERE action_type = 'APPLY_FIXED_DISCOUNT'
            ),
            0
        ) AS total_fixed_discount,

        jsonb_agg
        (
            DISTINCT jsonb_build_object
            (
                'rule_id', rule_id,
                'rule_name', rule_name
            )
        ) AS selected_discount_rules

    FROM selected_rule_actions

    WHERE rule_stage = 'DISCOUNT'

    GROUP BY event_id
)
--select * from discount_parameters
,

/* ============================================================================
   15. Surcharge parameters
   ============================================================================ */

surcharge_parameters AS
(
    SELECT
        event_id,

        COALESCE
        (
            SUM(parameter_value::NUMERIC)
            FILTER
            (
                WHERE action_type = 'APPLY_PERCENT_SURCHARGE'
            ),
            0
        ) AS total_surcharge_percent,

        COALESCE
        (
            SUM(parameter_value::NUMERIC)
            FILTER
            (
                WHERE action_type = 'APPLY_FIXED_SURCHARGE'
            ),
            0
        ) AS total_fixed_surcharge

    FROM selected_rule_actions

    WHERE rule_stage = 'SURCHARGE'

    GROUP BY event_id
)
--select * from surcharge_parameters where event_id = 'TC103'
,

/* ============================================================================
   16. Tax parameters
   ============================================================================ */

tax_parameters AS
(
    SELECT
        event_id,

        COALESCE
        (
            SUM(parameter_value::NUMERIC)
            FILTER
            (
                WHERE action_type = 'APPLY_PERCENT_TAX'
            ),
            0
        ) AS total_tax_percent,

        COALESCE
        (
            SUM(parameter_value::NUMERIC)
            FILTER
            (
                WHERE action_type = 'APPLY_FIXED_TAX'
            ),
            0
        ) AS total_fixed_tax,

        jsonb_agg
        (
            DISTINCT jsonb_build_object
            (
                'rule_id', rule_id,
                'rule_name', rule_name
            )
        ) AS selected_tax_rules

    FROM selected_rule_actions

    WHERE rule_stage = 'TAX'

    GROUP BY event_id
)

,

/* ============================================================================
   17. Build rating inputs
   ============================================================================ */

rating_inputs AS
(
    SELECT
        e.*,

        bp.base_rule_id,
        bp.base_rule_name,
        bp.expected_currency,
        COALESCE(bp.rate, 0) AS rate,

        COALESCE(bp.unit_seconds, 60)
            AS unit_seconds,

        COALESCE(bp.unit_kb, 1024)
            AS unit_kb,

        COALESCE(bp.unit_count, 1)
            AS unit_count,

        COALESCE(
            bp.pulse_seconds,
            bp.unit_seconds,
            60
        ) AS pulse_seconds,

        COALESCE(bp.quantity_rounding, 'CEILING')
            AS quantity_rounding,

        COALESCE(bp.minimum_charge, 0)
            AS minimum_charge,

        bp.maximum_charge,

        COALESCE(ap.allowance_rule_applied, FALSE)
            AS allowance_rule_applied,

        ap.allowance_rule_id,
        ap.allowance_balance_type,

        COALESCE(dp.total_discount_percent, 0)
            AS total_discount_percent,

        COALESCE(dp.total_fixed_discount, 0)
            AS total_fixed_discount,

        COALESCE(dp.selected_discount_rules, '[]'::JSONB)
            AS selected_discount_rules,

        COALESCE(sp.total_surcharge_percent, 0)
            AS total_surcharge_percent,

        COALESCE(sp.total_fixed_surcharge, 0)
            AS total_fixed_surcharge,

        COALESCE(tp.total_tax_percent, 0)
            AS total_tax_percent,

        COALESCE(tp.total_fixed_tax, 0)
            AS total_fixed_tax,

        COALESCE(tp.selected_tax_rules, '[]'::JSONB)
            AS selected_tax_rules

    FROM enriched_events e

    LEFT JOIN base_rate_parameters bp
        ON bp.event_id = e.event_id

    LEFT JOIN allowance_parameters ap
        ON ap.event_id = e.event_id

    LEFT JOIN discount_parameters dp
        ON dp.event_id = e.event_id

    LEFT JOIN surcharge_parameters sp
        ON sp.event_id = e.event_id

    LEFT JOIN tax_parameters tp
        ON tp.event_id = e.event_id
)
,

/* ============================================================================
   18. Normalize source quantities and apply bundle allowance
   ============================================================================ */

quantity_calculation AS
(
    SELECT
        ri.*,

        CASE
            WHEN ri.service_type = 'VOICE'
                THEN ri.duration_sec::NUMERIC

            WHEN ri.service_type = 'SMS'
                THEN ri.unit_count

            WHEN ri.service_type = 'DATA'
                THEN ri.volume_kb

            ELSE 0
        END AS original_quantity,

        CASE
            WHEN NOT ri.allowance_rule_applied
                THEN 0

            WHEN ri.service_type = 'VOICE'
                THEN LEAST(
                         ri.duration_sec::NUMERIC,
                         COALESCE(ri.bundle_remaining, 0) * 60
                     )

            WHEN ri.service_type = 'SMS'
                THEN LEAST(
                         ri.unit_count,
                         COALESCE(ri.bundle_remaining, 0)
                     )

            WHEN ri.service_type = 'DATA'
                THEN LEAST(
                         ri.volume_kb,
                         COALESCE(ri.bundle_remaining, 0)
                     )

            ELSE 0
        END AS free_quantity

    FROM rating_inputs ri
)

,

chargeable_quantity AS
(
    SELECT
        qc.*,

        GREATEST(
            qc.original_quantity - qc.free_quantity,
            0
        ) AS chargeable_quantity

    FROM quantity_calculation qc
)

,

/* ============================================================================
   19. Calculate the base usage charge
   ============================================================================ */

calculated_base_charge AS
(
    SELECT
        cq.*,

        CASE

            WHEN cq.base_rule_id IS NULL
                THEN 0

            WHEN cq.service_type = 'VOICE'
                THEN
                (
                    CASE UPPER(cq.quantity_rounding)
                        WHEN 'FLOOR'
                            THEN FLOOR(
                                     cq.chargeable_quantity
                                     /
                                     GREATEST(cq.pulse_seconds, 1)
                                 )

                        WHEN 'NEAREST'
                            THEN ROUND(
                                     cq.chargeable_quantity
                                     /
                                     GREATEST(cq.pulse_seconds, 1)
                                 )

                        ELSE CEIL(
                                 cq.chargeable_quantity
                                 /
                                 GREATEST(cq.pulse_seconds, 1)
                             )
                    END
                )
                *
                (
                    cq.pulse_seconds::NUMERIC
                    /
                    GREATEST(cq.unit_seconds, 1)
                )
                *
                cq.rate

            WHEN cq.service_type = 'SMS'
                THEN
                (
                    cq.chargeable_quantity
                    /
                    GREATEST(cq.unit_count, 1)
                )
                *
                cq.rate

            WHEN cq.service_type = 'DATA'
                THEN
                (
                    CASE UPPER(cq.quantity_rounding)
                        WHEN 'FLOOR'
                            THEN FLOOR(
                                     cq.chargeable_quantity
                                     /
                                     GREATEST(cq.unit_kb, 1)
                                 )

                        WHEN 'NEAREST'
                            THEN ROUND(
                                     cq.chargeable_quantity
                                     /
                                     GREATEST(cq.unit_kb, 1)
                                 )

                        ELSE CEIL(
                                 cq.chargeable_quantity
                                 /
                                 GREATEST(cq.unit_kb, 1)
                             )
                    END
                )
                *
                cq.rate

            ELSE 0

        END AS calculated_usage_charge

    FROM chargeable_quantity cq
),

/* ============================================================================
   20. Apply minimum and maximum event charge
   ============================================================================ */

bounded_base_charge AS
(
    SELECT
        cbc.*,

        CASE
            WHEN cbc.calculated_usage_charge <= 0
                THEN cbc.calculated_usage_charge

            WHEN cbc.maximum_charge IS NOT NULL
                THEN LEAST(
                         GREATEST(
                             cbc.calculated_usage_charge,
                             cbc.minimum_charge
                         ),
                         cbc.maximum_charge
                     )

            ELSE GREATEST(
                     cbc.calculated_usage_charge,
                     cbc.minimum_charge
                 )
        END AS expected_base_charge

    FROM calculated_base_charge cbc
),

/* ============================================================================
   21. Apply discounts
   ============================================================================ */

discount_calculation AS
(
    SELECT
        bbc.*,

        GREATEST
        (
            bbc.expected_base_charge
            -
            (
                bbc.expected_base_charge
                * bbc.total_discount_percent
                / 100
            )
            -
            bbc.total_fixed_discount,
            0
        ) AS expected_after_discount,

        LEAST
        (
            bbc.expected_base_charge,

            (
                bbc.expected_base_charge
                * bbc.total_discount_percent
                / 100
            )
            +
            bbc.total_fixed_discount
        ) AS expected_discount

    FROM bounded_base_charge bbc
)

,

/* ============================================================================
   22. Apply surcharges
   ============================================================================ */

surcharge_calculation AS
(
    SELECT
        dc.*,

        (
            dc.expected_after_discount
            * dc.total_surcharge_percent
            / 100
        )
        +
        dc.total_fixed_surcharge
            AS expected_surcharge,

        dc.expected_after_discount
        +
        (
            dc.expected_after_discount
            * dc.total_surcharge_percent
            / 100
        )
        +
        dc.total_fixed_surcharge
            AS expected_before_tax

    FROM discount_calculation dc
),

/* ============================================================================
   23. Apply taxes
   ============================================================================ */

tax_calculation AS
(
    SELECT
        sc.*,

        (
            sc.expected_before_tax
            * sc.total_tax_percent
            / 100
        )
        +
        sc.total_fixed_tax
            AS expected_tax,

        sc.expected_before_tax
        +
        (
            sc.expected_before_tax
            * sc.total_tax_percent
            / 100
        )
        +
        sc.total_fixed_tax
            AS expected_final_charge

    FROM surcharge_calculation sc
)
--select * from tax_calculation where event_id = 'TC103'
,

/* ============================================================================
   24. Reconciliation and explanation
   ============================================================================ */

final_result AS
(
    SELECT
        tc.*,

        ROUND(
            COALESCE(tc.actual_charge, 0)
            -
            tc.expected_final_charge,
            6
        ) AS charge_variance,

        CASE
            WHEN tc.base_rule_id IS NULL
                THEN 'NO_MATCHING_TARIFF'

            WHEN tc.actual_charge IS NULL
                THEN 'MISSING_REFERENCE_DATA'

            WHEN ABS(
                     tc.actual_charge
                     -
                     tc.expected_final_charge
                 )
                 <=
                 (
                     SELECT reconciliation_tolerance
                     FROM params
                 )
                THEN 'MATCHED'

            WHEN tc.actual_charge < tc.expected_final_charge
                THEN 'UNDERCHARGED'

            WHEN tc.actual_charge > tc.expected_final_charge
                THEN 'OVERCHARGED'

            ELSE 'ERROR'
        END AS reconciliation_status,

        jsonb_build_object
        (
            'base_rule',
                jsonb_build_object
                (
                    'rule_id', tc.base_rule_id,
                    'rule_name', tc.base_rule_name,
                    'rate', tc.rate,
                    'unit_seconds', tc.unit_seconds,
                    'unit_kb', tc.unit_kb,
                    'pulse_seconds', tc.pulse_seconds,
                    'rounding', tc.quantity_rounding,
                    'minimum_charge', tc.minimum_charge,
                    'maximum_charge', tc.maximum_charge
                ),

            'allowance_rule',
                jsonb_build_object
                (
                    'rule_id', tc.allowance_rule_id,
                    'balance_type', tc.allowance_balance_type,
                    'bundle_code', tc.bundle_code,
                    'bundle_remaining', tc.bundle_remaining,
                    'free_quantity', tc.free_quantity
                ),

            'discount_rules',
                tc.selected_discount_rules,

            'tax_rules',
                tc.selected_tax_rules,

            'classification',
                jsonb_build_object
                (
                    'matched_prefix', tc.matched_prefix,
                    'destination_zone', tc.destination_zone,
                    'time_bands', to_jsonb(tc.applicable_time_bands)
                ),

            'calculation',
                jsonb_build_object
                (
                    'original_quantity', tc.original_quantity,
                    'chargeable_quantity', tc.chargeable_quantity,
                    'base_charge', tc.expected_base_charge,
                    'discount', tc.expected_discount,
                    'surcharge', tc.expected_surcharge,
                    'tax', tc.expected_tax,
                    'final_charge', tc.expected_final_charge
                )
        ) AS explanation

    FROM tax_calculation tc
)

/* ============================================================================
   25. Final output
   ============================================================================ */

SELECT
    event_id,
    msisdn,
    account_type,
    offer_code,
    service_type,
    called_number,

    matched_prefix,
    destination_zone,
    applicable_time_bands,

    base_rule_id,
    base_rule_name,
    allowance_rule_id,
    selected_discount_rules,
    selected_tax_rules,

    rate,
    duration_sec,
    volume_kb,
    bundle_remaining,

    ROUND(original_quantity, 6)
        AS original_quantity,

    ROUND(free_quantity, 6)
        AS free_quantity,

    ROUND(chargeable_quantity, 6)
        AS chargeable_quantity,

    ROUND(expected_base_charge, 6)
        AS expected_base_charge,

    ROUND(expected_discount, 6)
        AS expected_discount,

    ROUND(expected_surcharge, 6)
        AS expected_surcharge,

    ROUND(expected_before_tax, 6)
        AS expected_before_tax,

    ROUND(expected_tax, 6)
        AS expected_tax,

    ROUND(expected_final_charge, 6)
        AS expected_final_charge,

    actual_charge,
    charge_variance,
    reconciliation_status,
    expected_currency,
    event_time,
    explanation

FROM final_result

ORDER BY event_time, event_id;
