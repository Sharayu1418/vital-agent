"""Sandboxed code execution (D9) + static safety rails (D11).

Two independent layers with distinct jobs:
1. check_code_safety — AST-based static gate BEFORE anything executes.
   Prompt injection can make the model write hostile code; it cannot make
   this parser approve it.
2. The E2B Firecracker microVM — isolates the HOST: approved code runs
   with no secrets, no access to our filesystem or infrastructure, torn
   down per invocation.

What the microVM does NOT guarantee: E2B sandboxes (free tier especially)
may have outbound internet. Exfiltration control is therefore the gate's
URL-literal ban plus, in production, E2B's network-restriction config —
not the VM itself. Keep this mental model straight when reasoning about
what a hostile snippet could do.

The runner is a plain callable `(code: str) -> {"stdout", "error"}` so
tests inject fakes and the analysis graph never imports e2b directly.
"""
import ast
import re
from typing import Callable

from vital.config import settings

RunnerFn = Callable[[str], dict]

BANNED_IMPORTS = {
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "ctypes",
    "importlib", "requests", "urllib", "http", "httpx", "aiohttp",
    "ftplib", "smtplib", "telnetlib", "pickle", "marshal", "signal",
    "multiprocessing", "threading", "webbrowser", "pty", "platform",
    "builtins", "io",  # builtins.open / io.open smuggling
}
BANNED_CALLS = {"exec", "eval", "open", "__import__", "compile", "input",
                "breakpoint", "globals", "locals", "vars", "getattr", "setattr"}
# attribute smuggling: x.open, builtins.eval, pd.io.common.os.popen, ...
BANNED_ATTRS = {"system", "popen", "spawn", "fork", "kill",
                "open", "exec", "eval"}
_URL_RE = re.compile(r"^\s*(https?|ftp|s3|file|gs)://", re.IGNORECASE)


def check_code_safety(code: str) -> tuple[bool, str | None]:
    """Static gate. Returns (ok, reason_if_not).

    Honest scope: this is defense-in-depth, not the sandbox. A determined
    attacker can beat any AST filter; the E2B microVM is the boundary that
    protects our host and secrets. It does NOT necessarily block outbound
    network (see module docstring) — the URL ban here plus E2B network
    restrictions in prod handle exfiltration. The gate exists to cheaply
    stop the common cases and keep honest models honest.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, f"syntax error: {exc.msg} (line {exc.lineno})"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in BANNED_IMPORTS:
                    return False, f"banned import: {root}"
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in BANNED_IMPORTS:
                return False, f"banned import: {root}"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in BANNED_CALLS:
                return False, f"banned call: {node.func.id}()"
        elif isinstance(node, ast.Attribute):
            if node.attr in BANNED_ATTRS:
                return False, f"banned attribute: .{node.attr}"
            if node.attr.startswith("__"):  # ().__class__.__bases__ traversal
                return False, f"banned dunder access: .{node.attr}"
        elif isinstance(node, ast.Name):
            if node.id.startswith("__"):  # __builtins__, __loader__, ...
                return False, f"banned dunder name: {node.id}"
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _URL_RE.match(node.value):  # pd.read_csv('https://...')
                return False, f"network URL in code: {node.value[:60]}"
    return True, None


def make_e2b_runner(files: dict[str, bytes]) -> RunnerFn:
    """Real runner: uploads `files` to /data/ in a fresh microVM, executes,
    tears down. Lazy import so tests never need the e2b package."""
    def run(code: str) -> dict:
        from e2b_code_interpreter import Sandbox  # lazy: needs E2B_API_KEY

        cfg = settings()
        with Sandbox(api_key=cfg.e2b_api_key) as sbx:
            for name, content in files.items():
                sbx.files.write(f"/data/{name}", content)
            execution = sbx.run_code(code, timeout=cfg.sandbox_timeout_seconds)
            return {
                "stdout": "\n".join(execution.logs.stdout),
                "error": execution.error.traceback if execution.error else None,
            }
    return run
