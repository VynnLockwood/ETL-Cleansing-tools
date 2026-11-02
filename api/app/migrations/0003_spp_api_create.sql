-- Runs as spp_owner. The import transaction creates tables in spp as spp_api
-- (spec: "no DDL on spp outside the import transaction"), so spp_api needs CREATE.
GRANT CREATE ON SCHEMA spp TO spp_api;
