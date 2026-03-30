#!/usr/bin/env python3
"""
Claude Memory — Session Startup Display
Generates formatted context summary for the SessionStart hook.
Replaces the need to call mem_context() manually.
Version: 3.0
"""

import os
import sys
from datetime import datetime, timedelta

try:
    import mariadb
except ImportError:
    print("⚠ claude-memory: mariadb connector not available")
    sys.exit(0)

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

TYPE_EMOJI = {
    "decision": "⚖️",
    "error": "🔴",
    "discovery": "🔵",
    "progress": "✅",
    "blocker": "🚫",
    "note": "📝",
}

# Priority order for smart sorting (lower = higher priority)
TYPE_PRIORITY = {
    "blocker": 0,
    "error": 1,
    "decision": 2,
    "discovery": 3,
    "progress": 4,
    "note": 5,
}

SEPARATOR = "────────────────────────────────────────────────────────────────────────"


def detect_project() -> str:
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


def main():
    try:
        conn = mariadb.connect(**DB_CONFIG)
    except mariadb.Error as e:
        print(f"⚠ claude-memory: DB unavailable — {e}")
        return

    try:
        cur = conn.cursor()
        project = detect_project()
        now = datetime.now()
        week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        out = []

        # ── Global Stats ──
        cur.execute(
            "SELECT "
            "  (SELECT COUNT(*) FROM observations),"
            "  (SELECT COUNT(*) FROM session_summaries),"
            "  (SELECT COUNT(DISTINCT project) FROM observations),"
            "  (SELECT ROUND(SUM(data_length + index_length)/1024/1024, 2) "
            "   FROM information_schema.tables WHERE table_schema='claude_memory'),"
            "  (SELECT COUNT(*) FROM observations WHERE created_at > ?)",
            (week_ago,),
        )
        total_obs, total_sess, total_proj, db_size, week_count = cur.fetchone()
        db_size = db_size or 0

        # ── Header ──
        ts = now.strftime("%Y-%m-%d %H:%M")
        out.append(f"# 🧠 Claude Memory — {project}")
        out.append(
            f"mich · {ts} · {total_obs} obs · {total_sess} sessions · "
            f"{total_proj} projects · {db_size} MB · +{week_count} this week"
        )
        out.append("")
        out.append("⚖️ decision  🔴 error  🔵 discovery  ✅ progress  🚫 blocker  📝 note")
        out.append(SEPARATOR)

        # ── Brief ──
        cur.execute(
            "SELECT brief, updated_at FROM project_briefs WHERE project = ?",
            (project,),
        )
        brief_row = cur.fetchone()
        if brief_row:
            brief_text = brief_row[0]
            if len(brief_text) > 600:
                brief_text = brief_text[:600] + "…"
            out.append("")
            out.append(f"## Brief ({brief_row[1].strftime('%b %d %H:%M')})")
            out.append(brief_text)

        # ── Blockers ──
        cur.execute(
            "SELECT id, content, tags FROM observations "
            "WHERE project = ? AND type = 'blocker' "
            "ORDER BY created_at DESC LIMIT 5",
            (project,),
        )
        blockers = cur.fetchall()
        if blockers:
            out.append("")
            out.append(f"## 🚫 Blockers ({len(blockers)})")
            for b_id, b_content, b_tags in blockers:
                preview = b_content[:150].replace("\n", " ")
                tag = f" [{b_tags}]" if b_tags else ""
                out.append(f"  #{b_id}  {preview}{tag}")

        # ── Timeline (7d) — smart priority sort ──
        cur.execute(
            "SELECT id, type, content, tags, created_at FROM observations "
            "WHERE project = ? AND created_at > ? "
            "ORDER BY DATE(created_at) DESC, "
            "FIELD(type, 'blocker', 'error', 'decision', 'discovery', 'progress', 'note'), "
            "created_at DESC "
            "LIMIT 20",
            (project, week_ago),
        )
        recent = cur.fetchall()
        if recent:
            out.append("")
            out.append(f"## Timeline (7d, {len(recent)} obs)")
            current_date = None
            for obs_id, obs_type, content, tags, created in recent:
                obs_date = created.strftime("%b %d")
                if obs_date != current_date:
                    current_date = obs_date
                    out.append(f"  {obs_date}")
                emoji = TYPE_EMOJI.get(obs_type, "📝")
                time_str = created.strftime("%H:%M")
                preview = content[:120].replace("\n", " ")
                if len(content) > 120:
                    preview += "…"
                out.append(f"    #{obs_id:<5} {time_str}  {emoji}  {preview}")

        # ── Last Session ──
        cur.execute(
            "SELECT session_id, summary, created_at FROM session_summaries "
            "WHERE project = ? ORDER BY created_at DESC LIMIT 1",
            (project,),
        )
        last = cur.fetchone()
        if last:
            sess_id, summary, created = last
            out.append("")
            out.append(f"## Last Session ({created.strftime('%b %d %H:%M')}, {sess_id})")
            if len(summary) > 400:
                summary = summary[:400] + "…"
            out.append(summary)

        # ── Top Knowledge ──
        cur.execute(
            "SELECT id, type, content, accessed_count FROM observations "
            "WHERE project = ? AND accessed_count > 0 "
            "ORDER BY accessed_count DESC LIMIT 5",
            (project,),
        )
        top = cur.fetchall()
        if top:
            out.append("")
            out.append("## Top Knowledge")
            for t_id, t_type, t_content, t_count in top:
                emoji = TYPE_EMOJI.get(t_type, "📝")
                preview = t_content[:100].replace("\n", " ")
                out.append(f"  #{t_id:<5} ({t_count}×)  {emoji}  {preview}")

        # ── Other Projects ──
        cur.execute(
            "SELECT project, COUNT(*) AS cnt, MAX(created_at) AS last_act "
            "FROM observations WHERE project != ? "
            "GROUP BY project ORDER BY last_act DESC LIMIT 5",
            (project,),
        )
        others = cur.fetchall()
        if others:
            out.append("")
            out.append("## Other Projects")
            for o_proj, o_cnt, o_last in others:
                out.append(f"  {o_proj} — {o_cnt} obs, last {o_last.strftime('%b %d')}")

        # ── Footer ──
        out.append("")
        out.append(SEPARATOR)
        out.append(
            "12 tools | mem_save · mem_search · mem_context · mem_stats · "
            "mem_update · mem_delete"
        )
        out.append("✓ Context loaded — skip mem_context() this session")

        print("\n".join(out))

    finally:
        conn.close()


if __name__ == "__main__":
    main()
