"""Explicit MongoDB mappings exclude internal identifiers from public payloads."""


def conversation_view(record: dict) -> dict:
    return {
        "id": str(record["_id"]),
        "ownerId": None,
        "patientId": str(record["patientId"]) if record.get("patientId") else None,
        "title": record["title"],
        "createdAt": record["createdAt"],
        "updatedAt": record["updatedAt"],
    }


def message_view(record: dict) -> dict:
    return {
        "id": str(record["_id"]),
        "conversationId": str(record["conversationId"]),
        "role": record["role"],
        "content": record["content"],
        "status": record["status"],
        "citations": record.get("citations", []),
        "createdAt": record["createdAt"],
        "updatedAt": record["updatedAt"],
    }
