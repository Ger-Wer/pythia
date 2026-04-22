-- Phase 1B — LISTEN/NOTIFY publisher for the Dual-Loop shadow store.
--
-- Installs AFTER INSERT / UPDATE / DELETE triggers on the tables the
-- gateway-api needs to push to operators in near-real-time. The trigger
-- function emits a compact JSON envelope via `pg_notify` (payload size
-- stays well under Postgres' 8000 byte limit — subscribers re-read the
-- row via SQL when they need the body).
--
-- Activation is session-scoped via the custom GUC `dual_loop.pg_notify`:
--
--     SET dual_loop.pg_notify = 'on';      -- enable for this session
--     ALTER DATABASE pythia_dev SET dual_loop.pg_notify = 'on';  -- durable
--
-- When the setting is missing or anything other than `on`, the trigger
-- short-circuits and does no work. Default is OFF — this migration is
-- safe to apply on top of the existing Phase 1 Track D shadow schema
-- without any behavioural change.
--
-- Envelope (all fields are stable; additions will bump `v`):
--   {
--     "v": 1,
--     "op": "insert" | "update" | "delete",
--     "table": "predictions",
--     "pk": { "id": 42 },            -- primary key(s)
--     "market_id": "polymarket:0x…", -- present when the row has one
--     "ts": "2026-04-22T12:34:56.789Z"
--   }
--
-- Channels (one per table) — gateway-api LISTENs on each:
--   dual_loop.predictions
--   dual_loop.signals
--   dual_loop.decisions
--   dual_loop.attention_items
--   dual_loop.watch_dossiers
--   dual_loop.watch_updates
--
-- Reference: docs/dual_loop/pg_listen_notify.md.

-- ---------------------------------------------------------------------------
-- Generic NOTIFY helper. Accepts a channel suffix + a jsonb body and
-- prepends the `dual_loop.` prefix + the common envelope fields.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dual_loop_notify(
  channel_suffix text,
  op text,
  tbl text,
  pk jsonb,
  market_id text
) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  enabled text := coalesce(current_setting('dual_loop.pg_notify', true), 'off');
  payload jsonb;
BEGIN
  IF enabled <> 'on' THEN
    RETURN;
  END IF;

  payload := jsonb_build_object(
    'v', 1,
    'op', op,
    'table', tbl,
    'pk', pk,
    'ts', to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
  );

  IF market_id IS NOT NULL THEN
    payload := payload || jsonb_build_object('market_id', market_id);
  END IF;

  PERFORM pg_notify('dual_loop.' || channel_suffix, payload::text);
END;
$$;

-- ---------------------------------------------------------------------------
-- predictions
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dual_loop_predictions_notify() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  row_record record;
BEGIN
  row_record := COALESCE(NEW, OLD);
  PERFORM dual_loop_notify(
    'predictions',
    lower(TG_OP),
    'predictions',
    jsonb_build_object('id', row_record.id),
    row_record.market_id
  );
  RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS dual_loop_predictions_notify ON predictions;
CREATE TRIGGER dual_loop_predictions_notify
AFTER INSERT OR UPDATE OR DELETE ON predictions
FOR EACH ROW EXECUTE FUNCTION dual_loop_predictions_notify();

-- ---------------------------------------------------------------------------
-- signals
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dual_loop_signals_notify() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  row_record record;
BEGIN
  row_record := COALESCE(NEW, OLD);
  PERFORM dual_loop_notify(
    'signals',
    lower(TG_OP),
    'signals',
    jsonb_build_object('id', row_record.id),
    row_record.market_id
  );
  RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS dual_loop_signals_notify ON signals;
CREATE TRIGGER dual_loop_signals_notify
AFTER INSERT OR UPDATE OR DELETE ON signals
FOR EACH ROW EXECUTE FUNCTION dual_loop_signals_notify();

-- ---------------------------------------------------------------------------
-- decisions
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dual_loop_decisions_notify() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  row_record record;
BEGIN
  row_record := COALESCE(NEW, OLD);
  PERFORM dual_loop_notify(
    'decisions',
    lower(TG_OP),
    'decisions',
    jsonb_build_object('id', row_record.id),
    row_record.market_id
  );
  RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS dual_loop_decisions_notify ON decisions;
CREATE TRIGGER dual_loop_decisions_notify
AFTER INSERT OR UPDATE OR DELETE ON decisions
FOR EACH ROW EXECUTE FUNCTION dual_loop_decisions_notify();

-- ---------------------------------------------------------------------------
-- attention_items
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dual_loop_attention_items_notify() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  row_record record;
BEGIN
  row_record := COALESCE(NEW, OLD);
  PERFORM dual_loop_notify(
    'attention_items',
    lower(TG_OP),
    'attention_items',
    jsonb_build_object('id', row_record.id),
    row_record.market_id
  );
  RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS dual_loop_attention_items_notify ON attention_items;
CREATE TRIGGER dual_loop_attention_items_notify
AFTER INSERT OR UPDATE OR DELETE ON attention_items
FOR EACH ROW EXECUTE FUNCTION dual_loop_attention_items_notify();

-- ---------------------------------------------------------------------------
-- watch_dossiers
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dual_loop_watch_dossiers_notify() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  row_record record;
BEGIN
  row_record := COALESCE(NEW, OLD);
  PERFORM dual_loop_notify(
    'watch_dossiers',
    lower(TG_OP),
    'watch_dossiers',
    jsonb_build_object('id', row_record.id),
    row_record.market_id
  );
  RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS dual_loop_watch_dossiers_notify ON watch_dossiers;
CREATE TRIGGER dual_loop_watch_dossiers_notify
AFTER INSERT OR UPDATE OR DELETE ON watch_dossiers
FOR EACH ROW EXECUTE FUNCTION dual_loop_watch_dossiers_notify();

-- ---------------------------------------------------------------------------
-- watch_updates — no market_id column; dossier id travels in pk.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dual_loop_watch_updates_notify() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  row_record record;
BEGIN
  row_record := COALESCE(NEW, OLD);
  PERFORM dual_loop_notify(
    'watch_updates',
    lower(TG_OP),
    'watch_updates',
    jsonb_build_object(
      'id', row_record.id,
      'watch_dossier_id', row_record.watch_dossier_id
    ),
    NULL
  );
  RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS dual_loop_watch_updates_notify ON watch_updates;
CREATE TRIGGER dual_loop_watch_updates_notify
AFTER INSERT OR UPDATE OR DELETE ON watch_updates
FOR EACH ROW EXECUTE FUNCTION dual_loop_watch_updates_notify();
