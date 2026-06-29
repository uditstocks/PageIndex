# Evaluation, Limitations & Roadmap 🧭

This page is written for a critical reader assessing how mature VectorlessRAG is. Honesty is the point: we tell you exactly what has been measured, what has not, and where the approach is and is not the right tool.

## Evaluation approach (read this first)

VectorlessRAG does **not** currently ship a quantitative benchmark. There is:

- **no** recall@k or any retrieval-ranking metric,
- **no** faithfulness / answer-quality score,
- **no** measured latency or cost numbers.

What exists today is two honest, narrower things:

1. **A qualitative comparison** against classic vector-database RAG, framed conceptually (see the table below). It argues about *mechanism* and *design trade-offs*, not measured outcomes.
2. **An internal self-signal** produced during indexing by `verify_toc`. This checks how well the generated hierarchical tree matches the actual document. It is an *indexing-quality gate*, not an answer-quality metric.

If you are evaluating VectorlessRAG for production, treat it as an architecturally motivated system with a clear retrieval philosophy and a built-in indexing self-check — not as a system with published benchmark wins. Any numeric value you see in the repo (for example an `accuracy` of `1.0` in a log file) is a per-document indexing self-check on the small sample PDFs, **not** a benchmark result. See [Internal quality signal](#internal-quality-signal) below for exactly what that number means.

## Qualitative comparison with vector RAG

The comparison below contrasts the *design* of vectorless retrieval (reasoning over structure) with the *design* of nearest-neighbour vector RAG. These are conceptual distinctions, not measured results.

| Dimension | Classic vector RAG | VectorlessRAG |
| --- | --- | --- |
| Retrieval unit | Fixed-size chunk | Logical section / page range (or line range for Markdown) |
| Mechanism | Nearest-neighbour over embeddings | LLM reasoning over the document's hierarchical tree |
| Context integrity | Fragmented across arbitrary chunk boundaries | Preserved — whole sections and contiguous page ranges |
| Explainability | Low — similarity scores, opaque ranking | High — the Navigator returns an explicit `reasoning` string for the pages it chose |
| Infrastructure | Vector database required | None required for in-document retrieval |
| Citations | Chunk-level, approximate | Page-level, precise (each span tagged `[Source: <doc_name>, Page N]`) |
| Human oversight | Rare | Built-in human-in-the-loop at each of the four stages |

The "no vector database" claim is scoped to **in-document retrieval**. The Librarian stage still uses a coarse *local* embedding pre-filter (sentence-transformers `all-MiniLM-L6-v2`, 384-dim) over each document's single description embedding to shortlist candidate documents — but that is document-granularity only, never chunk-level, and it runs locally with no external vector store. The retrieval *into* a document — choosing which pages to read — is pure LLM reasoning over the tree. See [`./methodology.md`](./methodology.md) for the full four-stage pipeline.

## Internal quality signal

During indexing, `verify_toc` (in [`../pageindex/page_index.py`](../pageindex/page_index.py)) samples sections from the generated tree and checks, via `check_title_appearance`, whether each section's title genuinely appears on the page the tree claims it starts on. The fraction that pass is recorded as an **accuracy** value for that document.

This value does two jobs inside the engine:

- **It gates strategy selection.** The engine's adaptive chain (`with_page_numbers` -> `no_page_numbers` -> `no_toc` -> best-effort structure) relies on knowing whether a parsed table of contents actually lines up with the physical pages. A low score is the engine's evidence that a strategy mismatched the document.
- **It triggers self-repair.** Sections that fail the check are handed to `fix_incorrect_toc_with_retries`, which relocates a failed section between its correct neighbours within a bounded number of retries.

What it is **not**:

- It is **not** an answer-quality metric. It says nothing about whether the Generator's final answer is correct, complete, or well-cited.
- It is **not** a benchmark. The per-document values you will find in `logs/<file>_<timestamp>.json` (for example `1.0` on the small sample PDFs in `documents/`) are self-checks computed on those specific files at index time. They do not generalize and they are not measured against any external dataset or ground truth.

In short: `verify_toc` tells you the *index* faithfully reflects the *document*. It is a useful, automatic gate on indexing quality — and deliberately scoped to that.

## Discussion

**Where vectorless retrieval wins.** The approach is strongest on well-structured, long-form documents — manuals, textbooks, technical reports, standards, and similar material with a meaningful table of contents and clear section hierarchy. When a document has real structure, reasoning over that structure lets the system jump to the right pages and keep whole sections intact, which preserves context and yields precise, page-level citations.

**The latency/cost shift.** Vectorless retrieval moves work from infrastructure to inference. Classic vector RAG answers a query with a fast vector lookup; VectorlessRAG answers it with LLM calls — the Navigator reasons over the tree to choose pages, and the Generator reads only those pages. This is a deliberate trade: you pay in LLM calls at query time instead of maintaining and querying a vector index. The pipeline is cost-aware where it can be — for example, if nothing is retrieved, the Generator's answer call is skipped entirely.

**When classic vector RAG remains competitive.** For ultra-high-QPS retrieval over large bodies of unstructured text — where documents lack meaningful structure and per-query LLM reasoning is too expensive at scale — nearest-neighbour vector search is still the pragmatic choice. Vectorless retrieval is not trying to replace that regime.

## Limitations

- **Indexing is LLM-intensive.** Building the tree calls the LLM repeatedly (TOC transformation, per-node summaries, document description, structure generation when no TOC exists). This is slower and costlier than embedding a document once. It is a one-time, up-front cost per document, but it is real.
- **Quality depends on the document having structure.** The core premise is reasoning over a contents-page-like tree. A document with no meaningful headings, sections, or table of contents gives the engine little to reason about. The graceful-degradation chain still produces a best-effort structure rather than discarding the document, but a flat or chaotic source limits how well navigation can work.
- **Navigation quality scales with model reasoning.** The Navigator's page choices are only as good as the model doing the reasoning. A weaker model will make weaker page selections, and there is no embedding-similarity safety net inside a document to fall back on.

## Roadmap / future work

The following are planned directions, not shipped features:

- **Caching navigation decisions** — reuse the Navigator's page selections for repeated or similar queries to cut redundant LLM calls.
- **Multi-hop tree descent** — for very large documents, descend the hierarchical tree in stages (coarse section, then finer subsection) rather than reasoning over the whole tree at once.
- **Hybrid retrieval** — combine the tree with an optional chunk-embedding fallback so unstructured documents (where the tree is weak) still have a retrieval path.
- **Quantitative benchmarking** — add recall@k and faithfulness measurements so the qualitative story can be backed by numbers.
- **Confidence-aware HITL** — prompt the user only when the Navigator's confidence is low, keeping human oversight where it adds value while letting high-confidence queries flow through automatically.

---

## See also

- [`./research-background.md`](./research-background.md) — the motivation and prior art behind vectorless retrieval.
- [`./methodology.md`](./methodology.md) — the four-stage query pipeline (Librarian, Navigator, Reader, Generator) in detail.
- [`./README.md`](./README.md) — the documentation index.
