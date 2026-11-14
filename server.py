#!/usr/bin/env python3
"""
YAML FixIt Studio - Web Server (yamlfixit.stewillis.com)
Companion to ontology.stewillis.com:
- Multi-format import: Raw SQL DDL, CSV/TXT schemas, broken/partial YAML, + DB Introspection SQL generator
- Interactive ER Node Canvas: Full-page mode, smooth zoom/pan, topological hierarchical layout,
  inline double-click editing, hover blast-radius highlighting, schema filter pills, & live minimap
- Smart Foreign Key Auto-Detector + Gemini AI Auto-Enricher
- Direct Bridge to ontology.stewillis.com (1-click Push to Neo4j Ontology Studio)
- Persistent Project Snapshots & Shareable URLs (?project=slug) + localStorage auto-save
- Exports 100% compliant Ontology YAML (.yaml), Markdown Guide (.md), SQL DDL (.sql), and Vector Diagram (.svg)
"""

import os
import re
import sys
import json
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.parsers import parse_sql_ddl, parse_csv_txt
from core.fixer import repair_and_parse_yaml, normalize_ontology_dict, validate_ontology
from core.fk_detector import detect_foreign_keys
from core.enricher import enrich_ontology_with_ai
from core.exporters import export_to_yaml, export_to_markdown, export_to_sql_ddl

PORT = int(os.getenv("PORT", "8076"))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SNAPSHOTS_DIR = os.path.join(BASE_DIR, "snapshots")
os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

DEFAULT_ONTOLOGY = {
    "version": "1.0",
    "database": {
        "name": "CommerceHub",
        "rdbms_type": "PostgreSQL",
        "description": "Multi-tenant e-commerce orders, customer accounts, and product inventory database."
    },
    "schemas": [
        {"name": "sales", "description": "Customer accounts, orders, and checkout line items"},
        {"name": "inventory", "description": "Warehouses, product catalog, and stock levels"}
    ],
    "business_concepts": [
        {
            "name": "Customer",
            "schema": "sales",
            "table": "customers",
            "key_column": "id",
            "description": "Registered buyer account with contact and billing details"
        },
        {
            "name": "Order",
            "schema": "sales",
            "table": "orders",
            "key_column": "id",
            "description": "Customer purchase order header record"
        },
        {
            "name": "Product",
            "schema": "inventory",
            "table": "products",
            "key_column": "id",
            "description": "Catalog SKU item available for purchase"
        }
    ],
    "tables": [
        {
            "schema": "sales",
            "name": "customers",
            "description": "Registered buyer accounts and profile metadata",
            "columns": [
                {"name": "id", "type": "serial", "pk": True, "description": "Unique customer ID"},
                {"name": "email", "type": "varchar(255)", "pk": False, "description": "Primary customer email"},
                {"name": "full_name", "type": "varchar(150)", "pk": False, "description": "Customer full name"},
                {"name": "created_at", "type": "timestamp", "pk": False, "description": "Account creation timestamp"}
            ]
        },
        {
            "schema": "sales",
            "name": "orders",
            "description": "Customer order headers and fulfillment status",
            "columns": [
                {"name": "id", "type": "serial", "pk": True, "description": "Unique order ID"},
                {
                    "name": "customer_id",
                    "type": "int",
                    "pk": False,
                    "fk": {"target_table": "sales.customers", "target_column": "id"},
                    "description": "Purchasing customer reference"
                },
                {"name": "status", "type": "varchar(50)", "pk": False, "description": "Order status: pending, paid, shipped"},
                {"name": "total_amount", "type": "decimal(12,2)", "pk": False, "description": "Order total in GBP"},
                {"name": "placed_at", "type": "timestamp", "pk": False, "description": "Checkout timestamp"}
            ]
        },
        {
            "schema": "sales",
            "name": "order_items",
            "description": "Individual product line items within an order",
            "columns": [
                {"name": "id", "type": "serial", "pk": True, "description": "Line item ID"},
                {
                    "name": "order_id",
                    "type": "int",
                    "pk": False,
                    "fk": {"target_table": "sales.orders", "target_column": "id"},
                    "description": "Parent order ID"
                },
                {
                    "name": "product_id",
                    "type": "int",
                    "pk": False,
                    "fk": {"target_table": "inventory.products", "target_column": "id"},
                    "description": "Purchased product SKU ID"
                },
                {"name": "quantity", "type": "int", "pk": False, "description": "Units ordered"},
                {"name": "unit_price", "type": "decimal(12,2)", "pk": False, "description": "Captured unit price"}
            ]
        },
        {
            "schema": "inventory",
            "name": "products",
            "description": "Master product catalog and pricing",
            "columns": [
                {"name": "id", "type": "serial", "pk": True, "description": "Product ID"},
                {"name": "sku", "type": "varchar(64)", "pk": False, "description": "Unique stock keeping unit code"},
                {"name": "name", "type": "varchar(255)", "pk": False, "description": "Display product title"},
                {"name": "price", "type": "decimal(12,2)", "pk": False, "description": "Current retail unit price"},
                {"name": "is_active", "type": "boolean", "pk": False, "description": "True if listed for sale"}
            ]
        }
    ],
    "semantic_relationships": [
        {
            "name": "PLACED_BY_CUSTOMER",
            "inverse_name": "PLACES_ORDERS",
            "source_table": "sales.orders",
            "target_table": "sales.customers",
            "source_column": "customer_id",
            "target_column": "id",
            "cardinality": "N:1",
            "forward_description": "Each order is placed by one registered customer account",
            "reverse_description": "A customer can place zero or many orders over time",
            "description": "[orders -> customers]: Each order is placed by one registered customer account | [customers -> orders]: A customer can place zero or many orders over time"
        },
        {
            "name": "BELONGS_TO_ORDER",
            "inverse_name": "HAS_ORDER_ITEMS",
            "source_table": "sales.order_items",
            "target_table": "sales.orders",
            "source_column": "order_id",
            "target_column": "id",
            "cardinality": "N:1",
            "forward_description": "Each order line item belongs to one parent order header",
            "reverse_description": "An order header contains one or more purchased order line items",
            "description": "[order_items -> orders]: Each order line item belongs to one parent order header | [orders -> order_items]: An order header contains one or more purchased order line items"
        },
        {
            "name": "REFERENCES_PRODUCT",
            "inverse_name": "ORDERED_IN_ITEMS",
            "source_table": "sales.order_items",
            "target_table": "inventory.products",
            "source_column": "product_id",
            "target_column": "id",
            "cardinality": "N:1",
            "forward_description": "Each order line item references a specific catalog product SKU",
            "reverse_description": "A catalog product can appear across many customer order line items",
            "description": "[order_items -> products]: Each order line item references a specific catalog product SKU | [products -> order_items]: A catalog product can appear across many customer order line items"
        }
    ],
    "common_join_paths": [
        {
            "name": "Full Customer Order Breakdown",
            "description": "Joins customers, orders, line items, and products for revenue reporting",
            "sql": (
                "SELECT\n"
                "    c.email,\n"
                "    o.id AS order_id,\n"
                "    o.status,\n"
                "    p.sku,\n"
                "    p.name AS product_name,\n"
                "    oi.quantity,\n"
                "    oi.unit_price\n"
                "FROM sales.orders o\n"
                "JOIN sales.customers c ON o.customer_id = c.id\n"
                "JOIN sales.order_items oi ON o.id = oi.order_id\n"
                "JOIN inventory.products p ON oi.product_id = p.id;"
            )
        }
    ]
}


def _seed_builtin_snapshots():
    """Seeds CommerceHub and PQBookings into the snapshots folder."""
    ch_path = os.path.join(SNAPSHOTS_DIR, "commercehub.json")
    with open(ch_path, "w", encoding="utf-8") as f:
        json.dump({"slug": "commercehub", "name": "CommerceHub (E-Commerce)", "ontology": DEFAULT_ONTOLOGY, "positions": {}}, f, indent=2)

    pq_yaml_path = os.path.join(BASE_DIR, "pqbookings_schema.yaml")
    pq_snap_path = os.path.join(SNAPSHOTS_DIR, "pqbookings.json")
    if os.path.exists(pq_yaml_path):
        try:
            with open(pq_yaml_path, "r", encoding="utf-8") as f:
                raw_pq = yaml.safe_load(f.read())
            norm_pq, _ = normalize_ontology_dict(raw_pq)
            with open(pq_snap_path, "w", encoding="utf-8") as f:
                json.dump({"slug": "pqbookings", "name": "PQBookings (Warehouse & Slots)", "ontology": norm_pq, "positions": {}}, f, indent=2)
        except Exception as e:
            sys.stderr.write(f"[YAMLFixIt] Note: Could not seed pqbookings snapshot: {e}\n")


_seed_builtin_snapshots()


def _list_snapshots():
    items = []
    for fname in sorted(os.listdir(SNAPSHOTS_DIR)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(SNAPSHOTS_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            slug = data.get("slug") or fname[:-5]
            ont = data.get("ontology") or {}
            db_name = ont.get("database", {}).get("name") or slug
            t_count = len(ont.get("tables", []))
            items.append({
                "slug": slug,
                "name": data.get("name") or f"{db_name} ({t_count} tables)",
                "tables_count": t_count
            })
        except Exception:
            continue
    return items


def _push_yaml_to_ontology_bridge(yaml_text: str) -> dict:
    """Pushes validated YAML to ontology-sql-bridge (/api/deploy) on port 8075."""
    candidate_urls = [
        os.getenv("ONTOLOGY_BRIDGE_URL", "").rstrip("/"),
        "http://host.docker.internal:8075",
        "http://127.0.0.1:8075",
        "http://172.17.0.1:8075"
    ]
    last_err = "Could not reach ontology-sql-bridge on port 8075"
    for base in candidate_urls:
        if not base:
            continue
        url = f"{base}/api/deploy"
        try:
            req = urllib.request.Request(
                url,
                data=yaml_text.encode("utf-8"),
                headers={"Content-Type": "text/yaml; charset=utf-8"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = str(e)
            continue
    raise RuntimeError(last_err)


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Ste Willis | YAML FixIt — Ontology & Schema Builder</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
  <style>
    body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }
    pre, code, .font-mono { font-family: 'JetBrains Mono', monospace; }

    /* Native modal dialog scroll containment & backdrop per modern-web-guidance */
    dialog:modal {
      overscroll-behavior: contain;
    }
    dialog::backdrop {
      overflow: hidden;
      overscroll-behavior: contain;
      background-color: rgba(15, 23, 42, 0.55);
      backdrop-filter: blur(3px);
    }

    /* Interactive ER Canvas dot-grid background */
    .er-canvas-bg {
      background-color: #0b1120;
      background-image: radial-gradient(#334155 1.25px, transparent 1.25px);
      background-size: 22px 22px;
    }

    .er-node {
      user-select: none;
      box-shadow: 0 12px 28px -6px rgba(0, 0, 0, 0.5), 0 0 0 1px rgba(148, 163, 184, 0.18);
      transition: box-shadow 0.15s ease, opacity 0.18s ease, transform 0.15s ease;
    }
    .er-node:hover, .er-node.node-highlighted {
      box-shadow: 0 16px 36px -4px rgba(99, 102, 241, 0.42), 0 0 0 2px rgba(129, 140, 248, 0.85);
      z-index: 30;
    }
    .er-node.node-dimmed {
      opacity: 0.32;
    }

    .port-dot {
      width: 14px;
      height: 14px;
      border-radius: 9999px;
      background: #6366f1;
      border: 2px solid #e0e7ff;
      cursor: crosshair;
      z-index: 35;
      box-shadow: 0 0 0 2px rgba(15, 23, 42, 0.9);
      transition: transform 0.12s ease, background-color 0.12s ease, box-shadow 0.12s ease;
    }
    .port-dot::after {
      content: '';
      position: absolute;
      inset: -8px;
      border-radius: 9999px;
    }
    .port-dot:hover, .port-dot.active-port {
      transform: scale(1.45);
      background: #fbbf24;
      border-color: #ffffff;
      box-shadow: 0 0 12px rgba(251, 191, 36, 0.9);
    }

    .wire-drop-target-row {
      background-color: rgba(99, 102, 241, 0.38) !important;
      outline: 2px solid #fbbf24 !important;
      outline-offset: -1px;
      border-radius: 6px;
    }
    .wire-drop-target-card {
      box-shadow: 0 0 0 3px #fbbf24, 0 16px 36px -4px rgba(251, 191, 36, 0.45) !important;
    }

    .wire-path {
      fill: none;
      stroke-width: 2.25;
      cursor: pointer;
      transition: stroke-width 0.15s ease, stroke 0.15s ease, opacity 0.18s ease;
    }
    .wire-path:hover, .wire-path.wire-highlighted {
      stroke-width: 3.75;
      stroke: #fbbf24 !important;
    }
    .wire-path.wire-dimmed {
      opacity: 0.18;
    }

    /* Full-page canvas overlay mode */
    .builder-fullpage {
      position: fixed !important;
      inset: 0 !important;
      z-index: 40 !important;
      width: 100vw !important;
      height: 100dvh !important;
      max-height: 100dvh !important;
      border-radius: 0 !important;
      border: none !important;
      margin: 0 !important;
    }
  </style>
</head>
<body class="bg-slate-100/80 text-slate-800 min-h-screen antialiased selection:bg-indigo-500 selection:text-white p-3 sm:p-5 lg:p-6">
  <div class="max-w-[1640px] mx-auto space-y-4">

    <!-- Top Bento Header -->
    <header class="bg-white border border-slate-200/90 rounded-2xl p-4 sm:p-5 shadow-sm flex flex-col xl:flex-row justify-between items-start xl:items-center gap-4">
      <div class="flex items-center gap-3.5">
        <div class="w-11 h-11 rounded-xl bg-slate-900 text-white flex items-center justify-center shadow-md shrink-0">
          <i class="fa-solid fa-wand-magic-sparkles text-indigo-400 text-lg"></i>
        </div>
        <div>
          <div class="flex items-center gap-2.5 flex-wrap">
            <h1 class="text-xl font-bold tracking-tight text-slate-900">Ste Willis</h1>
            <span class="text-slate-300 font-light">|</span>
            <span class="text-indigo-600 font-semibold tracking-wide text-lg">YAML FixIt Studio</span>
            <span class="bg-indigo-50 border border-indigo-200 text-indigo-700 text-[11px] px-2.5 py-0.5 rounded-full font-medium flex items-center gap-1.5">
              <span class="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-pulse"></span>
              Ontology Schema &amp; ER Builder
            </span>
          </div>
          <p class="text-slate-500 text-xs mt-0.5">
            Import SQL DDL, CSV/TXT, or broken YAML • Double-click canvas cards to edit • Push directly to
            <a href="https://ontology.stewillis.com" target="_blank" rel="noopener" class="text-indigo-600 hover:underline font-medium">ontology.stewillis.com</a>
          </p>
        </div>
      </div>

      <!-- Global Action Toolbar -->
      <div class="flex flex-wrap items-center gap-2 w-full xl:w-auto">
        <!-- Saved Project / Preset Switcher -->
        <div class="flex items-center bg-slate-50 border border-slate-200 rounded-xl px-2.5 py-1.5 gap-1.5">
          <i class="fa-solid fa-folder-open text-slate-400 text-xs"></i>
          <select id="projectSnapshotSelect" onchange="loadSelectedSnapshot(this.value)" class="bg-transparent text-xs font-semibold text-slate-700 focus:outline-none cursor-pointer">
            <option value="">Switch Project...</option>
          </select>
        </div>

        <button type="button" onclick="saveAndShareSnapshot()" class="bg-white hover:bg-slate-50 border border-slate-200 text-slate-700 px-3 py-2 rounded-xl text-xs font-semibold transition flex items-center gap-1.5 shadow-sm" title="Save snapshot on server and copy shareable link">
          <i class="fa-solid fa-share-nodes text-emerald-600"></i>
          Save &amp; Share
        </button>

        <button type="button" onclick="openImportModal()" class="bg-slate-900 hover:bg-slate-800 text-white px-3.5 py-2 rounded-xl text-xs font-semibold transition flex items-center gap-2 shadow-sm">
          <i class="fa-solid fa-file-import text-indigo-400"></i>
          Import / Fix
        </button>

        <button type="button" onclick="runAutoDetectFKs()" class="bg-white hover:bg-slate-50 border border-slate-200 text-slate-700 px-3 py-2 rounded-xl text-xs font-medium transition flex items-center gap-1.5 shadow-sm" title="Scan column names (*_id) and automatically connect tables">
          <i class="fa-solid fa-link text-amber-500"></i>
          Auto-FKs
        </button>

        <button type="button" id="aiEnrichBtn" onclick="runAiEnrich()" class="bg-indigo-50 hover:bg-indigo-100 border border-indigo-200 text-indigo-700 px-3 py-2 rounded-xl text-xs font-semibold transition flex items-center gap-1.5 shadow-sm" title="Use Gemini AI to enrich descriptions, business concepts, and SQL join templates">
          <i class="fa-solid fa-sparkles text-indigo-600"></i>
          AI Enrich
        </button>

        <button type="button" id="pushBridgeBtn" onclick="pushToOntologyStudio()" class="bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 text-white px-3.5 py-2 rounded-xl text-xs font-semibold transition flex items-center gap-1.5 shadow-sm" title="Deploy this Ontology YAML straight into Neo4j on ontology.stewillis.com">
          <i class="fa-solid fa-cloud-bolt"></i>
          Push to Ontology Studio
        </button>
      </div>
    </header>

    <!-- Status / Toast Banner -->
    <div id="toastBanner" class="hidden bg-indigo-950 text-indigo-100 border border-indigo-800 rounded-xl px-4 py-2.5 text-xs flex items-center justify-between shadow-sm">
      <div class="flex items-center gap-2.5">
        <i class="fa-solid fa-circle-check text-emerald-400"></i>
        <span id="toastMessage">Ready.</span>
      </div>
      <button type="button" onclick="document.getElementById('toastBanner').classList.add('hidden')" class="text-indigo-300 hover:text-white">
        <i class="fa-solid fa-xmark"></i>
      </button>
    </div>

    <!-- Database Metadata & Export Bar -->
    <section class="bg-white border border-slate-200/90 rounded-2xl p-3.5 shadow-sm">
      <div class="grid grid-cols-1 md:grid-cols-12 gap-3 items-center">
        <div class="md:col-span-2">
          <label class="block text-[10px] font-semibold uppercase tracking-wider text-slate-400 mb-1">Database Name</label>
          <input id="dbNameInput" type="text" oninput="updateDbMeta()" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-slate-800 focus:outline-none focus:border-indigo-500" placeholder="MyDatabase">
        </div>
        <div class="md:col-span-2">
          <label class="block text-[10px] font-semibold uppercase tracking-wider text-slate-400 mb-1">SQL Dialect (RDBMS)</label>
          <select id="dbRdbmsInput" onchange="updateDbMeta()" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-2 py-1.5 text-xs font-medium text-slate-800 focus:outline-none focus:border-indigo-500">
            <option value="PostgreSQL">PostgreSQL</option>
            <option value="MySQL">MySQL</option>
            <option value="Microsoft SQL Server">Microsoft SQL Server</option>
            <option value="Redshift">Redshift</option>
          </select>
        </div>
        <div class="md:col-span-4">
          <label class="block text-[10px] font-semibold uppercase tracking-wider text-slate-400 mb-1">Ontology Description</label>
          <input id="dbDescInput" type="text" oninput="updateDbMeta()" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 text-xs text-slate-700 focus:outline-none focus:border-indigo-500" placeholder="Brief summary of the database purpose...">
        </div>
        <div class="md:col-span-4 flex flex-wrap items-center justify-end gap-1.5 pt-2 md:pt-0">
          <span id="statsSummary" class="text-[11px] font-mono font-semibold text-slate-500 mr-1">0 Tables • 0 Links</span>
          <button type="button" onclick="downloadOutput('yaml')" class="bg-emerald-600 hover:bg-emerald-500 text-white px-2.5 py-1.5 rounded-lg text-xs font-semibold transition flex items-center gap-1 shadow-sm">
            <i class="fa-solid fa-download"></i> .YAML
          </button>
          <button type="button" onclick="downloadOutput('md')" class="bg-slate-800 hover:bg-slate-700 text-white px-2.5 py-1.5 rounded-lg text-xs font-semibold transition flex items-center gap-1 shadow-sm">
            <i class="fa-brands fa-markdown"></i> .MD
          </button>
          <button type="button" onclick="downloadOutput('sql')" class="bg-indigo-600 hover:bg-indigo-500 text-white px-2.5 py-1.5 rounded-lg text-xs font-semibold transition flex items-center gap-1 shadow-sm">
            <i class="fa-solid fa-database"></i> .SQL
          </button>
          <button type="button" onclick="exportCanvasToSVG()" class="bg-amber-500 hover:bg-amber-400 text-slate-950 px-2.5 py-1.5 rounded-lg text-xs font-semibold transition flex items-center gap-1 shadow-sm" title="Download ER Diagram as standalone SVG vector image">
            <i class="fa-solid fa-bezier-curve"></i> .SVG
          </button>
        </div>
      </div>
    </section>

    <!-- Main 12-Col Studio Grid -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-5">

      <!-- Left 7 Cols: Interactive Builder (ER Canvas + Structured Editor + Relationships/Concepts) -->
      <div id="builderCard" class="lg:col-span-7 bg-white border border-slate-200/90 rounded-2xl shadow-sm overflow-hidden flex flex-col min-h-[700px]">

        <!-- Builder Navigation Tabs -->
        <div class="border-b border-slate-200 bg-slate-50/70 px-4 py-2.5 flex flex-wrap items-center justify-between gap-2">
          <div class="flex items-center gap-1.5">
            <button type="button" id="tabBtnCanvas" onclick="switchBuilderTab('canvas')" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold transition flex items-center gap-2 bg-indigo-600 text-white shadow-sm">
              <i class="fa-solid fa-diagram-project"></i>
              Visual ER Canvas
            </button>
            <button type="button" id="tabBtnTables" onclick="switchBuilderTab('tables')" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold transition flex items-center gap-2 text-slate-600 hover:bg-slate-200/70">
              <i class="fa-solid fa-table-list"></i>
              Tables &amp; Columns
            </button>
            <button type="button" id="tabBtnRels" onclick="switchBuilderTab('rels')" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold transition flex items-center gap-2 text-slate-600 hover:bg-slate-200/70">
              <i class="fa-solid fa-bezier-curve"></i>
              Connections &amp; Glossary
            </button>
          </div>

          <div class="flex items-center gap-2">
            <button type="button" onclick="addNewTable()" class="bg-indigo-50 hover:bg-indigo-100 border border-indigo-200 text-indigo-700 px-3 py-1.5 rounded-lg text-xs font-semibold transition flex items-center gap-1.5">
              <i class="fa-solid fa-plus"></i> Add Table
            </button>
            <button type="button" onclick="openNewRelationshipModal()" class="bg-amber-50 hover:bg-amber-100 border border-amber-200 text-amber-800 px-3 py-1.5 rounded-lg text-xs font-semibold transition flex items-center gap-1.5">
              <i class="fa-solid fa-plug"></i> Connect Tables
            </button>
            <button type="button" id="fullPageTopBtn" onclick="toggleFullPageCanvas()" class="bg-slate-900 hover:bg-slate-800 text-white px-3 py-1.5 rounded-lg text-xs font-semibold transition flex items-center gap-1.5 shadow-sm" title="Toggle Full Page View (Esc to exit)">
              <i id="fullPageTopIcon" class="fa-solid fa-expand text-indigo-400"></i>
              <span id="fullPageTopLabel">Full Page</span>
            </button>
          </div>
        </div>

        <!-- TAB 1: Interactive Visual ER Canvas -->
        <div id="panelCanvas" class="relative flex-1 flex flex-col">
          <!-- Canvas Sub-toolbar + Schema Domain Filter Pills -->
          <div class="bg-slate-900/95 border-b border-slate-800 px-3.5 py-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-300">
            <!-- Schema Filter Pills -->
            <div class="flex items-center gap-1.5 flex-wrap" id="schemaFilterBar">
              <span class="text-slate-400 font-semibold mr-1"><i class="fa-solid fa-filter text-indigo-400 mr-1"></i>Domain:</span>
            </div>

            <div class="flex items-center gap-1.5 flex-wrap">
              <!-- Zoom Controls -->
              <div class="inline-flex items-center bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
                <button type="button" onclick="changeCanvasZoom(-0.15)" class="hover:bg-slate-700 text-slate-200 px-2.5 py-1 text-xs font-bold transition" title="Zoom Out (-)">
                  <i class="fa-solid fa-minus text-[10px]"></i>
                </button>
                <button type="button" id="zoomLevelBadge" onclick="setCanvasZoom(1.0)" class="hover:bg-slate-700 text-indigo-300 font-mono text-[11px] px-2 py-1 border-x border-slate-700 min-w-[50px] text-center" title="Reset Zoom to 100%">
                  100%
                </button>
                <button type="button" onclick="changeCanvasZoom(0.15)" class="hover:bg-slate-700 text-slate-200 px-2.5 py-1 text-xs font-bold transition" title="Zoom In (+)">
                  <i class="fa-solid fa-plus text-[10px]"></i>
                </button>
              </div>

              <button type="button" onclick="fitCanvasToScreen()" class="bg-slate-800 hover:bg-slate-700 text-slate-200 px-2.5 py-1 rounded-lg text-[11px] font-medium border border-slate-700 transition" title="Fit all tables into view">
                <i class="fa-solid fa-crop-simple text-emerald-400 mr-1"></i>Fit All
              </button>
              <button type="button" onclick="autoLayoutCanvas()" class="bg-slate-800 hover:bg-slate-700 text-slate-200 px-2.5 py-1 rounded-lg text-[11px] font-medium border border-slate-700 transition" title="Topological Left-to-Right Hierarchy Layout">
                <i class="fa-solid fa-sitemap text-indigo-400 mr-1"></i>Hierarchy Layout
              </button>
              <button type="button" id="fullPageCanvasBtn" onclick="toggleFullPageCanvas()" class="bg-indigo-600 hover:bg-indigo-500 text-white px-2.5 py-1 rounded-lg text-[11px] font-semibold transition flex items-center gap-1">
                <i id="fullPageCanvasIcon" class="fa-solid fa-maximize"></i>
                <span id="fullPageCanvasLabel">Full Page</span>
              </button>
            </div>
          </div>

          <!-- Helper Hint Bar -->
          <div class="bg-slate-950/90 border-b border-slate-800/80 px-3.5 py-1 text-[10px] text-slate-400 flex items-center justify-between">
            <span><strong class="text-emerald-300">Drag any column row or port (●)</strong> to connect tables • <strong class="text-amber-300">Click any wire, pill badge, or 🔗 icon</strong> to edit relationship • <strong class="text-indigo-300">Double-click</strong> text to rename</span>
            <span class="font-mono text-slate-500 hidden sm:inline">Scroll = Zoom • Drag Grid = Pan</span>
          </div>

          <!-- Interactive Canvas Viewport -->
          <div id="erViewport" class="er-canvas-bg relative flex-1 overflow-auto min-h-[580px] cursor-grab active:cursor-grabbing">
            <div id="erZoomSizer" class="relative" style="width: 2800px; height: 1800px;">
              <div id="erStage" class="relative origin-top-left" style="width: 2800px; height: 1800px;">
                <!-- SVG Layer for Bezier Relationship Wires -->
                <svg id="erSvgLayer" class="absolute inset-0 w-full h-full pointer-events-none" style="z-index: 15;"></svg>
                <!-- HTML Nodes Layer (pointer-events-none on container so clicks pass through to wires!) -->
                <div id="erNodesLayer" class="absolute inset-0 w-full h-full pointer-events-none" style="z-index: 20;"></div>
                <!-- Interactive SVG Layer for Relationship Pill Badges (above table cards so always clickable) -->
                <svg id="erLabelsSvgLayer" class="absolute inset-0 w-full h-full pointer-events-none" style="z-index: 32;"></svg>
                <!-- Top SVG Layer for Active Drag-to-Connect Wire (renders above everything) -->
                <svg id="erDragSvgLayer" class="absolute inset-0 w-full h-full pointer-events-none" style="z-index: 50;"></svg>
              </div>
            </div>
          </div>

          <!-- Bottom-Right Live Minimap Overlay -->
          <div id="erMinimapContainer" class="absolute bottom-3 right-3 z-30 bg-slate-950/90 border border-slate-700/80 rounded-xl p-1.5 shadow-xl backdrop-blur-sm select-none">
            <div class="flex items-center justify-between px-1 mb-1">
              <span class="text-[9px] font-mono uppercase tracking-wider text-slate-400">Minimap</span>
              <span id="minimapCount" class="text-[9px] font-mono text-indigo-400"></span>
            </div>
            <svg id="erMinimapSvg" width="168" height="108" viewBox="0 0 2800 1800" class="bg-slate-900/90 rounded-lg border border-slate-800 cursor-pointer" onclick="onMinimapClick(event)"></svg>
          </div>
        </div>

        <!-- TAB 2: Structured Tables & Columns Editor -->
        <div id="panelTables" class="hidden flex-1 overflow-y-auto p-4 space-y-4 max-h-[680px]">
          <datalist id="sqlTypesList">
            <option value="serial">
            <option value="int">
            <option value="bigint">
            <option value="varchar(255)">
            <option value="varchar(100)">
            <option value="varchar(50)">
            <option value="text">
            <option value="decimal(12,2)">
            <option value="boolean">
            <option value="timestamp">
            <option value="date">
            <option value="jsonb">
            <option value="uuid">
          </datalist>
          <div id="tablesEditorContainer" class="space-y-4"></div>
        </div>

        <!-- TAB 3: Connections (Semantic Relationships), Schemas, Business Concepts & SQL Joins -->
        <div id="panelRels" class="hidden flex-1 overflow-y-auto p-4 space-y-6 max-h-[680px]">

          <!-- Section: Table Connections (Foreign Keys / Semantic Relationships) -->
          <div class="border border-slate-200 rounded-xl p-4 bg-slate-50/50">
            <div class="flex items-center justify-between mb-3">
              <div>
                <h3 class="text-xs font-bold uppercase tracking-wider text-slate-700 flex items-center gap-2">
                  <i class="fa-solid fa-bezier-curve text-indigo-600"></i>
                  Table Connections &amp; Foreign Keys (`semantic_relationships`)
                </h3>
                <p class="text-[11px] text-slate-500">Defines how tables join together in Neo4j Graph-RAG and SQL generation.</p>
              </div>
              <button type="button" onclick="openNewRelationshipModal()" class="bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 rounded-lg text-xs font-semibold transition">
                <i class="fa-solid fa-plus mr-1"></i>Add Connection
              </button>
            </div>
            <div id="relsListContainer" class="space-y-2"></div>
          </div>

          <!-- Section: Schemas / Functional Modules -->
          <div class="border border-slate-200 rounded-xl p-4 bg-slate-50/50">
            <div class="flex items-center justify-between mb-3">
              <div>
                <h3 class="text-xs font-bold uppercase tracking-wider text-slate-700 flex items-center gap-2">
                  <i class="fa-solid fa-layer-group text-emerald-600"></i>
                  Functional Schemas / Modules (`schemas`)
                </h3>
              </div>
              <button type="button" onclick="addSchemaItem()" class="bg-white hover:bg-slate-100 border border-slate-200 text-slate-700 px-2.5 py-1 rounded-lg text-xs font-medium transition">
                <i class="fa-solid fa-plus mr-1"></i>Add Schema
              </button>
            </div>
            <div id="schemasListContainer" class="space-y-2"></div>
          </div>

          <!-- Section: Business Concepts Glossary -->
          <div class="border border-slate-200 rounded-xl p-4 bg-slate-50/50">
            <div class="flex items-center justify-between mb-3">
              <div>
                <h3 class="text-xs font-bold uppercase tracking-wider text-slate-700 flex items-center gap-2">
                  <i class="fa-solid fa-book-open text-amber-600"></i>
                  Business Concepts Glossary (`business_concepts`)
                </h3>
              </div>
              <button type="button" onclick="addConceptItem()" class="bg-white hover:bg-slate-100 border border-slate-200 text-slate-700 px-2.5 py-1 rounded-lg text-xs font-medium transition">
                <i class="fa-solid fa-plus mr-1"></i>Add Concept
              </button>
            </div>
            <div id="conceptsListContainer" class="space-y-2"></div>
          </div>

          <!-- Section: Common SQL Join Paths -->
          <div class="border border-slate-200 rounded-xl p-4 bg-slate-50/50">
            <div class="flex items-center justify-between mb-3">
              <div>
                <h3 class="text-xs font-bold uppercase tracking-wider text-slate-700 flex items-center gap-2">
                  <i class="fa-solid fa-code-merge text-purple-600"></i>
                  Common SQL Join Templates (`common_join_paths`)
                </h3>
              </div>
              <button type="button" onclick="addJoinPathItem()" class="bg-white hover:bg-slate-100 border border-slate-200 text-slate-700 px-2.5 py-1 rounded-lg text-xs font-medium transition">
                <i class="fa-solid fa-plus mr-1"></i>Add SQL Join Path
              </button>
            </div>
            <div id="joinsListContainer" class="space-y-3"></div>
          </div>

        </div>
      </div>

      <!-- Right 5 Cols: Live YAML / Markdown / SQL DDL Output & Validator -->
      <div class="lg:col-span-5 flex flex-col gap-5">

        <!-- Validation Status Card -->
        <div id="validationCard" class="bg-white border border-slate-200/90 rounded-2xl p-4 shadow-sm">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2.5">
              <div id="valBadgeIcon" class="w-8 h-8 rounded-lg bg-emerald-50 border border-emerald-200 text-emerald-600 flex items-center justify-center">
                <i class="fa-solid fa-check"></i>
              </div>
              <div>
                <div class="flex items-center gap-2">
                  <h2 id="valTitle" class="text-xs font-bold uppercase tracking-wider text-slate-800">Ontology Validation</h2>
                  <span id="valPill" class="text-[11px] px-2 py-0.5 rounded-full font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
                    100% Ready for ontology.stewillis.com
                  </span>
                </div>
                <p id="valSubtext" class="text-[11px] text-slate-500 mt-0.5">All primary keys, foreign keys, and schema references are valid.</p>
              </div>
            </div>
            <button type="button" onclick="fixCurrentYamlInEditor()" class="bg-slate-100 hover:bg-slate-200 text-slate-700 px-3 py-1.5 rounded-lg text-xs font-semibold transition flex items-center gap-1.5" title="Validate and auto-repair the current YAML">
              <i class="fa-solid fa-wrench text-indigo-600"></i>
              FixIt &amp; Sync
            </button>
          </div>
          <div id="valMessages" class="hidden mt-3 pt-3 border-t border-slate-100 space-y-1 text-xs"></div>
        </div>

        <!-- Live Output Code & Preview Card -->
        <div class="bg-white border border-slate-200/90 rounded-2xl shadow-sm overflow-hidden flex-1 flex flex-col min-h-[580px]">
          <div class="border-b border-slate-200 bg-slate-50/70 px-3.5 py-2.5 flex flex-wrap items-center justify-between gap-2">
            <div class="flex items-center gap-1 flex-wrap">
              <button type="button" id="outTabYaml" onclick="switchOutputTab('yaml')" class="px-2.5 py-1.5 rounded-lg text-xs font-semibold transition bg-slate-900 text-white">
                <i class="fa-solid fa-file-code text-emerald-400 mr-1"></i>ontology.yaml
              </button>
              <button type="button" id="outTabMd" onclick="switchOutputTab('md')" class="px-2.5 py-1.5 rounded-lg text-xs font-semibold transition text-slate-600 hover:bg-slate-200/70">
                <i class="fa-brands fa-markdown text-indigo-500 mr-1"></i>ontology.md
              </button>
              <button type="button" id="outTabSql" onclick="switchOutputTab('sql')" class="px-2.5 py-1.5 rounded-lg text-xs font-semibold transition text-slate-600 hover:bg-slate-200/70">
                <i class="fa-solid fa-database text-cyan-500 mr-1"></i>schema.sql
              </button>
              <button type="button" id="outTabPreview" onclick="switchOutputTab('preview')" class="px-2.5 py-1.5 rounded-lg text-xs font-semibold transition text-slate-600 hover:bg-slate-200/70">
                <i class="fa-solid fa-eye text-amber-500 mr-1"></i>Rendered Docs
              </button>
            </div>

            <div class="flex items-center gap-1.5">
              <button type="button" onclick="copyCurrentOutput()" class="bg-white hover:bg-slate-100 border border-slate-200 text-slate-700 px-2.5 py-1 rounded-lg text-xs font-medium transition flex items-center gap-1">
                <i class="fa-regular fa-copy"></i> Copy
              </button>
            </div>
          </div>

          <!-- YAML Editable Output Pane -->
          <div id="outPaneYaml" class="flex-1 flex flex-col bg-slate-950">
            <div class="bg-slate-900/90 border-b border-slate-800 px-3.5 py-1.5 flex items-center justify-between text-[11px] text-slate-400">
              <span>Live Ontology YAML (editable — click <strong>FixIt &amp; Sync</strong> to parse edits back into canvas)</span>
              <span class="font-mono text-emerald-400">YAML 1.0</span>
            </div>
            <textarea id="yamlOutputArea" spellcheck="false" class="flex-1 w-full bg-slate-950 text-emerald-300 font-mono text-xs p-4 focus:outline-none resize-none leading-relaxed"></textarea>
          </div>

          <!-- Markdown Raw Output Pane -->
          <div id="outPaneMd" class="hidden flex-1 flex flex-col bg-slate-950">
            <div class="bg-slate-900/90 border-b border-slate-800 px-3.5 py-1.5 flex items-center justify-between text-[11px] text-slate-400">
              <span>Generated Ontology &amp; Schema Guide Markdown (with Mermaid `erDiagram`)</span>
              <span class="font-mono text-indigo-400">Markdown</span>
            </div>
            <textarea id="mdOutputArea" readonly spellcheck="false" class="flex-1 w-full bg-slate-950 text-slate-200 font-mono text-xs p-4 focus:outline-none resize-none leading-relaxed"></textarea>
          </div>

          <!-- SQL DDL Output Pane -->
          <div id="outPaneSql" class="hidden flex-1 flex flex-col bg-slate-950">
            <div class="bg-slate-900/90 border-b border-slate-800 px-3.5 py-1.5 flex items-center justify-between text-[11px] text-slate-400">
              <span>Executable SQL DDL (`CREATE TABLE` + `FOREIGN KEY` constraints)</span>
              <span class="font-mono text-cyan-400">SQL DDL</span>
            </div>
            <textarea id="sqlOutputArea" readonly spellcheck="false" class="flex-1 w-full bg-slate-950 text-cyan-200 font-mono text-xs p-4 focus:outline-none resize-none leading-relaxed"></textarea>
          </div>

          <!-- Rendered Markdown + Mermaid ER Diagram Pane -->
          <div id="outPanePreview" class="hidden flex-1 overflow-y-auto p-5 space-y-4 max-h-[580px] prose prose-slate prose-sm max-w-none">
            <div id="mermaidPreviewBox" class="bg-slate-50 border border-slate-200 rounded-xl p-4 overflow-x-auto"></div>
            <div id="markdownRenderedBox" class="text-xs text-slate-700 space-y-3 leading-relaxed"></div>
          </div>
        </div>

      </div>
    </div>
  </div>

  <!-- ===================================================================== -->
  <!-- MODAL 1: Multi-Format Paste / File Upload / FixIt Dialog              -->
  <!-- Uses native <dialog closedby="any"> per modern-web-guidance           -->
  <!-- ===================================================================== -->
  <dialog id="importDialog" closedby="any" aria-labelledby="importDialogTitle" class="rounded-2xl bg-white border border-slate-200 shadow-2xl p-0 w-full max-w-3xl text-slate-800">
    <div class="p-5 border-b border-slate-200 flex items-center justify-between bg-slate-50">
      <div class="flex items-center gap-2.5">
        <div class="w-9 h-9 rounded-xl bg-indigo-600 text-white flex items-center justify-center">
          <i class="fa-solid fa-file-import"></i>
        </div>
        <div>
          <h2 id="importDialogTitle" class="text-sm font-bold text-slate-900">Import, Paste, or Fix Schema</h2>
          <p class="text-xs text-slate-500">Paste SQL DDL (`CREATE TABLE`), CSV/TXT columns, or broken YAML—or upload a file.</p>
        </div>
      </div>
      <form method="dialog">
        <button type="submit" class="text-slate-400 hover:text-slate-600 p-1.5 rounded-lg" aria-label="Close import dialog">
          <i class="fa-solid fa-xmark text-base"></i>
        </button>
      </form>
    </div>

    <div class="p-5 space-y-4">
      <!-- 1-Click SQL Introspection Query Helper -->
      <div class="bg-indigo-50/70 border border-indigo-200/80 rounded-xl p-3 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
        <div>
          <div class="text-xs font-bold text-indigo-950 flex items-center gap-1.5">
            <i class="fa-solid fa-terminal text-indigo-600"></i>
            Extracting from a live database? Copy an `INFORMATION_SCHEMA` query:
          </div>
          <div class="text-[11px] text-indigo-700">Run this query in DBeaver, MySQL Workbench, pgAdmin, or SSMS, then export/copy the result as CSV and paste below!</div>
        </div>
        <div class="flex items-center gap-1.5 shrink-0">
          <button type="button" onclick="copyIntrospectionQuery('mysql')" class="bg-white hover:bg-indigo-100 border border-indigo-200 text-indigo-800 px-2.5 py-1 rounded-lg text-[11px] font-semibold shadow-sm transition">
            <i class="fa-regular fa-copy mr-1"></i>MySQL Query
          </button>
          <button type="button" onclick="copyIntrospectionQuery('postgres')" class="bg-white hover:bg-indigo-100 border border-indigo-200 text-indigo-800 px-2.5 py-1 rounded-lg text-[11px] font-semibold shadow-sm transition">
            <i class="fa-regular fa-copy mr-1"></i>Postgres Query
          </button>
          <button type="button" onclick="copyIntrospectionQuery('mssql')" class="bg-white hover:bg-indigo-100 border border-indigo-200 text-indigo-800 px-2.5 py-1 rounded-lg text-[11px] font-semibold shadow-sm transition">
            <i class="fa-regular fa-copy mr-1"></i>SQL Server Query
          </button>
        </div>
      </div>

      <!-- Format Selector & Sample Presets -->
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="flex items-center gap-2">
          <label class="text-xs font-semibold text-slate-600">Input Format:</label>
          <select id="importFormatSelect" class="bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 text-xs font-medium text-slate-800">
            <option value="auto">Auto-Detect (SQL DDL, CSV/TXT, or YAML)</option>
            <option value="sql">Raw SQL DDL (CREATE TABLE ...)</option>
            <option value="csv">CSV / TSV / TXT Column List</option>
            <option value="yaml">Broken or Existing YAML / JSON (FixIt Mode)</option>
          </select>
        </div>

        <div class="flex items-center gap-1.5">
          <span class="text-[11px] text-slate-400 font-medium">Load Example:</span>
          <button type="button" onclick="loadSampleInput('sql')" class="bg-slate-100 hover:bg-slate-200 text-slate-700 px-2.5 py-1 rounded text-[11px] font-medium transition">
            SQL DDL Sample
          </button>
          <button type="button" onclick="loadSampleInput('csv')" class="bg-slate-100 hover:bg-slate-200 text-slate-700 px-2.5 py-1 rounded text-[11px] font-medium transition">
            CSV Sample
          </button>
          <button type="button" onclick="loadSampleInput('broken_yaml')" class="bg-amber-50 hover:bg-amber-100 text-amber-800 border border-amber-200 px-2.5 py-1 rounded text-[11px] font-medium transition">
            Broken YAML Sample
          </button>
        </div>
      </div>

      <!-- File Upload Dropzone -->
      <div class="border-2 border-dashed border-slate-200 hover:border-indigo-400 rounded-xl p-3 bg-slate-50/60 flex flex-col sm:flex-row items-center justify-between gap-3 transition">
        <div class="flex items-center gap-3">
          <i class="fa-solid fa-cloud-arrow-up text-indigo-500 text-xl"></i>
          <div>
            <div class="text-xs font-semibold text-slate-700">Upload `.sql`, `.csv`, `.txt`, `.yaml`, or `.json` file</div>
            <div class="text-[11px] text-slate-400">File contents will populate the editor below for review before importing</div>
          </div>
        </div>
        <label class="cursor-pointer bg-white hover:bg-slate-100 border border-slate-200 text-slate-700 px-3 py-1.5 rounded-lg text-xs font-semibold shadow-sm transition shrink-0">
          Choose File...
          <input id="fileUploadInput" type="file" accept=".sql,.csv,.tsv,.txt,.yaml,.yml,.json" class="hidden" onchange="handleFileUpload(event)">
        </label>
      </div>

      <!-- Raw Input Textarea -->
      <div>
        <textarea id="importRawTextarea" autofocus rows="10" spellcheck="false" class="w-full bg-slate-950 text-slate-100 font-mono text-xs p-3.5 rounded-xl border border-slate-800 focus:outline-none focus:border-indigo-500 leading-relaxed" placeholder="Paste your CREATE TABLE statements, CSV/TXT columns (table_name,column_name,data_type), or broken YAML here..."></textarea>
      </div>

      <div class="flex items-center justify-between pt-1">
        <label class="flex items-center gap-2 text-xs text-slate-600 cursor-pointer">
          <input id="importAutoFkCheck" type="checkbox" checked class="rounded text-indigo-600 focus:ring-indigo-500">
          <span>Automatically detect &amp; wire Foreign Keys (`*_id` columns) on import</span>
        </label>

        <div class="flex items-center gap-2">
          <form method="dialog">
            <button type="submit" value="cancel" class="px-4 py-2 rounded-xl text-xs font-semibold text-slate-600 hover:bg-slate-100 transition">
              Cancel
            </button>
          </form>
          <button type="button" onclick="submitImport()" class="bg-indigo-600 hover:bg-indigo-500 text-white px-5 py-2 rounded-xl text-xs font-semibold shadow-sm transition flex items-center gap-2">
            <i class="fa-solid fa-bolt"></i>
            Parse &amp; Build Ontology
          </button>
        </div>
      </div>
    </div>
  </dialog>

  <!-- ===================================================================== -->
  <!-- MODAL 2: Connect Tables / Edit Bidirectional Relationship Dialog       -->
  <!-- ===================================================================== -->
  <dialog id="relDialog" closedby="any" aria-labelledby="relDialogTitle" class="rounded-2xl bg-white border border-slate-200 shadow-2xl p-0 w-full max-w-2xl text-slate-800">
    <div class="p-4 border-b border-slate-200 flex items-center justify-between bg-slate-50">
      <div class="flex items-center gap-2.5">
        <div class="w-9 h-9 rounded-xl bg-amber-500 text-white flex items-center justify-center shadow-sm">
          <i class="fa-solid fa-arrows-left-right"></i>
        </div>
        <div>
          <h2 id="relDialogTitle" class="text-sm font-bold text-slate-900">Edit Bidirectional Relationship (AI &amp; Graph Edge)</h2>
          <p class="text-[11px] text-slate-500">Explain how these tables relate in <strong>both directions</strong> so AI &amp; Graph-RAG understand every query path.</p>
        </div>
      </div>
      <form method="dialog">
        <button type="submit" class="text-slate-400 hover:text-slate-600 p-1 rounded-lg" aria-label="Close relationship dialog">
          <i class="fa-solid fa-xmark text-base"></i>
        </button>
      </form>
    </div>

    <div class="p-5 space-y-4 text-xs">
      <input type="hidden" id="relEditIndex" value="-1">

      <!-- Source & Target Table/Column Pickers + Swap Button -->
      <div class="bg-slate-50 border border-slate-200/90 rounded-xl p-3.5 space-y-3">
        <div class="flex items-center justify-between">
          <span class="text-[11px] font-bold uppercase tracking-wider text-slate-500">1. Linked Columns &amp; Cardinality</span>
          <button type="button" onclick="swapRelDirectionInModal()" class="bg-white hover:bg-indigo-50 border border-slate-200 text-indigo-700 px-2.5 py-1 rounded-lg text-[11px] font-semibold transition flex items-center gap-1.5 shadow-sm" title="Swap Source and Target tables">
            <i class="fa-solid fa-right-left text-indigo-500"></i>
            Swap Direction
          </button>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div class="bg-white border border-indigo-200/80 rounded-lg p-2.5 space-y-2">
            <div class="text-[10px] font-bold uppercase tracking-wider text-indigo-600 flex items-center gap-1">
              <i class="fa-solid fa-circle-dot"></i> Source Table (Child / FK Side)
            </div>
            <select id="relSourceTable" onchange="populateRelColumns('source'); suggestRelName(false);" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 font-mono text-xs font-semibold"></select>
            <select id="relSourceCol" onchange="suggestRelName(false)" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 font-mono text-xs"></select>
          </div>

          <div class="bg-white border border-emerald-200/80 rounded-lg p-2.5 space-y-2">
            <div class="text-[10px] font-bold uppercase tracking-wider text-emerald-600 flex items-center gap-1">
              <i class="fa-solid fa-bullseye"></i> Target Table (Parent / PK Side)
            </div>
            <select id="relTargetTable" onchange="populateRelColumns('target'); suggestRelName(false);" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 font-mono text-xs font-semibold"></select>
            <select id="relTargetCol" onchange="suggestRelName(false)" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 font-mono text-xs"></select>
          </div>
        </div>

        <div class="flex flex-wrap items-center justify-between gap-2 pt-1">
          <div class="flex items-center gap-2">
            <label class="text-[11px] font-semibold text-slate-600">Cardinality (`Source → Target`):</label>
            <select id="relCardinality" onchange="updateBidirectionalLabels()" class="bg-white border border-slate-200 rounded-lg px-2.5 py-1.5 font-mono text-xs font-semibold text-slate-800">
              <option value="N:1">N:1 (Many Source → One Target)</option>
              <option value="1:N">1:N (One Source → Many Target)</option>
              <option value="1:1">1:1 (One-to-One)</option>
            </select>
          </div>
          <span id="relInverseCardBadge" class="text-[11px] font-mono bg-emerald-50 text-emerald-700 border border-emerald-200 px-2.5 py-1 rounded-lg">
            Reverse (`Target → Source`): 1:N
          </span>
        </div>
      </div>

      <!-- Bidirectional AI Explanations (Both Ways) -->
      <div class="space-y-3">
        <div class="flex items-center justify-between">
          <span class="text-[11px] font-bold uppercase tracking-wider text-slate-500">2. Explain Relationship Both Ways for AI Reasoning</span>
          <button type="button" onclick="suggestRelName(true)" class="bg-indigo-50 hover:bg-indigo-100 border border-indigo-200 text-indigo-700 px-2.5 py-1 rounded-lg text-[11px] font-semibold transition flex items-center gap-1.5">
            <i class="fa-solid fa-wand-magic-sparkles"></i>
            Auto-Suggest Both Ways
          </button>
        </div>

        <!-- Direction 1: Forward (Source -> Target) -->
        <div class="bg-indigo-50/40 border border-indigo-200/90 rounded-xl p-3.5 space-y-2.5">
          <div class="flex items-center justify-between">
            <span class="text-[11px] font-bold text-indigo-950 flex items-center gap-1.5">
              <span class="bg-indigo-600 text-white text-[10px] font-mono px-2 py-0.5 rounded">A → B</span>
              <span id="relFwdHeaderLabel">Forward Direction (Source → Target)</span>
            </span>
            <span id="relFwdCardPill" class="font-mono text-[10px] text-indigo-700 bg-indigo-100 px-2 py-0.5 rounded">N:1</span>
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
            <div>
              <label class="block text-[10px] font-semibold uppercase text-indigo-900/70 mb-1">Forward Edge Name</label>
              <input id="relNameInput" type="text" autofocus class="w-full bg-white border border-indigo-200 rounded-lg px-2.5 py-1.5 font-mono text-xs font-bold text-indigo-900 uppercase" placeholder="PLACED_BY_CUSTOMER">
            </div>
            <div class="sm:col-span-2">
              <label class="block text-[10px] font-semibold uppercase text-indigo-900/70 mb-1">How does Source relate to Target? (AI Context)</label>
              <input id="relDescInput" type="text" class="w-full bg-white border border-indigo-200 rounded-lg px-2.5 py-1.5 text-xs text-slate-800" placeholder="e.g. Each order is placed by one registered customer">
            </div>
          </div>
        </div>

        <!-- Direction 2: Reverse / Inverse (Target -> Source) -->
        <div class="bg-emerald-50/40 border border-emerald-200/90 rounded-xl p-3.5 space-y-2.5">
          <div class="flex items-center justify-between">
            <span class="text-[11px] font-bold text-emerald-950 flex items-center gap-1.5">
              <span class="bg-emerald-600 text-white text-[10px] font-mono px-2 py-0.5 rounded">B → A</span>
              <span id="relRevHeaderLabel">Reverse Direction (Target → Source)</span>
            </span>
            <span id="relRevCardPill" class="font-mono text-[10px] text-emerald-700 bg-emerald-100 px-2 py-0.5 rounded">1:N</span>
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
            <div>
              <label class="block text-[10px] font-semibold uppercase text-emerald-900/70 mb-1">Reverse Edge Name</label>
              <input id="relInverseNameInput" type="text" class="w-full bg-white border border-emerald-200 rounded-lg px-2.5 py-1.5 font-mono text-xs font-bold text-emerald-900 uppercase" placeholder="PLACES_ORDERS">
            </div>
            <div class="sm:col-span-2">
              <label class="block text-[10px] font-semibold uppercase text-emerald-900/70 mb-1">How does Target relate back to Source? (AI Context)</label>
              <input id="relReverseDescInput" type="text" class="w-full bg-white border border-emerald-200 rounded-lg px-2.5 py-1.5 text-xs text-slate-800" placeholder="e.g. A customer can place zero or many orders over time">
            </div>
          </div>
        </div>
      </div>

      <div class="flex items-center justify-between pt-3 border-t border-slate-100">
        <button type="button" id="relDeleteBtn" onclick="deleteRelFromModal()" class="hidden text-rose-600 hover:bg-rose-50 px-3 py-1.5 rounded-lg text-xs font-semibold transition">
          <i class="fa-solid fa-trash-can mr-1"></i>Delete Connection
        </button>
        <div class="flex items-center gap-2 ml-auto">
          <form method="dialog">
            <button type="submit" value="cancel" class="px-3.5 py-2 rounded-xl text-xs font-semibold text-slate-600 hover:bg-slate-100">Cancel</button>
          </form>
          <button type="button" onclick="saveRelationshipModal()" class="bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-xl text-xs font-semibold shadow-sm flex items-center gap-1.5">
            <i class="fa-solid fa-check"></i>
            Save Bidirectional Connection
          </button>
        </div>
      </div>
    </div>
  </dialog>

  <script>
    // Initialize Mermaid for Markdown preview
    mermaid.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'loose' });

    // Fallback for browsers without native <dialog closedby="any"> support (per modern-web-guidance)
    if (!('closedBy' in HTMLDialogElement.prototype)) {
      const outside = (e, dialog) => {
        const r = dialog.getBoundingClientRect();
        return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
      };
      for (const dialog of document.querySelectorAll('dialog[closedby="any"]')) {
        let pressedOutside = false;
        dialog.addEventListener('pointerdown', (e) => { pressedOutside = outside(e, dialog); });
        dialog.addEventListener('pointerup', (e) => { if (pressedOutside && outside(e, dialog)) dialog.close(); });
      }
    }
    for (const dialog of document.querySelectorAll('dialog')) {
      dialog.addEventListener('close', () => { dialog.returnValue = ''; });
    }

    // Canonical Ontology State
    let ontology = __INITIAL_ONTOLOGY__;
    let nodePositions = {}; // { "schema.table": { x, y } }
    let activeBuilderTab = 'canvas';
    let activeOutputTab = 'yaml';
    let activeSchemaFilter = 'ALL';
    let hoveredTableKey = null;
    let canvasZoom = 1.0;
    let isFullPageCanvas = false;
    const STAGE_BASE_W = 2800;
    const STAGE_BASE_H = 1800;
    const STORAGE_KEY = 'yamlfixit_autosave_v4';

    // Dragging state for ER nodes, wire creation, and canvas background panning
    let dragNodeState = null; // { fullKey, startX, startY, origX, origY }
    let wireDragState = null; // { srcTable, srcCol, startX, startY, tempLine }
    let panCanvasState = null; // { startX, startY, scrollLeft, scrollTop }

    const SCHEMA_COLORS = [
      { header: 'bg-indigo-950 border-indigo-500/40', badge: 'bg-indigo-500/20 text-indigo-300 border-indigo-400/30', wire: '#818cf8', hex: '#4f46e5' },
      { header: 'bg-emerald-950 border-emerald-500/40', badge: 'bg-emerald-500/20 text-emerald-300 border-emerald-400/30', wire: '#34d399', hex: '#059669' },
      { header: 'bg-amber-950 border-amber-500/40', badge: 'bg-amber-500/20 text-amber-300 border-amber-400/30', wire: '#fbbf24', hex: '#d97706' },
      { header: 'bg-purple-950 border-purple-500/40', badge: 'bg-purple-500/20 text-purple-300 border-purple-400/30', wire: '#c084fc', hex: '#9333ea' },
      { header: 'bg-cyan-950 border-cyan-500/40', badge: 'bg-cyan-500/20 text-cyan-300 border-cyan-400/30', wire: '#22d3ee', hex: '#0891b2' }
    ];

    function getSchemaStyle(schemaName) {
      const schemas = (ontology.schemas || []).map(s => s.name);
      let idx = schemas.indexOf(schemaName);
      if (idx === -1) idx = 0;
      return SCHEMA_COLORS[idx % SCHEMA_COLORS.length];
    }

    function showToast(msg) {
      const banner = document.getElementById('toastBanner');
      document.getElementById('toastMessage').textContent = msg;
      banner.classList.remove('hidden');
    }

    function saveToLocalStorage() {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({ ontology, nodePositions }));
      } catch (e) {}
    }

    // =========================================================================
    // SMART HIERARCHICAL TOPOLOGICAL LAYOUT (Left-to-Right Parent -> Child)
    // =========================================================================
    function computeTopologicalPositions() {
      const tables = ontology.tables || [];
      const rels = ontology.semantic_relationships || [];
      const fullKeys = tables.map(t => `${t.schema || 'public'}.${t.name}`);

      // Compute depth: if A references B (A -> B), B is parent (left, lower depth) and A is child (right, higher depth)
      const depth = {};
      fullKeys.forEach(k => { depth[k] = 0; });

      for (let iter = 0; iter < tables.length + 2; iter++) {
        let changed = false;
        rels.forEach(r => {
          const child = r.source_table;
          const parent = r.target_table;
          if (child && parent && child !== parent && depth[parent] !== undefined && depth[child] !== undefined) {
            if (depth[child] <= depth[parent]) {
              depth[child] = Math.min(6, depth[parent] + 1);
              changed = true;
            }
          }
        });
        if (!changed) break;
      }

      // Group tables by topological column layer, then sort by schema
      const layers = {};
      tables.forEach(t => {
        const k = `${t.schema || 'public'}.${t.name}`;
        const d = depth[k] || 0;
        if (!layers[d]) layers[d] = [];
        layers[d].push(t);
      });

      const layerKeys = Object.keys(layers).map(Number).sort((a, b) => a - b);
      // If all tables landed in layer 0 (no relationships), fall back to balanced grid
      if (layerKeys.length <= 1 && tables.length > 3) {
        const colsPerRow = Math.max(2, Math.ceil(Math.sqrt(tables.length)));
        tables.forEach((t, idx) => {
          const key = `${t.schema || 'public'}.${t.name}`;
          const col = idx % colsPerRow;
          const row = Math.floor(idx / colsPerRow);
          nodePositions[key] = { x: 52 + col * 350, y: 48 + row * 290 };
        });
        return;
      }

      let visualColIdx = 0;
      const maxRowsPerCol = tables.length > 8 ? 3 : 2;
      layerKeys.forEach((lKey) => {
        const group = layers[lKey];
        group.sort((a, b) => (a.schema || '').localeCompare(b.schema || '') || a.name.localeCompare(b.name));
        let curY = 44;
        let rowsInCol = 0;
        group.forEach((t, idx) => {
          if (rowsInCol >= maxRowsPerCol) {
            visualColIdx += 1;
            curY = 44;
            rowsInCol = 0;
          }
          const key = `${t.schema || 'public'}.${t.name}`;
          const colCount = (t.columns || []).length;
          const estHeight = 58 + colCount * 28 + 28;
          nodePositions[key] = {
            x: 48 + visualColIdx * 345,
            y: curY
          };
          curY += estHeight;
          rowsInCol += 1;
        });
        visualColIdx += 1;
      });
    }

    function ensureNodePositions(forceReset = false) {
      const tables = ontology.tables || [];
      if (forceReset) {
        computeTopologicalPositions();
        saveToLocalStorage();
        return;
      }
      const missing = tables.some(t => !nodePositions[`${t.schema || 'public'}.${t.name}`]);
      if (missing) {
        computeTopologicalPositions();
      }
    }

    function autoLayoutCanvas() {
      ensureNodePositions(true);
      renderERCanvas();
      fitCanvasToScreen();
      showToast('Arranged tables in Left-to-Right Parent → Child hierarchy.');
    }

    function setCanvasZoom(newZoom) {
      canvasZoom = Math.min(2.2, Math.max(0.3, Math.round(newZoom * 100) / 100));
      const stage = document.getElementById('erStage');
      const sizer = document.getElementById('erZoomSizer');
      const badge = document.getElementById('zoomLevelBadge');
      if (stage) stage.style.transform = `scale(${canvasZoom})`;
      if (sizer) {
        sizer.style.width = `${Math.round(STAGE_BASE_W * canvasZoom)}px`;
        sizer.style.height = `${Math.round(STAGE_BASE_H * canvasZoom)}px`;
      }
      if (badge) badge.textContent = `${Math.round(canvasZoom * 100)}%`;
      requestAnimationFrame(() => {
        drawERWires();
        renderMinimap();
      });
    }

    function changeCanvasZoom(delta) {
      setCanvasZoom(canvasZoom + delta);
    }

    function resetCanvasZoom() {
      setCanvasZoom(1.0);
      const vp = document.getElementById('erViewport');
      if (vp) vp.scrollTo({ top: 0, left: 0, behavior: 'smooth' });
    }

    function fitCanvasToScreen() {
      const vp = document.getElementById('erViewport');
      const cards = document.querySelectorAll('#erNodesLayer .er-node');
      if (!vp || !cards.length) {
        resetCanvasZoom();
        return;
      }
      let maxRight = 400;
      let maxBottom = 300;
      cards.forEach(c => {
        const x = parseFloat(c.style.left || '0') + c.offsetWidth + 70;
        const y = parseFloat(c.style.top || '0') + c.offsetHeight + 70;
        if (x > maxRight) maxRight = x;
        if (y > maxBottom) maxBottom = y;
      });
      const scaleX = vp.clientWidth / maxRight;
      const scaleY = vp.clientHeight / maxBottom;
      const idealZoom = Math.min(1.2, Math.max(0.35, Math.min(scaleX, scaleY) * 0.94));
      setCanvasZoom(idealZoom);
      vp.scrollTo({ top: 0, left: 0, behavior: 'smooth' });
    }

    function toggleFullPageCanvas() {
      isFullPageCanvas = !isFullPageCanvas;
      const card = document.getElementById('builderCard');
      const topIcon = document.getElementById('fullPageTopIcon');
      const topLabel = document.getElementById('fullPageTopLabel');
      const canvasIcon = document.getElementById('fullPageCanvasIcon');
      const canvasLabel = document.getElementById('fullPageCanvasLabel');
      const panelTables = document.getElementById('panelTables');
      const panelRels = document.getElementById('panelRels');

      if (isFullPageCanvas) {
        card.classList.add('builder-fullpage');
        if (topIcon) topIcon.className = 'fa-solid fa-compress text-amber-400';
        if (topLabel) topLabel.textContent = 'Exit Full Page';
        if (canvasIcon) canvasIcon.className = 'fa-solid fa-minimize';
        if (canvasLabel) canvasLabel.textContent = 'Exit Full Page (Esc)';
        if (panelTables) panelTables.classList.remove('max-h-[680px]');
        if (panelRels) panelRels.classList.remove('max-h-[680px]');
      } else {
        card.classList.remove('builder-fullpage');
        if (topIcon) topIcon.className = 'fa-solid fa-expand text-indigo-400';
        if (topLabel) topLabel.textContent = 'Full Page';
        if (canvasIcon) canvasIcon.className = 'fa-solid fa-maximize';
        if (canvasLabel) canvasLabel.textContent = 'Full Page';
        if (panelTables) panelTables.classList.add('max-h-[680px]');
        if (panelRels) panelRels.classList.add('max-h-[680px]');
      }
      requestAnimationFrame(() => {
        drawERWires();
        renderMinimap();
      });
    }

    // Sync top bar metadata inputs
    function syncTopInputs() {
      const db = ontology.database || {};
      document.getElementById('dbNameInput').value = db.name || 'MyDatabase';
      document.getElementById('dbRdbmsInput').value = db.rdbms_type || 'PostgreSQL';
      document.getElementById('dbDescInput').value = db.description || '';
      const tCount = (ontology.tables || []).length;
      const rCount = (ontology.semantic_relationships || []).length;
      document.getElementById('statsSummary').textContent = `${tCount} Tables • ${rCount} Links`;
    }

    function updateDbMeta() {
      ontology.database = ontology.database || {};
      ontology.database.name = document.getElementById('dbNameInput').value.trim() || 'MyDatabase';
      ontology.database.rdbms_type = document.getElementById('dbRdbmsInput').value;
      ontology.database.description = document.getElementById('dbDescInput').value.trim();
      refreshOutputsFromBackend();
    }

    // Switch Left Builder Tabs
    function switchBuilderTab(tab) {
      activeBuilderTab = tab;
      const tabs = ['canvas', 'tables', 'rels'];
      const btnIds = { canvas: 'tabBtnCanvas', tables: 'tabBtnTables', rels: 'tabBtnRels' };
      const panelIds = { canvas: 'panelCanvas', tables: 'panelTables', rels: 'panelRels' };

      tabs.forEach(t => {
        const btn = document.getElementById(btnIds[t]);
        const panel = document.getElementById(panelIds[t]);
        if (t === tab) {
          btn.className = 'px-3.5 py-1.5 rounded-lg text-xs font-semibold transition flex items-center gap-2 bg-indigo-600 text-white shadow-sm';
          panel.classList.remove('hidden');
        } else {
          btn.className = 'px-3.5 py-1.5 rounded-lg text-xs font-semibold transition flex items-center gap-2 text-slate-600 hover:bg-slate-200/70';
          panel.classList.add('hidden');
        }
      });

      if (tab === 'canvas') renderERCanvas();
      if (tab === 'tables') renderTablesEditor();
      if (tab === 'rels') renderRelsAndGlossary();
    }

    // Switch Right Output Tabs
    function switchOutputTab(tab) {
      activeOutputTab = tab;
      const tabs = ['yaml', 'md', 'sql', 'preview'];
      const btnIds = { yaml: 'outTabYaml', md: 'outTabMd', sql: 'outTabSql', preview: 'outTabPreview' };
      const paneIds = { yaml: 'outPaneYaml', md: 'outPaneMd', sql: 'outPaneSql', preview: 'outPanePreview' };

      tabs.forEach(t => {
        const btn = document.getElementById(btnIds[t]);
        const pane = document.getElementById(paneIds[t]);
        if (t === tab) {
          btn.className = 'px-2.5 py-1.5 rounded-lg text-xs font-semibold transition bg-slate-900 text-white';
          pane.classList.remove('hidden');
        } else {
          btn.className = 'px-2.5 py-1.5 rounded-lg text-xs font-semibold transition text-slate-600 hover:bg-slate-200/70';
          pane.classList.add('hidden');
        }
      });

      if (tab === 'preview') renderMarkdownPreview();
    }

    // =========================================================================
    // 1. RENDER INTERACTIVE ER CANVAS + SCHEMA FILTER PILLS + INLINE EDITING
    // =========================================================================
    function renderSchemaFilterBar() {
      const bar = document.getElementById('schemaFilterBar');
      if (!bar) return;
      const tables = ontology.tables || [];
      const counts = {};
      tables.forEach(t => {
        const s = t.schema || 'public';
        counts[s] = (counts[s] || 0) + 1;
      });
      const schemaNames = Object.keys(counts);

      let html = `<span class="text-slate-400 font-semibold mr-1"><i class="fa-solid fa-filter text-indigo-400 mr-1"></i>Domain:</span>`;
      const allActive = activeSchemaFilter === 'ALL';
      html += `<button type="button" onclick="setSchemaFilter('ALL')" class="px-2 py-0.5 rounded-md text-[10px] font-mono font-semibold transition ${allActive ? 'bg-indigo-600 text-white' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}">ALL (${tables.length})</button>`;

      schemaNames.forEach(s => {
        const isAct = activeSchemaFilter === s;
        html += `<button type="button" onclick="setSchemaFilter('${escapeAttr(s)}')" class="px-2 py-0.5 rounded-md text-[10px] font-mono font-semibold transition ${isAct ? 'bg-indigo-600 text-white' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}">${escapeHtml(s)} (${counts[s]})</button>`;
      });
      bar.innerHTML = html;
    }

    function setSchemaFilter(schema) {
      activeSchemaFilter = schema;
      renderERCanvas();
    }

    function renderERCanvas() {
      ensureNodePositions(false);
      renderSchemaFilterBar();
      const nodesLayer = document.getElementById('erNodesLayer');
      nodesLayer.innerHTML = '';

      const tables = ontology.tables || [];
      tables.forEach((t, tIdx) => {
        const schema = t.schema || 'public';
        const fullKey = `${schema}.${t.name}`;
        const pos = nodePositions[fullKey] || { x: 60, y: 60 };
        const style = getSchemaStyle(schema);
        const filteredOut = activeSchemaFilter !== 'ALL' && activeSchemaFilter !== schema;

        const card = document.createElement('div');
        card.className = `er-node pointer-events-auto absolute w-[290px] rounded-xl bg-slate-900/95 border border-slate-700/80 text-slate-100 overflow-visible ${filteredOut ? 'node-dimmed' : ''}`;
        card.style.left = `${pos.x}px`;
        card.style.top = `${pos.y}px`;
        card.dataset.fullTable = fullKey;
        card.dataset.schema = schema;

        // Hover Blast-Radius Highlighting
        card.addEventListener('pointerenter', () => setHoverHighlight(fullKey));
        card.addEventListener('pointerleave', () => setHoverHighlight(null));

        // Card Header (Draggable handle + double-click inline rename)
        const header = document.createElement('div');
        header.className = `px-3.5 py-2.5 rounded-t-xl border-b ${style.header} flex items-center justify-between cursor-grab active:cursor-grabbing`;
        header.innerHTML = `
          <div class="flex items-center gap-2 truncate flex-1 mr-1">
            <span ondblclick="inlineEditTable(${tIdx}, 'schema', this)" class="text-[10px] font-mono px-1.5 py-0.5 rounded border cursor-text ${style.badge}" title="Double-click to rename schema">${escapeHtml(schema)}</span>
            <span ondblclick="inlineEditTable(${tIdx}, 'name', this)" class="font-bold text-xs tracking-wide text-white truncate cursor-text" title="Double-click to rename table">${escapeHtml(t.name)}</span>
          </div>
          <div class="flex items-center gap-1 shrink-0">
            <button type="button" onclick="quickAddColumn(${tIdx})" class="text-slate-400 hover:text-emerald-400 px-1.5 py-0.5 rounded text-[10px]" title="Add Column">
              <i class="fa-solid fa-plus"></i>
            </button>
            <button type="button" onclick="deleteTableFromCanvas(${tIdx})" class="text-slate-400 hover:text-rose-400 px-1.5 py-0.5 rounded text-[10px]" title="Delete Table">
              <i class="fa-solid fa-trash-can"></i>
            </button>
          </div>
        `;
        header.addEventListener('pointerdown', (e) => startNodeDrag(e, fullKey));
        card.appendChild(header);

        // Columns List
        const colList = document.createElement('div');
        colList.className = 'divide-y divide-slate-800/80 py-1';

        (t.columns || []).forEach((c, cIdx) => {
          const row = document.createElement('div');
          row.className = 'relative px-3.5 py-1.5 flex items-center justify-between text-xs hover:bg-slate-800/75 group cursor-crosshair select-none';
          row.dataset.table = fullKey;
          row.dataset.column = c.name;
          row.title = `Drag to another table to connect ${fullKey}.${c.name} (or double-click text to edit)`;

          // Allow dragging directly from any column row (except action buttons or active inline inputs)
          row.addEventListener('pointerdown', (e) => {
            if (e.target.closest('.col-action-btn') || e.target.closest('input')) return;
            startWireDrag(e, fullKey, c.name);
          });

          // Left input port
          const leftPort = document.createElement('div');
          leftPort.className = 'port-dot absolute -left-[7px] top-1/2 -translate-y-1/2';
          leftPort.title = `Drag to connect ${fullKey}.${c.name}`;
          leftPort.dataset.portTable = fullKey;
          leftPort.dataset.portCol = c.name;
          leftPort.addEventListener('pointerdown', (e) => startWireDrag(e, fullKey, c.name));

          // Right output port
          const rightPort = document.createElement('div');
          rightPort.className = 'port-dot absolute -right-[7px] top-1/2 -translate-y-1/2';
          rightPort.title = `Drag to connect ${fullKey}.${c.name}`;
          rightPort.dataset.portTable = fullKey;
          rightPort.dataset.portCol = c.name;
          rightPort.addEventListener('pointerdown', (e) => startWireDrag(e, fullKey, c.name));

          // Check if this column has a relationship in ontology.semantic_relationships
          const relIdx = (ontology.semantic_relationships || []).findIndex(
            r => r.source_table === fullKey && r.source_column === c.name
          );
          const relObj = relIdx >= 0 ? ontology.semantic_relationships[relIdx] : null;

          const iconHtml = c.pk
            ? `<button type="button" onclick="toggleColPkFromCanvas(${tIdx}, ${cIdx})" class="col-action-btn text-amber-400 hover:text-amber-300 w-4 text-left" title="Primary Key (click to toggle)"><i class="fa-solid fa-key text-[10px]"></i></button>`
            : (relIdx >= 0
                ? `<button type="button" onclick="openEditRelationshipModal(${relIdx})" class="col-action-btn text-indigo-400 hover:text-amber-300 w-4 text-left" title="Click to edit relationship (${escapeAttr(relObj.name || 'FK')})"><i class="fa-solid fa-link text-[10px]"></i></button>`
                : `<button type="button" onclick="toggleColPkFromCanvas(${tIdx}, ${cIdx})" class="col-action-btn text-slate-600 hover:text-amber-400 w-4 text-left text-[10px]" title="Click to mark as Primary Key">•</button>`);

          const fkBadgeHtml = relIdx >= 0
            ? `<button type="button" onclick="openEditRelationshipModal(${relIdx})" class="col-action-btn bg-indigo-950/90 hover:bg-indigo-700 text-indigo-200 border border-indigo-500/50 rounded px-1.5 py-0.2 text-[9px] font-mono transition flex items-center gap-1" title="Click to edit relationship: ${escapeAttr(relObj.name)}">
                 <span>→ ${escapeHtml((relObj.target_table || '').split('.').pop())}</span>
                 <i class="fa-solid fa-pen text-[8px] text-amber-300"></i>
               </button>`
            : '';

          row.innerHTML = `
            <div class="flex items-center gap-1.5 truncate pr-2 flex-1">
              ${iconHtml}
              <span ondblclick="inlineEditColumn(${tIdx}, ${cIdx}, 'name', this)" class="font-mono text-[11px] ${c.pk ? 'font-semibold text-amber-200' : 'text-slate-200'} truncate cursor-text" title="Double-click to rename column (or drag to connect)">${escapeHtml(c.name)}</span>
            </div>
            <div class="flex items-center gap-1.5 shrink-0">
              ${fkBadgeHtml}
              <span ondblclick="inlineEditColumn(${tIdx}, ${cIdx}, 'type', this)" class="font-mono text-[10px] text-slate-400 hover:text-indigo-300 cursor-text" title="Double-click to change data type">${escapeHtml(c.type || 'varchar')}</span>
              <span class="text-indigo-400/60 group-hover:text-amber-300 text-[10px] px-0.5 transition" title="Drag to wire relationship">
                <i class="fa-solid fa-plug-circle-plus"></i>
              </span>
              <button type="button" onclick="removeColumnFromCanvas(${tIdx}, ${cIdx})" class="col-action-btn opacity-0 group-hover:opacity-100 text-slate-500 hover:text-rose-400 text-[10px] transition" title="Remove column">
                <i class="fa-solid fa-xmark"></i>
              </button>
            </div>
          `;

          row.appendChild(leftPort);
          row.appendChild(rightPort);
          colList.appendChild(row);
        });

        card.appendChild(colList);
        nodesLayer.appendChild(card);
      });

      requestAnimationFrame(() => {
        drawERWires();
        renderMinimap();
      });
    }

    // Inline Double-Click Editing on ER Canvas Cards
    function inlineEditTable(tIdx, field, spanEl) {
      const t = ontology.tables[tIdx];
      if (!t) return;
      const oldVal = t[field] || '';
      const input = document.createElement('input');
      input.type = 'text';
      input.value = oldVal;
      input.className = 'bg-slate-950 text-white font-mono text-xs px-1.5 py-0.5 rounded border border-indigo-400 focus:outline-none w-28';
      spanEl.replaceWith(input);
      input.focus();
      input.select();

      const commit = () => {
        const nextVal = input.value.trim() || oldVal;
        updateTableField(tIdx, field, nextVal);
        renderERCanvas();
      };
      input.addEventListener('blur', commit);
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') input.blur();
        if (e.key === 'Escape') { input.value = oldVal; input.blur(); }
      });
    }

    function inlineEditColumn(tIdx, cIdx, field, spanEl) {
      const col = ontology.tables[tIdx]?.columns[cIdx];
      if (!col) return;
      const oldVal = col[field] || '';
      const input = document.createElement('input');
      input.type = 'text';
      input.value = oldVal;
      if (field === 'type') input.setAttribute('list', 'sqlTypesList');
      input.className = 'bg-slate-950 text-amber-200 font-mono text-[11px] px-1.5 py-0.5 rounded border border-indigo-400 focus:outline-none w-24';
      spanEl.replaceWith(input);
      input.focus();
      input.select();

      const commit = () => {
        const nextVal = input.value.trim() || oldVal;
        const oldName = col.name;
        col[field] = nextVal;
        if (field === 'name' && nextVal !== oldName) {
          const fullT = `${ontology.tables[tIdx].schema || 'public'}.${ontology.tables[tIdx].name}`;
          (ontology.semantic_relationships || []).forEach(r => {
            if (r.source_table === fullT && r.source_column === oldName) r.source_column = nextVal;
            if (r.target_table === fullT && r.target_column === oldName) r.target_column = nextVal;
          });
        }
        renderERCanvas();
        refreshOutputsFromBackend();
      };
      input.addEventListener('blur', commit);
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') input.blur();
        if (e.key === 'Escape') { input.value = oldVal; input.blur(); }
      });
    }

    function toggleColPkFromCanvas(tIdx, cIdx) {
      const col = ontology.tables[tIdx]?.columns[cIdx];
      if (!col) return;
      col.pk = !col.pk;
      renderERCanvas();
      refreshOutputsFromBackend();
    }

    function removeColumnFromCanvas(tIdx, cIdx) {
      ontology.tables[tIdx].columns.splice(cIdx, 1);
      renderERCanvas();
      refreshOutputsFromBackend();
    }

    function deleteTableFromCanvas(tIdx) {
      deleteTable(tIdx);
      renderERCanvas();
    }

    // Hover Blast-Radius Highlighting
    function setHoverHighlight(fullTableKey) {
      hoveredTableKey = fullTableKey;
      const cards = document.querySelectorAll('#erNodesLayer .er-node');
      const wires = document.querySelectorAll('#erSvgLayer .wire-path');
      if (!fullTableKey) {
        cards.forEach(c => {
          c.classList.remove('node-highlighted');
          const filteredOut = activeSchemaFilter !== 'ALL' && activeSchemaFilter !== c.dataset.schema;
          c.classList.toggle('node-dimmed', filteredOut);
        });
        wires.forEach(w => {
          w.classList.remove('wire-highlighted', 'wire-dimmed');
        });
        return;
      }

      const connectedTables = new Set([fullTableKey]);
      (ontology.semantic_relationships || []).forEach(r => {
        if (r.source_table === fullTableKey) connectedTables.add(r.target_table);
        if (r.target_table === fullTableKey) connectedTables.add(r.source_table);
      });

      cards.forEach(c => {
        const key = c.dataset.fullTable;
        if (connectedTables.has(key)) {
          c.classList.add('node-highlighted');
          c.classList.remove('node-dimmed');
        } else {
          c.classList.remove('node-highlighted');
          c.classList.add('node-dimmed');
        }
      });

      wires.forEach(w => {
        const src = w.dataset.srcTable;
        const tgt = w.dataset.tgtTable;
        if (src === fullTableKey || tgt === fullTableKey) {
          w.classList.add('wire-highlighted');
          w.classList.remove('wire-dimmed');
        } else {
          w.classList.remove('wire-highlighted');
          w.classList.add('wire-dimmed');
        }
      });
    }

    function drawERWires() {
      const svg = document.getElementById('erSvgLayer');
      const labelsSvg = document.getElementById('erLabelsSvgLayer') || svg;
      const stage = document.getElementById('erStage');
      if (!svg || !stage) return;
      svg.innerHTML = '';
      if (labelsSvg !== svg) labelsSvg.innerHTML = '';

      const stageRect = stage.getBoundingClientRect();
      const rels = ontology.semantic_relationships || [];

      rels.forEach((r, idx) => {
        const srcRow = stage.querySelector(`[data-table="${CSS.escape(r.source_table)}"][data-column="${CSS.escape(r.source_column)}"]`);
        const tgtRow = stage.querySelector(`[data-table="${CSS.escape(r.target_table)}"][data-column="${CSS.escape(r.target_column)}"]`);
        if (!srcRow || !tgtRow) return;

        const srcRect = srcRow.getBoundingClientRect();
        const tgtRect = tgtRow.getBoundingClientRect();

        const srcCenterX = srcRect.left + srcRect.width / 2;
        const tgtCenterX = tgtRect.left + tgtRect.width / 2;
        const isSelfRef = r.source_table === r.target_table;

        const y1 = (srcRect.top + srcRect.height / 2 - stageRect.top) / canvasZoom;
        const y2 = (tgtRect.top + tgtRect.height / 2 - stageRect.top) / canvasZoom;

        let x1, x2, d, midX, midY;
        if (isSelfRef) {
          // Self-referencing loop on the right side of the same table card
          x1 = (srcRect.right - stageRect.left) / canvasZoom;
          x2 = (tgtRect.right - stageRect.left) / canvasZoom;
          const loopOut = 78;
          d = `M ${x1} ${y1} C ${x1 + loopOut} ${y1}, ${x2 + loopOut} ${y2}, ${x2} ${y2}`;
          midX = x1 + loopOut * 0.76;
          midY = (y1 + y2) / 2;
        } else {
          let dir1, dir2;
          if (srcCenterX <= tgtCenterX) {
            x1 = (srcRect.right - stageRect.left) / canvasZoom;
            x2 = (tgtRect.left - stageRect.left) / canvasZoom;
            dir1 = 1;
            dir2 = -1;
          } else {
            x1 = (srcRect.left - stageRect.left) / canvasZoom;
            x2 = (tgtRect.right - stageRect.left) / canvasZoom;
            dir1 = -1;
            dir2 = 1;
          }
          const dx = Math.max(55, Math.abs(x2 - x1) * 0.45);
          d = `M ${x1} ${y1} C ${x1 + dir1 * dx} ${y1}, ${x2 + dir2 * dx} ${y2}, ${x2} ${y2}`;
          midX = (x1 + x2) / 2;
          midY = (y1 + y2) / 2;
        }

        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', d);
        path.setAttribute('class', 'wire-path pointer-events-auto');
        path.setAttribute('stroke', isSelfRef ? '#34d399' : '#818cf8');
        path.setAttribute('stroke-dasharray', r.cardinality === '1:1' ? '5,4' : 'none');
        path.dataset.srcTable = r.source_table;
        path.dataset.tgtTable = r.target_table;
        path.dataset.relIndex = String(idx);
        path.addEventListener('click', (e) => {
          e.stopPropagation();
          openEditRelationshipModal(idx);
        });

        // Wide invisible hit-target path (18px wide) so clicking anywhere near a wire opens the editor
        const hitPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        hitPath.setAttribute('d', d);
        hitPath.setAttribute('fill', 'none');
        hitPath.setAttribute('stroke', 'transparent');
        hitPath.setAttribute('stroke-width', '18');
        hitPath.setAttribute('class', 'pointer-events-auto cursor-pointer');
        hitPath.dataset.relIndex = String(idx);
        hitPath.addEventListener('pointerenter', () => path.classList.add('wire-highlighted'));
        hitPath.addEventListener('pointerleave', () => path.classList.remove('wire-highlighted'));
        hitPath.addEventListener('click', (e) => {
          e.stopPropagation();
          openEditRelationshipModal(idx);
        });

        svg.appendChild(path);
        svg.appendChild(hitPath);

        // Relationship Pill Label at Midpoint (rendered in #erLabelsSvgLayer above cards so always clickable)
        const labelGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        labelGroup.setAttribute('class', 'pointer-events-auto cursor-pointer');
        labelGroup.dataset.relIndex = String(idx);

        const invName = r.inverse_name ? ` ⇄ ${r.inverse_name}` : '';
        const prefixIcon = isSelfRef ? '↻ ' : '';
        const labelText = `${prefixIcon}${r.name || 'FK'} (${r.cardinality || 'N:1'}) ✎`;
        const boxWidth = Math.max(88, labelText.length * 6.2 + 16);

        const titleEl = document.createElementNS('http://www.w3.org/2000/svg', 'title');
        const fwdTip = r.forward_description || r.description || `${r.source_table} -> ${r.target_table}`;
        const revTip = r.reverse_description || `${r.target_table} -> ${r.source_table}`;
        titleEl.textContent = `Click to edit relationship:\n• Forward (${r.name}${invName}): ${fwdTip}\n• Reverse: ${revTip}`;
        labelGroup.appendChild(titleEl);

        const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        rect.setAttribute('x', midX - boxWidth / 2);
        rect.setAttribute('y', midY - 11);
        rect.setAttribute('width', boxWidth);
        rect.setAttribute('height', 22);
        rect.setAttribute('rx', 6);
        rect.setAttribute('fill', '#1e1b4b');
        rect.setAttribute('stroke', '#818cf8');
        rect.setAttribute('stroke-width', '1.25');

        const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        text.setAttribute('x', midX);
        text.setAttribute('y', midY + 3.5);
        text.setAttribute('text-anchor', 'middle');
        text.setAttribute('fill', '#e0e7ff');
        text.setAttribute('font-size', '9.5');
        text.setAttribute('font-family', 'JetBrains Mono, monospace');
        text.textContent = labelText;

        labelGroup.addEventListener('pointerenter', () => {
          path.classList.add('wire-highlighted');
          rect.setAttribute('stroke', '#fbbf24');
          rect.setAttribute('fill', '#312e81');
        });
        labelGroup.addEventListener('pointerleave', () => {
          path.classList.remove('wire-highlighted');
          rect.setAttribute('stroke', '#818cf8');
          rect.setAttribute('fill', '#1e1b4b');
        });
        labelGroup.addEventListener('click', (e) => {
          e.stopPropagation();
          openEditRelationshipModal(idx);
        });

        labelGroup.appendChild(rect);
        labelGroup.appendChild(text);
        labelsSvg.appendChild(labelGroup);
      });
    }

    // Live Bottom-Right Minimap
    function renderMinimap() {
      const mmSvg = document.getElementById('erMinimapSvg');
      const vp = document.getElementById('erViewport');
      if (!mmSvg || !vp) return;

      const tables = ontology.tables || [];
      document.getElementById('minimapCount').textContent = `${tables.length}T`;

      let svgContent = '';
      // Draw mini relationship lines
      (ontology.semantic_relationships || []).forEach(r => {
        const p1 = nodePositions[r.source_table];
        const p2 = nodePositions[r.target_table];
        if (p1 && p2) {
          svgContent += `<line x1="${p1.x + 145}" y1="${p1.y + 60}" x2="${p2.x + 145}" y2="${p2.y + 60}" stroke="#6366f1" stroke-width="10" stroke-opacity="0.55" />`;
        }
      });

      // Draw mini table blocks
      tables.forEach(t => {
        const schema = t.schema || 'public';
        const key = `${schema}.${t.name}`;
        const pos = nodePositions[key] || { x: 60, y: 60 };
        const style = getSchemaStyle(schema);
        const h = 50 + (t.columns || []).length * 24;
        svgContent += `<rect x="${pos.x}" y="${pos.y}" width="290" height="${h}" rx="18" fill="${style.hex}" fill-opacity="0.85" stroke="#e2e8f0" stroke-width="6" />`;
      });

      // Draw current viewport indicator frame
      const vx = vp.scrollLeft / canvasZoom;
      const vy = vp.scrollTop / canvasZoom;
      const vw = vp.clientWidth / canvasZoom;
      const vh = vp.clientHeight / canvasZoom;
      svgContent += `<rect x="${vx}" y="${vy}" width="${vw}" height="${vh}" fill="rgba(255,255,255,0.06)" stroke="#fbbf24" stroke-width="14" stroke-dasharray="28,18" rx="16" />`;

      mmSvg.innerHTML = svgContent;
    }

    function onMinimapClick(e) {
      const mmSvg = document.getElementById('erMinimapSvg');
      const vp = document.getElementById('erViewport');
      if (!mmSvg || !vp) return;
      const rect = mmSvg.getBoundingClientRect();
      const clickX = ((e.clientX - rect.left) / rect.width) * STAGE_BASE_W;
      const clickY = ((e.clientY - rect.top) / rect.height) * STAGE_BASE_H;
      vp.scrollTo({
        left: Math.max(0, clickX * canvasZoom - vp.clientWidth / 2),
        top: Math.max(0, clickY * canvasZoom - vp.clientHeight / 2),
        behavior: 'smooth'
      });
    }

    function startNodeDrag(e, fullKey) {
      if (e.target.closest('button') || e.target.closest('input')) return;
      e.preventDefault();
      const pos = nodePositions[fullKey] || { x: 60, y: 60 };
      dragNodeState = {
        fullKey,
        startX: e.clientX,
        startY: e.clientY,
        origX: pos.x,
        origY: pos.y
      };
      window.addEventListener('pointermove', onNodeDragMove);
      window.addEventListener('pointerup', onNodeDragEnd);
    }

    function onNodeDragMove(e) {
      if (!dragNodeState) return;
      const dx = (e.clientX - dragNodeState.startX) / canvasZoom;
      const dy = (e.clientY - dragNodeState.startY) / canvasZoom;
      const newX = Math.max(12, dragNodeState.origX + dx);
      const newY = Math.max(12, dragNodeState.origY + dy);
      nodePositions[dragNodeState.fullKey] = { x: newX, y: newY };

      const card = document.querySelector(`[data-full-table="${CSS.escape(dragNodeState.fullKey)}"]`);
      if (card) {
        card.style.left = `${newX}px`;
        card.style.top = `${newY}px`;
      }
      drawERWires();
      renderMinimap();
    }

    function onNodeDragEnd() {
      dragNodeState = null;
      saveToLocalStorage();
      window.removeEventListener('pointermove', onNodeDragMove);
      window.removeEventListener('pointerup', onNodeDragEnd);
    }

    // Drag-to-connect wire between two columns or tables
    function clearWireDropHighlights() {
      document.querySelectorAll('.wire-drop-target-row').forEach(el => el.classList.remove('wire-drop-target-row'));
      document.querySelectorAll('.wire-drop-target-card').forEach(el => el.classList.remove('wire-drop-target-card'));
      document.querySelectorAll('.port-dot.active-port').forEach(el => el.classList.remove('active-port'));
    }

    function resolveDefaultTargetCol(fullTableKey) {
      const tObj = (ontology.tables || []).find(t => `${t.schema || 'public'}.${t.name}` === fullTableKey);
      const cols = tObj?.columns || [];
      if (!cols.length) return null;
      const pkCol = cols.find(c => c.pk) || cols.find(c => c.name.toLowerCase() === 'id') || cols[0];
      return pkCol ? pkCol.name : null;
    }

    function resolveWireDropTarget(clientX, clientY, srcTable, srcCol) {
      const targetEl = document.elementFromPoint(clientX, clientY);
      if (!targetEl) return { tgtTable: null, tgtCol: null, rowEl: null, cardEl: null };
      const portEl = targetEl.closest('[data-port-table]');
      const rowEl = targetEl.closest('[data-table][data-column]');
      const cardEl = targetEl.closest('[data-full-table]');

      let tgtTable = null;
      let tgtCol = null;
      if (portEl) {
        tgtTable = portEl.dataset.portTable;
        tgtCol = portEl.dataset.portCol;
      } else if (rowEl) {
        tgtTable = rowEl.dataset.table;
        tgtCol = rowEl.dataset.column;
      } else if (cardEl) {
        tgtTable = cardEl.dataset.fullTable;
        tgtCol = resolveDefaultTargetCol(tgtTable);
      }

      if (tgtTable === srcTable && tgtCol === srcCol) {
        return { tgtTable: null, tgtCol: null, rowEl: null, cardEl: null };
      }
      const resolvedRowEl = rowEl || (tgtTable && tgtCol
        ? document.querySelector(`[data-table="${CSS.escape(tgtTable)}"][data-column="${CSS.escape(tgtCol)}"]`)
        : null);
      return { tgtTable, tgtCol, rowEl: resolvedRowEl, cardEl };
    }

    function startWireDrag(e, srcTable, srcCol) {
      e.stopPropagation();
      const stage = document.getElementById('erStage');
      const stageRect = stage.getBoundingClientRect();
      const srcRow = stage.querySelector(`[data-table="${CSS.escape(srcTable)}"][data-column="${CSS.escape(srcCol)}"]`);
      if (srcRow) {
        srcRow.querySelectorAll('.port-dot').forEach(p => p.classList.add('active-port'));
      }

      const startX = (e.clientX - stageRect.left) / canvasZoom;
      const startY = (e.clientY - stageRect.top) / canvasZoom;

      const dragSvg = document.getElementById('erDragSvgLayer') || document.getElementById('erSvgLayer');
      dragSvg.innerHTML = '';

      const tempGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      tempGroup.setAttribute('style', 'pointer-events: none;');

      const tempLine = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      tempLine.setAttribute('stroke', '#fbbf24');
      tempLine.setAttribute('stroke-width', '3.2');
      tempLine.setAttribute('stroke-dasharray', '6,4');
      tempLine.setAttribute('fill', 'none');

      const endDot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      endDot.setAttribute('r', '5.5');
      endDot.setAttribute('fill', '#fbbf24');
      endDot.setAttribute('stroke', '#0f172a');
      endDot.setAttribute('stroke-width', '2');
      endDot.setAttribute('cx', startX);
      endDot.setAttribute('cy', startY);

      tempGroup.appendChild(tempLine);
      tempGroup.appendChild(endDot);
      dragSvg.appendChild(tempGroup);

      wireDragState = { srcTable, srcCol, startX, startY, tempGroup, tempLine, endDot };
      window.addEventListener('pointermove', onWireDragMove);
      window.addEventListener('pointerup', onWireDragEnd);
    }

    function onWireDragMove(e) {
      if (!wireDragState) return;
      const stage = document.getElementById('erStage');
      const stageRect = stage.getBoundingClientRect();
      const curX = (e.clientX - stageRect.left) / canvasZoom;
      const curY = (e.clientY - stageRect.top) / canvasZoom;
      const { srcTable, srcCol, startX, startY, tempLine, endDot } = wireDragState;
      const dir = curX >= startX ? 1 : -1;
      const dx = Math.max(45, Math.abs(curX - startX) * 0.45);
      tempLine.setAttribute('d', `M ${startX} ${startY} C ${startX + dir * dx} ${startY}, ${curX - dir * dx} ${curY}, ${curX} ${curY}`);
      endDot.setAttribute('cx', curX);
      endDot.setAttribute('cy', curY);

      clearWireDropHighlights();
      const srcRow = stage.querySelector(`[data-table="${CSS.escape(srcTable)}"][data-column="${CSS.escape(srcCol)}"]`);
      if (srcRow) srcRow.querySelectorAll('.port-dot').forEach(p => p.classList.add('active-port'));

      const { tgtTable, rowEl, cardEl } = resolveWireDropTarget(e.clientX, e.clientY, srcTable, srcCol);
      if (rowEl) {
        rowEl.classList.add('wire-drop-target-row');
      }
      if (cardEl && tgtTable) {
        cardEl.classList.add('wire-drop-target-card');
      }
    }

    function onWireDragEnd(e) {
      if (!wireDragState) return;
      const { srcTable, srcCol, tempGroup } = wireDragState;
      if (tempGroup && tempGroup.parentNode) tempGroup.parentNode.removeChild(tempGroup);
      clearWireDropHighlights();
      wireDragState = null;
      window.removeEventListener('pointermove', onWireDragMove);
      window.removeEventListener('pointerup', onWireDragEnd);

      const { tgtTable, tgtCol } = resolveWireDropTarget(e.clientX, e.clientY, srcTable, srcCol);
      if (tgtTable && tgtCol && (tgtTable !== srcTable || tgtCol !== srcCol)) {
        openNewRelationshipModal(srcTable, srcCol, tgtTable, tgtCol);
      }
    }

    // =========================================================================
    // 2. STRUCTURED TABLES & COLUMNS EDITOR
    // =========================================================================
    function renderTablesEditor() {
      const container = document.getElementById('tablesEditorContainer');
      container.innerHTML = '';

      const tables = ontology.tables || [];
      if (!tables.length) {
        container.innerHTML = `<div class="text-center py-12 text-slate-400 text-xs">No tables yet. Click <strong>+ Add Table</strong> or import a schema.</div>`;
        return;
      }

      tables.forEach((t, tIdx) => {
        const card = document.createElement('div');
        card.className = 'border border-slate-200 rounded-xl bg-white shadow-sm overflow-hidden';

        const fullTable = `${t.schema || 'public'}.${t.name}`;
        let colsHtml = '';
        (t.columns || []).forEach((c, cIdx) => {
          const fkTarget = c.fk ? `${c.fk.target_table}.${c.fk.target_column}` : '';
          const relIdx = (ontology.semantic_relationships || []).findIndex(
            r => r.source_table === fullTable && r.source_column === c.name
          );
          colsHtml += `
            <tr class="border-t border-slate-100 hover:bg-slate-50/70">
              <td class="px-2.5 py-1.5">
                <input type="text" value="${escapeAttr(c.name)}" onchange="updateColField(${tIdx}, ${cIdx}, 'name', this.value)" class="w-full bg-slate-50 border border-slate-200 rounded px-2 py-1 font-mono text-xs">
              </td>
              <td class="px-2.5 py-1.5">
                <input type="text" list="sqlTypesList" value="${escapeAttr(c.type || 'varchar(255)')}" onchange="updateColField(${tIdx}, ${cIdx}, 'type', this.value)" class="w-full bg-slate-50 border border-slate-200 rounded px-2 py-1 font-mono text-xs">
              </td>
              <td class="px-2.5 py-1.5 text-center">
                <input type="checkbox" ${c.pk ? 'checked' : ''} onchange="updateColField(${tIdx}, ${cIdx}, 'pk', this.checked)" class="rounded text-indigo-600">
              </td>
              <td class="px-2.5 py-1.5">
                <input type="text" value="${escapeAttr(c.description || '')}" onchange="updateColField(${tIdx}, ${cIdx}, 'description', this.value)" class="w-full bg-slate-50 border border-slate-200 rounded px-2 py-1 text-xs" placeholder="Column description...">
              </td>
              <td class="px-2.5 py-1.5 text-right whitespace-nowrap">
                ${relIdx >= 0
                  ? `<button type="button" onclick="openEditRelationshipModal(${relIdx})" class="inline-flex items-center gap-1 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 border border-indigo-200 font-mono text-[10px] px-2 py-0.5 rounded mr-1 transition" title="Click to edit relationship"><span>→ ${escapeHtml(fkTarget)}</span><i class="fa-solid fa-pen text-[9px]"></i></button>`
                  : (fkTarget ? `<span class="inline-block bg-indigo-50 text-indigo-700 border border-indigo-200 font-mono text-[10px] px-1.5 py-0.5 rounded mr-1" title="Foreign Key">→ ${escapeHtml(fkTarget)}</span>` : '')}
                <button type="button" onclick="removeColumn(${tIdx}, ${cIdx})" class="text-slate-400 hover:text-rose-600 px-1.5 py-0.5" title="Delete column">
                  <i class="fa-solid fa-trash-can text-xs"></i>
                </button>
              </td>
            </tr>
          `;
        });

        card.innerHTML = `
          <div class="bg-slate-50 border-b border-slate-200 px-3.5 py-2.5 flex flex-wrap items-center justify-between gap-2">
            <div class="flex items-center gap-2 flex-1 min-w-[240px]">
              <input type="text" value="${escapeAttr(t.schema || 'public')}" onchange="updateTableField(${tIdx}, 'schema', this.value)" class="w-24 bg-white border border-slate-200 rounded px-2 py-1 font-mono text-xs text-indigo-700 font-semibold" title="Schema / Domain">
              <span class="text-slate-400 font-mono">.</span>
              <input type="text" value="${escapeAttr(t.name)}" onchange="updateTableField(${tIdx}, 'name', this.value)" class="w-44 bg-white border border-slate-200 rounded px-2.5 py-1 font-mono text-xs font-bold text-slate-900" title="Table Name">
              <input type="text" value="${escapeAttr(t.description || '')}" onchange="updateTableField(${tIdx}, 'description', this.value)" class="flex-1 bg-white border border-slate-200 rounded px-2.5 py-1 text-xs text-slate-600" placeholder="Table description...">
            </div>
            <div class="flex items-center gap-1.5">
              <button type="button" onclick="quickAddColumn(${tIdx})" class="bg-indigo-50 hover:bg-indigo-100 text-indigo-700 border border-indigo-200 px-2.5 py-1 rounded text-xs font-semibold">
                <i class="fa-solid fa-plus mr-1"></i>Add Column
              </button>
              <button type="button" onclick="deleteTable(${tIdx})" class="text-slate-400 hover:text-rose-600 px-2 py-1 rounded text-xs" title="Delete Table">
                <i class="fa-solid fa-trash-can"></i>
              </button>
            </div>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-left border-collapse">
              <thead>
                <tr class="bg-slate-50/40 text-[10px] uppercase tracking-wider text-slate-400 font-semibold">
                  <th class="px-2.5 py-1.5 w-1/4">Column Name</th>
                  <th class="px-2.5 py-1.5 w-1/5">Data Type</th>
                  <th class="px-2.5 py-1.5 w-12 text-center">PK</th>
                  <th class="px-2.5 py-1.5">Description</th>
                  <th class="px-2.5 py-1.5 text-right w-28">FK / Action</th>
                </tr>
              </thead>
              <tbody>${colsHtml}</tbody>
            </table>
          </div>
        `;
        container.appendChild(card);
      });
    }

    function updateTableField(tIdx, field, val) {
      const t = ontology.tables[tIdx];
      if (!t) return;
      const oldFull = `${t.schema || 'public'}.${t.name}`;
      t[field] = val.trim();
      const newFull = `${t.schema || 'public'}.${t.name}`;

      if (oldFull !== newFull) {
        if (nodePositions[oldFull]) {
          nodePositions[newFull] = nodePositions[oldFull];
          delete nodePositions[oldFull];
        }
        (ontology.semantic_relationships || []).forEach(r => {
          if (r.source_table === oldFull) r.source_table = newFull;
          if (r.target_table === oldFull) r.target_table = newFull;
        });
      }
      refreshOutputsFromBackend();
    }

    function updateColField(tIdx, cIdx, field, val) {
      const col = ontology.tables[tIdx]?.columns[cIdx];
      if (!col) return;
      col[field] = typeof val === 'string' ? val.trim() : val;
      refreshOutputsFromBackend();
    }

    function quickAddColumn(tIdx) {
      const t = ontology.tables[tIdx];
      if (!t) return;
      t.columns = t.columns || [];
      const nextNum = t.columns.length + 1;
      t.columns.push({
        name: `column_${nextNum}`,
        type: 'varchar(255)',
        pk: t.columns.length === 0,
        description: `Column ${nextNum}`
      });
      if (activeBuilderTab === 'canvas') renderERCanvas();
      if (activeBuilderTab === 'tables') renderTablesEditor();
      refreshOutputsFromBackend();
    }

    function removeColumn(tIdx, cIdx) {
      ontology.tables[tIdx].columns.splice(cIdx, 1);
      renderTablesEditor();
      refreshOutputsFromBackend();
    }

    function addNewTable() {
      ontology.tables = ontology.tables || [];
      const defaultSchema = activeSchemaFilter !== 'ALL' ? activeSchemaFilter : (ontology.schemas?.[0]?.name || 'public');
      const nextNum = ontology.tables.length + 1;
      const tName = `new_table_${nextNum}`;
      const fullKey = `${defaultSchema}.${tName}`;
      nodePositions[fullKey] = { x: 100 + (nextNum % 4) * 320, y: 80 + Math.floor(nextNum / 4) * 240 };
      ontology.tables.push({
        schema: defaultSchema,
        name: tName,
        description: `Records for ${tName}`,
        columns: [
          { name: 'id', type: 'serial', pk: true, description: 'Primary key ID' },
          { name: 'name', type: 'varchar(255)', pk: false, description: 'Display name' },
          { name: 'created_at', type: 'timestamp', pk: false, description: 'Created timestamp' }
        ]
      });
      syncTopInputs();
      if (activeBuilderTab === 'canvas') renderERCanvas();
      if (activeBuilderTab === 'tables') renderTablesEditor();
      refreshOutputsFromBackend();
      showToast(`Added table ${fullKey} (double-click its header on the canvas to rename!)`);
    }

    function deleteTable(tIdx) {
      const t = ontology.tables[tIdx];
      if (!t) return;
      const full = `${t.schema || 'public'}.${t.name}`;
      ontology.tables.splice(tIdx, 1);
      ontology.semantic_relationships = (ontology.semantic_relationships || []).filter(
        r => r.source_table !== full && r.target_table !== full
      );
      syncTopInputs();
      if (activeBuilderTab === 'tables') renderTablesEditor();
      refreshOutputsFromBackend();
    }

    // =========================================================================
    // 3. RELATIONSHIPS, SCHEMAS, CONCEPTS & JOIN PATHS EDITOR
    // =========================================================================
    function renderRelsAndGlossary() {
      const relBox = document.getElementById('relsListContainer');
      const rels = ontology.semantic_relationships || [];
      if (!rels.length) {
        relBox.innerHTML = `<div class="text-xs text-slate-400 py-3">No connections yet. Drag between columns on the ER Canvas or click <strong>+ Add Connection</strong>.</div>`;
      } else {
        relBox.innerHTML = rels.map((r, idx) => {
          const card = r.cardinality || 'N:1';
          const invCard = card === 'N:1' ? '1:N' : (card === '1:N' ? 'N:1' : '1:1');
          const invName = r.inverse_name || `HAS_${(r.source_table || '').split('.').pop().toUpperCase()}`;
          const fwdDesc = r.forward_description || r.description || '';
          const revDesc = r.reverse_description || '';
          return `
          <div onclick="openEditRelationshipModal(${idx})" class="bg-white hover:bg-indigo-50/40 border border-slate-200 hover:border-indigo-300 rounded-xl p-3 space-y-2 text-xs cursor-pointer transition shadow-sm">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="font-mono font-bold text-indigo-700 bg-indigo-50 border border-indigo-200 px-2 py-0.5 rounded">${escapeHtml(r.name)}</span>
                <span class="font-mono text-slate-800">${escapeHtml(r.source_table)}.<strong>${escapeHtml(r.source_column)}</strong></span>
                <i class="fa-solid fa-arrows-left-right text-amber-500"></i>
                <span class="font-mono text-slate-800">${escapeHtml(r.target_table)}.<strong>${escapeHtml(r.target_column)}</strong></span>
                <span class="font-mono font-bold text-emerald-700 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded">${escapeHtml(invName)}</span>
              </div>
              <div class="flex items-center gap-1.5" onclick="event.stopPropagation()">
                <button type="button" onclick="openEditRelationshipModal(${idx})" class="bg-slate-100 hover:bg-indigo-100 text-slate-700 hover:text-indigo-700 px-2.5 py-1 rounded-lg font-semibold flex items-center gap-1 transition">
                  <i class="fa-solid fa-pen text-[10px]"></i> Edit
                </button>
                <button type="button" onclick="deleteRel(${idx})" class="text-slate-400 hover:text-rose-600 px-2 py-1" title="Delete relationship">
                  <i class="fa-solid fa-trash-can"></i>
                </button>
              </div>
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-1 border-t border-slate-100 text-[11px]">
              <div class="text-slate-600"><span class="font-mono font-semibold text-indigo-600">Forward (${escapeHtml(card)}):</span> ${escapeHtml(fwdDesc)}</div>
              <div class="text-slate-600"><span class="font-mono font-semibold text-emerald-600">Reverse (${escapeHtml(invCard)}):</span> ${escapeHtml(revDesc || 'Click Edit to describe reverse direction')}</div>
            </div>
          </div>
          `;
        }).join('');
      }

      const schBox = document.getElementById('schemasListContainer');
      const schemas = ontology.schemas || [];
      schBox.innerHTML = schemas.map((s, idx) => `
        <div class="flex items-center gap-2">
          <input type="text" value="${escapeAttr(s.name)}" onchange="ontology.schemas[${idx}].name=this.value.trim();refreshOutputsFromBackend();" class="w-36 bg-white border border-slate-200 rounded px-2.5 py-1 font-mono text-xs font-semibold">
          <input type="text" value="${escapeAttr(s.description || '')}" onchange="ontology.schemas[${idx}].description=this.value.trim();refreshOutputsFromBackend();" class="flex-1 bg-white border border-slate-200 rounded px-2.5 py-1 text-xs" placeholder="Schema description...">
          <button type="button" onclick="ontology.schemas.splice(${idx},1);renderRelsAndGlossary();refreshOutputsFromBackend();" class="text-slate-400 hover:text-rose-600 px-2 py-1"><i class="fa-solid fa-trash-can text-xs"></i></button>
        </div>
      `).join('');

      const bcBox = document.getElementById('conceptsListContainer');
      const concepts = ontology.business_concepts || [];
      bcBox.innerHTML = concepts.map((bc, idx) => `
        <div class="grid grid-cols-1 sm:grid-cols-12 gap-2 bg-white border border-slate-200 rounded-lg p-2 items-center">
          <input type="text" value="${escapeAttr(bc.name)}" onchange="ontology.business_concepts[${idx}].name=this.value.trim();refreshOutputsFromBackend();" class="sm:col-span-3 bg-slate-50 border border-slate-200 rounded px-2 py-1 text-xs font-semibold" placeholder="Concept">
          <input type="text" value="${escapeAttr(bc.schema || 'public')}" onchange="ontology.business_concepts[${idx}].schema=this.value.trim();refreshOutputsFromBackend();" class="sm:col-span-2 bg-slate-50 border border-slate-200 rounded px-2 py-1 font-mono text-xs" placeholder="Schema">
          <input type="text" value="${escapeAttr(bc.table || '')}" onchange="ontology.business_concepts[${idx}].table=this.value.trim();refreshOutputsFromBackend();" class="sm:col-span-2 bg-slate-50 border border-slate-200 rounded px-2 py-1 font-mono text-xs" placeholder="Table">
          <input type="text" value="${escapeAttr(bc.description || '')}" onchange="ontology.business_concepts[${idx}].description=this.value.trim();refreshOutputsFromBackend();" class="sm:col-span-4 bg-slate-50 border border-slate-200 rounded px-2 py-1 text-xs" placeholder="Description">
          <button type="button" onclick="ontology.business_concepts.splice(${idx},1);renderRelsAndGlossary();refreshOutputsFromBackend();" class="sm:col-span-1 text-slate-400 hover:text-rose-600 text-center"><i class="fa-solid fa-trash-can text-xs"></i></button>
        </div>
      `).join('');

      const jpBox = document.getElementById('joinsListContainer');
      const joins = ontology.common_join_paths || [];
      jpBox.innerHTML = joins.map((jp, idx) => `
        <div class="bg-white border border-slate-200 rounded-lg p-3 space-y-2">
          <div class="flex items-center justify-between gap-2">
            <input type="text" value="${escapeAttr(jp.name)}" onchange="ontology.common_join_paths[${idx}].name=this.value.trim();refreshOutputsFromBackend();" class="w-1/3 bg-slate-50 border border-slate-200 rounded px-2.5 py-1 text-xs font-semibold" placeholder="Join Path Title">
            <input type="text" value="${escapeAttr(jp.description || '')}" onchange="ontology.common_join_paths[${idx}].description=this.value.trim();refreshOutputsFromBackend();" class="flex-1 bg-slate-50 border border-slate-200 rounded px-2.5 py-1 text-xs" placeholder="Description">
            <button type="button" onclick="ontology.common_join_paths.splice(${idx},1);renderRelsAndGlossary();refreshOutputsFromBackend();" class="text-slate-400 hover:text-rose-600 px-2"><i class="fa-solid fa-trash-can text-xs"></i></button>
          </div>
          <textarea rows="3" onchange="ontology.common_join_paths[${idx}].sql=this.value;refreshOutputsFromBackend();" class="w-full bg-slate-950 text-emerald-300 font-mono text-xs p-2.5 rounded-lg">${escapeHtml(jp.sql || '')}</textarea>
        </div>
      `).join('');
    }

    function addSchemaItem() {
      ontology.schemas = ontology.schemas || [];
      ontology.schemas.push({ name: 'new_schema', description: 'Domain functional module' });
      renderRelsAndGlossary();
      refreshOutputsFromBackend();
    }

    function addConceptItem() {
      ontology.business_concepts = ontology.business_concepts || [];
      const firstT = ontology.tables?.[0] || { schema: 'public', name: 'table' };
      ontology.business_concepts.push({
        name: 'NewConcept',
        schema: firstT.schema || 'public',
        table: firstT.name || '',
        key_column: 'id',
        description: 'Business glossary concept definition'
      });
      renderRelsAndGlossary();
      refreshOutputsFromBackend();
    }

    function addJoinPathItem() {
      ontology.common_join_paths = ontology.common_join_paths || [];
      ontology.common_join_paths.push({
        name: 'Standard Entity Join',
        description: 'Joins parent and child tables',
        sql: 'SELECT *\nFROM public.table_a a\nJOIN public.table_b b ON a.id = b.table_a_id;'
      });
      renderRelsAndGlossary();
      refreshOutputsFromBackend();
    }

    // =========================================================================
    // 4. RELATIONSHIP CONNECTION MODAL LOGIC
    // =========================================================================
    function populateTableSelects() {
      const srcSel = document.getElementById('relSourceTable');
      const tgtSel = document.getElementById('relTargetTable');
      const opts = (ontology.tables || []).map(t => {
        const full = `${t.schema || 'public'}.${t.name}`;
        return `<option value="${escapeAttr(full)}">${escapeHtml(full)}</option>`;
      }).join('');
      srcSel.innerHTML = opts;
      tgtSel.innerHTML = opts;
    }

    function populateRelColumns(side, selectedCol = null) {
      const tblSel = document.getElementById(side === 'source' ? 'relSourceTable' : 'relTargetTable');
      const colSel = document.getElementById(side === 'source' ? 'relSourceCol' : 'relTargetCol');
      const fullTbl = tblSel.value;
      const tObj = (ontology.tables || []).find(t => `${t.schema || 'public'}.${t.name}` === fullTbl);
      const cols = tObj?.columns || [];
      colSel.innerHTML = cols.map(c => `<option value="${escapeAttr(c.name)}">${escapeHtml(c.name)} (${escapeHtml(c.type)})</option>`).join('');
      if (selectedCol && cols.some(c => c.name === selectedCol)) {
        colSel.value = selectedCol;
      }
    }

    function getInverseCardinality(card) {
      if (card === 'N:1') return '1:N';
      if (card === '1:N') return 'N:1';
      return '1:1';
    }

    function updateBidirectionalLabels() {
      const srcFull = document.getElementById('relSourceTable')?.value || 'source';
      const srcCol = document.getElementById('relSourceCol')?.value || 'id';
      const tgtFull = document.getElementById('relTargetTable')?.value || 'target';
      const tgtCol = document.getElementById('relTargetCol')?.value || 'id';
      const card = document.getElementById('relCardinality')?.value || 'N:1';
      const invCard = getInverseCardinality(card);

      const srcShort = srcFull.split('.').pop();
      const tgtShort = tgtFull.split('.').pop();

      const fwdHeader = document.getElementById('relFwdHeaderLabel');
      const revHeader = document.getElementById('relRevHeaderLabel');
      const fwdPill = document.getElementById('relFwdCardPill');
      const revPill = document.getElementById('relRevCardPill');
      const invBadge = document.getElementById('relInverseCardBadge');

      if (fwdHeader) fwdHeader.textContent = `Forward: ${srcShort}.${srcCol} → ${tgtShort}.${tgtCol}`;
      if (revHeader) revHeader.textContent = `Reverse: ${tgtShort}.${tgtCol} → ${srcShort}.${srcCol}`;
      if (fwdPill) fwdPill.textContent = card;
      if (revPill) revPill.textContent = invCard;
      if (invBadge) invBadge.textContent = `Reverse (${tgtShort} → ${srcShort}): ${invCard}`;
    }

    function swapRelDirectionInModal() {
      const srcTblEl = document.getElementById('relSourceTable');
      const srcColEl = document.getElementById('relSourceCol');
      const tgtTblEl = document.getElementById('relTargetTable');
      const tgtColEl = document.getElementById('relTargetCol');
      const cardEl = document.getElementById('relCardinality');
      const fwdNameEl = document.getElementById('relNameInput');
      const revNameEl = document.getElementById('relInverseNameInput');
      const fwdDescEl = document.getElementById('relDescInput');
      const revDescEl = document.getElementById('relReverseDescInput');

      const oldSrcTbl = srcTblEl.value;
      const oldSrcCol = srcColEl.value;
      const oldTgtTbl = tgtTblEl.value;
      const oldTgtCol = tgtColEl.value;

      srcTblEl.value = oldTgtTbl;
      populateRelColumns('source', oldTgtCol);
      tgtTblEl.value = oldSrcTbl;
      populateRelColumns('target', oldSrcCol);

      cardEl.value = getInverseCardinality(cardEl.value);

      const tmpName = fwdNameEl.value;
      fwdNameEl.value = revNameEl.value;
      revNameEl.value = tmpName;

      const tmpDesc = fwdDescEl.value;
      fwdDescEl.value = revDescEl.value;
      revDescEl.value = tmpDesc;

      updateBidirectionalLabels();
    }

    function suggestRelName(forceOverwrite = false) {
      const tgtFull = document.getElementById('relTargetTable').value || '';
      const srcFull = document.getElementById('relSourceTable').value || '';
      const srcCol = document.getElementById('relSourceCol').value || '';
      const card = document.getElementById('relCardinality').value || 'N:1';

      const tgtShort = tgtFull.split('.').pop() || 'target';
      const srcShort = srcFull.split('.').pop() || 'source';
      const tgtSingular = tgtShort.replace(/ies$/i, 'y').replace(/s$/i, '').replace(/_/g, ' ');
      const srcSingular = srcShort.replace(/ies$/i, 'y').replace(/s$/i, '').replace(/_/g, ' ');
      const srcPlural = srcShort.replace(/_/g, ' ');
      const tgtPlural = tgtShort.replace(/_/g, ' ');

      const nameInput = document.getElementById('relNameInput');
      const invNameInput = document.getElementById('relInverseNameInput');
      const descInput = document.getElementById('relDescInput');
      const revDescInput = document.getElementById('relReverseDescInput');
      const isSelfRef = srcFull && tgtFull && srcFull === tgtFull;
      const tgtCol = document.getElementById('relTargetCol')?.value || 'id';

      if (isSelfRef) {
        const lowCol = srcCol.toLowerCase();
        const isManagerLike = lowCol.includes('report') || lowCol.includes('manager') || lowCol.includes('supervisor') || lowCol.includes('boss');
        const defaultFwdEdge = isManagerLike ? 'REPORTS_TO' : `HAS_PARENT_${tgtShort.toUpperCase().replace(/S$/, '')}`;
        const defaultRevEdge = isManagerLike ? 'MANAGES_DIRECT_REPORTS' : `HAS_CHILD_${srcShort.toUpperCase()}`;

        if (forceOverwrite || !nameInput.value || nameInput.value.startsWith('HAS_') || nameInput.value.startsWith('BELONGS_TO_')) {
          nameInput.value = defaultFwdEdge;
        }
        if (invNameInput && (forceOverwrite || !invNameInput.value || invNameInput.value.startsWith('HAS_'))) {
          invNameInput.value = defaultRevEdge;
        }
        if (forceOverwrite || !descInput.value) {
          descInput.value = isManagerLike
            ? `Self-referencing hierarchy: each ${srcSingular} (alias sub.${srcCol}) reports to one manager/supervisor in the same ${srcShort} table (alias mgr.${tgtCol})`
            : `Self-referencing hierarchy: each child ${srcSingular} (alias child.${srcCol}) links to its parent ${srcSingular} in the same ${srcShort} table (alias parent.${tgtCol})`;
        }
        if (revDescInput && (forceOverwrite || !revDescInput.value)) {
          revDescInput.value = isManagerLike
            ? `One manager/supervisor in ${srcShort} (alias mgr.${tgtCol}) can oversee zero or many direct-report ${srcPlural} in the same table (alias sub.${srcCol})`
            : `One parent ${srcSingular} in ${srcShort} (alias parent.${tgtCol}) can have multiple child ${srcPlural} records in the same table (alias child.${srcCol})`;
        }
        updateBidirectionalLabels();
        return;
      }

      if (forceOverwrite || !nameInput.value || nameInput.value.startsWith('HAS_') || nameInput.value.startsWith('BELONGS_TO_')) {
        nameInput.value = `BELONGS_TO_${tgtShort.toUpperCase().replace(/S$/, '')}`;
      }

      if (invNameInput && (forceOverwrite || !invNameInput.value || invNameInput.value.startsWith('HAS_'))) {
        invNameInput.value = `HAS_${srcShort.toUpperCase()}`;
      }

      if (forceOverwrite || !descInput.value) {
        if (card === '1:1') {
          descInput.value = `Each ${srcSingular} is linked one-to-one with a single ${tgtSingular} via ${srcCol}`;
        } else if (card === '1:N') {
          descInput.value = `Each ${srcSingular} can reference multiple ${tgtPlural} records via ${srcCol}`;
        } else {
          descInput.value = `Each ${srcSingular} belongs to one ${tgtSingular} (via ${srcCol})`;
        }
      }

      if (revDescInput && (forceOverwrite || !revDescInput.value)) {
        if (card === '1:1') {
          revDescInput.value = `Each ${tgtSingular} corresponds to at most one ${srcSingular}`;
        } else if (card === '1:N') {
          revDescInput.value = `Each ${tgtSingular} belongs to one parent ${srcSingular}`;
        } else {
          revDescInput.value = `Each ${tgtSingular} can have multiple associated ${srcPlural} records`;
        }
      }

      updateBidirectionalLabels();
    }

    function openNewRelationshipModal(srcTbl = null, srcCol = null, tgtTbl = null, tgtCol = null) {
      if (!(ontology.tables || []).length) {
        showToast('Add at least one table first before creating connections.');
        return;
      }
      document.getElementById('relEditIndex').value = '-1';
      document.getElementById('relDeleteBtn').classList.add('hidden');
      populateTableSelects();

      const tables = ontology.tables || [];
      const defaultSrc = srcTbl || `${tables[0].schema || 'public'}.${tables[0].name}`;
      const defaultTgt = tgtTbl || `${(tables[1] || tables[0]).schema || 'public'}.${(tables[1] || tables[0]).name}`;

      document.getElementById('relSourceTable').value = defaultSrc;
      populateRelColumns('source', srcCol);
      document.getElementById('relTargetTable').value = defaultTgt;
      populateRelColumns('target', tgtCol || 'id');

      document.getElementById('relNameInput').value = '';
      document.getElementById('relInverseNameInput').value = '';
      document.getElementById('relDescInput').value = '';
      document.getElementById('relReverseDescInput').value = '';
      document.getElementById('relCardinality').value = 'N:1';
      suggestRelName(true);

      document.getElementById('relDialog').showModal();
    }

    function openEditRelationshipModal(idx) {
      const r = ontology.semantic_relationships?.[idx];
      if (!r) return;
      document.getElementById('relEditIndex').value = String(idx);
      document.getElementById('relDeleteBtn').classList.remove('hidden');
      populateTableSelects();

      document.getElementById('relSourceTable').value = r.source_table;
      populateRelColumns('source', r.source_column);
      document.getElementById('relTargetTable').value = r.target_table;
      populateRelColumns('target', r.target_column);

      const srcShort = (r.source_table || '').split('.').pop();
      document.getElementById('relNameInput').value = r.name || 'REFERENCES';
      document.getElementById('relInverseNameInput').value = r.inverse_name || `HAS_${srcShort.toUpperCase()}`;
      document.getElementById('relCardinality').value = r.cardinality || 'N:1';

      // Populate both forward and reverse descriptions
      let fwd = r.forward_description || '';
      let rev = r.reverse_description || '';
      if (!fwd && r.description) {
        if (r.description.includes(' | [')) {
          const parts = r.description.split(' | ');
          fwd = parts[0].replace(/^\[[^\]]+\]:\s*/, '');
          if (!rev && parts[1]) rev = parts[1].replace(/^\[[^\]]+\]:\s*/, '');
        } else {
          fwd = r.description;
        }
      }
      document.getElementById('relDescInput').value = fwd;
      document.getElementById('relReverseDescInput').value = rev;
      if (!fwd || !rev) suggestRelName(false);
      else updateBidirectionalLabels();

      document.getElementById('relDialog').showModal();
    }

    function saveRelationshipModal() {
      const editIdx = parseInt(document.getElementById('relEditIndex').value, 10);
      const srcTbl = document.getElementById('relSourceTable').value;
      const srcCol = document.getElementById('relSourceCol').value;
      const tgtTbl = document.getElementById('relTargetTable').value;
      const tgtCol = document.getElementById('relTargetCol').value;
      const srcShort = srcTbl.split('.').pop();
      const tgtShort = tgtTbl.split('.').pop();

      const relName = (document.getElementById('relNameInput').value.trim() || 'REFERENCES').toUpperCase().replace(/[^A-Z0-9_]/g, '_');
      const invName = (document.getElementById('relInverseNameInput').value.trim() || `HAS_${srcShort.toUpperCase()}`).toUpperCase().replace(/[^A-Z0-9_]/g, '_');
      const card = document.getElementById('relCardinality').value;
      const fwdDesc = document.getElementById('relDescInput').value.trim() || `Each ${srcShort} references ${tgtShort} via ${srcCol}`;
      const revDesc = document.getElementById('relReverseDescInput').value.trim() || `Each ${tgtShort} can have associated ${srcShort} records`;
      const combinedDesc = `[${srcShort} -> ${tgtShort}]: ${fwdDesc} | [${tgtShort} -> ${srcShort}]: ${revDesc}`;

      const relObj = {
        name: relName,
        inverse_name: invName,
        source_table: srcTbl,
        target_table: tgtTbl,
        source_column: srcCol,
        target_column: tgtCol,
        cardinality: card,
        forward_description: fwdDesc,
        reverse_description: revDesc,
        description: combinedDesc
      };

      ontology.semantic_relationships = ontology.semantic_relationships || [];
      if (editIdx >= 0 && editIdx < ontology.semantic_relationships.length) {
        ontology.semantic_relationships[editIdx] = relObj;
      } else {
        ontology.semantic_relationships.push(relObj);
      }

      const tObj = (ontology.tables || []).find(t => `${t.schema || 'public'}.${t.name}` === srcTbl);
      if (tObj) {
        const cObj = (tObj.columns || []).find(c => c.name === srcCol);
        if (cObj) {
          cObj.fk = { target_table: tgtTbl, target_column: tgtCol };
        }
      }

      document.getElementById('relDialog').close();
      syncTopInputs();
      if (activeBuilderTab === 'canvas') renderERCanvas();
      if (activeBuilderTab === 'tables') renderTablesEditor();
      if (activeBuilderTab === 'rels') renderRelsAndGlossary();
      refreshOutputsFromBackend();
      showToast(`Saved bidirectional link: ${srcShort}.${srcCol} ⇄ ${tgtShort}.${tgtCol} (${relName} / ${invName})`);
    }

    function deleteRel(idx) {
      const r = ontology.semantic_relationships?.[idx];
      if (!r) return;
      const tObj = (ontology.tables || []).find(t => `${t.schema || 'public'}.${t.name}` === r.source_table);
      if (tObj) {
        const cObj = (tObj.columns || []).find(c => c.name === r.source_column);
        if (cObj) delete cObj.fk;
      }
      ontology.semantic_relationships.splice(idx, 1);
      syncTopInputs();
      if (activeBuilderTab === 'canvas') renderERCanvas();
      if (activeBuilderTab === 'rels') renderRelsAndGlossary();
      refreshOutputsFromBackend();
    }

    function deleteRelFromModal() {
      const editIdx = parseInt(document.getElementById('relEditIndex').value, 10);
      if (editIdx >= 0) deleteRel(editIdx);
      document.getElementById('relDialog').close();
    }

    // =========================================================================
    // 5. BACKEND SYNC: EXPORT YAML / MD / SQL, VALIDATE, BRIDGE, SNAPSHOTS
    // =========================================================================
    async function refreshOutputsFromBackend() {
      saveToLocalStorage();
      try {
        const res = await fetch('/api/export', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ontology })
        });
        const data = await res.json();
        if (data.yaml) document.getElementById('yamlOutputArea').value = data.yaml;
        if (data.markdown) document.getElementById('mdOutputArea').value = data.markdown;
        if (data.sql_ddl) document.getElementById('sqlOutputArea').value = data.sql_ddl;
        updateValidationUI(data.validation);
        if (activeOutputTab === 'preview') renderMarkdownPreview();
      } catch (err) {
        console.error('Export sync failed:', err);
      }
    }

    function updateValidationUI(val) {
      if (!val) return;
      const iconBox = document.getElementById('valBadgeIcon');
      const pill = document.getElementById('valPill');
      const sub = document.getElementById('valSubtext');
      const msgBox = document.getElementById('valMessages');

      const errors = val.errors || [];
      const warnings = val.warnings || [];

      if (val.is_valid && warnings.length === 0) {
        iconBox.className = 'w-8 h-8 rounded-lg bg-emerald-50 border border-emerald-200 text-emerald-600 flex items-center justify-center';
        iconBox.innerHTML = '<i class="fa-solid fa-check"></i>';
        pill.className = 'text-[11px] px-2 py-0.5 rounded-full font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200';
        pill.textContent = '100% Ready for ontology.stewillis.com';
        sub.textContent = 'All primary keys, foreign keys, and schema references are valid.';
        msgBox.classList.add('hidden');
      } else if (val.is_valid && warnings.length > 0) {
        iconBox.className = 'w-8 h-8 rounded-lg bg-amber-50 border border-amber-200 text-amber-600 flex items-center justify-center';
        iconBox.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i>';
        pill.className = 'text-[11px] px-2 py-0.5 rounded-full font-semibold bg-amber-50 text-amber-700 border border-amber-200';
        pill.textContent = `Valid (${warnings.length} Warning${warnings.length > 1 ? 's' : ''})`;
        sub.textContent = 'Schema is valid but has non-blocking recommendations.';
        msgBox.classList.remove('hidden');
        msgBox.innerHTML = warnings.map(w => `<div class="text-amber-700 flex items-center gap-1.5"><i class="fa-solid fa-circle-info"></i><span>${escapeHtml(w)}</span></div>`).join('');
      } else {
        iconBox.className = 'w-8 h-8 rounded-lg bg-rose-50 border border-rose-200 text-rose-600 flex items-center justify-center';
        iconBox.innerHTML = '<i class="fa-solid fa-xmark"></i>';
        pill.className = 'text-[11px] px-2 py-0.5 rounded-full font-semibold bg-rose-50 text-rose-700 border border-rose-200';
        pill.textContent = `${errors.length} Validation Issue${errors.length > 1 ? 's' : ''}`;
        sub.textContent = 'Click "FixIt & Sync" to automatically repair missing keys or broken links.';
        msgBox.classList.remove('hidden');
        msgBox.innerHTML = [
          ...errors.map(e => `<div class="text-rose-600 font-medium flex items-center gap-1.5"><i class="fa-solid fa-circle-exclamation"></i><span>${escapeHtml(e)}</span></div>`),
          ...warnings.map(w => `<div class="text-amber-700 flex items-center gap-1.5"><i class="fa-solid fa-circle-info"></i><span>${escapeHtml(w)}</span></div>`)
        ].join('');
      }
    }

    async function renderMarkdownPreview() {
      const mdText = document.getElementById('mdOutputArea').value || '';
      const mermaidBox = document.getElementById('mermaidPreviewBox');
      const mdBox = document.getElementById('markdownRenderedBox');

      const mMatch = mdText.match(/```mermaid\s+([\s\S]*?)```/);
      if (mMatch) {
        mermaidBox.classList.remove('hidden');
        try {
          const { svg } = await mermaid.render('mermaidErSvg_' + Date.now(), mMatch[1].trim());
          mermaidBox.innerHTML = `<div class="text-[10px] font-mono uppercase text-slate-400 mb-2">Mermaid ER Diagram Preview</div>` + svg;
        } catch (e) {
          mermaidBox.innerHTML = `<pre class="text-xs text-rose-600">${escapeHtml(mMatch[1])}</pre>`;
        }
      } else {
        mermaidBox.classList.add('hidden');
      }

      const strippedMd = mdText.replace(/```mermaid[\s\S]*?```/, '');
      mdBox.innerHTML = marked.parse(strippedMd);
    }

    async function fixCurrentYamlInEditor() {
      const rawYaml = document.getElementById('yamlOutputArea').value;
      try {
        const res = await fetch('/api/fix-yaml', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ raw_text: rawYaml })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Failed to parse YAML');
        ontology = data.ontology;
        syncTopInputs();
        renderERCanvas();
        if (activeBuilderTab === 'tables') renderTablesEditor();
        if (activeBuilderTab === 'rels') renderRelsAndGlossary();
        await refreshOutputsFromBackend();
        const fixCount = (data.fixes || []).length;
        showToast(fixCount ? `YAML FixIt applied ${fixCount} repair(s) and synced studio.` : 'YAML validated and synced to canvas.');
      } catch (err) {
        alert('YAML FixIt Error: ' + err.message);
      }
    }

    async function runAutoDetectFKs() {
      try {
        const res = await fetch('/api/detect-fks', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ontology })
        });
        const data = await res.json();
        ontology = data.ontology;
        const count = (data.detected || []).length;
        syncTopInputs();
        if (activeBuilderTab === 'canvas') renderERCanvas();
        if (activeBuilderTab === 'tables') renderTablesEditor();
        if (activeBuilderTab === 'rels') renderRelsAndGlossary();
        await refreshOutputsFromBackend();
        showToast(count > 0 ? `Auto-detected and wired ${count} new Foreign Key connection(s)!` : 'All matching *_id Foreign Keys are already connected.');
      } catch (err) {
        alert('Auto-Detect FKs failed: ' + err.message);
      }
    }

    async function runAiEnrich() {
      const btn = document.getElementById('aiEnrichBtn');
      const origHtml = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Enriching...';
      try {
        const res = await fetch('/api/ai-enrich', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ontology })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Enrichment failed');
        ontology = data.ontology;
        syncTopInputs();
        if (activeBuilderTab === 'canvas') renderERCanvas();
        if (activeBuilderTab === 'tables') renderTablesEditor();
        if (activeBuilderTab === 'rels') renderRelsAndGlossary();
        await refreshOutputsFromBackend();
        showToast((data.changes || ['AI enrichment complete!']).join(' • '));
      } catch (err) {
        alert('AI Enrich Error: ' + err.message);
      } finally {
        btn.disabled = false;
        btn.innerHTML = origHtml;
      }
    }

    // Direct Push to ontology.stewillis.com
    async function pushToOntologyStudio() {
      const btn = document.getElementById('pushBridgeBtn');
      const origHtml = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Deploying to Neo4j...';
      try {
        const yamlText = document.getElementById('yamlOutputArea').value;
        const res = await fetch('/api/push-to-ontology', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ yaml_text: yamlText })
        });
        const data = await res.json();
        if (!res.ok || !data.success) throw new Error(data.error || 'Failed to deploy to Ontology Studio');
        showToast(`Deployed '${ontology.database?.name}' directly to ontology.stewillis.com (${data.nodes_created || 0} graph nodes compiled in Neo4j)!`);
      } catch (err) {
        alert('Push to Ontology Studio failed: ' + err.message);
      } finally {
        btn.disabled = false;
        btn.innerHTML = origHtml;
      }
    }

    // Server Project Snapshots & Shareable Links
    async function refreshSnapshotsList(selectedSlug = '') {
      try {
        const res = await fetch('/api/snapshots');
        const data = await res.json();
        const sel = document.getElementById('projectSnapshotSelect');
        if (!sel) return;
        sel.innerHTML = `<option value="">Switch Project / Preset...</option>` +
          (data.snapshots || []).map(s => `<option value="${escapeAttr(s.slug)}" ${s.slug === selectedSlug ? 'selected' : ''}>${escapeHtml(s.name)}</option>`).join('');
      } catch (e) {}
    }

    async function loadSelectedSnapshot(slug) {
      if (!slug) return;
      try {
        const res = await fetch(`/api/snapshots?slug=${encodeURIComponent(slug)}`);
        const data = await res.json();
        if (!res.ok || !data.ontology) throw new Error(data.error || 'Snapshot not found');
        ontology = data.ontology;
        nodePositions = data.positions || {};
        activeSchemaFilter = 'ALL';
        ensureNodePositions(!Object.keys(nodePositions).length);
        syncTopInputs();
        renderERCanvas();
        if (activeBuilderTab === 'tables') renderTablesEditor();
        if (activeBuilderTab === 'rels') renderRelsAndGlossary();
        await refreshOutputsFromBackend();
        window.history.replaceState({}, '', `/?project=${encodeURIComponent(slug)}`);
        showToast(`Loaded project '${data.name || slug}' (${(ontology.tables || []).length} tables).`);
      } catch (err) {
        alert('Failed to load project: ' + err.message);
      }
    }

    async function saveAndShareSnapshot() {
      const dbName = ontology.database?.name || 'MyDatabase';
      const slug = dbName.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_|_$/g, '') || 'schema';
      try {
        const res = await fetch('/api/snapshots', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            slug,
            name: `${dbName} (${(ontology.tables || []).length} tables)`,
            ontology,
            positions: nodePositions
          })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Save failed');
        const shareUrl = `${window.location.origin}/?project=${encodeURIComponent(data.slug)}`;
        window.history.replaceState({}, '', `/?project=${encodeURIComponent(data.slug)}`);
        await navigator.clipboard.writeText(shareUrl).catch(() => {});
        await refreshSnapshotsList(data.slug);
        showToast(`Saved project '${dbName}' & copied shareable URL: ${shareUrl}`);
      } catch (err) {
        alert('Save snapshot error: ' + err.message);
      }
    }

    // =========================================================================
    // 6. IMPORT MODAL, DB INTROSPECTION SQL QUERIES & SAMPLES
    // =========================================================================
    function openImportModal() {
      document.getElementById('importDialog').showModal();
    }

    function copyIntrospectionQuery(dialect) {
      let q = '';
      if (dialect === 'mysql') {
        q = `SELECT TABLE_SCHEMA AS table_schema, TABLE_NAME AS table_name, COLUMN_NAME AS column_name, COLUMN_TYPE AS data_type, COLUMN_KEY AS column_key, COLUMN_COMMENT AS description
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
ORDER BY TABLE_NAME, ORDINAL_POSITION;`;
      } else if (dialect === 'postgres') {
        q = `SELECT c.table_schema, c.table_name, c.column_name, c.data_type,
  CASE WHEN tc.constraint_type = 'PRIMARY KEY' THEN 'PRI' ELSE '' END AS column_key,
  COALESCE(pgd.description, '') AS description
FROM information_schema.columns c
LEFT JOIN information_schema.key_column_usage kcu
  ON c.table_schema = kcu.table_schema AND c.table_name = kcu.table_name AND c.column_name = kcu.column_name
LEFT JOIN information_schema.table_constraints tc
  ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema AND tc.constraint_type = 'PRIMARY KEY'
LEFT JOIN pg_catalog.pg_statio_all_tables st
  ON c.table_schema = st.schemaname AND c.table_name = st.relname
LEFT JOIN pg_catalog.pg_description pgd
  ON pgd.objoid = st.relid AND pgd.objsubid = c.ordinal_position
WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY c.table_schema, c.table_name, c.ordinal_position;`;
      } else {
        q = `SELECT s.name AS table_schema, t.name AS table_name, c.name AS column_name, ty.name AS data_type,
  CASE WHEN ic.index_column_id IS NOT NULL THEN 'PRI' ELSE '' END AS column_key,
   ISNULL(CAST(ep.value AS NVARCHAR(500)), '') AS description
FROM sys.tables t
JOIN sys.schemas s ON t.schema_id = s.schema_id
JOIN sys.columns c ON t.object_id = c.object_id
JOIN sys.types ty ON c.user_type_id = ty.user_type_id
LEFT JOIN sys.indexes i ON t.object_id = i.object_id AND i.is_primary_key = 1
LEFT JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id AND c.column_id = ic.column_id
LEFT JOIN sys.extended_properties ep ON ep.major_id = t.object_id AND ep.minor_id = c.column_id AND ep.name = 'MS_Description'
ORDER BY s.name, t.name, c.column_id;`;
      }
      navigator.clipboard.writeText(q);
      document.getElementById('importFormatSelect').value = 'csv';
      showToast(`Copied ${dialect.toUpperCase()} introspection query to clipboard! Run it in your SQL client and paste the CSV result.`);
    }

    function handleFileUpload(event) {
      const file = event.target.files?.[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (e) => {
        document.getElementById('importRawTextarea').value = e.target.result || '';
        const name = file.name.toLowerCase();
        if (name.endsWith('.sql')) document.getElementById('importFormatSelect').value = 'sql';
        else if (name.endsWith('.csv') || name.endsWith('.tsv') || name.endsWith('.txt')) document.getElementById('importFormatSelect').value = 'csv';
        else if (name.endsWith('.yaml') || name.endsWith('.yml') || name.endsWith('.json')) document.getElementById('importFormatSelect').value = 'yaml';
      };
      reader.readAsText(file);
    }

    function loadSampleInput(kind) {
      const area = document.getElementById('importRawTextarea');
      const fmt = document.getElementById('importFormatSelect');
      if (kind === 'sql') {
        fmt.value = 'sql';
        area.value = `CREATE TABLE fleet.depots (
  id INT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(120) NOT NULL COMMENT 'Depot site name',
  postcode VARCHAR(20)
);

CREATE TABLE fleet.vehicles (
  id INT PRIMARY KEY AUTO_INCREMENT,
  depot_id INT NOT NULL,
  registration VARCHAR(20) NOT NULL,
  vehicle_type VARCHAR(50) COMMENT 'Artic, Rigid, Van',
  capacity_pallets INT DEFAULT 26,
  FOREIGN KEY (depot_id) REFERENCES fleet.depots(id)
);

CREATE TABLE fleet.deliveries (
  id INT PRIMARY KEY AUTO_INCREMENT,
  vehicle_id INT NOT NULL,
  scheduled_date DATE NOT NULL,
  status VARCHAR(40) DEFAULT 'scheduled'
);`;
      } else if (kind === 'csv') {
        fmt.value = 'csv';
        area.value = `table_schema,table_name,column_name,data_type,column_key,description
hr,departments,id,int,PRI,Department unique ID
hr,departments,name,varchar(100),,Department title
hr,employees,id,int,PRI,Employee ID
hr,employees,department_id,int,,Assigned department ID
hr,employees,email,varchar(255),,Work email address
hr,timesheets,id,int,PRI,Timesheet entry ID
hr,timesheets,employee_id,int,,Submitted by employee ID
hr,timesheets,hours_worked,decimal(5,2),,Logged billable hours`;
      } else if (kind === 'broken_yaml') {
        fmt.value = 'yaml';
        area.value = `\`\`\`yaml
database: "WarehouseOps"
entities:
\t- name: "warehouses.locations"
\t  columns:
\t    - name:"id"
\t      type: "int"
\t    - name: "site_name"
\t      type: "varchar(200)"
\t- name: "warehouses.bays"
\t  columns:
\t    - name: "id"
\t      type: "int"
\t    - name: "location_id"
\t      type: "int"
\t    - name: "bay_code"
\t      type: "varchar(50)"
\`\`\``;
      }
    }

    async function submitImport() {
      const rawText = document.getElementById('importRawTextarea').value.trim();
      if (!rawText) {
        alert('Please paste or upload schema text first.');
        return;
      }
      const format = document.getElementById('importFormatSelect').value;
      const autoFk = document.getElementById('importAutoFkCheck').checked;
      const dbName = document.getElementById('dbNameInput').value.trim() || 'MyDatabase';
      const rdbms = document.getElementById('dbRdbmsInput').value;

      try {
        const res = await fetch('/api/import', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            raw_text: rawText,
            format,
            auto_detect_fks: autoFk,
            db_name: dbName,
            rdbms_type: rdbms
          })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Import failed');

        ontology = data.ontology;
        nodePositions = {};
        activeSchemaFilter = 'ALL';
        ensureNodePositions(true);
        document.getElementById('importDialog').close();

        syncTopInputs();
        renderERCanvas();
        if (activeBuilderTab === 'tables') renderTablesEditor();
        if (activeBuilderTab === 'rels') renderRelsAndGlossary();
        await refreshOutputsFromBackend();

        const msgParts = [`Imported ${(ontology.tables || []).length} tables`];
        if (data.detected_fks?.length) msgParts.push(`auto-wired ${data.detected_fks.length} FK link(s)`);
        if (data.fixes?.length) msgParts.push(`applied ${data.fixes.length} FixIt repair(s)`);
        showToast(msgParts.join(' • ') + '!');
      } catch (err) {
        alert('Import Error: ' + err.message);
      }
    }

    // =========================================================================
    // 7. COPY, DOWNLOAD & STANDALONE SVG DIAGRAM EXPORT
    // =========================================================================
    function copyCurrentOutput() {
      let text = document.getElementById('yamlOutputArea').value;
      if (activeOutputTab === 'md' || activeOutputTab === 'preview') text = document.getElementById('mdOutputArea').value;
      if (activeOutputTab === 'sql') text = document.getElementById('sqlOutputArea').value;
      navigator.clipboard.writeText(text);
      showToast(`Copied ${activeOutputTab.toUpperCase()} output to clipboard!`);
    }

    function downloadOutput(ext) {
      const dbSlug = (ontology.database?.name || 'database').toLowerCase().replace(/[^a-z0-9_]+/g, '_');
      let content = document.getElementById('yamlOutputArea').value;
      let mime = 'text/yaml;charset=utf-8';
      if (ext === 'md') {
        content = document.getElementById('mdOutputArea').value;
        mime = 'text/markdown;charset=utf-8';
      } else if (ext === 'sql') {
        content = document.getElementById('sqlOutputArea').value;
        mime = 'application/sql;charset=utf-8';
      }
      const filename = `${dbSlug}_ontology.${ext}`;
      const blob = new Blob([content], { type: mime });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      showToast(`Downloaded ${filename}`);
    }

    function exportCanvasToSVG() {
      const tables = ontology.tables || [];
      const rels = ontology.semantic_relationships || [];
      let maxRight = 900;
      let maxBottom = 600;
      tables.forEach(t => {
        const key = `${t.schema || 'public'}.${t.name}`;
        const pos = nodePositions[key] || { x: 60, y: 60 };
        const h = 48 + (t.columns || []).length * 26;
        if (pos.x + 340 > maxRight) maxRight = pos.x + 340;
        if (pos.y + h + 60 > maxBottom) maxBottom = pos.y + h + 60;
      });

      let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${maxRight}" height="${maxBottom}" viewBox="0 0 ${maxRight} ${maxBottom}" style="background:#0b1120;font-family:Inter,system-ui,sans-serif;">\n`;
      // Wires
      rels.forEach(r => {
        const p1 = nodePositions[r.source_table];
        const p2 = nodePositions[r.target_table];
        if (!p1 || !p2) return;
        const x1 = p1.x <= p2.x ? p1.x + 290 : p1.x;
        const x2 = p1.x <= p2.x ? p2.x : p2.x + 290;
        const y1 = p1.y + 52;
        const y2 = p2.y + 52;
        const dx = Math.max(55, Math.abs(x2 - x1) * 0.45) * (p1.x <= p2.x ? 1 : -1);
        svg += `<path d="M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}" fill="none" stroke="#818cf8" stroke-width="2.5" />\n`;
      });
      // Table Cards
      tables.forEach(t => {
        const schema = t.schema || 'public';
        const key = `${schema}.${t.name}`;
        const pos = nodePositions[key] || { x: 60, y: 60 };
        const cols = t.columns || [];
        const h = 42 + cols.length * 24;
        const style = getSchemaStyle(schema);
        svg += `<g transform="translate(${pos.x}, ${pos.y})">\n`;
        svg += `  <rect width="290" height="${h}" rx="12" fill="#0f172a" stroke="#334155" stroke-width="1.5" />\n`;
        svg += `  <rect width="290" height="34" rx="12" fill="${style.hex}" fill-opacity="0.35" />\n`;
        svg += `  <text x="14" y="21" fill="#ffffff" font-size="12" font-weight="bold" font-family="JetBrains Mono,monospace">${escapeHtml(schema)}.${escapeHtml(t.name)}</text>\n`;
        cols.forEach((c, idx) => {
          const cy = 54 + idx * 24;
          const badge = c.pk ? '[PK] ' : (c.fk ? '[FK] ' : '');
          const colColor = c.pk ? '#fde68a' : '#e2e8f0';
          svg += `  <text x="14" y="${cy}" fill="${colColor}" font-size="11" font-family="JetBrains Mono,monospace">${escapeHtml(badge + c.name)}</text>\n`;
          svg += `  <text x="276" y="${cy}" text-anchor="end" fill="#94a3b8" font-size="10" font-family="JetBrains Mono,monospace">${escapeHtml(c.type || 'varchar')}</text>\n`;
        });
        svg += `</g>\n`;
      });
      svg += `</svg>`;

      const dbSlug = (ontology.database?.name || 'database').toLowerCase().replace(/[^a-z0-9_]+/g, '_');
      const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${dbSlug}_er_diagram.svg`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      showToast(`Exported ${dbSlug}_er_diagram.svg!`);
    }

    function escapeHtml(s) {
      return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    function escapeAttr(s) {
      return String(s ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    // Initialize Studio on load
    window.addEventListener('DOMContentLoaded', async () => {
      const params = new URLSearchParams(window.location.search);
      const projectSlug = params.get('project');

      await refreshSnapshotsList(projectSlug || '');

      if (projectSlug) {
        await loadSelectedSnapshot(projectSlug);
      } else {
        // Check localStorage auto-save
        try {
          const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
          if (saved && saved.ontology && Array.isArray(saved.ontology.tables) && saved.ontology.tables.length) {
            ontology = saved.ontology;
            nodePositions = saved.nodePositions || {};
          }
        } catch (e) {}
        syncTopInputs();
        ensureNodePositions(!Object.keys(nodePositions).length);
        renderERCanvas();
        refreshOutputsFromBackend();
      }

      // Mouse-wheel zoom and empty-canvas drag-to-pan on #erViewport
      const vp = document.getElementById('erViewport');
      if (vp) {
        vp.addEventListener('scroll', () => requestAnimationFrame(renderMinimap));
        vp.addEventListener('wheel', (e) => {
          e.preventDefault();
          const delta = e.deltaY < 0 ? 0.1 : -0.1;
          changeCanvasZoom(delta);
        }, { passive: false });

        vp.addEventListener('pointerdown', (e) => {
          if (e.target.closest('.er-node') || e.target.closest('.port-dot') || e.target.closest('[data-rel-index]') || e.target.closest('.wire-path') || e.target.closest('g')) {
            return;
          }
          panCanvasState = {
            startX: e.clientX,
            startY: e.clientY,
            scrollLeft: vp.scrollLeft,
            scrollTop: vp.scrollTop
          };
          const onPanMove = (ev) => {
            if (!panCanvasState) return;
            vp.scrollLeft = panCanvasState.scrollLeft - (ev.clientX - panCanvasState.startX);
            vp.scrollTop = panCanvasState.scrollTop - (ev.clientY - panCanvasState.startY);
          };
          const onPanEnd = () => {
            panCanvasState = null;
            window.removeEventListener('pointermove', onPanMove);
            window.removeEventListener('pointerup', onPanEnd);
          };
          window.addEventListener('pointermove', onPanMove);
          window.addEventListener('pointerup', onPanEnd);
        });
      }

      // Keyboard shortcuts: Esc exits Full Page mode if no modal dialog is open
      window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && isFullPageCanvas) {
          const openModal = document.querySelector('dialog[open]');
          if (!openModal) {
            toggleFullPageCanvas();
          }
        }
      });
    });
    window.addEventListener('resize', () => {
      if (activeBuilderTab === 'canvas') {
        drawERWires();
        renderMinimap();
      }
    });
  </script>
</body>
</html>
"""


class YamlFixItHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        sys.stderr.write(f"[YAMLFixIt] {self.address_string()} - {format % args}\n")

    def _send_json(self, payload: dict, status: int = 200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        return json.loads(raw)

    def do_HEAD(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html", "/health"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
        else:
            self.send_error(404, "Not Found")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            page = HTML_PAGE.replace("__INITIAL_ONTOLOGY__", json.dumps(DEFAULT_ONTOLOGY))
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/health":
            self._send_json({"status": "ok", "service": "yamlfixit", "port": PORT})
        elif parsed.path == "/api/snapshots":
            qs = parse_qs(parsed.query)
            slug = (qs.get("slug", [""])[0] or "").strip()
            if slug:
                safe_slug = re.sub(r"[^a-zA-Z0-9_-]", "", slug)
                fpath = os.path.join(SNAPSHOTS_DIR, f"{safe_slug}.json")
                if not os.path.exists(fpath):
                    self._send_json({"error": f"Project '{safe_slug}' not found"}, 404)
                    return
                with open(fpath, "r", encoding="utf-8") as f:
                    self._send_json(json.load(f))
            else:
                self._send_json({"snapshots": _list_snapshots()})
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            data = self._read_json()

            if parsed.path == "/api/export":
                ont = data.get("ontology") or DEFAULT_ONTOLOGY
                norm_ont, _ = normalize_ontology_dict(ont)
                is_valid, errors, warnings = validate_ontology(norm_ont)
                yaml_str = export_to_yaml(norm_ont)
                md_str = export_to_markdown(norm_ont)
                sql_str = export_to_sql_ddl(norm_ont)
                self._send_json({
                    "ontology": norm_ont,
                    "yaml": yaml_str,
                    "markdown": md_str,
                    "sql_ddl": sql_str,
                    "validation": {
                        "is_valid": is_valid,
                        "errors": errors,
                        "warnings": warnings
                    }
                })

            elif parsed.path == "/api/import":
                raw_text = (data.get("raw_text") or "").strip()
                fmt = (data.get("format") or "auto").lower()
                auto_fk = bool(data.get("auto_detect_fks", True))
                db_name = data.get("db_name") or "MyDatabase"
                rdbms_type = data.get("rdbms_type") or "PostgreSQL"

                if not raw_text:
                    self._send_json({"error": "Empty input text."}, 400)
                    return

                fixes = []
                if fmt == "auto":
                    if re.search(r"\bCREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\b", raw_text, re.IGNORECASE):
                        fmt = "sql"
                    elif raw_text.startswith(("{", "version:", "database:", "tables:", "entities:", "```yaml", "---")) or (":" in raw_text and "\n" in raw_text and "," not in raw_text.splitlines()[0]):
                        fmt = "yaml"
                    else:
                        fmt = "csv"

                if fmt == "sql":
                    ont = parse_sql_ddl(raw_text, db_name=db_name, rdbms_type=rdbms_type)
                elif fmt == "yaml":
                    ont, fixes = repair_and_parse_yaml(raw_text)
                else:
                    ont = parse_csv_txt(raw_text, db_name=db_name, rdbms_type=rdbms_type)

                detected_fks = []
                if auto_fk:
                    ont, detected_fks = detect_foreign_keys(ont, auto_apply=True)

                ont, norm_fixes = normalize_ontology_dict(ont)
                fixes.extend(norm_fixes)
                self._send_json({
                    "ontology": ont,
                    "fixes": fixes,
                    "detected_fks": detected_fks
                })

            elif parsed.path == "/api/fix-yaml":
                raw_text = (data.get("raw_text") or "").strip()
                ont, fixes = repair_and_parse_yaml(raw_text)
                self._send_json({"ontology": ont, "fixes": fixes})

            elif parsed.path == "/api/detect-fks":
                ont = data.get("ontology") or DEFAULT_ONTOLOGY
                ont, detected = detect_foreign_keys(ont, auto_apply=True)
                self._send_json({"ontology": ont, "detected": detected})

            elif parsed.path == "/api/ai-enrich":
                ont = data.get("ontology") or DEFAULT_ONTOLOGY
                ont, changes = enrich_ontology_with_ai(ont)
                self._send_json({"ontology": ont, "changes": changes})

            elif parsed.path == "/api/push-to-ontology":
                yaml_text = (data.get("yaml_text") or "").strip()
                if not yaml_text:
                    self._send_json({"error": "No YAML provided to deploy."}, 400)
                    return
                res = _push_yaml_to_ontology_bridge(yaml_text)
                self._send_json(res, 200 if res.get("success") else 400)

            elif parsed.path == "/api/snapshots":
                slug = re.sub(r"[^a-zA-Z0-9_-]", "", (data.get("slug") or "project").lower()) or "project"
                name = data.get("name") or slug
                ont = data.get("ontology") or DEFAULT_ONTOLOGY
                positions = data.get("positions") or {}
                fpath = os.path.join(SNAPSHOTS_DIR, f"{slug}.json")
                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump({"slug": slug, "name": name, "ontology": ont, "positions": positions}, f, indent=2)
                self._send_json({"status": "saved", "slug": slug, "name": name})

            else:
                self._send_json({"error": f"Unknown endpoint {parsed.path}"}, 404)

        except Exception as e:
            self._send_json({"error": str(e)}, 400)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), YamlFixItHandler)
    print(f"============================================================")
    print(f" Ste Willis | YAML FixIt Studio running on http://127.0.0.1:{PORT}")
    print(f"============================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
