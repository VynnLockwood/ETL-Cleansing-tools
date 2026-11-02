-- Runs as spp_owner. Adds the transient 'applying' status (apply concurrency guard)
-- and a stage on issues so apply errors can be replaced on each attempt.
-- No grants needed: spp_api already has DML on these tables from 0002.
ALTER TABLE app.import_job DROP CONSTRAINT import_job_status_check;
ALTER TABLE app.import_job ADD CONSTRAINT import_job_status_check
    CHECK (status IN ('uploaded', 'proposed', 'applying', 'applied', 'failed'));

ALTER TABLE app.import_issue
    ADD COLUMN stage text NOT NULL DEFAULT 'upload' CHECK (stage IN ('upload', 'apply'));
