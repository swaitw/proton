import os
import subprocess

def check_and_commit():
    try:
        # Check status
        result = subprocess.run(['git', 'status', '-s'], capture_output=True, text=True, check=True)
        if not result.stdout.strip():
            print("Working tree clean, nothing to commit.")
            return

        print("Changes to be committed:")
        print(result.stdout)

        # Add files
        subprocess.run(['git', 'add', '.'], check=True)

        # Commit
        commit_msg = """refactor(core): implement industrial-grade sandbox isolation and fine-grained tool governance

- Introduced `fnmatch` wildcard support in `ToolPolicyEngine` and `ToolGovernanceSlice` for IAM-style fine-grained tool access control (e.g., `allow_tools: ["mempalace_*"]`).
- Implemented `_prune_tool_results` in `ExecutionContext._compress_context` to safely truncate verbose tool logs (e.g., 5000-line code search results) before LLM summarization, significantly reducing token bloat and noise.
- Extracted all raw OS file/process operations from `shell.py` and `filesystem.py` into a standardized `ExecutionBackend` protocol.
- Created `LocalProcessBackend` to handle all local executions, featuring robust `relative_to` workspace path boundary checks to absolutely prevent directory traversal.
- Resolved silent path hijacking by implementing "Fail Fast" guardrails: absolute paths outside the workspace now raise an immediate `PermissionError` with contextual feedback, forcing the LLM to correct its path hallucinations.
- Bound `LocalProcessBackend` lifecycle to `PortalService` as a reusable instance, eliminating repetitive and expensive I/O operations (e.g., test file creation/deletion) on every conversational turn.
- Added `workspace_dir` to `SuperPortalConfig` and API allowed fields, enabling dynamic, per-portal physical sandbox isolation. Default portals now automatically fallback to isolated directories (`/tmp/proton_workspaces/<portal_id>`).
- Resolved circular import issues between `ExecutionBackend` and `ExecutionContext` using `TYPE_CHECKING`."""

        subprocess.run(['git', 'commit', '-m', commit_msg], check=True)
        print("Commit successful!")

    except Exception as e:
        print(f"Error during git operations: {e}")

if __name__ == "__main__":
    check_and_commit()