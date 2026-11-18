def enqueue_ingestion(doc_id: str) -> None:
    """Send a doc to the Celery queue. Imported lazily so inline mode never needs Celery/Redis."""
    from app.workers.tasks import ingest_task

    ingest_task.delay(doc_id)
