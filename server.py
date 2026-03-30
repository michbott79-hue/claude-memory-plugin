#!/usr/bin/env python3
"""
Claude Memory Server — Persistent cross-session memory for Claude Code.

Three-tier architecture:
  Tier 1: Observations (raw, detailed, FULLTEXT searchable)
  Tier 2: Session Summaries (compressed, per-session)
  Tier 3: Project Briefs (always-current, injected at session start)

MariaDB backend with InnoDB page compression.
Version: 3.1
"""

import os
import re
import sys
import uuid
import time
import mariadb
from datetime import datetime, timedelta
from mcp.server.fastmcp import FastMCP

# --- Configuration ---

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "claude",
    "password": "claude_mem_2026",
    "database": "claude_memory",
    "autocommit": True,
}

PROJECTS_DIR = os.environ.get(
    "MEMORY_PROJECTS_DIR",
    os.path.join(os.path.expanduser("~"), "projects"),
)

SESSION_ID = uuid.uuid4().hex[:12]


# --- Database helpers ---

def get_conn(retries: int = 3) -> mariadb.Connection:
    """Get DB connection with retry logic."""
    for attempt in range(retries):
        try:
            return mariadb.connect(**DB_CONFIG)
        except mariadb.Error:
            if attempt == retries - 1:
                raise
            time.sleep(0.5 * (attempt + 1))


def detect_project(override: str = "") -> str:
    if override:
        return override
    cwd = os.path.realpath(os.getcwd())
    projects_dir = os.path.realpath(PROJECTS_DIR)
    if cwd.startswith(projects_dir + os.sep):
        relative = cwd[len(projects_dir) + 1:]
        name = relative.split(os.sep)[0]
        if name:
            return name
    for marker in ("CLAUDE.md", ".git"):
        if os.path.exists(os.path.join(cwd, marker)):
            return os.path.basename(cwd)
    return "general"


def normalize_tags(tags: str) -> str:
    """Normalize tags: lowercase, strip, deduplicate, sort."""
    if not tags:
        return ""
    parts = [t.strip().lower().replace(" ", "-") for t in tags.split(",")]
    parts = [t for t in parts if t]
    seen = set()
    unique = []
    for t in parts:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return ",".join(sorted(unique))


def auto_tags(content: str) -> list:
    """Auto-detect tags from content."""
    tags = []
    cl = content.lower()

    # IP addresses
    if re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", content):
        tags.append("infra")

    # SSH
    if any(w in cl for w in ["ssh ", "sshd", "authorized_keys", "id_rsa", "ssh-", ":22 ", "porta 22"]):
        tags.extend(["ssh", "accesso"])

    # Hex addresses (RE)
    if re.search(r"\b0x[0-9a-fA-F]{6,}\b", content):
        tags.append("reverse-engineering")

    # CVE
    if re.search(r"CVE-\d{4}-\d+", content, re.IGNORECASE):
        tags.append("cve")

    # Tools
    tool_map = {
        "frida": "frida", "ghidra": "ghidra", "gdb ": "gdb", "gdb:": "gdb",
        "radare2": "r2", " r2 ": "r2", "objdump": "objdump",
        "strace": "strace", "ltrace": "ltrace", "wireshark": "wireshark",
        "nmap": "nmap", "burp": "burp", "nuitka": "nuitka",
        "magisk": "magisk", "keydive": "keydive",
    }
    for keyword, tag in tool_map.items():
        if keyword in cl:
            tags.append(tag)

    # Credentials / access
    if any(w in cl for w in ["password", "credenziali", "token", "api key", "api_key", "apikey"]):
        tags.extend(["credenziali", "accesso"])

    # Database
    if any(w in cl for w in ["mysql", "mariadb", "postgres", "sqlite", "mongodb", "database"]):
        tags.append("database")

    # Proxy
    if any(w in cl for w in ["proxy", "socks5", "socks4"]):
        tags.extend(["proxy", "infra"])

    # Widevine / DRM
    if any(w in cl for w in ["widevine", "l1", "l3", "keybox", "drm", "cdm"]):
        tags.append("widevine")

    # Kernel / exploit
    if any(w in cl for w in ["kernel", "exploit", "uaf", "oob", "heap spray", "privilege escalation"]):
        tags.append("exploit")

    return list(set(tags))


# --- MCP Server ---

server = FastMCP(
    "memory",
    instructions="""\
You have persistent memory across sessions via the "memory" MCP server.

AT SESSION START:
- Context is auto-loaded by the SessionStart hook (startup.py) — do NOT call mem_context()
- Use mem_context(project="X") ONLY when switching to a different project mid-session

DURING THE SESSION:
- Call mem_save() for EVERYTHING: decisions, discoveries, errors, progress, credentials, RE data
- Call mem_search() to recall past work or check if something was tried before
- Use mem_pin() for critical observations that must always be visible
- Use mem_creds_save() for structured credential storage
- Use parent_id to chain related observations together

BEFORE CONTEXT COMPRESSION:
- Save critical in-progress work with mem_save() so nothing is lost

AT SESSION END (when the user is wrapping up):
- Call mem_session_end() with a summary and updated brief

GUIDELINES:
- Save observations proactively — anything you'd want to remember next session
- Tags are auto-detected from content but add specific ones too
- The brief should always reflect the TRUE current state of the project
""",
)


# --- Tool 1: mem_save ---

@server.tool()
def mem_save(
    content: str,
    type: str = "note",
    project: str = "",
    tags: str = "",
    parent_id: int = 0,
) -> str:
    """Save an observation to persistent memory.

    Args:
        content: What to remember (1-3 sentences).
        type: One of: decision, error, discovery, progress, blocker, note.
        project: Project name (auto-detected from CWD if empty).
        tags: Comma-separated searchable tags (e.g. 'frida,widevine,hook').
        parent_id: Link to a parent observation ID (0 = no parent).
    """
    proj = detect_project(project)
    valid_types = ("decision", "error", "discovery", "progress", "blocker", "note")
    if type not in valid_types:
        type = "note"

    # Merge manual tags with auto-detected tags
    manual = [t.strip().lower() for t in tags.split(",") if t.strip()] if tags else []
    detected = auto_tags(content)
    all_tags = list(set(manual + detected))
    tags = normalize_tags(",".join(all_tags))

    pid = parent_id if parent_id > 0 else None

    conn = get_conn()
    try:
        cur = conn.cursor()

        # Dedup: skip exact duplicate content in same project
        cur.execute(
            "SELECT id FROM observations WHERE project = ? AND content = ? LIMIT 1",
            (proj, content),
        )
        dup = cur.fetchone()
        if dup:
            return f"Duplicate skipped — matches existing #{dup[0]} in '{proj}'."

        cur.execute(
            "INSERT INTO observations (project, session_id, type, content, tags, parent_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (proj, SESSION_ID, type, content, tags, pid),
        )
        new_id = cur.lastrowid
        cur.execute(
            "SELECT COUNT(*) FROM observations WHERE project = ?", (proj,)
        )
        count = cur.fetchone()[0]
    finally:
        conn.close()

    auto_note = f" [auto-tags: {','.join(detected)}]" if detected else ""
    return f"Saved #{new_id} [{type}] for '{proj}' (session {SESSION_ID}).{auto_note} Total: {count} observations."


# --- Tool 2: mem_search ---

@server.tool()
def mem_search(
    query: str,
    project: str = "",
    type: str = "",
    limit: int = 10,
    from_date: str = "",
    to_date: str = "",
    session_id: str = "",
) -> str:
    """Search persistent memory using full-text search.

    Args:
        query: Search terms. Supports boolean mode: +required -excluded "exact phrase".
        project: Filter by project (empty = all projects).
        type: Filter by type (decision/error/discovery/progress/blocker/note).
        limit: Max results (default 10).
        from_date: Start date filter (YYYY-MM-DD format).
        to_date: End date filter (YYYY-MM-DD format).
        session_id: Filter by session ID.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        sql = (
            "SELECT id, project, type, content, tags, created_at, session_id, pinned "
            "FROM observations "
            "WHERE MATCH(content, tags) AGAINST(? IN BOOLEAN MODE) "
        )
        params: list = [query]
        if project:
            sql += "AND project = ? "
            params.append(project)
        if type:
            sql += "AND type = ? "
            params.append(type)
        if from_date:
            sql += "AND created_at >= ? "
            params.append(from_date)
        if to_date:
            sql += "AND created_at <= ? "
            params.append(to_date + " 23:59:59")
        if session_id:
            sql += "AND session_id = ? "
            params.append(session_id)
        sql += "ORDER BY pinned DESC, created_at DESC LIMIT ?"
        params.append(limit)
        cur.execute(sql, params)
        rows = cur.fetchall()

        if rows:
            ids = [r[0] for r in rows]
            placeholders = ",".join(["?"] * len(ids))
            cur.execute(
                f"UPDATE observations SET accessed_count = accessed_count + 1, "
                f"last_accessed = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
                ids,
            )
    except mariadb.Error:
        conn2 = get_conn()
        try:
            cur2 = conn2.cursor()
            sql = (
                "SELECT id, project, type, content, tags, created_at, session_id, pinned "
                "FROM observations WHERE content LIKE ? "
            )
            params = [f"%{query}%"]
            if project:
                sql += "AND project = ? "
                params.append(project)
            if from_date:
                sql += "AND created_at >= ? "
                params.append(from_date)
            if to_date:
                sql += "AND created_at <= ? "
                params.append(to_date + " 23:59:59")
            if session_id:
                sql += "AND session_id = ? "
                params.append(session_id)
            sql += "ORDER BY pinned DESC, created_at DESC LIMIT ?"
            params.append(limit)
            cur2.execute(sql, params)
            rows = cur2.fetchall()
        finally:
            conn2.close()
    finally:
        conn.close()

    if not rows:
        return f"No results for '{query}'."

    results = []
    for r in rows:
        id_, proj, typ, content, tags, created, sess, pinned = r
        pin = " 📌" if pinned else ""
        line = f"[#{id_}]{pin} {created} [{proj}] [{typ}] session:{sess}"
        if tags:
            line += f" tags:{tags}"
        line += f"\n{content}"
        results.append(line)

    return f"Found {len(rows)} results:\n\n" + "\n\n---\n\n".join(results)


# --- Tool 3: mem_brief ---

@server.tool()
def mem_brief(project: str = "") -> str:
    """Get the current project brief — compressed summary of project state.

    Args:
        project: Project name (auto-detected from CWD if empty).
    """
    proj = detect_project(project)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT brief, updated_at FROM project_briefs WHERE project = ?", (proj,)
        )
        row = cur.fetchone()
        if row:
            return f"# {proj}\n_Brief updated: {row[1]}_\n\n{row[0]}"
        cur.execute(
            "SELECT type, content, created_at FROM observations "
            "WHERE project = ? ORDER BY created_at DESC LIMIT 10",
            (proj,),
        )
        recent = cur.fetchall()
    finally:
        conn.close()

    if recent:
        lines = [f"- [{r[0]}] {r[1][:200]}" for r in recent]
        return f"No brief for '{proj}' yet. Recent observations:\n" + "\n".join(lines)
    return f"No brief or observations for '{proj}'. Start saving with mem_save()."


# --- Tool 4: mem_brief_update ---

@server.tool()
def mem_brief_update(brief: str, project: str = "") -> str:
    """Update the project brief (Tier 3).

    Args:
        brief: The updated brief text (300-600 words).
        project: Project name (auto-detected if empty).
    """
    proj = detect_project(project)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO project_briefs (project, brief) VALUES (?, ?) "
            "ON DUPLICATE KEY UPDATE brief = VALUES(brief), updated_at = CURRENT_TIMESTAMP",
            (proj, brief),
        )
    finally:
        conn.close()
    return f"Brief updated for '{proj}'."


# --- Tool 5: mem_recent ---

@server.tool()
def mem_recent(project: str = "", limit: int = 20, days: int = 7) -> str:
    """Get recent activity timeline.

    Args:
        project: Filter by project (empty = all projects).
        limit: Max results (default 20).
        days: How many days back to look (default 7).
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    try:
        cur = conn.cursor()
        if project:
            cur.execute(
                "SELECT id, project, type, content, tags, created_at, pinned "
                "FROM observations WHERE project = ? AND created_at > ? "
                "ORDER BY pinned DESC, created_at DESC LIMIT ?",
                (project, cutoff, limit),
            )
        else:
            cur.execute(
                "SELECT id, project, type, content, tags, created_at, pinned "
                "FROM observations WHERE created_at > ? "
                "ORDER BY pinned DESC, created_at DESC LIMIT ?",
                (cutoff, limit),
            )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return "No recent observations."

    results = []
    for r in rows:
        id_, proj, typ, content, tags, created, pinned = r
        pin = " 📌" if pinned else ""
        preview = content[:300] + ("..." if len(content) > 300 else "")
        results.append(f"[#{id_}]{pin} {created} [{proj}] [{typ}]\n{preview}")

    return f"Recent ({len(rows)}):\n\n" + "\n\n---\n\n".join(results)


# --- Tool 6: mem_session_end ---

@server.tool()
def mem_session_end(summary: str, project: str = "", brief: str = "") -> str:
    """Record session summary and optionally update project brief.

    Args:
        summary: 200-500 words of what was done, tried, learned.
        project: Project name (auto-detected if empty).
        brief: If provided, also updates the project brief.
    """
    proj = detect_project(project)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO session_summaries (project, session_id, summary) VALUES (?, ?, ?)",
            (proj, SESSION_ID, summary),
        )
        cur.execute(
            "INSERT INTO observations (project, session_id, type, content, tags) "
            "VALUES (?, ?, 'progress', ?, 'session-summary')",
            (proj, SESSION_ID, f"[Session Summary] {summary}"),
        )
        if brief:
            cur.execute(
                "INSERT INTO project_briefs (project, brief) VALUES (?, ?) "
                "ON DUPLICATE KEY UPDATE brief = VALUES(brief), updated_at = CURRENT_TIMESTAMP",
                (proj, brief),
            )
    finally:
        conn.close()
    msg = f"Session {SESSION_ID} saved for '{proj}'."
    if brief:
        msg += " Brief updated."
    return msg


# --- Tool 7: mem_projects ---

@server.tool()
def mem_projects() -> str:
    """List all known projects with observation counts and brief status."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT o.project, COUNT(*) AS obs_count, MAX(o.created_at) AS last_activity,
                   pb.brief IS NOT NULL AS has_brief, SUBSTRING(pb.brief, 1, 150) AS brief_excerpt
            FROM observations o LEFT JOIN project_briefs pb ON pb.project = o.project
            GROUP BY o.project ORDER BY last_activity DESC
        """)
        rows = cur.fetchall()
        cur.execute("""
            SELECT project, brief FROM project_briefs
            WHERE project NOT IN (SELECT DISTINCT project FROM observations)
        """)
        brief_only = cur.fetchall()
    finally:
        conn.close()

    if not rows and not brief_only:
        return "No projects in memory yet."

    results = []
    for proj, count, last, has_brief, excerpt in rows:
        brief_info = f"\n  {excerpt}..." if excerpt else "\n  (no brief)"
        results.append(f"**{proj}** — {count} obs, last: {last}, brief: {'yes' if has_brief else 'no'}" + brief_info)
    for proj, brief in brief_only:
        excerpt = brief[:150] if brief else ""
        results.append(f"**{proj}** — 0 obs, brief only\n  {excerpt}...")

    return "Projects:\n\n" + "\n\n".join(results)


# --- Tool 8: mem_context ---

@server.tool()
def mem_context(project: str = "") -> str:
    """Load full session context in one call.

    Args:
        project: Project name (auto-detected from CWD if empty).
    """
    proj = detect_project(project)
    sections = []
    conn = get_conn()
    try:
        cur = conn.cursor()

        cur.execute("SELECT brief, updated_at FROM project_briefs WHERE project = ?", (proj,))
        brief_row = cur.fetchone()
        if brief_row:
            sections.append(f"## Brief ({brief_row[1]})\n{brief_row[0]}")
        else:
            sections.append(f"## Brief\nNo brief for '{proj}' yet.")

        # Pinned observations
        cur.execute(
            "SELECT id, type, content, tags FROM observations "
            "WHERE project = ? AND pinned = TRUE ORDER BY created_at DESC",
            (proj,),
        )
        pinned = cur.fetchall()
        if pinned:
            lines = [f"- 📌 [#{p[0]}] [{p[1]}] {p[2][:200]}" for p in pinned]
            sections.append("## Pinned\n" + "\n".join(lines))

        # Credentials
        cur.execute(
            "SELECT service, host, port, username, extra FROM credentials WHERE project = ?",
            (proj,),
        )
        creds = cur.fetchall()
        if creds:
            lines = []
            for svc, host, port, user, extra in creds:
                parts = [svc]
                if host:
                    parts.append(f"{host}:{port}" if port else host)
                if user:
                    parts.append(f"user:{user}")
                if extra:
                    parts.append(extra[:100])
                lines.append("- " + " | ".join(parts))
            sections.append("## Credentials\n" + "\n".join(lines))

        cur.execute(
            "SELECT id, content, tags, created_at FROM observations "
            "WHERE project = ? AND type = 'blocker' ORDER BY created_at DESC LIMIT 5",
            (proj,),
        )
        blockers = cur.fetchall()
        if blockers:
            lines = [f"- [#{b[0]}] {b[1]}" for b in blockers]
            sections.append("## Active Blockers\n" + "\n".join(lines))

        cur.execute(
            "SELECT session_id, summary, created_at FROM session_summaries "
            "WHERE project = ? ORDER BY created_at DESC LIMIT 1",
            (proj,),
        )
        last_session = cur.fetchone()
        if last_session:
            sections.append(f"## Last Session ({last_session[2]}, id: {last_session[0]})\n{last_session[1]}")

        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "SELECT id, type, content, tags, created_at FROM observations "
            "WHERE project = ? AND created_at > ? AND pinned = FALSE "
            "ORDER BY created_at DESC LIMIT 10",
            (proj, cutoff),
        )
        recent = cur.fetchall()
        if recent:
            lines = [f"- [#{r[0]}] [{r[1]}] {r[2][:200]}" for r in recent]
            sections.append("## Recent (7d)\n" + "\n".join(lines))

        cur.execute(
            "SELECT id, type, content, accessed_count FROM observations "
            "WHERE project = ? AND accessed_count > 0 ORDER BY accessed_count DESC LIMIT 5",
            (proj,),
        )
        top = cur.fetchall()
        if top:
            lines = [f"- [#{t[0]}] [{t[1]}] (hit {t[3]}x) {t[2][:150]}" for t in top]
            sections.append("## Top Knowledge\n" + "\n".join(lines))

    finally:
        conn.close()

    if not sections:
        return f"No context for '{proj}'. Start saving with mem_save()."
    return f"# Context: {proj}\n\n" + "\n\n".join(sections)


# --- Tool 9: mem_cleanup ---

@server.tool()
def mem_cleanup(project: str = "", days_old: int = 30, dry_run: bool = True) -> str:
    """Compress old observations and remove duplicates. Pinned observations are never cleaned.

    Args:
        project: Project name (empty = all projects).
        days_old: Age threshold in days (default 30).
        dry_run: If True, only report what would be cleaned (default True).
    """
    proj = detect_project(project) if project else None
    cutoff = (datetime.now() - timedelta(days=days_old)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    try:
        cur = conn.cursor()

        where = "WHERE created_at < ? AND accessed_count = 0 AND pinned = FALSE AND type NOT IN ('blocker', 'decision')"
        params: list = [cutoff]
        if proj:
            where += " AND project = ?"
            params.append(proj)

        cur.execute(f"SELECT COUNT(*) FROM observations {where}", params)
        candidate_count = cur.fetchone()[0]

        dup_sql = (
            "SELECT COUNT(*) FROM observations o1 "
            "INNER JOIN observations o2 ON o1.project = o2.project "
            "AND o1.content = o2.content AND o1.id < o2.id"
        )
        if proj:
            dup_sql += " WHERE o1.project = ?"
            cur.execute(dup_sql, (proj,))
        else:
            cur.execute(dup_sql)
        dup_count = cur.fetchone()[0]

        total_sql = "SELECT COUNT(*) FROM observations"
        if proj:
            total_sql += " WHERE project = ?"
            cur.execute(total_sql, (proj,))
        else:
            cur.execute(total_sql)
        total = cur.fetchone()[0]

        if dry_run:
            scope = f"project '{proj}'" if proj else "all projects"
            return (
                f"Cleanup report for {scope}:\n"
                f"- Total observations: {total}\n"
                f"- Cleanup candidates (>{days_old}d, never accessed, not pinned/blocker/decision): {candidate_count}\n"
                f"- Duplicate observations: {dup_count}\n"
                f"- Would remove: {candidate_count + dup_count}\n"
                f"- Would keep: {total - candidate_count - dup_count}\n\n"
                f"Run with dry_run=False to execute."
            )

        removed = 0
        dup_del = (
            "DELETE o2 FROM observations o2 "
            "INNER JOIN observations o1 ON o1.project = o2.project "
            "AND o1.content = o2.content AND o1.id < o2.id"
        )
        if proj:
            dup_del += " WHERE o1.project = ?"
            cur.execute(dup_del, (proj,))
        else:
            cur.execute(dup_del)
        removed += cur.rowcount

        cur.execute(
            f"SELECT project, GROUP_CONCAT(content SEPARATOR ' | ') "
            f"FROM observations {where} GROUP BY project", params,
        )
        for arch_proj, combined in cur.fetchall():
            summary = combined[:2000] if combined else ""
            if summary:
                cur.execute(
                    "INSERT INTO session_summaries (project, session_id, summary) VALUES (?, ?, ?)",
                    (arch_proj, f"clean_{SESSION_ID[:4]}", f"[Auto-cleanup] {summary}"),
                )

        cur.execute(f"DELETE FROM observations {where}", params)
        removed += cur.rowcount

        return f"Cleanup done. Removed {removed} observations. Remaining: {total - removed}."
    finally:
        conn.close()


# --- Tool 10: mem_stats ---

@server.tool()
def mem_stats() -> str:
    """Get memory usage statistics across all projects."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT project, COUNT(*) AS total,
                SUM(type='decision') AS decisions, SUM(type='error') AS errors,
                SUM(type='blocker') AS blockers, SUM(type='discovery') AS discoveries,
                SUM(type='progress') AS progress, SUM(type='note') AS notes,
                SUM(pinned) AS pinned_count,
                MIN(created_at) AS first_obs, MAX(created_at) AS last_obs
            FROM observations GROUP BY project ORDER BY last_obs DESC
        """)
        projects = cur.fetchall()

        cur.execute("SELECT COUNT(*) FROM observations")
        total_obs = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM session_summaries")
        total_sessions = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM project_briefs")
        total_briefs = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM credentials")
        total_creds = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM observations WHERE pinned = TRUE")
        total_pinned = cur.fetchone()[0]

        cur.execute(
            "SELECT id, project, content, accessed_count FROM observations "
            "WHERE accessed_count > 0 ORDER BY accessed_count DESC LIMIT 5"
        )
        top_accessed = cur.fetchall()

        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("SELECT COUNT(*) FROM observations WHERE created_at > ?", (week_ago,))
        this_week = cur.fetchone()[0]

        cur.execute("""
            SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS size_mb
            FROM information_schema.tables WHERE table_schema = 'claude_memory'
        """)
        db_size = cur.fetchone()[0] or 0
    finally:
        conn.close()

    lines = [
        "# Memory Stats", "",
        f"**Global:** {total_obs} observations, {total_sessions} sessions, {total_briefs} briefs, {total_creds} credentials",
        f"**Pinned:** {total_pinned} observations",
        f"**This week:** {this_week} new observations",
        f"**DB size:** {db_size} MB", "",
        "## Per Project",
    ]
    for p in projects:
        proj, total, dec, err, blk, disc, prog, notes, pinned, first, last = p
        pin_str = f" 📌{pinned}" if pinned else ""
        lines.append(
            f"- **{proj}** — {total} obs{pin_str} "
            f"(D:{dec} E:{err} B:{blk} Di:{disc} P:{prog} N:{notes}) | {first} → {last}"
        )
    if top_accessed:
        lines.append("\n## Most Accessed")
        for t in top_accessed:
            lines.append(f"- [#{t[0]}] [{t[1]}] ({t[3]}x) {t[2][:100]}")
    return "\n".join(lines)


# --- Tool 11: mem_update ---

@server.tool()
def mem_update(id: int, content: str = "", tags: str = "", type: str = "") -> str:
    """Update an existing observation's content, tags, or type.

    Args:
        id: Observation ID to update.
        content: New content (empty = keep current).
        tags: New tags (empty = keep current).
        type: New type (empty = keep current).
    """
    if not content and not tags and not type:
        return "Nothing to update — provide content, tags, or type."

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, project FROM observations WHERE id = ?", (id,))
        row = cur.fetchone()
        if not row:
            return f"Observation #{id} not found."

        updates, params = [], []
        if content:
            updates.append("content = ?")
            params.append(content)
        if tags:
            updates.append("tags = ?")
            params.append(normalize_tags(tags))
        if type:
            valid_types = ("decision", "error", "discovery", "progress", "blocker", "note")
            if type not in valid_types:
                return f"Invalid type '{type}'."
            updates.append("type = ?")
            params.append(type)

        params.append(id)
        cur.execute(f"UPDATE observations SET {', '.join(updates)} WHERE id = ?", params)
    finally:
        conn.close()

    changed = [x for x in ["content" if content else "", "tags" if tags else "", "type" if type else ""] if x]
    return f"Updated #{id} in '{row[1]}' ({', '.join(changed)} changed)."


# --- Tool 12: mem_delete ---

@server.tool()
def mem_delete(id: int) -> str:
    """Delete an observation by ID.

    Args:
        id: Observation ID to delete.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, project, type, content FROM observations WHERE id = ?", (id,))
        row = cur.fetchone()
        if not row:
            return f"Observation #{id} not found."
        cur.execute("UPDATE observations SET parent_id = NULL WHERE parent_id = ?", (id,))
        cur.execute("DELETE FROM observations WHERE id = ?", (id,))
    finally:
        conn.close()
    return f"Deleted #{id} [{row[2]}] from '{row[1]}': {row[3][:100]}"


# --- Tool 13: mem_pin ---

@server.tool()
def mem_pin(id: int) -> str:
    """Toggle pin on an observation. Pinned observations always show in context and are never cleaned up.

    Args:
        id: Observation ID to pin/unpin.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, project, pinned, content FROM observations WHERE id = ?", (id,))
        row = cur.fetchone()
        if not row:
            return f"Observation #{id} not found."

        new_state = not row[2]
        cur.execute("UPDATE observations SET pinned = ? WHERE id = ?", (new_state, id))
    finally:
        conn.close()

    action = "📌 Pinned" if new_state else "Unpinned"
    return f"{action} #{id} in '{row[1]}': {row[3][:100]}"


# --- Tool 14: mem_creds_save ---

@server.tool()
def mem_creds_save(
    project: str,
    service: str,
    host: str = "",
    port: int = 0,
    username: str = "",
    password: str = "",
    extra: str = "",
) -> str:
    """Save or update credentials for a project service.

    Args:
        project: Project name.
        service: Service identifier (e.g. 'ssh-production', 'mysql-local', 'api-main').
        host: Hostname or IP address.
        port: Port number (0 = default).
        username: Username or login.
        password: Password, token, or key.
        extra: Additional info (JSON or free text).
    """
    proj = detect_project(project)
    p = port if port > 0 else None

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO credentials (project, service, host, port, username, password, extra) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON DUPLICATE KEY UPDATE host=VALUES(host), port=VALUES(port), "
            "username=VALUES(username), password=VALUES(password), extra=VALUES(extra), "
            "updated_at=CURRENT_TIMESTAMP",
            (proj, service, host, p, username, password, extra),
        )
    finally:
        conn.close()

    return f"Credentials saved for '{proj}/{service}' ({host}:{port or 'default'} user:{username})."


# --- Tool 15: mem_creds ---

@server.tool()
def mem_creds(project: str = "") -> str:
    """List all credentials for a project.

    Args:
        project: Project name (empty = all projects).
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        if project:
            proj = detect_project(project)
            cur.execute(
                "SELECT project, service, host, port, username, password, extra, updated_at "
                "FROM credentials WHERE project = ? ORDER BY service",
                (proj,),
            )
        else:
            cur.execute(
                "SELECT project, service, host, port, username, password, extra, updated_at "
                "FROM credentials ORDER BY project, service"
            )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return f"No credentials found{' for ' + project if project else ''}."

    results = []
    for proj, svc, host, port, user, pwd, extra, updated in rows:
        line = f"**{proj}/{svc}**"
        if host:
            line += f"\n  Host: {host}:{port}" if port else f"\n  Host: {host}"
        if user:
            line += f"\n  User: {user}"
        if pwd:
            line += f"\n  Pass: {pwd}"
        if extra:
            line += f"\n  Extra: {extra}"
        line += f"\n  Updated: {updated}"
        results.append(line)

    return f"Credentials ({len(rows)}):\n\n" + "\n\n".join(results)


# --- Tool 16: mem_resume ---

@server.tool()
def mem_resume(project: str = "") -> str:
    """Get a comprehensive catch-up summary for a project. Use when returning to a project after days.

    Args:
        project: Project name (auto-detected from CWD if empty).
    """
    proj = detect_project(project)
    sections = []
    conn = get_conn()
    try:
        cur = conn.cursor()

        # Brief
        cur.execute("SELECT brief, updated_at FROM project_briefs WHERE project = ?", (proj,))
        brief = cur.fetchone()
        if brief:
            sections.append(f"## Project Brief (updated {brief[1]})\n{brief[0]}")

        # Pinned observations
        cur.execute(
            "SELECT id, type, content, tags, created_at FROM observations "
            "WHERE project = ? AND pinned = TRUE ORDER BY created_at DESC",
            (proj,),
        )
        pinned = cur.fetchall()
        if pinned:
            lines = [f"- 📌 [#{p[0]}] [{p[1]}] {p[2][:300]}" for p in pinned]
            sections.append("## Pinned Observations\n" + "\n".join(lines))

        # Credentials
        cur.execute(
            "SELECT service, host, port, username, password, extra FROM credentials WHERE project = ?",
            (proj,),
        )
        creds = cur.fetchall()
        if creds:
            lines = []
            for svc, host, port, user, pwd, extra in creds:
                parts = [f"**{svc}**"]
                if host:
                    parts.append(f"{host}:{port}" if port else host)
                if user:
                    parts.append(f"user:{user}")
                if pwd:
                    parts.append(f"pass:{pwd}")
                if extra:
                    parts.append(extra[:100])
                lines.append("- " + " | ".join(parts))
            sections.append("## Credentials\n" + "\n".join(lines))

        # All blockers
        cur.execute(
            "SELECT id, content, tags, created_at FROM observations "
            "WHERE project = ? AND type = 'blocker' ORDER BY created_at DESC LIMIT 10",
            (proj,),
        )
        blockers = cur.fetchall()
        if blockers:
            lines = [f"- [#{b[0]}] ({b[3].strftime('%b %d')}) {b[1][:250]}" for b in blockers]
            sections.append("## Blockers\n" + "\n".join(lines))

        # Last 3 session summaries
        cur.execute(
            "SELECT session_id, summary, created_at FROM session_summaries "
            "WHERE project = ? ORDER BY created_at DESC LIMIT 3",
            (proj,),
        )
        sessions = cur.fetchall()
        if sessions:
            lines = []
            for sid, summary, created in sessions:
                preview = summary[:400] + ("..." if len(summary) > 400 else "")
                lines.append(f"### Session {sid} ({created.strftime('%b %d %H:%M')})\n{preview}")
            sections.append("## Recent Sessions\n" + "\n".join(lines))

        # Decisions
        cur.execute(
            "SELECT id, content, created_at FROM observations "
            "WHERE project = ? AND type = 'decision' ORDER BY created_at DESC LIMIT 5",
            (proj,),
        )
        decisions = cur.fetchall()
        if decisions:
            lines = [f"- [#{d[0]}] ({d[2].strftime('%b %d')}) {d[1][:250]}" for d in decisions]
            sections.append("## Key Decisions\n" + "\n".join(lines))

        # Recent observations (14 days, more than context)
        cutoff = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "SELECT id, type, content, tags, created_at FROM observations "
            "WHERE project = ? AND created_at > ? AND pinned = FALSE "
            "ORDER BY DATE(created_at) DESC, "
            "FIELD(type, 'blocker','error','decision','discovery','progress','note'), "
            "created_at DESC LIMIT 30",
            (proj, cutoff),
        )
        recent = cur.fetchall()
        if recent:
            lines = [f"- [#{r[0]}] [{r[1]}] ({r[4].strftime('%b %d %H:%M')}) {r[2][:200]}" for r in recent]
            sections.append("## Recent Activity (14d)\n" + "\n".join(lines))

        # Stats
        cur.execute("SELECT COUNT(*) FROM observations WHERE project = ?", (proj,))
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM session_summaries WHERE project = ?", (proj,))
        sess_count = cur.fetchone()[0]
        sections.append(f"## Stats\nTotal: {total} observations, {sess_count} sessions, {len(creds)} credentials, {len(pinned)} pinned")

    finally:
        conn.close()

    if not sections:
        return f"No data for '{proj}'."
    return f"# Resume: {proj}\n\n" + "\n\n".join(sections)


# --- Main ---

if __name__ == "__main__":
    server.run(transport="stdio")
