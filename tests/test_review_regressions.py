from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from fastapi.testclient import TestClient

from app_factory import create_app
from backend.audit import JsonlAuditSink
from backend.rbac import ClaimAccessPolicy
from backend.tenant_context import PrincipalContext
from config import Settings


def test_explicit_empty_acl_denies_adjuster():
    principal = PrincipalContext('alice', 'tenant-a', frozenset({'adjuster'}))
    assert not ClaimAccessPolicy({}).can_access_claim(principal, 'unassigned')


def test_concurrent_audit_preserves_every_event(tmp_path):
    sink = JsonlAuditSink(tmp_path / 'audit.jsonl')
    def record(i):
        sink.record({'event': 'probe', 'request_id': str(i), 'tenant_id': 'test'})
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(record, range(100)))
    assert {e['request_id'] for e in sink.read_events()} == {str(i) for i in range(100)}


def local_settings(tmp_path, **kw):
    return replace(Settings(), rag_db_path=tmp_path / 'rag.db',
                   stored_documents_dir=tmp_path / 'docs', jobs_db_path=tmp_path / 'jobs.db',
                   audit_log_path=tmp_path / 'audit.jsonl', llm_provider='none', **kw)


def test_factory_serves_real_api_and_isolates_apps(tmp_path):
    first = create_app(local_settings(tmp_path))
    second = create_app(local_settings(tmp_path / 'second'))
    assert first.state.dependencies is not second.state.dependencies
    with TestClient(first) as client:
        assert client.get('/api/auth/me').status_code == 200


def test_disabled_simulation_never_answers():
    from backend.agentic_router import AgenticRAGRouter
    class Store:
        def search_similarity(self, *args, **kwargs):
            return []
    class Embedder:
        def embed_query(self, text):
            return [1., 0.]
    result = AgenticRAGRouter().run_query('OEM Parts Rider', '#2026-99382', 'simulated',
                                         Embedder(), Store(), caps=Settings(simulation_mode=False))
    assert result.get('status') == 'error'
    assert not result['sources']
    assert 'PASSED' not in result['answer']


class FakeEmbedder:
    def embed_chunks(self, chunks):
        return [[1.] + [0.] * 383 for _ in chunks]

    def embed_query(self, query):
        return [1.] + [0.] * 383


def test_claim_and_tenant_boundaries_cover_actual_files(tmp_path):
    from backend.authn import ServiceAccountAuthenticator
    application = create_app(local_settings(tmp_path, tenant_id='tenant-a'))
    rt = application.state.runtime
    rt._authenticator = ServiceAccountAuthenticator({
        'alice': {'subject': 'alice', 'tenant_id': 'tenant-a', 'roles': ['adjuster']},
        'outsider': {'subject': 'outsider', 'tenant_id': 'tenant-b', 'roles': ['admin']},
    })
    rt._claim_access_policy = ClaimAccessPolicy({'claim-a': ['alice'], 'claim-b': ['bob']})
    rt.vector_store.add_document('private.txt', 'txt', 7, 'PRIVATE', FakeEmbedder(), claim_id='claim-b')
    rt.vector_store.add_document('policy.txt', 'txt', 6, 'POLICY', FakeEmbedder())
    with TestClient(application) as client:
        for path in ('/api/documents/content/private.txt', '/api/documents/download/private.txt'):
            assert client.get(path, headers={'X-API-Key': 'alice'}).status_code == 403
        assert client.get('/api/documents/content/policy.txt', headers={'X-API-Key': 'alice'}).status_code == 200
        for path in ('/api/documents', '/api/jobs/unknown', '/api/auth/me'):
            assert client.get(path, headers={'X-API-Key': 'outsider'}).status_code == 403


def test_factory_async_upload_indexes_and_stops_worker(tmp_path):
    import time
    application = create_app(local_settings(tmp_path, ingestion_mode='async', worker_poll_interval_seconds=.01))
    rt = application.state.runtime
    rt._get_embedding_engine = FakeEmbedder
    assert rt.ingestion_service._queue is rt._ingestion_worker.queue
    with TestClient(application) as client:
        response = client.post('/api/upload', files={'file': ('queued.txt', b'QUEUE EVIDENCE', 'text/plain')})
        assert response.status_code == 202
        for _ in range(100):
            job = client.get('/api/jobs/' + response.json()['job_id']).json()
            if job['status'] == 'indexed':
                break
            time.sleep(.02)
        assert job['status'] == 'indexed'
        assert client.get('/api/documents/download/queued.txt').content == b'QUEUE EVIDENCE'
    assert rt._ingestion_worker._thread is None


def test_replaced_citation_rejected_and_blob_failure_preserves_index(tmp_path):
    from backend.source_storage import source_store
    application = create_app(local_settings(tmp_path))
    rt = application.state.runtime
    store = rt.vector_store
    store.add_document('policy.txt', 'txt', 3, 'OLD', FakeEmbedder())
    old = store.get_document_metadata('policy.txt')
    store.add_document('policy.txt', 'txt', 3, 'NEW', FakeEmbedder())
    with TestClient(application) as client:
        assert client.get('/api/documents/download/policy.txt', params={'version': old['document_version']}).status_code == 409
        assert client.get('/api/documents/download/policy.txt').content == b'NEW'
    class BrokenBlob:
        def put(self, *args):
            raise OSError('synthetic outage')
    store.blob_store = BrokenBlob()
    import pytest
    with pytest.raises(OSError):
        store.add_document('policy.txt', 'txt', 6, 'FAILED', FakeEmbedder())
    store.blob_store = None
    assert store.get_document_content('policy.txt') == 'NEW'
    assert source_store(store).get(store.get_blob_key('policy.txt')) == b'NEW'


def test_chat_and_extraction_limits_apply_before_embedding(tmp_path):
    application = create_app(local_settings(tmp_path, max_query_chars=10, max_document_chars=5))
    rt = application.state.runtime
    def never_embed():
        raise AssertionError('limits must run before embedding')
    rt._get_embedding_engine = never_embed
    with TestClient(application) as client:
        for route in ('/api/chat', '/api/chat/stream'):
            assert client.post(route, json={'query': 'x' * 11, 'engine': 'simulated'}).status_code == 413
        assert client.post('/api/upload', files={'file': ('large.txt', b'x' * 6)}).status_code == 413


def test_postgres_selected_by_actual_factory(tmp_path, monkeypatch):
    from backend import postgres_store
    from backend.rag_engine import SQLiteVectorStore
    calls = []
    def fake_postgres(**kwargs):
        calls.append(kwargs)
        return SQLiteVectorStore(str(tmp_path / 'stub.db'), str(tmp_path / 'docs'))
    monkeypatch.setattr(postgres_store, 'PostgresVectorStore', fake_postgres)
    application = create_app(local_settings(tmp_path, vector_store='postgres', postgres_dsn='postgresql://synthetic', tenant_id='configured'))
    assert calls[0]['tenant_id'] == 'configured'
    with TestClient(application) as client:
        assert client.get('/api/documents').status_code == 200


def _process_audit_write(args):
    path, first = args
    sink = JsonlAuditSink(path)
    for i in range(first, first + 20):
        sink.record({'event': 'probe', 'request_id': str(i), 'tenant_id': 'test'})


def test_audit_preserves_events_across_processes(tmp_path):
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing
    path = str(tmp_path / 'process-audit.jsonl')
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
        list(pool.map(_process_audit_write, [(path, i * 20) for i in range(4)]))
    assert len(JsonlAuditSink(path).read_events()) == 80


def test_post_cap_empty_context_never_reaches_synthesis():
    from backend.agentic_router import AgenticRAGRouter
    class Model:
        def models(self):
            return ['test']
        def model_for_stage(self, stage):
            return 'test'
        def complete(self, messages, **kwargs):
            assert kwargs.get('stage') == 'planning', 'ungrounded synthesis attempted'
            return '{"needs_global_policies":true,"sub_queries":["test"]}'
    class Store:
        def search_similarity(self, *args, **kwargs):
            return [{'filename': 'test.txt', 'content': 'evidence', 'score': 1, 'file_type': 'txt'}]
    result = AgenticRAGRouter().run_query('test', None, 'lm-studio', FakeEmbedder(), Store(),
                                         llm_client=Model(), caps=Settings(context_max_prompt_chars=1))
    assert result['status'] == 'refused'
    assert result['sources'] == []


def test_expanded_archive_and_parser_timeout_are_bounded(tmp_path, monkeypatch):
    import subprocess
    import zipfile
    import pytest
    from backend.document_limits import DocumentLimitError, parse_bounded
    archive = tmp_path / 'bomb.docx'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr('word/document.xml', 'x' * 10000)
    with pytest.raises(DocumentLimitError, match='expanded'):
        parse_bounded(archive, 'docx', 10000, 100, 1)
    text = tmp_path / 'small.txt'
    text.write_text('small')
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)
    monkeypatch.setattr(subprocess, 'run', timeout)
    with pytest.raises(DocumentLimitError, match='time limit'):
        parse_bounded(text, 'pdf', 10000, 100, 1)


def test_audit_recovers_torn_tail_without_losing_complete_records(tmp_path):
    path = tmp_path / 'torn.jsonl'
    sink = JsonlAuditSink(path)
    sink.record({'event': 'complete', 'request_id': '1', 'tenant_id': 'test'})
    with path.open('ab') as output:
        output.write(b'{"event":"unfinished')
    sink.record({'event': 'after', 'request_id': '2', 'tenant_id': 'test'})
    assert [event['event'] for event in sink.read_events()] == ['complete', 'after']
    assert sink.read_events()[-1]['recovered_incomplete_tail']


def test_garbage_collection_preserves_current_source(tmp_path):
    from scripts.collect_source_garbage import collect
    application = create_app(local_settings(tmp_path))
    store = application.state.runtime.vector_store
    store.add_document('policy.txt', 'txt', 3, 'OLD', FakeEmbedder())
    old_key = store.get_blob_key('policy.txt')
    store.add_document('policy.txt', 'txt', 3, 'NEW', FakeEmbedder())
    assert collect(store) == [old_key]
    assert collect(store, apply=True) == [old_key]
    assert collect(store) == []
    assert store.get_document_content('policy.txt') == 'NEW'
