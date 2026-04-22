CREATE TABLE "attention_item_events" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"attention_item_id" integer NOT NULL,
	"action" text NOT NULL,
	"from_status" text,
	"to_status" text,
	"created_at" text NOT NULL,
	"note" text DEFAULT '' NOT NULL
);
--> statement-breakpoint
CREATE TABLE "attention_items" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"market_id" text NOT NULL,
	"source_type" text NOT NULL,
	"source_id" integer,
	"category" text NOT NULL,
	"priority" text NOT NULL,
	"status" text DEFAULT 'new' NOT NULL,
	"title" text NOT NULL,
	"detail" text NOT NULL,
	"action_label" text DEFAULT '' NOT NULL,
	"created_at" text NOT NULL,
	"updated_at" text NOT NULL,
	"last_seen_at" text,
	"last_telegram_sent_at" text
);
--> statement-breakpoint
CREATE TABLE "books" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"name" text NOT NULL,
	"mode" text NOT NULL,
	"currency" text DEFAULT 'USDC' NOT NULL,
	"starting_balance" double precision NOT NULL,
	"current_balance" double precision NOT NULL,
	"max_risk_per_trade_pct" double precision NOT NULL,
	"max_total_exposure_pct" double precision NOT NULL,
	"status" text DEFAULT 'active' NOT NULL,
	"created_at" text NOT NULL,
	"updated_at" text NOT NULL,
	CONSTRAINT "books_name_unique" UNIQUE("name")
);
--> statement-breakpoint
CREATE TABLE "decisions" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"signal_id" integer NOT NULL,
	"book_id" integer,
	"market_id" text NOT NULL,
	"actor_type" text NOT NULL,
	"decision_type" text NOT NULL,
	"decision_reason" text NOT NULL,
	"operator_confidence" text,
	"confirmation_criteria" text,
	"invalidation_criteria" text,
	"review_due_at" text,
	"status" text DEFAULT 'active' NOT NULL,
	"created_at" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE "position_events" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"position_id" integer NOT NULL,
	"decision_id" integer,
	"event_type" text NOT NULL,
	"event_note" text,
	"market_price" double precision,
	"research_probability" double precision,
	"created_at" text NOT NULL,
	"payload_json" text
);
--> statement-breakpoint
CREATE TABLE "positions" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"book_id" integer NOT NULL,
	"market_id" text NOT NULL,
	"signal_id" integer NOT NULL,
	"opened_by_decision_id" integer NOT NULL,
	"mode" text NOT NULL,
	"side" text NOT NULL,
	"status" text DEFAULT 'open' NOT NULL,
	"entry_yes_price" double precision NOT NULL,
	"entry_cost_per_contract" double precision NOT NULL,
	"entry_zone_min" double precision,
	"entry_zone_max" double precision,
	"size_contracts" double precision NOT NULL,
	"notional_usd" double precision NOT NULL,
	"review_due_at" text,
	"opened_at" text NOT NULL,
	"closed_at" text,
	"closed_yes_price" double precision,
	"close_reason" text,
	"thesis_cycle_number" integer DEFAULT 1 NOT NULL,
	"pnl_usd" double precision,
	"pnl_percent" double precision
);
--> statement-breakpoint
CREATE TABLE "postmortems" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"position_id" integer NOT NULL,
	"market_id" text NOT NULL,
	"outcome_label" text,
	"pnl_usd" double precision,
	"pnl_percent" double precision,
	"forecast_error" integer,
	"entry_error" integer,
	"timing_error" integer,
	"management_error" integer,
	"discipline_error" integer,
	"operator_note" text,
	"system_note" text,
	"created_at" text NOT NULL,
	"updated_at" text NOT NULL,
	CONSTRAINT "postmortems_position_id_unique" UNIQUE("position_id")
);
--> statement-breakpoint
CREATE TABLE "predictions" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"market_id" text NOT NULL,
	"question" text NOT NULL,
	"category" text,
	"volume" double precision,
	"created_at" text,
	"end_date" text,
	"market_price" double precision NOT NULL,
	"dexter_p" double precision,
	"dexter_confidence" text,
	"dexter_reasoning" text,
	"dexter_key_risk" text,
	"dexter_base_rate" text,
	"naked_p" double precision,
	"naked_reasoning" text,
	"research_brief_json" text,
	"forecast_timestamp" text NOT NULL,
	"is_valid" integer DEFAULT 1 NOT NULL,
	"outcome" integer,
	"resolved_at" text,
	"brier_dexter" double precision,
	"brier_naked" double precision,
	"brier_market" double precision,
	"api_cost_usd" double precision DEFAULT 0,
	"_shadow_pg_written_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "reviews" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"position_id" integer NOT NULL,
	"market_id" text NOT NULL,
	"review_type" text NOT NULL,
	"due_at" text NOT NULL,
	"status" text DEFAULT 'due' NOT NULL,
	"review_note" text,
	"resolved_at" text,
	"created_at" text NOT NULL,
	"updated_at" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE "signals" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"prediction_id" integer NOT NULL,
	"market_id" text NOT NULL,
	"question" text NOT NULL,
	"forecast_timestamp" text NOT NULL,
	"market_price" double precision,
	"research_probability" double precision,
	"naked_probability" double precision,
	"best_probability" double precision,
	"signal_edge" double precision,
	"signal_strength" double precision NOT NULL,
	"recommended_action" text NOT NULL,
	"entry_zone_min" double precision,
	"entry_zone_max" double precision,
	"thesis_summary" text,
	"primary_driver" text,
	"disconfirming_signal" text,
	"confidence" text,
	"research_status" text NOT NULL,
	"research_status_detail" text,
	"status" text DEFAULT 'new' NOT NULL,
	"created_at" text NOT NULL,
	"updated_at" text NOT NULL,
	CONSTRAINT "signals_prediction_id_unique" UNIQUE("prediction_id")
);
--> statement-breakpoint
CREATE TABLE "watch_dossiers" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"market_id" text NOT NULL,
	"status" text DEFAULT 'active' NOT NULL,
	"created_from_prediction_id" integer,
	"created_from_signal_id" integer,
	"created_at" text NOT NULL,
	"updated_at" text NOT NULL,
	"first_watched_at" text NOT NULL,
	"last_refresh_at" text,
	"last_deep_review_at" text,
	"next_review_due_at" text,
	"freshness_policy" text NOT NULL,
	"initial_snapshot_state" text DEFAULT 'reused' NOT NULL,
	"thesis_state" text DEFAULT 'review_required' NOT NULL,
	"current_market_price" double precision,
	"current_research_probability" double precision,
	"current_naked_probability" double precision,
	"current_confidence" text,
	"current_research_status" text,
	"current_thesis_summary" text,
	"current_primary_driver" text,
	"current_invalidation_signal" text,
	"last_status_label" text,
	"last_reason_summary" text,
	"last_action_summary" text,
	"operator_note" text DEFAULT '' NOT NULL,
	CONSTRAINT "watch_dossiers_market_id_unique" UNIQUE("market_id")
);
--> statement-breakpoint
CREATE TABLE "watch_refresh_runs" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"started_at" text NOT NULL,
	"finished_at" text,
	"status" text NOT NULL,
	"markets_total" integer DEFAULT 0 NOT NULL,
	"markets_checked" integer DEFAULT 0 NOT NULL,
	"markets_changed" integer DEFAULT 0 NOT NULL,
	"deep_reviews_triggered" integer DEFAULT 0 NOT NULL,
	"alerts_created" integer DEFAULT 0 NOT NULL,
	"error_summary" text DEFAULT '' NOT NULL
);
--> statement-breakpoint
CREATE TABLE "watch_refresh_snapshots" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"watch_dossier_id" integer NOT NULL,
	"refresh_run_id" integer NOT NULL,
	"captured_at" text NOT NULL,
	"market_price" double precision,
	"research_probability" double precision,
	"naked_probability" double precision,
	"edge_vs_market" double precision,
	"price_delta_since_last" double precision,
	"thesis_state" text NOT NULL,
	"research_status" text,
	"monitoring_status" text NOT NULL,
	"trigger_flags_json" text DEFAULT '{}' NOT NULL,
	"news_digest_json" text DEFAULT '{}' NOT NULL,
	"monitoring_output_json" text DEFAULT '{}' NOT NULL
);
--> statement-breakpoint
CREATE TABLE "watch_updates" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"watch_dossier_id" integer NOT NULL,
	"refresh_run_id" integer NOT NULL,
	"created_at" text NOT NULL,
	"severity" text NOT NULL,
	"status_label" text NOT NULL,
	"reason_summary" text NOT NULL,
	"action_summary" text NOT NULL,
	"market_price_before" double precision,
	"market_price_after" double precision,
	"research_probability_before" double precision,
	"research_probability_after" double precision,
	"change_summary_json" text DEFAULT '{}' NOT NULL,
	"delivered_to_dashboard_at" text,
	"delivered_to_telegram_at" text
);
--> statement-breakpoint
CREATE TABLE "watchlist" (
	"market_id" text PRIMARY KEY NOT NULL,
	"note" text DEFAULT '' NOT NULL,
	"created_at" text NOT NULL,
	"updated_at" text NOT NULL
);
--> statement-breakpoint
ALTER TABLE "attention_item_events" ADD CONSTRAINT "attention_item_events_item_fk" FOREIGN KEY ("attention_item_id") REFERENCES "public"."attention_items"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "decisions" ADD CONSTRAINT "decisions_signal_id_fk" FOREIGN KEY ("signal_id") REFERENCES "public"."signals"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "decisions" ADD CONSTRAINT "decisions_book_id_fk" FOREIGN KEY ("book_id") REFERENCES "public"."books"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "position_events" ADD CONSTRAINT "position_events_position_id_fk" FOREIGN KEY ("position_id") REFERENCES "public"."positions"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "position_events" ADD CONSTRAINT "position_events_decision_id_fk" FOREIGN KEY ("decision_id") REFERENCES "public"."decisions"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "positions" ADD CONSTRAINT "positions_book_id_fk" FOREIGN KEY ("book_id") REFERENCES "public"."books"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "positions" ADD CONSTRAINT "positions_signal_id_fk" FOREIGN KEY ("signal_id") REFERENCES "public"."signals"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "positions" ADD CONSTRAINT "positions_opened_by_decision_id_fk" FOREIGN KEY ("opened_by_decision_id") REFERENCES "public"."decisions"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "postmortems" ADD CONSTRAINT "postmortems_position_id_fk" FOREIGN KEY ("position_id") REFERENCES "public"."positions"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "reviews" ADD CONSTRAINT "reviews_position_id_fk" FOREIGN KEY ("position_id") REFERENCES "public"."positions"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "watch_dossiers" ADD CONSTRAINT "watch_dossiers_prediction_fk" FOREIGN KEY ("created_from_prediction_id") REFERENCES "public"."predictions"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "watch_dossiers" ADD CONSTRAINT "watch_dossiers_signal_fk" FOREIGN KEY ("created_from_signal_id") REFERENCES "public"."signals"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "watch_refresh_snapshots" ADD CONSTRAINT "watch_refresh_snapshots_dossier_fk" FOREIGN KEY ("watch_dossier_id") REFERENCES "public"."watch_dossiers"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "watch_refresh_snapshots" ADD CONSTRAINT "watch_refresh_snapshots_run_fk" FOREIGN KEY ("refresh_run_id") REFERENCES "public"."watch_refresh_runs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "watch_updates" ADD CONSTRAINT "watch_updates_dossier_fk" FOREIGN KEY ("watch_dossier_id") REFERENCES "public"."watch_dossiers"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "watch_updates" ADD CONSTRAINT "watch_updates_run_fk" FOREIGN KEY ("refresh_run_id") REFERENCES "public"."watch_refresh_runs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "idx_attention_item_events_item" ON "attention_item_events" USING btree ("attention_item_id","created_at");--> statement-breakpoint
CREATE INDEX "idx_attention_items_market_category_status" ON "attention_items" USING btree ("market_id","category","status","updated_at");--> statement-breakpoint
CREATE INDEX "idx_attention_items_priority_status" ON "attention_items" USING btree ("priority","status","updated_at");--> statement-breakpoint
CREATE INDEX "idx_attention_items_telegram_delivery" ON "attention_items" USING btree ("last_telegram_sent_at","priority","updated_at");--> statement-breakpoint
CREATE INDEX "idx_decisions_market_created" ON "decisions" USING btree ("market_id","created_at");--> statement-breakpoint
CREATE INDEX "idx_decisions_signal_id" ON "decisions" USING btree ("signal_id");--> statement-breakpoint
CREATE INDEX "idx_position_events_position_created" ON "position_events" USING btree ("position_id","created_at");--> statement-breakpoint
CREATE INDEX "idx_positions_market_status" ON "positions" USING btree ("market_id","status");--> statement-breakpoint
CREATE INDEX "idx_positions_book_status" ON "positions" USING btree ("book_id","status");--> statement-breakpoint
CREATE INDEX "idx_postmortems_market_created" ON "postmortems" USING btree ("market_id","created_at");--> statement-breakpoint
CREATE UNIQUE INDEX "predictions_market_forecast_uniq" ON "predictions" USING btree ("market_id","forecast_timestamp");--> statement-breakpoint
CREATE INDEX "idx_outcome" ON "predictions" USING btree ("outcome");--> statement-breakpoint
CREATE INDEX "idx_end_date" ON "predictions" USING btree ("end_date");--> statement-breakpoint
CREATE INDEX "idx_forecast_ts" ON "predictions" USING btree ("forecast_timestamp");--> statement-breakpoint
CREATE INDEX "idx_predictions_valid" ON "predictions" USING btree ("is_valid");--> statement-breakpoint
CREATE INDEX "idx_reviews_status_due_at" ON "reviews" USING btree ("status","due_at");--> statement-breakpoint
CREATE INDEX "idx_reviews_market_id" ON "reviews" USING btree ("market_id");--> statement-breakpoint
CREATE INDEX "idx_signals_market_id" ON "signals" USING btree ("market_id");--> statement-breakpoint
CREATE INDEX "idx_signals_status" ON "signals" USING btree ("status");--> statement-breakpoint
CREATE INDEX "idx_signals_forecast_ts" ON "signals" USING btree ("forecast_timestamp");--> statement-breakpoint
CREATE INDEX "idx_watch_dossiers_market_id" ON "watch_dossiers" USING btree ("market_id");--> statement-breakpoint
CREATE INDEX "idx_watch_dossiers_status_updated_at" ON "watch_dossiers" USING btree ("status","updated_at");--> statement-breakpoint
CREATE INDEX "idx_watch_refresh_runs_status_started_at" ON "watch_refresh_runs" USING btree ("status","started_at");--> statement-breakpoint
CREATE INDEX "idx_watch_refresh_snapshots_dossier_captured_at" ON "watch_refresh_snapshots" USING btree ("watch_dossier_id","captured_at");--> statement-breakpoint
CREATE INDEX "idx_watch_updates_dossier_created_at" ON "watch_updates" USING btree ("watch_dossier_id","created_at");--> statement-breakpoint
CREATE INDEX "idx_watch_updates_delivery" ON "watch_updates" USING btree ("delivered_to_telegram_at","created_at");