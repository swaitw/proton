import asyncio
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, List
from .base import ExecutionBackend, RunResult, FileEntry

logger = logging.getLogger(__name__)

class LocalProcessBackend(ExecutionBackend):
    """Executes code and commands in a local subprocess (Better than exec, but less secure than Docker)."""
    
    def __init__(self, workspace_dir: Optional[str] = None, namespace: Optional[str] = None):
        self._workspace = self._resolve_workspace(workspace_dir, namespace)

    def get_workspace(self) -> str:
        return str(self._workspace)
        
    def _resolve_workspace(self, configured_dir: Optional[str], namespace: Optional[str]) -> Path:
        if configured_dir:
            p = Path(configured_dir).resolve()
            try:
                p.mkdir(parents=True, exist_ok=True)
                # Test writability
                test_file = p / ".proton_write_test"
                test_file.touch()
                test_file.unlink()
                return p
            except Exception as e:
                logger.warning(f"Failed to use configured workspace {configured_dir}: {e}. Falling back to temp dir.")
        
        # Fallback to temp directory, isolated by namespace if provided
        base_dir = Path(tempfile.gettempdir())
        if namespace:
            temp_dir = base_dir / "proton_workspaces" / namespace
        else:
            temp_dir = base_dir / "proton_workspace"
            
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir.resolve()

    def _ensure_safe_path(self, filepath: str) -> Path:
        """Ensure the path is within the workspace to prevent directory traversal."""
        if not filepath:
            return self._workspace
            
        # Handle absolute paths that are already inside workspace
        p = Path(filepath)
        if p.is_absolute():
            try:
                resolved = p.resolve()
                if self._workspace in resolved.parents or resolved == self._workspace:
                    return resolved
            except Exception:
                pass
                
        # For absolute paths that are NOT in workspace (e.g. LLM thinks it's at /src/main.py)
        # Block the operation to prevent silent path hijacking and force the LLM to correct it.
        if p.is_absolute():
            try:
                p.resolve().relative_to(self._workspace)
            except ValueError:
                raise PermissionError(
                    f"Access denied: The absolute path '{filepath}' is outside the current workspace.\n"
                    f"Current workspace root is: '{self._workspace}'.\n"
                    f"Please use relative paths (e.g., 'src/main.py') or absolute paths within the workspace."
                )
                
        # Resolve relative to workspace
        target_path = (self._workspace / filepath).resolve()
        
        # Check if target is inside workspace
        try:
            target_path.relative_to(self._workspace)
            return target_path
        except ValueError:
            raise PermissionError(f"Path traversal detected or path outside workspace: {filepath}")
    
    async def run_python(self, code: str, params: Dict[str, Any], timeout: int = 30) -> RunResult:
        wrapper_code = f"""
import sys
import json

try:
    params = json.loads(sys.stdin.read() or "{{}}")
except Exception:
    params = {{}}

result = None
try:
{self._indent(code, 4)}
    print(json.dumps({{"__proton_result": result}}))
except Exception as e:
    import traceback
    print(json.dumps({{"__proton_error": str(e), "traceback": traceback.format_exc()}}), file=sys.stderr)
    sys.exit(1)
"""
        try:
            process = await asyncio.create_subprocess_exec(
                "python", "-c", wrapper_code,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            input_data = json.dumps(params).encode('utf-8')
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(input=input_data),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return RunResult(output="", error=f"Execution timed out after {timeout}s", exit_code=124)

            stdout_str = stdout.decode('utf-8').strip()
            stderr_str = stderr.decode('utf-8').strip()
            
            if process.returncode != 0:
                error_msg = stderr_str
                for line in stderr_str.splitlines():
                    try:
                        data = json.loads(line)
                        if "__proton_error" in data:
                            error_msg = data["__proton_error"]
                            break
                    except json.JSONDecodeError:
                        pass
                return RunResult(output="", error=error_msg, exit_code=process.returncode)

            final_result = stdout_str
            for line in reversed(stdout_str.splitlines()):
                try:
                    data = json.loads(line)
                    if "__proton_result" in data:
                        final_result = str(data["__proton_result"])
                        break
                except json.JSONDecodeError:
                    pass

            return RunResult(output=final_result, error=None, exit_code=0)
            
        except Exception as e:
            logger.error(f"Local process backend error: {e}")
            return RunResult(output="", error=str(e), exit_code=-1)

    def _indent(self, text: str, spaces: int) -> str:
        prefix = " " * spaces
        return "\n".join(prefix + line for line in text.splitlines())

    async def run_command(self, command: str, cwd: Optional[str] = None, timeout: int = 60, env: Optional[Dict[str, str]] = None) -> RunResult:
        working_dir = self._ensure_safe_path(cwd or ".")
        merged_env = {**os.environ, **(env or {})}
        
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(working_dir),
                env=merged_env
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
                return RunResult(
                    output=stdout.decode("utf-8", errors="replace").strip(),
                    error=stderr.decode("utf-8", errors="replace").strip() if stderr else None,
                    exit_code=process.returncode or 0
                )
            except asyncio.TimeoutError:
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass
                return RunResult(output="", error=f"Timeout after {timeout}s", exit_code=124)
                
        except Exception as e:
            logger.error(f"Local process command error: {e}")
            return RunResult(output="", error=str(e), exit_code=-1)

    async def run_command_background(self, command: str, cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> int:
        working_dir = self._ensure_safe_path(cwd or ".")
        merged_env = {**os.environ, **(env or {})}
        
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(working_dir),
            env=merged_env
        )
        return process.pid

    async def read_file(self, path: str, encoding: str = "utf-8") -> str:
        safe_path = self._ensure_safe_path(path)
        if not safe_path.is_file():
            raise FileNotFoundError(f"Not a file or not found: {path}")
        return safe_path.read_text(encoding=encoding)

    async def write_file(self, path: str, content: str, encoding: str = "utf-8", append: bool = False) -> None:
        safe_path = self._ensure_safe_path(path)
        mode = "a" if append else "w"
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        with open(safe_path, mode, encoding=encoding) as f:
            f.write(content)

    async def list_dir(self, path: str, recursive: bool = False) -> List[FileEntry]:
        safe_path = self._ensure_safe_path(path)
        if not safe_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {path}")
            
        entries = []
        iterator = safe_path.rglob("*") if recursive else safe_path.iterdir()
        for p in iterator:
            try:
                entries.append(FileEntry(
                    name=p.name,
                    path=str(p.relative_to(self._workspace)),
                    is_dir=p.is_dir(),
                    size=p.stat().st_size if p.is_file() else 0
                ))
            except Exception as e:
                logger.debug(f"Error reading file stat {p}: {e}")
                
        return entries

    async def delete_path(self, path: str) -> None:
        safe_path = self._ensure_safe_path(path)
        if not safe_path.exists():
            return
            
        if safe_path.is_file() or safe_path.is_symlink():
            safe_path.unlink()
        elif safe_path.is_dir():
            shutil.rmtree(safe_path)
