import abc
from dataclasses import dataclass
from typing import Dict, Any, Optional, List

@dataclass
class RunResult:
    output: str
    error: Optional[str] = None
    exit_code: int = 0

@dataclass
class FileEntry:
    name: str
    path: str
    is_dir: bool
    size: int

class ExecutionBackend(abc.ABC):
    """Base class for execution backends."""
    
    @abc.abstractmethod
    async def run_python(self, code: str, params: Dict[str, Any], timeout: int = 30) -> RunResult:
        pass

    @abc.abstractmethod
    async def run_command(self, command: str, cwd: Optional[str] = None, timeout: int = 60, env: Optional[Dict[str, str]] = None) -> RunResult:
        pass

    @abc.abstractmethod
    async def run_command_background(self, command: str, cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> int:
        pass

    @abc.abstractmethod
    async def read_file(self, path: str, encoding: str = "utf-8") -> str:
        pass

    @abc.abstractmethod
    async def write_file(self, path: str, content: str, encoding: str = "utf-8", append: bool = False) -> None:
        pass

    @abc.abstractmethod
    async def list_dir(self, path: str, recursive: bool = False) -> List[FileEntry]:
        pass

    @abc.abstractmethod
    async def delete_path(self, path: str) -> None:
        pass

    @abc.abstractmethod
    def get_workspace(self) -> str:
        pass
