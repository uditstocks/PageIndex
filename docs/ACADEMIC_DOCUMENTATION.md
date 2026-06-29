# Reasoning-Based Document Retrieval without Vector Databases

### An Academic Study of a Tree-Structured, Vectorless Retrieval-Augmented Generation System

---

| | |
|---|---|
| **Project Title** | VectorlessRAG — Reasoning-Based Retrieval over Hierarchical Document Trees |
| **Domain** | Natural Language Processing · Information Retrieval · Large Language Models |
| **Core Technique** | Tree-structured indexing + LLM tree-navigation (PageIndex) |
| **Implementation** | Python 3.10+, LiteLLM, NVIDIA NIM, Sentence-Transformers |
| **Document Type** | Academic / Educational Project Report |

---

## Abstract

Conventional Retrieval-Augmented Generation (RAG) systems depend on a **vector
database**: documents are split into fixed-size chunks, each chunk is converted
into a dense embedding vector, and retrieval is performed by approximate
nearest-neighbour search over those vectors. While effective, this paradigm
introduces well-known weaknesses — loss of document structure, arbitrary chunk
boundaries that fragment ideas, an additional piece of database infrastructure
to operate, and a retrieval step that is *similarity-based* rather than
*reasoning-based*.

This project implements and studies an **alternative retrieval paradigm** that
removes the vector database from the critical retrieval path. Instead of
embedding chunks, the system parses each document into a **hierarchical tree**
that mirrors the document's own table of contents (sections, subsections, and
their page ranges). At query time, a Large Language Model (LLM) **reasons over
this tree structure** — exactly as a human reader would scan a contents page —
to decide *which* sections and pages are most relevant, then reads only those
pages to produce a grounded answer.

The system is built on top of the open-source **PageIndex** indexing engine and
is wrapped by an interactive, multi-document application (`RAGG.py`) that adds a
two-stage retrieval pipeline ("Librarian" → "Navigator"), a human-in-the-loop
verification layer, and a hallucination-resistant answer generator. This
document presents the motivation, theory, architecture, implementation, and
evaluation of the system as a self-contained academic learning resource.

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Background and Motivation](#2-background-and-motivation)
3. [System Overview](#3-system-overview)
4. [Theoretical Foundations](#4-theoretical-foundations)
5. [System Architecture](#5-system-architecture)
6. [Detailed Methodology](#6-detailed-methodology)
7. [Implementation Details](#7-implementation-details)
8. [Engineering Robustness and Design Decisions](#8-engineering-robustness-and-design-decisions)
9. [Evaluation and Discussion](#9-evaluation-and-discussion)
10. [Limitations and Future Work](#10-limitations-and-future-work)
11. [How to Reproduce](#11-how-to-reproduce)
12. [Glossary](#12-glossary)
13. [References](#13-references)

---

## 1. Introduction

### 1.1 Problem Statement

Given a library of documents (PDFs, Markdown files) and a natural-language
question, the goal is to return an answer that is **accurate**, **grounded in
the source material**, and **traceable** to specific pages. The central research
question explored here is:

> *Can we achieve high-quality, citation-grounded document question answering
> **without** building and maintaining a vector database — by instead letting an
> LLM reason over a document's own structural hierarchy?*

### 1.2 Contributions

This project makes the following contributions as an educational artifact:

1. **A working vectorless RAG pipeline** that retrieves by *tree navigation*
   rather than *vector similarity*.
2. **A two-stage retrieval design** that separates *document selection* (the
   "Librarian") from *in-document page selection* (the "Navigator"), mirroring
   how a human uses a library.
3. **A human-in-the-loop control layer** that surfaces the model's reasoning and
   lets a user approve, override, or skip each retrieval decision.
4. **A hardened, multi-strategy indexing engine** capable of degrading
   gracefully when documents have no table of contents or when smaller models
   produce imperfect structure.

### 1.3 Intended Audience

This document is written for students and practitioners learning about modern
information retrieval and LLM application design. It assumes basic familiarity
with Python and a conceptual understanding of what an LLM is, but explains all
RAG-specific and retrieval-specific concepts from first principles.

---

## 2. Background and Motivation

### 2.1 Retrieval-Augmented Generation (RAG)

An LLM has a fixed *context window* and a *knowledge cutoff*: it cannot read an
entire 500-page manual at once, and it has no knowledge of a user's private
documents. **RAG** addresses both problems by *retrieving* a small,
question-relevant slice of an external corpus and placing it into the prompt, so
the model answers from supplied evidence rather than from memory.

A RAG system therefore has two halves:

- **Retriever** — selects the relevant text given a query.
- **Generator** — the LLM that produces an answer from the retrieved text.

The quality of the entire system is bounded by the retriever: *if the right
passage is never retrieved, no amount of generation skill can recover the
correct answer.*

### 2.2 The Conventional Approach: Vector RAG

The dominant retrieval method works as follows:

```
Document → Chunk (≈500 tokens) → Embed each chunk → Store vectors in a DB
Query    → Embed query        → Nearest-neighbour search → Top-k chunks
```

This is powerful but carries structural costs:

| Issue | Explanation |
|---|---|
| **Chunk fragmentation** | Fixed-size windows cut across paragraphs, tables, and sections, severing context that belongs together. |
| **Lost hierarchy** | A flat list of vectors discards the document's logical structure (chapter → section → subsection). |
| **Similarity ≠ relevance** | Nearest-neighbour search retrieves text that is *lexically/semantically similar*, which is not always what is needed to *reason* about a question. |
| **Infrastructure burden** | A vector database is an additional stateful service to deploy, scale, tune, and keep in sync. |
| **Opaque retrieval** | It is difficult to explain *why* a particular chunk was retrieved. |

### 2.3 The Alternative: Reasoning-Based, Vectorless Retrieval

A human researcher does **not** mentally compute cosine similarity over every
paragraph. They open the **table of contents**, reason ("the answer to a
question about *deployment* is probably in Chapter 7"), turn to those pages, and
read. This project operationalises exactly that behaviour:

```
Document → Parse into a hierarchical TREE (sections + page ranges + summaries)
Query    → LLM reads the tree → reasons which sections/pages are relevant
         → read only those pages → answer
```

Retrieval becomes an act of **reasoning over structure** rather than
**similarity search over vectors**. The vector database is eliminated from the
in-document retrieval path. (As discussed in §5, lightweight embeddings are
retained only for a coarse *document-level* pre-filter when many documents are
present — never for chunk-level retrieval.)

---

## 3. System Overview

The system has two distinct phases: an **offline indexing phase** and an
**online query phase**.

```
┌──────────────────────────── INDEXING (offline, once per document) ───────────────────────────┐
│                                                                                               │
│   PDF / Markdown ──► PageIndex Engine ──► Hierarchical Tree (JSON)                             │
│                       • detect table of contents                                              │
│                       • extract section hierarchy                                             │
│                       • map sections → physical page ranges                                   │
│                       • generate per-node summaries                                           │
│                       • generate a whole-document description                                 │
│                                                                                               │
│                       Persisted to: workspace/<doc_id>.json  +  _meta.json                    │
└───────────────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────── QUERY (online, per question) ────────────────────────────────┐
│                                                                                               │
│   User question                                                                               │
│        │                                                                                       │
│        ▼                                                                                       │
│   [1] LIBRARIAN  ── embedding pre-filter (top-N docs) ─► LLM refinement ─► relevant doc(s)     │
│        │                                          (human confirms / overrides)                 │
│        ▼                                                                                       │
│   [2] NAVIGATOR ── LLM reads each doc's TREE ─► chooses page/line range                        │
│        │                                          (human confirms / overrides / skips)         │
│        ▼                                                                                       │
│   [3] READER    ── extract text from chosen pages ─► assemble grounded context                 │
│        │                                                                                       │
│        ▼                                                                                       │
│   [4] GENERATOR ── LLM answers ONLY from retrieved context, with page citations                │
│                                                                                               │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Theoretical Foundations

### 4.1 The Document as a Tree

Most non-trivial documents are *intrinsically hierarchical*. A book is a list of
chapters; a chapter is a list of sections; a section is a list of paragraphs.
This project represents that hierarchy explicitly as a **tree** $T$ of nodes,
where each node $n$ carries:

$$
n = \langle \text{title},\ \text{node\_id},\ \text{start\_index},\ \text{end\_index},\ \text{summary},\ \text{children} \rangle
$$

- `title` — the section heading.
- `start_index` / `end_index` — the *physical page range* (PDF) or *line range*
  (Markdown) the section spans.
- `summary` — an LLM-generated abstract of the section's content.
- `children` — nested sub-sections (the recursive structure).

This tree is a **navigable index**: it is small enough to fit entirely inside an
LLM's context window even for large documents, because it contains only titles,
page ranges, and short summaries — not the full text.

### 4.2 Retrieval as Tree Search

Given a query $q$ and tree $T$, retrieval is the problem of selecting a node set
$N^* \subseteq T$ whose underlying pages most likely contain the answer:

$$
N^* = \operatorname*{arg\,max}_{N \subseteq T} \ P(\text{answer}(q) \in \text{pages}(N) \mid T, q)
$$

Crucially, the probability $P(\cdot)$ is **estimated by an LLM reasoning over the
tree**, not by a distance metric over embeddings. The LLM reads the titles and
summaries and reasons about which branch to descend — a learned, semantic,
*explainable* form of tree search.

### 4.3 Grounding and Faithfulness

The generator operates under a strict **closed-book-on-the-context**
constraint: it must answer *only* from the retrieved pages and must explicitly
state when the answer is absent. This is the standard mechanism for suppressing
**hallucination** (confident but unsupported output). Each retrieved span is
tagged with its source (`[Source: <doc>, Page <n>]`), making every answer
*traceable* back to evidence — a key requirement for trustworthy, academically
defensible question answering.

---

## 5. System Architecture

### 5.1 Component Map

| Layer | File | Responsibility |
|---|---|---|
| **Application / Orchestration** | `RAGG.py` | Interactive CLI; Librarian → Navigator → Reader → Generator pipeline; human-in-the-loop. |
| **Client Facade** | `pageindex/client.py` | `PageIndexClient`: indexing entry point, workspace persistence, lazy loading, embedding generation. |
| **PDF Indexing Engine** | `pageindex/page_index.py` | TOC detection, structure extraction, page-offset alignment, verification, self-repair. |
| **Markdown Indexing Engine** | `pageindex/page_index_md.py` | Header-based tree construction for Markdown. |
| **Retrieval Tools** | `pageindex/retrieve.py` | `get_document_structure`, `get_page_content`, page-range parsing. |
| **Utilities** | `pageindex/utils.py` | LLM wrappers (LiteLLM), tokenisation, JSON extraction, embeddings, config loading. |
| **CLI Runner** | `run_pageindex.py` | Stand-alone command to index a single file and dump its tree to JSON. |
| **Configuration** | `pageindex/config.yaml` | Default models and indexing hyper-parameters. |

### 5.2 The Two-Stage Retrieval Insight ("Library" Metaphor)

A defining design choice is the separation of retrieval into two cognitively
distinct stages, modelled on a physical library:

1. **The Librarian (document selection).** When the corpus contains many
   documents, the system first decides *which book to open*. To keep this cheap
   and fast it uses a **coarse embedding pre-filter** — each document's
   *description* (not its content) is embedded once at index time; the query is
   embedded once at run time; cosine similarity ranks the top-N candidate
   documents. An LLM then refines this shortlist to 1–3 documents. *This is the
   only place embeddings appear, and they operate at document granularity, not
   chunk granularity.*

2. **The Navigator (page selection).** Within each chosen document, the LLM
   reads the **tree structure** and reasons about *which pages to turn to* — the
   genuinely vectorless, reasoning-based core of the system.

This separation is significant: it confines vector math to a cheap, coarse,
optional pre-filter and reserves the precise retrieval decision for LLM
reasoning over structure.

---

## 6. Detailed Methodology

### 6.1 Phase A — Indexing a Document into a Tree

The indexing engine (`page_index.py`) converts a raw document into a verified
hierarchical tree. The pipeline is *adaptive*: it chooses a strategy based on
what the document actually contains.

**Step 1 — Page tokenisation.** The PDF is parsed page-by-page; each page's text
and token count are recorded (`get_page_tokens`).

**Step 2 — Table-of-Contents (TOC) detection.** The engine scans the first pages
(`find_toc_pages`, `toc_detector_single_page`) to determine whether the document
ships with its own table of contents, and whether that TOC already lists page
numbers.

**Step 3 — Strategy selection (`meta_processor`).** Based on Step 2, one of
three strategies is invoked:

| Strategy | When used | Mechanism |
|---|---|---|
| `process_toc_with_page_numbers` | Document has a TOC *with* page numbers | Parse the TOC, then compute a **page offset** by matching a few section titles to their true physical pages, correcting the gap between printed page numbers and physical PDF page indices. |
| `process_toc_no_page_numbers` | Document has a TOC *without* page numbers | Parse the TOC structure, then locate each section's start page by scanning the body text. |
| `process_no_toc` | Document has *no* TOC at all | Generate the hierarchy directly from the body text, page group by page group, using the LLM as a structure extractor. |

**Step 4 — Verification (`verify_toc`).** A sample of generated sections is
checked: does each section title actually *appear* on the page the tree claims?
This yields an **accuracy score**.

**Step 5 — Self-repair (`fix_incorrect_toc_with_retries`).** Sections that fail
verification are re-located by searching the page range bracketed by their
nearest *correct* neighbours, and re-verified — up to a bounded number of
attempts.

**Step 6 — Recursive refinement (`process_large_node_recursively`).** Any node
spanning too many pages/tokens is recursively re-parsed into finer subsections,
producing a balanced, query-friendly tree.

**Step 7 — Enrichment.** Node IDs are assigned, per-node **summaries** are
generated, and a whole-document **description** is synthesised (used later by the
Librarian).

The result is persisted as `workspace/<doc_id>.json`, with a lightweight
`_meta.json` index holding descriptions and embeddings for fast startup.

### 6.2 Phase B — Querying the Library

**Step 1 — Librarian (`select_relevant_documents`).**
- Embed the query locally with `all-MiniLM-L6-v2`.
- Compute cosine similarity against each stored document-description embedding;
  display a transparency table of scores; keep the top-N.
- Ask the LLM to refine this to the 1–3 most relevant documents (returned as
  JSON document IDs).
- A **fallback** (`_llm_select_documents_fallback`) handles the case where
  embeddings are unavailable (e.g. legacy indices), selecting purely by LLM.

**Step 2 — Human verification.** The selected documents are shown to the user,
who may accept, re-query, or narrow the selection by name.

**Step 3 — Navigator (`retrieve_pages`).**
- Load the chosen document's tree.
- Build a *type-aware* prompt: PDFs are addressed by **page numbers**, Markdown
  by **line numbers** (`line_num`) — a subtle but essential distinction, since
  asking a Markdown tree for "pages" returns numbers that match no node.
- The LLM returns a JSON object: `{"pages": "3-5, 8", "reasoning": "..."}`.
- The *reasoning* is shown to the user (explainability), who may accept,
  manually override the range, or skip the document entirely.

**Step 4 — Reader (`get_page_content`).** The requested page/line range is
parsed (`_parse_pages`) and the corresponding text is extracted — from cached
per-page text for PDFs, or from the matching tree nodes for Markdown. Each span
is labelled with its source for citation.

**Step 5 — Generator (`generate_answer`).** The assembled context is given to
the LLM under a strict system prompt: *answer only from the provided context;
if the answer is not present, say so; cite document name and page numbers.*
Notably, prior conversation turns are **intentionally not** replayed into this
call — this preserves the "answer only from retrieved context" guarantee and
prevents the model from leaking facts from earlier, now-irrelevant documents.

---

## 7. Implementation Details

### 7.1 Technology Stack

| Concern | Choice | Rationale |
|---|---|---|
| LLM gateway | **LiteLLM** | A single, provider-agnostic API; lets the same code target NVIDIA NIM, OpenAI, Anthropic, OpenRouter, etc. |
| LLM provider | **NVIDIA NIM** (`llama-3.3-nemotron-super-49b`) | Used for indexing, navigation, and answering. |
| Local embeddings | **Sentence-Transformers `all-MiniLM-L6-v2`** | Runs locally; no external embedding API; powers the document pre-filter only. |
| PDF parsing | **PyMuPDF** + **PyPDF2** | Page tokenisation and text extraction. |
| Terminal UI | **Rich** | Tables, panels, progress bars, Markdown rendering for an instructive, transparent CLI. |
| Config | **PyYAML** + `ConfigLoader` | Centralised, override-friendly hyper-parameters. |

### 7.2 Configurable Indexing Hyper-parameters (`config.yaml`)

| Parameter | Meaning |
|---|---|
| `toc_check_page_num` | How many leading pages to scan for a table of contents. |
| `max_page_num_each_node` | Page threshold above which a node is recursively subdivided. |
| `max_token_num_each_node` | Token threshold for recursive subdivision. |
| `max_toc_chunk_tokens` | Chunk size when transforming a long raw TOC into JSON. |
| `if_add_node_summary` / `if_add_doc_description` / `if_add_node_id` / `if_add_node_text` | Toggles for tree enrichment. |

### 7.3 Persistence Model

- Each document is stored as `workspace/<doc_id>.json` (full tree + cached
  pages).
- `_meta.json` is a compact index of every document's name, description, type,
  and description-embedding — read at startup so the app is responsive without
  loading every full tree.
- Heavy fields (`structure`, `pages`) are **lazy-loaded on demand**
  (`_ensure_doc_loaded`) and dropped from memory after saving, bounding memory
  use as the library grows.

---

## 8. Engineering Robustness and Design Decisions

A substantial part of this project's educational value lies in its *defensive
engineering* — the handling of the many ways real documents and real LLM outputs
misbehave. Selected examples:

1. **Graceful degradation of indexing.** If high-accuracy strategies fail, the
   engine falls back `with_page_numbers → no_page_numbers → no_toc`, and as a
   *last resort* returns a best-effort structure rather than discarding the
   document — so a hard-to-parse file is still queryable.

2. **Page-offset correction.** Printed page numbers rarely equal physical PDF
   page indices (front matter, cover pages). The engine statistically infers the
   offset by matching titles to their true pages (`calculate_page_offset`).

3. **Robust LLM-output parsing.** Model responses are defended with layered
   JSON extraction, fenced-code stripping, and regex fallbacks (e.g. UUID
   extraction when a JSON document-selection reply is malformed).

4. **Type-aware addressing.** PDFs use page numbers; Markdown uses line numbers.
   The Navigator prompt and the Reader both branch on document type to avoid the
   silent "empty retrieval" failure mode.

5. **Out-of-bounds and null safety.** TOC indices beyond document length are
   truncated (`validate_and_truncate_physical_indices`); non-numeric LLM page
   values are coerced and skipped; missing embeddings degrade to neutral scores
   rather than vanishing from retrieval.

6. **Cost-aware control flow.** If nothing is retrieved (all docs skipped or
   empty), the system *skips the answer LLM call entirely* rather than paying for
   a round-trip that could only produce an ungrounded response.

7. **Explicit provider-key handling.** Environment variables for the LLM
   provider are set defensively so authentication does not silently rely on an
   undocumented fallback that could break on a library upgrade.

These decisions illustrate a central lesson of applied LLM engineering: *the
model is one component in a system that must remain correct even when the model,
the data, or the infrastructure does not cooperate.*

---

## 9. Evaluation and Discussion

### 9.1 Qualitative Comparison with Vector RAG

| Dimension | Vector RAG | This System (Vectorless / Tree-based) |
|---|---|---|
| Retrieval unit | Fixed-size chunk | Logical section / page range |
| Retrieval mechanism | Nearest-neighbour over embeddings | LLM reasoning over structure |
| Context integrity | Often fragmented | Preserved (whole sections) |
| Explainability | Low (distance scores) | High (model states its reasoning) |
| Infrastructure | Vector DB required | None for in-doc retrieval |
| Citations | Chunk-level, approximate | Page-level, precise |
| Human oversight | Rare | Built into every step |

### 9.2 Internal Quality Signal

The indexing engine carries its **own** evaluation metric: `verify_toc` measures
the fraction of generated sections whose titles genuinely appear on their
claimed pages. This *self-checking* accuracy gates strategy selection and
triggers self-repair, giving the system a built-in, document-specific quality
estimate rather than relying on external benchmarks alone.

### 9.3 Discussion

The strongest results of the vectorless approach appear on **well-structured,
long-form documents** (manuals, textbooks, reports, standards) where the
hierarchy is rich and meaningful — precisely the documents where conventional
chunking does the most damage. The approach is also attractive operationally:
removing the vector database removes an entire stateful service from the
deployment.

The trade-off is a **latency/cost shift**: retrieval now costs LLM calls
(navigation) rather than a vector lookup. For interactive, accuracy-sensitive,
citation-required use cases — the target of this project — that trade is
favourable; for ultra-high-QPS retrieval over unstructured text, classic vector
RAG remains competitive.

---

## 10. Limitations and Future Work

**Limitations.**
- Indexing is LLM-intensive and therefore slower and costlier than embedding a
  document once.
- Quality depends on the document *having* a meaningful structure; flat,
  structureless text gains less from tree navigation.
- Navigation quality scales with the reasoning ability of the underlying model.

**Future directions.**
1. **Caching navigation decisions** for repeated or similar queries.
2. **Multi-hop tree navigation** — descending iteratively (chapter → section →
   subsection) for very large documents instead of a single-shot page choice.
3. **Hybrid retrieval** — combining tree navigation with optional chunk-level
   embedding fallback for unstructured documents.
4. **Quantitative benchmarking** against standard RAG datasets (e.g. retrieval
   recall@k, answer faithfulness scores).
5. **Confidence-aware human-in-the-loop** — only prompting the user when the
   model's navigation confidence is low.

---

## 11. How to Reproduce

> The following is a high-level reproduction guide. Adapt model names and keys to
> your own provider.

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure API keys** — copy `.env.example` to `.env` and set:
   ```
   NVIDIA_API_KEY=...
   OPENROUTER_API_KEY=...
   ```

3. **Add documents** — drop `.pdf` / `.md` files into the `documents/` folder.

4. **Run the interactive application**
   ```bash
   python RAGG.py
   ```
   On first run each document is indexed into a tree (stored under
   `workspace/`); subsequent runs reuse the saved indices.

5. **Ask questions** — the system will show the Librarian's document choice and
   the Navigator's page plan, ask you to confirm, and then produce a grounded,
   cited answer. Type `info` to list indexed documents, `exit` to quit.

6. **(Optional) Index a single file to inspect its tree**
   ```bash
   python run_pageindex.py --pdf_path documents/your_file.pdf
   # → writes results/your_file_structure.json
   ```

---

## 12. Glossary

| Term | Definition |
|---|---|
| **RAG** | Retrieval-Augmented Generation: retrieving relevant text and supplying it to an LLM to ground its answer. |
| **Vector database** | A store of embedding vectors supporting nearest-neighbour search; the component this project removes from the retrieval path. |
| **Embedding** | A dense numeric vector representing the meaning of a piece of text. |
| **Chunk** | A fixed-size text fragment used as the retrieval unit in classic RAG. |
| **Tree / hierarchical index** | A nested representation of a document's sections, used here as the navigable index. |
| **Node** | One element of the tree: a section with a title, page range, summary, and children. |
| **Physical index / page** | The actual page position in the file, as opposed to the printed page number. |
| **TOC** | Table of Contents. |
| **Navigator** | The stage that reasons over the tree to choose which pages to read. |
| **Librarian** | The stage that chooses which document(s) to open. |
| **Grounding** | Constraining the generator to answer only from supplied evidence. |
| **Hallucination** | Confident model output not supported by the source material. |
| **Human-in-the-loop (HITL)** | A design where a person reviews/approves automated decisions. |
| **LiteLLM** | A library providing one unified API across many LLM providers. |

---

## 13. References

1. Lewis, P. et al. (2020). *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks.* NeurIPS.
2. Karpukhin, V. et al. (2020). *Dense Passage Retrieval for Open-Domain Question Answering.* EMNLP.
3. Reimers, N. & Gurevych, I. (2019). *Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks.* EMNLP. *(basis of the `all-MiniLM-L6-v2` model used for document pre-filtering)*
4. **PageIndex** — Reasoning-based, vectorless document indexing engine (the indexing core this project builds upon).
5. **LiteLLM** — Unified interface to LLM providers. <https://github.com/BerriAI/litellm>
6. NVIDIA NIM — NVIDIA Inference Microservices for hosted LLM inference.

---

<div align="center">

*This document is an academic / educational artifact describing the design and
implementation of a reasoning-based, vectorless Retrieval-Augmented Generation
system. It is intended to help readers understand both the theory and the
practical engineering of modern document question-answering.*

</div>
