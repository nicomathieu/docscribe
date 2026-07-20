# How I Built an LLM Agent That Actually Fills Word Documents — Without Destroying the Formatting

*From proprietary enterprise pipeline to a public 5-file open-source demo*

---

## The problem nobody talks about

Everyone demos LLM agents generating markdown. The real world runs on Word documents.

In pharma, biotech, and regulated industries, the artifacts that matter — Clinical Study Reports, protocols, consent forms — are DOCX files. They have structure. They have bold labels, column headers, specific fonts, tracked changes. A junior scientist spends hours copy-pasting data from Excel into the right cell of the right table, in the right format.

I spent the last year building exactly this kind of agent inside an enterprise platform. This post extracts the core methodology into a public, minimal repo you can run in 10 minutes.

---

## The naive approach (and why it fails)

The obvious approach is: give the LLM the document as text, ask it to produce a filled version, then... what? You can't just write text back to a DOCX. Word's XML is deeply nested, runs carry formatting attributes, merged cells have ghost references, and any serialisation library that isn't `python-docx` will silently corrupt your file.

Two failure modes I hit repeatedly:

1. **Format destruction**: the LLM generates bold text where the template had plain text, or vice-versa. A CSR where the study title is accidentally bold when the label already is looks off and may fail document QC.

2. **Structure blindness**: asking the LLM to fill cell (row 3, col 2, table 4) is fragile — table indices shift when you add content. You need stable IDs.

The solution is an **AST layer**.

---

## The AST approach

Instead of treating a DOCX as a text file, I parse it into a document Abstract Syntax Tree: every heading, paragraph, and table cell becomes a Python dataclass with:

- A **stable `element_id`** tied to position in the document (`cell-t0-r1-c1`)
- The **text content**
- The **run list**: each text fragment with its formatting (`bold`, `italic`, `font_name`, `font_size_pt`)

The LLM never touches the DOCX binary. It only reads and writes the AST through tool calls.

```
Template DOCX
    │
    ▼ build_ast()
DocumentAST
  elements = [
    HeadingElement(element_id="heading-0", text="1. Study Identification", level=1)
    TableCellElement(element_id="cell-t0-r0-c0", text="Study Title:", runs=[TextRun(text="Study Title:", bold=True)])
    TableCellElement(element_id="cell-t0-r0-c1", text="", runs=[])
    ...
  ]
```

The agent calls `load_document_ast("csr-template")`, gets the full list, identifies `cell-t0-r0-c1` as the empty cell next to the bold "Study Title:" label, and issues:

```json
{
  "type": "table_cell",
  "element_id": "cell-t0-r0-c1",
  "changes": { "text": "A Phase III randomised trial of Zetaribumab 150 mg SC Q4W in gMG" }
}
```

---

## The formatting preservation problem

The elegant part — and the one I see skipped in every blog post — is **run-level formatting preservation**.

The template cell for "Study Title" might have two runs:

```
Run 1: text="Study Title: ", bold=True, font_name="Calibri", font_size_pt=10
Run 2: text="", bold=False, font_name="Calibri", font_size_pt=10
```

When the LLM fills in the value, you don't want to overwrite the label run. You want:

```
Run 1: text="Study Title: ", bold=True
Run 2: text="A Phase III randomised trial of Zetaribumab", bold=False
```

My `create_runs_from_template()` function does this in four steps:

1. **Common prefix match**: find the longest common prefix between new text and original template text
2. **Prefix → runs mapping**: map that prefix to the original template runs (preserving bold/italic)
3. **Sequential matching**: for the remaining new text, try to match runs sequentially against remaining template fragments
4. **Pattern search fallback**: scan for known bold-label fragments (e.g. `"Study Title: "`) and wrap them in bold; everything else becomes plain

This gives you a result that looks like it was typed by a human who understood the formatting — because the LLM-generated value is always plain, and template labels are always bold.

---

## The MCP layer

The agent connects to the tools via the **Model Context Protocol** (MCP) — a standardised HTTP interface where tools are discoverable and schema-typed.

I expose five tools:

| Tool | What it does |
|---|---|
| `upload_document` | Parse DOCX → AST, register in session |
| `get_session_documents` | List what's been uploaded |
| `load_document_ast` | Return full element list |
| `edit_document` | Apply edits with formatting preservation |
| `validate_document_state` | Check AST integrity, bump version |

The agent's system prompt encodes a deterministic 5-step workflow. The agent doesn't improvise the steps — it follows the protocol, which means it's reproducible and auditable.

---

## The demo: filling a Clinical Study Report

The public repo includes a fictional (but structurally realistic) CSR template with:
- Study identification table
- Synopsis table with multi-field cells
- Efficacy results tables (including a 3-column numerical results table)
- Safety summary
- Conclusions and signatures

You paste in raw meeting notes, the agent fills the entire document end-to-end in one shot.

The key insight: a clinical study report has a defined structure (ICH E3 guideline). The LLM doesn't need to understand biostatistics — it needs to match field names to values. The AST makes that mapping tractable.

---

## What I stripped out to make this public

The enterprise version had:

- **AWS S3** for document storage → replaced with `LocalDocumentStore` (15 lines)
- **PostgreSQL** for conversation tracking → replaced with `SessionRegistry` (in-memory dict)
- **Redis** for AST caching → just read from disk; fast enough for demo
- **Private LiteLLM gateway** → direct Anthropic API key
- **Internal auth** (x-session-data headers, STS credentials) → removed entirely
- **Monitoring SDK** → dropped

The core algorithm — AST conversion, run formatter, MCP tools — is identical. The enterprise layer adds multi-tenancy and cloud scale; the demo layer adds clarity.

---

## Run it yourself

```bash
git clone https://github.com/nmathieufact/docfill-agent
cd docfill-agent
pip install -e ".[dev]"
cp .env.example .env  # add your ANTHROPIC_API_KEY

# Generate the CSR template
python examples/csr_demo/create_template.py

# Start MCP server
uvicorn docfill.mcp.server:app --port 8000

# Open the notebook
jupyter notebook examples/csr_demo/csr_fill_demo.ipynb
```

---

## What's next

- **Streaming edits**: apply edits as the agent generates them, show live progress in the document
- **Change tracking**: write back with Word's tracked-changes XML so a human can review/accept
- **Table detection improvement**: handle merged cells and irregular table layouts
- **Evaluation harness**: score fill accuracy against a golden dataset (I'm building this separately)

The repo is at [github.com/nmathieufact/docfill-agent](https://github.com/nmathieufact/docfill-agent). PRs welcome.

---

*Nicolas Mathieu — AI Engineer. Building agents for regulated industries.*
