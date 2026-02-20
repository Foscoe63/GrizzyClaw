"""Parsing utilities for LLM command output (TOOL_CALL, MEMORY_SAVE, etc.)."""

import re
from typing import Optional


def strip_json_comments(s: str) -> str:
    """Remove // and /* */ comments so json.loads accepts LLM output with comments."""
    s = re.sub(r",?\s*//[^\n]*", "", s)
    s = re.sub(r"/\*[\s\S]*?\*/", "", s)
    return s


def extract_balanced_brace(s: str, start: int) -> Optional[tuple[int, int]]:
    """From index of '{', return (start, end) of matching '}' (handles nesting)."""
    if start < 0 or start >= len(s) or s[start] != "{":
        return None
    depth = 0
    i = start
    in_string = None
    escape = False
    while i < len(s):
        c = s[i]
        if escape:
            escape = False
            i += 1
            continue
        if c == "\\" and in_string:
            escape = True
            i += 1
            continue
        if in_string:
            if c == in_string:
                in_string = None
            i += 1
            continue
        if c in ('"', "'"):
            in_string = c
            i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return (start, i + 1)
        i += 1
    return None


def extract_balanced_brace_dumb(s: str, start: int) -> Optional[tuple[int, int]]:
    """From index of '{', return (start, end) by counting braces only."""
    if start < 0 or start >= len(s) or s[start] != "{":
        return None
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return (start, i + 1)
    return None


def find_json_blocks(text: str, prefix: str) -> list[str]:
    """Find all PREFIX = [optional ```] { ... } with balanced braces."""
    pattern = re.compile(
        re.escape(prefix) + r"\s*=\s*(?:```(?:json)?\s*)?\{",
        re.IGNORECASE,
    )
    blocks: list[str] = []
    for m in pattern.finditer(text):
        brace_start = m.end() - 1
        pair = extract_balanced_brace(text, brace_start)
        if pair is None:
            pair = extract_balanced_brace_dumb(text, brace_start)
        if pair:
            blocks.append(text[pair[0] : pair[1]])
    return blocks


def find_json_blocks_fallback(text: str, prefix: str) -> list[str]:
    """Fallback: find PREFIX = then { within 400 chars and extract balanced block."""
    blocks: list[str] = []
    idx = 0
    pattern = re.compile(re.escape(prefix) + r"\s*=", re.IGNORECASE)
    while True:
        m = pattern.search(text[idx:])
        if not m:
            break
        start = idx + m.end()
        window = text[start : start + 400]
        brace_in_window = window.find("{")
        if brace_in_window == -1:
            idx = start + 1
            continue
        brace = start + brace_in_window
        pair = extract_balanced_brace_dumb(text, brace)
        if pair:
            blocks.append(text[pair[0] : pair[1]])
        idx = start + 1
    return blocks


def find_schedule_task_fallback(text: str) -> list[str]:
    """Fallback: find SCHEDULE_TASK then = then { and extract balanced block."""
    return find_json_blocks_fallback(text, "SCHEDULE_TASK")


def strip_code_fence(s: str) -> str:
    """Remove leading/trailing markdown code fence lines."""
    s = s.strip()
    if s.startswith("```"):
        first = s.find("\n")
        if first != -1:
            s = s[first + 1 :]
        else:
            s = s[3:].strip()
    if s.rstrip().endswith("```"):
        s = s[: s.rfind("```")].rstrip()
    return s.strip()


def normalize_llm_json(s: str) -> str:
    """Fix common LLM JSON: backslashes before quotes, smart quotes, code fences."""
    s = strip_code_fence(s)
    s = strip_json_comments(s)
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2018", "'").replace("\u2019", "'")
    s = re.sub(r'([{,]\s*)\\+"', r'\1"', s)
    s = re.sub(r'\\+":', '":', s)
    s = re.sub(r'\\+",', '",', s)
    s = re.sub(r'{\\+"', '{"', s)
    s = re.sub(r'\\+"}', '"}', s)
    s = re.sub(r'\\+"\s*}', '" }', s)
    s = re.sub(r':\s*\\+"', ': "', s)
    s = re.sub(r',\s*}', '}', s)
    s = re.sub(r',\s*]', ']', s)
    return s
