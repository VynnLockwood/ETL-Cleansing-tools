-- Runs as spp_owner.
CREATE TABLE app.use_case (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name         text NOT NULL,
    button_label text,
    note         text,
    start_table  text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.use_case_field (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    use_case_id uuid NOT NULL REFERENCES app.use_case (id) ON DELETE CASCADE,
    position    int NOT NULL,
    table_name  text NOT NULL,
    column_name text NOT NULL,
    UNIQUE (use_case_id, table_name, column_name)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON app.use_case, app.use_case_field TO spp_api;
