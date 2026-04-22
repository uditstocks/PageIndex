import os
import sys
import json
import asyncio
from pathlib import Path
from dotenv import load_dotenv
 
load_dotenv()

# Model config
GROQ_API_KEY= os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    print("❌ GROQ_API_KEY not found! Add it to your .env file.")
    sys.exit(1)

INDEXING_MODEL  = "groq/llama-3.1-8b-instant"   # builds the tree
RETRIEVAL_MODEL = "groq/llama-3.3-70b-versatile"   # navigates tree to find pages
ANSWER_MODEL    = "groq/llama-3.3-70b-versatile"   # generates final answer

WORKSPACE = Path("C:/Users/udits/OneDrive/Pictures/Desktop/Vectorless RAG").parent / "workspace" # where tree JSONs are saved
WORKSPACE.mkdir(exist_ok=True)

# Import PageIndex (must be cloned locally)
try:
    from pageindex.client import PageIndexClient
    from pageindex.retrieve import get_document_structure, get_page_content
except ImportError:
    print("""
❌ PageIndex not found!
 
You need to clone the PageIndex repo and install its requirements:
 
    git clone https://github.com/VectifyAI/PageIndex
    cd PageIndex
    pip install -r requirements.txt
 
Then run this script from INSIDE the PageIndex folder:
 
    python path/to/rag.py your_document.pdf
""")
    sys.exit(1)


# Step 1: Index PDF → Build Semantic Tree 
def index_pdf(pdf_path: str) -> tuple[PageIndexClient, str]:
    """
    Reads the PDF, calls Groq (via PageIndex) to build a hierarchical tree.
    The tree is saved as JSON in ./workspace/ for reuse — you won't re-index
    the same PDF twice unless you delete the workspace.
 
    Returns: (client, doc_id)
    """

    pdf_path = str(Path(pdf_path).resolve())
    doc_name = Path(pdf_path).stem

    print(f"\n{'='*60}")
    print(f"  🔱 Vectorless RAG — PageIndex Powered by {INDEXING_MODEL}")
    print(f"{'='*60}")
    print(f"\n📄 Document : {Path(pdf_path).name}")
    print(f"🤖 LLM      : {INDEXING_MODEL}")
    print(f"💾 Workspace: {WORKSPACE}")

    # Init client — pointing at Groq via litellm
    client = PageIndexClient(
        api_key=GROQ_API_KEY,
        model=INDEXING_MODEL,       # used for tree generation
        retrieve_model=RETRIEVAL_MODEL,
        workspace=str(WORKSPACE),
    )

    # Check if already indexed (PageIndex stores loaded docs in client.documents)
    for doc_id, doc_info in client.documents.items():
        if doc_info.get("path") == pdf_path or doc_info.get("name") == doc_name:
            print(f"\n✅ Already indexed! Loaded from workspace (doc_id={doc_id})")
            print("   Delete ./workspace/ folder to re-index.\n")
            return client, doc_id
        
     # Index the PDF — this calls Groq to reason about document structure
    print(f"\n⏳ Building semantic tree (this calls Groq ~10-30x depending on doc size)...")
    doc_id = client.index(pdf_path)

    print(f"\n✅ Tree built! doc_id = {doc_id}")
    print(f"   Saved to: {WORKSPACE}/{doc_id}.json\n")
 
    return client, doc_id

def retrieve_pages(client: PageIndexClient, doc_id: str, query: str) -> str:
    """
    The LLM navigates the document tree (like a human flipping through a book's
    table of contents) to find which pages are most relevant to the query.
 
    This is the core PageIndex magic — reasoning-based retrieval, not vectors.
    """
    print(f"🔍 Retrieving relevant pages for: '{query}'")
 
    # Get document structure (the tree) — strips full text to save tokens
    docs = client.documents  # internal dict: {doc_id: doc_info}
    client._ensure_doc_loaded(doc_id)
 
    doc_structure = get_document_structure(docs, doc_id)
 
    # Use Groq to reason: "Given this tree structure, which pages answer the query?"
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
    )
 
    raw = response.choices[0].message.content.strip()
 
    # Parse the page range from LLM response
    try:
        # Strip markdown code blocks if present
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        
        result = json.loads(raw.strip())
        pages_str = result.get("pages", "1-3")
    except Exception:
        # Fallback: try to extract page numbers from raw text
        import re
        match = re.search(r'"pages"\s*:\s*"([^"]+)"', raw)
        pages_str = match.group(1) if match else "1-3"
 
    print(f"   📌 Relevant pages identified: {pages_str}")
 
    # Fetch the actual page content
    page_content_list = get_page_content(docs, doc_id, pages_str)
 
    # Format into a single context string
    context_parts = []
    for item in page_content_list:
        page_num = item.get("page", "?")
        content = item.get("content", "").strip()
        if content:
            context_parts.append(f"[Page {page_num}]\n{content}")
 
    context = "\n\n---\n\n".join(context_parts)
    print(f"   ✅ Fetched {len(page_content_list)} page(s) of content.\n")
 
    return context

def generate_answer(query: str, context: str, history: list = None) -> str:
    """
    Final step: send retrieved context + query to Groq and get the answer.
    Supports multi-turn conversation via history.
    """
    import litellm
 
    system = """You are a precise, intelligent document assistant.
 
You are given relevant pages from a document, retrieved by PageIndex (a vectorless,
tree-based reasoning system — no semantic similarity, pure document structure reasoning).
 
Rules:
- Answer ONLY from the provided context. Never hallucinate.
- If the answer isn't in the context, say: "This information isn't in the retrieved pages."
- Be concise and structured. Use bullet points where helpful.
- Reference page numbers when citing specific facts.
- For follow-up questions, use the conversation history."""
 
    messages = [{"role": "system", "content": system}]
 
    if history:
        messages.extend(history)
 
    user_msg = f"""Retrieved document pages:
---
{context}
---
 
Question: {query}"""
 
    messages.append({"role": "user", "content": user_msg})
 
    response = litellm.completion(
        model=ANSWER_MODEL,
        messages=messages,
        temperature=0.1,
        max_tokens=1024,
    )
 
    return response.choices[0].message.content.strip()

def run_session(pdf_path: str):
    """
    Full multi-turn Q&A session on a PDF.
    Upload once → ask anything → answers grounded in the actual document.
    """
    # Step 1: Index
    client, doc_id = index_pdf(pdf_path)
 
    print(f"🎯 Ready! Ask anything about '{Path(pdf_path).name}'")
    print("   Type 'exit' to quit | 'info' to see doc structure\n")
 
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
            print("\n📋 Document Structure (Tree):")
            print(json.dumps(structure, indent=2)[:2000])
            print("...\n")
            continue
 
        try:
            # Step 2: Retrieve relevant pages
            context = retrieve_pages(client, doc_id, query)
 
            # Step 3: Generate answer
            answer = generate_answer(query, context, history)
 
            print(f"🤖 Assistant:\n{answer}\n")
            print("-" * 55)
 
            # Keep rolling history (last 6 turns = 12 messages)
            history.append({"role": "user", "content": query})
            history.append({"role": "assistant", "content": answer})
            if len(history) > 12:
                history = history[-12:]
 
        except Exception as e:
            print(f"❌ Error: {e}\n")
            import traceback
            traceback.print_exc()
 
 
# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
 
    # ✅ PDF path hardcoded — just change the filename below
    # Keep your PDF inside the PageIndex folder (same folder as this script)
    PDF_FILENAME = "Langchain and LangGraph Interview QA.pdf"   # 👈 CHANGE THIS to your PDF filename
 
    # Automatically resolves to: PageIndex/attention.pdf
    pdf_path = str(Path(__file__).parent / PDF_FILENAME)
 
    if not Path(pdf_path).exists():
        print(f"❌ File not found: {pdf_path}")
        print(f"   Make sure '{PDF_FILENAME}' is inside the PageIndex folder.")
        sys.exit(1)
 
    run_session(pdf_path)
 