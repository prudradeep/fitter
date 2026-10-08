import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db.session import Base
from app.db.session import get_db
from app.models import KnowledgeChunk, KnowledgeDocument
from app.routes import sync as sync_routes
from app.services import knowledge_base as kb
from app.services.bedrock_provider import EmbeddingVector, _embedding_sync, proxy_embedding


class EmbeddingRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_uses_authenticated_proxy_without_data_sync(self) -> None:
        settings = Settings(
            app_mode="client", embedding_provider="bedrock", sync_enabled=False,
            sync_server_url="https://example.test", sync_api_token="token", _env_file=None,
        )
        service = kb.KnowledgeBaseService.__new__(kb.KnowledgeBaseService)
        service.settings = settings
        with (
            patch("app.services.knowledge_base.proxy_bedrock_embedding", new_callable=AsyncMock) as proxy,
            patch("app.services.knowledge_base.bedrock_embedding", new_callable=AsyncMock) as direct,
        ):
            proxy.return_value = EmbeddingVector([0.1, 0.2], "server-model")
            result = await service._embed("example")
        self.assertEqual(result.model_id, "server-model")
        proxy.assert_awaited_once_with(settings, "example")
        direct.assert_not_awaited()

    async def test_proxy_sends_token_and_returns_server_model(self) -> None:
        settings = Settings(
            app_mode="client", embedding_provider="bedrock", sync_server_url="https://example.test",
            sync_api_token="token", _env_file=None,
        )
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["request"] = request
            return httpx.Response(200, json={"embedding": [0.1, 0.2], "model": "server-model"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("app.services.bedrock_provider.httpx.AsyncClient", return_value=client):
            result = await proxy_embedding(settings, "example")
        self.assertEqual(result.model_id, "server-model")
        self.assertEqual(seen["request"].url.path, "/api/sync/llm/embedding")
        self.assertEqual(seen["request"].headers["X-Sync-Token"], "token")

    async def test_server_uses_bedrock_directly(self) -> None:
        settings = Settings(app_mode="server", embedding_provider="bedrock", _env_file=None)
        service = kb.KnowledgeBaseService.__new__(kb.KnowledgeBaseService)
        service.settings = settings
        with (
            patch("app.services.knowledge_base.proxy_bedrock_embedding", new_callable=AsyncMock) as proxy,
            patch("app.services.knowledge_base.bedrock_embedding", new_callable=AsyncMock) as direct,
        ):
            direct.return_value = [0.3, 0.4]
            self.assertEqual(await service._embed("example"), [0.3, 0.4])
        direct.assert_awaited_once_with(settings, "example")
        proxy.assert_not_awaited()


class BedrockEmbeddingTests(unittest.TestCase):
    def test_titan_invoke_model_payload_and_vector(self) -> None:
        settings = Settings(
            app_mode="server", embedding_provider="bedrock",
            bedrock_embedding_cache_enabled=False, _env_file=None,
        )
        client = MagicMock()
        client.invoke_model.return_value = {"body": io.BytesIO(json.dumps({"embedding": [1, 2]}).encode())}
        with (
            patch("app.services.bedrock_provider._client", return_value=client),
            patch("app.services.bedrock_provider.log_llm_exchange"),
        ):
            result = _embedding_sync(settings, "example")
        self.assertEqual(result, [1.0, 2.0])
        self.assertEqual(result.model_id, settings.bedrock_embedding_model_id)
        self.assertEqual(json.loads(client.invoke_model.call_args.kwargs["body"]), {"inputText": "example"})

    def test_server_cache_reuses_exact_text_and_model_across_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(
                app_mode="server", embedding_provider="bedrock",
                bedrock_embedding_cache_path=str(Path(temp_dir) / "embeddings.sqlite"),
                _env_file=None,
            )
            client = MagicMock()
            client.invoke_model.return_value = {"body": io.BytesIO(b'{"embedding": [1, 2]}')}
            with (
                patch("app.services.bedrock_provider._client", return_value=client),
                patch("app.services.bedrock_provider.log_llm_exchange"),
            ):
                first = _embedding_sync(settings, "private chunk")
                second = _embedding_sync(settings, "private chunk")
            self.assertEqual(first, second)
            client.invoke_model.assert_called_once()
            self.assertNotIn(b"private chunk", Path(settings.bedrock_embedding_cache_path).read_bytes())

            changed_model = settings.model_copy(update={"bedrock_embedding_model_id": "another-model"})
            client.invoke_model.return_value = {"body": io.BytesIO(b'{"embedding": [3, 4]}')}
            with (
                patch("app.services.bedrock_provider._client", return_value=client),
                patch("app.services.bedrock_provider.log_llm_exchange"),
            ):
                different = _embedding_sync(changed_model, "private chunk")
            self.assertEqual(different, [3.0, 4.0])
            self.assertEqual(client.invoke_model.call_count, 2)

    def test_cache_write_failure_returns_bedrock_vector(self) -> None:
        settings = Settings(app_mode="server", embedding_provider="bedrock", _env_file=None)
        client = MagicMock()
        client.invoke_model.return_value = {"body": io.BytesIO(b'{"embedding": [1, 2]}')}
        with (
            patch("app.services.bedrock_provider._client", return_value=client),
            patch("app.services.bedrock_provider.cached_embedding", return_value=None),
            patch("app.services.bedrock_provider.store_embedding", side_effect=OSError("disk unavailable")),
            patch("app.services.bedrock_provider.log_llm_exchange"),
        ):
            result = _embedding_sync(settings, "example")
        self.assertEqual(result, [1.0, 2.0])
        client.invoke_model.assert_called_once()


class EmbeddingProxyRouteTests(unittest.TestCase):
    def test_proxy_requires_valid_sync_token(self) -> None:
        settings = Settings(app_mode="server", embedding_provider="bedrock", sync_enabled=True, _env_file=None)
        app = FastAPI()
        app.include_router(sync_routes.router)
        app.dependency_overrides[get_db] = lambda: object()
        with (
            patch.object(sync_routes, "settings", settings),
            patch.object(sync_routes.SyncService, "sync_client_for_token", side_effect=lambda token: {"id": "client"} if token == "valid" else None),
            patch.object(sync_routes, "bedrock_embedding", new=AsyncMock(return_value=EmbeddingVector([0.1, 0.2], "server-model"))) as embed,
            TestClient(app) as client,
        ):
            denied = client.post("/api/sync/llm/embedding", headers={"X-Sync-Token": "invalid"}, json={"text": "example"})
            accepted = client.post("/api/sync/llm/embedding", headers={"X-Sync-Token": "valid"}, json={"text": "example"})
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["model"], "server-model")
        embed.assert_awaited_once_with(settings, "example")

    def test_proxy_reuses_cached_embedding_on_second_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(
                app_mode="server", embedding_provider="bedrock", sync_enabled=True,
                bedrock_embedding_cache_path=str(Path(temp_dir) / "embeddings.sqlite"),
                _env_file=None,
            )
            app = FastAPI()
            app.include_router(sync_routes.router)
            app.dependency_overrides[get_db] = lambda: object()
            bedrock_client = MagicMock()
            bedrock_client.invoke_model.return_value = {"body": io.BytesIO(b'{"embedding": [1, 2]}')}
            with (
                patch.object(sync_routes, "settings", settings),
                patch.object(sync_routes.SyncService, "sync_client_for_token", return_value={"id": "client"}),
                patch("app.services.bedrock_provider._client", return_value=bedrock_client),
                patch("app.services.bedrock_provider.log_llm_exchange"),
                TestClient(app) as client,
            ):
                first = client.post("/api/sync/llm/embedding", headers={"X-Sync-Token": "valid"}, json={"text": "same chunk"})
                second = client.post("/api/sync/llm/embedding", headers={"X-Sync-Token": "valid"}, json={"text": "same chunk"})
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json(), second.json())
            bedrock_client.invoke_model.assert_called_once()


@unittest.skipIf(kb.faiss is None or kb.np is None, "FAISS is unavailable")
class EmbeddingIndexCompatibilityTests(unittest.TestCase):
    def test_provider_change_rebuilds_index_before_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(
                app_mode="client", embedding_provider="bedrock", sync_server_url="https://example.test",
                sync_api_token="token", faiss_index_path=str(Path(temp_dir) / "knowledge.faiss"),
                _env_file=None,
            )
            service = kb.KnowledgeBaseService.__new__(kb.KnowledgeBaseService)
            service.scope = "main"
            service.settings = settings
            service.db = SimpleNamespace(execute=MagicMock(return_value=[]))
            old = kb.faiss.IndexIDMap2(kb.faiss.IndexFlatIP(2))
            kb.faiss.write_index(old, str(service._index_path))
            service._write_index_metadata({"version": 1, "provider": "ollama", "model": "nomic-embed-text"}, 2)
            service.rebuild_index = AsyncMock(return_value={"error": False})
            rebuilt = asyncio.run(service._rebuild_index_if_needed(EmbeddingVector([0.1, 0.2], "server-model")))
            self.assertTrue(rebuilt)
            service.rebuild_index.assert_awaited_once()

    def test_rebuild_includes_policy_documents_and_references_sharing_one_index(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=engine)
        db = sessionmaker(bind=engine, expire_on_commit=False)()
        try:
            for scope in ("policy_document", "policy_reference"):
                document = KnowledgeDocument(title=scope, source_type="txt", scope=scope)
                db.add(document)
                db.flush()
                db.add(KnowledgeChunk(document_id=document.id, chunk_index=0, content=scope, source_type="txt"))
            db.commit()
            with tempfile.TemporaryDirectory() as temp_dir:
                settings = Settings(
                    app_mode="client", embedding_provider="bedrock", sync_server_url="https://example.test",
                    sync_api_token="token", faiss_index_path=str(Path(temp_dir) / "knowledge.faiss"),
                    _env_file=None,
                )
                service = kb.KnowledgeBaseService(db, None, scope="policy_document")
                service.settings = settings
                old = kb.faiss.IndexIDMap2(kb.faiss.IndexFlatIP(2))
                service._save_index(old, {"version": 1, "provider": "ollama", "model": "nomic-embed-text"})
                embeddings = [EmbeddingVector([1.0, 0.0], "server-model"), EmbeddingVector([0.0, 1.0], "server-model")]
                with patch.object(service, "_embed_many", new=AsyncMock(return_value=embeddings)):
                    result = asyncio.run(service.rebuild_index())
                self.assertEqual(result["indexed_chunks"], 2)
                self.assertEqual(kb.faiss.read_index(str(service._index_path)).ntotal, 2)
                self.assertEqual(service._read_index_metadata()["model"], "server-model")
        finally:
            db.close()
            engine.dispose()
