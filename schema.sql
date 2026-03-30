-- Claude Memory Server — MariaDB Schema
-- Three-tier persistent memory for Claude Code
-- Version: 3.1 (2026-03-30)

CREATE DATABASE IF NOT EXISTS claude_memory
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE claude_memory;

-- Tier 1: Raw observations (everything gets saved here)
CREATE TABLE IF NOT EXISTS observations (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    session_id VARCHAR(12) NOT NULL,
    type ENUM('decision','error','discovery','progress','blocker','note') NOT NULL DEFAULT 'note',
    content TEXT NOT NULL,
    tags VARCHAR(500) DEFAULT '',
    parent_id BIGINT UNSIGNED DEFAULT NULL,
    accessed_count INT UNSIGNED NOT NULL DEFAULT 0,
    last_accessed DATETIME DEFAULT NULL,
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_project (project),
    INDEX idx_session (session_id),
    INDEX idx_type (type),
    INDEX idx_created (created_at DESC),
    INDEX idx_parent (parent_id),
    INDEX idx_pinned (pinned),
    INDEX idx_project_content (project, content(255)),
    FULLTEXT idx_ft_content (content, tags),
    CONSTRAINT fk_parent FOREIGN KEY (parent_id) REFERENCES observations(id) ON DELETE SET NULL
) ENGINE=InnoDB PAGE_COMPRESSED=1;

-- Tier 2: Session summaries (compressed, per-session)
CREATE TABLE IF NOT EXISTS session_summaries (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    session_id VARCHAR(12) NOT NULL,
    summary TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_project (project),
    INDEX idx_session (session_id),
    INDEX idx_created (created_at DESC),
    FULLTEXT idx_ft_summary (summary)
) ENGINE=InnoDB PAGE_COMPRESSED=1;

-- Tier 3: Project briefs (always-current, small)
CREATE TABLE IF NOT EXISTS project_briefs (
    project VARCHAR(100) PRIMARY KEY,
    brief TEXT NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Tier 4: Structured credentials (per-project, per-service)
CREATE TABLE IF NOT EXISTS credentials (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    project VARCHAR(100) NOT NULL,
    service VARCHAR(100) NOT NULL,
    host VARCHAR(255) DEFAULT '',
    port INT UNSIGNED DEFAULT NULL,
    username VARCHAR(255) DEFAULT '',
    password VARCHAR(500) DEFAULT '',
    extra TEXT DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_project (project),
    UNIQUE KEY uk_project_service (project, service)
) ENGINE=InnoDB;

-- ============================================================
-- Migration v3.0 -> v3.1 (2026-03-30)
-- ============================================================
-- ALTER TABLE observations ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT FALSE AFTER last_accessed;
-- ALTER TABLE observations ADD INDEX idx_pinned (pinned);
-- CREATE TABLE credentials (see above);

-- ============================================================
-- Migration v2 -> v3.0 (2026-03-30)
-- ============================================================
-- ALTER TABLE observations MODIFY session_id VARCHAR(12) NOT NULL;
-- ALTER TABLE session_summaries MODIFY session_id VARCHAR(12) NOT NULL;
-- ALTER TABLE observations ADD INDEX idx_project_content (project, content(255));

-- ============================================================
-- Migration v1 -> v2 (2026-03-28)
-- ============================================================
-- ALTER TABLE observations ADD COLUMN parent_id BIGINT UNSIGNED DEFAULT NULL AFTER tags;
-- ALTER TABLE observations ADD COLUMN accessed_count INT UNSIGNED NOT NULL DEFAULT 0 AFTER parent_id;
-- ALTER TABLE observations ADD COLUMN last_accessed DATETIME DEFAULT NULL AFTER accessed_count;
-- ALTER TABLE observations ADD INDEX idx_parent (parent_id);
-- ALTER TABLE session_summaries ADD INDEX idx_session (session_id);
