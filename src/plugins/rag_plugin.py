"""
RAG (Retrieval Augmented Generation) Plugin implementation.

This plugin provides context retrieval capabilities for agents.
"""

import logging
from typing import Any, Dict, List, Optional

from .registry import Plugin, Tool
from ..core.models import PluginConfig, RAGSourceConfig

logger = logging.getLogger(__name__)


class RAGPlugin(Plugin):
    """
    Plugin for Retrieval Augmented Generation.

    Provides tools for:
    - Semantic search over documents
    - Context retrieval for agent queries
    - Vector database integration
    """

    def __init__(self, config: PluginConfig):
        super().__init__(config)
        self._rag_config: Optional[RAGSourceConfig] = config.rag_config
        self._vector_store = None
        self._embeddings = None

    async def initialize(self) -> None:
        """Initialize the RAG plugin."""
        if self._initialized:
            return

        if not self._rag_config:
            raise ValueError("RAGSourceConfig is required")

        # Initialize based on type
        try:
            if self._rag_config.type == "vector_db":
                await self._init_vector_db()
            elif self._rag_config.type == "file":
                await self._init_file_source()
            elif self._rag_config.type == "api":
                await self._init_api_source()
            else:
                logger.warning(f"Unknown RAG source type: {self._rag_config.type}")

            # Create search tool
            self._tools = [self._create_search_tool()]

            self._initialized = True
            logger.info(f"RAG plugin initialized: {self._rag_config.name}")

        except Exception as e:
            logger.error(f"Failed to initialize RAG plugin: {e}")
            self._tools = [self._create_fallback_tool()]
            self._initialized = True

    async def _init_vector_db(self) -> None:
        """Initialize vector database connection."""
        # Try different vector DB backends
        connection_string = self._rag_config.connection_string

        if "chroma" in connection_string.lower() if connection_string else False:
            await self._init_chromadb()
        elif "pinecone" in connection_string.lower() if connection_string else False:
            await self._init_pinecone()
        elif "qdrant" in connection_string.lower() if connection_string else False:
            await self._init_qdrant()
        else:
            # Default: try ChromaDB local
            await self._init_chromadb()

    async def _init_chromadb(self) -> None:
        """Initialize ChromaDB."""
        try:
            import chromadb
            from chromadb.config import Settings

            # Create client
            self._vector_store = chromadb.Client(Settings(
                anonymized_telemetry=False,
            ))

            # Get or create collection
            collection_name = self._rag_config.collection_name or "default"
            self._collection = self._vector_store.get_or_create_collection(
                name=collection_name,
            )

            logger.info(f"ChromaDB initialized with collection: {collection_name}")

        except ImportError:
            logger.warning("ChromaDB not installed")
            raise

    async def _init_pinecone(self) -> None:
        """Initialize Pinecone."""
        try:
            from pinecone import Pinecone

            pc = Pinecone(api_key=self._extract_api_key())
            index_name = self._rag_config.collection_name or "default"
            self._vector_store = pc.Index(index_name)

            logger.info(f"Pinecone initialized with index: {index_name}")

        except ImportError:
            logger.warning("Pinecone not installed")
            raise

    async def _init_qdrant(self) -> None:
        """Initialize Qdrant."""
        try:
            from qdrant_client import QdrantClient

            self._vector_store = QdrantClient(
                url=self._rag_config.connection_string,
            )

            logger.info("Qdrant initialized")

        except ImportError:
            logger.warning("Qdrant client not installed")
            raise

    async def _init_file_source(self) -> None:
        """Initialize file-based RAG source."""
        # For file sources, we'll use a simple in-memory approach
        # In production, you'd want to index files into a vector store
        self._documents = []
        logger.info("File-based RAG source initialized (placeholder)")

    async def _init_api_source(self) -> None:
        """Initialize API-based RAG source."""
        # For API sources, we'll make HTTP calls to retrieve context
        self._api_url = self._rag_config.connection_string
        logger.info(f"API-based RAG source initialized: {self._api_url}")

    def _extract_api_key(self) -> Optional[str]:
        """Extract API key from connection string."""
        if not self._rag_config.connection_string:
            return None

        # Simple parsing: assume format "protocol://api_key@host"
        conn = self._rag_config.connection_string
        if "@" in conn:
            parts = conn.split("@")
            return parts[0].split("//")[-1]
        return None

    def _create_search_tool(self) -> Tool:
        """Create the search tool."""
        return Tool(
            name=f"search_{self._rag_config.name}",
            description=f"Search for relevant information in {self._rag_config.name}. "
                        f"Use this to find context and background information.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": f"Number of results (default: {self._rag_config.top_k})",
                        "default": self._rag_config.top_k,
                    },
                },
                "required": ["query"],
            },
            handler=self._search,
            source="rag",
            metadata={
                "rag_source": self._rag_config.name,
                "rag_type": self._rag_config.type,
            },
        )

    def _create_fallback_tool(self) -> Tool:
        """Create a fallback tool when RAG is not available."""
        async def fallback_search(query: str, top_k: int = 5) -> Dict[str, Any]:
            return {
                "results": [],
                "message": "RAG source not available",
            }

        return Tool(
            name=f"search_{self._rag_config.name}",
            description=f"Search (unavailable): {self._rag_config.name}",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
            handler=fallback_search,
            source="rag",
            metadata={"fallback": True},
        )

    async def _search(self, query: str, top_k: Optional[int] = None) -> Dict[str, Any]:
        """
        Perform semantic search.

        Args:
            query: Search query
            top_k: Number of results to return

        Returns:
            Search results with documents and scores
        """
        k = top_k or self._rag_config.top_k

        try:
            if self._rag_config.type == "vector_db":
                return await self._search_vector_db(query, k)
            elif self._rag_config.type == "file":
                return await self._search_files(query, k)
            elif self._rag_config.type == "api":
                return await self._search_api(query, k)
            else:
                return {"results": [], "error": "Unknown source type"}

        except Exception as e:
            logger.error(f"RAG search error: {e}")
            return {"results": [], "error": str(e)}

    async def _search_vector_db(self, query: str, top_k: int) -> Dict[str, Any]:
        """Search in vector database."""
        if hasattr(self, '_collection'):
            # ChromaDB
            results = self._collection.query(
                query_texts=[query],
                n_results=top_k,
            )

            documents = []
            if results['documents']:
                for i, doc in enumerate(results['documents'][0]):
                    documents.append({
                        "content": doc,
                        "score": results['distances'][0][i] if results.get('distances') else None,
                        "metadata": results['metadatas'][0][i] if results.get('metadatas') else {},
                    })

            return {"results": documents}

        elif self._vector_store:
            # Generic vector store (Pinecone, Qdrant, etc.)
            # Would need embedding first
            return {"results": [], "message": "Direct search not implemented"}

        return {"results": []}

    async def _search_files(self, query: str, top_k: int) -> Dict[str, Any]:
        """Search in file-based documents."""
        # Simple keyword search for demo
        results = []
        query_lower = query.lower()

        for doc in self._documents:
            if query_lower in doc.get('content', '').lower():
                results.append(doc)
                if len(results) >= top_k:
                    break

        return {"results": results}

    async def _search_api(self, query: str, top_k: int) -> Dict[str, Any]:
        """Search via external API."""
        import aiohttp

        if not self._api_url:
            return {"results": [], "error": "No API URL configured"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._api_url,
                    json={"query": query, "top_k": top_k},
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return {"results": data.get("results", [])}
                    else:
                        return {"results": [], "error": f"API error: {response.status}"}

        except Exception as e:
            return {"results": [], "error": str(e)}

    async def add_documents(
        self,
        documents: List[Dict[str, Any]],
        embeddings: Optional[List[List[float]]] = None,
    ) -> None:
        """
        Add documents to the RAG source.

        Args:
            documents: List of documents with 'content' and optional 'metadata'
            embeddings: Optional pre-computed embeddings
        """
        if self._rag_config.type == "vector_db" and hasattr(self, '_collection'):
            ids = [f"doc_{i}" for i in range(len(documents))]
            contents = [doc.get('content', '') for doc in documents]
            metadatas = [doc.get('metadata', {}) for doc in documents]

            self._collection.add(
                ids=ids,
                documents=contents,
                metadatas=metadatas,
                embeddings=embeddings,
            )

        elif self._rag_config.type == "file":
            self._documents.extend(documents)

    async def cleanup(self) -> None:
        """Clean up RAG resources."""
        self._vector_store = None
        self._embeddings = None
        self._tools = []

    def get_tools(self) -> List[Tool]:
        """Get RAG search tools."""
        return self._tools
