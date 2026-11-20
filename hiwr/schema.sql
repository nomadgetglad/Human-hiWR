-- HiWR client contact data system (SQLite)
-- One contact record that appointments, shipments, tickets and phone/SMS handoffs link to.
-- History is never overwritten: changes go to contact_events (append-only).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS contacts (
    id                 INTEGER PRIMARY KEY,
    kind               TEXT NOT NULL CHECK (kind IN ('client', 'vendor')),
    display_name       TEXT NOT NULL,
    company            TEXT,
    preferred_language TEXT NOT NULL DEFAULT 'en',      -- 'en', 'es', ...
    notes              TEXT,                            -- allergies, preferences (client-facing, no money)
    merged_into        INTEGER REFERENCES contacts(id), -- set when a duplicate is merged; row is kept
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    created_by         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contact_phones (
    id           INTEGER PRIMARY KEY,
    contact_id   INTEGER NOT NULL REFERENCES contacts(id),
    e164         TEXT NOT NULL,                         -- normalized, e.g. +19105551234
    label        TEXT NOT NULL DEFAULT 'mobile',        -- mobile, work, home
    is_primary   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (contact_id, e164)
);
CREATE INDEX IF NOT EXISTS idx_phones_e164 ON contact_phones(e164);

-- SMS consent is tracked per phone number and per purpose.
-- 'transactional' = appointment and shipment updates; 'marketing' = promotions (needs its own written consent).
-- Latest row per (phone, purpose) wins; rows are never edited.
CREATE TABLE IF NOT EXISTS sms_consent (
    id          INTEGER PRIMARY KEY,
    e164        TEXT NOT NULL,
    purpose     TEXT NOT NULL CHECK (purpose IN ('transactional', 'marketing')),
    status      TEXT NOT NULL CHECK (status IN ('granted', 'revoked')),
    source      TEXT NOT NULL,                          -- 'booking form', 'verbal at counter', 'STOP reply'
    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    recorded_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_consent ON sms_consent(e164, purpose, recorded_at);

CREATE TABLE IF NOT EXISTS appointments (
    id          INTEGER PRIMARY KEY,
    contact_id  INTEGER NOT NULL REFERENCES contacts(id),
    starts_at   TEXT NOT NULL,                          -- local time, ISO 8601
    ends_at     TEXT,
    party_size  INTEGER,
    quote_cents INTEGER,                                -- quoted price, used for fees
    kind        TEXT NOT NULL DEFAULT 'reservation',    -- reservation, catering, fitting, clinic_visit
    workflow    TEXT,                                   -- e.g. 'clinic_checkin'; stages live in workflow_stages
    details     TEXT,                                   -- clinic: NEVER reason for visit or clinical data
    ticket_id   TEXT                                    -- links to tickets.json when a dispute exists
);

CREATE TABLE IF NOT EXISTS shipments (
    id           INTEGER PRIMARY KEY,
    contact_id   INTEGER NOT NULL REFERENCES contacts(id), -- vendor for inbound, client for outbound
    direction    TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    invoice_ref  TEXT,
    po_ref       TEXT,
    carrier      TEXT,
    tracking     TEXT,
    expected_at  TEXT,
    arrived_at   TEXT,
    status       TEXT NOT NULL DEFAULT 'expected'
                 CHECK (status IN ('expected', 'in_transit', 'delivered', 'short', 'rejected', 'returned', 'cancelled')),
    ticket_id    TEXT                                    -- SHIPMENT ticket when something went wrong
);

-- Append-only history: creations, edits, merges, consent changes, calls and texts (live-person handoffs).
CREATE TABLE IF NOT EXISTS contact_events (
    id          INTEGER PRIMARY KEY,
    contact_id  INTEGER NOT NULL REFERENCES contacts(id),
    at          TEXT NOT NULL DEFAULT (datetime('now')),
    actor       TEXT NOT NULL,
    event       TEXT NOT NULL,                          -- created, phone_added, merged, call, sms_sent, sms_received, ...
    detail      TEXT,                                   -- JSON
    ticket_id   TEXT
);

CREATE TRIGGER IF NOT EXISTS contact_events_no_update
BEFORE UPDATE ON contact_events BEGIN SELECT RAISE(ABORT, 'contact_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS contact_events_no_delete
BEFORE DELETE ON contact_events BEGIN SELECT RAISE(ABORT, 'contact_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS sms_consent_no_update
BEFORE UPDATE ON sms_consent BEGIN SELECT RAISE(ABORT, 'sms_consent is append-only'); END;

-- Staff view: kitchen and floor staff see names, appointment details and the last 4 digits only.
CREATE VIEW IF NOT EXISTS v_staff_contacts AS
SELECT c.id, c.display_name, c.kind, c.preferred_language, c.notes,
       '***-***-' || substr(p.e164, -4) AS phone_masked
FROM contacts c
LEFT JOIN contact_phones p ON p.contact_id = c.id AND p.is_primary = 1
WHERE c.merged_into IS NULL;

-- Current consent per phone and purpose.
CREATE VIEW IF NOT EXISTS v_current_consent AS
SELECT s.e164, s.purpose, s.status, s.source, s.recorded_at
FROM sms_consent s
WHERE s.id = (SELECT s2.id FROM sms_consent s2
              WHERE s2.e164 = s.e164 AND s2.purpose = s.purpose
              ORDER BY s2.recorded_at DESC, s2.id DESC LIMIT 1);

-- ===================================================================
-- Orders and production line: one order record, workflow stages per business type.
-- Works for a restaurant's live orders and for made-to-order goods (e.g. boots).
-- ===================================================================

-- Each business type defines its own ordered stages.
CREATE TABLE IF NOT EXISTS workflows (
    id     INTEGER PRIMARY KEY,
    name   TEXT NOT NULL UNIQUE                  -- 'restaurant_live', 'boots_made_to_order'
);
CREATE TABLE IF NOT EXISTS workflow_stages (
    workflow_id INTEGER NOT NULL REFERENCES workflows(id),
    seq         INTEGER NOT NULL,                -- order of the stage
    stage       TEXT NOT NULL,
    station     TEXT,                            -- grill, fry, cold line / cutting, stitching
    PRIMARY KEY (workflow_id, seq),
    UNIQUE (workflow_id, stage)
);

CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY,
    contact_id   INTEGER REFERENCES contacts(id),   -- may be NULL for a walk-in table order
    workflow_id  INTEGER NOT NULL REFERENCES workflows(id),
    placed_at    TEXT NOT NULL DEFAULT (datetime('now')),
    due_at       TEXT,                               -- table time, pickup time, promised ship date
    fulfillment  TEXT NOT NULL CHECK (fulfillment IN ('dine_in', 'pickup', 'delivery', 'ship')),
    appointment_id INTEGER REFERENCES appointments(id),
    shipment_id    INTEGER REFERENCES shipments(id),
    source_ref   TEXT,                               -- POS check #, web order #
    ticket_id    TEXT                                -- DISPUTE ticket if one exists
);

CREATE TABLE IF NOT EXISTS order_items (
    id        INTEGER PRIMARY KEY,
    order_id  INTEGER NOT NULL REFERENCES orders(id),
    item      TEXT NOT NULL,                         -- 'Burger', 'Western boot, roper toe'
    qty       INTEGER NOT NULL DEFAULT 1,
    specs     TEXT                                   -- JSON: modifiers/allergies, or measurements/leather/size
);

-- Append-only stage history; the current stage is the latest row.
CREATE TABLE IF NOT EXISTS order_stage_events (
    id        INTEGER PRIMARY KEY,
    order_id  INTEGER NOT NULL REFERENCES orders(id),
    item_id   INTEGER REFERENCES order_items(id),    -- NULL = whole order
    stage     TEXT NOT NULL,
    at        TEXT NOT NULL DEFAULT (datetime('now')),
    actor     TEXT NOT NULL,
    note      TEXT
);
CREATE TRIGGER IF NOT EXISTS order_stage_events_no_update
BEFORE UPDATE ON order_stage_events BEGIN SELECT RAISE(ABORT, 'order_stage_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS order_stage_events_no_delete
BEFORE DELETE ON order_stage_events BEGIN SELECT RAISE(ABORT, 'order_stage_events is append-only'); END;

-- Production board: current stage of each item (what a kitchen display or shop board shows).
CREATE VIEW IF NOT EXISTS v_production_board AS
SELECT o.id AS order_id, i.id AS item_id, w.name AS workflow, i.item, i.qty, i.specs,
       o.fulfillment, o.due_at, c.display_name AS client,
       e.stage AS current_stage, ws.station, e.at AS since
FROM order_items i
JOIN orders o     ON o.id = i.order_id
JOIN workflows w  ON w.id = o.workflow_id
LEFT JOIN contacts c ON c.id = o.contact_id
LEFT JOIN order_stage_events e ON e.id = (
    SELECT e2.id FROM order_stage_events e2
    WHERE e2.order_id = o.id AND (e2.item_id = i.id OR e2.item_id IS NULL)
    ORDER BY e2.at DESC, e2.id DESC LIMIT 1)
LEFT JOIN workflow_stages ws ON ws.workflow_id = w.id AND ws.stage = e.stage;

-- Appointment check-in history (doctor's office, salon, boot fitting). Current stage = latest row.
-- Clinic stages: booked, confirmed, arrived, checked_in, roomed, seen, checked_out, no_show, cancelled.
CREATE TABLE IF NOT EXISTS appointment_events (
    id              INTEGER PRIMARY KEY,
    appointment_id  INTEGER NOT NULL REFERENCES appointments(id),
    stage           TEXT NOT NULL,
    at              TEXT NOT NULL DEFAULT (datetime('now')),
    actor           TEXT NOT NULL,
    note            TEXT
);
CREATE TRIGGER IF NOT EXISTS appointment_events_no_update
BEFORE UPDATE ON appointment_events BEGIN SELECT RAISE(ABORT, 'appointment_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS appointment_events_no_delete
BEFORE DELETE ON appointment_events BEGIN SELECT RAISE(ABORT, 'appointment_events is append-only'); END;

-- Read audit: who looked at which contact (required practice for a clinic under HIPAA).
CREATE TABLE IF NOT EXISTS access_log (
    id          INTEGER PRIMARY KEY,
    at          TEXT NOT NULL DEFAULT (datetime('now')),
    actor       TEXT NOT NULL,
    role        TEXT NOT NULL,
    contact_id  INTEGER REFERENCES contacts(id),
    action      TEXT NOT NULL                        -- view, export, edit
);
CREATE TRIGGER IF NOT EXISTS access_log_no_update
BEFORE UPDATE ON access_log BEGIN SELECT RAISE(ABORT, 'access_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS access_log_no_delete
BEFORE DELETE ON access_log BEGIN SELECT RAISE(ABORT, 'access_log is append-only'); END;

-- Today's check-in board.
CREATE VIEW IF NOT EXISTS v_checkin_board AS
SELECT a.id AS appointment_id, a.starts_at, c.display_name, a.kind,
       e.stage AS current_stage, e.at AS since
FROM appointments a
JOIN contacts c ON c.id = a.contact_id
LEFT JOIN appointment_events e ON e.id = (
    SELECT e2.id FROM appointment_events e2 WHERE e2.appointment_id = a.id
    ORDER BY e2.at DESC, e2.id DESC LIMIT 1);

-- ===================================================================
-- Authority: certified approvers, money actions, result check-ins, assignments.
-- ===================================================================

-- Who is certified to approve which money level. Granted by an owner, revocable, never edited.
CREATE TABLE IF NOT EXISTS approver_certifications (
    id          INTEGER PRIMARY KEY,
    member      TEXT NOT NULL,
    scope       TEXT NOT NULL CHECK (scope IN ('refund', 'payroll', 'fee', 'subcontract')),
    status      TEXT NOT NULL CHECK (status IN ('granted', 'revoked')),
    granted_by  TEXT NOT NULL,
    at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Refunds, credits, chargebacks, payroll runs: PENDING until two certified approvers sign.
CREATE TABLE IF NOT EXISTS money_actions (
    id            INTEGER PRIMARY KEY,
    scope         TEXT NOT NULL CHECK (scope IN ('refund', 'payroll', 'fee', 'subcontract')),
    amount_cents  INTEGER NOT NULL,
    description   TEXT NOT NULL,
    requested_by  TEXT NOT NULL,
    ticket_id     TEXT,
    requested_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS approvals (
    id               INTEGER PRIMARY KEY,
    money_action_id  INTEGER NOT NULL REFERENCES money_actions(id),
    approver         TEXT NOT NULL,
    decision         TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
    at               TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (money_action_id, approver)
);

-- Active check-in on any result: does it work, and did it have the intended consequence?
CREATE TABLE IF NOT EXISTS result_checkins (
    id                   INTEGER PRIMARY KEY,
    subject              TEXT NOT NULL,            -- 'money_action:3', 'order:12', 'report:weekly'
    checked_by           TEXT NOT NULL,
    works                INTEGER NOT NULL,         -- 1 yes, 0 no
    intended_consequence INTEGER NOT NULL,         -- 1 yes, 0 no
    note                 TEXT,
    at                   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Plan owners assign members daily tasks, analysis goals, calls and appointments.
CREATE TABLE IF NOT EXISTS assignments (
    id              INTEGER PRIMARY KEY,
    assigned_by     TEXT NOT NULL,
    assignee        TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('task', 'analysis', 'call', 'appointment')),
    title           TEXT NOT NULL,
    due_on          TEXT,
    goal            TEXT,                           -- analysis: the work goal it serves
    contact_id      INTEGER REFERENCES contacts(id),
    appointment_id  INTEGER REFERENCES appointments(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS assignment_events (
    id             INTEGER PRIMARY KEY,
    assignment_id  INTEGER NOT NULL REFERENCES assignments(id),
    status         TEXT NOT NULL CHECK (status IN ('assigned', 'started', 'done', 'blocked', 'cancelled')),
    actor          TEXT NOT NULL,
    note           TEXT,
    at             TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS plan_owners (
    member  TEXT PRIMARY KEY,
    added_by TEXT NOT NULL,
    at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TRIGGER IF NOT EXISTS approver_certifications_no_update BEFORE UPDATE ON approver_certifications BEGIN SELECT RAISE(ABORT, 'approver_certifications is append-only'); END;
CREATE TRIGGER IF NOT EXISTS approver_certifications_no_delete BEFORE DELETE ON approver_certifications BEGIN SELECT RAISE(ABORT, 'approver_certifications is append-only'); END;
CREATE TRIGGER IF NOT EXISTS approvals_no_update BEFORE UPDATE ON approvals BEGIN SELECT RAISE(ABORT, 'approvals is append-only'); END;
CREATE TRIGGER IF NOT EXISTS approvals_no_delete BEFORE DELETE ON approvals BEGIN SELECT RAISE(ABORT, 'approvals is append-only'); END;
CREATE TRIGGER IF NOT EXISTS result_checkins_no_update BEFORE UPDATE ON result_checkins BEGIN SELECT RAISE(ABORT, 'result_checkins is append-only'); END;
CREATE TRIGGER IF NOT EXISTS result_checkins_no_delete BEFORE DELETE ON result_checkins BEGIN SELECT RAISE(ABORT, 'result_checkins is append-only'); END;
CREATE TRIGGER IF NOT EXISTS assignment_events_no_update BEFORE UPDATE ON assignment_events BEGIN SELECT RAISE(ABORT, 'assignment_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS assignment_events_no_delete BEFORE DELETE ON assignment_events BEGIN SELECT RAISE(ABORT, 'assignment_events is append-only'); END;

-- ===================================================================
-- Scheduling: staff availability, equipment, cancellation fee policy.
-- ===================================================================
CREATE TABLE IF NOT EXISTS staff (
    member           TEXT PRIMARY KEY,
    role             TEXT NOT NULL,               -- cook, server, fitter, nurse
    hourly_cents     INTEGER NOT NULL,
    min_call_in_hours REAL NOT NULL DEFAULT 0     -- hours paid if a booked shift is cancelled late
);
CREATE TABLE IF NOT EXISTS staff_availability (
    member      TEXT NOT NULL REFERENCES staff(member),
    day         TEXT NOT NULL,                    -- YYYY-MM-DD
    start_hour  REAL NOT NULL,
    end_hour    REAL NOT NULL,
    PRIMARY KEY (member, day)
);
CREATE TABLE IF NOT EXISTS staff_booked_hours (     -- hours already on the schedule that week (for overtime)
    member      TEXT NOT NULL REFERENCES staff(member),
    week_start  TEXT NOT NULL,
    hours       REAL NOT NULL,
    PRIMARY KEY (member, week_start)
);
CREATE TABLE IF NOT EXISTS equipment (
    name             TEXT PRIMARY KEY,
    cost_cents       INTEGER NOT NULL,            -- rental or use cost per booking
    cancel_fee_cents INTEGER NOT NULL DEFAULT 0,  -- what the supplier charges if cancelled late
    cancel_notice_hours INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS equipment_blackout (
    name  TEXT NOT NULL REFERENCES equipment(name),
    day   TEXT NOT NULL,
    PRIMARY KEY (name, day)
);
CREATE TABLE IF NOT EXISTS appointment_requirements (
    appointment_id  INTEGER NOT NULL REFERENCES appointments(id),
    need_type       TEXT NOT NULL CHECK (need_type IN ('role', 'equipment')),
    need            TEXT NOT NULL,                -- 'cook' or 'chafing set'
    qty             INTEGER NOT NULL DEFAULT 1,
    assigned        TEXT                          -- member or equipment name once scheduled
);
CREATE TABLE IF NOT EXISTS fee_policy (
    kind              TEXT PRIMARY KEY,           -- appointment kind, e.g. 'catering'
    notice_hours      INTEGER NOT NULL,           -- cancel later than this before start = late
    late_fee_pct      REAL NOT NULL,              -- of the quoted price
    reschedule_waives INTEGER NOT NULL DEFAULT 0  -- 1 = fee waived if rebooked within 14 days
);

-- ===================================================================
-- Waitlist, dining tables, tickets, and hot/calculated processes.
-- ===================================================================
CREATE TABLE IF NOT EXISTS waitlist (
    id            INTEGER PRIMARY KEY,
    contact_id    INTEGER NOT NULL REFERENCES contacts(id),
    provider      TEXT NOT NULL,                   -- staff member, e.g. dr_patel
    appointment_id INTEGER REFERENCES appointments(id), -- the later booking they hold now
    wants_before  TEXT NOT NULL,                   -- only offer slots before this
    joined_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS waitlist_events (
    id           INTEGER PRIMARY KEY,
    waitlist_id  INTEGER NOT NULL REFERENCES waitlist(id),
    event        TEXT NOT NULL,                    -- offered, accepted, declined, expired, removed
    slot         TEXT,
    at           TEXT NOT NULL,
    hold_until   TEXT
);

CREATE TABLE IF NOT EXISTS dining_tables (
    name         TEXT PRIMARY KEY,
    seats        INTEGER NOT NULL,
    join_group   TEXT,                             -- tables that can be pushed together
    join_order   INTEGER                           -- position in that row (only neighbours join)
);
CREATE TABLE IF NOT EXISTS table_assignments (       -- append-only; latest row per appointment wins
    id             INTEGER PRIMARY KEY,
    appointment_id INTEGER NOT NULL REFERENCES appointments(id),
    tables         TEXT NOT NULL,                  -- 'T1' or 'T1+T2+T3'
    actor          TEXT NOT NULL,
    reason         TEXT,
    at             TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Local mirror of v7 tickets (tickets.json stays the source; ticket_sync.py keeps them aligned).
CREATE TABLE IF NOT EXISTS tickets (
    id        TEXT PRIMARY KEY,                    -- SHP-0001, DSP-0001
    type      TEXT NOT NULL CHECK (type IN ('DISPUTE', 'SHIPMENT', 'STAFFING', 'INSPECTION')),
    status    TEXT NOT NULL DEFAULT 'new',
    priority  INTEGER NOT NULL,                    -- 1 highest
    subject   TEXT NOT NULL,
    financial TEXT,                                -- JSON financial block; hidden from non-accounting roles
    opened_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ticket_events (
    id        INTEGER PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES tickets(id),
    at        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    event     TEXT NOT NULL,
    detail    TEXT
);

-- Processes: 'hot' = time-critical, checked against a running clock (cooling, live shipment at the dock).
-- 'calculated' = deadlines worked backward from a target date (ship-by, production test schedule).
CREATE TABLE IF NOT EXISTS processes (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,                     -- cooling, order_shipment, production_test
    class       TEXT NOT NULL CHECK (class IN ('hot', 'calculated')),
    subject     TEXT NOT NULL,                     -- 'order:12', 'batch:chili-1120'
    started_at  TEXT NOT NULL,
    deadline    TEXT,
    ticket_id   TEXT REFERENCES tickets(id)
);
CREATE TABLE IF NOT EXISTS process_checkpoints (
    id          INTEGER PRIMARY KEY,
    process_id  INTEGER NOT NULL REFERENCES processes(id),
    seq         INTEGER NOT NULL,
    name        TEXT NOT NULL,
    due_at      TEXT NOT NULL,
    rule        TEXT,                              -- JSON, e.g. {"max_f": 70}
    UNIQUE (process_id, seq)
);
CREATE TABLE IF NOT EXISTS process_readings (        -- append-only
    id             INTEGER PRIMARY KEY,
    checkpoint_id  INTEGER NOT NULL REFERENCES process_checkpoints(id),
    at             TEXT NOT NULL,
    actor          TEXT NOT NULL,
    value          TEXT,                           -- JSON
    passed         INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS waitlist_events_no_update BEFORE UPDATE ON waitlist_events BEGIN SELECT RAISE(ABORT, 'waitlist_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS waitlist_events_no_delete BEFORE DELETE ON waitlist_events BEGIN SELECT RAISE(ABORT, 'waitlist_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS table_assignments_no_update BEFORE UPDATE ON table_assignments BEGIN SELECT RAISE(ABORT, 'table_assignments is append-only'); END;
CREATE TRIGGER IF NOT EXISTS table_assignments_no_delete BEFORE DELETE ON table_assignments BEGIN SELECT RAISE(ABORT, 'table_assignments is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ticket_events_no_update BEFORE UPDATE ON ticket_events BEGIN SELECT RAISE(ABORT, 'ticket_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ticket_events_no_delete BEFORE DELETE ON ticket_events BEGIN SELECT RAISE(ABORT, 'ticket_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS process_readings_no_update BEFORE UPDATE ON process_readings BEGIN SELECT RAISE(ABORT, 'process_readings is append-only'); END;
CREATE TRIGGER IF NOT EXISTS process_readings_no_delete BEFORE DELETE ON process_readings BEGIN SELECT RAISE(ABORT, 'process_readings is append-only'); END;

-- HACCP-style corrective action log (append-only).
CREATE TABLE IF NOT EXISTS corrective_actions (
    id           INTEGER PRIMARY KEY,
    at           TEXT NOT NULL,
    process_id   INTEGER REFERENCES processes(id),
    what_failed  TEXT NOT NULL,
    reading      TEXT,
    action       TEXT NOT NULL,                    -- reheat to 165F and restart cooling, discard, reject
    actor        TEXT NOT NULL,
    verified_by  TEXT                              -- manager; NULL until verified (a new row records it)
);
CREATE TRIGGER IF NOT EXISTS corrective_actions_no_delete BEFORE DELETE ON corrective_actions
BEGIN SELECT RAISE(ABORT, 'corrective_actions is append-only'); END;

-- ===================================================================
-- Hiring: positions, candidates, certifications, availability, matching.
-- No fields for age, sex, race, religion, national origin, disability, family status
-- or any other protected trait. Work style is self-reported and job-related only.
-- ===================================================================
CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY,
    role            TEXT NOT NULL,
    title           TEXT NOT NULL,
    required_certs  TEXT NOT NULL DEFAULT '[]',   -- JSON list, e.g. ["food_handler"]
    preferred_certs TEXT NOT NULL DEFAULT '[]',
    min_experience_months INTEGER NOT NULL DEFAULT 0,
    work_style      TEXT,                          -- JSON: what the job actually involves
    hourly_cents    INTEGER NOT NULL,
    opened_by       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    phone       TEXT,
    language    TEXT NOT NULL DEFAULT 'en',
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS candidate_certs (
    id           INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    cert         TEXT NOT NULL,                    -- food_handler, servsafe_manager, alcohol_server
    number       TEXT,
    expires_on   TEXT,
    verified_by  TEXT,                             -- NULL = not yet checked against the issuer
    verified_at  TEXT
);
CREATE TABLE IF NOT EXISTS candidate_experience (
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    role         TEXT NOT NULL,
    months       INTEGER NOT NULL,
    place        TEXT
);
CREATE TABLE IF NOT EXISTS candidate_availability (
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    weekday      INTEGER NOT NULL,                 -- 0 Monday .. 6 Sunday
    start_hour   REAL NOT NULL,
    end_hour     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS candidate_work_style (    -- self-reported answers to job-related questions
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    question     TEXT NOT NULL,                    -- fast_paced, team_based, customer_facing, late_nights
    answer       TEXT NOT NULL,                    -- yes / no / sometimes
    PRIMARY KEY (candidate_id, question)
);
-- The swipe: each side says yes or no; both yes = a match worth an interview.
CREATE TABLE IF NOT EXISTS swipes (
    id           INTEGER PRIMARY KEY,
    position_id  INTEGER NOT NULL REFERENCES positions(id),
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    side         TEXT NOT NULL CHECK (side IN ('candidate', 'employer')),
    decision     TEXT NOT NULL CHECK (decision IN ('yes', 'no')),
    actor        TEXT NOT NULL,
    at           TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS hire_decisions (
    id           INTEGER PRIMARY KEY,
    position_id  INTEGER NOT NULL REFERENCES positions(id),
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    decision     TEXT NOT NULL CHECK (decision IN ('hire', 'interview', 'decline', 'hold')),
    decided_by   TEXT NOT NULL,
    reason       TEXT NOT NULL,                    -- job-related reason, required
    ticket_id    TEXT,
    at           TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TRIGGER IF NOT EXISTS swipes_no_update BEFORE UPDATE ON swipes BEGIN SELECT RAISE(ABORT, 'swipes is append-only'); END;
CREATE TRIGGER IF NOT EXISTS swipes_no_delete BEFORE DELETE ON swipes BEGIN SELECT RAISE(ABORT, 'swipes is append-only'); END;
CREATE TRIGGER IF NOT EXISTS hire_decisions_no_update BEFORE UPDATE ON hire_decisions BEGIN SELECT RAISE(ABORT, 'hire_decisions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS hire_decisions_no_delete BEFORE DELETE ON hire_decisions BEGIN SELECT RAISE(ABORT, 'hire_decisions is append-only'); END;

-- ===================================================================
-- Human (employee module): staffing rules, production quotas, materials, purchase drafts.
-- ===================================================================
CREATE TABLE IF NOT EXISTS staffing_rules (
    role        TEXT NOT NULL,
    driver      TEXT NOT NULL CHECK (driver IN ('covers', 'units', 'appointments')),
    per_staff   REAL NOT NULL,                     -- 1 cook per 40 covers, 1 maker per 2 pairs/day
    minimum     INTEGER NOT NULL DEFAULT 0,
    start_hour  REAL NOT NULL,
    end_hour    REAL NOT NULL,
    PRIMARY KEY (role, driver)
);
CREATE TABLE IF NOT EXISTS production_quota (
    day      TEXT NOT NULL,
    product  TEXT NOT NULL,
    units    REAL NOT NULL,
    PRIMARY KEY (day, product)
);
CREATE TABLE IF NOT EXISTS bill_of_materials (
    product   TEXT NOT NULL,                       -- 'dinner_cover' for restaurants, 'boot_pair'
    material  TEXT NOT NULL,
    per_unit  REAL NOT NULL,
    PRIMARY KEY (product, material)
);
CREATE TABLE IF NOT EXISTS materials (
    material     TEXT PRIMARY KEY,
    unit         TEXT NOT NULL,
    on_hand      REAL NOT NULL,
    perishable   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS material_sources (
    material        TEXT NOT NULL REFERENCES materials(material),
    vendor          TEXT NOT NULL,
    lead_days       INTEGER NOT NULL,
    unit_cost_cents INTEGER NOT NULL,
    min_order       REAL NOT NULL DEFAULT 1,
    preferred       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (material, vendor)
);
CREATE TABLE IF NOT EXISTS purchase_drafts (
    id           INTEGER PRIMARY KEY,
    material     TEXT NOT NULL,
    vendor       TEXT NOT NULL,
    qty          REAL NOT NULL,
    cost_cents   INTEGER NOT NULL,
    order_by     TEXT NOT NULL,
    needed_on    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'draft',   -- draft until a manager approves and sends it
    made_by      TEXT NOT NULL,
    at           TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ===================================================================
-- Work agreements (temporary and regular), shifts, punches, conduct on both sides.
-- ===================================================================
CREATE TABLE IF NOT EXISTS work_agreements (
    id              INTEGER PRIMARY KEY,
    position_id     INTEGER REFERENCES positions(id),
    candidate_id    INTEGER REFERENCES candidates(id),
    member          TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('temporary', 'regular')),
    starts_on       TEXT NOT NULL,
    ends_on         TEXT,
    hourly_cents    INTEGER NOT NULL,
    min_hours_per_shift REAL NOT NULL DEFAULT 0,  -- employer promise if a shift is cut or cancelled late
    terms_version   TEXT NOT NULL,
    signed_worker_at   TEXT,
    signed_employer_at TEXT,
    signed_employer_by TEXT
);
CREATE TABLE IF NOT EXISTS shifts (
    id            INTEGER PRIMARY KEY,
    agreement_id  INTEGER NOT NULL REFERENCES work_agreements(id),
    starts_at     TEXT NOT NULL,
    ends_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shift_events (           -- append-only: punches, cancellations, notices
    id        INTEGER PRIMARY KEY,
    shift_id  INTEGER NOT NULL REFERENCES shifts(id),
    event     TEXT NOT NULL,                       -- clock_in, clock_out, cancelled_by_employer, called_out, no_show
    at        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    note      TEXT
);
CREATE TABLE IF NOT EXISTS conduct_events (         -- append-only record of conditions not honored, either side
    id            INTEGER PRIMARY KEY,
    agreement_id  INTEGER NOT NULL REFERENCES work_agreements(id),
    party         TEXT NOT NULL CHECK (party IN ('worker', 'employer')),
    kind          TEXT NOT NULL,                   -- late, no_call_no_show, left_early_no_notice, late_cancel, hours_cut, late_pay
    shift_id      INTEGER REFERENCES shifts(id),
    at            TEXT NOT NULL,
    detail        TEXT,
    response      TEXT                             -- warning, call-in pay owed, assignment ended, ...
);
CREATE TRIGGER IF NOT EXISTS shift_events_no_update BEFORE UPDATE ON shift_events BEGIN SELECT RAISE(ABORT, 'shift_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS shift_events_no_delete BEFORE DELETE ON shift_events BEGIN SELECT RAISE(ABORT, 'shift_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS conduct_events_no_update BEFORE UPDATE ON conduct_events BEGIN SELECT RAISE(ABORT, 'conduct_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS conduct_events_no_delete BEFORE DELETE ON conduct_events BEGIN SELECT RAISE(ABORT, 'conduct_events is append-only'); END;

-- HR requests shown in the Staff section (time off, swaps, pay questions, documents).
CREATE TABLE IF NOT EXISTS hr_requests (
    id          INTEGER PRIMARY KEY,
    member      TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('time_off', 'swap', 'pay_question', 'document', 'other')),
    from_day    TEXT,
    to_day      TEXT,
    detail      TEXT,
    at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hr_request_events (
    id          INTEGER PRIMARY KEY,
    request_id  INTEGER NOT NULL REFERENCES hr_requests(id),
    status      TEXT NOT NULL CHECK (status IN ('approved', 'denied', 'answered')),
    actor       TEXT NOT NULL,
    note        TEXT,
    at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shift_offers (
    id            INTEGER PRIMARY KEY,
    candidate_id  INTEGER NOT NULL REFERENCES candidates(id),
    role          TEXT NOT NULL,
    starts_at     TEXT NOT NULL,
    ends_at       TEXT NOT NULL,
    hourly_cents  INTEGER NOT NULL,
    sent_at       TEXT NOT NULL,
    hold_until    TEXT NOT NULL,
    answer        TEXT,                            -- yes / no / NULL
    answered_at   TEXT
);

-- ===================================================================
-- Access: visibility levels set by the account holder, task certifications that can be passed on.
-- ===================================================================
CREATE TABLE IF NOT EXISTS member_levels (          -- append-only; latest row per member wins
    id      INTEGER PRIMARY KEY,
    member  TEXT NOT NULL,
    level   INTEGER NOT NULL CHECK (level BETWEEN 0 AND 4),
    set_by  TEXT NOT NULL,
    at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS task_grants (
    id            INTEGER PRIMARY KEY,
    member        TEXT NOT NULL,
    task          TEXT NOT NULL,                   -- inventory_audit, roll_call, receiving_check, payroll_approval ...
    granted_by    TEXT NOT NULL,
    can_delegate  INTEGER NOT NULL DEFAULT 0,      -- may certify others for this task
    expires_on    TEXT,
    at            TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS task_revocations (
    grant_id    INTEGER NOT NULL REFERENCES task_grants(id),
    revoked_by  TEXT NOT NULL,
    reason      TEXT,
    at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TRIGGER IF NOT EXISTS member_levels_no_update BEFORE UPDATE ON member_levels BEGIN SELECT RAISE(ABORT, 'member_levels is append-only'); END;
CREATE TRIGGER IF NOT EXISTS member_levels_no_delete BEFORE DELETE ON member_levels BEGIN SELECT RAISE(ABORT, 'member_levels is append-only'); END;
CREATE TRIGGER IF NOT EXISTS task_grants_no_update BEFORE UPDATE ON task_grants BEGIN SELECT RAISE(ABORT, 'task_grants is append-only'); END;
CREATE TRIGGER IF NOT EXISTS task_grants_no_delete BEFORE DELETE ON task_grants BEGIN SELECT RAISE(ABORT, 'task_grants is append-only'); END;
CREATE TRIGGER IF NOT EXISTS task_revocations_no_update BEFORE UPDATE ON task_revocations BEGIN SELECT RAISE(ABORT, 'task_revocations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS task_revocations_no_delete BEFORE DELETE ON task_revocations BEGIN SELECT RAISE(ABORT, 'task_revocations is append-only'); END;

-- ===================================================================
-- Retail: products, daily sales, sale proposals.
-- ===================================================================
CREATE TABLE IF NOT EXISTS products (
    sku          TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL,
    price_cents  INTEGER NOT NULL,
    cost_cents   INTEGER NOT NULL,
    shelf        INTEGER NOT NULL,                 -- units on the floor
    back_stock   INTEGER NOT NULL,                 -- units in the back
    expires_on   TEXT                              -- nearest expiry of stock on hand
);
CREATE TABLE IF NOT EXISTS daily_sales (
    sku    TEXT NOT NULL REFERENCES products(sku),
    day    TEXT NOT NULL,
    units  INTEGER NOT NULL,
    PRIMARY KEY (sku, day)
);
CREATE TABLE IF NOT EXISTS sale_proposals (
    id           INTEGER PRIMARY KEY,
    sku          TEXT NOT NULL REFERENCES products(sku),
    kind         TEXT NOT NULL CHECK (kind IN ('markdown', 'bundle')),
    pct_off      REAL NOT NULL,
    bundle_with  TEXT REFERENCES products(sku),
    starts_on    TEXT NOT NULL,
    ends_on      TEXT NOT NULL,
    forecast     TEXT NOT NULL,                    -- JSON: units, revenue, margin vs no sale
    proposed_by  TEXT NOT NULL,
    at           TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sale_decisions (
    proposal_id  INTEGER NOT NULL REFERENCES sale_proposals(id),
    decision     TEXT NOT NULL CHECK (decision IN ('approved', 'rejected', 'changed')),
    decided_by   TEXT NOT NULL,
    note         TEXT,
    at           TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TRIGGER IF NOT EXISTS sale_proposals_no_update BEFORE UPDATE ON sale_proposals BEGIN SELECT RAISE(ABORT, 'sale_proposals is append-only'); END;
CREATE TRIGGER IF NOT EXISTS sale_proposals_no_delete BEFORE DELETE ON sale_proposals BEGIN SELECT RAISE(ABORT, 'sale_proposals is append-only'); END;
CREATE TRIGGER IF NOT EXISTS sale_decisions_no_update BEFORE UPDATE ON sale_decisions BEGIN SELECT RAISE(ABORT, 'sale_decisions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS sale_decisions_no_delete BEFORE DELETE ON sale_decisions BEGIN SELECT RAISE(ABORT, 'sale_decisions is append-only'); END;

-- ===================================================================
-- Lab: long jobs (e.g. building a house): phases, inspections, subcontracts, draws, crew time.
-- ===================================================================
CREATE TABLE IF NOT EXISTS lab_projects (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    contact_id      INTEGER REFERENCES contacts(id),
    contract_cents  INTEGER NOT NULL,
    starts_on       TEXT NOT NULL,
    target_end      TEXT NOT NULL,
    permit_number   TEXT
);
CREATE TABLE IF NOT EXISTS lab_phases (
    id                INTEGER PRIMARY KEY,
    project_id        INTEGER NOT NULL REFERENCES lab_projects(id),
    seq               INTEGER NOT NULL,
    name              TEXT NOT NULL,
    labor_budget_cents    INTEGER NOT NULL DEFAULT 0,
    material_budget_cents INTEGER NOT NULL DEFAULT 0,
    inspection        TEXT,                         -- required inspection to close the phase
    UNIQUE (project_id, seq)
);
CREATE TABLE IF NOT EXISTS lab_phase_events (        -- append-only: started, completed, blocked
    id        INTEGER PRIMARY KEY,
    phase_id  INTEGER NOT NULL REFERENCES lab_phases(id),
    event     TEXT NOT NULL,
    at        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    note      TEXT
);
CREATE TABLE IF NOT EXISTS lab_inspections (          -- append-only
    id         INTEGER PRIMARY KEY,
    phase_id   INTEGER NOT NULL REFERENCES lab_phases(id),
    kind       TEXT NOT NULL,
    at         TEXT NOT NULL,
    inspector  TEXT NOT NULL,
    result     TEXT NOT NULL CHECK (result IN ('pass', 'fail')),
    notes      TEXT,
    ticket_id  TEXT
);
CREATE TABLE IF NOT EXISTS lab_subcontracts (
    id                 INTEGER PRIMARY KEY,
    project_id         INTEGER NOT NULL REFERENCES lab_projects(id),
    phase_id           INTEGER NOT NULL REFERENCES lab_phases(id),
    sub                TEXT NOT NULL,
    scope              TEXT NOT NULL,
    amount_cents       INTEGER NOT NULL,
    retainage_pct      REAL NOT NULL DEFAULT 0.10,
    license_no         TEXT,
    insurance_expires  TEXT
);
CREATE TABLE IF NOT EXISTS lab_draws (                -- append-only pay applications from subs
    id               INTEGER PRIMARY KEY,
    subcontract_id   INTEGER NOT NULL REFERENCES lab_subcontracts(id),
    at               TEXT NOT NULL,
    pct_complete     REAL NOT NULL,
    gross_cents      INTEGER NOT NULL,              -- this draw before retainage
    retainage_cents  INTEGER NOT NULL,
    backcharge_cents INTEGER NOT NULL DEFAULT 0,
    net_cents        INTEGER NOT NULL,
    lien_waiver      TEXT,                          -- 'conditional', 'unconditional', NULL
    money_action_id  INTEGER REFERENCES money_actions(id),
    status           TEXT NOT NULL,                 -- submitted, blocked, pending_approval
    note             TEXT
);
CREATE TABLE IF NOT EXISTS lab_timesheets (
    id         INTEGER PRIMARY KEY,
    phase_id   INTEGER NOT NULL REFERENCES lab_phases(id),
    member     TEXT NOT NULL,
    day        TEXT NOT NULL,
    hours      REAL NOT NULL,
    rate_cents INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS lab_phase_events_no_update BEFORE UPDATE ON lab_phase_events BEGIN SELECT RAISE(ABORT, 'lab_phase_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS lab_phase_events_no_delete BEFORE DELETE ON lab_phase_events BEGIN SELECT RAISE(ABORT, 'lab_phase_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS lab_inspections_no_update BEFORE UPDATE ON lab_inspections BEGIN SELECT RAISE(ABORT, 'lab_inspections is append-only'); END;
CREATE TRIGGER IF NOT EXISTS lab_inspections_no_delete BEFORE DELETE ON lab_inspections BEGIN SELECT RAISE(ABORT, 'lab_inspections is append-only'); END;
CREATE TRIGGER IF NOT EXISTS lab_draws_no_update BEFORE UPDATE ON lab_draws BEGIN SELECT RAISE(ABORT, 'lab_draws is append-only'); END;
CREATE TRIGGER IF NOT EXISTS lab_draws_no_delete BEFORE DELETE ON lab_draws BEGIN SELECT RAISE(ABORT, 'lab_draws is append-only'); END;

-- ===================================================================
-- Factory: owner-written procedures (SOPs), per-unit check-throughs, station output.
-- ===================================================================
CREATE TABLE IF NOT EXISTS sop_steps (
    id          INTEGER PRIMARY KEY,
    station     TEXT NOT NULL,                     -- cut, bevel, weld, hydrotest, coat
    seq         INTEGER NOT NULL,                  -- order on the line
    version     INTEGER NOT NULL,
    instruction TEXT NOT NULL,                     -- the owner's own words; the AI teaches only from this
    checks      TEXT NOT NULL,                     -- JSON: [{"name","type":"number|yes_no|photo","min","max","must"}]
    approved_by TEXT NOT NULL,
    at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (station, version)
);
CREATE TABLE IF NOT EXISTS units (                   -- one row per pipe
    serial      TEXT PRIMARY KEY,
    spec        TEXT NOT NULL,                     -- e.g. 12in Sch40, rated 285 psi
    started_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS unit_checks (             -- append-only check-through records
    id          INTEGER PRIMARY KEY,
    serial      TEXT NOT NULL REFERENCES units(serial),
    station     TEXT NOT NULL,
    sop_version INTEGER NOT NULL,
    member      TEXT NOT NULL,
    at          TEXT NOT NULL,
    values_json TEXT NOT NULL,
    passed      INTEGER NOT NULL,
    failures    TEXT
);
CREATE TABLE IF NOT EXISTS station_output (          -- worker-hours and finished units per station per day
    day      TEXT NOT NULL,
    station  TEXT NOT NULL,
    member   TEXT NOT NULL,
    hours    REAL NOT NULL,
    units    INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS unit_checks_no_update BEFORE UPDATE ON unit_checks BEGIN SELECT RAISE(ABORT, 'unit_checks is append-only'); END;
CREATE TRIGGER IF NOT EXISTS unit_checks_no_delete BEFORE DELETE ON unit_checks BEGIN SELECT RAISE(ABORT, 'unit_checks is append-only'); END;

-- ===================================================================
-- Pharmacy: prescription order status only. Drug, dose and clinical details stay in the
-- pharmacy's own system; HiWR keeps its reference number and the status history.
-- ===================================================================
CREATE TABLE IF NOT EXISTS rx_orders (
    id           INTEGER PRIMARY KEY,
    contact_id   INTEGER NOT NULL REFERENCES contacts(id),
    rx_ref       TEXT NOT NULL,                    -- the pharmacy system's number, no drug name
    received_at  TEXT NOT NULL,
    promised_at  TEXT NOT NULL,
    controlled   INTEGER NOT NULL DEFAULT 0        -- 1 = handled only in the pharmacy system
);
CREATE TABLE IF NOT EXISTS rx_events (
    id        INTEGER PRIMARY KEY,
    rx_id     INTEGER NOT NULL REFERENCES rx_orders(id),
    stage     TEXT NOT NULL,
    at        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    note      TEXT                                  -- operational only: 'awaiting insurer', 'expected Tue'
);
CREATE TRIGGER IF NOT EXISTS rx_events_no_update BEFORE UPDATE ON rx_events BEGIN SELECT RAISE(ABORT, 'rx_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS rx_events_no_delete BEFORE DELETE ON rx_events BEGIN SELECT RAISE(ABORT, 'rx_events is append-only'); END;

-- ===================================================================
-- Inventory Management > Warehouse (retail floor and back stock) and > Stew (recipes).
-- ===================================================================
CREATE TABLE IF NOT EXISTS shelf_plan (
    sku       TEXT PRIMARY KEY REFERENCES products(sku),
    aisle     TEXT NOT NULL,
    capacity  INTEGER NOT NULL,                    -- facings x depth
    par       INTEGER NOT NULL                     -- restock when the shelf drops below this
);
CREATE TABLE IF NOT EXISTS stock_moves (             -- append-only: back -> floor pulls, receipts, sales
    id      INTEGER PRIMARY KEY,
    sku     TEXT NOT NULL,
    qty     INTEGER NOT NULL,
    from_loc TEXT NOT NULL,
    to_loc   TEXT NOT NULL,
    member  TEXT NOT NULL,
    at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recipes (
    name         TEXT PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('dish', 'sub')),   -- sub = used inside other recipes
    yield_qty    REAL NOT NULL DEFAULT 1,          -- a sub-recipe batch makes this much
    yield_unit   TEXT NOT NULL DEFAULT 'plate',
    price_cents  INTEGER                           -- dishes only
);
CREATE TABLE IF NOT EXISTS recipe_lines (
    recipe     TEXT NOT NULL REFERENCES recipes(name),
    component  TEXT NOT NULL,                      -- a material or another recipe (sub)
    qty        REAL NOT NULL,
    unit       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS material_allergens (
    material  TEXT NOT NULL,
    tag       TEXT NOT NULL,                        -- tomato, nightshade, dairy, gluten, peanut ...
    PRIMARY KEY (material, tag)
);
CREATE TABLE IF NOT EXISTS material_moves (           -- append-only depletion by sales, receipts, waste
    id        INTEGER PRIMARY KEY,
    material  TEXT NOT NULL,
    qty       REAL NOT NULL,                        -- negative = used
    reason    TEXT NOT NULL,
    ref       TEXT,
    at        TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS stock_moves_no_update BEFORE UPDATE ON stock_moves BEGIN SELECT RAISE(ABORT, 'stock_moves is append-only'); END;
CREATE TRIGGER IF NOT EXISTS stock_moves_no_delete BEFORE DELETE ON stock_moves BEGIN SELECT RAISE(ABORT, 'stock_moves is append-only'); END;
CREATE TRIGGER IF NOT EXISTS material_moves_no_update BEFORE UPDATE ON material_moves BEGIN SELECT RAISE(ABORT, 'material_moves is append-only'); END;
CREATE TRIGGER IF NOT EXISTS material_moves_no_delete BEFORE DELETE ON material_moves BEGIN SELECT RAISE(ABORT, 'material_moves is append-only'); END;

-- ===================================================================
-- Prep level: what one station makes ahead, from sold-so-far and expected demand.
-- ===================================================================
CREATE TABLE IF NOT EXISTS prep_items (
    item           TEXT PRIMARY KEY,               -- 'fried tortilla chips'
    station        TEXT NOT NULL,                  -- fryer
    unit           TEXT NOT NULL,
    batch_qty      REAL NOT NULL,                  -- one batch makes this much
    batch_minutes  INTEGER NOT NULL,
    hold_minutes   INTEGER NOT NULL,               -- quality window; older than this is waste
    buffer_pct     REAL NOT NULL DEFAULT 0.10,
    input_material TEXT,                           -- drawn from Stew inventory per batch
    input_qty      REAL
);
CREATE TABLE IF NOT EXISTS prep_usage (              -- how much of a prep item each dish uses
    dish   TEXT NOT NULL,
    item   TEXT NOT NULL REFERENCES prep_items(item),
    qty    REAL NOT NULL,
    PRIMARY KEY (dish, item)
);
CREATE TABLE IF NOT EXISTS dish_sales (              -- POS lines by hour (history and today)
    day    TEXT NOT NULL,
    hour   INTEGER NOT NULL,
    dish   TEXT NOT NULL,
    qty    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS prep_batches (            -- append-only
    id     INTEGER PRIMARY KEY,
    item   TEXT NOT NULL,
    qty    REAL NOT NULL,
    made_at TEXT NOT NULL,
    member TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS prep_batches_no_update BEFORE UPDATE ON prep_batches BEGIN SELECT RAISE(ABORT, 'prep_batches is append-only'); END;

-- ===================================================================
-- Customer Service: the link between the customer and the order lines (UPC, price, qty, specs).
-- ===================================================================
CREATE TABLE IF NOT EXISTS catalog (
    upc          TEXT PRIMARY KEY,
    item         TEXT NOT NULL,                    -- matches a Stew recipe name for dishes
    price_cents  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS order_line_meta (
    order_item_id   INTEGER PRIMARY KEY REFERENCES order_items(id),
    upc             TEXT NOT NULL REFERENCES catalog(upc),
    unit_price_cents INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'ok'      -- ok, awaiting_answer, held_component
);
CREATE TABLE IF NOT EXISTS cs_messages (               -- append-only conversation per order
    id        INTEGER PRIMARY KEY,
    order_id  INTEGER NOT NULL REFERENCES orders(id),
    who       TEXT NOT NULL,                       -- customer, server, hiwr, kitchen
    channel   TEXT NOT NULL,                       -- sms, table, phone, app
    text      TEXT NOT NULL,
    at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_line_changes (        -- append-only; every field change on a line
    id             INTEGER PRIMARY KEY,
    order_item_id  INTEGER REFERENCES order_items(id),
    order_id       INTEGER NOT NULL REFERENCES orders(id),
    field          TEXT NOT NULL,                  -- qty, specs, added, removed, hold
    old            TEXT,
    new            TEXT,
    requested_by   TEXT NOT NULL,
    confirmed_by   TEXT,
    at             TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS cs_messages_no_update BEFORE UPDATE ON cs_messages BEGIN SELECT RAISE(ABORT, 'cs_messages is append-only'); END;
CREATE TRIGGER IF NOT EXISTS cs_messages_no_delete BEFORE DELETE ON cs_messages BEGIN SELECT RAISE(ABORT, 'cs_messages is append-only'); END;
CREATE TRIGGER IF NOT EXISTS order_line_changes_no_update BEFORE UPDATE ON order_line_changes BEGIN SELECT RAISE(ABORT, 'order_line_changes is append-only'); END;
CREATE TRIGGER IF NOT EXISTS order_line_changes_no_delete BEFORE DELETE ON order_line_changes BEGIN SELECT RAISE(ABORT, 'order_line_changes is append-only'); END;

-- ===================================================================
-- Manager ordering: vendor item codes, PO drafts, back stock suggestions.
-- ===================================================================
CREATE TABLE IF NOT EXISTS vendor_items (
    material   TEXT NOT NULL,
    vendor     TEXT NOT NULL,
    upc        TEXT NOT NULL,
    pack_size  REAL NOT NULL,                      -- units of the material per case
    pack_label TEXT NOT NULL,                      -- '25 lb case'
    PRIMARY KEY (material, vendor)
);
CREATE TABLE IF NOT EXISTS material_par (
    material     TEXT PRIMARY KEY,
    safety_days  REAL NOT NULL DEFAULT 1,          -- keep this many days of use on hand
    review_days  INTEGER NOT NULL DEFAULT 7,       -- order covers until the next order
    expires_on   TEXT                              -- nearest expiry of stock on hand
);
CREATE TABLE IF NOT EXISTS po_drafts (
    id          INTEGER PRIMARY KEY,
    vendor      TEXT NOT NULL,
    order_by    TEXT NOT NULL,
    delivery_on TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'draft',      -- draft, sent (by a manager)
    made_by     TEXT NOT NULL,
    sent_by     TEXT
);
CREATE TABLE IF NOT EXISTS po_lines (
    po_id     INTEGER NOT NULL REFERENCES po_drafts(id),
    material  TEXT NOT NULL,
    upc       TEXT NOT NULL,
    packs     INTEGER NOT NULL,
    units     REAL NOT NULL,
    cost_cents INTEGER NOT NULL,
    why       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backstock_choices (        -- append-only manager picks
    id        INTEGER PRIMARY KEY,
    material  TEXT NOT NULL,
    choice    TEXT NOT NULL,
    detail    TEXT,
    chosen_by TEXT NOT NULL,
    at        TEXT NOT NULL
);

-- ===== Accounting view (Finance level): classify expenses, cross with revenue =====
CREATE TABLE IF NOT EXISTS acct_txns (            -- append-only; one row per real money movement
    id           INTEGER PRIMARY KEY,
    txn_date     TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('revenue', 'expense')),
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    description  TEXT NOT NULL,
    counterparty TEXT,
    department   TEXT,                            -- food, beverage, catering, shared
    source       TEXT NOT NULL,                   -- pos, payroll, invoice, bank, money_action
    source_ref   TEXT NOT NULL,
    posted_by    TEXT NOT NULL,
    UNIQUE (source, source_ref)                   -- the same record can't be posted twice
);
CREATE TABLE IF NOT EXISTS acct_class_events (    -- append-only; latest row per txn wins
    id         INTEGER PRIMARY KEY,
    txn_id     INTEGER NOT NULL REFERENCES acct_txns(id),
    class      TEXT NOT NULL,
    department TEXT,
    status     TEXT NOT NULL CHECK (status IN ('proposed', 'needs_person', 'confirmed', 'excluded')),
    by         TEXT NOT NULL,
    reason     TEXT,
    at         TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS acct_rules (           -- learned when a person corrects a proposal
    id           INTEGER PRIMARY KEY,
    counterparty TEXT NOT NULL,
    class        TEXT NOT NULL,
    department   TEXT,
    created_by   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS acct_memos (           -- shown beside the books, never added to them (e.g. waste at cost)
    id INTEGER PRIMARY KEY, memo_date TEXT NOT NULL, kind TEXT NOT NULL, amount_cents INTEGER NOT NULL, note TEXT
);
CREATE TABLE IF NOT EXISTS acct_targets (         -- owner-set; latest per metric wins
    id INTEGER PRIMARY KEY, metric TEXT NOT NULL, low REAL, high REAL, set_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS qb_pl_lines (          -- QuickBooks P&L imported per period, mapped to HiWR classes
    id INTEGER PRIMARY KEY, period TEXT NOT NULL, qb_account TEXT NOT NULL, class TEXT NOT NULL,
    amount_cents INTEGER NOT NULL, imported_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS acct_flags (
    id INTEGER PRIMARY KEY, period TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TRIGGER IF NOT EXISTS acct_txns_no_update BEFORE UPDATE ON acct_txns
BEGIN SELECT RAISE(ABORT, 'acct_txns is append-only: post a correcting entry'); END;
CREATE TRIGGER IF NOT EXISTS acct_txns_no_delete BEFORE DELETE ON acct_txns
BEGIN SELECT RAISE(ABORT, 'acct_txns is append-only: post a correcting entry'); END;
CREATE TRIGGER IF NOT EXISTS acct_class_no_update BEFORE UPDATE ON acct_class_events
BEGIN SELECT RAISE(ABORT, 'acct_class_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS acct_class_no_delete BEFORE DELETE ON acct_class_events
BEGIN SELECT RAISE(ABORT, 'acct_class_events is append-only'); END;
CREATE TABLE IF NOT EXISTS acct_inventory_counts (  -- value of stock on hand at close of day, from Stew/Warehouse counts
    id INTEGER PRIMARY KEY, count_date TEXT NOT NULL, department TEXT NOT NULL, value_cents INTEGER NOT NULL,
    counted_by TEXT NOT NULL
);
