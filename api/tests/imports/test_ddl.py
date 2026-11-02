import psycopg
import pytest

from app.imports.ddl import DdlError, generate_ddl, quote_ident
from app.imports.models import ColumnProfile, ForeignKey, Proposal, TableProposal


def col(name, typ="text", unique=False):
    return ColumnProfile(name=name, sql_type=typ, non_null=1, distinct=1, unique=unique, samples=[])


def fk(t, c, rt, rc, accepted=True):
    return ForeignKey(table=t, column=c, ref_table=rt, ref_column=rc,
                      source="name", unmatched=0, accepted=accepted)


def table(name, cols, pk=None):
    return TableProposal(name=name, columns=cols, primary_key=pk or [])


def prop(tables, fks=(), audit=False):
    return Proposal(tables=tables, foreign_keys=list(fks), add_audit=audit)


def test_quotes_identifiers():
    stmts, _ = generate_ddl(prop([table('we"ird', [col('a"b')])]))
    assert stmts[0].startswith('CREATE TABLE "spp"."we""ird" (')
    assert '"a""b" text' in stmts[0]
    assert quote_ident("ชื่อ") == '"ชื่อ"'


@pytest.mark.parametrize("bad", ["", "a\x00b", "x" * 64, "ก" * 22])
def test_rejects_bad_identifiers(bad):
    with pytest.raises(DdlError):
        quote_ident(bad)


def test_orders_by_dependency():
    p = prop(
        [table("c", [col("id"), col("b_id")], ["id"]),
         table("b", [col("id"), col("a_id")], ["id"]),
         table("a", [col("id")], ["id"])],
        [fk("c", "b_id", "b", "id"), fk("b", "a_id", "a", "id")],
    )
    stmts, order = generate_ddl(p)
    assert order == ["a", "b", "c"]
    assert 'REFERENCES "spp"."a" ("id")' in stmts[1]
    assert [s.split('"')[3] for s in stmts] == order


def test_independent_tables_keep_input_order():
    _, order = generate_ddl(prop([table("z", [col("x")]), table("a", [col("x")])]))
    assert order == ["z", "a"]


def test_rejects_cycle():
    p = prop(
        [table("a", [col("id"), col("b_id")], ["id"]),
         table("b", [col("id"), col("a_id")], ["id"])],
        [fk("a", "b_id", "b", "id"), fk("b", "a_id", "a", "id")],
    )
    with pytest.raises(DdlError, match=r"cycle detected: a -> b -> a"):
        generate_ddl(p)


def test_includes_only_accepted_foreign_keys():
    p = prop(
        [table("a", [col("id")], ["id"]), table("b", [col("id"), col("a_id")], ["id"])],
        [fk("b", "a_id", "a", "id", accepted=False),
         fk("b", "a_id", "nope", "id"), fk("b", "a_id", "a", "nope")],
    )
    stmts, order = generate_ddl(p)
    assert all("REFERENCES" not in s for s in stmts)
    assert order == ["a", "b"]


def test_fk_to_non_unique_column_rejected():
    p = prop([table("a", [col("id"), col("x")], ["id"]), table("b", [col("a_x")])],
             [fk("b", "a_x", "a", "x")])
    with pytest.raises(DdlError, match="not a primary key or unique"):
        generate_ddl(p)


def test_fk_to_unique_column_adds_unique_constraint():
    p = prop([table("a", [col("id"), col("x", unique=True)], ["id"]), table("b", [col("a_x")])],
             [fk("b", "a_x", "a", "x")])
    stmts, _ = generate_ddl(p)
    assert '"x" text UNIQUE' in stmts[0]


def test_audit_columns():
    stmts, _ = generate_ddl(prop([table("a", [col("id"), col("version", "integer")], ["id"])],
                                 audit=True))
    s = stmts[0]
    for frag in ['"isDelete" boolean DEFAULT false', '"createdDate" timestamptz',
                 '"createdBy" uuid', '"createdName" varchar(255)',
                 '"createdEmployeeCode" varchar(50)', '"createdPosition" varchar(255)',
                 '"createdDept" varchar(512)', '"updatedBy" uuid', '"updatedDept" varchar(512)',
                 '"deletedBy" varchar(50)', '"deletedDate" timestamptz',
                 '"deletedName" varchar(255)']:
        assert frag in s
    assert '"version" integer DEFAULT 1' not in s  # existing column is not duplicated
    assert s.count('"version"') == 1
    stmts, _ = generate_ddl(prop([table("a", [col("id")], ["id"])], audit=True))
    assert '"version" integer DEFAULT 1' in stmts[0]


def test_self_reference_allowed():
    p = prop([table("n", [col("id"), col("parent")], ["id"])], [fk("n", "parent", "n", "id")])
    stmts, order = generate_ddl(p)
    assert order == ["n"]
    assert 'REFERENCES "spp"."n" ("id")' in stmts[0]


@pytest.mark.parametrize("typ", ["text; drop table x", "varchar(1) ", "int", "numeric(a)", ""])
def test_rejects_unknown_types(typ):
    with pytest.raises(DdlError):
        generate_ddl(prop([table("a", [col("c", typ)])]))


@pytest.mark.parametrize("typ", ["boolean", "double precision", "varchar(255)", "numeric(10,2)",
                                 "numeric(10)", "timestamptz", "uuid", "bigint"])
def test_accepts_known_types(typ):
    generate_ddl(prop([table("a", [col("c", typ)])]))


def test_bad_primary_key_rejected():
    with pytest.raises(DdlError):
        generate_ddl(prop([table("a", [col("id")], ["missing"])]))


def test_generated_ddl_executes_on_postgres(clean_db):
    p = prop(
        [table("child", [col("id", "integer"), col("parent_id", "integer")], ["id"]),
         table("parent", [col("id", "integer"), col("name", "varchar(50)")], ["id"])],
        [fk("child", "parent_id", "parent", "id")],
        audit=True,
    )
    stmts, order = generate_ddl(p)
    assert order == ["parent", "child"]
    with psycopg.connect(clean_db) as conn:
        for s in stmts:
            conn.execute(s)
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'spp' ORDER BY table_name"
        ).fetchall()
        assert [r[0] for r in rows] == ["child", "parent"]
        fkc = conn.execute(
            "SELECT count(*) FROM information_schema.table_constraints "
            "WHERE table_schema='spp' AND constraint_type='FOREIGN KEY'"
        ).fetchone()
        assert fkc[0] == 1
        conn.rollback()
    with psycopg.connect(clean_db) as conn:
        n = conn.execute("SELECT count(*) FROM information_schema.tables "
                         "WHERE table_schema='spp'").fetchone()[0]
        assert n == 0
