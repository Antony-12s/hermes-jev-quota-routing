"""Print the jev_routes subagent options as JSON for `hermes config set`.

Codex and Gemini routes are added only when the machine has them (HAS_CODEX / HAS_GEMINI = "1"),
so Jev never picks a provider the user cannot run.
"""

import json
import os


def opus(name, description, effort):
    return {"name": name, "description": description, "provider": "anthropic",
            "model": "claude-opus-5-5", "reasoning_effort": effort}


def build(has_codex: bool, has_gemini: bool) -> list[dict]:
    options = [
        opus("fast", "Lookups, file searches, small edits, simple explanations", "low"),
        opus("standard", "Normal coding, reviews of one file, summaries of several sources", "medium"),
        opus("deep", "Hard debugging, multi-file design, long planning, careful writing", "high"),
        {"name": "fable", "provider": "anthropic", "model": "claude-fable-5-2", "reasoning_effort": "high",
         "description": "Coding or reasoning beyond Opus 5.5 that would be slow or fail on it: "
                        "long-horizon agentic coding, big multi-file refactors, hard proofs and algorithms"},
    ]
    if has_codex:
        # Keep `codex` before `design`: the Claude-to-Codex quota fallback takes the first Codex option.
        codex = {"provider": "openai-codex", "model": "gpt-6.1-sol", "reasoning_effort": "high"}
        options.append({"name": "codex", **codex,
                        "description": "Implement a well-specified change, fix a failing test, security fix"})
        options.append({"name": "design", **codex,
                        "description": "Art and visual work: UI and UX design, mockups, logos, SVG, icons, "
                                       "color and layout, making a page look good"})
    if has_gemini:
        options.append({"name": "research", "provider": "antigravity-oauth", "model": "gemini-3.8-flash",
                        "reasoning_effort": "high",
                        "description": "Research and fact-finding: web lookups, price and spec checks, "
                                       "version checks, comparisons, collecting docs with sources"})
    return options


if __name__ == "__main__":
    print(json.dumps(build(os.environ.get("HAS_CODEX") == "1", os.environ.get("HAS_GEMINI") == "1")))
