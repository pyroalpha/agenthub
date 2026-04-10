"""Companion module for Pokemon Avatar feature."""

from __future__ import annotations

from agenthub.core.pokemon_db import (
    get_random_pokemon,
    lookup_pokemon_by_name,
)
from agenthub.core.types import PokemonData


def get_pokemon_avatar(
    agent_id: str,
    requested_name: str | None = None,
) -> tuple[PokemonData, str]:
    """Get Pokemon avatar for an agent.

    Logic:
    1. If user provided name and it matches a Pokemon -> use matched Pokemon name
    2. If user provided name but no match -> random Pokemon + user name
    3. If no name -> random Pokemon + Pokemon name

    Note:
        Pokemon selection is always random (not deterministic hash) to ensure
        each creation is independent.

    Args:
        agent_id: Unique agent identifier (used for fallback and logging)
        requested_name: User-provided name

    Returns:
        Tuple of (PokemonData, agent_name)
    """
    # Case 1: User provided name and it matches a Pokemon
    if requested_name:
        matched = lookup_pokemon_by_name(requested_name)
        if matched:
            # User name matches a Pokemon -> use Pokemon name
            pokemon_data = PokemonData(**matched)
            return pokemon_data, pokemon_data.name

    # Case 2: User provided name but no match -> random Pokemon + user name
    if requested_name:
        random_pokemon = get_random_pokemon()
        pokemon_data = PokemonData(**random_pokemon)
        return pokemon_data, requested_name

    # Case 3: No user name -> random Pokemon + Pokemon name
    # No deterministic hash - each creation is independent
    random_pokemon = get_random_pokemon()
    pokemon_data = PokemonData(**random_pokemon)
    return pokemon_data, pokemon_data.name


def get_personality(user_personality: str | None) -> str | None:
    """Pass through user-provided personality.

    This function is a passthrough - personality generation happens
    in the Skill Layer based on Pokemon type/abilities.

    Args:
        user_personality: User-provided personality string (optional)

    Returns:
        The same personality string if provided, None otherwise
    """
    return user_personality
