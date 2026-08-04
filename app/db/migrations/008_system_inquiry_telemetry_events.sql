CREATE TABLE IF NOT EXISTS system_inquiry_telemetry_events (
  id CHAR(36) PRIMARY KEY,
  event_key VARCHAR(64) NOT NULL UNIQUE,
  payload_json TEXT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'queued',
  attempts INT NOT NULL DEFAULT 0,
  last_error TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  synced_at DATETIME NULL,
  INDEX ix_system_inquiry_telemetry_event_key (event_key),
  INDEX ix_system_inquiry_telemetry_status (status)
);
