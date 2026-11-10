-- =============================================================================
-- Day 8: Observability Metrics & User Clearances
-- Database migration and seed data for MCP servers
-- =============================================================================

-- 1. Table: server_metrics
CREATE TABLE IF NOT EXISTS server_metrics (
    id SERIAL PRIMARY KEY,
    service_id TEXT NOT NULL,
    cpu_percent FLOAT NOT NULL,
    memory_percent FLOAT NOT NULL,
    error_rate_percent FLOAT NOT NULL,
    p95_latency_ms FLOAT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('healthy', 'degraded', 'offline', 'maintenance')),
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_server_metrics_service_time 
ON server_metrics(service_id, recorded_at DESC);

-- 2. Table: user_clearances
CREATE TABLE IF NOT EXISTS user_clearances (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    granted BOOLEAN NOT NULL DEFAULT TRUE,
    approved_by TEXT,
    granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_user_clearances_user_action 
ON user_clearances(user_id, action_type);

-- -----------------------------------------------------------------------------
-- Seed Data: server_metrics (telemetry samples across key services)
-- -----------------------------------------------------------------------------
-- auth-api (healthy, normal load)
INSERT INTO server_metrics (service_id, cpu_percent, memory_percent, error_rate_percent, p95_latency_ms, status, recorded_at)
VALUES 
  ('auth-api', 32.5, 54.0, 0.05, 120.0, 'healthy', NOW() - INTERVAL '30 minutes'),
  ('auth-api', 34.0, 55.2, 0.08, 125.0, 'healthy', NOW() - INTERVAL '20 minutes'),
  ('auth-api', 31.8, 54.8, 0.04, 118.0, 'healthy', NOW() - INTERVAL '10 minutes'),
  ('auth-api', 33.1, 55.0, 0.06, 122.0, 'healthy', NOW());

-- redis-cluster (OOM spike anomaly: high memory, degraded status)
INSERT INTO server_metrics (service_id, cpu_percent, memory_percent, error_rate_percent, p95_latency_ms, status, recorded_at)
VALUES 
  ('redis-cluster', 45.0, 82.0, 0.10, 85.0, 'healthy', NOW() - INTERVAL '40 minutes'),
  ('redis-cluster', 68.0, 91.5, 1.20, 310.0, 'degraded', NOW() - INTERVAL '25 minutes'),
  ('redis-cluster', 88.5, 96.8, 8.40, 890.0, 'degraded', NOW() - INTERVAL '10 minutes'),
  ('redis-cluster', 94.2, 98.4, 14.50, 1420.0, 'degraded', NOW());

-- postgres-primary (connection pool saturation anomaly)
INSERT INTO server_metrics (service_id, cpu_percent, memory_percent, error_rate_percent, p95_latency_ms, status, recorded_at)
VALUES 
  ('postgres-primary', 52.0, 68.0, 0.02, 45.0, 'healthy', NOW() - INTERVAL '30 minutes'),
  ('postgres-primary', 78.4, 82.0, 2.10, 320.0, 'degraded', NOW() - INTERVAL '15 minutes'),
  ('postgres-primary', 89.1, 88.5, 6.70, 780.0, 'degraded', NOW());

-- checkout-api (latency spike due to downstream redis/db)
INSERT INTO server_metrics (service_id, cpu_percent, memory_percent, error_rate_percent, p95_latency_ms, status, recorded_at)
VALUES 
  ('checkout-api', 41.0, 60.5, 0.12, 180.0, 'healthy', NOW() - INTERVAL '30 minutes'),
  ('checkout-api', 65.0, 74.0, 3.40, 650.0, 'degraded', NOW() - INTERVAL '15 minutes'),
  ('checkout-api', 72.3, 76.8, 5.80, 980.0, 'degraded', NOW());

-- vpn-service (healthy corporate gateway)
INSERT INTO server_metrics (service_id, cpu_percent, memory_percent, error_rate_percent, p95_latency_ms, status, recorded_at)
VALUES 
  ('vpn-service', 22.0, 44.0, 0.01, 35.0, 'healthy', NOW() - INTERVAL '30 minutes'),
  ('vpn-service', 24.5, 45.1, 0.02, 38.0, 'healthy', NOW());

-- -----------------------------------------------------------------------------
-- Seed Data: user_clearances (sample role clearance records)
-- -----------------------------------------------------------------------------
-- EMP-0012 (Platform Engineer, DevOps) -> Full clearance
INSERT INTO user_clearances (user_id, action_type, granted, approved_by) VALUES
  ('EMP-0012', 'restart_service', TRUE, 'EMP-0044'),
  ('EMP-0012', 'clear_cache', TRUE, 'EMP-0044'),
  ('EMP-0012', 'initiate_hardware_replacement', TRUE, 'EMP-0044');

-- EMP-0044 (DevOps Lead) -> Full clearance
INSERT INTO user_clearances (user_id, action_type, granted, approved_by) VALUES
  ('EMP-0044', 'restart_service', TRUE, 'SYSTEM'),
  ('EMP-0044', 'clear_cache', TRUE, 'SYSTEM'),
  ('EMP-0044', 'initiate_hardware_replacement', TRUE, 'SYSTEM');

-- EMP-0005 (Tech Lead, Engineering) -> Cache clear only
INSERT INTO user_clearances (user_id, action_type, granted, approved_by) VALUES
  ('EMP-0005', 'clear_cache', TRUE, 'EMP-0044'),
  ('EMP-0005', 'restart_service', FALSE, 'EMP-0044');

-- EMP-0001 (Accountant, Finance) -> No operational clearances
INSERT INTO user_clearances (user_id, action_type, granted, approved_by) VALUES
  ('EMP-0001', 'restart_service', FALSE, 'SYSTEM'),
  ('EMP-0001', 'clear_cache', FALSE, 'SYSTEM');

