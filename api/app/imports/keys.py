from app.imports.models import (
    ForeignKey,
    Proposal,
    TableProfile,
    TableProposal,
    Workbook,
)


def _primary_key(profile: TableProfile) -> list[str]:
    wanted = f"{profile.name}id".lower()
    for col in profile.columns:
        if col.name.lower() == wanted:
            return [col.name]
    candidates = [
        c
        for c in profile.columns
        if c.unique and c.non_null > 0 and c.non_null == profile.row_count
    ]
    return [candidates[0].name] if len(candidates) == 1 else []


def _column_values(wb: Workbook, profile: TableProfile) -> dict[str, list[str]]:
    """Stripped non-null values per column name (first column wins on duplicate names)."""
    sheet = next((s for s in wb.sheets if s.name == profile.name), None)
    out: dict[str, list[str]] = {}
    if sheet is None:
        return out
    for i, header in enumerate(sheet.headers):
        if header in out:
            continue
        vals = [row[i].strip() if i < len(row) else "" for row in sheet.rows]
        out[header] = [v for v in vals if v != ""]
    return out


def detect_keys(
    wb: Workbook, profiles: list[TableProfile], threshold: float = 0.99
) -> Proposal:
    pks = {p.name: _primary_key(p) for p in profiles}
    values = {p.name: _column_values(wb, p) for p in profiles}
    # Value sets are built once per table, only for single-column primary keys.
    pk_sets = {
        name: set(values[name].get(pk[0], []))
        for name, pk in pks.items()
        if len(pk) == 1
    }

    fks: list[ForeignKey] = []
    for p in profiles:
        own_pk = pks[p.name]
        seen_cols: set[str] = set()
        for col in p.columns:
            if col.name in seen_cols or col.name in own_pk:
                continue
            seen_cols.add(col.name)
            vals = values[p.name].get(col.name, [])
            if not vals:
                continue
            # (rank key, ForeignKey-without-accepted data); lower rank sorts first.
            found: list[tuple[tuple, ForeignKey, bool]] = []
            for order, ref in enumerate(profiles):
                if ref.name == p.name or ref.name not in pk_sets:
                    continue
                ref_col = pks[ref.name][0]
                ref_set = pk_sets[ref.name]
                unmatched = sum(1 for v in vals if v not in ref_set)
                matched = len(vals) - unmatched
                if matched == 0:
                    continue
                by_name = col.name.lower() == ref_col.lower()
                by_containment = matched / len(vals) >= threshold
                if not (by_name or by_containment):
                    continue
                named = ref.name.lower() in col.name.lower()
                found.append(
                    (
                        (0 if by_name else 1, -matched / len(vals), unmatched),
                        ForeignKey(
                            table=p.name,
                            column=col.name,
                            ref_table=ref.name,
                            ref_column=ref_col,
                            source="name" if by_name else "containment",
                            unmatched=unmatched,
                            accepted=False,
                        ),
                        by_name or named,
                    )
                )
            if not found:
                continue
            # Stable sort keeps profile order as the final tie-breaker.
            found.sort(key=lambda f: f[0])
            _, best, corroborated = found[0]
            ambiguous = len(found) > 1 and found[0][0] == found[1][0]
            best.accepted = best.unmatched == 0 and corroborated and not ambiguous
            fks.append(best)

    tables = [
        TableProposal(name=p.name, columns=p.columns, primary_key=pks[p.name])
        for p in profiles
    ]
    return Proposal(tables=tables, foreign_keys=fks)
