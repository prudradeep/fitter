ALTER TABLE app_users
ADD COLUMN sync_encrypted_payload TEXT NULL AFTER role;
