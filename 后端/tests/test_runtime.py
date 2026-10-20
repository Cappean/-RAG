"""SDK request/response contracts, based on the observed Milvus 2.5 payload.

The actual SDK builds hybrid requests and RRF ranking configuration. Only the
network client is replaced, so these checks run without external service writes.
"""

import pytest
from bson import ObjectId
from bson.errors import InvalidId
from pymilvus import AnnSearchRequest, RRFRanker

from app.infrastructure.runtime import VectorStore

# Observed real server response shape: a declared VARCHAR primary key is exposed
# under its schema name, rather than the generic 'id' used by some SDK examples.
CHUNK_ID = "6ad64c1b643f4a840b2aac8f"
MILVUS_25_RESULT = [[{"chunkId": CHUNK_ID, "distance": 0.0163, "entity": {"chunkId": CHUNK_ID}}]]


class CapturingMilvusClient:
    def __init__(self):
        self.call = None

    def hybrid_search(self, collection, requests, ranker, **kwargs):
        self.call = collection, requests, ranker, kwargs
        return MILVUS_25_RESULT


def make_store():
    # Bypass __init__ solely to avoid constructing a network client. No adapter
    # behavior is stubbed: VectorStore.search and SDK requests are the real ones.
    store = object.__new__(VectorStore)
    store.collection = "isolated_test_collection"
    store.client = CapturingMilvusClient()
    return store


def test_milvus_25_named_primary_key_hybrid_contract_and_ready_filter():
    store = make_store()
    document_ids = [str(ObjectId()), str(ObjectId())]
    dense, sparse = [0.2, 0.4], {13: 0.8, 27: 0.3}
    result = store.search(dense, sparse, document_ids, limit=4)
    assert result == [CHUNK_ID]
    collection, requests, ranker, options = store.client.call
    assert collection == "isolated_test_collection"
    assert isinstance(ranker, RRFRanker)
    assert all(isinstance(request, AnnSearchRequest) for request in requests)
    assert {request.anns_field for request in requests} == {"denseVector", "sparseVector"}
    keyed = {request.anns_field: request for request in requests}
    assert keyed["denseVector"].data == [dense]
    assert keyed["sparseVector"].data == [sparse]
    for request in requests:
        assert request.param["metric_type"] == "IP"
        assert request.limit >= options["limit"]
        assert "documentId in" in request.expr
        assert all(identifier in request.expr for identifier in document_ids)
    assert options["limit"] == 4
    assert options["consistency_level"] == "Strong"
    assert "chunkId" in options["output_fields"]


def test_empty_ready_set_never_searches_unfiltered_collection():
    store = make_store()
    assert store.search([0.1], {3: 0.2}, [], limit=4) == []
    assert store.client.call is None


def test_document_ids_are_validated_before_becoming_milvus_expressions():
    store = make_store()
    with pytest.raises(InvalidId):
        store.search([0.1], {3: 0.2}, ['x" or true'], limit=4)
    assert store.client.call is None
