# Claude Memory Server

MCP server per memoria persistente cross-sessione di Claude Code.

## Architettura
- **server.py** — MCP server (FastMCP + mariadb connector, stdio transport)
- **schema.sql** — Schema MariaDB v3.0 (3 tabelle: observations, session_summaries, project_briefs)
- **startup.py** — Hook SessionStart, genera contesto formattato all'avvio
- **viewer.py** — Web UI con CRUD completo (http://localhost:8899)
- **MariaDB** — database `claude_memory`, utente `claude`@`localhost`

## Three-Tier Model
- Tier 1: `observations` — osservazioni raw, FULLTEXT searchable, con parent_id per chaining e accessed_count per relevance
- Tier 2: `session_summaries` — riassunti compressi per sessione
- Tier 3: `project_briefs` — stato corrente per progetto (~500 parole)

## Tools (12)
| Tool | Scopo |
|------|-------|
| `mem_save()` | Salva osservazione con tipo, tags (auto-normalizzati), parent_id, dedup check |
| `mem_search()` | FULLTEXT search con boolean mode, filtri data/sessione, aggiorna accessed_count |
| `mem_context()` | Session start — carica brief + blockers + last session + recent + top knowledge |
| `mem_brief()` | Legge solo il brief del progetto |
| `mem_brief_update()` | Aggiorna il brief del progetto |
| `mem_recent()` | Timeline osservazioni recenti |
| `mem_session_end()` | Chiude sessione con summary + brief update |
| `mem_projects()` | Lista tutti i progetti con stats |
| `mem_cleanup()` | Comprimi/rimuovi osservazioni vecchie e duplicate |
| `mem_stats()` | Dashboard statistiche memoria |
| `mem_update()` | Modifica content/tags/type di un'osservazione esistente |
| `mem_delete()` | Elimina un'osservazione per ID (scollega figli automaticamente) |

## Feature v3.0
- **Tag normalization**: lowercase, strip, dedup, sort automatico
- **Dedup check**: skip salvataggio se contenuto identico esiste già nel progetto
- **Search avanzata**: filtri from_date, to_date, session_id
- **DB retry**: connessione con retry automatico (3 tentativi)
- **Session ID 12 char**: ridotto rischio collisioni
- **Viewer CRUD**: edit/delete osservazioni dal browser, export JSON, toast notifications
- **Smart timeline**: priorità blocker > error > decision > discovery > progress > note

## Hooks configurati (~/.claude/settings.json)
- **SessionStart** — esegue startup.py + prompt display contesto
- **PreCompact** — forza mem_save() prima della compressione contesto

## Come lanciare
Configurato in `~/.claude/settings.json` come MCP server stdio.
Si avvia automaticamente con Claude Code via `uv run --with mcp[cli] --with mariadb`.

## Dipendenze
- MariaDB server (sistema)
- Python: `mcp[cli]`, `mariadb` (gestite da `uv run --with`)
