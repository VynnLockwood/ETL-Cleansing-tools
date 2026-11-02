-- Runs as spp_owner.
CREATE TABLE app.import_job (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    filename   text,
    status     text CHECK (status IN ('uploaded', 'proposed', 'applied', 'failed')),
    proposal   jsonb,
    created_at timestamptz DEFAULT now()
);

CREATE TABLE app.import_issue (
    id       bigserial PRIMARY KEY,
    job_id   uuid REFERENCES app.import_job (id),
    sheet    text,
    "column" text,
    row      int,
    message  text
);

GRANT SELECT, INSERT, UPDATE, DELETE ON app.import_job, app.import_issue TO spp_api;
GRANT USAGE, SELECT ON SEQUENCE app.import_issue_id_seq TO spp_api;
