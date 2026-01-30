"""
Skill Plugin implementation.

Skills are Python functions that can be exposed as tools for agents.
"""

import logging
import importlib
import inspect
from typing import Any, Callable, Dict, List, Optional, get_type_hints

from .registry import Plugin, Tool
from ..core.models import PluginConfig, SkillConfig

logger = logging.getLogger(__name__)


class SkillPlugin(Plugin):
    """
    Plugin for registering Python functions as agent tools.

    Skills allow you to expose custom functionality to agents
    through simple Python functions with type hints.
    """

    def __init__(self, config: PluginConfig):
        super().__init__(config)
        self._skill_config: Optional[SkillConfig] = config.skill_config
        self._function: Optional[Callable[..., Any]] = None

    async def initialize(self) -> None:
        """Initialize the skill plugin."""
        if self._initialized:
            return

        if not self._skill_config:
            raise ValueError("SkillConfig is required")

        # Load the function
        self._function = self._load_function()

        if self._function:
            # Create tool from function
            tool = self._create_tool_from_function(self._function)
            self._tools = [tool]
        else:
            self._tools = []

        self._initialized = True
        logger.info(f"Skill plugin initialized: {self._skill_config.name}")

    def _load_function(self) -> Optional[Callable[..., Any]]:
        """Load the skill function from module."""
        try:
            module = importlib.import_module(self._skill_config.module_path)
            func = getattr(module, self._skill_config.function_name)
            return func
        except ImportError as e:
            logger.error(f"Failed to import module {self._skill_config.module_path}: {e}")
            return None
        except AttributeError as e:
            logger.error(
                f"Function {self._skill_config.function_name} not found in "
                f"{self._skill_config.module_path}: {e}"
            )
            return None

    def _create_tool_from_function(self, func: Callable[..., Any]) -> Tool:
        """Create a Tool from a Python function."""
        # Get function signature
        sig = inspect.signature(func)

        # Build parameters schema from type hints
        type_hints = get_type_hints(func) if hasattr(func, '__annotations__') else {}
        parameters_schema = self._build_parameters_schema(sig, type_hints)

        # Use provided schema if available
        if self._skill_config.parameters_schema:
            parameters_schema = self._skill_config.parameters_schema

        # Create handler that wraps the function
        async def handler(**kwargs: Any) -> Any:
            try:
                result = func(**kwargs)
                if inspect.iscoroutine(result):
                    result = await result
                return result
            except Exception as e:
                return {"error": str(e)}

        return Tool(
            name=self._skill_config.name,
            description=self._skill_config.description or (func.__doc__ or ""),
            parameters_schema=parameters_schema,
            handler=handler,
            source="skill",
            metadata={
                "module": self._skill_config.module_path,
                "function": self._skill_config.function_name,
                "approval_required": self._skill_config.approval_required,
            },
        )

    def _build_parameters_schema(
        self,
        sig: inspect.Signature,
        type_hints: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build JSON Schema from function signature."""
        properties = {}
        required = []

        for name, param in sig.parameters.items():
            if name in ("self", "cls"):
                continue

            prop = {"type": "string"}  # Default

            # Get type from hints
            if name in type_hints:
                hint = type_hints[name]
                prop = self._type_to_schema(hint)

            # Get description from Annotated if available
            if hasattr(hint, '__metadata__'):
                for meta in hint.__metadata__:
                    if isinstance(meta, str):
                        prop["description"] = meta
                        break

            properties[name] = prop

            # Required if no default
            if param.default is inspect.Parameter.empty:
                required.append(name)

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    def _type_to_schema(self, hint: Any) -> Dict[str, Any]:
        """Convert Python type hint to JSON Schema type."""
        type_map = {
            str: {"type": "string"},
            int: {"type": "integer"},
            float: {"type": "number"},
            bool: {"type": "boolean"},
            list: {"type": "array"},
            dict: {"type": "object"},
        }

        # Handle basic types
        if hint in type_map:
            return type_map[hint]

        # Handle Optional
        origin = getattr(hint, '__origin__', None)
        if origin is type(None):
            return {"type": "null"}

        # Handle List[T]
        if origin is list:
            args = getattr(hint, '__args__', ())
            if args:
                return {
                    "type": "array",
                    "items": self._type_to_schema(args[0]),
                }
            return {"type": "array"}

        # Handle Dict[K, V]
        if origin is dict:
            return {"type": "object"}

        # Default to string
        return {"type": "string"}

    async def cleanup(self) -> None:
        """Clean up skill resources."""
        self._function = None
        self._tools = []

    def get_tools(self) -> List[Tool]:
        """Get the skill tool."""
        return self._tools


def skill(
    name: Optional[str] = None,
    description: Optional[str] = None,
    approval_required: bool = False,
):
    """
    Decorator to mark a function as a skill.

    Usage:
        @skill(name="get_weather", description="Get weather for a city")
        def get_weather(city: str) -> str:
            return f"Weather in {city}: Sunny"

    Args:
        name: Tool name (defaults to function name)
        description: Tool description (defaults to docstring)
        approval_required: Whether approval is needed before execution
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func._skill_name = name or func.__name__
        func._skill_description = description or func.__doc__ or ""
        func._skill_approval_required = approval_required
        return func

    return decorator
