#!/usr/bin/env python3
"""
Ollama MCP Server — Local LLM tools for Claude Code.

Provides tools to delegate tasks to local Ollama models (GPU-accelerated).
Designed for: RE analysis, code generation, text parsing, summarization.

Uses the same FastMCP stack as claude-memory server.
Version: 1.0
"""

import json
import urllib.request
import urllib.error
from mcp.server.fastmcp import FastMCP

OLLAMA_URL = "http://localhost:11434"

# Model selection: coding tasks → coder model, everything else → general
MODEL_CODING = "qwen2.5-coder:7b"
MODEL_CODING_ALT = "deepseek-coder:6.7b"  # deepseek coding, 3.8GB
MODEL_GENERAL = "llama3.1:8b"
MODEL_REASONING = "qwen3:8b"


def ollama_generate(model: str, prompt: str, system: str = "", temperature: float = 0.3) -> str:
    """Call Ollama API with streaming disabled for simplicity."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_ctx": 4096,
        },
    }
    if system:
        payload["system"] = system

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
            return result.get("response", "")
    except urllib.error.URLError as e:
        return f"ERROR: Ollama non raggiungibile ({e}). Verificare: systemctl status ollama"
    except Exception as e:
        return f"ERROR: {e}"


def ollama_api(endpoint: str) -> dict:
    """Generic Ollama API GET call."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}{endpoint}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


# --- MCP Server ---

server = FastMCP(
    "ollama",
    instructions="""\
Local LLM tools powered by Ollama (GPU-accelerated on RTX 3070).
Use these for delegating bulk/repetitive tasks to save Claude context:
- llm_analyze: parse disassembly, logs, binary output
- llm_code: generate scripts, hooks, boilerplate code
- llm_summarize: condense long text (docs, papers, output)
- llm_extract: extract structured data from unstructured text
- llm_models: check available models and GPU status
""",
)


@server.tool()
def llm_analyze(text: str, prompt: str, model: str = "") -> str:
    """Analyze text using a local LLM. Good for parsing disassembly, logs, binary output, patterns.

    Args:
        text: The text to analyze (disassembly, log output, dump, etc.).
        prompt: What to look for or analyze (e.g. "find all function calls", "identify the crypto algorithm").
        model: Override model (default: auto-select based on content).
    """
    m = model or MODEL_REASONING
    system = (
        "You are a reverse engineering and security analysis assistant. "
        "Analyze the provided data precisely. Be concise and technical. "
        "Output findings in a structured format."
    )
    full_prompt = f"## Task\n{prompt}\n\n## Data\n```\n{text[:6000]}\n```"
    return ollama_generate(m, full_prompt, system=system)


@server.tool()
def llm_code(prompt: str, language: str = "python", model: str = "") -> str:
    """Generate code using a local LLM. Good for Frida hooks, exploit PoC, scripts, boilerplate.

    Args:
        prompt: What code to generate (e.g. "Frida hook for SSL_read that dumps plaintext").
        language: Programming language (python, javascript, c, bash, etc.).
        model: Override model (default: qwen2.5-coder:7b).
    """
    m = model or MODEL_CODING
    system = (
        f"You are an expert {language} developer specializing in security tools, "
        f"reverse engineering, and exploit development. Generate clean, working code. "
        f"Output ONLY the code, no explanations unless critical."
    )
    return ollama_generate(m, prompt, system=system, temperature=0.2)


@server.tool()
def llm_summarize(text: str, focus: str = "", model: str = "") -> str:
    """Summarize long text using a local LLM. Good for docs, papers, long output.

    Args:
        text: The text to summarize.
        focus: Optional focus area (e.g. "security implications", "API endpoints").
        model: Override model (default: llama3.1:8b).
    """
    m = model or MODEL_GENERAL
    system = "Summarize concisely. Keep technical details. Use bullet points."
    focus_str = f" Focus on: {focus}." if focus else ""
    full_prompt = f"Summarize this text.{focus_str}\n\n{text[:8000]}"
    return ollama_generate(m, full_prompt, system=system)


@server.tool()
def llm_extract(text: str, schema: str, model: str = "") -> str:
    """Extract structured data from unstructured text. Returns JSON.

    Args:
        text: The text to extract from (GDB output, strace log, Ghidra decompilation, etc.).
        schema: What to extract as JSON schema (e.g. "functions: [{name, address, args}]").
        model: Override model (default: qwen2.5-coder:7b).
    """
    m = model or MODEL_CODING
    system = (
        "You are a data extraction tool. Extract the requested information from the text "
        "and return it as valid JSON. Nothing else — only the JSON output."
    )
    full_prompt = (
        f"Extract the following structure from the text:\n"
        f"Schema: {schema}\n\n"
        f"Text:\n```\n{text[:6000]}\n```\n\n"
        f"Return valid JSON only."
    )
    return ollama_generate(m, full_prompt, system=system, temperature=0.1)


@server.tool()
def llm_models() -> str:
    """List available Ollama models and GPU status."""
    # Models
    models_data = ollama_api("/api/tags")
    if "error" in models_data:
        return f"Ollama error: {models_data['error']}"

    models = models_data.get("models", [])
    lines = ["## Ollama Models\n"]
    for m in models:
        name = m.get("name", "?")
        size_gb = m.get("size", 0) / 1e9
        details = m.get("details", {})
        params = details.get("parameter_size", "?")
        quant = details.get("quantization_level", "?")
        lines.append(f"- **{name}** — {size_gb:.1f}GB, {params}, {quant}")

    # Running models
    ps_data = ollama_api("/api/ps")
    running = ps_data.get("models", [])
    if running:
        lines.append("\n## Currently Loaded")
        for r in running:
            name = r.get("name", "?")
            vram = r.get("size_vram", 0) / 1e9
            lines.append(f"- {name} — {vram:.1f}GB VRAM")
    else:
        lines.append("\n_No models currently loaded in VRAM_")

    # Defaults
    lines.append(f"\n## Defaults")
    lines.append(f"- Coding: {MODEL_CODING}")
    lines.append(f"- General: {MODEL_GENERAL}")
    lines.append(f"- Reasoning: {MODEL_REASONING}")

    return "\n".join(lines)


if __name__ == "__main__":
    server.run(transport="stdio")
