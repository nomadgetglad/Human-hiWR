-- Tables the agent runner adds to the hiWR database. All append-only.

-- Last 4 of the card used on an order, so a client can verify a sale. Never the full number.
CREATE TABLE IF NOT EXISTS order_payments (
    id          INTEGER PRIMARY KEY,
    order_id    INTEGER NOT NULL,
    card_last4  TEXT NOT NULL CHECK (length(card_last4) = 4 AND card_last4 NOT GLOB '*[^0-9]*'),
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Live-person requests. Queued only: a person places the call or sends the text.
CREATE TABLE IF NOT EXISTS agent_handoffs (
    id            INTEGER PRIMARY KEY,
    at            TEXT NOT NULL DEFAULT (datetime('now')),
    requested_for TEXT NOT NULL,                 -- who asked (member id, or 'client')
    channel       TEXT NOT NULL CHECK (channel IN ('call', 'callback', 'text')),
    route_to      TEXT NOT NULL,                 -- on_duty_manager, owner, approvers
    reason        TEXT,
    contact       TEXT,                          -- number they gave, if any
    best_time     TEXT,
    ticket_id     TEXT,
    status        TEXT NOT NULL DEFAULT 'queued - a person must place it'
);

-- Approval items that aren't one of the money_actions scopes: purchase orders, vendor chargebacks, rewards,
-- pay changes, hires, permissions, schedule overrides, write-ups, terminations, end-of-day sign-off.
-- Recorded for the approvers; the agent has no way to approve or carry them out.
CREATE TABLE IF NOT EXISTS agent_proposals (
    id            INTEGER PRIMARY KEY,
    at            TEXT NOT NULL DEFAULT (datetime('now')),
    kind          TEXT NOT NULL,
    description   TEXT NOT NULL,
    amount_cents  INTEGER,
    currency      TEXT,
    ticket_id     TEXT,
    requested_by  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'PENDING - requires double approval'
);

-- One row per turn: who asked, where it ran, which tools ran. Never the content.
CREATE TABLE IF NOT EXISTS agent_log (
    id          INTEGER PRIMARY KEY,
    at          TEXT NOT NULL DEFAULT (datetime('now')),
    requester   TEXT NOT NULL,
    level       INTEGER NOT NULL,
    backend     TEXT NOT NULL,                   -- local or cloud
    model       TEXT NOT NULL,
    route_why   TEXT,
    tools       TEXT,                            -- JSON list of tool names and whether they were allowed
    hidden      INTEGER NOT NULL DEFAULT 0       -- how many names/numbers were swapped before sending to the cloud
);

CREATE TRIGGER IF NOT EXISTS order_payments_no_update BEFORE UPDATE ON order_payments BEGIN SELECT RAISE(ABORT, 'order_payments is append-only'); END;
CREATE TRIGGER IF NOT EXISTS order_payments_no_delete BEFORE DELETE ON order_payments BEGIN SELECT RAISE(ABORT, 'order_payments is append-only'); END;
CREATE TRIGGER IF NOT EXISTS agent_handoffs_no_update BEFORE UPDATE ON agent_handoffs BEGIN SELECT RAISE(ABORT, 'agent_handoffs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS agent_handoffs_no_delete BEFORE DELETE ON agent_handoffs BEGIN SELECT RAISE(ABORT, 'agent_handoffs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS agent_proposals_no_update BEFORE UPDATE ON agent_proposals BEGIN SELECT RAISE(ABORT, 'agent_proposals is append-only'); END;
CREATE TRIGGER IF NOT EXISTS agent_proposals_no_delete BEFORE DELETE ON agent_proposals BEGIN SELECT RAISE(ABORT, 'agent_proposals is append-only'); END;
CREATE TRIGGER IF NOT EXISTS agent_log_no_update BEFORE UPDATE ON agent_log BEGIN SELECT RAISE(ABORT, 'agent_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS agent_log_no_delete BEFORE DELETE ON agent_log BEGIN SELECT RAISE(ABORT, 'agent_log is append-only'); END;
