# Glossary 📖

A reference of the terms used across the VectorlessRAG documentation suite. Definitions are deliberately precise and consistent with the rest of the docs; where a term names a concrete pipeline stage, function, or file, it links back to where that concept is explained in depth.

The table below is roughly alphabetical, grouping the four query stages (Librarian, Navigator, Reader, Generator) where they read most naturally.

| Term | Definition |
| --- | --- |
| **Chunk** | A fixed-size span of text (often a paragraph or token window) that a conventional vector RAG system embeds and stores; VectorlessRAG does **not** chunk-and-embed document bodies — its only embeddings are at the whole-document granularity, used for coarse filtering. |
| **Document granularity** | The level at which VectorlessRAG produces an embedding: one vector per document (from its description), never one per chunk — so embeddings narrow *which document*, never *which passage*. |
| **Embedding** | A numeric vector representation of text; in VectorlessRAG the only embeddings are 384-dim vectors from `sentence-transformers/all-MiniLM-L6-v2`, computed locally over each document's description for the Librarian's coarse pre-filter. |
| **Generator** | The fourth query stage (`generate_answer`): answers strictly from the retrieved pages under a grounded system prompt, cites document and pages, says the information "isn't in the retrieved pages" when absent, and is skipped entirely when nothing was retrieved. |
| **Grounding** | The property of answering only from explicitly retrieved context; VectorlessRAG preserves grounding by citing page-level sources and by *not* replaying prior conversation turns into the Generator. |
| **Hallucination** | A model output that is unsupported by the source material; grounding and page-level citation are the mechanisms VectorlessRAG uses to make hallucinations visible and to constrain the Generator against them. |
| **Hierarchical tree** | See **Tree**. |
| **Human-in-the-loop (HITL)** | The interactive design of `RAGG.py` in which the user confirms, overrides, narrows, or skips at each of the four query stages before the pipeline proceeds. |
| **Librarian** | The first query stage (`select_relevant_documents`): a coarse local embedding pre-filter over document descriptions (cosine similarity, top-N) followed by LLM refinement to 1–3 documents, with a user confirmation step and an `_llm_select_documents_fallback` for indices lacking stored embeddings. |
| **Line number (Markdown addressing)** | The `line_num` on a Markdown tree node; Markdown documents are addressed by line numbers rather than physical pages, making the Navigator and Reader type-aware. |
| **LiteLLM** | The library that provides VectorlessRAG's unified LLM interface (sync `llm_completion`, async `llm_acompletion`); in `RAGG.py` it routes completions to NVIDIA NIM. |
| **Navigator** | The second query stage (`retrieve_pages`): sends a document's tree to the LLM and receives a JSON page/line selection (e.g. `{"pages":"3-5,8","reasoning":"..."}`), which the user may approve, override, or skip. |
| **Node** | A single element of the tree, carrying a `title`, zero-padded `node_id`, a page range (`start_index`/`end_index`) or `line_num`, an LLM-written `summary` (or `prefix_summary` on parents), optional `text`, and child `nodes`. |
| **NVIDIA NIM** | The inference backend VectorlessRAG calls through LiteLLM; `RAGG.py` targets `nvidia_nim/nvidia/llama-3.3-nemotron-super-49b-v1` for indexing, retrieval, and answering. |
| **PageIndex** | The indexing engine underneath VectorlessRAG that parses a document into a hierarchical tree mirroring its table of contents, with page ranges and LLM-written summaries per node. |
| **Page range** | A compact specification of pages to read, such as `"5-7"` or `"3,8"`, produced by the Navigator and parsed by the Reader. |
| **Physical index / page** | The actual page position within a PDF (`start_index`/`end_index`), which can differ from the printed page number — PageIndex computes a page **offset** to reconcile the two during indexing. |
| **RAG (Retrieval-Augmented Generation)** | A pattern that grounds an LLM's answer in retrieved source material; VectorlessRAG performs the retrieval step by reasoning over document structure rather than by nearest-neighbour search over embeddings. |
| **Reader** | The third query stage (`get_page_content`): extracts the text for the chosen pages or lines and tags each span `[Source: <doc_name>, Page N]` for citation. |
| **Reasoning over structure** | VectorlessRAG's retrieval strategy: an LLM scans a document's tree (like a person scanning a contents page) to decide which pages to read, in place of vector similarity. |
| **Self-signal** | An internal, per-document quality indicator rather than a benchmark; in VectorlessRAG the self-signal is `verify_toc`'s accuracy score during indexing — see **`verify_toc`**. |
| **TOC (table of contents)** | The contents listing PageIndex detects and parses to build the tree; documents without a usable TOC fall back through a graceful degradation chain that still produces a best-effort structure. |
| **Tree / hierarchical index** | The per-document structure that mirrors its table of contents — nested sections and subsections with page ranges (or line numbers) and summaries — over which the Navigator reasons at query time. |
| **Vector database** | A store of chunk embeddings queried by nearest-neighbour similarity; VectorlessRAG deliberately omits this component, which is why retrieval is described as **vectorless**. |
| **Vectorless retrieval** | Retrieval that uses no vector database for passage selection, relying instead on reasoning over the document tree (the document-level embedding filter is a coarse pre-step, not the retrieval mechanism). |
| **`verify_toc`** | An indexing-quality gate that samples sections and checks whether each title genuinely appears on its claimed page (via `check_title_appearance`), yielding an **accuracy** self-signal — an indexing check, not an answer-quality metric. |

## A note on terminology consistency

Two distinctions are easy to blur and worth stating plainly:

- **Embedding-as-filter vs. embedding-as-retrieval.** VectorlessRAG uses embeddings only at document granularity to *narrow the candidate set* (the Librarian). It never uses embeddings to select passages — that is the Navigator's job, and it is done by reasoning over structure. This is why the project is "vectorless" despite computing some vectors.
- **Self-signal vs. benchmark.** `verify_toc`'s accuracy score is a per-document indexing self-check. It is not a recall, faithfulness, or latency measurement, and the project ships no quantitative benchmark. See [./evaluation.md](./evaluation.md) for how evaluation is framed honestly.

## See also

- [./research-background.md](./research-background.md) the concepts and motivation behind vectorless retrieval and reasoning over structure
- [./architecture.md](./architecture.md) - how the Librarian, Navigator, Reader, and Generator fit together
- [./methodology.md](./methodology.md) - how PageIndex builds the tree and how `verify_toc` works
- [./README.md](./README.md) - the documentation index
