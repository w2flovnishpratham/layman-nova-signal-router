-- Wipes ALL strategies (personal + published), every version, conversion
-- request, admin review, instance, position, and signal history -- keeps
-- users, entitlements, payments, everything else untouched.
--
-- Uses DELETE, not TRUNCATE: TRUNCATE refuses to run on any table that has
-- an *existing* foreign-key constraint from another table, even if that
-- table's rows are empty or already NULLed -- it's a structural check, not
-- a data check. DELETE performs real per-row constraint checks, so as long
-- as the external durable tables (user_runs, user_engine_configs,
-- strategy_execution_jobs, portfolio_trades -- which hold real run/engine
-- selection/trade P&L history unrelated to which strategy they last
-- pointed at, and must NOT be wiped) have their FK columns nulled first,
-- and the 21 strategy-related tables below are deleted in dependency order
-- (children before parents, verified against information_schema.table_constraints
-- directly against production, not assumed), DELETE succeeds without CASCADE.

BEGIN;

UPDATE user_runs SET strategy_version_id = NULL WHERE strategy_version_id IS NOT NULL;
UPDATE user_runs SET configuration_revision_id = NULL, configuration_revision = NULL
  WHERE configuration_revision_id IS NOT NULL;
UPDATE user_engine_configs SET selected_strategy_instance_id = NULL, selected_configuration_revision_id = NULL
  WHERE selected_strategy_instance_id IS NOT NULL OR selected_configuration_revision_id IS NOT NULL;
UPDATE strategy_execution_jobs SET configuration_revision_id = NULL, configuration_revision = NULL
  WHERE configuration_revision_id IS NOT NULL;
UPDATE portfolio_trades SET configuration_revision_id = NULL WHERE configuration_revision_id IS NOT NULL;

-- Tier 1: leaves -- nothing else references these, safe to delete first.
DELETE FROM pine_semantic_analyses;
DELETE FROM position_events;
DELETE FROM canonical_signal_outcomes;
DELETE FROM strategy_signal_rejections;
DELETE FROM strategy_instance_webhook_credentials;
DELETE FROM tradingview_setups;
DELETE FROM engine_start_operations;
DELETE FROM tradingview_compile_evidence;
DELETE FROM pine_user_acceptances;
DELETE FROM pine_prompt_qualification_trials;
DELETE FROM strategy_validation_reports;
DELETE FROM strategy_admin_reviews;
DELETE FROM strategy_subscriptions;

-- Tier 2: only depended on by tier-1 tables, which are now gone.
DELETE FROM strategy_instance_positions;
DELETE FROM canonical_signal_decisions;
DELETE FROM strategy_source_artifacts;
DELETE FROM strategy_configuration_revisions;

-- Tier 3: depends on tier 1 + 2 being gone.
DELETE FROM strategy_instances;
DELETE FROM pine_conversion_requests;

-- Tier 4: depends on tier 3 being gone.
DELETE FROM strategy_versions;

-- Tier 5: the root -- everything above pointed at this, directly or transitively.
DELETE FROM strategy_catalog;

COMMIT;

-- Verify afterwards:
--   SELECT count(*) FROM strategy_catalog;   -- 0
--   SELECT count(*) FROM strategy_instances; -- 0
--   SELECT count(*) FROM users;              -- unchanged
