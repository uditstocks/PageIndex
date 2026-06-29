# API & CLI Reference

This page is the integration reference for **VectorlessRAG**. It documents the
public surface of the **PageIndex** engine: the `PageIndexClient` class, the
low-level retrieval tool functions, the two indexing entry points, and the
`run_pageindex.py` command-line tool.

Everything here reflects the real code in [`../pageindex/`](../pageindex/) and
[`../run_pageindex.py`](../run_pageindex.py). For the exact JSON shapes that
these methods return, see [`./data-formats.md`](./data-formats.md); for the
meaning of each configuration key, see [`./configuration.md`](./configuration.md).

> Scope note: the interactive 4-stage query pipeline (Librarian, Navigator,
> Reader, Generator) lives in [`../RAGG.py`](../RAGG.py) and is described in
> [`./architecture.md`](./architecture.md). This page covers the **library and
> CLI** you script against, not the interactive REPL.

---

## `PageIndexClient` (`pageindex/client.py`)

`PageIndexClient` is the high-level entry point: index a file, then read back
its metadata, its hierarchical tree, or the text of specific pages. It is
defined in [`../pageindex/client.py`](../pageindex/client.py) and exported from
the package, so you can import it directly:

```python
from pageindex import PageIndexClient
```

### Constructor

```python
PageIndexClient(
    api_key: str = None,
    model: str = None,
    retrieve_model: str = None,
    embedding_model: str = None,
    workspace: str = None,
)
```

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `api_key` | `str` | `None` | Auth key for the LLM provider. When set, it is exported to `OPENAI_API_KEY` for the OpenAI-compatible client used by the indexing internals. |
| `model` | `str` | `None` | Main LLM for indexing, TOC processing, and summaries. Falls back to the `model` default in [`config.yaml`](../pageindex/config.yaml) when unset. |
| `retrieve_model` | `str` | `None` | LLM used for retrieval reasoning. Falls back to `retrieve_model` from config, and then to `model` if that is also unset. |
| `embedding_model` | `str` | `None` | Embedding model used to embed each document's description. When unset, no description embedding is generated. |
| `workspace` | `str` | `None` | Directory for persistence. When provided, the client creates it, persists each indexed document, and lazy-loads existing documents on construction. |

Notes:

- If `workspace` is set, the constructor loads any previously indexed documents
  from the workspace registry so they are immediately queryable.
- `retrieve_model` is normalized internally so that provider-prefixed model
  paths route through LiteLLM consistently.

### Persistence model

When a `workspace` is configured, the client writes two kinds of files (see
[`./data-formats.md`](./data-formats.md) for full schemas):

| File | Contents |
| --- | --- |
| `workspace/<doc_id>.json` | The full document: tree `structure` plus cached `pages` (PDF). For PDFs, raw node `text` is stripped here because it is redundant with `pages`. |
| `workspace/_meta.json` | A lightweight registry mapping `doc_id` to `{ type, doc_name, doc_description, doc_description_embedding, path, page_count }`. |

Heavy fields (`structure`, `pages`) are dropped from memory after saving and
lazy-loaded on demand via the internal `_ensure_doc_loaded` step the first time
you call `get_document_structure` or `get_page_content`.

### Methods

#### `index(file_path, mode="auto") -> doc_id`

```python
def index(self, file_path: str, mode: str = "auto") -> str
```

Index a single document and return a generated document id (a UUID string).

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `file_path` | `str` | — | Path to the document. Expanded to a canonical absolute path before use. |
| `mode` | `str` | `"auto"` | One of `"auto"`, `"pdf"`, `"md"`. In `"auto"`, the extension decides: `.pdf` is treated as PDF; `.md` / `.markdown` as Markdown. |

Behaviour:

- PDFs are parsed by `page_index` and per-page text is extracted and cached so
  later queries never need the original file.
- Markdown is parsed by `md_to_tree`; nodes are addressed by line number.
- If both `embedding_model` and a `doc_description` are available, the
  description is embedded and stored on the document.
- Raises `FileNotFoundError` if the file does not exist, and `ValueError` for an
  unsupported extension.

Returns: the `doc_id` (`str`) used by all the read methods below.

#### `get_document(doc_id) -> JSON metadata`

```python
def get_document(self, doc_id: str) -> str
```

Returns a JSON string of document metadata: `doc_id`, `doc_name`,
`doc_description`, `type`, `status`, and `page_count` (PDF) or `line_count`
(Markdown). If the id is unknown, the JSON contains an `error` field. See the
metadata schema in [`./data-formats.md`](./data-formats.md).

#### `get_document_structure(doc_id) -> JSON tree`

```python
def get_document_structure(self, doc_id: str) -> str
```

Returns a JSON string of the hierarchical tree with all `text` fields removed,
so it is compact enough to hand to an LLM for reasoning over structure. Each
node retains its `title`, `node_id`, page range (`start_index` / `end_index`) or
`line_num`, `summary`, and child `nodes`. See the node schema in
[`./data-formats.md`](./data-formats.md).

#### `get_page_content(doc_id, pages) -> JSON list of {page, content}`

```python
def get_page_content(self, doc_id: str, pages: str) -> str
```

Returns a JSON string: a list of `{ "page": int, "content": str }` objects for
the requested pages. The `pages` argument uses the page-range mini-syntax
described [below](#page-range-mini-syntax). For PDFs the numbers are physical
page numbers; for Markdown they are `line_num` values. Invalid input yields a
JSON `error` field rather than raising.

---

## Retrieval tool functions (`pageindex/retrieve.py`)

These are the stateless functions underneath the client, defined in
[`../pageindex/retrieve.py`](../pageindex/retrieve.py) and exported from the
package. They operate on a plain `documents` dict (mapping `doc_id` to a
document record) rather than holding their own state, which makes them
convenient as agent tool definitions. Each returns a **JSON string**.

```python
from pageindex import get_document, get_document_structure, get_page_content
```

| Function | Signature | Returns |
| --- | --- | --- |
| `get_document` | `get_document(documents, doc_id)` | JSON metadata (`doc_id`, `doc_name`, `doc_description`, `type`, `status`, `page_count`/`line_count`). |
| `get_document_structure` | `get_document_structure(documents, doc_id)` | JSON tree with `text` fields stripped. |
| `get_page_content` | `get_page_content(documents, doc_id, pages)` | JSON list of `{page, content}`. |

If `doc_id` is not present in `documents`, each function returns a JSON object
with an `error` field instead of throwing.

### Page-range mini-syntax

The `pages` argument is a string parsed into a sorted, de-duplicated set of
integers:

| Form | Example | Meaning |
| --- | --- | --- |
| single | `"12"` | Page 12. |
| range | `"5-7"` | Pages 5, 6, 7 (inclusive). A range whose start exceeds its end is rejected. |
| list | `"3,8"` | Pages 3 and 8. |
| combined | `"3-5,8"` | Pages 3, 4, 5, and 8. |

For **PDF** documents these are physical page numbers (1-indexed). For
**Markdown** documents these are `line_num` values, and the helper returns the
text of nodes whose header line falls within the requested span. A malformed
string produces a JSON `error` describing the expected format.

---

## Indexing entry points

Two functions do the actual parsing. Both are exported from the `pageindex`
package via [`../pageindex/__init__.py`](../pageindex/__init__.py).

```python
from pageindex import page_index   # PDF
from pageindex import md_to_tree   # Markdown
```

### `page_index(...)` — PDFs

`page_index` (in [`../pageindex/page_index.py`](../pageindex/page_index.py)) is
the adaptive, multi-strategy PDF engine: it detects a table of contents, picks a
parsing strategy, verifies section placement (`verify_toc`), repairs misplaced
sections, recursively subdivides oversized nodes, and enriches the tree with
node ids, summaries, and an optional document description.

Key options (these mirror the keys in
[`./configuration.md`](./configuration.md)):

| Option | Type | Mirrors config key | Meaning |
| --- | --- | --- | --- |
| `toc_check_page_num` | `int` | `toc_check_page_num` | Leading pages scanned for a table of contents. |
| `max_page_num_each_node` | `int` | `max_page_num_each_node` | Page threshold above which a node is recursively subdivided. |
| `max_token_num_each_node` | `int` | `max_token_num_each_node` | Token threshold for recursive subdivision. |
| `if_add_node_id` | `str` (`"yes"`/`"no"`) | `if_add_node_id` | Add a zero-padded `node_id` to each node. |
| `if_add_node_summary` | `str` | `if_add_node_summary` | Generate a per-node summary. |
| `if_add_doc_description` | `str` | `if_add_doc_description` | Generate a whole-document description. |
| `if_add_node_text` | `str` | `if_add_node_text` | Include raw node `text` in the output (verbose). |
| `model` | `str` | `model` | LLM used for indexing and enrichment. |

### `md_to_tree(...)` — Markdown

`md_to_tree` (in
[`../pageindex/page_index_md.py`](../pageindex/page_index_md.py)) is an async
coroutine. It extracts headers (`#`..`######`, skipping code fences), captures
the text spans between headers, optionally thins small nodes, builds the tree,
and generates summaries. Nodes are addressed by `line_num`.

Key options:

| Option | Type | Meaning |
| --- | --- | --- |
| `md_path` | `str` | Path to the Markdown file. |
| `if_thinning` | `bool` | Merge nodes smaller than the token threshold. |
| `min_token_threshold` | `int` | Token threshold used when thinning. |
| `if_add_node_summary` | `str` | Generate per-node summaries. |
| `summary_token_threshold` | `int` | Token threshold for generating summaries. |
| `if_add_doc_description` | `str` | Generate a whole-document description. |
| `if_add_node_text` | `str` | Include raw node `text` in the output. |
| `if_add_node_id` | `str` | Add a zero-padded `node_id` to each node. |
| `model` | `str` | LLM used for summaries/description. |

Because it is a coroutine, call it with `asyncio.run(md_to_tree(...))` (or
`await` it inside an event loop).

---

## `run_pageindex.py` CLI

[`../run_pageindex.py`](../run_pageindex.py) is a standalone tool that indexes
**one** file and dumps its tree to `results/<name>_structure.json`. Exactly one
of `--pdf_path` or `--md_path` must be supplied. Flags left unset fall back to
the defaults in [`config.yaml`](../pageindex/config.yaml).

### Flags

| Flag | Type | Applies to | Meaning |
| --- | --- | --- | --- |
| `--pdf_path` | `str` | PDF | Path to the PDF file. |
| `--md_path` | `str` | Markdown | Path to the Markdown file. |
| `--model` | `str` | both | Model to use (overrides `config.yaml`). |
| `--toc-check-pages` | `int` | PDF | Number of leading pages scanned for a table of contents. |
| `--max-pages-per-node` | `int` | PDF | Page threshold above which a node is subdivided. |
| `--max-tokens-per-node` | `int` | PDF | Token threshold for node subdivision. |
| `--if-add-node-id` | `str` | both | Add a zero-padded `node_id` to each node (`yes`/`no`). |
| `--if-add-node-summary` | `str` | both | Generate per-node summaries (`yes`/`no`). |
| `--if-add-doc-description` | `str` | both | Generate a whole-document description (`yes`/`no`). |
| `--if-add-node-text` | `str` | both | Include raw node `text` in the output (`yes`/`no`). |
| `--if-thinning` | `str` | Markdown | Apply tree thinning (`yes`/`no`; default `no`). |
| `--thinning-threshold` | `int` | Markdown | Minimum token threshold for thinning (default `5000`). |
| `--summary-token-threshold` | `int` | Markdown | Token threshold for generating summaries (default `200`). |

### Examples

Index a PDF with the default settings:

```bash
python run_pageindex.py --pdf_path documents/kubernetes_interview.pdf
```

Index a Markdown file with thinning enabled and a custom model:

```bash
python run_pageindex.py \
  --md_path notes/handbook.md \
  --model gpt-4o-2024-11-20 \
  --if-thinning yes \
  --thinning-threshold 4000 \
  --if-add-doc-description yes
```

### Output

The resulting tree is written to:

```text
results/<name>_structure.json
```

where `<name>` is the input file's base name (without extension). The
`results/` directory is created if it does not exist.

---

## Programmatic example

A minimal end-to-end script: construct a client, index a file, fetch the tree,
then fetch the text of a couple of pages. Replace the illustrative values
(`<your-api-key>`, model names, file path, page range) with your own.

```python
import json
from pageindex import PageIndexClient

# Illustrative values — substitute your own.
client = PageIndexClient(
    api_key="<your-api-key>",
    model="gpt-4o-2024-11-20",
    embedding_model="sentence-transformers/all-MiniLM-L6-v2",
    workspace="./workspace",
)

# 1. Index a document (auto-detects PDF vs Markdown by extension).
doc_id = client.index("documents/kubernetes_interview.pdf", mode="auto")

# 2. Read back metadata.
meta = json.loads(client.get_document(doc_id))
print(meta["doc_name"], "-", meta.get("page_count"), "pages")

# 3. Fetch the hierarchical tree (text fields stripped) to reason over.
structure = json.loads(client.get_document_structure(doc_id))

# 4. Fetch the text of specific pages using the page-range mini-syntax.
pages = json.loads(client.get_page_content(doc_id, "3-5,8"))
for entry in pages:
    print(f"--- page {entry['page']} ---")
    print(entry["content"][:200])
```

For the precise shape of each returned object — metadata, tree node, and
`{page, content}` records — see [`./data-formats.md`](./data-formats.md).

---

## See also

- [`./configuration.md`](./configuration.md) — every config key and how
  overrides are merged.
- [`./data-formats.md`](./data-formats.md) — exact JSON schemas for metadata,
  trees, and page content.
- [`./getting-started.md`](./getting-started.md) — install, set keys, and run
  your first index and query.
- [`./README.md`](./README.md) — the documentation index.
