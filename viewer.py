#!/usr/bin/env python3
"""
Claude Memory Viewer — Web UI for browsing persistent memory.
Run: python3 viewer.py [--port 8899]
Open: http://localhost:8899
Version: 3.0 — Full CRUD + Export
"""

import json
import sys
import mariadb
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "claude",
    "password": "claude_mem_2026",
    "database": "claude_memory",
}

PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8899


def get_conn():
    return mariadb.connect(**DB_CONFIG)


def query(sql, params=()):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def execute(sql, params=()):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def json_serial(obj):
    from datetime import datetime
    from decimal import Decimal
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Type {type(obj)} not serializable")


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Memory</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {
    --bg-base: #09090b; --bg-card: #111113; --bg-elevated: #18181b;
    --bg-hover: #1e1e22; --bg-input: #0f0f11;
    --border: rgba(255,255,255,0.06); --border-hover: rgba(255,255,255,0.12);
    --text: #fafafa; --text-secondary: #a1a1aa; --text-muted: #52525b;
    --accent: #818cf8; --accent-glow: rgba(129,140,248,0.15);
    --green: #34d399; --green-bg: rgba(52,211,153,0.1);
    --orange: #fbbf24; --orange-bg: rgba(251,191,36,0.1);
    --red: #f87171; --red-bg: rgba(248,113,113,0.1);
    --purple: #c084fc; --purple-bg: rgba(192,132,252,0.1);
    --blue: #60a5fa; --blue-bg: rgba(96,165,250,0.1);
    --cyan: #22d3ee; --cyan-bg: rgba(34,211,238,0.08);
    --radius: 12px; --radius-sm: 8px; --radius-xs: 6px;
    --shadow: 0 1px 3px rgba(0,0,0,0.4), 0 1px 2px rgba(0,0,0,0.3);
    --shadow-lg: 0 10px 30px rgba(0,0,0,0.5);
    --transition: 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg-base); color: var(--text); line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }

  /* Layout */
  .layout { display: flex; min-height: 100vh; }
  .sidebar {
    width: 260px; background: var(--bg-card); border-right: 1px solid var(--border);
    display: flex; flex-direction: column; position: fixed; top: 0; left: 0;
    height: 100vh; z-index: 10;
  }
  .main { flex: 1; margin-left: 260px; min-height: 100vh; }
  .content { max-width: 960px; margin: 0 auto; padding: 32px 40px; }

  /* Sidebar */
  .sidebar-header { padding: 24px 20px 20px; border-bottom: 1px solid var(--border); }
  .logo { display: flex; align-items: center; gap: 10px; }
  .logo-icon {
    width: 32px; height: 32px; border-radius: 8px;
    background: linear-gradient(135deg, var(--accent), var(--purple));
    display: flex; align-items: center; justify-content: center;
    font-size: 16px; font-weight: 700; color: #fff;
  }
  .logo-text { font-size: 15px; font-weight: 600; letter-spacing: -0.3px; }
  .logo-sub { font-size: 11px; color: var(--text-muted); margin-top: 2px; }

  .sidebar-nav { padding: 12px 8px; flex: 1; overflow-y: auto; }
  .nav-section { margin-bottom: 8px; }
  .nav-section-title {
    font-size: 11px; font-weight: 500; color: var(--text-muted);
    text-transform: uppercase; letter-spacing: 0.5px; padding: 8px 12px 4px;
  }
  .nav-item {
    display: flex; align-items: center; gap: 10px; padding: 8px 12px;
    border-radius: var(--radius-sm); cursor: pointer; font-size: 13px;
    color: var(--text-secondary); transition: all var(--transition); margin: 1px 0;
  }
  .nav-item:hover { background: var(--bg-hover); color: var(--text); }
  .nav-item.active { background: var(--accent-glow); color: var(--accent); }
  .nav-item svg { width: 16px; height: 16px; opacity: 0.7; flex-shrink: 0; }
  .nav-item.active svg { opacity: 1; }
  .nav-item .badge {
    margin-left: auto; font-size: 11px; font-weight: 500;
    background: var(--bg-elevated); padding: 1px 7px; border-radius: 10px;
    color: var(--text-muted);
  }
  .nav-item.active .badge { background: var(--accent-glow); color: var(--accent); }

  .sidebar-projects { padding: 0 8px 16px; overflow-y: auto; }
  .project-nav-item {
    display: flex; align-items: center; gap: 8px; padding: 7px 12px;
    border-radius: var(--radius-xs); cursor: pointer; font-size: 13px;
    color: var(--text-secondary); transition: all var(--transition);
  }
  .project-nav-item:hover { background: var(--bg-hover); color: var(--text); }
  .project-nav-item.active { background: var(--accent-glow); color: var(--accent); }
  .project-dot {
    width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
    background: var(--text-muted);
  }
  .project-nav-item.active .project-dot { background: var(--accent); }
  .project-nav-item .p-count {
    margin-left: auto; font-size: 11px; color: var(--text-muted);
    font-family: 'JetBrains Mono', monospace;
  }

  .sidebar-footer {
    padding: 16px 20px; border-top: 1px solid var(--border);
    font-size: 11px; color: var(--text-muted);
  }

  /* Top bar */
  .topbar {
    display: flex; align-items: center; gap: 16px;
    padding: 20px 40px; border-bottom: 1px solid var(--border);
    background: rgba(9,9,11,0.8); backdrop-filter: blur(12px);
    position: sticky; top: 0; z-index: 5; margin-left: 260px;
  }
  .topbar-title { font-size: 18px; font-weight: 600; letter-spacing: -0.3px; }
  .topbar-title span { color: var(--text-muted); font-weight: 400; }

  .search-wrapper { flex: 1; max-width: 420px; position: relative; }
  .search-wrapper svg {
    position: absolute; left: 12px; top: 50%; transform: translateY(-50%);
    width: 16px; height: 16px; color: var(--text-muted); pointer-events: none;
  }
  .search-input {
    width: 100%; padding: 8px 12px 8px 36px; background: var(--bg-input);
    border: 1px solid var(--border); border-radius: var(--radius-sm);
    color: var(--text); font-size: 13px; font-family: inherit;
    outline: none; transition: all var(--transition);
  }
  .search-input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow); }
  .search-input::placeholder { color: var(--text-muted); }
  .search-kbd {
    position: absolute; right: 10px; top: 50%; transform: translateY(-50%);
    font-size: 11px; color: var(--text-muted); background: var(--bg-elevated);
    padding: 2px 6px; border-radius: 4px; border: 1px solid var(--border);
    font-family: 'JetBrains Mono', monospace; pointer-events: none;
  }

  .topbar-actions { display: flex; gap: 8px; margin-left: auto; }
  .btn {
    padding: 7px 14px; border-radius: var(--radius-sm); font-size: 12px;
    font-weight: 500; cursor: pointer; transition: all var(--transition);
    border: 1px solid var(--border); font-family: inherit;
  }
  .btn-ghost {
    background: transparent; color: var(--text-secondary);
  }
  .btn-ghost:hover { background: var(--bg-hover); color: var(--text); }
  .btn-primary {
    background: var(--accent); color: #fff; border-color: var(--accent);
  }
  .btn-primary:hover { opacity: 0.9; }
  .btn-danger {
    background: var(--red-bg); color: var(--red); border-color: rgba(248,113,113,0.2);
  }
  .btn-danger:hover { background: rgba(248,113,113,0.2); }
  .btn-sm { padding: 4px 10px; font-size: 11px; }

  /* Stats cards */
  .stats-grid {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 32px;
  }
  .stat-card {
    background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 16px 20px; transition: all var(--transition);
  }
  .stat-card:hover { border-color: var(--border-hover); }
  .stat-label { font-size: 12px; color: var(--text-muted); margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.3px; }
  .stat-value { font-size: 28px; font-weight: 700; letter-spacing: -1px; }
  .stat-value.accent { color: var(--accent); }
  .stat-value.green { color: var(--green); }
  .stat-value.purple { color: var(--purple); }
  .stat-value.orange { color: var(--orange); }
  .stat-sub { font-size: 11px; color: var(--text-muted); margin-top: 2px; }

  /* Section headers */
  .section-header {
    display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px;
  }
  .section-title { font-size: 14px; font-weight: 600; letter-spacing: -0.2px; }
  .section-count {
    font-size: 12px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace;
  }

  /* Project cards */
  .projects-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 12px; margin-bottom: 32px;
  }
  .project-card {
    background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 20px; cursor: pointer; transition: all var(--transition);
    position: relative; overflow: hidden;
  }
  .project-card::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, var(--accent), var(--purple));
    opacity: 0; transition: opacity var(--transition);
  }
  .project-card:hover { border-color: var(--border-hover); transform: translateY(-1px); box-shadow: var(--shadow); }
  .project-card:hover::before { opacity: 1; }
  .project-card h3 { font-size: 15px; font-weight: 600; margin-bottom: 6px; letter-spacing: -0.2px; }
  .project-card .card-meta {
    display: flex; align-items: center; gap: 12px;
    color: var(--text-muted); font-size: 12px; margin-bottom: 12px;
  }
  .project-card .card-meta span { display: flex; align-items: center; gap: 4px; }
  .project-card .card-meta svg { width: 12px; height: 12px; }
  .project-card .card-excerpt {
    color: var(--text-secondary); font-size: 13px; line-height: 1.5;
    display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
  }

  /* Brief panel */
  .brief-panel { display: none; }
  .brief-panel.visible { display: block; }
  .brief-back {
    display: inline-flex; align-items: center; gap: 6px;
    color: var(--text-muted); font-size: 13px; cursor: pointer;
    margin-bottom: 20px; transition: color var(--transition);
  }
  .brief-back:hover { color: var(--accent); }
  .brief-back svg { width: 16px; height: 16px; }
  .brief-title { font-size: 22px; font-weight: 700; letter-spacing: -0.5px; margin-bottom: 16px; }
  .brief-content {
    background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 24px; margin-bottom: 24px; white-space: pre-wrap;
    font-size: 14px; line-height: 1.8; color: var(--text-secondary);
  }

  /* Observations */
  .obs-list { display: flex; flex-direction: column; gap: 6px; }
  .obs-item {
    background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 14px 18px; transition: all var(--transition); position: relative;
  }
  .obs-item:hover { border-color: var(--border-hover); }
  .obs-header {
    display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap;
  }
  .obs-id {
    font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--text-muted);
  }
  .obs-project {
    font-size: 11px; font-weight: 500; padding: 2px 8px;
    border-radius: 20px; background: var(--cyan-bg); color: var(--cyan);
  }
  .pill {
    font-size: 11px; font-weight: 500; padding: 2px 10px;
    border-radius: 20px; display: inline-flex; align-items: center; gap: 4px;
  }
  .pill.decision { background: var(--green-bg); color: var(--green); }
  .pill.error { background: var(--red-bg); color: var(--red); }
  .pill.discovery { background: var(--purple-bg); color: var(--purple); }
  .pill.progress { background: var(--blue-bg); color: var(--blue); }
  .pill.blocker { background: var(--orange-bg); color: var(--orange); }
  .pill.note { background: rgba(255,255,255,0.04); color: var(--text-muted); }
  .obs-time {
    margin-left: auto; font-size: 11px; color: var(--text-muted);
    font-family: 'JetBrains Mono', monospace; white-space: nowrap;
  }
  .obs-actions {
    display: none; gap: 4px; margin-left: 8px;
  }
  .obs-item:hover .obs-actions { display: flex; }
  .obs-content { font-size: 13px; line-height: 1.6; color: var(--text-secondary); white-space: pre-wrap; }
  .obs-tags { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 4px; }
  .tag {
    font-size: 11px; padding: 1px 8px; border-radius: 4px;
    background: rgba(255,255,255,0.04); color: var(--text-muted);
    font-family: 'JetBrains Mono', monospace;
  }

  /* Sessions */
  .session-item {
    background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 16px 20px; margin-bottom: 8px; transition: all var(--transition);
  }
  .session-item:hover { border-color: var(--border-hover); }
  .session-header { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
  .session-id {
    font-family: 'JetBrains Mono', monospace; font-size: 12px;
    color: var(--accent); background: var(--accent-glow);
    padding: 2px 8px; border-radius: 4px;
  }
  .session-date { font-size: 12px; color: var(--text-muted); }
  .session-summary { font-size: 13px; color: var(--text-secondary); line-height: 1.6; }

  /* Modal */
  .modal-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,0.7);
    display: flex; align-items: center; justify-content: center; z-index: 100;
    backdrop-filter: blur(4px);
  }
  .modal {
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: var(--radius); width: 560px; max-width: 90vw;
    max-height: 80vh; overflow-y: auto; box-shadow: var(--shadow-lg);
  }
  .modal-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 20px 24px; border-bottom: 1px solid var(--border);
  }
  .modal-title { font-size: 16px; font-weight: 600; }
  .modal-close {
    width: 28px; height: 28px; display: flex; align-items: center;
    justify-content: center; border-radius: var(--radius-xs); cursor: pointer;
    color: var(--text-muted); font-size: 18px; transition: all var(--transition);
  }
  .modal-close:hover { background: var(--bg-hover); color: var(--text); }
  .modal-body { padding: 20px 24px; }
  .modal-footer {
    display: flex; justify-content: flex-end; gap: 8px;
    padding: 16px 24px; border-top: 1px solid var(--border);
  }
  .form-group { margin-bottom: 16px; }
  .form-group label {
    display: block; font-size: 12px; font-weight: 500; color: var(--text-secondary);
    margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.3px;
  }
  .form-input, .form-select, .form-textarea {
    width: 100%; padding: 8px 12px; background: var(--bg-input);
    border: 1px solid var(--border); border-radius: var(--radius-xs);
    color: var(--text); font-size: 13px; font-family: inherit;
    outline: none; transition: all var(--transition);
  }
  .form-input:focus, .form-select:focus, .form-textarea:focus {
    border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow);
  }
  .form-textarea {
    resize: vertical; min-height: 120px;
    font-family: 'JetBrains Mono', monospace; font-size: 12px; line-height: 1.6;
  }
  .form-select { cursor: pointer; }
  .form-select option { background: var(--bg-card); }

  /* Confirm dialog */
  .confirm-body { text-align: center; padding: 24px; }
  .confirm-body p { color: var(--text-secondary); margin-bottom: 8px; }
  .confirm-body .confirm-id { font-family: 'JetBrains Mono', monospace; color: var(--red); }
  .confirm-body .confirm-preview {
    font-size: 12px; color: var(--text-muted); margin-top: 12px;
    padding: 12px; background: var(--bg-base); border-radius: var(--radius-xs);
    max-height: 100px; overflow-y: auto; text-align: left;
  }

  /* Toast */
  .toast {
    position: fixed; bottom: 24px; right: 24px; background: var(--bg-card);
    border: 1px solid var(--border); border-radius: var(--radius-sm);
    padding: 12px 20px; font-size: 13px; color: var(--text);
    box-shadow: var(--shadow-lg); z-index: 200; opacity: 0;
    transform: translateY(10px); transition: all 0.3s ease;
  }
  .toast.show { opacity: 1; transform: translateY(0); }
  .toast.success { border-left: 3px solid var(--green); }
  .toast.error { border-left: 3px solid var(--red); }

  /* Empty state */
  .empty-state { text-align: center; padding: 60px 20px; color: var(--text-muted); }
  .empty-state svg { width: 48px; height: 48px; margin-bottom: 16px; opacity: 0.3; }
  .empty-state p { font-size: 14px; }

  /* Loading */
  .loading { display: flex; justify-content: center; padding: 40px; }
  .spinner {
    width: 24px; height: 24px; border: 2px solid var(--border);
    border-top-color: var(--accent); border-radius: 50%;
    animation: spin 0.6s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
  .fade-in { animation: fadeIn 0.25s ease-out; }

  @media (max-width: 900px) {
    .sidebar { display: none; }
    .main { margin-left: 0; }
    .topbar { margin-left: 0; }
    .stats-grid { grid-template-columns: repeat(2, 1fr); }
    .content { padding: 20px; }
    .topbar { padding: 16px 20px; }
  }
</style>
</head>
<body>
<div class="layout">
  <!-- Sidebar -->
  <aside class="sidebar">
    <div class="sidebar-header">
      <div class="logo">
        <div class="logo-icon">M</div>
        <div>
          <div class="logo-text">Claude Memory</div>
          <div class="logo-sub">v3.0 — Full CRUD</div>
        </div>
      </div>
    </div>
    <nav class="sidebar-nav">
      <div class="nav-section">
        <div class="nav-item active" data-view="dashboard" onclick="navigate('dashboard')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
          Dashboard
        </div>
        <div class="nav-item" data-view="timeline" onclick="navigate('timeline')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          Timeline
        </div>
        <div class="nav-item" data-view="sessions" onclick="navigate('sessions')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
          Sessions
          <span class="badge" id="sessions-badge">0</span>
        </div>
      </div>
      <div class="nav-section">
        <div class="nav-section-title">Projects</div>
        <div id="sidebar-projects"></div>
      </div>
    </nav>
    <div class="sidebar-footer" id="sidebar-footer">Loading...</div>
  </aside>

  <!-- Main -->
  <div class="main">
    <div class="topbar">
      <div class="topbar-title" id="topbar-title">Dashboard</div>
      <div class="search-wrapper">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input type="text" class="search-input" id="searchInput" placeholder="Search observations...">
        <span class="search-kbd">/</span>
      </div>
      <div class="topbar-actions">
        <button class="btn btn-ghost" onclick="exportData()" title="Export all data as JSON">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle;margin-right:4px"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          Export
        </button>
      </div>
    </div>

    <div class="content">
      <!-- Dashboard view -->
      <div id="view-dashboard" class="view fade-in">
        <div class="stats-grid" id="stats-grid"></div>
        <div class="section-header">
          <div class="section-title">Projects</div>
          <div class="section-count" id="project-count"></div>
        </div>
        <div class="projects-grid" id="projects-grid"></div>
        <div class="section-header">
          <div class="section-title">Recent Activity</div>
        </div>
        <div class="obs-list" id="recent-list"></div>
      </div>

      <!-- Timeline view -->
      <div id="view-timeline" class="view" style="display:none;">
        <div class="section-header">
          <div class="section-title">All Observations</div>
          <div class="section-count" id="timeline-count"></div>
        </div>
        <div class="obs-list" id="timeline-list"></div>
      </div>

      <!-- Sessions view -->
      <div id="view-sessions" class="view" style="display:none;">
        <div class="section-header">
          <div class="section-title">Session Summaries</div>
          <div class="section-count" id="session-count"></div>
        </div>
        <div id="sessions-list"></div>
      </div>

      <!-- Project detail view -->
      <div id="view-project" class="view" style="display:none;">
        <div class="brief-back" onclick="navigate('dashboard')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
          Back to projects
        </div>
        <div class="brief-title" id="project-title"></div>
        <div class="brief-content" id="project-brief"></div>
        <div class="section-header">
          <div class="section-title">Observations</div>
          <div class="section-count" id="project-obs-count"></div>
        </div>
        <div class="obs-list" id="project-obs"></div>
      </div>

      <!-- Search results view -->
      <div id="view-search" class="view" style="display:none;">
        <div class="brief-back" onclick="navigate(lastView || 'dashboard')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
          Back
        </div>
        <div class="section-header">
          <div class="section-title" id="search-title">Search Results</div>
          <div class="section-count" id="search-count"></div>
        </div>
        <div class="obs-list" id="search-list"></div>
      </div>
    </div>
  </div>
</div>

<!-- Edit Modal -->
<div id="edit-modal" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeEditModal()">
  <div class="modal">
    <div class="modal-header">
      <span class="modal-title">Edit Observation <span id="edit-modal-id" style="color:var(--text-muted);font-family:'JetBrains Mono',monospace;font-size:13px;"></span></span>
      <span class="modal-close" onclick="closeEditModal()">&times;</span>
    </div>
    <div class="modal-body">
      <input type="hidden" id="edit-id">
      <div class="form-group">
        <label>Type</label>
        <select id="edit-type" class="form-select">
          <option value="note">note</option>
          <option value="decision">decision</option>
          <option value="error">error</option>
          <option value="discovery">discovery</option>
          <option value="progress">progress</option>
          <option value="blocker">blocker</option>
        </select>
      </div>
      <div class="form-group">
        <label>Tags</label>
        <input type="text" id="edit-tags" class="form-input" placeholder="tag1, tag2, tag3">
      </div>
      <div class="form-group">
        <label>Content</label>
        <textarea id="edit-content" class="form-textarea" rows="8"></textarea>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-ghost" onclick="closeEditModal()">Cancel</button>
      <button class="btn btn-primary" onclick="saveEdit()">Save Changes</button>
    </div>
  </div>
</div>

<!-- Delete Confirm Modal -->
<div id="delete-modal" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeDeleteModal()">
  <div class="modal" style="width:420px;">
    <div class="modal-header">
      <span class="modal-title">Delete Observation</span>
      <span class="modal-close" onclick="closeDeleteModal()">&times;</span>
    </div>
    <div class="confirm-body">
      <p>Are you sure you want to delete observation</p>
      <p class="confirm-id" id="delete-modal-id"></p>
      <div class="confirm-preview" id="delete-modal-preview"></div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-ghost" onclick="closeDeleteModal()">Cancel</button>
      <button class="btn btn-danger" onclick="confirmDelete()">Delete</button>
    </div>
  </div>
</div>

<!-- Toast -->
<div id="toast" class="toast"></div>

<script>
let lastView = 'dashboard';
let allProjects = [];
let pendingDeleteId = null;

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  return r.json();
}

function esc(s) {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function timeAgo(dateStr) {
  const d = new Date(dateStr);
  const now = new Date();
  const diff = Math.floor((now - d) / 1000);
  if (diff < 60) return 'now';
  if (diff < 3600) return Math.floor(diff/60) + 'm ago';
  if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
  if (diff < 604800) return Math.floor(diff/86400) + 'd ago';
  return d.toLocaleDateString('en', { month: 'short', day: 'numeric' });
}

function showToast(msg, type = 'success') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + type;
  setTimeout(() => t.className = 'toast', 3000);
}

function renderObs(obs) {
  if (!obs.length) return `
    <div class="empty-state">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><line x1="8" y1="12" x2="16" y2="12"/></svg>
      <p>No observations found</p>
    </div>`;
  return obs.map(o => {
    const tags = o.tags ? o.tags.split(',').map(t =>
      `<span class="tag">${esc(t.trim())}</span>`).join('') : '';
    const data = esc(JSON.stringify(o)).replace(/'/g, '&#39;');
    return `
    <div class="obs-item fade-in" id="obs-${o.id}">
      <div class="obs-header">
        <span class="obs-id">#${o.id}</span>
        <span class="obs-project">${esc(o.project)}</span>
        <span class="pill ${o.type}">${o.type}</span>
        <span class="obs-actions">
          <button class="btn btn-ghost btn-sm" onclick='openEditModal(${JSON.stringify(o).replace(/'/g,"&#39;")})' title="Edit">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          </button>
          <button class="btn btn-danger btn-sm" onclick="openDeleteModal(${o.id}, '${esc(o.content.substring(0,80)).replace(/'/g,"&#39;")}')" title="Delete">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
          </button>
        </span>
        <span class="obs-time">${timeAgo(o.created_at)}</span>
      </div>
      <div class="obs-content">${esc(o.content)}</div>
      ${tags ? `<div class="obs-tags">${tags}</div>` : ''}
    </div>`;
  }).join('');
}

// ── Edit Modal ──
function openEditModal(obs) {
  document.getElementById('edit-id').value = obs.id;
  document.getElementById('edit-modal-id').textContent = '#' + obs.id;
  document.getElementById('edit-type').value = obs.type;
  document.getElementById('edit-tags').value = obs.tags || '';
  document.getElementById('edit-content').value = obs.content;
  document.getElementById('edit-modal').style.display = 'flex';
}

function closeEditModal() {
  document.getElementById('edit-modal').style.display = 'none';
}

async function saveEdit() {
  const id = document.getElementById('edit-id').value;
  const data = {
    type: document.getElementById('edit-type').value,
    tags: document.getElementById('edit-tags').value,
    content: document.getElementById('edit-content').value,
  };
  try {
    const r = await api(`/api/observations/${id}`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data),
    });
    if (r.ok) {
      showToast(`Observation #${id} updated`);
      closeEditModal();
      refreshCurrentView();
    } else {
      showToast(r.error || 'Update failed', 'error');
    }
  } catch (e) {
    showToast('Network error', 'error');
  }
}

// ── Delete Modal ──
function openDeleteModal(id, preview) {
  pendingDeleteId = id;
  document.getElementById('delete-modal-id').textContent = '#' + id;
  document.getElementById('delete-modal-preview').textContent = preview;
  document.getElementById('delete-modal').style.display = 'flex';
}

function closeDeleteModal() {
  document.getElementById('delete-modal').style.display = 'none';
  pendingDeleteId = null;
}

async function confirmDelete() {
  if (!pendingDeleteId) return;
  const id = pendingDeleteId;
  try {
    const r = await api(`/api/observations/${id}`, { method: 'DELETE' });
    if (r.ok) {
      showToast(`Observation #${id} deleted`);
      closeDeleteModal();
      const el = document.getElementById('obs-' + id);
      if (el) el.remove();
      else refreshCurrentView();
    } else {
      showToast(r.error || 'Delete failed', 'error');
    }
  } catch (e) {
    showToast('Network error', 'error');
  }
}

// ── Export ──
async function exportData() {
  try {
    const data = await api('/api/export');
    const blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `claude-memory-export-${new Date().toISOString().split('T')[0]}.json`;
    a.click();
    URL.revokeObjectURL(url);
    showToast(`Exported ${data.observations.length} observations`);
  } catch (e) {
    showToast('Export failed', 'error');
  }
}

// ── Navigation ──
function navigate(view, param) {
  if (view !== 'search') lastView = view;
  document.querySelectorAll('.view').forEach(v => v.style.display = 'none');
  document.querySelectorAll('.nav-item').forEach(n =>
    n.classList.toggle('active', n.dataset.view === view));
  document.querySelectorAll('.project-nav-item').forEach(n =>
    n.classList.toggle('active', view === 'project' && n.dataset.project === param));

  const el = document.getElementById('view-' + view);
  if (el) { el.style.display = ''; el.classList.add('fade-in'); }

  const titles = {
    dashboard: 'Dashboard', timeline: 'Timeline', sessions: 'Sessions',
    project: param || 'Project', search: 'Search Results'
  };
  document.getElementById('topbar-title').innerHTML = titles[view] || view;

  if (view === 'timeline') loadTimeline();
  if (view === 'sessions') loadSessions();
  if (view === 'project') loadProject(param);
}

function refreshCurrentView() {
  const active = document.querySelector('.nav-item.active');
  if (active) {
    const view = active.dataset.view;
    if (view === 'dashboard') loadDashboard();
    else if (view === 'timeline') loadTimeline();
    else if (view === 'sessions') loadSessions();
  }
  const activeProject = document.querySelector('.project-nav-item.active');
  if (activeProject) loadProject(activeProject.dataset.project);
}

async function loadDashboard() {
  const [projects, recent, stats] = await Promise.all([
    api('/api/projects'), api('/api/recent?limit=10&days=7'), api('/api/stats')
  ]);
  allProjects = projects;

  document.getElementById('stats-grid').innerHTML = `
    <div class="stat-card">
      <div class="stat-label">Projects</div>
      <div class="stat-value accent">${stats.projects || projects.length}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Observations</div>
      <div class="stat-value green">${stats.observations || 0}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Sessions</div>
      <div class="stat-value purple">${stats.sessions || 0}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">This Week</div>
      <div class="stat-value orange">${stats.week_count || 0}</div>
      <div class="stat-sub">observations</div>
    </div>`;

  document.getElementById('project-count').textContent = projects.length + ' total';
  const grid = document.getElementById('projects-grid');
  if (!projects.length) {
    grid.innerHTML = '<div class="empty-state"><p>No projects yet</p></div>';
  } else {
    grid.innerHTML = projects.map(p => `
      <div class="project-card fade-in" onclick="navigate('project','${esc(p.project)}')">
        <h3>${esc(p.project)}</h3>
        <div class="card-meta">
          <span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v6m0 6v6"/></svg> ${p.obs_count}</span>
          ${p.last_activity ? `<span>${timeAgo(p.last_activity)}</span>` : ''}
        </div>
        <div class="card-excerpt">${esc(p.brief_excerpt || 'No brief')}</div>
      </div>`).join('');
  }

  document.getElementById('sidebar-projects').innerHTML = projects.map(p => `
    <div class="project-nav-item" data-project="${esc(p.project)}" onclick="navigate('project','${esc(p.project)}')">
      <span class="project-dot"></span>
      ${esc(p.project)}
      <span class="p-count">${p.obs_count}</span>
    </div>`).join('');

  document.getElementById('recent-list').innerHTML = renderObs(recent);
  document.getElementById('sidebar-footer').textContent =
    `${stats.observations || 0} obs / ${stats.db_size || '?'} MB`;
  document.getElementById('sessions-badge').textContent = stats.sessions || 0;
}

async function loadTimeline() {
  const data = await api('/api/recent?limit=200&days=30');
  document.getElementById('timeline-count').textContent = data.length + ' observations';
  document.getElementById('timeline-list').innerHTML = renderObs(data);
}

async function loadSessions() {
  const data = await api('/api/sessions');
  document.getElementById('session-count').textContent = data.length + ' sessions';
  if (!data.length) {
    document.getElementById('sessions-list').innerHTML =
      '<div class="empty-state"><p>No session summaries yet</p></div>';
    return;
  }
  document.getElementById('sessions-list').innerHTML = data.map(s => `
    <div class="session-item fade-in">
      <div class="session-header">
        <span class="session-id">${esc(s.session_id)}</span>
        <span class="obs-project">${esc(s.project)}</span>
        <span class="session-date">${timeAgo(s.created_at)}</span>
      </div>
      <div class="session-summary">${esc(s.summary)}</div>
    </div>`).join('');
}

async function loadProject(name) {
  const [brief, obs] = await Promise.all([
    api(`/api/brief?project=${encodeURIComponent(name)}`),
    api(`/api/recent?project=${encodeURIComponent(name)}&limit=200`)
  ]);
  document.getElementById('project-title').textContent = name;
  document.getElementById('project-brief').innerHTML = esc(brief.brief || 'No brief yet.');
  document.getElementById('project-obs-count').textContent = obs.length + ' observations';
  document.getElementById('project-obs').innerHTML = renderObs(obs);
}

async function doSearch() {
  const q = document.getElementById('searchInput').value.trim();
  if (!q) return;
  navigate('search');
  document.getElementById('search-title').textContent = `Results for "${q}"`;
  document.getElementById('search-list').innerHTML =
    '<div class="loading"><div class="spinner"></div></div>';
  const data = await api(`/api/search?query=${encodeURIComponent(q)}&limit=50`);
  document.getElementById('search-count').textContent = data.length + ' found';
  document.getElementById('search-list').innerHTML = renderObs(data);
}

// ── Keyboard shortcuts ──
document.addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
    e.preventDefault();
    document.getElementById('searchInput').focus();
  }
  if (e.key === 'Escape') {
    document.getElementById('searchInput').blur();
    closeEditModal();
    closeDeleteModal();
  }
});

document.getElementById('searchInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') doSearch();
});

loadDashboard();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        def param(name, default=""):
            return params.get(name, [default])[0]

        if path == "/" or path == "/index.html":
            self.respond(200, HTML, "text/html")

        elif path == "/api/projects":
            rows = query("""
                SELECT COALESCE(o.project, p.project) AS project,
                       COALESCE(o.obs_count, 0) AS obs_count,
                       o.last_activity,
                       p.brief IS NOT NULL AS has_brief,
                       SUBSTRING(p.brief, 1, 200) AS brief_excerpt
                FROM (
                    SELECT project, COUNT(*) AS obs_count, MAX(created_at) AS last_activity
                    FROM observations GROUP BY project
                ) o
                LEFT JOIN project_briefs p ON p.project = o.project
                UNION
                SELECT p.project, 0 AS obs_count, NULL AS last_activity,
                       1 AS has_brief, SUBSTRING(p.brief, 1, 200) AS brief_excerpt
                FROM project_briefs p
                WHERE p.project NOT IN (SELECT DISTINCT project FROM observations)
                ORDER BY last_activity DESC
            """)
            self.respond_json(rows)

        elif path == "/api/brief":
            project = param("project")
            rows = query("SELECT brief, updated_at FROM project_briefs WHERE project = ?", (project,))
            self.respond_json(rows[0] if rows else {"brief": None})

        elif path == "/api/recent":
            project = param("project")
            limit = int(param("limit", "20"))
            days = int(param("days", "30"))
            if project:
                rows = query(
                    "SELECT id, project, type, content, tags, created_at, session_id "
                    "FROM observations WHERE project = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (project, limit),
                )
            else:
                rows = query(
                    "SELECT id, project, type, content, tags, created_at, session_id "
                    "FROM observations ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            self.respond_json(rows)

        elif path == "/api/stats":
            rows = query("""
                SELECT
                    (SELECT COUNT(*) FROM observations) AS observations,
                    (SELECT COUNT(*) FROM session_summaries) AS sessions,
                    (SELECT COUNT(DISTINCT project) FROM observations) AS projects,
                    (SELECT ROUND(SUM(data_length + index_length)/1024/1024, 2)
                     FROM information_schema.tables WHERE table_schema='claude_memory') AS db_size,
                    (SELECT COUNT(*) FROM observations
                     WHERE created_at > DATE_SUB(NOW(), INTERVAL 7 DAY)) AS week_count
            """)
            self.respond_json(rows[0] if rows else {})

        elif path == "/api/sessions":
            limit = int(param("limit", "50"))
            project = param("project")
            if project:
                rows = query(
                    "SELECT session_id, project, summary, created_at "
                    "FROM session_summaries WHERE project = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (project, limit),
                )
            else:
                rows = query(
                    "SELECT session_id, project, summary, created_at "
                    "FROM session_summaries ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            self.respond_json(rows)

        elif path == "/api/search":
            q = param("query")
            limit = int(param("limit", "20"))
            try:
                rows = query(
                    "SELECT id, project, type, content, tags, created_at, session_id "
                    "FROM observations WHERE MATCH(content, tags) AGAINST(? IN BOOLEAN MODE) "
                    "ORDER BY created_at DESC LIMIT ?",
                    (q, limit),
                )
            except mariadb.Error:
                rows = query(
                    "SELECT id, project, type, content, tags, created_at, session_id "
                    "FROM observations WHERE content LIKE ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (f"%{q}%", limit),
                )
            self.respond_json(rows)

        elif path == "/api/export":
            observations = query(
                "SELECT id, project, session_id, type, content, tags, parent_id, "
                "accessed_count, last_accessed, created_at "
                "FROM observations ORDER BY created_at DESC"
            )
            sessions = query(
                "SELECT id, project, session_id, summary, created_at "
                "FROM session_summaries ORDER BY created_at DESC"
            )
            briefs = query("SELECT project, brief, updated_at FROM project_briefs")
            self.respond_json({
                "exported_at": json_serial(
                    __import__("datetime").datetime.now()
                ),
                "observations": observations,
                "session_summaries": sessions,
                "project_briefs": briefs,
            })

        else:
            self.respond(404, "Not found", "text/plain")

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # PUT /api/observations/<id>
        if path.startswith("/api/observations/"):
            try:
                obs_id = int(path.split("/")[-1])
            except ValueError:
                self.respond_json({"ok": False, "error": "Invalid ID"})
                return

            body = self._read_body()
            if not body:
                self.respond_json({"ok": False, "error": "Empty body"})
                return

            updates = []
            params = []
            if "content" in body and body["content"]:
                updates.append("content = ?")
                params.append(body["content"])
            if "tags" in body:
                tags = body["tags"]
                # Normalize tags
                if tags:
                    parts = [t.strip().lower().replace(" ", "-") for t in tags.split(",")]
                    parts = list(dict.fromkeys(p for p in parts if p))
                    tags = ",".join(sorted(parts))
                updates.append("tags = ?")
                params.append(tags)
            if "type" in body and body["type"]:
                valid = ("decision", "error", "discovery", "progress", "blocker", "note")
                if body["type"] in valid:
                    updates.append("type = ?")
                    params.append(body["type"])

            if not updates:
                self.respond_json({"ok": False, "error": "Nothing to update"})
                return

            params.append(obs_id)
            affected = execute(
                f"UPDATE observations SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            self.respond_json({"ok": affected > 0})
        else:
            self.respond(404, "Not found", "text/plain")

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # DELETE /api/observations/<id>
        if path.startswith("/api/observations/"):
            try:
                obs_id = int(path.split("/")[-1])
            except ValueError:
                self.respond_json({"ok": False, "error": "Invalid ID"})
                return

            # Unlink children
            execute("UPDATE observations SET parent_id = NULL WHERE parent_id = ?", (obs_id,))
            affected = execute("DELETE FROM observations WHERE id = ?", (obs_id,))
            self.respond_json({"ok": affected > 0})
        else:
            self.respond(404, "Not found", "text/plain")

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def respond(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body.encode() if isinstance(body, str) else body)

    def respond_json(self, data):
        self.respond(200, json.dumps(data, default=json_serial), "application/json")

    def do_OPTIONS(self):
        self.respond(204, "", "text/plain")

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    print(f"Claude Memory Viewer running at http://localhost:{PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
