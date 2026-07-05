import os
import shutil
from typing import List
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from google import genai
from google.genai import types
import chromadb
from pypdf import PdfReader

# Load environment variables
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

app = FastAPI(title="Gemini RAG Document Dashboard")

# Mount static directory
app.mount("/static", StaticFiles(directory="static"), name="static")

UPLOAD_DIR = "./uploads"
CHROMA_DIR = "./chroma_db"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Initialize Gemini Client & ChromaDB Client
client = None
if api_key and api_key != "your_free_api_key_here":
    client = genai.Client(api_key=api_key)

chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
collection = chroma_client.get_or_create_collection(
    name="session_chunks",
    metadata={"hnsw:space": "cosine"}
)

class ChatRequest(BaseModel):
    question: str

def extract_text_from_filepath(filepath: str) -> str:
    ext = os.path.splitext(filepath)[1].lower()
    text = ""
    if ext in [".txt", ".md"]:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    elif ext == ".pdf":
        reader = PdfReader(filepath)
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted + "\n"
    return text.strip()

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> List[str]:
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += (chunk_size - overlap)
    return chunks

def generate_embedding(text: str) -> List[float]:
    if not client:
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY is not configured in .env file.")
    response = client.models.embed_content(
        model="gemini-embedding-001",
        contents=text
    )
    return response.embeddings[0].values

@app.get("/")
async def get_index():
    return FileResponse("static/index.html")

@app.get("/api/files")
async def list_files():
    """Returns list of unique documents indexed in ChromaDB."""
    try:
        results = collection.get(include=["metadatas"])
        metadatas = results.get("metadatas", [])
        files = list(set([m.get("source") for m in metadatas if m and "source" in m]))
        return {"files": files, "total_chunks": len(metadatas)}
    except Exception as e:
        return {"files": [], "total_chunks": 0}

@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    """Uploads, parses, embeds, and indexes document files."""
    if not client:
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY missing in .env")

    indexed_summary = []

    for file in files:
        filename = file.filename
        filepath = os.path.join(UPLOAD_DIR, filename)
        
        # Save file to disk
        with open(filepath, "wb") as f:
            shutil.copyfileobj(file.file, f)

        # Extract text & chunk
        raw_text = extract_text_from_filepath(filepath)
        if not raw_text:
            continue
        chunks = chunk_text(raw_text)

        ids = []
        embeddings = []
        metadatas = []
        documents = []

        for idx, chunk in enumerate(chunks):
            chunk_id = f"{filename}_chunk_{idx}"
            emb = generate_embedding(chunk)
            ids.append(chunk_id)
            embeddings.append(emb)
            metadatas.append({"source": filename, "chunk_index": idx})
            documents.append(chunk)

        # Upsert to ChromaDB
        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=documents
        )

        indexed_summary.append({"filename": filename, "chunks": len(chunks)})

    return {"status": "success", "indexed": indexed_summary, "total_docs": collection.count()}

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    """Processes user question with vector search and Gemini grounded generation."""
    if not client:
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY is missing.")

    count = collection.count()
    if count == 0:
        return {
            "answer": "No documents uploaded for this session yet. Please drag & drop PDF, TXT, or Markdown documents on the left panel to begin!",
            "citations": []
        }

    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    # 1. Embed question
    query_vector = generate_embedding(question)

    # 2. Vector search in ChromaDB
    top_k = min(3, count)
    results = collection.query(
        query_embeddings=[query_vector],
        n_results=top_k
    )

    retrieved_docs = results.get("documents", [[]])[0]
    retrieved_meta = results.get("metadatas", [[]])[0]

    citations = []
    context_parts = []

    for idx, (doc, meta) in enumerate(zip(retrieved_docs, retrieved_meta)):
        source = meta.get("source", "Document")
        chunk_idx = meta.get("chunk_index", idx)
        citations.append({
            "source": source,
            "chunk_index": chunk_idx,
            "snippet": doc[:150] + "..." if len(doc) > 150 else doc
        })
        context_parts.append(f"--- Document Source: {source} (Chunk #{chunk_idx}) ---\n{doc}")

    full_context = "\n\n".join(context_parts)

    prompt = f"""
You are an expert Document Intelligence Assistant.
Answer the user's question accurately using ONLY the retrieved document context below.
Be clear, structured, and helpful. Format your response with markdown formatting (bullet points, bold text where appropriate).

--- RETRIEVED CONTEXT ---
{full_context}

--- USER QUESTION ---
{question}
"""

    models_to_try = ["gemini-2.5-flash", "gemini-1.5-flash"]
    answer_text = ""

    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.2)
            )
            answer_text = response.text
            break
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                continue
            raise HTTPException(status_code=500, detail=f"Gemini API error: {str(e)}")

    if not answer_text:
        answer_text = "The AI service is currently experiencing high demand. Please try asking again in a few seconds."

    return {
        "answer": answer_text,
        "citations": citations
    }

@app.post("/api/clear")
async def clear_session():
    """Clears all session documents and vector database."""
    global collection
    chroma_client.delete_collection("session_chunks")
    collection = chroma_client.get_or_create_collection(
        name="session_chunks",
        metadata={"hnsw:space": "cosine"}
    )
    # Delete uploaded files
    if os.path.exists(UPLOAD_DIR):
        for f in os.listdir(UPLOAD_DIR):
            fp = os.path.join(UPLOAD_DIR, f)
            if os.path.isfile(fp):
                os.remove(fp)

    return {"status": "success", "message": "Session cleared."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
