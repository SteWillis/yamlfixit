"""
Gemini AI Auto-Enricher for YAML FixIt:
Takes a raw or minimally described ontology dictionary and enriches:
1. Database description & logical functional schemas (grouping tables by domain)
2. Rich, business-friendly descriptions for tables and columns
3. Meaningful semantic relationship names (e.g., PLACED_BY_CUSTOMER, RESERVED_FOR_SLOT)
4. Business Concepts glossary entries
5. Common SQL Join Paths (ready-to-use multi-table SQL queries)
"""

import json
import os
import re
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, List, Tuple

FALLBACK_MODELS = [
    "gemini-3-flash-preview",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-3.1-pro-preview",
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash"
]


def get_gemini_api_key(override_key: Optional[str] = None) -> str:
    if override_key and override_key.strip():
        return override_key.strip()
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if key and key != "your_gemini_api_key_here":
        return key

    # Check local .env or .env.prod
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    for fname in (".env", ".env.prod"):
        fpath = os.path.join(base_dir, fname)
        if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip().startswith("GEMINI_API_KEY="):
                        val = line.strip().split("=", 1)[1].strip()
                        if val and val != "your_gemini_api_key_here":
                            return val
    return ""


def call_gemini_json(prompt: str, api_key: str) -> Dict[str, Any]:
    """Invokes Google Gemini API and parses the JSON response."""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.15,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json"
        }
    }

    last_error = None
    for model in FALLBACK_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=35) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                candidates = res_data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        raw_text = parts[0].get("text", "").strip()
                        if raw_text.startswith("```"):
                            raw_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.DOTALL).strip()
                        return json.loads(raw_text)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            last_error = f"{e.code}: {err_body}"
            if e.code in (400, 404, 429, 500, 503):
                continue
            raise RuntimeError(f"Gemini API Error ({e.code}): {err_body}")
        except Exception as e:
            last_error = str(e)
            continue

    raise RuntimeError(f"Gemini AI enrichment failed across models: {last_error}")


def enrich_ontology_with_ai(ontology: Dict[str, Any], api_key: Optional[str] = None) -> Tuple[Dict[str, Any], List[str]]:
    """
    Uses Gemini AI (or smart deterministic heuristics if no API key is configured)
    to enrich descriptions, business concepts, relationship names, and common SQL join paths.
    """
    key = get_gemini_api_key(api_key)
    changes: List[str] = []

    if key:
        try:
            compact_summary = {
                "database": ontology.get("database", {}),
                "tables": [
                    {
                        "schema": t.get("schema", "public"),
                        "name": t.get("name"),
                        "columns": [
                            {"name": c.get("name"), "type": c.get("type"), "pk": c.get("pk", False), "fk": c.get("fk")}
                            for c in t.get("columns", [])
                        ]
                    }
                    for t in ontology.get("tables", [])
                ],
                "semantic_relationships": ontology.get("semantic_relationships", [])
            }

            prompt = f"""You are a Senior Data Architect building a Graph-RAG SQL Ontology for `ontology.stewillis.com`.
Analyze the following database structure and return a JSON object with rich semantic metadata:
1. "database_description": A concise 1-sentence executive description of the database.
2. "table_descriptions": An object mapping "<schema>.<table_name>" to a crisp 1-sentence description of the table's business role.
3. "column_descriptions": An object mapping "<schema>.<table_name>.<column_name>" to a concise description (mentioning allowed status values or FK meaning where relevant).
4. "relationship_enrichments": A list of objects for each existing or inferred foreign key explaining the relationship BOTH ways so an AI understands how to traverse Source->Target and Target->Source:
   {{"source_table": "schema.table", "source_column": "col", "target_table": "schema.table", "target_column": "col", "name": "FORWARD_VERB_NAME", "inverse_name": "REVERSE_VERB_NAME", "forward_description": "How source relates to target (Source -> Target)", "reverse_description": "How target relates to source (Target -> Source)"}}
5. "business_concepts": A list of business glossary concepts (1 per core table):
   {{"name": "PascalCaseConceptName", "schema": "schema", "table": "table_name", "key_column": "pk_col", "description": "Business definition for AI reasoning"}}
6. "common_join_paths": 2 to 3 realistic multi-table SQL SELECT queries joining related tables in this schema:
   {{"name": "Descriptive Query Title", "description": "What this join path retrieves", "sql": "SELECT ... FROM ... JOIN ..."}}

Input Schema JSON:
{json.dumps(compact_summary, indent=2)}
"""
            ai_data = call_gemini_json(prompt, key)

            if ai_data.get("database_description"):
                ontology.setdefault("database", {})["description"] = ai_data["database_description"]
                changes.append("AI enriched database overview description.")

            t_descs = ai_data.get("table_descriptions", {})
            c_descs = ai_data.get("column_descriptions", {})
            updated_t = 0
            updated_c = 0
            for t in ontology.get("tables", []):
                full_t = f"{t.get('schema', 'public')}.{t.get('name')}"
                if full_t in t_descs and t_descs[full_t]:
                    t["description"] = t_descs[full_t]
                    updated_t += 1
                elif t.get("name") in t_descs and t_descs[t["name"]]:
                    t["description"] = t_descs[t["name"]]
                    updated_t += 1
                for c in t.get("columns", []):
                    full_c = f"{full_t}.{c.get('name')}"
                    short_c = f"{t.get('name')}.{c.get('name')}"
                    if full_c in c_descs and c_descs[full_c]:
                        c["description"] = c_descs[full_c]
                        updated_c += 1
                    elif short_c in c_descs and c_descs[short_c]:
                        c["description"] = c_descs[short_c]
                        updated_c += 1

            if updated_t or updated_c:
                changes.append(f"AI enriched {updated_t} table descriptions and {updated_c} column descriptions.")

            rel_enrich = ai_data.get("relationship_enrichments", [])
            if isinstance(rel_enrich, list) and rel_enrich:
                rel_lookup = {
                    (r.get("source_table"), r.get("source_column")): r
                    for r in rel_enrich if isinstance(r, dict)
                }
                for r in ontology.get("semantic_relationships", []):
                    match = rel_lookup.get((r.get("source_table"), r.get("source_column")))
                    if match:
                        if match.get("name"):
                            r["name"] = re.sub(r"[^A-Z0-9_]", "_", str(match["name"]).upper())
                        if match.get("inverse_name"):
                            r["inverse_name"] = re.sub(r"[^A-Z0-9_]", "_", str(match["inverse_name"]).upper())
                        if match.get("forward_description"):
                            r["forward_description"] = str(match["forward_description"])
                        if match.get("reverse_description"):
                            r["reverse_description"] = str(match["reverse_description"])
                        src_s = str(r.get("source_table", "")).split(".")[-1]
                        tgt_s = str(r.get("target_table", "")).split(".")[-1]
                        fwd = r.get("forward_description") or match.get("description") or r.get("description", "")
                        rev = r.get("reverse_description") or ""
                        r["description"] = f"[{src_s} -> {tgt_s}]: {fwd}" + (f" | [{tgt_s} -> {src_s}]: {rev}" if rev else "")
                changes.append("AI refined bidirectional semantic relationship names and descriptions.")

            if isinstance(ai_data.get("business_concepts"), list) and ai_data["business_concepts"]:
                ontology["business_concepts"] = ai_data["business_concepts"]
                changes.append(f"AI generated {len(ai_data['business_concepts'])} business glossary concepts.")

            if isinstance(ai_data.get("common_join_paths"), list) and ai_data["common_join_paths"]:
                ontology["common_join_paths"] = ai_data["common_join_paths"]
                changes.append(f"AI synthesized {len(ai_data['common_join_paths'])} standard SQL join templates.")

            return ontology, changes
        except Exception as e:
            changes.append(f"Note: Live Gemini API fallback to smart heuristic enricher ({e}).")

    # Deterministic Smart Enricher (works even offline or if Gemini quota is reached)
    return _heuristic_enrich(ontology, changes)


def _heuristic_enrich(ontology: Dict[str, Any], changes: List[str]) -> Tuple[Dict[str, Any], List[str]]:
    """Deterministic semantic enrichment for tables, columns, concepts, and SQL join paths."""
    tables = ontology.get("tables", [])
    rels = ontology.get("semantic_relationships", [])

    for t in tables:
        t_name = t.get("name", "")
        readable_t = t_name.replace("_", " ")
        if not t.get("description") or t["description"].startswith("Records for "):
            t["description"] = f"Core domain table storing {readable_t} records and operational attributes."
        for c in t.get("columns", []):
            c_name = c.get("name", "")
            low = c_name.lower()
            if c.get("pk"):
                c["description"] = f"Primary unique identifier for {readable_t}"
            elif c.get("fk"):
                tgt = c["fk"].get("target_table", "").split(".")[-1]
                c["description"] = f"Foreign key linking to {tgt} ({c['fk'].get('target_column', 'id')})"
            elif low in ("created_at", "created_on", "date_created"):
                c["description"] = "Timestamp when the record was initially created"
            elif low in ("updated_at", "modified_at", "last_updated"):
                c["description"] = "Timestamp when the record was last modified"
            elif low in ("is_active", "active", "enabled"):
                c["description"] = "Boolean flag indicating whether the record is currently active"
            elif "status" in low or "state" in low:
                c["description"] = f"Lifecycle status indicator for {readable_t}"
            elif "email" in low:
                c["description"] = "Primary contact email address"
            elif "amount" in low or "price" in low or "total" in low or "cost" in low:
                c["description"] = f"Monetary value for {c_name.replace('_', ' ')}"

    # Generate a common join path if relationships exist and none are defined yet
    if rels and not ontology.get("common_join_paths"):
        first_rel = rels[0]
        src_t = first_rel["source_table"]
        tgt_t = first_rel["target_table"]
        src_alias = src_t.split(".")[-1][0].lower() + "1"
        tgt_alias = tgt_t.split(".")[-1][0].lower() + "2"
        sql = (
            f"SELECT\n"
            f"    {src_alias}.*,\n"
            f"    {tgt_alias}.*\n"
            f"FROM {src_t} {src_alias}\n"
            f"JOIN {tgt_t} {tgt_alias} ON {src_alias}.{first_rel['source_column']} = {tgt_alias}.{first_rel['target_column']}\n"
            f"LIMIT 100;"
        )
        ontology["common_join_paths"] = [
            {
                "name": f"{src_t.split('.')[-1].title()} to {tgt_t.split('.')[-1].title()} Join",
                "description": f"Joins {src_t} with parent {tgt_t} via {first_rel['source_column']}",
                "sql": sql
            }
        ]
        changes.append("Synthesized standard SQL join path template from foreign key relationships.")

    changes.append("Enriched table and column descriptions using semantic patterns.")
    return ontology, changes
