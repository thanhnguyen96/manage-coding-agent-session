from __future__ import annotations

STEP_USER_INPUT = 14

STEP_PLANNER_RESPONSE = 15

STEP_ERROR_MESSAGE = 17

STEP_CHECKPOINT = 23

STEP_SYSTEM_MESSAGE = 101

STEP_GENERIC = 132

STATUS_RUNNING = 2

STATUS_DONE = 3

STATUS_CANCELED = 6

STATUS_ERROR = 7

STATUS_INTERRUPTED = 12

SOURCE_MODEL = 2

SOURCE_USER_IMPLICIT = 3

SOURCE_USER_EXPLICIT = 4

SOURCE_SYSTEM = 5

TRAJECTORY_TYPE_CASCADE = 4

TRAJECTORY_SOURCE_CLI = 17

STOP_REASON_STOP_PATTERN = 2

CASCADE_STATUS_IDLE = 1

TERMINATION_REASON_IDLE = 4

CONVERSATION_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS `trajectory_meta` (
    `trajectory_id` text,
    `cascade_id` text,
    `trajectory_type` integer,
    `source` integer,
    PRIMARY KEY (`trajectory_id`)
);
CREATE TABLE IF NOT EXISTS `steps` (
    `idx` integer,
    `step_type` integer NOT NULL DEFAULT 0,
    `status` integer NOT NULL DEFAULT 0,
    `has_subtrajectory` numeric NOT NULL DEFAULT false,
    `metadata` blob,
    `error_details` blob,
    `permissions` blob,
    `task_details` blob,
    `render_info` blob,
    `step_payload` blob,
    `step_format` integer NOT NULL DEFAULT 0,
    PRIMARY KEY (`idx`)
);
CREATE INDEX IF NOT EXISTS `idx_steps_status` ON `steps`(`status`);
CREATE INDEX IF NOT EXISTS `idx_steps_step_type` ON `steps`(`step_type`);
CREATE TABLE IF NOT EXISTS `gen_metadata` (
    `idx` integer,
    `data` blob,
    `size` integer NOT NULL DEFAULT 0,
    PRIMARY KEY (`idx`)
);
CREATE TABLE IF NOT EXISTS `executor_metadata` (
    `idx` integer,
    `data` blob,
    PRIMARY KEY (`idx`)
);
CREATE TABLE IF NOT EXISTS `parent_references` (
    `idx` integer,
    `data` blob,
    PRIMARY KEY (`idx`)
);
CREATE TABLE IF NOT EXISTS `trajectory_metadata_blob` (
    `id` text DEFAULT "main",
    `data` blob,
    PRIMARY KEY (`id`)
);
CREATE TABLE IF NOT EXISTS `battle_mode_infos` (
    `idx` integer,
    `data` blob,
    PRIMARY KEY (`idx`)
);
"""

SUMMARIES_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS `conversation_summaries` (
    `conversation_id` text,
    `title` text NOT NULL DEFAULT "",
    `preview` text NOT NULL DEFAULT "",
    `step_count` integer NOT NULL DEFAULT 0,
    `last_modified_time` datetime NOT NULL,
    `workspace_uris` text NOT NULL,
    `status` text NOT NULL DEFAULT "",
    `source` text NOT NULL DEFAULT "",
    `project_id` text NOT NULL DEFAULT "",
    `agent_name` text NOT NULL DEFAULT "",
    `parent_conversation_id` text NOT NULL DEFAULT "",
    `nesting_depth` integer NOT NULL DEFAULT 0,
    `battle_id` text NOT NULL DEFAULT "",
    `winning_conversation_id` text NOT NULL DEFAULT "",
    `not_fully_idle` numeric NOT NULL DEFAULT false,
    `killed` numeric NOT NULL DEFAULT false,
    `last_user_input_time` datetime NOT NULL,
    `last_user_input_step_index` integer NOT NULL DEFAULT -1,
    `app_data_dir` text NOT NULL DEFAULT "",
    `raw_summary` blob,
    group_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (`conversation_id`)
);
CREATE INDEX IF NOT EXISTS `idx_conversation_summaries_last_user_input_time`
    ON `conversation_summaries`(`last_user_input_time`);
CREATE INDEX IF NOT EXISTS `idx_conversation_summaries_last_modified_time`
    ON `conversation_summaries`(`last_modified_time`);
CREATE TABLE IF NOT EXISTS search_schema_version (
    version INTEGER PRIMARY KEY
);
CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(
    conversation_id UNINDEXED,
    chunk_index UNINDEXED,
    chunk_first_step UNINDEXED,
    chunk_last_step UNINDEXED,
    last_modified_time UNINDEXED,
    step_count UNINDEXED,
    last_step_created_at UNINDEXED,
    archived UNINDEXED,
    user_step_byte_offsets UNINDEXED,
    agent_step_byte_offsets UNINDEXED,
    title,
    workspace,
    project,
    user_body,
    agent_body,
    tokenize = 'unicode61 remove_diacritics 2',
    prefix = '1 2 3'
);
CREATE TABLE IF NOT EXISTS conversation_parents (
    conversation_id TEXT PRIMARY KEY,
    parent_id TEXT NOT NULL
) WITHOUT ROWID;
"""
