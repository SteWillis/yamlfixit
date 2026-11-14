"""
Multi-Format Schema Parser for YAML FixIt:
Supports:
1. Raw SQL DDL (CREATE TABLE & ALTER TABLE statements from MySQL, PostgreSQL, SQL Server, Redshift)
2. CSV / TSV / TXT exports (INFORMATION_SCHEMA.COLUMNS or simple table,column,type lists)
3. Existing or partial YAML / JSON ontology definitions
"""

import csv
import io
import json
import re
from typing import Dict, List, Any, Tuple, Optional
import yaml


def _clean_ident(ident: str) -> str:
    """Strips quotes, backticks, and square brackets from SQL identifiers."""
    if not ident:
        return ""
    s = ident.strip()
    # Remove [dbo].[table] brackets or `backticks` or "quotes"
    if (s.startswith("`") and s.endswith("`")) or (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1]
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return s.strip()


def _split_schema_table(raw_name: str, default_schema: str = "public") -> Tuple[str, str]:
    """Splits a potentially schema-qualified table name like `sales`.`orders` or [dbo].[orders]."""
    raw_name = raw_name.strip()
    if "." in raw_name:
        parts = raw_name.split(".", 1)
        s = _clean_ident(parts[0]) or default_schema
        t = _clean_ident(parts[1])
        # Map SQL Server 'dbo' to 'public' or keep 'dbo'
        return s, t
    return default_schema, _clean_ident(raw_name)


def _split_top_level_commas(body: str) -> List[str]:
    """Splits a CREATE TABLE body by commas that are not inside parentheses or quotes."""
    items = []
    current = []
    depth = 0
    in_single = False
    in_double = False
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == "'" and not in_double:
            # Check escaped quote
            if i + 1 < n and body[i + 1] == "'":
                current.append("''")
                i += 2
                continue
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                items.append("".join(current).strip())
                current = []
                i += 1
                continue
        current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail:
        items.append(tail)
    return items


def parse_sql_ddl(sql_text: str, db_name: str = "MyDatabase", rdbms_type: str = "PostgreSQL") -> Dict[str, Any]:
    """
    Parses SQL DDL (CREATE TABLE and ALTER TABLE statements) into the canonical ontology dict.
    """
    # Strip block comments /* ... */ and line comments -- ...
    cleaned = re.sub(r"/\*.*?\*/", "", sql_text, flags=re.DOTALL)
    lines = []
    for line in cleaned.splitlines():
        # Strip -- comments unless inside quotes (simple heuristic)
        idx = line.find("--")
        if idx != -1:
            line = line[:idx]
        lines.append(line)
    cleaned_sql = "\n".join(lines)

    # Detect RDBMS hints from DDL if not overridden
    lower_sql = cleaned_sql.lower()
    detected_rdbms = rdbms_type
    if "auto_increment" in lower_sql or "engine=innodb" in lower_sql or "`" in cleaned_sql:
        detected_rdbms = "MySQL"
    elif "identity(" in lower_sql or "[dbo]" in lower_sql or "nvarchar" in lower_sql:
        detected_rdbms = "Microsoft SQL Server"
    elif "serial" in lower_sql or "timestamptz" in lower_sql or "bigserial" in lower_sql:
        detected_rdbms = "PostgreSQL"

    default_schema = "public" if detected_rdbms != "Microsoft SQL Server" else "dbo"

    tables_map: Dict[str, Dict[str, Any]] = {}  # full_name -> table dict
    schemas_set = set()
    relationships: List[Dict[str, Any]] = []

    # Find CREATE TABLE blocks using parenthesis balancing
    create_pattern = re.compile(
        r"CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([^\(\s]+)\s*\(",
        re.IGNORECASE
    )

    pos = 0
    while True:
        m = create_pattern.search(cleaned_sql, pos)
        if not m:
            break
        raw_table_name = m.group(1)
        start_paren = m.end() - 1
        # Find matching closing paren
        depth = 0
        in_single = False
        end_paren = -1
        for i in range(start_paren, len(cleaned_sql)):
            ch = cleaned_sql[i]
            if ch == "'":
                in_single = not in_single
            elif not in_single:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        end_paren = i
                        break
        if end_paren == -1:
            pos = m.end()
            continue

        table_body = cleaned_sql[start_paren + 1:end_paren]
        pos = end_paren + 1

        schema_name, table_name = _split_schema_table(raw_table_name, default_schema)
        schemas_set.add(schema_name)
        full_table = f"{schema_name}.{table_name}"

        columns = []
        pk_cols = set()
        inline_fks = []

        clauses = _split_top_level_commas(table_body)
        for clause in clauses:
            c_str = clause.strip()
            if not c_str:
                continue

            upper_c = c_str.upper()

            # Check table-level PRIMARY KEY (...)
            pk_match = re.match(
                r"^(?:CONSTRAINT\s+\S+\s+)?PRIMARY\s+KEY\s*(?:CLUSTERED|NONCLUSTERED)?\s*\(([^\)]+)\)",
                c_str,
                re.IGNORECASE
            )
            if pk_match:
                for pk_col in pk_match.group(1).split(","):
                    pk_cols.add(_clean_ident(pk_col.split()[0]))
                continue

            # Check table-level FOREIGN KEY (...) REFERENCES target_table (target_col)
            fk_match = re.match(
                r"^(?:CONSTRAINT\s+(\S+)\s+)?FOREIGN\s+KEY\s*\(([^\)]+)\)\s*REFERENCES\s+([^\(\s]+)\s*\(([^\)]+)\)",
                c_str,
                re.IGNORECASE
            )
            if fk_match:
                constraint_name = _clean_ident(fk_match.group(1) or "")
                src_col = _clean_ident(fk_match.group(2).split(",")[0])
                tgt_raw = fk_match.group(3)
                tgt_col = _clean_ident(fk_match.group(4).split(",")[0])
                tgt_s, tgt_t = _split_schema_table(tgt_raw, schema_name)
                inline_fks.append((src_col, f"{tgt_s}.{tgt_t}", tgt_col, constraint_name))
                continue

            # Skip UNIQUE / INDEX / KEY / CHECK constraints at table level
            if re.match(r"^(?:CONSTRAINT\s+\S+\s+)?(?:UNIQUE|INDEX|KEY|CHECK|FULLTEXT|SPATIAL)\b", upper_c):
                continue

            # Parse column definition: col_name data_type [modifiers...]
            col_tokens = c_str.split(None, 1)
            if len(col_tokens) < 2:
                continue
            col_name = _clean_ident(col_tokens[0])
            rest = col_tokens[1].strip()

            # Extract data type (including optional parens like varchar(255) or decimal(12,2) or unsigned)
            type_match = re.match(
                r"^([a-zA-Z0-9_]+(?:\s+varying|\s+precision|\s+ without time zone|\s+with time zone)?(?:\s*\([^\)]+\))?(?:\s+unsigned)?)",
                rest,
                re.IGNORECASE
            )
            if type_match:
                col_type = type_match.group(1).strip().lower()
                modifiers = rest[type_match.end():].strip()
            else:
                col_type = rest.split()[0].lower()
                modifiers = rest[len(col_type):].strip()

            is_pk = bool(re.search(r"\bPRIMARY\s+KEY\b", modifiers, re.IGNORECASE))
            if is_pk:
                pk_cols.add(col_name)

            # Extract inline COMMENT '...'
            desc = ""
            comment_match = re.search(r"\bCOMMENT\s+'([^']*)'", modifiers, re.IGNORECASE)
            if comment_match:
                desc = comment_match.group(1).strip()

            # Extract inline REFERENCES target_table(target_col)
            ref_match = re.search(
                r"\bREFERENCES\s+([^\(\s]+)\s*(?:\(([^\)]+)\))?",
                modifiers,
                re.IGNORECASE
            )
            col_fk = None
            if ref_match:
                tgt_raw = ref_match.group(1)
                tgt_col = _clean_ident(ref_match.group(2) or "id")
                tgt_s, tgt_t = _split_schema_table(tgt_raw, schema_name)
                tgt_full = f"{tgt_s}.{tgt_t}"
                col_fk = {"target_table": tgt_full, "target_column": tgt_col}
                inline_fks.append((col_name, tgt_full, tgt_col, ""))

            col_obj: Dict[str, Any] = {
                "name": col_name,
                "type": col_type,
                "pk": is_pk,
                "description": desc or f"{col_name.replace('_', ' ').title()}"
            }
            if col_fk:
                col_obj["fk"] = col_fk
            columns.append(col_obj)

        # Apply table-level PKs and FKs to columns
        for col in columns:
            if col["name"] in pk_cols:
                col["pk"] = True

        for src_col, tgt_full, tgt_col, cname in inline_fks:
            for col in columns:
                if col["name"] == src_col:
                    col["fk"] = {"target_table": tgt_full, "target_column": tgt_col}
            rel_name = cname.upper() if cname else f"HAS_{tgt_full.split('.')[-1].upper()}"
            rel_name = re.sub(r"[^A-Z0-9_]", "_", rel_name)
            relationships.append({
                "name": rel_name,
                "source_table": full_table,
                "target_table": tgt_full,
                "source_column": src_col,
                "target_column": tgt_col,
                "cardinality": "N:1",
                "description": f"{table_name}.{src_col} references {tgt_full}({tgt_col})"
            })

        tables_map[full_table] = {
            "schema": schema_name,
            "name": table_name,
            "description": f"Records for {table_name.replace('_', ' ')}",
            "columns": columns
        }

    # Also parse standalone ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY statements
    alter_fk_pattern = re.compile(
        r"ALTER\s+TABLE\s+(?:ONLY\s+)?([^\s]+)\s+ADD\s+(?:CONSTRAINT\s+(\S+)\s+)?FOREIGN\s+KEY\s*\(([^\)]+)\)\s*REFERENCES\s+([^\(\s]+)\s*\(([^\)]+)\)",
        re.IGNORECASE
    )
    for am in alter_fk_pattern.finditer(cleaned_sql):
        src_raw, cname, src_col_raw, tgt_raw, tgt_col_raw = am.groups()
        src_s, src_t = _split_schema_table(src_raw, default_schema)
        src_full = f"{src_s}.{src_t}"
        tgt_s, tgt_t = _split_schema_table(tgt_raw, src_s)
        tgt_full = f"{tgt_s}.{tgt_t}"
        src_col = _clean_ident(src_col_raw.split(",")[0])
        tgt_col = _clean_ident(tgt_col_raw.split(",")[0])

        if src_full in tables_map:
            for col in tables_map[src_full]["columns"]:
                if col["name"] == src_col:
                    col["fk"] = {"target_table": tgt_full, "target_column": tgt_col}
        rel_name = _clean_ident(cname or "").upper() or f"REFERENCES_{tgt_t.upper()}"
        rel_name = re.sub(r"[^A-Z0-9_]", "_", rel_name)
        relationships.append({
            "name": rel_name,
            "source_table": src_full,
            "target_table": tgt_full,
            "source_column": src_col,
            "target_column": tgt_col,
            "cardinality": "N:1",
            "description": f"{src_t}.{src_col} references {tgt_full}({tgt_col})"
        })

    schemas_list = [
        {"name": s, "description": f"{s.title()} domain schema"}
        for s in sorted(schemas_set)
    ]

    # Build initial business concepts from tables
    business_concepts = []
    for full_t, t_obj in tables_map.items():
        pk_col = next((c["name"] for c in t_obj["columns"] if c.get("pk")), None)
        if not pk_col and t_obj["columns"]:
            pk_col = t_obj["columns"][0]["name"]
        concept_name = "".join(word.capitalize() for word in t_obj["name"].rstrip("s").split("_")) or t_obj["name"].capitalize()
        business_concepts.append({
            "name": concept_name,
            "schema": t_obj["schema"],
            "table": t_obj["name"],
            "key_column": pk_col or "id",
            "description": t_obj["description"]
        })

    return {
        "version": "1.0",
        "database": {
            "name": db_name or "MyDatabase",
            "rdbms_type": detected_rdbms,
            "description": f"Database ontology generated from SQL DDL ({len(tables_map)} tables)."
        },
        "schemas": schemas_list,
        "business_concepts": business_concepts,
        "tables": list(tables_map.values()),
        "semantic_relationships": relationships,
        "common_join_paths": []
    }


def parse_csv_txt(raw_text: str, db_name: str = "MyDatabase", rdbms_type: str = "PostgreSQL") -> Dict[str, Any]:
    """
    Parses CSV, TSV, or structured TXT column lists into the canonical ontology dict.
    Supports:
    - INFORMATION_SCHEMA exports (table_schema, table_name, column_name, data_type, column_key, description, etc.)
    - Simple CSV/TSV with headers (table, column, type, pk, fk, description)
    - Headerless CSV/TSV (table_name, column_name, data_type)
    """
    lines = [ln.strip() for ln in raw_text.strip().splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        raise ValueError("Empty CSV/TXT input provided.")

    # Detect delimiter
    first_line = lines[0]
    delimiter = ","
    if "\t" in first_line:
        delimiter = "\t"
    elif "|" in first_line:
        delimiter = "|"
    elif ";" in first_line and "," not in first_line:
        delimiter = ";"

    reader = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))
    if not reader:
        raise ValueError("Could not parse any rows from CSV/TXT.")

    header_candidates = [h.strip().lower().replace(" ", "_") for h in reader[0]]
    known_headers = {
        "table", "table_name", "tablename", "schema", "table_schema", "schema_name",
        "column", "column_name", "columnname", "field", "type", "data_type", "datatype",
        "column_type", "pk", "is_pk", "column_key", "key", "fk", "foreign_key",
        "references", "description", "comment", "column_comment"
    }

    has_header = any(h in known_headers for h in header_candidates)
    rows = reader[1:] if has_header else reader

    tables_map: Dict[str, Dict[str, Any]] = {}
    schemas_set = set()
    relationships: List[Dict[str, Any]] = []

    def _find_idx(names: List[str], default: Optional[int] = None) -> Optional[int]:
        if not has_header:
            return default
        for n in names:
            if n in header_candidates:
                return header_candidates.index(n)
        return None

    schema_idx = _find_idx(["table_schema", "schema", "schema_name"], None)
    table_idx = _find_idx(["table_name", "table", "tablename", "entity"], 0)
    col_idx = _find_idx(["column_name", "column", "columnname", "field", "attribute"], 1)
    type_idx = _find_idx(["column_type", "data_type", "type", "datatype"], 2)
    pk_idx = _find_idx(["column_key", "pk", "is_pk", "key", "primary_key"], 3)
    fk_idx = _find_idx(["fk", "foreign_key", "references", "target"], 4)
    desc_idx = _find_idx(["column_comment", "description", "comment", "notes"], 5)

    for row in rows:
        row = [c.strip() for c in row if c is not None]
        if not row or len(row) < 2:
            continue

        schema_name = "public"
        if schema_idx is not None and schema_idx < len(row) and row[schema_idx]:
            schema_name = _clean_ident(row[schema_idx])

        raw_table = row[table_idx] if (table_idx is not None and table_idx < len(row)) else "table_1"
        if "." in raw_table and schema_idx is None:
            schema_name, table_name = _split_schema_table(raw_table, "public")
        else:
            table_name = _clean_ident(raw_table)

        col_name = _clean_ident(row[col_idx]) if (col_idx is not None and col_idx < len(row)) else ""
        if not table_name or not col_name:
            continue

        col_type = row[type_idx].lower() if (type_idx is not None and type_idx < len(row) and row[type_idx]) else "varchar(255)"

        is_pk = False
        if pk_idx is not None and pk_idx < len(row):
            pk_val = row[pk_idx].strip().upper()
            if pk_val in ("PRI", "PK", "TRUE", "YES", "1", "PRIMARY", "PRIMARY KEY"):
                is_pk = True
        elif col_name.lower() == "id":
            is_pk = True

        fk_obj = None
        if fk_idx is not None and fk_idx < len(row) and row[fk_idx]:
            fk_raw = row[fk_idx].strip()
            # Format: target_table.target_col or schema.target_table(target_col)
            m_fk = re.match(r"^([^\(\.]+(?:\.[^\(\.]+)?)[\.\(]([^\)\s]+)\)?$", fk_raw)
            if m_fk:
                t_raw, t_col = m_fk.group(1), m_fk.group(2)
                ts, tt = _split_schema_table(t_raw, schema_name)
                fk_obj = {"target_table": f"{ts}.{tt}", "target_column": t_col}
            elif fk_raw.upper() not in ("", "NULL", "NONE", "FALSE", "0", "MUL"):
                ts, tt = _split_schema_table(fk_raw, schema_name)
                fk_obj = {"target_table": f"{ts}.{tt}", "target_column": "id"}

        desc = row[desc_idx] if (desc_idx is not None and desc_idx < len(row) and row[desc_idx]) else f"{col_name.replace('_', ' ').title()}"

        schemas_set.add(schema_name)
        full_table = f"{schema_name}.{table_name}"
        if full_table not in tables_map:
            tables_map[full_table] = {
                "schema": schema_name,
                "name": table_name,
                "description": f"Records for {table_name.replace('_', ' ')}",
                "columns": []
            }

        col_entry: Dict[str, Any] = {
            "name": col_name,
            "type": col_type,
            "pk": is_pk,
            "description": desc
        }
        if fk_obj:
            col_entry["fk"] = fk_obj
            relationships.append({
                "name": f"REFERENCES_{fk_obj['target_table'].split('.')[-1].upper()}",
                "source_table": full_table,
                "target_table": fk_obj["target_table"],
                "source_column": col_name,
                "target_column": fk_obj["target_column"],
                "cardinality": "N:1",
                "description": f"{table_name}.{col_name} references {fk_obj['target_table']}({fk_obj['target_column']})"
            })

        tables_map[full_table]["columns"].append(col_entry)

    # Ensure every table has at least 1 PK flagged if `id` or `<table>_id` is present
    for full_t, t_obj in tables_map.items():
        if not any(c.get("pk") for c in t_obj["columns"]) and t_obj["columns"]:
            for c in t_obj["columns"]:
                if c["name"].lower() in ("id", f"{t_obj['name'].rstrip('s').lower()}_id"):
                    c["pk"] = True
                    break

    schemas_list = [{"name": s, "description": f"{s.title()} domain schema"} for s in sorted(schemas_set)]
    business_concepts = [
        {
            "name": "".join(w.capitalize() for w in t["name"].rstrip("s").split("_")) or t["name"].capitalize(),
            "schema": t["schema"],
            "table": t["name"],
            "key_column": next((c["name"] for c in t["columns"] if c.get("pk")), t["columns"][0]["name"] if t["columns"] else "id"),
            "description": t["description"]
        }
        for t in tables_map.values()
    ]

    return {
        "version": "1.0",
        "database": {
            "name": db_name or "MyDatabase",
            "rdbms_type": rdbms_type or "PostgreSQL",
            "description": f"Database ontology imported from CSV/TXT ({len(tables_map)} tables)."
        },
        "schemas": schemas_list,
        "business_concepts": business_concepts,
        "tables": list(tables_map.values()),
        "semantic_relationships": relationships,
        "common_join_paths": []
    }
