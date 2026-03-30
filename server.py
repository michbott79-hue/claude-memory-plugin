#!/usr/bin/env python3
"""
Claude Memory Server — Persistent cross-session memory for Claude Code.

Three-tier architecture:
  Tier 1: Observations (raw, detailed, FULLTEXT searchable)
  Tier 2: Session Summaries (compressed, per-session)
  Tier 3: Project Briefs (always-current, injected at session start)

MariaDB backend with InnoDB page compression.
Version: 3.0
"""

import os
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


# --- MCP Server ---

server = FastMCP(
    "memory",
    instructions="""\
You have persistent memory across sessions via the "memory" MCP server.

AT SESSION START:
- Context is auto-loaded by the SessionStart hook (startup.py) — do NOT call mem_context()
- Use mem_context(project="X") ONLY when switching to a different project mid-session

DURING THE SESSION:
- Call mem_save() for important decisions, discoveries, errors, progress
- Call mem_search() to recall past work or check if something was tried before
- Use parent_id to chain related observations together

BEFORE CONTEXT COMPRESSION:
- Save critical in-progress work with mem_save() so nothing is lost

AT SESSION END (when the user is wrapping up):
- Call mem_session_end() with a summary and updated brief

GUIDELINES:
- Save observations proactively — anything you'd want to remember next session
- Keep observations concise (1-3 sentences each)
- Use specific tags for searchability (tool names, CVEs, error codes)
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

    tags = normalize_tags(tags)
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

    return f"Saved #{new_id} [{type}] for '{proj}' (session {SESSION_ID}). Total: {count} observations."


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
            "SELECT id, project, type, content, tags, created_at, session_id "
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

        sql += "ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cur.execute(sql, params)
        rows = cur.fetchall()

        # Update accessed_count for returned results
        if rows:
            ids = [r[0] for r in rows]
            placeholders = ",".join(["?"] * len(ids))
            cur.execute(
                f"UPDATE observations SET accessed_count = accessed_count + 1, "
                f"last_accessed = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
                ids,
            )
    except mariadb.Error:
        # Fallback to LIKE search
        conn2 = get_conn()
        try:
            cur2 = conn2.cursor()
            sql = (
                "SELECT id, project, type, content, tags, created_at, session_id "
                "FROM observations WHERE content LIKE ? "
            )
            params = [f"%{query}%"]
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
            sql += "ORDER BY created_at DESC LIMIT ?"
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
        id_, proj, typ, content, tags, created, sess = r
        line = f"[#{id_}] {created} [{proj}] [{typ}] session:{sess}"
        if tags:
            line += f" tags:{tags}"
        line += f"\n{content}"
        results.append(line)

    return f"Found {len(rows)} results:\n\n" + "\n\n---\n\n".join(results)


# --- Tool 3: mem_brief ---

@server.tool()
def mem_brief(project: str = "") -> str:
    """Get the current project brief — compressed summary of project state.

    Call this at the START of every session. Returns current state,
    recent progress, active blockers, and next steps.

    Args:
        project: Project name (auto-detected from CWD if empty).
    """
    proj = detect_project(project)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT brief, updated_at FROM project_briefs WHERE project = ?",
            (proj,),
        )
        row = cur.fetchone()

        if row:
            brief, updated = row
            return f"# {proj}\n_Brief updated: {updated}_\n\n{brief}"

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
        return (
            f"No brief for '{proj}' yet. Recent observations:\n"
            + "\n".join(lines)
            + "\n\nUse mem_brief_update to create one."
        )
    return f"No brief or observations for '{proj}'. Start saving with mem_save()."


# --- Tool 4: mem_brief_update ---

@server.tool()
def mem_brief_update(brief: str, project: str = "") -> str:
    """Update the project brief (Tier 3).

    The brief should be 300-600 words covering:
    - What this project is (1-2 sentences)
    - Current state and recent progress
    - Active blockers or open questions
    - Immediate next steps

    Args:
        brief: The updated brief text.
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
                "SELECT id, project, type, content, tags, created_at "
                "FROM observations WHERE project = ? AND created_at > ? "
                "ORDER BY created_at DESC LIMIT ?",
                (project, cutoff, limit),
            )
        else:
            cur.execute(
                "SELECT id, project, type, content, tags, created_at "
                "FROM observations WHERE created_at > ? "
                "ORDER BY created_at DESC LIMIT ?",
                (cutoff, limit),
            )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return "No recent observations."

    results = []
    for r in rows:
        id_, proj, typ, content, tags, created = r
        preview = content[:300] + ("..." if len(content) > 300 else "")
        results.append(f"[#{id_}] {created} [{proj}] [{typ}]\n{preview}")

    return f"Recent ({len(rows)}):\n\n" + "\n\n---\n\n".join(results)


# --- Tool 6: mem_session_end ---

@server.tool()
def mem_session_end(
    summary: str,
    project: str = "",
    brief: str = "",
) -> str:
    """Record session summary and optionally update project brief.

    Call at the end of a work session.

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
            "INSERT INTO session_summaries (project, session_id, summary) "
            "VALUES (?, ?, ?)",
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
            SELECT
                o.project,
                COUNT(*) AS obs_count,
                MAX(o.created_at) AS last_activity,
                pb.brief IS NOT NULL AS has_brief,
                SUBSTRING(pb.brief, 1, 150) AS brief_excerpt
            FROM observations o
            LEFT JOIN project_briefs pb ON pb.project = o.project
            GROUP BY o.project
            ORDER BY last_activity DESC
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
    for r in rows:
        proj, count, last, has_brief, excerpt = r
        brief_info = f"\n  {excerpt}..." if excerpt else "\n  (no brief)"
        results.append(
            f"**{proj}** — {count} obs, last: {last}, brief: {'yes' if has_brief else 'no'}"
            + brief_info
        )

    for proj, brief in brief_only:
        excerpt = brief[:150] if brief else ""
        results.append(f"**{proj}** — 0 obs, brief only\n  {excerpt}...")

    return "Projects:\n\n" + "\n\n".join(results)


# --- Tool 8: mem_context (single-call session loader) ---

@server.tool()
def mem_context(project: str = "") -> str:
    """Load full session context in one call. USE THIS AT SESSION START.

    Returns: project brief + active blockers + recent observations + last session summary.
    This replaces calling mem_brief + mem_recent separately.

    Args:
        project: Project name (auto-detected from CWD if empty).
    """
    proj = detect_project(project)
    sections = []
    conn = get_conn()
    try:
        cur = conn.cursor()

        # 1. Project brief
        cur.execute(
            "SELECT brief, updated_at FROM project_briefs WHERE project = ?",
            (proj,),
        )
        brief_row = cur.fetchone()
        if brief_row:
            sections.append(f"## Brief ({brief_row[1]})\n{brief_row[0]}")
        else:
            sections.append(f"## Brief\nNo brief for '{proj}' yet.")

        # 2. Active blockers
        cur.execute(
            "SELECT id, content, tags, created_at FROM observations "
            "WHERE project = ? AND type = 'blocker' "
            "ORDER BY created_at DESC LIMIT 5",
            (proj,),
        )
        blockers = cur.fetchall()
        if blockers:
            lines = [f"- [#{b[0]}] {b[1]}" for b in blockers]
            sections.append("## Active Blockers\n" + "\n".join(lines))

        # 3. Last session summary
        cur.execute(
            "SELECT session_id, summary, created_at FROM session_summaries "
            "WHERE project = ? ORDER BY created_at DESC LIMIT 1",
            (proj,),
        )
        last_session = cur.fetchone()
        if last_session:
            sections.append(
                f"## Last Session ({last_session[2]}, id: {last_session[0]})\n{last_session[1]}"
            )

        # 4. Recent observations (last 7 days, max 10)
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "SELECT id, type, content, tags, created_at FROM observations "
            "WHERE project = ? AND created_at > ? "
            "ORDER BY created_at DESC LIMIT 10",
            (proj, cutoff),
        )
        recent = cur.fetchall()
        if recent:
            lines = []
            for r in recent:
                tag_str = f" [{r[3]}]" if r[3] else ""
                lines.append(f"- [#{r[0]}] [{r[1]}]{tag_str} {r[2][:200]}")
            sections.append("## Recent (7d)\n" + "\n".join(lines))

        # 5. Most accessed observations (top knowledge)
        cur.execute(
            "SELECT id, type, content, accessed_count FROM observations "
            "WHERE project = ? AND accessed_count > 0 "
            "ORDER BY accessed_count DESC LIMIT 5",
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
    """Compress old observations into session summaries and remove duplicates.

    Observations older than days_old with accessed_count=0 are candidates for cleanup.
    High-value observations (blockers, decisions, high access count) are always kept.

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

        # Find cleanup candidates: old, never accessed, not blockers/decisions
        where = "WHERE created_at < ? AND accessed_count = 0 AND type NOT IN ('blocker', 'decision')"
        params: list = [cutoff]
        if proj:
            where += " AND project = ?"
            params.append(proj)

        cur.execute(f"SELECT COUNT(*) FROM observations {where}", params)
        candidate_count = cur.fetchone()[0]

        # Find duplicates (same content, same project)
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

        # Total observations
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
                f"- Cleanup candidates (>{days_old}d old, never accessed, not blocker/decision): {candidate_count}\n"
                f"- Duplicate observations: {dup_count}\n"
                f"- Would remove: {candidate_count + dup_count}\n"
                f"- Would keep: {total - candidate_count - dup_count}\n\n"
                f"Run with dry_run=False to execute."
            )

        # Execute cleanup
        removed = 0

        # Remove duplicates (keep oldest)
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

        # Archive old unused observations: save summary then delete
        cur.execute(
            f"SELECT project, GROUP_CONCAT(content SEPARATOR ' | ') "
            f"FROM observations {where} GROUP BY project",
            params,
        )
        archives = cur.fetchall()
        for arch_proj, combined in archives:
            summary = combined[:2000] if combined else ""
            if summary:
                cur.execute(
                    "INSERT INTO session_summaries (project, session_id, summary) "
                    "VALUES (?, ?, ?)",
                    (arch_proj, f"clean_{SESSION_ID[:4]}", f"[Auto-cleanup] {summary}"),
                )

        # Delete archived observations
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

        # Per-project stats
        cur.execute("""
            SELECT
                project,
                COUNT(*) AS total,
                SUM(type = 'decision') AS decisions,
                SUM(type = 'error') AS errors,
                SUM(type = 'blocker') AS blockers,
                SUM(type = 'discovery') AS discoveries,
                SUM(type = 'progress') AS progress,
                SUM(type = 'note') AS notes,
                MIN(created_at) AS first_obs,
                MAX(created_at) AS last_obs
            FROM observations
            GROUP BY project
            ORDER BY last_obs DESC
        """)
        projects = cur.fetchall()

        # Global stats
        cur.execute("SELECT COUNT(*) FROM observations")
        total_obs = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM session_summaries")
        total_sessions = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM project_briefs")
        total_briefs = cur.fetchone()[0]

        # Most accessed
        cur.execute(
            "SELECT id, project, content, accessed_count FROM observations "
            "WHERE accessed_count > 0 ORDER BY accessed_count DESC LIMIT 5"
        )
        top_accessed = cur.fetchall()

        # This week activity
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "SELECT COUNT(*) FROM observations WHERE created_at > ?", (week_ago,)
        )
        this_week = cur.fetchone()[0]

        # DB size
        cur.execute("""
            SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS size_mb
            FROM information_schema.tables
            WHERE table_schema = 'claude_memory'
        """)
        db_size = cur.fetchone()[0] or 0

    finally:
        conn.close()

    lines = [
        f"# Memory Stats",
        f"",
        f"**Global:** {total_obs} observations, {total_sessions} sessions, {total_briefs} briefs",
        f"**This week:** {this_week} new observations",
        f"**DB size:** {db_size} MB",
        f"",
        f"## Per Project",
    ]

    for p in projects:
        proj, total, dec, err, blk, disc, prog, notes, first, last = p
        lines.append(
            f"- **{proj}** — {total} obs "
            f"(D:{dec} E:{err} B:{blk} Di:{disc} P:{prog} N:{notes}) "
            f"| {first} → {last}"
        )

    if top_accessed:
        lines.append("\n## Most Accessed")
        for t in top_accessed:
            lines.append(f"- [#{t[0]}] [{t[1]}] ({t[3]}x) {t[2][:100]}")

    return "\n".join(lines)


# --- Tool 11: mem_update ---

@server.tool()
def mem_update(
    id: int,
    content: str = "",
    tags: str = "",
    type: str = "",
) -> str:
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

        cur.execute(
            "SELECT id, project, content, tags, type FROM observations WHERE id = ?",
            (id,),
        )
        row = cur.fetchone()
        if not row:
            return f"Observation #{id} not found."

        updates = []
        params = []
        if content:
            updates.append("content = ?")
            params.append(content)
        if tags:
            updates.append("tags = ?")
            params.append(normalize_tags(tags))
        if type:
            valid_types = ("decision", "error", "discovery", "progress", "blocker", "note")
            if type not in valid_types:
                return f"Invalid type '{type}'. Valid: {', '.join(valid_types)}"
            updates.append("type = ?")
            params.append(type)

        params.append(id)
        cur.execute(
            f"UPDATE observations SET {', '.join(updates)} WHERE id = ?",
            params,
        )
    finally:
        conn.close()

    changed = []
    if content:
        changed.append("content")
    if tags:
        changed.append("tags")
    if type:
        changed.append("type")
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
        cur.execute(
            "SELECT id, project, type, content FROM observations WHERE id = ?", (id,)
        )
        row = cur.fetchone()
        if not row:
            return f"Observation #{id} not found."

        # Unlink children before deleting
        cur.execute(
            "UPDATE observations SET parent_id = NULL WHERE parent_id = ?", (id,)
        )
        cur.execute("DELETE FROM observations WHERE id = ?", (id,))
    finally:
        conn.close()

    preview = row[3][:100]
    return f"Deleted #{id} [{row[2]}] from '{row[1]}': {preview}"


# --- Main ---

if __name__ == "__main__":
    server.run(transport="stdio")
