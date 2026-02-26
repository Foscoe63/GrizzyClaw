"""Parsing utilities for LLM command output (TOOL_CALL, MEMORY_SAVE, etc.)."""

import json
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


def _find_block_ranges(text: str, prefix: str) -> list[tuple[int, int]]:
    """Find (start, end) ranges for PREFIX = { ... } (including prefix and optional ```)."""
    pattern = re.compile(
        re.escape(prefix) + r"\s*=\s*(?:```(?:json)?\s*)?\{",
        re.IGNORECASE,
    )
    ranges: list[tuple[int, int]] = []
    for m in pattern.finditer(text):
        block_start = m.start()
        brace_start = m.end() - 1
        pair = extract_balanced_brace(text, brace_start)
        if pair is None:
            pair = extract_balanced_brace_dumb(text, brace_start)
        if pair:
            ranges.append((block_start, pair[1]))
    return ranges


def strip_response_blocks(text: str) -> str:
    """Remove SKILL_ACTION, TOOL_CALL, EXEC_COMMAND, SCHEDULE_TASK, SPAWN_SUBAGENT blocks so the user sees only pertinent text and tool results."""
    prefixes = ("SKILL_ACTION", "TOOL_CALL", "EXEC_COMMAND", "SCHEDULE_TASK", "SPAWN_SUBAGENT")
    all_ranges: list[tuple[int, int]] = []
    for prefix in prefixes:
        all_ranges.extend(_find_block_ranges(text, prefix))
    all_ranges.sort(key=lambda r: r[0])
    # Merge overlapping and drop contained ranges
    merged: list[tuple[int, int]] = []
    for start, end in all_ranges:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    # Build result by skipping merged ranges; replace each with a single newline if needed to avoid gluing lines
    if not merged:
        return text
    out: list[str] = []
    pos = 0
    for start, end in merged:
        chunk = text[pos:start]
        if chunk.rstrip():
            out.append(chunk.rstrip())
            if not chunk.endswith("\n"):
                out.append("\n")
        pos = end
    if pos < len(text):
        rest = text[pos:]
        if rest.strip():
            out.append(rest.strip())
    return "\n".join(out) if out else text[: merged[0][0]].strip()


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


def find_tool_call_blocks_relaxed(text: str) -> list[str]:
    """Relaxed fallback for TOOL_CALL: handles formats like to=TOOL_CALL <|constrain|>json<|message|>{...}.

    Some models (e.g. gpt-oss-120b-mlx-gs32) output TOOL_CALLs with special tokens instead of
    the standard 'TOOL_CALL = {...}' format. This searches for 'TOOL_CALL' then the next {...}
    and validates it has mcp + tool keys.
    """
    blocks: list[str] = []
    idx = 0
    # Match "TOOL_CALL" or "tool call" when part of a token (e.g. <|channel|>tool call or to=TOOL_CALL)
    # Avoid matching prose like "We need tool call to" - require "tool call" followed by <| or similar
    pattern = re.compile(
        r"TOOL_CALL|(?<=[>=|\s])tool\s*call(?=\s*[<|]|\s*$)",
        re.IGNORECASE,
    )
    while True:
        m = pattern.search(text[idx:])
        if not m:
            break
        after_match = idx + m.end()
        window = text[after_match : after_match + 1200]  # Allow long preamble (e.g. to=TOOL_CALL code<|message|>)
        brace_pos = window.find("{")
        if brace_pos == -1:
            idx = after_match + 1
            continue
        brace = after_match + brace_pos
        pair = extract_balanced_brace(text, brace)
        if pair is None:
            pair = extract_balanced_brace_dumb(text, brace)
        if pair:
            raw = text[pair[0] : pair[1]]
            try:
                normalized = normalize_llm_json(raw)
                obj = json.loads(normalized)
                if isinstance(obj, dict) and "mcp" in obj and "tool" in obj:
                    blocks.append(raw)
            except (json.JSONDecodeError, ValueError):
                pass
        idx = after_match + 1
    return blocks


def find_tool_call_blocks_raw_json(text: str) -> list[str]:
    """Find JSON objects with mcp+tool keys when model outputs raw JSON without TOOL_CALL keyword.

    Some models describe actions in text ('Now writing files...') then output raw JSON like
    {"mcp": "fast-filesystem", "tool": "write_file", "arguments": {...}} without the TOOL_CALL=
    prefix. This scans for such objects.
    """
    seen: set[tuple[int, int]] = set()
    blocks: list[str] = []
    # Look for "mcp" as a JSON key - typically "mcp": or "mcp":
    for m in re.finditer(r'"mcp"\s*:\s*', text, re.IGNORECASE):
        key_pos = m.start()
        # Find opening { before this (within 80 chars - key must be inside object)
        search_start = max(0, key_pos - 80)
        chunk = text[search_start : key_pos + 1]
        brace_pos = chunk.rfind("{")
        if brace_pos == -1:
            continue
        abs_brace = search_start + brace_pos
        pair = extract_balanced_brace(text, abs_brace)
        if pair is None:
            pair = extract_balanced_brace_dumb(text, abs_brace)
        if not pair:
            continue
        rng = (pair[0], pair[1])
        if rng in seen:
            continue
        seen.add(rng)
        raw = text[pair[0] : pair[1]]
        try:
            normalized = normalize_llm_json(raw)
            obj = json.loads(normalized)
            if isinstance(obj, dict) and "mcp" in obj and "tool" in obj:
                blocks.append(raw)
        except (json.JSONDecodeError, ValueError):
            pass
    return blocks


def find_write_file_path_content_blocks(text: str) -> list[tuple[str, str]]:
    """Find JSON objects with path+content when model uses fast-filesystem.fast_write_file format.

    Some models (e.g. gpt-oss-120b-mlx-gs32) output:
      to=fast-filesystem.fast_write_file <|constrain|>json<|message|>{"path": "...", "content": "..."}
    with no mcp/tool/arguments wrapper. This finds such objects and returns [(path, content)].
    """
    results: list[tuple[str, str]] = []
    seen: set[tuple[int, int]] = set()
    for m in re.finditer(r'"path"\s*:\s*', text, re.IGNORECASE):
        key_pos = m.start()
        search_start = max(0, key_pos - 120)
        chunk = text[search_start : key_pos + 1]
        brace_pos = chunk.rfind("{")
        if brace_pos == -1:
            continue
        abs_brace = search_start + brace_pos
        pair = extract_balanced_brace(text, abs_brace)
        if pair is None:
            pair = extract_balanced_brace_dumb(text, abs_brace)
        if not pair:
            continue
        rng = (pair[0], pair[1])
        if rng in seen:
            continue
        seen.add(rng)
        raw = text[pair[0] : pair[1]]
        try:
            normalized = normalize_llm_json(raw)
            obj = json.loads(normalized)
            if isinstance(obj, dict) and "path" in obj and "content" in obj:
                path = str(obj.get("path", "")).strip()
                content = obj.get("content", "")
                if path and (path.endswith(".swift") or path.endswith(".py") or "." in path):
                    results.append((path, str(content) if content is not None else ""))
        except (json.JSONDecodeError, ValueError):
            pass
    return results


def _extract_base_path_from_response(text: str) -> str | None:
    """Extract base directory from phrases like 'written to **/Volumes/Storage/ZZZ**' or 'to **/path**'."""
    # written to **/path**, to **/path**, in **/path**, **/Volumes/Storage/ZZZ**
    for pat in [
        r"written\s+to\s+\*+\s*([/\w\-\.]+?)\s*\*+",
        r"will\s+be\s+written\s+to\s+\*+\s*([/\w\-\.]+?)\s*\*+",
        r"to\s+\*+\s*([/\w\-\.]+?)\s*\*+",
        r"in\s+\*+\s*([/\w\-\.]+?)\s*\*+",
        r"\*\*([/][^*`\n]+?)\*\*",
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            p = m.group(1).strip().rstrip("/")
            if p.startswith("/") and len(p) > 3:
                return p
    return None


def extract_code_blocks_for_file_creation(
    text: str, base_path_hint: str | None = None
) -> list[tuple[str, str]]:
    """Extract (full_path, content) from markdown when model shows code instead of TOOL_CALL.

    Looks for **FileName.swift** or similar before ```language blocks, and a base path
    from phrases like 'written to **/Volumes/Storage/ZZZ**'.
    Returns [(full_path, content), ...] for use with write_file.
    """
    base = base_path_hint or _extract_base_path_from_response(text)
    if not base:
        return []
    # Normalize: remove spaces and zero-width chars (model may add Z​ZZZ or "/Volumes /storage/Zzzz")
    for zw in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        base = base.replace(zw, "")
    base = base.replace(" ", "").strip()

    results: list[tuple[str, str]] = []
    # Match ```lang newline content ```
    for m in re.finditer(r"```(\w*)\n([\s\S]*?)```", text):
        lang, content = m.group(1).strip(), m.group(2).strip()
        if not content or len(content) > 100_000:
            continue
        # Look backwards for filename (within 350 chars before block start)
        start = max(0, m.start() - 350)
        before = text[start : m.start()]
        filename = None
        # **TodoListApp.swift**, `MyTodoApp.swift`, #### 1️⃣ TodoListApp.swift, | **TodoListApp.swift**
        for pat in [
            r"`([A-Za-z0-9_\-]+\.[a-zA-Z0-9]+)`",
            r"\*\*([A-Za-z0-9_\-]+\.[a-zA-Z0-9]+)\*\*",
            r"\|?\s*\*\*([A-Za-z0-9_\-]+\.[a-zA-Z0-9]+)\*\*",
            r"#{2,6}[^A-Za-z]*`?([A-Za-z0-9_\-]+\.[a-zA-Z]{2,10})`?(?:\s|$|[|–\-])",
            r"([A-Za-z0-9_\-]+\.[a-zA-Z0-9]+)\s*[–\-]\s*",
            r"([A-Za-z0-9_\-]+\.[a-zA-Z0-9]+)\s*\|",
        ]:
            matches = list(re.finditer(pat, before))
            if matches:
                # Prefer the one closest to the code block
                cand = matches[-1].group(1).strip()
                if "." in cand and not cand.startswith("."):
                    filename = cand
                    break
        # Also look after block (e.g. "Add a new Swift file named **ContentView.swift**")
        if not filename:
            end = min(len(text), m.end() + 300)
            after = text[m.end() : end]
            for pat in [
                r"(?:named|add|create|paste into)\s+(?:\*\*)?`?([A-Za-z0-9_\-]+\.[a-zA-Z0-9]+)`?(?:\*\*)?",
                r"\*\*([A-Za-z0-9_\-]+\.[a-zA-Z0-9]+)\*\*(?:\s+and|\s*\.)",
            ]:
                ma = re.search(pat, after, re.IGNORECASE)
                if ma:
                    cand = ma.group(1).strip()
                    if "." in cand and not cand.startswith("."):
                        filename = cand
                        break
        if not filename:
            continue
        full_path = f"{base.rstrip('/')}/{filename}"
        results.append((full_path, content))
    return results


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
    """Fix common LLM JSON: backslashes before quotes, smart quotes, code fences, double braces."""
    s = strip_code_fence(s)
    s = strip_json_comments(s)
    # Fix double braces {{ }} -> { } (LLMs copy from escaped prompt templates)
    s = re.sub(r'\{\{', '{', s)
    s = re.sub(r'\}\}', '}', s)
    # Normalize all Unicode quote chars to ASCII (models often emit „ " " etc.)
    for _o, _r in [
        ("\u201c", '"'), ("\u201d", '"'), ("\u201e", '"'), ("\u201f", '"'),
        ("\u00ab", '"'), ("\u00bb", '"'),
        ("\u2018", "'"), ("\u2019", "'"), ("\u201a", "'"), ("\u201b", "'"),
    ]:
        s = s.replace(_o, _r)
    s = re.sub(r'([{,]\s*)\\+"', r'\1"', s)
    s = re.sub(r'\\+":', '":', s)
    s = re.sub(r'\\+",', '",', s)
    s = re.sub(r'{\\+"', '{"', s)
    s = re.sub(r'\\+"}', '"}', s)
    s = re.sub(r'\\+"\s*}', '" }', s)
    s = re.sub(r':\s*\\+"', ': "', s)
    # Fix errant backslash before key names: \"path\" -> "path"
    for _key in (
        "mcp", "tool", "arguments", "path", "content",
        "recursive", "create_dirs", "old_text", "new_text", "backup", "overwrite",
    ):
        s = re.sub(rf'\\"{_key}\\"', f'"{_key}"', s)
    s = re.sub(r',\s*}', '}', s)
    s = re.sub(r',\s*]', ']', s)
    return s


def repair_json_single_quotes(s: str) -> str:
    """Convert Python-style single-quoted strings to JSON double-quoted so json.loads accepts it.
    Handles 'key': 'value' and escaped quotes inside single-quoted strings.
    """
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == '"':
            # Double-quoted string: keep as-is, skip to closing "
            out.append(c)
            i += 1
            while i < n:
                c2 = s[i]
                if c2 == "\\":
                    out.append(s[i : i + 2])
                    i += 2
                    continue
                if c2 == '"':
                    out.append(c2)
                    i += 1
                    break
                out.append(c2)
                i += 1
            continue
        if c == "'":
            # Single-quoted string: convert to "..."
            start = i
            i += 1
            content: list[str] = []
            while i < n:
                c2 = s[i]
                if c2 == "\\":
                    if i + 1 < n:
                        content.append(s[i : i + 2])
                        i += 2
                    else:
                        content.append("\\")
                        i += 1
                    continue
                if c2 == "'":
                    i += 1
                    break
                content.append(c2)
                i += 1
            # JSON-escape content: \ -> \\, " -> \"
            raw = "".join(content)
            escaped = raw.replace("\\", "\\\\").replace('"', '\\"')
            out.append('"')
            out.append(escaped)
            out.append('"')
            continue
        out.append(c)
        i += 1
    return "".join(out)
