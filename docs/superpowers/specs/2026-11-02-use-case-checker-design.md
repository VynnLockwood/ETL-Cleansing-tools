# Use Case Checker: Design

Date: 2026-11-02
Status: Draft for review
Builds on: `2026-11-02-spp-service-design.md` (this is its part 3, reshaped from the original "use-case ERD and view builder")

## 1. Purpose

The owner cannot tell whether the database structure is right for what the web screens need. A screen (a button in the web app) needs data that may live in many tables. Today there is no way to see whether that data can be reached through the foreign keys, or what is missing.

The Use Case Checker lets the owner:

1. Define a **use case**: a name (the button), and a list of fields (`table.column`) picked from dropdowns. Fields may come from many tables.
2. Press **Test**. The service works out how to reach every field through the foreign keys, builds the query or queries, runs a live preview, and shows what cannot be reached.
3. For every unreachable field, see **why** and a **recommendation** for fixing the structure.

Success criteria:

- On the real SPP schema (58 tables, 15 foreign keys), a use case with fields from `sppProject`, `sppBusinessPartner`, `sppProjectConnection` and `sppProjectAddress` tests as: partner reachable, connection reachable as a linked query (one-to-many), address **unreachable**, with a recommendation to add the key `sppProjectAddress.sppProject` to `sppProject.sppProjectID`.
- The preview rows come from the live database, read-only, in under the timeout.
- Everything the service executes is built from names verified against the database catalog. No free-form SQL is accepted.

## 2. Scope

In scope (version 1):

- Schema source: the live `spp` schema (read from the Postgres catalog).
- Use cases: create, list, edit, delete, test.
- Resolver: choose a start table, find join paths, split into a main query and linked queries.
- Live preview of each query.
- Recommendations from fixed rules (section 6).
- Web page with dropdowns, results, an ERD of the use case.

Out of scope (later, in this order): pin a chosen path when several are equal; a "one record" key filter; "what-if" test of a suggested key in a rolled-back transaction; schema-change regression (re-run all saved use cases after a new import); publish a query as a Superset dataset; draft mode that checks a dictionary without creating tables; composite-key suggestions.

## 3. Concepts

- **Edge**: a foreign key constraint. Traversing it from the table that holds the key to the table it points at is **many-to-one** (cost 1; also cost 1 if the key columns are unique or the primary key, which makes it one-to-one). Traversing it the other way is **one-to-many** (cost 3), because rows multiply (fan-out).
- **Start table**: the table the result is built around.
- **Main query**: the start table plus every table reachable through many-to-one edges only. One flat result, one row per start row.
- **Linked query**: for every one-to-many edge used, a separate query rooted at the table on the "many" side. It carries the key columns that tie it back to its parent (the **merge key**) and all fields reachable below it through many-to-one edges. A further one-to-many edge below a linked query starts another linked query. No query ever joins across a one-to-many edge, so rows never multiply.

## 4. Data model

New migration `0005` in schema `app`:

- `app.use_case(id uuid pk default gen_random_uuid(), name text not null, button_label text, note text, start_table text, created_at timestamptz default now(), updated_at timestamptz default now())`. `start_table` is an optional override; null means automatic.
- `app.use_case_field(id uuid pk default gen_random_uuid(), use_case_id uuid not null references app.use_case on delete cascade, position int not null, table_name text not null, column_name text not null, unique (use_case_id, table_name, column_name))`.
- Grants: `spp_api` DML on both tables.

Table and column names are validated against the catalog (schema `spp`, ordinary and partitioned tables) when a use case is saved. A field whose table or column disappears later is reported as `missing` at test time, not deleted.

## 5. Resolver

Input: the use case's fields, optional start table. Output: the plan and result described in section 7.

1. **Graph.** Read all foreign keys in schema `spp` from `pg_constraint` (multi-column keys are one edge with a column list) and all primary-key and unique constraints (to detect one-to-one).
2. **Start table.** If overridden, use it. Otherwise try each table that owns at least one chosen field and pick the one that minimises, in order: number of unreachable fields, number of one-to-many edges used, total path cost, table name. The reason is returned ("reaches 6 of 7 fields with 1 linked query").
3. **Paths.** Dijkstra from the start table over the cost model in section 3. Ties are broken by constraint name so the result is deterministic. If more than one path of equal cost exists to a needed table, the table is flagged `ambiguous` (recommendation `AMBIGUOUS_PATH`); the first path by constraint name is used.
4. **Plan.** The union of paths to the needed tables gives the join tree. Cut it at every one-to-many edge to get the main query and the linked queries. A field on a table with no path is `unreachable`.
5. **SQL.** Built with `psycopg.sql` composition. Aliases `t0, t1, ...`; all joins are `LEFT JOIN`; select-list aliases are `"table.column"`; a `LIMIT` parameter caps the rows. Main query selects the start table's primary key too (when it has one) so linked queries can be matched to it; a linked query selects its merge-key columns.

## 6. Recommendations

Fixed, deterministic rules. Each recommendation has `code`, `severity` (`error` blocks a field, `warning`, `info`), a plain message and, for key suggestions, a `fix` with `table`, `column`, `refTable`, `refColumn` and `evidence` (`matched`, `total`).

| Code | Trigger | Advice |
|---|---|---|
| `NO_PATH_KEY_MATCH` | A field's table is unreachable, and some column `c` of that table (or of a reached table) has all non-null values contained in the primary key of the other side, or (when the table is empty) its name contains the other table's name | Add a foreign key `T.c -> U.pk`; shows how many values matched. If column types differ, say they must be aligned first. Reuses the containment logic of `keys.py`. |
| `MISSING_TABLE` | A column named like a reference (name equals or starts with a name that has no table, e.g. `sppProject.mtZone`) with no foreign key | The column looks like a reference to table `X` which does not exist; create it. |
| `NO_PRIMARY_KEY` | A path is blocked because the table that would be pointed at has no primary key or unique key | Table has no key; define one. |
| `AMBIGUOUS_PATH` | Equal-cost paths | List the candidate paths; the first is used; pinning comes later. |
| `ISOLATED_TABLE` | The unreachable table has no foreign key in or out and no `NO_PATH_KEY_MATCH` candidate | Table is isolated; say which column should hold the key. |
| `FAN_OUT` | A linked query was needed | Info: N linked rows per start row, shown as a separate query. |

## 7. API

All routes need the admin login. JSON is lowerCamel. Added under `/api`:

- `GET /schema/tables` -> `[{name, columns:[{name, sqlType, isPrimaryKey, isForeignKey}]}]` for the dropdowns.
- `GET /usecases`, `POST /usecases`, `GET /usecases/{id}`, `PUT /usecases/{id}`, `DELETE /usecases/{id}`. Body: `{name, buttonLabel, note, startTable, fields:[{table, column}]}` (order of `fields` is the position). `422 {issues}` for unknown table or column; `404` for unknown or malformed id.
- `POST /usecases/{id}/test` -> `TestResult`:
  - `startTable`, `startTableReason`
  - `fields:[{table, column, status: "ok"|"unreachable"|"missing", query, via:[edge labels], reason}]`
  - `queries:[{id, title, kind: "main"|"linked", mergeKey, sql, columns, rows, truncated, error}]`
  - `recommendations:[...]` as in section 6
  - `erd:{nodes:[{table, role: "start"|"reached"|"unreachable", fields}], edges:[{from, to, columns, status: "fk"|"suggested", cardinality}]}`

## 8. Live preview

- Runs as role `spp_reader` through a second connection pool (built from `SPP_READER_PASSWORD`, already in `.env.example`).
- Each query runs in a read-only transaction with `SET LOCAL statement_timeout = '5s'` and `LIMIT 50` (constants in config).
- A query that fails (timeout, permission) returns its `error` string and does not fail the whole test.

## 9. Web page

A new page `/usecases.html`, linked from the existing page and back:

- Left: list of use cases with New / Delete.
- Editor: name, button label, note; field picker (table dropdown, then column dropdown filled from `/schema/tables`, Add button); the chosen fields list with remove and reorder; optional start table; Save.
- **Test** button: shows (a) the fields with green / red status and reasons, (b) recommendation cards, (c) the ERD drawn as SVG by the page (tables in columns by distance from the start table, solid lines for foreign keys, red dashed lines for suggested keys), (d) one tab per query with the SQL and preview rows.
- Server text is inserted with `textContent` only.

## 10. Error handling and safety

- All identifiers come from the catalog and are composed with `psycopg.sql.Identifier`; field names from the request are matched against the catalog before use.
- No endpoint accepts SQL.
- Large schemas: path search is over at most a few hundred tables; no limit needed in version 1.
- A catalog read failure returns `503` with a short message.

## 11. Testing

- Unit tests on a small hand-made schema with data (tables `a`, `b`, `c`, `d` with many-to-one, one-to-many, one-to-one, an unreachable table, and two equal paths): start-table choice, cost model, main/linked split, ambiguity flag, generated SQL results, each recommendation rule with a fixture that triggers it and one that does not.
- Read-only and timeout: a query that tries to write fails; `pg_sleep` longer than the timeout returns an `error` for that query only.
- API tests: validation, 404/422, auth on every route, camelCase.
- Real-schema test: load the real dictionary (schema only) and assert the success-criterion use case from section 1 (address unreachable, `NO_PATH_KEY_MATCH` for `sppProjectAddress.sppProject`).
- `scripts/e2e.py` gains a use-case step against the real dictionary.

## 12. Files

```
api/app/migrations/0005_use_cases.sql
api/app/usecases/{graph,resolver,sqlgen,recommend,preview,router,models}.py
api/tests/usecases/
frontend/usecases.html   (+ link in index.html)
```
