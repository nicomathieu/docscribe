# DocScribe

> **An LLM agent that reads, understands, and authors DOCX documents — extracting structure, filling fields, and preserving formatting.**

Built on [Agno](https://github.com/agno-agi/agno) + [FastMCP](https://github.com/jlowin/fastmcp) + `python-docx`.

---

## What it does

Paste meeting notes, clinical study data, or any structured content into the agent. It:

1. Parses the DOCX template into a structured **AST** (headings, paragraphs, table cells — each with a stable `element_id` and run-level formatting)
2. Exposes **5 MCP tools** the agent can call: `upload_document`, `get_session_documents`, `load_document_ast`, `edit_document`, `validate_document_state`
3. The LLM reads the AST, matches fields to content, and calls `edit_document` with the right `element_id`
4. A **run-level formatter** re-applies the template's bold/italic labels around the new plain-text values — automatically

No HTML conversion. No regex scraping. Pure AST surgery.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│               Agno Agent (LiteLLM / Claude)             │
│   instructions: 5-step workflow                         │
│   tool calls ──────────────────────────────────────┐    │
└────────────────────────────────────────────────────│────┘
                                                     │ HTTP/MCP
┌────────────────────────────────────────────────────▼────┐
│                  FastMCP Server  (:8000)                 │
│                                                         │
│  upload_document ──► converter.build_ast()              │
│  load_document_ast ─► DocumentStore.get_ast()           │
│  edit_document ─────► converter.apply_cell_edit()       │
│                        + run_formatter.create_runs()    │
│  validate_document_state ──► build_ast() comparison     │
└─────────────────────────────────────────────────────────┘
         │ python-docx read/write
┌────────▼────────────────────────────────────┐
│   Storage backend (local FS  or  S3)        │
│   DOCX files  +  AST JSON per file_id       │
└─────────────────────────────────────────────┘
```

### Run-level formatting preservation

The core algorithmic challenge: the template has cells like

```
[bold] "Study Title: " [/bold][plain] "" [/plain]
```

The LLM produces `"A Phase III randomised trial of Zetaribumab"`.  
The formatter:
1. Finds the longest common prefix between the new text and the original template text
2. Maps that prefix to template runs (preserving bold)
3. For remaining text, scans for known bold-label fragments and applies their formatting
4. LLM-generated values always end up plain — no false bold

---

## Quick start

```bash
# 1. Install
pip install -e ".[dev]"

# 2. Configure
cp .env.example .env
# → edit .env: set LITELLM_HOST, LITELLM_API_KEY, MODEL_ID

# 3. Create the CSR template
python examples/csr_demo/create_template.py

# 4. Start the MCP server
uvicorn docscribe.mcp.server:app --port 8000

# 5. Run the demo notebook
jupyter notebook examples/csr_demo/csr_fill_demo.ipynb
```

---

## Project structure

```
docscribe/
├── src/
│   └── docscribe/
│       ├── ast/
│       │   ├── models.py          # DocumentAST, TextRun, TableCellElement, …
│       │   ├── converter.py       # build_ast(), apply_cell_edit(), apply_heading_edit()
│       │   └── run_formatter.py   # create_runs_from_template() — the formatting engine
│       ├── storage/
│       │   ├── local.py           # LocalDocumentStore (filesystem-backed)
│       │   ├── s3.py              # S3DocumentStore (boto3, same interface)
│       │   └── session.py         # SessionRegistry (in-memory)
│       └── mcp/
│           └── server.py          # FastMCP server — 5 tools
├── examples/
│   └── csr_demo/
│       ├── create_template.py     # Generates the fictional CSR DOCX template
│       ├── csr_fill_demo.ipynb    # End-to-end demo notebook
│       └── templates/             # csr_template.docx (generated)
├── pyproject.toml
├── .env.example
└── README.md
```

---

## MCP tools

| Tool | Args | Description |
|---|---|---|
| `upload_document` | `file_path`, `file_id?` | Parse DOCX → AST, register in session |
| `get_session_documents` | `include_metadata?` | List uploaded documents |
| `load_document_ast` | `file_id` | Return full element list with `element_id`, `text`, `runs` |
| `edit_document` | `file_id`, `edits` | Apply cell/heading edits with formatting preservation |
| `validate_document_state` | `file_id`, `create_new_version?` | Verify AST integrity, bump version |

`edit_document` edit format:
```json
[
  {
    "type": "table_cell",
    "element_id": "cell-t0-r1-c1",
    "changes": { "text": "A Phase III randomised trial of Zetaribumab 150 mg SC Q4W" }
  }
]
```

---

## LLM provider

The agent uses Agno's model abstraction. Default is LiteLLM (any OpenAI-compatible proxy):

```python
from agno.models.litellm import LiteLLMOpenAI
model = LiteLLMOpenAI(id="bedrock-claude-4-sonnet", api_key="...", base_url="https://your-proxy/")
```

To switch to direct Anthropic or OpenAI:

```python
from agno.models.anthropic import Claude
model = Claude(id="claude-sonnet-5")

from agno.models.openai import OpenAIChat
model = OpenAIChat(id="gpt-4o")
```

---

## Storage backends

Set `STORE_BACKEND` in `.env`:

| Value | Config | Description |
|---|---|---|
| `local` (default) | `STORE_DIR=./docscribe_store` | Local filesystem |
| `s3` | `S3_BUCKET`, `S3_FOLDER`, `AWS_PROFILE` | S3-backed store, same interface |

```bash
# Start with S3
AWS_PROFILE=my-profile uvicorn docscribe.mcp.server:app --port 8000
```

Install boto3 for S3 support: `pip install ".[s3]"`

---

## Limitations & known gaps

- `apply_cell_edit` rebuilds paragraphs from scratch — complex nested tables with merged cells may lose merge state. Works for standard clinical templates.
- The formatter heuristic works well for `Label: value` patterns. Multi-column mixed formatting may need manual tuning.
- The MCP server is single-process; `SessionRegistry` is in-memory and not shared across workers.

---

## License

MIT
