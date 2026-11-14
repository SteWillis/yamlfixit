"""
YAML FixIt Repair & Validation Engine:
1. Repairs broken / malformed YAML syntax (tabs, unquoted colons, trailing commas, markdown fences)
2. Normalizes incomplete or non-standard YAML/JSON schema dicts into the canonical 6-section
   Ontology format expected by ontology.stewillis.com
3. Validates the schema using the exact rules from ontology.stewillis.com
"""

import json
import re
from typing import Dict, List, Tuple, Any
import yaml

VALID_RDBMS = {"mysql", "postgresql", "microsoft sql server", "sql server", "redshift", "mssql", "postgres"}
CANONICAL_RDBMS = {
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "microsoft sql server": "Microsoft SQL Server",
    "sql server": "Microsoft SQL Server",
    "mssql": "Microsoft SQL Server",
    "redshift": "Redshift"
}


def repair_and_parse_yaml(raw_text: str) -> Tuple[Dict[str, Any], List[str]]:
    """
    Attempts to parse YAML/JSON text, automatically repairing common formatting mistakes:
    - Markdown code fences (```yaml ... ```)
    - Tabs instead of spaces
    - Missing space after colon (`key:value` -> `key: value` when not inside URLs/strings)
    - Unbalanced or legacy top-level keys (`entities` -> `tables`, `relationships` -> `semantic_relationships`)
    Returns (normalized_ontology_dict, list_of_fixes_applied)
    """
    fixes = []
    text = raw_text.strip()

    # 1. Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        fixes.append("Removed surrounding Markdown code fences (```yaml).")

    # 2. Replace tab characters with 2 spaces
    if "\t" in text:
        text = text.replace("\t", "  ")
        fixes.append("Converted tab indentation to 2-space YAML indentation.")

    # 3. Try direct YAML or JSON parse first
    parsed = None
    try:
        parsed = yaml.safe_load(text)
    except Exception as first_err:
        # Attempt line-by-line syntax repair
        repaired_lines = []
        for idx, line in enumerate(text.splitlines()):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                repaired_lines.append(line)
                continue
            # Fix missing space after colon in key:value (e.g. `name:"orders"` or `pk:true`)
            m_colon = re.match(r"^(\s*-?\s*[a-zA-Z0-9_]+):([^\s/].*)$", line)
            if m_colon:
                line = f"{m_colon.group(1)}: {m_colon.group(2)}"
                fixes.append(f"Line {idx + 1}: Added missing space after colon.")
            # Quote values with unquoted special chars like colons inside descriptions
            m_kv = re.match(r"^(\s*-?\s*[a-zA-Z0-9_]+\s*:\s*)(.+)$", line)
            if m_kv:
                prefix, val = m_kv.group(1), m_kv.group(2).strip()
                if (
                    not val.startswith(("{", "[", '"', "'", "|", ">"))
                    and ":" in val
                    and not val.endswith(":")
                ):
                    escaped_val = val.replace('"', '\\"')
                    line = f'{prefix}"{escaped_val}"'
                    fixes.append(f"Line {idx + 1}: Quoted string containing unescaped colon.")
            repaired_lines.append(line)

        repaired_text = "\n".join(repaired_lines)
        try:
            parsed = yaml.safe_load(repaired_text)
        except Exception as second_err:
            raise ValueError(f"YAML syntax error could not be automatically resolved: {first_err}") from second_err

    if not isinstance(parsed, dict):
        raise ValueError("Parsed YAML root must be a mapping/dictionary.")

    normalized, norm_fixes = normalize_ontology_dict(parsed)
    fixes.extend(norm_fixes)
    return normalized, fixes


def normalize_ontology_dict(data: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """
    Normalizes an arbitrary or partial schema dictionary into the strict 6-section
    Ontology specification required by ontology.stewillis.com.
    """
    fixes: List[str] = []
    out: Dict[str, Any] = {}

    # Version
    out["version"] = str(data.get("version", "1.0"))
    if "version" not in data:
        fixes.append("Added missing top-level `version: \"1.0\"`.")

    # 1. Database metadata
    db_in = data.get("database")
    if isinstance(db_in, str):
        out["database"] = {
            "name": db_in,
            "rdbms_type": "PostgreSQL",
            "description": f"{db_in} database schema ontology"
        }
        fixes.append("Converted string `database` into structured metadata object.")
    elif isinstance(db_in, dict):
        raw_rdbms = str(db_in.get("rdbms_type") or db_in.get("type") or db_in.get("dialect") or "PostgreSQL").strip()
        norm_rdbms = CANONICAL_RDBMS.get(raw_rdbms.lower(), raw_rdbms)
        if norm_rdbms != raw_rdbms:
            fixes.append(f"Normalized `rdbms_type` from '{raw_rdbms}' to '{norm_rdbms}'.")
        out["database"] = {
            "name": str(db_in.get("name") or data.get("name") or "MyDatabase").strip(),
            "rdbms_type": norm_rdbms,
            "description": str(db_in.get("description") or "Enterprise database schema ontology.").strip()
        }
    else:
        out["database"] = {
            "name": str(data.get("name") or data.get("db_name") or "MyDatabase"),
            "rdbms_type": "PostgreSQL",
            "description": "Enterprise database schema ontology."
        }
        fixes.append("Created missing `database` metadata section.")

    # 2. Tables & Columns (check aliases like `entities` or `models`)
    raw_tables = data.get("tables") or data.get("entities") or data.get("models") or []
    if "tables" not in data and raw_tables:
        fixes.append("Mapped top-level table alias (`entities`/`models`) to `tables`.")

    normalized_tables: List[Dict[str, Any]] = []
    discovered_schemas = set()

    # Support dict-shaped tables (`tables: { users: { columns: ... } }`)
    if isinstance(raw_tables, dict):
        fixes.append("Converted dictionary-keyed `tables` map into standard list of table objects.")
        temp_list = []
        for k, v in raw_tables.items():
            if isinstance(v, dict):
                v_copy = dict(v)
                v_copy.setdefault("name", k)
                temp_list.append(v_copy)
        raw_tables = temp_list

    for t in raw_tables:
        if not isinstance(t, dict):
            continue
        raw_tname = str(t.get("name") or t.get("table") or t.get("table_name") or "").strip()
        if not raw_tname:
            continue
        if "." in raw_tname and not t.get("schema"):
            s_part, t_part = raw_tname.split(".", 1)
            t_schema = s_part.strip()
            t_name = t_part.strip()
            fixes.append(f"Split qualified table '{raw_tname}' into schema '{t_schema}' and table '{t_name}'.")
        else:
            t_schema = str(t.get("schema") or "public").strip()
            t_name = raw_tname

        discovered_schemas.add(t_schema)
        t_desc = str(t.get("description") or f"Records for {t_name.replace('_', ' ')}").strip()

        raw_cols = t.get("columns") or t.get("fields") or t.get("attributes") or []
        if isinstance(raw_cols, dict):
            fixes.append(f"Table '{t_name}': Converted dictionary-keyed `columns` into list.")
            c_list = []
            for ck, cv in raw_cols.items():
                if isinstance(cv, dict):
                    cv_copy = dict(cv)
                    cv_copy.setdefault("name", ck)
                    c_list.append(cv_copy)
                else:
                    c_list.append({"name": ck, "type": str(cv)})
            raw_cols = c_list

        norm_cols: List[Dict[str, Any]] = []
        has_pk = False
        for c in raw_cols:
            if isinstance(c, str):
                parts = c.split()
                c_name = parts[0]
                c_type = parts[1] if len(parts) > 1 else "varchar(255)"
                is_pk = c_name.lower() == "id"
                if is_pk:
                    has_pk = True
                norm_cols.append({
                    "name": c_name,
                    "type": c_type,
                    "pk": is_pk,
                    "description": c_name.replace("_", " ").title()
                })
                continue
            if not isinstance(c, dict):
                continue
            c_name = str(c.get("name") or c.get("column") or "").strip()
            if not c_name:
                continue
            c_type = str(c.get("type") or c.get("data_type") or "varchar(255)").strip()
            is_pk = bool(c.get("pk") or c.get("is_pk") or c.get("primary_key"))
            if is_pk:
                has_pk = True
            col_entry: Dict[str, Any] = {
                "name": c_name,
                "type": c_type,
                "pk": is_pk,
                "description": str(c.get("description") or c_name.replace("_", " ").title()).strip()
            }
            if c.get("fk") and isinstance(c["fk"], dict):
                tgt_tbl = str(c["fk"].get("target_table") or "").strip()
                tgt_col = str(c["fk"].get("target_column") or "id").strip()
                if tgt_tbl and "." not in tgt_tbl:
                    tgt_tbl = f"{t_schema}.{tgt_tbl}"
                    fixes.append(f"Qualified FK target_table '{tgt_tbl}' on '{t_name}.{c_name}'.")
                if tgt_tbl:
                    col_entry["fk"] = {"target_table": tgt_tbl, "target_column": tgt_col}
            norm_cols.append(col_entry)

        if not has_pk and norm_cols:
            # Auto-promote `id` or `<table_singular>_id` to PK
            promoted = False
            singular = t_name.rstrip("s").lower()
            for col in norm_cols:
                if col["name"].lower() in ("id", f"{singular}_id"):
                    col["pk"] = True
                    promoted = True
                    fixes.append(f"Table '{t_schema}.{t_name}': Marked '{col['name']}' as Primary Key (`pk: true`).")
                    break
            if not promoted:
                norm_cols[0]["pk"] = True
                fixes.append(f"Table '{t_schema}.{t_name}': Marked first column '{norm_cols[0]['name']}' as Primary Key (`pk: true`).")

        normalized_tables.append({
            "schema": t_schema,
            "name": t_name,
            "description": t_desc,
            "columns": norm_cols
        })

    # 3. Schemas section
    raw_schemas = data.get("schemas") or []
    schema_map: Dict[str, str] = {}
    if isinstance(raw_schemas, list):
        for s in raw_schemas:
            if isinstance(s, dict) and s.get("name"):
                schema_map[str(s["name"]).strip()] = str(s.get("description") or f"{s['name']} schema").strip()
            elif isinstance(s, str):
                schema_map[s.strip()] = f"{s.strip().title()} domain schema"
    for ds in sorted(discovered_schemas):
        if ds not in schema_map:
            schema_map[ds] = f"{ds.title()} functional module"
            fixes.append(f"Added schema '{ds}' to top-level `schemas` list.")

    out["schemas"] = [{"name": k, "description": v} for k, v in schema_map.items()]
    out["tables"] = normalized_tables

    # Build lookup of table names -> schema.table
    short_to_full: Dict[str, str] = {}
    full_tables_set = set()
    for t in normalized_tables:
        full_name = f"{t['schema']}.{t['name']}"
        full_tables_set.add(full_name)
        short_to_full[t["name"]] = full_name

    # Re-resolve any column FKs whose schema prefix didn't match an actual table's schema
    for t in normalized_tables:
        for c in t["columns"]:
            fk = c.get("fk")
            if fk and fk["target_table"] not in full_tables_set:
                short_tgt = fk["target_table"].split(".")[-1]
                if short_tgt in short_to_full:
                    fk["target_table"] = short_to_full[short_tgt]

    # 4. Semantic Relationships (also sync from column-level `fk` definitions!)
    raw_rels = data.get("semantic_relationships") or data.get("relationships") or []
    if "semantic_relationships" not in data and raw_rels:
        fixes.append("Mapped `relationships` to `semantic_relationships`.")

    norm_rels: List[Dict[str, Any]] = []
    seen_rel_keys = set()

    for r in raw_rels:
        if not isinstance(r, dict):
            continue
        src = str(r.get("source_table") or r.get("from_table") or r.get("source") or "").strip()
        tgt = str(r.get("target_table") or r.get("to_table") or r.get("target") or "").strip()
        src_col = str(r.get("source_column") or r.get("from_column") or "").strip()
        tgt_col = str(r.get("target_column") or r.get("to_column") or "id").strip()

        if src and "." not in src and src in short_to_full:
            src = short_to_full[src]
            fixes.append(f"Qualified relationship source_table '{src}'.")
        if tgt and "." not in tgt and tgt in short_to_full:
            tgt = short_to_full[tgt]
            fixes.append(f"Qualified relationship target_table '{tgt}'.")

        if not src or not tgt or not src_col:
            continue

        r_name = str(r.get("name") or f"REFERENCES_{tgt.split('.')[-1].upper()}").strip().upper()
        r_name = re.sub(r"[^A-Z0-9_]", "_", r_name)
        src_short = src.split(".")[-1]
        tgt_short = tgt.split(".")[-1]
        inv_name = str(r.get("inverse_name") or f"HAS_{src_short.upper()}").strip().upper()
        inv_name = re.sub(r"[^A-Z0-9_]", "_", inv_name)
        card = str(r.get("cardinality") or "N:1")

        raw_desc = str(r.get("description") or "").strip()
        fwd_desc = str(r.get("forward_description") or "").strip()
        rev_desc = str(r.get("reverse_description") or "").strip()

        if not fwd_desc:
            if raw_desc and " | [" not in raw_desc:
                fwd_desc = raw_desc
            else:
                fwd_desc = f"Each {src_short.rstrip('s').replace('_', ' ')} references {tgt_short.replace('_', ' ')} via {src_col}"
        if not rev_desc:
            if card == "1:1":
                rev_desc = f"Each {tgt_short.rstrip('s').replace('_', ' ')} corresponds to at most one {src_short.rstrip('s').replace('_', ' ')}"
            elif card == "1:N":
                rev_desc = f"Each {tgt_short.rstrip('s').replace('_', ' ')} belongs to one {src_short.rstrip('s').replace('_', ' ')}"
            else:
                rev_desc = f"Each {tgt_short.rstrip('s').replace('_', ' ')} can have multiple associated {src_short.replace('_', ' ')} records"

        combined_desc = raw_desc if (raw_desc and " | [" in raw_desc) else f"[{src_short} -> {tgt_short}]: {fwd_desc} | [{tgt_short} -> {src_short}]: {rev_desc}"

        key = (src, src_col, tgt, tgt_col)
        if key not in seen_rel_keys:
            seen_rel_keys.add(key)
            norm_rels.append({
                "name": r_name,
                "inverse_name": inv_name,
                "source_table": src,
                "target_table": tgt,
                "source_column": src_col,
                "target_column": tgt_col,
                "cardinality": card,
                "forward_description": fwd_desc,
                "reverse_description": rev_desc,
                "description": combined_desc
            })

    # Ensure every column-level `fk` has a matching entry in `semantic_relationships` and vice-versa
    for t in normalized_tables:
        src_full = f"{t['schema']}.{t['name']}"
        for c in t["columns"]:
            fk = c.get("fk")
            if fk and isinstance(fk, dict):
                tgt_full = fk.get("target_table", "")
                tgt_col = fk.get("target_column", "id")
                key = (src_full, c["name"], tgt_full, tgt_col)
                if tgt_full and key not in seen_rel_keys:
                    seen_rel_keys.add(key)
                    tgt_short = tgt_full.split(".")[-1]
                    src_short = t["name"]
                    fwd_desc = f"Each {src_short.rstrip('s').replace('_', ' ')} references {tgt_short.replace('_', ' ')} via {c['name']}"
                    rev_desc = f"Each {tgt_short.rstrip('s').replace('_', ' ')} can have multiple associated {src_short.replace('_', ' ')} records"
                    norm_rels.append({
                        "name": f"BELONGS_TO_{tgt_short.upper().rstrip('S')}",
                        "inverse_name": f"HAS_{src_short.upper()}",
                        "source_table": src_full,
                        "target_table": tgt_full,
                        "source_column": c["name"],
                        "target_column": tgt_col,
                        "cardinality": "N:1",
                        "forward_description": fwd_desc,
                        "reverse_description": rev_desc,
                        "description": f"[{src_short} -> {tgt_short}]: {fwd_desc} | [{tgt_short} -> {src_short}]: {rev_desc}"
                    })
                    fixes.append(f"Synced column FK `{src_full}.{c['name']}` into `semantic_relationships`.")

    # Also ensure every `semantic_relationships` item sets `fk` on the corresponding source column
    for r in norm_rels:
        for t in normalized_tables:
            if f"{t['schema']}.{t['name']}" == r["source_table"]:
                for c in t["columns"]:
                    if c["name"] == r["source_column"] and "fk" not in c:
                        c["fk"] = {
                            "target_table": r["target_table"],
                            "target_column": r["target_column"]
                        }

    out["semantic_relationships"] = norm_rels

    # 5. Business Concepts
    raw_concepts = data.get("business_concepts") or []
    norm_concepts: List[Dict[str, Any]] = []
    covered_tables = set()
    for bc in raw_concepts:
        if not isinstance(bc, dict):
            continue
        t_name = str(bc.get("table") or "").strip()
        s_name = str(bc.get("schema") or "").strip()
        if "." in t_name and not s_name:
            s_name, t_name = t_name.split(".", 1)
        if not s_name and t_name in short_to_full:
            s_name = short_to_full[t_name].split(".")[0]
        s_name = s_name or "public"
        if not t_name:
            continue
        covered_tables.add(f"{s_name}.{t_name}")
        norm_concepts.append({
            "name": str(bc.get("name") or t_name.title().replace("_", "")),
            "schema": s_name,
            "table": t_name,
            "key_column": str(bc.get("key_column") or "id"),
            "description": str(bc.get("description") or f"Business concept mapped to {s_name}.{t_name}")
        })

    if not norm_concepts and normalized_tables:
        for t in normalized_tables:
            pk_col = next((c["name"] for c in t["columns"] if c.get("pk")), "id")
            c_name = "".join(w.capitalize() for w in t["name"].rstrip("s").split("_")) or t["name"].capitalize()
            norm_concepts.append({
                "name": c_name,
                "schema": t["schema"],
                "table": t["name"],
                "key_column": pk_col,
                "description": t["description"]
            })
        fixes.append(f"Generated {len(norm_concepts)} default `business_concepts` for tables.")

    out["business_concepts"] = norm_concepts

    # 6. Common Join Paths
    raw_joins = data.get("common_join_paths") or []
    norm_joins = []
    for jp in raw_joins:
        if isinstance(jp, dict) and jp.get("name") and jp.get("sql"):
            norm_joins.append({
                "name": str(jp["name"]),
                "description": str(jp.get("description") or ""),
                "sql": str(jp["sql"]).strip()
            })
    out["common_join_paths"] = norm_joins

    return out, fixes


def validate_ontology(data: dict) -> Tuple[bool, List[str], List[str]]:
    """
    Validates an ontology dictionary using the exact rules from ontology.stewillis.com.
    Returns (is_valid, errors, warnings).
    """
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(data, dict):
        return False, ["Root document must be a YAML dictionary / mapping."], []

    db_meta = data.get("database")
    if not db_meta or not isinstance(db_meta, dict):
        errors.append("Missing required top-level key: 'database' (must include 'name' and 'rdbms_type').")
    else:
        db_name = db_meta.get("name")
        if not db_name:
            errors.append("Database metadata must define 'name'.")
        rdbms = str(db_meta.get("rdbms_type", "")).lower()
        if not rdbms or rdbms not in VALID_RDBMS:
            warnings.append(
                f"Unrecognized rdbms_type '{db_meta.get('rdbms_type', '')}'. "
                "Supported types: MySQL, PostgreSQL, Microsoft SQL Server, Redshift."
            )

    tables = data.get("tables", [])
    if not tables:
        errors.append("No tables defined in schema.")
        return False, errors, warnings

    table_registry: Dict[str, set] = {}
    for t in tables:
        if not isinstance(t, dict):
            errors.append("Table entry must be a dictionary.")
            continue
        t_name = t.get("name")
        schema_name = t.get("schema", "public")
        if not t_name:
            errors.append("Table missing 'name' attribute.")
            continue
        full_table = f"{schema_name}.{t_name}"
        table_registry[full_table] = set()

        columns = t.get("columns", [])
        if not columns:
            errors.append(f"Table '{full_table}' has no columns.")
            continue

        has_pk = False
        for c in columns:
            if not isinstance(c, dict):
                continue
            c_name = c.get("name")
            if not c_name:
                errors.append(f"Table '{full_table}' has a column with no 'name'.")
                continue
            table_registry[full_table].add(c_name)
            if c.get("pk"):
                has_pk = True

        if not has_pk:
            warnings.append(f"Table '{full_table}' has no primary key ('pk: true') defined.")

    rels = data.get("semantic_relationships", [])
    for r in rels:
        if not isinstance(r, dict):
            continue
        r_name = r.get("name", "UNNAMED")
        src_tbl = r.get("source_table")
        tgt_tbl = r.get("target_table")
        src_col = r.get("source_column")
        tgt_col = r.get("target_column")

        if src_tbl not in table_registry:
            errors.append(f"Relationship '{r_name}': source_table '{src_tbl}' is not defined in tables.")
        elif src_col not in table_registry[src_tbl]:
            errors.append(f"Relationship '{r_name}': source_column '{src_col}' does not exist on '{src_tbl}'.")

        if tgt_tbl not in table_registry:
            errors.append(f"Relationship '{r_name}': target_table '{tgt_tbl}' is not defined in tables.")
        elif tgt_col not in table_registry[tgt_tbl]:
            errors.append(f"Relationship '{r_name}': target_column '{tgt_col}' does not exist on '{tgt_tbl}'.")

    concepts = data.get("business_concepts", [])
    for bc in concepts:
        if not isinstance(bc, dict):
            continue
        bc_name = bc.get("name", "Unnamed")
        s = bc.get("schema", "public")
        t = bc.get("table")
        full_tbl = f"{s}.{t}"
        key_col = bc.get("key_column")

        if full_tbl not in table_registry:
            warnings.append(f"BusinessConcept '{bc_name}' references table '{full_tbl}' which is not in tables list.")
        elif key_col and key_col not in table_registry[full_tbl]:
            warnings.append(f"BusinessConcept '{bc_name}' references key_column '{key_col}' which does not exist on '{full_tbl}'.")

    return len(errors) == 0, errors, warnings
