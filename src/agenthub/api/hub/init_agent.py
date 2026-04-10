"""Init Agent API - AgentHub perspective.

API Layer responsibilities:
- Generate agent_id (no timestamp)
- Generate agent_name (separate from agent_id)
- Conflict detection before directory creation
- Get Pokemon avatar via get_pokemon_avatar()
- Create empty directory structure
- Copy builtin skills (evolution, self-evolution) to agent's skills/builtin/
- Call executor.execute()
- Parse Skill return result
- Verify files written
- Return Agent object
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from agenthub.core.companion import get_pokemon_avatar, get_personality
from agenthub.core.config import get_config
from agenthub.core.errors import AgentHubError, ParseError, ValidationError
from agenthub.core.types import Agent, InitAgentConfig, InitAgentResult
from agenthub.runtime.executor import get_executor

logger = logging.getLogger(__name__)


async def init_agent(config: InitAgentConfig) -> Agent:
    """Initialize a new agent with bootstrap files.

    API Layer responsibilities:
    - Generate agent_id (no timestamp)
    - Generate agent_name (separate from agent_id)
    - Create directory structure
    - Copy builtin skills to agent's skills/builtin/
    - Call executor.execute()
    - Parse Skill return result
    - Verify files written
    - Return Agent object

    Args:
        config: Agent initialization configuration

    Returns:
        Created Agent object

    Raises:
        ValidationError: If config validation fails or name contains non-ASCII
        AgentHubError: If agent creation fails
    """
    logger.info(f"Initializing agent: {config.name}")

    # 1. Config validation
    try:
        validated_config = InitAgentConfig.model_validate(config)
    except PydanticValidationError as e:
        raise ValidationError(f"Invalid config: {e}") from e

    config_obj = get_config()

    # 2. Generate agent_id and agent_name (API Layer responsibility)
    # If user provided name: agent_id = slugify(name), agent_name = user input
    # If no name: get random pokemon first, then agent_id = {pokemon_name}-{uuid}
    if validated_config.name:
        # User provided name
        agent_id = _generate_agent_id(validated_config.name)
        agent_name = validated_config.name
        # Get Pokemon avatar (random, user name is display name)
        pokemon_data = get_pokemon_avatar(agent_id, validated_config.name)[0]
    else:
        # No user name: get random Pokemon first, then generate agent_id
        pokemon_data = get_pokemon_avatar(agent_id="temp", requested_name=None)[0]
        agent_name = pokemon_data.name
        agent_id = _generate_agent_id(None, pokemon_data.name)

    agent_dir = config_obj.agenthub_dir / agent_id

    # 3. Create directory structure
    try:
        _create_directory_structure(agent_dir)
    except OSError as e:
        raise AgentHubError(f"DIRECTORY_CREATE_ERROR: {e}") from e

    # 5. Write .agenthub_meta (API Layer responsibility)
    # This stores agent metadata for export/get_agent
    agenthub_meta_path = agent_dir / ".agenthub_meta"
    agenthub_meta = {
        "name": agent_name,
        "avatar": pokemon_data.ascii,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        agenthub_meta_path.write_text(json.dumps(agenthub_meta, indent=2, ensure_ascii=False))
    except OSError as e:
        raise AgentHubError(f"AGENTHUB_META_WRITE_ERROR: {e}") from e

    # 6. Copy builtin skills to agent's skills/builtin/ (API Layer responsibility)
    try:
        _copy_builtin_skills(agent_dir, config_obj.builtin_skills_dir)
    except OSError as e:
        raise AgentHubError(f"BUILTIN_SKILLS_COPY_ERROR: {e}") from e

    executor = get_executor()

    try:
        # 7. Call executor to run Skill
        result = await executor.execute(
            skill_name="init_agent",
            task_description="Initialize a new agent with bootstrap files.",
            agent_id=agent_id,
            context={
                "name": agent_name,  # User input or Pokemon name
                "personality": get_personality(validated_config.personality),  # Pass through or None
                "identity": validated_config.identity,
                "traits": validated_config.traits,
                "agent_id": agent_id,  # API layer generated ID
                "pokemon_data": pokemon_data.model_dump(),  # Pokemon companion data
            },
            timeout=config_obj.init_agent_timeout,
            scope="agent",
        )

        # 8. Parse Skill return result
        init_result = parse_init_agent_result(result)

        # 9. Verify files written
        _verify_files_written(agent_dir, init_result.files_written)

        # 10. Git init
        from agenthub.core.vcs import vcs_init_agent

        git_commit = None
        try:
            git_commit = vcs_init_agent(
                agent_dir,
                init_result.agent_name or agent_name,
                config_obj.agenthub_dir,
            )
        except Exception as e:
            logger.warning(f"vcs_init_agent failed: {e}")

        init_result.git_commit = git_commit

        logger.info(f"Agent '{agent_id}' initialized successfully at {agent_dir}")

        # 10. Return Agent object with Pokemon data
        # Use agent_name from Skill result, fallback to user input, then pokemon name
        agent_name = init_result.agent_name or validated_config.name or (pokemon_data.name if pokemon_data else agent_id)
        return Agent(
            id=agent_id,
            name=agent_name,
            path=agent_dir,
            created_at=datetime.now(timezone.utc),
            avatar=pokemon_data.ascii,
        )

    except (ValidationError, AgentHubError) as e:
        # Cleanup on failure for known error types
        shutil.rmtree(agent_dir, ignore_errors=True)
        raise
    except PydanticValidationError as e:
        # Cleanup on failure for Pydantic validation errors from parse_init_agent_result
        shutil.rmtree(agent_dir, ignore_errors=True)
        raise ParseError(f"SKILL_OUTPUT_VALIDATION_ERROR: {e}") from e
    except Exception as e:
        # Cleanup on failure for unexpected errors
        shutil.rmtree(agent_dir, ignore_errors=True)
        raise AgentHubError(f"SKILL_EXECUTION_ERROR: {e}") from e


def _generate_agent_id(name: str | None, pokemon_name: str | None = None) -> str:
    """Generate URL-safe agent ID from name.

    Args:
        name: Agent name (must be ASCII alphanumeric).
        pokemon_name: Pokemon name for fallback when name is None.

    Returns:
        URL-safe agent ID (lowercase, no timestamp).

    Raises:
        ValidationError: If name contains non-ASCII characters.
    """
    if name:
        # Validate: name must be ASCII alphanumeric
        if not name.isascii():
            raise ValidationError("NAME_MUST_BE_ASCII: Agent name must be English alphanumeric")
        # Convert to lowercase, replace spaces with hyphens, remove special chars
        agent_id = re.sub(r"[^a-z0-9]+", "-", name.lower())
        agent_id = agent_id.strip("-")
        if not agent_id:
            agent_id = "agent"
        return agent_id
    else:
        # No user name: use {pokemon_name}-{uuid}
        if not pokemon_name:
            pokemon_name = "agent"
        slug = re.sub(r"[^a-z0-9]+", "-", pokemon_name.lower())
        slug = slug.strip("-")
        uuid_suffix = uuid.uuid4().hex[:8]
        return f"{slug}-{uuid_suffix}"


def _create_directory_structure(agent_dir: Path) -> None:
    """Create agent directory structure.

    Args:
        agent_dir: Agent root directory

    Raises:
        OSError: If directory creation fails
    """
    dirs = [
        agent_dir,
        agent_dir / "skills",
        agent_dir / "skills" / "builtin",
        agent_dir / "skills" / "universal",
        agent_dir / "skills" / "projects",
        agent_dir / "memory",
        agent_dir / "memory" / "projects",
        agent_dir / "memory" / "projects" / "universal",
        agent_dir / "archives",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def _copy_builtin_skills(agent_dir: Path, builtin_skills_dir: Path) -> None:
    """Copy builtin skills to agent's skills/builtin/ directory.

    Copies evolution and self-evolution skills from the package's builtin_skills
    directory to the agent's skills/builtin directory.

    Args:
        agent_dir: Agent root directory
        builtin_skills_dir: Package's builtin skills directory

    Raises:
        OSError: If copy operation fails
    """
    source_agent_skills = builtin_skills_dir / "agent"
    target_builtin = agent_dir / "skills" / "builtin"

    if not source_agent_skills.exists():
        logger.warning(f"Source builtin skills dir does not exist: {source_agent_skills}")
        return

    for skill_dir in source_agent_skills.iterdir():
        if skill_dir.is_dir():
            target_dir = target_builtin / skill_dir.name
            if not target_dir.exists():
                shutil.copytree(skill_dir, target_dir)
                logger.info(f"Copied builtin skill: {skill_dir.name}")


def _verify_files_written(agent_dir: Path, files_written: list[str]) -> None:
    """Verify that Skill actually wrote the expected files.

    Args:
        agent_dir: Agent root directory
        files_written: List of files that Skill reported writing

    Raises:
        AgentHubError: If expected file is not found
    """
    for filename in files_written:
        # Handle paths with directory prefixes (e.g., "/agent-id/soul.md" or "skills/skill.md")
        import os
        basename = os.path.basename(filename)
        filepath = agent_dir / basename
        if not filepath.exists():
            raise AgentHubError(f"FILE_WRITE_MISSING: expected {basename} but not found")


def parse_init_agent_result(result: str) -> InitAgentResult:
    """Parse Skill return result to InitAgentResult.

    Args:
        result: executor.execute() return value (raw string)

    Returns:
        InitAgentResult object

    Raises:
        AgentHubError: If parsing fails or Skill returned an error
    """
    from agenthub.runtime.executor import _extract_json

    try:
        json_str = _extract_json(result)
        parsed = json.loads(json_str)

        # Check if Skill returned an error
        if "error" in parsed:
            error_code = parsed["error"]
            if error_code == "AGENT_EXISTS":
                raise AgentHubError(f"AGENT_EXISTS: {parsed.get('agent_id')}")
            elif error_code == "MISSING_NAME":
                raise ValidationError("Missing required field: name")
            else:
                raise AgentHubError(f"SKILL_ERROR: {error_code}")

        # Tolerate non-standard LLM output by extracting missing fields
        # LLM sometimes returns agent_id instead of agent_name, notes instead of personality
        if "agent_name" not in parsed or not parsed["agent_name"]:
            if "agent_id" in parsed:
                parsed["agent_name"] = parsed["agent_id"].split("-")[0] if "-" in parsed["agent_id"] else parsed["agent_id"]
        if "personality" not in parsed or not parsed["personality"]:
            if "notes" in parsed and parsed["notes"]:
                # Handle both list and dict formats for notes
                notes = parsed["notes"]
                if isinstance(notes, list):
                    for note in notes:
                        if isinstance(note, str) and len(note) > 10:
                            parsed["personality"] = note
                            break
                elif isinstance(notes, dict):
                    # notes is a dict like {'personality_generated': '...'}
                    for key, value in notes.items():
                        if isinstance(value, str) and len(value) > 10:
                            parsed["personality"] = value
                            break
        # Convert notes to list if it's a dict
        if "notes" in parsed and isinstance(parsed["notes"], dict):
            parsed["notes"] = [str(parsed["notes"])]

        return InitAgentResult.model_validate(parsed)

    except json.JSONDecodeError as e:
        raise AgentHubError(f"SKILL_OUTPUT_PARSE_ERROR: {e}") from e
