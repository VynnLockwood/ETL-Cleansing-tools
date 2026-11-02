# SDD ledger — plan: docs/superpowers/plans/2026-11-02-use-case-checker.md

Spec: docs/superpowers/specs/2026-11-02-use-case-checker-design.md (reachable). Branch: feat/use-case-checker (off master 18666c8). Executor: executing-plans (inline). Commits: repo git identity, no Claude trailers (user preference).
Env: no Docker daemon; local PostgreSQL 16 at /tmp/pgtest port 55432 (roles spp_owner/ownerpw spp_api/apipw spp_reader/readerpw). TEST_DATABASE_URL=postgresql://spp_owner:ownerpw@/spp?host=/tmp/pgtest&port=55432

## Pre-flight (shared interfaces)
| Tasks | Produces vs consumes | Finding |
|---|---|---|
| 1 -> 2,3,8 | Config.reader_database_url, app.state.reader_pool (None when unset) | consistent; routes must 503 when None |
| 2 -> 3 | SchemaGraph/load_graph used by validate; router.py created in T2, extended in T3/T8 | consistent |
| 3 -> 4 | FieldRef in models.py consumed by resolver | consistent (T3 before T4) |
| 4 -> 5 | Resolution/Step/Path | consistent |
| 5 -> 6,7 | QuerySpec, compose | consistent |
| 6 -> 7,8 | read_only_connection, QueryResult | CONFLICT: plan puts QueryResult in preview.py but TestResult (T8, models.py) embeds it while preview imports planner->resolver->models.FieldRef => circular import |
| 7 -> 8 | Recommendation in models.py | consistent |
| tests tree | unique test basenames (no __init__.py in tests/) | checked: no collisions with existing tests |

## Rulings
Ruling: QueryResult is defined in app/usecases/models.py (Task 6), preview.py imports it from there — avoids the circular import models<->preview — cost if wrong: none (import path only).

## Progress
Task 1: complete (commits 7a279b4..de11901, tests: bash -c 'cd api && python -m pytest -q -W error' → 251 passed in 7.95s)
Task 2: complete (commits de11901..6ef1467, tests: bash -c 'cd api && python -m pytest -q -W error' → 262 passed in 10.13s)
Task 2: Ruling: partition children (relispartition) are excluded from the graph; their parent partitioned table is included — children would duplicate inherited FK constraints — cost if wrong: partition children cannot be picked as fields.
Task 3: complete (commits 6ef1467..52cd2dd, tests: bash -c 'cd api && python -m pytest -q -W error' → 276 passed in 12.31s)
Task 4: complete (commits 52cd2dd..94a3c70, tests: bash -c 'cd api && python -m pytest -q -W error' → 290 passed in 13.87s)
Task 5: complete (commits 94a3c70..b899b96, tests: bash -c 'cd api && python -m pytest -q -W error' → 307 passed in 13.63s)
Task 5: Ruling: a linked query is also created for every intermediate fan-out ancestor (no selected fields, only merge-key columns) so parent_query chains never dangle; main also selects the columns its linked children reference (normally the PK) — plan said main selects the PK only — cost if wrong: a few extra select columns.
Task 5: Ruling: main query is omitted when its select list would be empty (start has no key, no fields, no children) — cost if wrong: none.
Task 6: complete (commits b899b96..f166fd4, tests: bash -c 'cd api && python -m pytest -q -W error' → 315 passed in 13.99s)
Task 7: complete (commits f166fd4..b4be583, tests: bash -c 'cd api && python -m pytest -q -W error' → 332 passed in 17.74s)
Task 7: Ruling: MISSING_TABLE is checked only for tables that own a chosen field with status ok (plan said "reached tables", i.e. the whole connected component, which would flood the user with unrelated mt* warnings) — cost if wrong: a missing reference table is not reported until a field from its table is chosen.
Task 7: Ruling: candidate keys for NO_PATH_KEY_MATCH rank name-hint first, then most matched values; a data-only match with no name hint is still offered (spec: "all non-null values contained") — cost if wrong: a spurious suggestion for small integer columns.
Task 8: complete (commits b4be583..d14e68d, tests: bash -c 'cd api && python -m pytest -q -W error' → 346 passed in 18.07s)
Task 8: Ruling: run_check takes (reader_pool, cfg, uc) — the plan's first `pool` parameter was unused (everything runs read-only on the reader pool) — cost if wrong: none.
Task 8: Ruling: with automatic start table the real-schema use case (project, partner, connection, address) starts at sppProjectConnection and is all flat (one main query), so the spec success criterion (connection as a linked query) holds only with start_table=sppProject; the acceptance test and e2e step use that override, and a second test pins the automatic choice — cost if wrong: none (tests document both).
Task 9: complete (commits d14e68d..db4fe4e, tests: bash -c 'node --test frontend/erd.test.js >/dev/null && cd api && python -m pytest -q -W error' → 346 passed in 19.63s)
Task 9: verified in real Chromium (Playwright) against the real API and the real 58-table schema: connect, build a 4-field use case, test, ERD (4 nodes, 2 solid + 1 dashed edge), tabs, delete, wrong password. Found and fixed from the screenshots: suggested edge drew straight through another box (now an arc over the top for edges spanning layers), edge labels overlapped (GAP_X 150, short n:1 labels with white outline), "Deleted." status shown before the list refreshed (order fixed).
Task 9: finding for the user: the real dictionary has mt* columns on sppBusinessPartner (mtTypeEntity, mtAccountClass, mtTitle, mtGender, mtStatusMarital, mtTypeLegalEntity) with NO foreign key note and NO table of that name — the checker reports them as MISSING_TABLE; earlier statement that only mtZone and mtVoltageLevel are undefined was about FK notes only.
Task 10: complete (commits db4fe4e..ea53804, tests: bash -c 'cd api && python -m pytest -q -W error' → 346 passed in 20.02s)
Task 10: e2e step RED (API without reader pool: 503 at /schema/tables) then GREEN twice in a row on the same stack (58 tables, all checks passed).
Task 10: Ruling: HANDOFF/README updated for the use case checker; the existing HANDOFF text about the earlier stack/import work was kept below a new section — cost if wrong: none.

## Final review (fresh opus reviewer, whole branch; no Critical, 4 Important)
Final: fixed a timed-out evidence query treated as "no evidence" (wrong suggestion / false ISOLATED_TABLE) — test_timed_out_evidence_is_reported_not_ignored and test_partial_timeout_is_mentioned_on_the_suggestion RED→GREEN, suite 354/354
Final: fixed unbounded evidence queries — key matching now only compares against tables the use case uses (start, field tables, path tables), skips columns that are already foreign keys, drops the ::text cast when types are equal, and stops after a 4×timeout budget; test_evidence_only_for_tables_the_use_case_uses and test_foreign_key_columns_are_not_offered_as_new_keys RED→GREEN; real schema: 539 → 15 evidence queries for the 4-field use case (a 17-table use case is still 1228 queries, cut off by the budget with a "time limit" note), suite 354/354
Final: fixed page could not test a use case with a dropped field (it saved first and the save was refused) — dirty tracking, unchanged use cases are tested as stored; browser check /tmp/ui_check2.py RED→GREEN
Final: fixed MISSING_TABLE false warnings on lookup tables' own columns (mtSPPFuelClassifiedID etc.) — primary keys and columns prefixed by an existing mt* table name are skipped; test_missing_table_ignores_lookup_tables_own_columns and test_missing_table_ignores_primary_key_columns RED→GREEN
Final: re-graded Minor→fixed by effect on the user: NO_PRIMARY_KEY contradicting the key card next to it (test_no_primary_key_not_reported_when_that_table_holds_the_key RED→GREEN); "table.column" headers cut at 63 bytes and merging (positional SQL aliases, readable names from the plan; test_long_column_names_are_not_truncated_or_merged RED→GREEN); Save and Test both creating the same new use case (all action buttons disabled while any request runs; browser check RED→GREEN); docs wrong in 3 places (this ledger is now committed as docs/superpowers/sdd-ledger-use-case-checker.md; README says the whole page needs SPP_READER_PASSWORD; spec rows updated)
Final: Ruling: with automatic start the real-schema use case starts at sppProjectConnection (one flat query, one row per connection), so projects without a connection are not in the preview — this follows the spec's start-table ranking; the start reason is shown on the page, and "choose the start table" is the way to get one row per project — cost if wrong: a surprising preview until the user picks sppProject.
Final: Ruling: a candidate whose check times out never silently loses to a weaker match: the card says "Not every column could be checked (time limit)" and, when nothing is confirmed, a warning-level NO_PATH_KEY_MATCH without a fix replaces ISOLATED_TABLE — cost if wrong: one more warning card.
Final: minor (deferred): linked query shows only its own merge columns, not the parent side (QueryResult has no parent_query/parent columns), previews have no ORDER BY so row order can change between runs, and linked rows are not filtered to the 50 main rows
Final: minor (deferred): catalog read failure returns 500 instead of 503 (spec §10)
Final: minor (deferred): AMBIGUOUS_PATH card repeated for every table below an ambiguous one, and its "N equal paths" is capped at 4 (alternatives are capped at 3)
Final: minor (deferred): a saved start table that no longer exists is silently shown as "(automatic)" and dropped on the next save
Final: minor (deferred): a wrong reader password makes API startup wait 30 s and fail, taking the import features down too; the main pool is not closed on that path
Final: minor (deferred): no size limits on a use case (field count, name length) or on preview cell size
Final: minor (deferred): extreme use cases (many unreachable tables) still run up to ~1200 evidence queries before the time budget stops them
