"""
File system tools: read, write, list, edit files.
"""

import os
import logging
import tempfile
from pathlib import Path
from typing import Any, List

from .base import SystemTool, ToolParameterSchema

logger = logging.getLogger(__name__)


# Default workspace directory
DEFAULT_WORKSPACE = os.environ.get("PROTON_WORKSPACE", os.path.expanduser("~/.proton/workspace"))

# Resolved workspace (may change at runtime if default is not writable)
_resolved_workspace: str | None = None


def _get_workspace() -> Path:
    """Get the writable workspace directory, with fallback."""
    global _resolved_workspace

    if _resolved_workspace:
        return Path(_resolved_workspace)

    # Try configured workspace
    workspace = Path(DEFAULT_WORKSPACE)
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        # Test if writable
        test_file = workspace / ".write_test"
        test_file.write_text("test")
        test_file.unlink()
        _resolved_workspace = str(workspace)
        logger.info(f"Using workspace: {workspace}")
        return workspace
    except (OSError, PermissionError) as e:
        logger.warning(f"Default workspace not writable ({e}), trying fallback...")

    # Fallback: project-relative output directory
    project_root = Path(__file__).parent.parent.parent
    fallback = project_root / "output"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        test_file = fallback / ".write_test"
        test_file.write_text("test")
        test_file.unlink()
        _resolved_workspace = str(fallback)
        logger.info(f"Using fallback workspace: {fallback}")
        return fallback
    except (OSError, PermissionError):
        pass

    # Last resort: temp directory
    fallback_tmp = Path(tempfile.gettempdir()) / "proton-workspace"
    fallback_tmp.mkdir(parents=True, exist_ok=True)
    _resolved_workspace = str(fallback_tmp)
    logger.info(f"Using temp workspace: {fallback_tmp}")
    return fallback_tmp


def _ensure_workspace(filepath: str) -> Path:
    """Ensure file path is within workspace and return absolute path."""
    workspace = _get_workspace()

    # Resolve the path
    if os.path.isabs(filepath):
        path = Path(filepath)
    else:
        path = workspace / filepath

    # Security check: ensure path is within workspace
    try:
        path.resolve().relative_to(workspace.resolve())
    except ValueError:
        raise PermissionError(f"Access denied: {filepath} is outside workspace ({workspace})")

    return path


class FileReadTool(SystemTool):
    """Read contents of a file."""

    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return "Read the contents of a file. Returns the file content as text."

    @property
    def parameters(self) -> List[ToolParameterSchema]:
        return [
            ToolParameterSchema(
                name="path",
                type="string",
                description="File path (relative to workspace or absolute)",
                required=True,
            ),
            ToolParameterSchema(
                name="encoding",
                type="string",
                description="File encoding (default: utf-8)",
                required=False,
                default="utf-8",
            ),
        ]

    @property
    def category(self) -> str:
        return "filesystem"

    async def execute(self, **kwargs: Any) -> str:
        filepath = kwargs.get("path", "")
        encoding = kwargs.get("encoding", "utf-8")

        if not filepath:
            return "Error: path is required"

        context = kwargs.get("__execution_context")
        backend = context.backend if context else None

        if backend:
            try:
                return await backend.read_file(filepath, encoding=encoding)
            except Exception as e:
                logger.error(f"Error reading file via backend: {e}")
                return f"Error reading file: {e}"

        try:
            path = _ensure_workspace(filepath)
            if not path.exists():
                return f"Error: File not found: {filepath}"
            if not path.is_file():
                return f"Error: {filepath} is not a file"

            content = path.read_text(encoding=encoding)
            return content
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error(f"Error reading file: {e}")
            return f"Error reading file: {e}"


class FileWriteTool(SystemTool):
    """Write content to a file."""

    @property
    def name(self) -> str:
        return "file_write"

    @property
    def description(self) -> str:
        return (
            "Write content to a file in the workspace directory. "
            "Creates the file if it doesn't exist, or overwrites if it does. "
            "IMPORTANT: Use relative paths only (e.g., 'travel_plan.md' or 'docs/report.md'). "
            "The file will be saved in the workspace directory."
        )

    @property
    def parameters(self) -> List[ToolParameterSchema]:
        return [
            ToolParameterSchema(
                name="path",
                type="string",
                description="Relative file path within workspace (e.g., 'report.md' or 'docs/plan.md'). Do NOT use absolute paths starting with '/'.",
                required=True,
            ),
            ToolParameterSchema(
                name="content",
                type="string",
                description="Content to write to the file",
                required=True,
            ),
            ToolParameterSchema(
                name="encoding",
                type="string",
                description="File encoding (default: utf-8)",
                required=False,
                default="utf-8",
            ),
        ]

    @property
    def category(self) -> str:
        return "filesystem"

    @property
    def requires_approval(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        filepath = kwargs.get("path", "")
        content = kwargs.get("content", "")
        encoding = kwargs.get("encoding", "utf-8")

        if not filepath:
            return "Error: path is required"

        # Strip leading slashes to prevent absolute path issues
        filepath = filepath.lstrip("/")

        context = kwargs.get("__execution_context")
        backend = context.backend if context else None

        if backend:
            try:
                await backend.write_file(filepath, content, encoding=encoding, append=False)
                return f"Successfully wrote {len(content)} characters to file.\nFull path: {filepath}"
            except Exception as e:
                logger.error(f"Error writing file via backend: {e}")
                return f"Error writing file: {e}"

        try:
            path = _ensure_workspace(filepath)
            path.parent.mkdir(parents=True, exist_ok=True)

            # Log for debugging
            logger.info(f"Writing file to: {path.resolve()}")
            logger.info(f"Content length: {len(content)} characters")

            with open(path, "w", encoding=encoding) as f:
                f.write(content)
            return f"Successfully wrote {len(content)} characters to file.\nFull path: {path}"
        except PermissionError as e:
            logger.error(f"Permission error writing file {filepath}: {e}")
            return f"Error: Permission denied - {e}"
        except OSError as e:
            logger.error(f"OS error writing file {filepath}: {e}")
            return f"Error writing file (OS error {e.errno}): {e}"
        except Exception as e:
            logger.error(f"Error writing file: {e}")
            return f"Error writing file: {e}"


class FileAppendTool(SystemTool):
    """Append content to a file."""

    @property
    def name(self) -> str:
        return "file_append"

    @property
    def description(self) -> str:
        return "Append content to a file. Creates the file if it doesn't exist."

    @property
    def parameters(self) -> List[ToolParameterSchema]:
        return [
            ToolParameterSchema(
                name="path",
                type="string",
                description="File path (relative to workspace or absolute)",
                required=True,
            ),
            ToolParameterSchema(
                name="content",
                type="string",
                description="Content to append to the file",
                required=True,
            ),
        ]

    @property
    def category(self) -> str:
        return "filesystem"

    @property
    def requires_approval(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        filepath = kwargs.get("path", "")
        content = kwargs.get("content", "")

        if not filepath:
            return "Error: path is required"

        context = kwargs.get("__execution_context")
        backend = context.backend if context else None

        if backend:
            try:
                await backend.write_file(filepath, content, append=True)
                return f"Successfully appended {len(content)} characters to {filepath}"
            except Exception as e:
                logger.error(f"Error appending to file via backend: {e}")
                return f"Error appending to file: {e}"

        try:
            path = _ensure_workspace(filepath)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(content)
            return f"Successfully appended {len(content)} characters to {filepath}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error(f"Error appending to file: {e}")
            return f"Error appending to file: {e}"


class FileListTool(SystemTool):
    """List files and directories."""

    @property
    def name(self) -> str:
        return "file_list"

    @property
    def description(self) -> str:
        return "List files and directories in a given path. Returns a list of entries with their types."

    @property
    def parameters(self) -> List[ToolParameterSchema]:
        return [
            ToolParameterSchema(
                name="path",
                type="string",
                description="Directory path (relative to workspace or absolute). Defaults to workspace root.",
                required=False,
                default=".",
            ),
            ToolParameterSchema(
                name="recursive",
                type="boolean",
                description="Whether to list recursively (default: false)",
                required=False,
                default=False,
            ),
        ]

    @property
    def category(self) -> str:
        return "filesystem"

    async def execute(self, **kwargs: Any) -> str:
        dirpath = kwargs.get("path", ".")
        recursive = kwargs.get("recursive", False)

        context = kwargs.get("__execution_context")
        backend = context.backend if context else None

        if backend:
            try:
                backend_entries = await backend.list_dir(dirpath, recursive=recursive)
                if not backend_entries:
                    return f"Directory {dirpath} is empty"
                
                result = []
                for entry in backend_entries:
                    entry_type = "dir" if entry.is_dir else "file"
                    display_path = entry.path if recursive else entry.name
                    result.append(f"[{entry_type}] {display_path} ({entry.size} bytes)")
                
                return "\n".join(sorted(result))
            except Exception as e:
                logger.error(f"Error listing directory via backend: {e}")
                return f"Error listing directory: {e}"

        try:
            path = _ensure_workspace(dirpath)
            if not path.exists():
                return f"Error: Directory not found: {dirpath}"
            if not path.is_dir():
                return f"Error: {dirpath} is not a directory"

            entries = []
            if recursive:
                for item in path.rglob("*"):
                    rel_path = item.relative_to(path)
                    entry_type = "dir" if item.is_dir() else "file"
                    size = item.stat().st_size if item.is_file() else 0
                    entries.append(f"[{entry_type}] {rel_path} ({size} bytes)")
            else:
                for item in path.iterdir():
                    entry_type = "dir" if item.is_dir() else "file"
                    size = item.stat().st_size if item.is_file() else 0
                    entries.append(f"[{entry_type}] {item.name} ({size} bytes)")

            if not entries:
                return f"Directory {dirpath} is empty"

            return "\n".join(sorted(entries))
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error(f"Error listing directory: {e}")
            return f"Error listing directory: {e}"


class FileDeleteTool(SystemTool):
    """Delete a file or empty directory."""

    @property
    def name(self) -> str:
        return "file_delete"

    @property
    def description(self) -> str:
        return "Delete a file or empty directory. Use with caution."

    @property
    def parameters(self) -> List[ToolParameterSchema]:
        return [
            ToolParameterSchema(
                name="path",
                type="string",
                description="File or directory path to delete",
                required=True,
            ),
        ]

    @property
    def category(self) -> str:
        return "filesystem"

    @property
    def requires_approval(self) -> bool:
        return True

    @property
    def is_dangerous(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        filepath = kwargs.get("path", "")

        if not filepath:
            return "Error: path is required"

        context = kwargs.get("__execution_context")
        backend = context.backend if context else None

        if backend:
            try:
                await backend.delete_path(filepath)
                return f"Successfully deleted: {filepath}"
            except Exception as e:
                logger.error(f"Error deleting via backend: {e}")
                return f"Error deleting: {e}"

        try:
            path = _ensure_workspace(filepath)
            if not path.exists():
                return f"Error: {filepath} does not exist"

            if path.is_file():
                path.unlink()
                return f"Successfully deleted file: {filepath}"
            elif path.is_dir():
                path.rmdir()  # Only removes empty directories
                return f"Successfully deleted directory: {filepath}"
            else:
                return f"Error: {filepath} is not a file or directory"
        except OSError as e:
            return f"Error: {e}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error(f"Error deleting: {e}")
            return f"Error deleting: {e}"
