import os
import warnings
warnings.filterwarnings("ignore")
# Suppress TensorFlow/Keras deprecation and init warnings aggressively
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
os.environ["ABSL_LOG_LEVEL"] = "3"
import logging
logging.getLogger("tensorflow").setLevel(logging.ERROR)

import sys
import json
import numpy as np
import asyncio
import re
import time
import litellm
from pathlib import Path
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown
from rich.rule import Rule
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

load_dotenv()

# ─── NVIDIA API Key ────────────────────────────────────────────────────────────
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
if not NVIDIA_API_KEY:
    print("❌ NVIDIA_API_KEY not found! Add it to your .env file.")
    sys.exit(1)

# ─── Model Config ──────────────────────────────────────────────────────────────
# NVIDIA NIM via litellm — openai/ prefix tells litellm to use OpenAI-compatible endpoint
INDEXING_MODEL  = "nvidia_nim/nvidia/llama-3.3-nemotron-super-49b-v1"   # builds the tree
RETRIEVAL_MODEL = "nvidia_nim/nvidia/llama-3.3-nemotron-super-49b-v1"   # navigates tree to find pages
ANSWER_MODEL    = "nvidia_nim/nvidia/llama-3.3-nemotron-super-49b-v1"   # generates final answer
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2" # local embeddings for document selection

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

# litellm ko batao NVIDIA endpoint use karo
console = Console()

os.environ["OPENAI_API_KEY"]  = NVIDIA_API_KEY   # PageIndex internally reads this
os.environ["OPENAI_BASE_URL"] = NVIDIA_BASE_URL  # redirect to NVIDIA NIM

# ─── Workspace — same folder as this script (PageIndex/) ──────────────────────
WORKSPACE = Path(__file__).parent / "workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)

# ─── Import PageIndex ──────────────────────────────────────────────────────────
try:
    from pageindex.client import PageIndexClient
    from pageindex.utils import llm_aembed
    from pageindex.retrieve import get_document_structure, get_page_content
except ImportError:
    print("❌ PageIndex not found! Run from inside the cloned PageIndex folder.")
    sys.exit(1)


# ─── Step 1: Index PDF → Build Semantic Tree ───────────────────────────────────
def initialize_client() -> PageIndexClient:
    client = PageIndexClient(
        api_key=NVIDIA_API_KEY,
        model=INDEXING_MODEL,
        retrieve_model=RETRIEVAL_MODEL,
        embedding_model=EMBEDDING_MODEL, # Pass embedding model to client
        workspace=str(WORKSPACE),
    )
    return client

def index_document(client: PageIndexClient, file_path: str) -> str:
    file_path = str(Path(file_path).resolve())
    file_name = Path(file_path).name

    # Already indexed in this workspace?
    for doc_id, doc_info in client.documents.items():
        if doc_info.get("path") == file_path or doc_info.get("doc_name") == file_name:
            console.print(f"[green]✅ Document '[bold]{file_name}[/bold]' already indexed.[/green]")
            return doc_id

    # Fresh index
    with console.status(f"[bold blue]Indexing {file_name}...", spinner="dots"):
        doc_id = client.index(file_path)
        return doc_id

# ─── Step 2: The Librarian (Select Relevant Documents) ────────────────────────
async def select_relevant_documents(client: PageIndexClient, query: str) -> list[str]:
    """The Librarian: Scans document descriptions to pick candidates."""
    if not client.documents:
        return []

    # --- Step 1: Embedding-based Pre-filtering ---
    # Generate embedding for the query
    with console.status("[dim]Generating query embedding...", spinner="simpleDots"):
        query_embedding = np.array(await llm_aembed(client.embedding_model, query))
    
    if query_embedding.size == 0:
        console.print("[yellow]⚠️  Failed to generate query embedding. Skipping pre-filtering.[/yellow]")
        # Fallback to LLM-only selection if embedding fails
        return await _llm_select_documents_fallback(client, query)

    document_scores = []
    for doc_id, info in client.documents.items():
        doc_embedding = info.get('doc_description_embedding')
        if doc_embedding:
            doc_embedding = np.array(doc_embedding)
            # Calculate cosine similarity
            q_norm = np.linalg.norm(query_embedding)
            d_norm = np.linalg.norm(doc_embedding)
            
            if q_norm > 0 and d_norm > 0:
                similarity = np.dot(query_embedding, doc_embedding) / (q_norm * d_norm)
                document_scores.append((similarity, doc_id, info.get('doc_name'), info.get('doc_description')))
            else:
                document_scores.append((0.0, doc_id, info.get('doc_name'), info.get('doc_description')))

    # Sort by similarity and pick top N (e.g., 5)
    document_scores.sort(key=lambda x: x[0], reverse=True)
    top_n_docs = document_scores[:5] # Adjust N as needed

    if document_scores:
        # Processing Visualization: Show the Similarity Table
        table = Table(title="Librarian: Document Similarity Scores", title_style="bold magenta")
        table.add_column("Score", justify="right", style="cyan")
        table.add_column("Document Name", style="white")
        for score, did, name, desc in document_scores[:8]: # Show top 8 for transparency
            table.add_row(f"{score:.4f}", name)
        console.print(table)

    if not top_n_docs:
        # Fallback to LLM selection if no embeddings are available (e.g. for existing indices)
        return await _llm_select_documents_fallback(client, query)

    # --- Step 2: LLM Refinement on Pre-filtered Documents ---
    # Create a catalog string for the LLM with only the top N documents
    refined_catalog = []
    for score, doc_id, name, desc in top_n_docs:
        refined_catalog.append(f"ID: {doc_id} | Name: {name} | Description: {desc}")

    refined_catalog_str = "\n".join(refined_catalog)

    prompt = f"""You are a Semantic Librarian. 
Given the following *pre-filtered* library of documents and their descriptions, identify which documents are most likely to contain the answer to the user's question.

Pre-filtered Library Catalog:
{refined_catalog_str}

User Question: {query}

Instructions:
1. Pick the top 1-3 most relevant documents from the PRE-FILTERED list.
2. If no document from the pre-filtered list is relevant, return an empty list.
3. Return ONLY a JSON object with the document IDs like: {{"selected_ids": ["uuid-1", "uuid-2"]}}"""

    with console.status("[bold magenta]Librarian is refining selection...", spinner="aesthetic"):
        response = litellm.completion(
            model=RETRIEVAL_MODEL,
            api_base="https://integrate.api.nvidia.com/v1",
            api_key=os.environ.get("NVIDIA_API_KEY"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )

    raw = response.choices[0].message.content.strip()
    try:
        # Basic JSON cleanup
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "")
        result = json.loads(raw.strip())
        selected_ids = result.get("selected_ids", [])
    except Exception:
        selected_ids = re.findall(r'[a-f0-9\-]{36}', raw) # Match UUIDs

    # Ensure we only return IDs that actually exist
    return list(dict.fromkeys([did for did in selected_ids if did in client.documents]))

async def _llm_select_documents_fallback(client: PageIndexClient, query: str) -> list[str]:
    """Fallback to LLM-only document selection if embedding fails or is not configured."""
    doc_catalog = []
    for doc_id, info in client.documents.items():
        desc = info.get("doc_description") or "No description available."
        doc_catalog.append(f"ID: {doc_id} | Name: {info.get('doc_name')} | Description: {desc}")

    catalog_str = "\n".join(doc_catalog)

    prompt = f"""You are a Semantic Librarian. 
Given the following library of documents and their descriptions, identify which documents are likely to contain the answer to the user's question.

Library Catalog:
{catalog_str}

User Question: {query}

Instructions:
1. Pick the top 1-3 most relevant documents.
2. If no document is relevant, return an empty list.
3. Return ONLY a JSON object with the document IDs like: {{"selected_ids": ["uuid-1", "uuid-2"]}}"""

    response = litellm.completion(
        model=RETRIEVAL_MODEL,
        api_base="https://integrate.api.nvidia.com/v1",
        api_key=os.environ.get("NVIDIA_API_KEY"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )

    raw = response.choices[0].message.content.strip()
    try:
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "")
        result = json.loads(raw.strip())
        selected_ids = result.get("selected_ids", [])
    except Exception:
        selected_ids = re.findall(r'[a-f0-9\-]{36}', raw)

    return list(dict.fromkeys([did for did in selected_ids if did in client.documents]))


# ─── Step 2: Retrieve Relevant Pages via Tree Search ──────────────────────────
def retrieve_pages(client: PageIndexClient, doc_id: str, query: str, progress: Progress = None) -> str:
    console.print(f"🔍 [dim]Retrieving relevant pages for: '{query}'[/dim]")

    docs = client.documents
    client._ensure_doc_loaded(doc_id)
    doc_structure = get_document_structure(docs, doc_id)

    tree_str = json.dumps(doc_structure, indent=2)

    prompt = f"""You are a precise document navigator.
Given this document's hierarchical tree structure, identify the page range(s) most relevant to answering the question.

Document Structure (tree index):
{tree_str}

Question: {query}

Instructions:
1. Look at the titles, summaries, and page numbers in the tree.
2. Identify which sections/pages are most relevant.
3. Return ONLY a JSON object like: {{"pages": "3-5, 8", "reasoning": "I chose these because section 2.1 covers X..."}}
4. Include 2-6 pages maximum. Be precise.
5. Return ONLY the JSON, nothing else."""

    with console.status(f"[bold cyan]🛰️  Navigator scanning {client.documents[doc_id].get('doc_name')}...", spinner="earth"):
        response = litellm.completion(
            model=RETRIEVAL_MODEL,
            api_base="https://integrate.api.nvidia.com/v1",
            api_key=os.environ.get("NVIDIA_API_KEY"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=200,
        )

    raw = response.choices[0].message.content.strip()

    # Parse page range
    pages_str = "1-2" # default
    reasoning = "No reasoning provided."

    try:
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        pages_str = result.get("pages", "1-3")
        reasoning = result.get("reasoning", reasoning)
    except Exception:
        match = re.search(r'"pages"\s*:\s*"([^"]+)"', raw)
        pages_str = match.group(1) if match else "1-3"

    doc_name = client.documents[doc_id].get('doc_name', 'Doc')

    # ─── Human in the Loop: Navigator Plan ──────────────────────────────────
    console.print(Panel(
        f"[bold yellow]Reason:[/bold yellow] {reasoning}\n[bold yellow]Target:[/bold yellow] Pages {pages_str}",
        title=f"🗺️  Navigation Plan: {doc_name}", border_style="yellow"
    ))
    user_input = console.input(f"   [bold green]✅ Proceed?[/bold green] [Enter for Yes / Type range / 's' to skip]: ").strip()
    
    if user_input.lower() == 's':
        console.print(f"   ⏩ [italic]Skipping {doc_name}...[/italic]")
        return ""
    elif user_input and user_input.lower() not in ('y', 'yes'):
        pages_str = user_input
        console.print(f"   🛠️  [italic]Manual override: Using pages {pages_str}[/italic]")

    # CRITICAL FIX: get_page_content returns a JSON string, must parse it.
    page_content_raw = get_page_content(docs, doc_id, pages_str)
    try:
        page_content_list = json.loads(page_content_raw)
        if isinstance(page_content_list, dict) and "error" in page_content_list:
            console.print(f"[red]❌ Retrieval error: {page_content_list['error']}[/red]")
            return ""
    except json.JSONDecodeError:
        # Handle case where it might already be a list or direct text
        page_content_list = []

    # Create local progress display only during the reading phase to avoid conflict with console.input
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
        transient=True
    ) as progress_bar:
        retrieval_task = progress_bar.add_task(f"   [cyan]📄 Reading pages from {doc_name}[/cyan]", total=len(page_content_list))

        context_parts = []
        for item in page_content_list:
            if isinstance(item, dict):
                page_num = str(item.get("page", "?"))
                content = str(item.get("content", item.get("text", ""))).strip()
            elif isinstance(item, str):
                page_num = "?"
                content = item.strip()
            else:
                continue
                
            if content:
                context_parts.append(f"[Source: {doc_name}, Page {page_num}]\n{content}")
                
            progress_bar.advance(retrieval_task)
            time.sleep(0.05)

        return "\n\n---\n\n".join(context_parts)


# ─── Step 3: Generate Answer ───────────────────────────────────────────────────
def generate_answer(query: str, context: str, history: list = None) -> str:
    system = """You are a precise, intelligent document assistant.

You are given relevant pages from a document retrieved by PageIndex 
(vectorless, tree-based reasoning — no semantic similarity).

Rules:
- Answer ONLY from the provided context. Never hallucinate.
- If the answer isn't in the context, say: "This information isn't in the retrieved pages."
- Be concise and structured. Use bullet points where helpful.
- Reference the Document Name and page numbers when citing specific facts."""

    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)

    messages.append({"role": "user", "content": f"""Retrieved document pages:
---
{context}
---

Question: {query}"""})

    with console.status("[bold green]Generating final answer...", spinner="point"):
        response = litellm.completion(
            model=ANSWER_MODEL,
            api_base="https://integrate.api.nvidia.com/v1",
            api_key=os.environ.get("NVIDIA_API_KEY"),
            messages=messages,
            temperature=0.1,
            max_tokens=1024,
        )

    return response.choices[0].message.content.strip()


# ─── Interactive Session ───────────────────────────────────────────────────────
def run_session(pdf_files: list[str]):
    console.print(Panel.fit(
        "[bold white]Multi-Doc Semantic Librarian[/bold white]\n[dim]Powered by PageIndex + NVIDIA NIM[/dim]",
        border_style="cyan"
    ))

    client = initialize_client()
    
    all_doc_ids = []
    for f in pdf_files:
        did = index_document(client, f)
        all_doc_ids.append(did)

    indexed_names = [info.get('doc_name') for info in client.documents.values()]
    
    console.print(f"\n🤖 [bold]Model:[/bold] [dim]{INDEXING_MODEL}[/dim]")
    console.print(f"📚 [bold]Library:[/bold] {len(indexed_names)} documents loaded.")
    console.print("\n[bold cyan]🎯 Ready![/bold cyan] Ask a question across the library.")
    console.print("[dim]Commands: 'exit' to quit | 'info' to see doc list[/dim]\n")

    history = []

    while True:
        try:
            query = console.input("[bold green]🧑 You:[/bold green] ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n🙏 Har Har Mahadev! Session ended.\n")
            break

        if not query:
            continue
        if query.lower() in ("exit", "quit", "q"):
            print("\n🙏 Har Har Mahadev! Session ended.\n")
            break
        if query.lower() in ("info", "list"):
            table = Table(show_header=True, header_style="bold blue")
            table.add_column("Document Name")
            for did, info in client.documents.items():
                table.add_row(info.get('doc_name'))
            console.print(table)
            continue

        try:
            selected_ids = asyncio.run(select_relevant_documents(client, query)) # Await the async function
            
            if not selected_ids:
                print("🤷 No relevant documents found in the library for this query.\n")
                continue

            selected_names = [client.documents[did].get('doc_name') for did in selected_ids]
            # ─── Human in the Loop: Librarian Verification ──────────────────
            console.print(Panel(
                f"📚 [bold]Librarian suggests:[/bold] {', '.join(selected_names)}", 
                border_style="magenta"
            ))
            confirm = console.input("   [bold magenta]✅ Search these?[/bold magenta] [Enter/Yes, 'n' to re-query, or name]: ").strip().lower()
            
            if confirm == 'n':
                continue
            elif confirm and confirm not in ('y', 'yes'):
                # Filter selection based on manual names provided
                selected_ids = [did for did in selected_ids if confirm in client.documents[did].get('doc_name', '').lower()]
                if not selected_ids:
                    print("⚠️  Selection narrowed to zero. Try again.")
                    continue

            # Navigation Step (per document)
            full_context_parts = []
            
            for did in selected_ids:
                doc_context = retrieve_pages(client, did, query)
                if doc_context:
                    full_context_parts.append(doc_context)
            
            aggregated_context = "\n\n=== NEXT DOCUMENT ===\n\n".join(full_context_parts)

            # Answer Step
            answer = generate_answer(query, aggregated_context, history)
            console.print(Rule(style="dim"))
            console.print(f"[bold blue]🤖 Assistant:[/bold blue]")
            console.print(Markdown(answer))
            console.print(Rule(style="dim"))

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

    # Create a specific folder for input documents if it doesn't exist
    DOCS_DIR = Path(__file__).parent / "documents"
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Supported formats based on PageIndex capabilities
    EXTENSIONS = {".pdf", ".md", ".markdown"}
    
    paths = []
    for file in DOCS_DIR.iterdir():
        if file.suffix.lower() in EXTENSIONS:
            paths.append(str(file.resolve()))

    # Check if workspace has existing indices
    has_workspace_data = any(WORKSPACE.glob("*.json"))

    if not paths and not has_workspace_data:
        console.print(f"[yellow]⚠️  No documents found in 'documents/' and workspace is empty.[/yellow]")
        console.print(f"💡 Please drop your PDF or Markdown files into: [bold]{DOCS_DIR}[/bold]\n")
    elif paths:
        console.print(f"📂 [dim]Scanning folder for new documents: {DOCS_DIR.resolve()}[/dim]")

    run_session(paths)