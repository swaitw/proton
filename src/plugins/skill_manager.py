"""
Skill package manager for the platform.

Manages installed skills, their lifecycle, and agent bindings.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

from ..core.models import InstalledSkill, SkillConfig
from .skill_parser import get_skill_parser

logger = logging.getLogger(__name__)


class SkillManager:
    """
    Manages installed skill packages.

    Responsibilities:
    - Track installed skills
    - Manage skill-agent bindings
    - Persist skill metadata
    - Provide skill discovery
    """

    def __init__(self, storage_path: str = "data/skills/registry.json"):
        """
        Initialize skill manager.

        Args:
            storage_path: Path to skill registry file
        """
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.parser = get_skill_parser()
        self._skills: Dict[str, InstalledSkill] = {}
        self._load_registry()

    def _load_registry(self) -> None:
        """Load skill registry from disk."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for skill_data in data.get('skills', []):
                        skill = InstalledSkill(**skill_data)
                        self._skills[skill.id] = skill
                logger.info(f"Loaded {len(self._skills)} skills from registry")
            except Exception as e:
                logger.error(f"Failed to load skill registry: {e}")
                self._skills = {}
        else:
            logger.info("No existing skill registry found")

    def _save_registry(self) -> None:
        """Save skill registry to disk."""
        try:
            data = {
                'skills': [skill.model_dump() for skill in self._skills.values()]
            }
            with open(self.storage_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)
            logger.info(f"Saved {len(self._skills)} skills to registry")
        except Exception as e:
            logger.error(f"Failed to save skill registry: {e}")

    async def install_skill(self, file_path: str) -> InstalledSkill:
        """
        Install a skill from a package file.

        Args:
            file_path: Path to .zip or .skill file

        Returns:
            InstalledSkill object

        Raises:
            ValueError: If package is invalid
        """
        # Parse and extract the skill package
        installed_skill = self.parser.parse_skill_package(file_path)

        # Check for duplicate names
        for existing_skill in self._skills.values():
            if existing_skill.metadata.name == installed_skill.metadata.name:
                logger.warning(
                    f"Skill with name '{installed_skill.metadata.name}' already exists. "
                    f"Installing as separate instance."
                )

        # Register the skill
        self._skills[installed_skill.id] = installed_skill
        self._save_registry()

        logger.info(f"Installed skill: {installed_skill.metadata.name} ({installed_skill.id})")
        return installed_skill

    async def uninstall_skill(self, skill_id: str) -> bool:
        """
        Uninstall a skill.

        Args:
            skill_id: ID of skill to uninstall

        Returns:
            True if successful, False otherwise
        """
        if skill_id not in self._skills:
            logger.warning(f"Skill not found: {skill_id}")
            return False

        # Remove from registry
        skill = self._skills.pop(skill_id)
        self._save_registry()

        # Delete files
        success = self.parser.uninstall_skill(skill_id)

        if success:
            logger.info(f"Uninstalled skill: {skill.metadata.name}")
        else:
            logger.warning(f"Failed to delete files for skill: {skill_id}")

        return success

    def get_skill(self, skill_id: str) -> Optional[InstalledSkill]:
        """Get an installed skill by ID."""
        return self._skills.get(skill_id)

    def list_skills(self, enabled_only: bool = False) -> List[InstalledSkill]:
        """
        List all installed skills.

        Args:
            enabled_only: If True, only return enabled skills

        Returns:
            List of InstalledSkill objects
        """
        skills = list(self._skills.values())

        if enabled_only:
            skills = [s for s in skills if s.enabled]

        return skills

    def search_skills(self, query: str) -> List[InstalledSkill]:
        """
        Search skills by name, description, or tags.

        Args:
            query: Search query

        Returns:
            List of matching InstalledSkill objects
        """
        query_lower = query.lower()
        results = []

        for skill in self._skills.values():
            if (query_lower in skill.metadata.name.lower() or
                query_lower in skill.metadata.description.lower() or
                any(query_lower in tag.lower() for tag in skill.metadata.tags)):
                results.append(skill)

        return results

    async def bind_skill_to_agent(self, skill_id: str, agent_id: str) -> bool:
        """
        Bind a skill to an agent.

        Args:
            skill_id: ID of the skill
            agent_id: ID of the agent

        Returns:
            True if successful
        """
        skill = self.get_skill(skill_id)
        if not skill:
            logger.error(f"Skill not found: {skill_id}")
            return False

        if agent_id not in skill.agent_ids:
            skill.agent_ids.append(agent_id)
            self._save_registry()
            logger.info(f"Bound skill {skill_id} to agent {agent_id}")

        return True

    async def unbind_skill_from_agent(self, skill_id: str, agent_id: str) -> bool:
        """
        Unbind a skill from an agent.

        Args:
            skill_id: ID of the skill
            agent_id: ID of the agent

        Returns:
            True if successful
        """
        skill = self.get_skill(skill_id)
        if not skill:
            logger.error(f"Skill not found: {skill_id}")
            return False

        if agent_id in skill.agent_ids:
            skill.agent_ids.remove(agent_id)
            self._save_registry()
            logger.info(f"Unbound skill {skill_id} from agent {agent_id}")

        return True

    def get_skills_for_agent(self, agent_id: str) -> List[InstalledSkill]:
        """
        Get all skills bound to an agent.

        Args:
            agent_id: ID of the agent

        Returns:
            List of InstalledSkill objects
        """
        return [
            skill for skill in self._skills.values()
            if agent_id in skill.agent_ids
        ]

    def get_skill_config(self, skill_id: str) -> Optional[SkillConfig]:
        """
        Convert InstalledSkill to SkillConfig for plugin system.

        Args:
            skill_id: ID of the skill

        Returns:
            SkillConfig object or None
        """
        skill = self.get_skill(skill_id)
        if not skill:
            return None

        # Build module path from package directory
        package_path = Path(skill.package_path)
        entry_file = package_path / skill.metadata.entry_point

        if not entry_file.exists():
            logger.error(f"Skill entry point not found: {entry_file}")
            return None

        # Create a dynamic module path
        # Convert path to module notation: data/skills/{id}/skill.py -> skills.{id}.skill
        module_name = f"skills.{skill_id}.{skill.metadata.entry_point.replace('.py', '')}"

        return SkillConfig(
            name=skill.metadata.name,
            description=skill.metadata.description,
            module_path=module_name,
            function_name=skill.metadata.function_name,
            parameters_schema=skill.metadata.parameters_schema,
            approval_required=skill.metadata.approval_required,
        )

    async def toggle_skill(self, skill_id: str, enabled: bool) -> bool:
        """
        Enable or disable a skill.

        Args:
            skill_id: ID of the skill
            enabled: New enabled state

        Returns:
            True if successful
        """
        skill = self.get_skill(skill_id)
        if not skill:
            return False

        skill.enabled = enabled
        self._save_registry()
        logger.info(f"{'Enabled' if enabled else 'Disabled'} skill: {skill.metadata.name}")
        return True


# Global skill manager instance
_skill_manager: Optional[SkillManager] = None


def get_skill_manager() -> SkillManager:
    """Get the global skill manager instance."""
    global _skill_manager
    if _skill_manager is None:
        _skill_manager = SkillManager()
    return _skill_manager
