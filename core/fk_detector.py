"""
Smart Foreign Key & Semantic Relationship Detector:
Scans all tables and columns in an ontology dictionary and detects missing
Foreign Key relationships using naming conventions:
- `customer_id` -> `customers.id` or `customer.id` or `customers.customer_id`
- `created_by_user_id` -> `users.id`
- `warehouseId` -> `warehouses.id`
"""

import re
from typing import Dict, List, Any, Tuple


def _singular_variants(table_name: str) -> List[str]:
    """Generates singular stem variants for a table name (e.g. 'warehouses' -> ['warehouse', 'warehous'], 'categories' -> ['category'])."""
    t = table_name.lower().strip()
    variants = [t]
    if t.endswith("ies") and len(t) > 3:
        variants.append(t[:-3] + "y")
    if t.endswith("ses") or t.endswith("xes") or t.endswith("zes"):
        variants.append(t[:-2])
    if t.endswith("s") and not t.endswith("ss") and len(t) > 2:
        variants.append(t[:-1])
    return variants


def detect_foreign_keys(ontology: Dict[str, Any], auto_apply: bool = True) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Scans `ontology['tables']` for columns that look like foreign keys (`*_id`, `*Id`, `*_code`)
    and matches them against target tables and primary key columns.
    Returns (updated_ontology, list_of_detected_relationships).
    """
    tables = ontology.get("tables", [])
    existing_rels = ontology.get("semantic_relationships", [])

    existing_pairs = set()
    for r in existing_rels:
        existing_pairs.add((
            r.get("source_table"),
            r.get("source_column"),
            r.get("target_table"),
            r.get("target_column")
        ))

    # Build lookup index of candidate target tables:
    # stem -> (full_table_name, pk_column_name, short_table_name)
    stem_index: Dict[str, Tuple[str, str, str]] = {}
    for t in tables:
        schema = t.get("schema", "public")
        t_name = t.get("name", "")
        if not t_name:
            continue
        full_name = f"{schema}.{t_name}"
        cols = t.get("columns", [])
        pk_col = next((c["name"] for c in cols if c.get("pk")), None)
        if not pk_col and cols:
            pk_col = next((c["name"] for c in cols if c["name"].lower() == "id"), cols[0]["name"])
        pk_col = pk_col or "id"

        for stem in _singular_variants(t_name):
            stem_index[stem] = (full_name, pk_col, t_name)

    detected: List[Dict[str, Any]] = []

    SELF_REF_COL_NAMES = {
        "reports_to": ("REPORTS_TO", "MANAGES_DIRECT_REPORTS", "subordinate", "manager/supervisor"),
        "reportsto": ("REPORTS_TO", "MANAGES_DIRECT_REPORTS", "subordinate", "manager/supervisor"),
        "reports_to_id": ("REPORTS_TO", "MANAGES_DIRECT_REPORTS", "subordinate", "manager/supervisor"),
        "manager_id": ("REPORTS_TO_MANAGER", "MANAGES_SUBORDINATES", "employee", "manager"),
        "supervisor_id": ("SUPERVISED_BY", "SUPERVISES", "person", "supervisor"),
        "line_manager_id": ("REPORTS_TO_LINE_MANAGER", "MANAGES_TEAM_MEMBERS", "team member", "line manager"),
        "parent_id": ("BELONGS_TO_PARENT", "HAS_CHILD_RECORDS", "child record", "parent record"),
        "reply_to_id": ("REPLIES_TO", "HAS_REPLIES", "reply", "original post")
    }

    for t in tables:
        schema = t.get("schema", "public")
        t_name = t.get("name", "")
        src_full = f"{schema}.{t_name}"
        cols = t.get("columns", [])
        self_pk_col = next((c["name"] for c in cols if c.get("pk")), None)
        if not self_pk_col and cols:
            self_pk_col = next((c["name"] for c in cols if c["name"].lower() == "id"), cols[0]["name"])
        self_pk_col = self_pk_col or "id"

        for col in cols:
            if col.get("pk"):
                continue
            col_name = col.get("name", "")
            low_col = col_name.lower()

            # 1. Check explicit self-referencing hierarchy columns (e.g., reports_to, manager_id, parent_id)
            if low_col in SELF_REF_COL_NAMES or (low_col.startswith("parent_") and low_col.endswith("_id")):
                fwd_edge, rev_edge, child_role, parent_role = SELF_REF_COL_NAMES.get(
                    low_col,
                    ("BELONGS_TO_PARENT", "HAS_CHILDREN", "child record", "parent record")
                )
                pair_key = (src_full, col_name, src_full, self_pk_col)
                if pair_key not in existing_pairs:
                    fwd_desc = (
                        f"Self-referencing hierarchy: each {child_role} in {t_name} (alias child.{col_name}) "
                        f"references its {parent_role} in the same {t_name} table (alias parent.{self_pk_col})"
                    )
                    rev_desc = (
                        f"One {parent_role} in {t_name} (alias parent.{self_pk_col}) can have zero or many "
                        f"direct {child_role} records in {t_name} (alias child.{col_name})"
                    )
                    new_rel = {
                        "name": fwd_edge,
                        "inverse_name": rev_edge,
                        "source_table": src_full,
                        "target_table": src_full,
                        "source_column": col_name,
                        "target_column": self_pk_col,
                        "cardinality": "N:1",
                        "forward_description": fwd_desc,
                        "reverse_description": rev_desc,
                        "description": f"[{t_name} (child) -> {t_name} (parent)]: {fwd_desc} | [{t_name} (parent) -> {t_name} (child)]: {rev_desc}"
                    }
                    detected.append(new_rel)
                    existing_pairs.add(pair_key)
                    if auto_apply:
                        col["fk"] = {"target_table": src_full, "target_column": self_pk_col}
                        existing_rels.append(new_rel)
                continue

            # Check if column ends with `_id` or `Id`
            candidate_stem = None
            if low_col.endswith("_id") and len(low_col) > 3:
                candidate_stem = low_col[:-3]
            elif re.search(r"[a-z0-9]Id$", col_name):
                # camelCase e.g. customerId -> customer
                s1 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", col_name).lower()
                if s1.endswith("_id"):
                    candidate_stem = s1[:-3]

            if not candidate_stem:
                continue

            # Try exact stem match first, then suffix stem match (e.g. `created_by_user` -> `user`)
            matched_target = stem_index.get(candidate_stem)
            rel_prefix = ""
            if not matched_target and "_" in candidate_stem:
                parts = candidate_stem.split("_")
                # Check last 2 words then last 1 word
                if len(parts) >= 2:
                    last_two = "_".join(parts[-2:])
                    if last_two in stem_index:
                        matched_target = stem_index[last_two]
                        rel_prefix = "_".join(parts[:-2]).upper()
                if not matched_target:
                    last_one = parts[-1]
                    if last_one in stem_index:
                        matched_target = stem_index[last_one]
                        rel_prefix = "_".join(parts[:-1]).upper()

            if not matched_target:
                continue

            tgt_full, tgt_pk, tgt_short = matched_target
            # Avoid self-referencing unless explicit prefix like parent_category_id
            if tgt_full == src_full and not rel_prefix:
                continue

            pair_key = (src_full, col_name, tgt_full, tgt_pk)
            if pair_key in existing_pairs:
                continue

            rel_name = f"{rel_prefix}_{tgt_short.upper()}" if rel_prefix else f"BELONGS_TO_{tgt_short.upper().rstrip('S')}"
            rel_name = re.sub(r"[^A-Z0-9_]", "_", rel_name).strip("_")
            inv_name = f"HAS_{t_name.upper()}"
            inv_name = re.sub(r"[^A-Z0-9_]", "_", inv_name).strip("_")

            src_readable = t_name.replace("_", " ")
            tgt_readable = tgt_short.replace("_", " ").rstrip("s")
            fwd_desc = f"Each {src_readable.rstrip('s')} record belongs to one {tgt_readable} via {col_name}"
            rev_desc = f"Each {tgt_readable} can have multiple associated {src_readable} records"

            new_rel = {
                "name": rel_name,
                "inverse_name": inv_name,
                "source_table": src_full,
                "target_table": tgt_full,
                "source_column": col_name,
                "target_column": tgt_pk,
                "cardinality": "N:1",
                "forward_description": fwd_desc,
                "reverse_description": rev_desc,
                "description": f"[{t_name} -> {tgt_short}]: {fwd_desc} | [{tgt_short} -> {t_name}]: {rev_desc}"
            }
            detected.append(new_rel)
            existing_pairs.add(pair_key)

            if auto_apply:
                col["fk"] = {"target_table": tgt_full, "target_column": tgt_pk}
                existing_rels.append(new_rel)

    if auto_apply:
        ontology["semantic_relationships"] = existing_rels

    return ontology, detected
