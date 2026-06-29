# References & Further Reading

This page lists the published research and the infrastructure projects that
**VectorlessRAG** builds on. The references below inform the system's core
design choice — **vectorless retrieval** by **reasoning over structure** — and
the components that surround it (the local document pre-filter, the LLM provider
layer, and hosted inference).

For how these ideas are woven into the design, see
[./research-background.md](./research-background.md). For implementation
specifics, see the "Further reading" note at the end of this page.

## Academic references

1. **Lewis, P., Perez, E., Piktus, A., et al. (2020).**
   *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks.*
   Advances in Neural Information Processing Systems (NeurIPS) 33.
   The paper that introduced the Retrieval-Augmented Generation (RAG) framing:
   condition a generative model on retrieved evidence so answers are grounded in
   an external corpus rather than parametric memory. VectorlessRAG keeps this
   retrieve-then-generate contract but replaces nearest-neighbour retrieval with
   LLM reasoning over a hierarchical tree.

2. **Karpukhin, V., Oğuz, B., Min, S., et al. (2020).**
   *Dense Passage Retrieval for Open-Domain Question Answering.*
   Proceedings of the 2020 Conference on Empirical Methods in Natural Language
   Processing (EMNLP).
   Established dense, embedding-based passage retrieval — encode chunks and
   queries into vectors and rank by similarity — as a strong baseline. This is
   the dominant approach that VectorlessRAG is positioned against: the
   `Navigator` stage selects pages by reasoning over a document tree instead of
   ranking embedded chunks.

3. **Reimers, N., & Gurevych, I. (2019).**
   *Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks.*
   Proceedings of the 2019 Conference on Empirical Methods in Natural Language
   Processing (EMNLP).
   Introduced the sentence-embedding architecture behind models like
   `all-MiniLM-L6-v2` (384-dimensional). VectorlessRAG uses such an embedding
   **only** for the coarse, document-level pre-filter in the `Librarian` stage —
   embedding each document's description, never individual chunks — and runs it
   locally via `sentence-transformers`.

## Engine and infrastructure

4. **PageIndex — reasoning-based, vectorless document indexing.**
   The indexing engine VectorlessRAG builds upon. PageIndex parses a document
   into a hierarchical tree that mirrors its table of contents (sections,
   subsections, page ranges or line numbers, and LLM-written summaries), so an
   LLM can reason over structure at query time rather than search a vector
   index. In this repository the engine lives under `pageindex/` (see
   [../pageindex/page_index.py](../pageindex/page_index.py) and
   [../pageindex/page_index_md.py](../pageindex/page_index_md.py)).

5. **LiteLLM — unified LLM provider interface.**
   <https://github.com/BerriAI/litellm>
   A single, OpenAI-compatible API across many model providers. VectorlessRAG
   routes all indexing, retrieval, and generation calls through LiteLLM wrappers
   in [../pageindex/utils.py](../pageindex/utils.py) (`llm_completion`,
   `llm_acompletion`), which keeps the model layer swappable.

6. **NVIDIA NIM — hosted LLM inference.**
   The hosted inference endpoint used by default in
   [../RAGG.py](../RAGG.py), reached through LiteLLM's OpenAI-compatible client
   against `https://integrate.api.nvidia.com/v1`. Authentication uses the
   `NVIDIA_API_KEY` environment variable.

## Further reading

For implementation specifics within this project, see the sibling documentation:

- [./architecture.md](./architecture.md) — the four query stages
  (`Librarian`, `Navigator`, `Reader`, `Generator`) and how they fit together.
- [./methodology.md](./methodology.md) — how the document tree is built and how
  reasoning over structure replaces nearest-neighbour search.
- [./api-reference.md](./api-reference.md) — `PageIndexClient` and the
  indexing/retrieval entry points.
- [./evaluation.md](./evaluation.md) — the qualitative comparison versus vector
  RAG and the internal `verify_toc` self-signal.

## See also

- [./research-background.md](./research-background.md) — the problem framing and
  how these references shape the design.
- [./README.md](./README.md) — the documentation index.
