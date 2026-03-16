"""Crash-Agent — core dump analysis with LLM assistance.

Monitors for new core dumps, extracts backtraces, and produces
human-readable crash analysis reports via tiered reasoning.
"""

from __future__ import annotations

import asyncio
import json
import time
import structlog

from yaaos_agentd.agent_base import BaseAgent
from yaaos_agentd.types import Action, ActionResult, AgentSpec

logger = structlog.get_logger()


class CrashAgent(BaseAgent):
    """Analyzes core dumps using coredumpctl and optional LLM reasoning.

    Designed for transient restart policy — activates on demand,
    analyzes available core dumps, then exits.
    """

    def __init__(self, spec: AgentSpec, **kwargs):
        super().__init__(spec, **kwargs)
        self._analyzed: set[str] = set()  # PIDs/timestamps already analyzed
        self._llm_enabled: bool = spec.config.get("llm_enabled", False)
        self._max_dumps: int = spec.config.get("max_dumps_per_cycle", 5)

    async def observe(self) -> dict:
        """Query coredumpctl for recent core dumps."""
        dumps: list[dict] = []

        try:
            proc = await asyncio.create_subprocess_exec(
                "coredumpctl",
                "list",
                "--json=short",
                "--no-pager",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)

            if proc.returncode == 0 and stdout:
                try:
                    raw_dumps = json.loads(stdout)
                    if isinstance(raw_dumps, list):
                        dumps = raw_dumps
                except json.JSONDecodeError:
                    self._log.warning("crash_agent.parse_error", stderr=stderr.decode()[:200])

        except FileNotFoundError:
            self._log.debug("crash_agent.coredumpctl_not_found")
        except asyncio.TimeoutError:
            self._log.warning("crash_agent.coredumpctl_timeout")
        except Exception as e:
            self._log.warning("crash_agent.observe_error", error=str(e))

        # Filter out already-analyzed dumps
        new_dumps = []
        for dump in dumps[-self._max_dumps :]:
            dump_id = f"{dump.get('pid', '')}-{dump.get('timestamp', '')}"
            if dump_id not in self._analyzed:
                new_dumps.append(dump)

        return {
            "total_dumps": len(dumps),
            "new_dumps": len(new_dumps),
            "dumps": new_dumps,
        }

    async def reason(self, observation: dict) -> list[Action]:
        """Decide which dumps to analyze."""
        actions: list[Action] = []

        for dump in observation.get("dumps", []):
            exe = dump.get("exe", dump.get("comm", "unknown"))
            signal = dump.get("sig", "unknown")
            pid = dump.get("pid", "unknown")

            actions.append(
                Action(
                    tool="coredumpctl",
                    action="backtrace",
                    params={
                        "pid": str(pid),
                        "exe": exe,
                        "signal": str(signal),
                        "timestamp": dump.get("timestamp", ""),
                    },
                    description=f"Analyze crash: {exe} (PID {pid}, signal {signal})",
                )
            )

        return actions

    async def act(self, actions: list[Action]) -> list[ActionResult]:
        """Extract backtraces and produce analysis reports."""
        results: list[ActionResult] = []

        for action in actions:
            start = time.monotonic()
            pid = action.params.get("pid", "")
            exe = action.params.get("exe", "unknown")

            try:
                # Extract backtrace
                backtrace = await self._extract_backtrace(pid)

                # Build analysis
                analysis_parts = [
                    f"Crash Analysis: {exe}",
                    f"PID: {pid}",
                    f"Signal: {action.params.get('signal', '?')}",
                    "",
                    "Backtrace:",
                    backtrace or "(no backtrace available)",
                ]

                # LLM analysis if enabled and Model Bus available
                if self._llm_enabled and self.model_bus and backtrace:
                    try:
                        llm_analysis = await self._llm_analyze(exe, backtrace, action.params)
                        analysis_parts.extend(["", "LLM Analysis:", llm_analysis])
                    except Exception as e:
                        analysis_parts.extend(["", f"LLM analysis failed: {e}"])

                analysis = "\n".join(analysis_parts)

                # Mark as analyzed
                dump_id = f"{pid}-{action.params.get('timestamp', '')}"
                self._analyzed.add(dump_id)

                self._log.info(
                    "crash_agent.analyzed",
                    exe=exe,
                    pid=pid,
                    has_backtrace=bool(backtrace),
                )

                results.append(
                    ActionResult(
                        action=action,
                        success=True,
                        output=analysis[:2000],
                        duration_ms=(time.monotonic() - start) * 1000,
                    )
                )

            except Exception as e:
                results.append(
                    ActionResult(
                        action=action,
                        success=False,
                        error=str(e),
                        duration_ms=(time.monotonic() - start) * 1000,
                    )
                )

        return results

    async def _extract_backtrace(self, pid: str) -> str:
        """Extract backtrace from a core dump using coredumpctl."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "coredumpctl",
                "debug",
                str(pid),
                "--debugger-arguments=-batch -ex bt -ex quit",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            return stdout.decode("utf-8", errors="replace").strip()
        except FileNotFoundError:
            return ""
        except asyncio.TimeoutError:
            return "(backtrace extraction timed out)"
        except Exception as e:
            return f"(backtrace error: {e})"

    async def _llm_analyze(self, exe: str, backtrace: str, params: dict) -> str:
        """Send crash data to Model Bus for LLM analysis."""
        prompt = (
            f"Analyze this crash and suggest a fix.\n\n"
            f"Executable: {exe}\n"
            f"Signal: {params.get('signal', '?')}\n\n"
            f"Backtrace:\n{backtrace[:3000]}\n\n"
            f"Explain the likely cause and suggest a fix."
        )
        # model_bus.generate() returns an async iterator of chunks
        text_parts: list[str] = []
        async for chunk in self.model_bus.generate(prompt, stream=False):
            if chunk.get("done"):
                return (chunk.get("text", "".join(text_parts)))[:2000]
            if "token" in chunk:
                text_parts.append(chunk["token"])
        return "".join(text_parts)[:2000]
