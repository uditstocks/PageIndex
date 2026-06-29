# Research & Background 📚

This page is the academic core of the VectorlessRAG documentation. It states the
research question, situates the project against conventional Retrieval-Augmented
Generation (RAG), and develops the theory behind *vectorless retrieval* —
treating a document as a tree and treating retrieval as reasoning over that
structure. It is written for researchers and students; the rest of the suite
covers the concrete pipeline ([methodology](./methodology.md)) and how the system
is judged ([evaluation](./evaluation.md)).

## Abstract

Conventional RAG depends on a **vector database**: documents are split into
fixed-size chunks, each chunk is embedded into a dense vector, and retrieval is
performed by approximate nearest-neighbour search over those vectors. This works,
but it carries structural costs — arbitrary chunk boundaries fragment ideas, the
document's hierarchy is flattened away, *similarity* is taken as a proxy for
*relevance*, and an extra stateful service must be operated.

VectorlessRAG studies an alternative that removes the vector database from the
in-document retrieval path. Each document is parsed by the **PageIndex** engine
into a **hierarchical tree** that mirrors its own table of contents: sections and
subsections, their page ranges (or line numbers), and LLM-written summaries. At
query time a Large Language Model **reasons over this tree** — exactly as a human
scans a contents page — to decide which pages to read, then answers *only* from
those pages with page-level citations. Retrieval becomes **reasoning over
structure** rather than nearest-neighbour search over embeddings. This project
demonstrates that citation-grounded document question answering is achievable
without a vector database: it ships a working four-stage query pipeline (the
Librarian, the Navigator, the Reader, the Generator), a human-in-the-loop control
layer, and a hardened, multi-strategy indexing engine that degrades gracefully on
documents that lack a clean structure.

## Problem statement & research question

Given a library of documents (PDFs and Markdown files) and a natural-language
question, the goal is to return an answer that is:

- **accurate** — correct with respect to the source material;
- **source-grounded** — derived only from retrieved evidence, not from the
  model's parametric memory; and
- **traceable** — attributable to specific pages so a reader can verify it.

The central research question is:

> *Can we achieve high-quality, citation-grounded document question answering
> **without** building and maintaining a vector database — by instead letting an
> LLM reason over a document's own structural hierarchy?*

VectorlessRAG is an answer-by-construction: a complete, runnable system that does
exactly this, plus an honest account of where the trade-off pays off and where it
does not (see [evaluation](./evaluation.md)).

## Contributions

The project makes four contributions as an educational and engineering artifact.

1. **A working vectorless RAG pipeline.** Retrieval is performed by *tree
   navigation* — an LLM reading a document's structure — rather than by *vector
   similarity*. The vector database is eliminated from the in-document retrieval
   path.

2. **A two-stage Librarian → Navigator retrieval design.** Document selection
   (which book to open) is separated from in-document page selection (which pages
   to turn to), mirroring how a person uses a physical library. This confines any
   vector math to a cheap, coarse, *document-level* pre-filter and reserves the
   precise retrieval decision for reasoning over structure.

3. **A human-in-the-loop (HITL) control layer.** The model's reasoning is
   surfaced at each decision point, and the user can approve, override, or skip
   it — at document selection and at page selection alike.

4. **A hardened, multi-strategy indexing engine.** The PageIndex engine adapts to
   what a document actually contains and degrades gracefully when there is no
   table of contents or when a smaller model produces imperfect structure, never
   discarding a document outright.

## Background: a RAG primer

A Large Language Model has a fixed *context window* and a *knowledge cutoff*: it
cannot read an entire long manual in one prompt, and it has no knowledge of a
user's private documents. **RAG** addresses both by *retrieving* a small,
question-relevant slice of an external corpus and placing it into the prompt, so
the model answers from supplied evidence rather than from memory.

A RAG system therefore has two halves:

- **Retriever** — selects relevant text given a query.
- **Generator** — the LLM that produces an answer from the retrieved text.

The quality of the whole system is bounded by the retriever:

> *If the right passage is never retrieved, no amount of generation skill can
> recover the correct answer.*

This bound is why VectorlessRAG invests its design effort in the retrieval
stages — the Librarian and the Navigator — and keeps the Generator deliberately
constrained.

## Conventional vector RAG and its structural costs

The dominant retrieval method works as follows:

```text
Document → Chunk (≈ fixed size) → Embed each chunk → Store vectors in a DB
Query    → Embed query          → Nearest-neighbour search → Top-k chunks
```

It is powerful, but it imposes structural costs that motivate the vectorless
alternative.

| Cost | Explanation |
|---|---|
| **Chunk fragmentation** | Fixed-size windows cut across paragraphs, tables, and sections, severing context that belongs together. |
| **Lost hierarchy** | A flat list of vectors discards the document's logical structure (chapter → section → subsection). |
| **Similarity ≠ relevance** | Nearest-neighbour search returns text that is lexically or semantically *similar*, which is not always what is needed to *reason* about a question. |
| **Infrastructure burden** | A vector database is an additional stateful service to deploy, scale, tune, and keep in sync with the source corpus. |
| **Opaque retrieval** | It is hard to explain *why* a particular chunk was returned; the evidence is a distance score, not a rationale. |

## The vectorless alternative

A human researcher does **not** mentally compute cosine similarity over every
paragraph. They open the **table of contents**, reason ("a question about
*deployment* is probably in Chapter 7"), turn to those pages, and read.
VectorlessRAG operationalises exactly that behaviour:

```text
Document → Parse into a hierarchical TREE (sections + page ranges + summaries)
Query    → LLM reads the tree → reasons which sections/pages are relevant
         → read only those pages → answer with citations
```

Retrieval becomes an act of **reasoning over structure** rather than
**similarity search over vectors**. The tree is small — it holds titles, page
ranges, and short summaries, not full text — so it fits inside an LLM's context
window even for large documents, letting the model survey the whole document's
shape at once.

Embeddings are not abandoned entirely; they are **retained only for an optional,
coarse document-level pre-filter**. When the library holds many documents, the
Librarian embeds each document's *description* once at index time and the query
once at run time, then ranks documents by cosine similarity to produce a
shortlist for the LLM to refine. This is the *only* place embeddings appear, and
they operate at **document granularity, never chunk granularity**. The precise,
in-document retrieval decision is made by the Navigator reasoning over the tree.

## Theoretical foundations

### The document as a tree

Most non-trivial documents are intrinsically hierarchical: a book is a list of
chapters, a chapter is a list of sections, a section is a list of paragraphs.
VectorlessRAG represents that hierarchy explicitly as a tree $T$ of nodes. Each
node $n$ is the tuple:

```text
n = ⟨ title, node_id, start_index, end_index, summary, children ⟩
```

- `title` — the section heading.
- `node_id` — a zero-padded identifier assigned during enrichment (e.g. `"0001"`).
- `start_index` / `end_index` — the **physical page range** (PDF) or, for
  Markdown, the **line number** (`line_num`) at which the section begins.
- `summary` — an LLM-written abstract of the section's content (parent nodes may
  instead carry a `prefix_summary`).
- `children` — nested subsections; the recursive structure that makes $T$ a tree.

Because each node stores a *range* and a *summary* rather than the section's full
text, the tree is a compact, navigable index. The full text lives elsewhere and
is fetched only once the Navigator has chosen which pages to read.

### Retrieval as tree search

Given a query $q$ and a tree $T$, retrieval is the problem of selecting a node
set $N^* \subseteq T$ whose underlying pages most likely contain the answer:

```text
N* = argmax_{ N ⊆ T }  P( answer(q) ∈ pages(N)  |  T, q )
```

Equivalently, in inline form: $N^{*} = \arg\max_{N \subseteq T} P(\text{answer}(q) \in \text{pages}(N) \mid T, q)$.

The decisive point is *how* the probability $P(\cdot)$ is estimated. In
conventional vector RAG it is approximated by a **distance metric** over
embeddings. Here it is **estimated by an LLM reasoning over the tree**: the model
reads the titles and summaries, reasons about which branch is likely to hold the
answer, and returns a page (or line) range together with its rationale — for
example, `{"pages": "3-5,8", "reasoning": "..."}`. The result is a learned,
semantic, *explainable* form of tree search: the selection comes with a stated
reason, not just a score.

### Grounding and faithfulness

The Generator operates under a strict **closed-book-on-the-context** constraint:
it must answer *only* from the retrieved pages and must explicitly state when the
answer is absent (for example, "This information isn't in the retrieved pages.").
This is the standard mechanism for suppressing **hallucination** — confident but
unsupported output.

Three design choices reinforce faithfulness:

- **Source tagging.** Each retrieved span is labelled with its origin
  (`[Source: <doc_name>, Page N]`), so every claim is traceable to evidence.
- **No replayed history.** Prior conversation turns are intentionally *not*
  replayed into the Generator call, which prevents facts from earlier,
  now-irrelevant documents from leaking into a grounded answer.
- **Cost-aware short-circuit.** If nothing was retrieved, the answer LLM call is
  skipped entirely — the system declines to produce an answer it could not ground
  rather than paying for an ungrounded round-trip.

Together these make every answer attributable to specific pages, which is the
prerequisite for trustworthy, academically defensible question answering. The
indexing side carries its own complementary signal: `verify_toc` checks the
fraction of sampled sections whose title genuinely appears on the page the tree
claims. This is an **indexing-quality self-check**, not an answer-quality
benchmark — see [evaluation](./evaluation.md) for how the project frames its
results honestly.

## See also

- [Methodology](./methodology.md) — the concrete indexing and four-stage query
  pipeline that implements the theory above.
- [Evaluation](./evaluation.md) — the qualitative comparison and the internal
  `verify_toc` self-signal, framed without overclaiming.
- [References](./references.md) — the works this background draws on.
- [Documentation index](./README.md) — back to the docs hub.
