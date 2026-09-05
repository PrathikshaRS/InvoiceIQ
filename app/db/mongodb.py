"""
MongoDB connection setup.

MongoClient manages its own internal connection pool, so we keep a single
shared client for the app's lifetime rather than opening/closing a
connection per request.
"""
from pymongo import MongoClient
from pymongo.collection import Collection

from app.core.config import settings

_client: MongoClient | None = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(settings.mongodb_uri)
    return _client


def get_invoices_collection() -> Collection:
    client = get_client()
    db = client[settings.mongodb_db_name]
    return db[settings.mongodb_collection_name]