# Ste Willis | YAML FixIt Studio (`yamlfixit.stewillis.com`)

Interactive database schema & ontology builder, multi-format importer (SQL DDL, CSV/TXT, broken YAML), visual ER canvas, and Graph-RAG `.yaml` / `.md` exporter designed for [ontology.stewillis.com](https://ontology.stewillis.com).

## Features
- **Multi-Format Ingest**:
  - **SQL DDL Parser**: Parses `CREATE TABLE` and `ALTER TABLE ... FOREIGN KEY` statements from PostgreSQL, MySQL, SQL Server, and Redshift.
  - **CSV / TXT Importer**: Accepts `INFORMATION_SCHEMA.COLUMNS` exports or simple `table,column,type` lists.
  - **YAML FixIt Mode**: Repairs malformed/broken YAML syntax and normalizes schemas to the 6-section Ontology format.
- **Dual-Mode Builder**:
  - **Visual ER Canvas**: Draggable table cards with click-and-drag Bezier wire connections between column ports.
  - **Structured Form Editor**: Inline table, column, primary key, foreign key, business concept, and SQL join path editors.
- **Smart Automation**:
  - **Auto-Detect FKs**: Infers foreign key links from column naming conventions (`*_id`, `*Id`).
  - **Gemini AI Auto-Enrich**: Generates semantic table/column descriptions, business glossary concepts, and standard SQL join templates.
- **Dual Export**:
  - **`.yaml`**: 100% validated for `ontology.stewillis.com`.
  - **`.md`**: Comprehensive Markdown documentation with Mermaid `erDiagram`, Entity Dictionary, and Join Templates.

## Deployment (`yamlfixit.stewillis.com`)

1. Add DNS `A` record for `yamlfixit` under `stewillis.com` pointing to your server IP.
2. Start the container (binds to `127.0.0.1:8076`):
   ```bash
   docker compose up -d --build
   ```
3. Run the Nginx + Let's Encrypt SSL setup script:
   ```bash
   sudo bash setup_yamlfixit_stewillis.sh
   ```
