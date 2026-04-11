"""
Pre-generation safety scanner for Portal synthesis stage.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from ..core.models import PortalMemoryEntry, SafetyScanResult


class PreGenerationSafetyScanner:
    """Rule-based scanner that blocks high-risk synthesis contexts."""

    _RULES: Dict[str, Dict[str, Any]] = {
        "prompt_injection": {
            "severity": "high",
            "patterns": [
                r"ignore\s+(all\s+)?previous\s+instructions",
                r"you\s+are\s+now\s+.*(dan|developer|system)",
                r"system\s+prompt",
                r"jailbreak",
                r"忽略(之前|以上|所有)?(系统|规则|指令)",
                r"越狱",
            ],
            "reason": "检测到提示词注入/越权引导",
        },
        "secret_exfiltration": {
            "severity": "high",
            "patterns": [
                r"api[_\-\s]?key",
                r"access[_\-\s]?token",
                r"private[_\-\s]?key",
                r"password",
                r"/etc/passwd",
                r"ssh-rsa",
            ],
            "reason": "检测到密钥/凭据泄露风险",
        },
        "dangerous_command": {
            "severity": "high",
            "patterns": [
                r"rm\s+-rf\s+/",
                r"curl\s+.*\|\s*(sh|bash)",
                r"wget\s+.*\|\s*(sh|bash)",
                r"os\.system\(",
                r"subprocess\.(popen|run)\(",
            ],
            "reason": "检测到高危命令执行指令",
        },
        "policy_bypass": {
            "severity": "medium",
            "patterns": [
                r"bypass\s+(policy|guard|safety)",
                r"禁用(安全|审核|限制)",
                r"绕过(规则|审批|安全)",
            ],
            "reason": "检测到绕过治理策略意图",
        },
    }

    _SEVERITY_WEIGHT = {"none": 0, "low": 1, "medium": 2, "high": 3}

    def scan(
        self,
        *,
        user_query: str,
        intent: str,
        workflow_results: Dict[str, str],
        memories: List[PortalMemoryEntry],
        memory_snapshot: str,
    ) -> SafetyScanResult:
        corpus = self._build_corpus(
            user_query=user_query,
            intent=intent,
            workflow_results=workflow_results,
            memories=memories,
            memory_snapshot=memory_snapshot,
        )
        matched_rules: List[str] = []
        reasons: List[str] = []
        severity = "none"

        for rule_name, cfg in self._RULES.items():
            patterns = cfg.get("patterns", [])
            if self._matches_any(corpus, patterns):
                matched_rules.append(rule_name)
                reasons.append(str(cfg.get("reason", rule_name)))
                current = str(cfg.get("severity", "low"))
                if self._SEVERITY_WEIGHT.get(current, 0) > self._SEVERITY_WEIGHT.get(severity, 0):
                    severity = current

        blocked = self._SEVERITY_WEIGHT.get(severity, 0) >= self._SEVERITY_WEIGHT["high"]
        return SafetyScanResult(
            blocked=blocked,
            severity=severity,
            reasons=reasons,
            matched_rules=matched_rules,
        )

    @staticmethod
    def _build_corpus(
        *,
        user_query: str,
        intent: str,
        workflow_results: Dict[str, str],
        memories: List[PortalMemoryEntry],
        memory_snapshot: str,
    ) -> str:
        workflow_text = "\n".join(workflow_results.values())
        memory_text = "\n".join(m.content for m in memories)
        parts = [user_query, intent, workflow_text, memory_text, memory_snapshot]
        return "\n".join(p for p in parts if p).lower()

    @staticmethod
    def _matches_any(text: str, patterns: Iterable[object]) -> bool:
        for pattern in patterns:
            if isinstance(pattern, str) and re.search(pattern, text, re.IGNORECASE):
                return True
        return False
