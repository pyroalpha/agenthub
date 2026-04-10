"""VCS (Version Control System) utilities for AgentHub."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def vcs_init_agent(agent_dir: Path, agent_name: str, agenthub_root: Path) -> str | None:
    """Initialize git repository for a new agent.

    Args:
        agent_dir: Agent root directory.
        agent_name: Agent display name (used in commit message).
        agenthub_root: AgentHub root directory (used for path security validation).

    Returns:
        Commit hash if successful, None if git unavailable or failed.

    Raises:
        None: Always succeeds or returns None (graceful degradation).
    """
    # Path security check: prevent path escape
    resolved_dir = agent_dir.resolve()
    resolved_root = agenthub_root.resolve()
    if not resolved_dir.is_relative_to(resolved_root):
        logger.warning(f"Path escape attempt detected: {agent_dir}")
        return None

    try:
        # git init
        result = subprocess.run(
            ["git", "init"],
            cwd=str(agent_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(f"git init failed: {result.stderr}")
            return None

        # git add -A
        result = subprocess.run(
            ["git", "add", "-A"],
            cwd=str(agent_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(f"git add failed: {result.stderr}")
            return None

        # git commit
        commit_message = f"InitAgent: create {agent_name}"
        result = subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=str(agent_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(f"git commit failed: {result.stderr}")
            return None

        # Get commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(agent_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
        commit_hash = result.stdout.strip() if result.returncode == 0 else None
        logger.info(f"Git init successful for '{agent_dir.name}', commit: {commit_hash}")
        return commit_hash

    except FileNotFoundError:
        logger.warning("git not found in PATH, skipping git init")
        return None
    except subprocess.TimeoutExpired:
        logger.warning(f"git init timed out for '{agent_dir.name}'")
        return None
    except Exception as e:
        logger.warning(f"git init error for '{agent_dir.name}': {e}")
        return None
