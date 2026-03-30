# Claude Memory + Ollama MCP Servers

Sistema di memoria persistente e LLM locali per Claude Code.

## Architettura
- **server.py** — MCP server memoria (FastMCP + MariaDB, stdio, 16 tool)
- **ollama_server.py** — MCP server LLM locali (FastMCP + Ollama API, stdio, 5 tool)
- **startup.py** — Hook SessionStart, genera contesto formattato all'avvio
- **viewer.py** — Web UI con CRUD completo (http://localhost:8899)
- **schema.sql** — Schema MariaDB v3.1 (4 tabelle)
- **MariaDB** — database `claude_memory`, utente `claude`@`localhost`
- **Ollama** — v0.18.2, GPU RTX 3070, API su localhost:11434

## MCP Server: memory (16 tool)
| Tool | Scopo |
|------|-------|
| `mem_save()` | Salva osservazione (auto-tag, dedup check) |
| `mem_search()` | FULLTEXT search + filtri data/sessione |
| `mem_context()` | Carica contesto completo (brief + pinned + creds + blockers + recent) |
| `mem_brief()` | Legge brief progetto |
| `mem_brief_update()` | Aggiorna brief progetto |
| `mem_recent()` | Timeline osservazioni recenti |
| `mem_session_end()` | Chiude sessione con summary + brief |
| `mem_projects()` | Lista progetti con stats |
| `mem_cleanup()` | Comprimi/rimuovi obs vecchie (rispetta pinned) |
| `mem_stats()` | Dashboard statistiche |
| `mem_update()` | Modifica content/tags/type di un'obs |
| `mem_delete()` | Elimina obs (auto-unlink figli) |
| `mem_pin()` | Toggle pin (sempre visibile, mai cleanup) |
| `mem_creds_save()` | Salva credenziali strutturate per progetto/servizio |
| `mem_creds()` | Lista credenziali per progetto |
| `mem_resume()` | Catch-up completo quando si torna su un progetto |

## MCP Server: ollama (5 tool)
| Tool | Scopo | Modello default |
|------|-------|-----------------|
| `llm_analyze(text, prompt)` | Analisi RE, log, disassembly | qwen3:8b |
| `llm_code(prompt, language)` | Genera codice, hook Frida, PoC | qwen2.5-coder:7b |
| `llm_summarize(text, focus)` | Riassunti documenti, output | llama3.1:8b |
| `llm_extract(text, schema)` | Estrae dati strutturati → JSON | qwen2.5-coder:7b |
| `llm_models()` | Lista modelli e stato GPU | — |

## Modelli Ollama installati
- `qwen3:8b` — reasoning, analisi (5.2 GB)
- `qwen2.5-coder:7b` — coding specializzato (4.7 GB)
- `deepseek-coder:6.7b` — coding alternativo (3.8 GB)
- `llama3.1:8b` — general purpose (4.9 GB)

## Database (4 tabelle)
- `observations` — Tier 1, obs raw con FULLTEXT, parent_id, pinned
- `session_summaries` — Tier 2, riassunti per sessione
- `project_briefs` — Tier 3, stato corrente per progetto
- `credentials` — Vault credenziali strutturate per progetto/servizio

## Hooks (~/.claude/settings.json)
- **SessionStart** — startup.py + prompt display contesto
- **PreCompact** — salvataggio aggressivo multi-obs prima della compressione

## Come lanciare
Configurati in `~/.mcp.json` come MCP server stdio.
Si avviano automaticamente con Claude Code via `uv run`.

## Dipendenze
- MariaDB server, Ollama server (sistema)
- Python: `mcp[cli]`, `mariadb` (gestite da `uv run --with`)
