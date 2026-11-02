-- Runs as spp_owner (no superuser needed). Roles are created by postgres/init/01-roles.sh.
CREATE SCHEMA IF NOT EXISTS spp;
CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS usecase;

-- spp_api: read/write on spp and app
GRANT USAGE ON SCHEMA spp, app TO spp_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA spp, app TO spp_api;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA spp, app TO spp_api;
ALTER DEFAULT PRIVILEGES FOR ROLE spp_owner IN SCHEMA spp, app
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO spp_api;
ALTER DEFAULT PRIVILEGES FOR ROLE spp_owner IN SCHEMA spp, app
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO spp_api;

-- spp_api: can create views in usecase
GRANT USAGE, CREATE ON SCHEMA usecase TO spp_api;

-- spp_reader: read-only on spp and usecase
GRANT USAGE ON SCHEMA spp, usecase TO spp_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA spp, usecase TO spp_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE spp_owner IN SCHEMA spp, usecase
    GRANT SELECT ON TABLES TO spp_reader;
