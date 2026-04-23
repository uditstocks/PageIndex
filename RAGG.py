import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ─── NVIDIA API Key ────────────────────────────────────────────────────────────
NVIDIA_NIM_API_KEY = os.getenv("NVIDIA_NIM_API_KEY")
if not NVIDIA_NIM_API_KEY:
    print("❌ NVIDIA_NIM_API_KEY not found! Add it to your .env file.")
    sys.exit(1)

# ─── Model Config ──────────────────────────────────────────────────────────────
# NVIDIA NIM via litellm — openai/ prefix tells litellm to use OpenAI-compatible endpoint
INDEXING_MODEL  = "nvidia_nim/nvidia/llama-3.3-nemotron-super-49b-v1"   # builds the tree
RETRIEVAL_MODEL = "nvidia_nim/nvidia/llama-3.3-nemotron-super-49b-v1"   # navigates tree to find pages
ANSWER_MODEL    = "nvidia_nim/nvidia/llama-3.3-nemotron-super-49b-v1"   # generates final answer

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

# litellm ko batao NVIDIA endpoint use karo
os.environ["OPENAI_API_KEY"]  = NVIDIA_NIM_API_KEY   # PageIndex internally reads this
os.environ["OPENAI_BASE_URL"] = NVIDIA_BASE_URL  # redirect to NVIDIA NIM

# ─── Workspace — same folder as this script (PageIndex/) ──────────────────────
WORKSPACE = Path(__file__).parent / "workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)

# ─── Import PageIndex ──────────────────────────────────────────────────────────
try:
    from pageindex.client import PageIndexClient
    from pageindex.retrieve import get_document_structure, get_page_content
except ImportError:
    print("❌ PageIndex not found! Run from inside the cloned PageIndex folder.")
    sys.exit(1)


# ─── Step 1: Index PDF → Build Semantic Tree ───────────────────────────────────
def index_pdf(pdf_path: str) -> tuple[PageIndexClient, str]:
    pdf_path = str(Path(pdf_path).resolve())
    doc_name = Path(pdf_path).stem

    print(f"\n{'='*60}")
    print(f"  🔱 Vectorless RAG — PageIndex + NVIDIA NIM")
    print(f"{'='*60}")
    print(f"\n📄 Document : {Path(pdf_path).name}")
    print(f"🤖 LLM      : {INDEXING_MODEL}")
    print(f"💾 Workspace: {WORKSPACE}")

    client = PageIndexClient(
        api_key=NVIDIA_NIM_API_KEY,
        model=INDEXING_MODEL,
        retrieve_model=RETRIEVAL_MODEL,
        workspace=str(WORKSPACE),
    )

    # Already indexed? Load from workspace
    for doc_id, doc_info in client.documents.items():
        if doc_info.get("path") == pdf_path or doc_info.get("name") == doc_name:
            print(f"\n✅ Already indexed! Loaded from workspace (doc_id={doc_id})")
            print("   Delete ./workspace/ folder to re-index.\n")
            return client, doc_id

    # Fresh index
    print(f"\n⏳ Building semantic tree via NVIDIA NIM...")
    print("   (NVIDIA free tier: 1000 credits — enough for multiple PDFs)\n")

    doc_id = client.index(pdf_path)

    print(f"\n✅ Tree built! doc_id = {doc_id}")
    print(f"   Saved to: {WORKSPACE / (doc_id + '.json')}\n")

    return client, doc_id


# ─── Step 2: Retrieve Relevant Pages via Tree Search ──────────────────────────
def retrieve_pages(client: PageIndexClient, doc_id: str, query: str) -> str:
    print(f"🔍 Retrieving relevant pages for: '{query}'")

    docs = client.documents
    client._ensure_doc_loaded(doc_id)
    doc_structure = get_document_structure(docs, doc_id)

    import litellm

    tree_str = json.dumps(doc_structure, indent=2)

    prompt = f"""You are a precise document navigator.
Given this document's hierarchical tree structure, identify the page range(s) most relevant to answering the question.

Document Structure (tree index):
{tree_str}

Question: {query}

Instructions:
1. Look at the titles, summaries, and page numbers in the tree.
2. Identify which sections/pages are most relevant.
3. Return ONLY a JSON object like: {{"pages": "3-5, 8, 12-14"}}
4. Include 2-6 pages maximum. Be precise.
5. Return ONLY the JSON, nothing else."""

    response = litellm.completion(
        model=RETRIEVAL_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=200,
        api_key=NVIDIA_NIM_API_KEY,
        api_base=NVIDIA_BASE_URL,
    )

    raw = response.choices[0].message.content.strip()

    # Parse page range
    try:
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        pages_str = result.get("pages", "1-3")
    except Exception:
        import re
        match = re.search(r'"pages"\s*:\s*"([^"]+)"', raw)
        pages_str = match.group(1) if match else "1-3"

    print(f"   📌 Relevant pages: {pages_str}")

    page_content_list = get_page_content(docs, doc_id, pages_str)

    context_parts = []
    for item in page_content_list:
        # 1. If it's a dictionary, extract safely
        if isinstance(item, dict):
            page_num = str(item.get("page", "?"))
            content = str(item.get("content", item.get("text", ""))).strip()
        # 2. If it's a plain string, just use it directly
        elif isinstance(item, str):
            page_num = "?"
            content = item.strip()
        # 3. Skip unexpected data types
        else:
            continue
            
        # 4. Add to the context block if there's actual text
        if content:
            context_parts.append(f"[Page {page_num}]\n{content}")

    context = "\n\n---\n\n".join(context_parts)
    print(f"   ✅ Fetched {len(page_content_list)} page(s).\n")

    return context


# ─── Step 3: Generate Answer ───────────────────────────────────────────────────
def generate_answer(query: str, context: str, history: list = None) -> str:
    import litellm

    system = """You are a precise, intelligent document assistant.

You are given relevant pages from a document retrieved by PageIndex 
(vectorless, tree-based reasoning — no semantic similarity).

Rules:
- Answer ONLY from the provided context. Never hallucinate.
- If the answer isn't in the context, say: "This information isn't in the retrieved pages."
- Be concise and structured. Use bullet points where helpful.
- Reference page numbers when citing specific facts."""

    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)

    messages.append({"role": "user", "content": f"""Retrieved document pages:
---
{context}
---

Question: {query}"""})

    response = litellm.completion(
        model=ANSWER_MODEL,
        messages=messages,
        temperature=0.1,
        max_tokens=1024,
        api_key=NVIDIA_NIM_API_KEY,
        api_base=NVIDIA_BASE_URL,
    )

    return response.choices[0].message.content.strip()


# ─── Interactive Session ───────────────────────────────────────────────────────
def run_session(pdf_path: str):
    client, doc_id = index_pdf(pdf_path)

    print(f"🎯 Ready! Ask anything about '{Path(pdf_path).name}'")
    print("   Commands: 'exit' to quit | 'info' to see document tree\n")

    history = []

    while True:
        try:
            query = input("🧑 You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n🙏 Har Har Mahadev! Session ended.\n")
            break

        if not query:
            continue
        if query.lower() in ("exit", "quit", "q"):
            print("\n🙏 Har Har Mahadev! Session ended.\n")
            break
        if query.lower() == "info":
            client._ensure_doc_loaded(doc_id)
            structure = get_document_structure(client.documents, doc_id)
            print("\n📋 Document Tree:")
            print(json.dumps(structure, indent=2)[:2000])
            print("...\n")
            continue

        try:
            context = retrieve_pages(client, doc_id, query)
            answer = generate_answer(query, context, history)

            print(f"🤖 Assistant:\n{answer}\n")
            print("-" * 55)

            history.append({"role": "user", "content": query})
            history.append({"role": "assistant", "content": answer})
            if len(history) > 12:
                history = history[-12:]

        except Exception as e:
            print(f"❌ Error: {e}\n")
            import traceback
            traceback.print_exc()


# ─── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":

    # 👇 Apna PDF ka naam yahan set karo
    PDF_FILENAME = "Langchain and LangGraph Interview QA.pdf"

    pdf_path = str(Path(__file__).parent / PDF_FILENAME)

    if not Path(pdf_path).exists():
        print(f"❌ File not found: {pdf_path}")
        print(f"   Make sure '{PDF_FILENAME}' is inside the PageIndex folder.")
        sys.exit(1)

    run_session(pdf_path)