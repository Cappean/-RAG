"""MongoDB boundary mapping for the documented patient fields."""

from datetime import UTC, datetime, time

from app.modules.patient.schema import PatientInput, PatientView


def storage_fields(data: PatientInput) -> dict:
    fields = data.model_dump()
    if data.birthDate is not None:
        fields["birthDate"] = datetime.combine(data.birthDate, time.min, UTC)
    return fields


def patient_view(record: dict) -> dict:
    fields = {key: value for key, value in record.items() if key != "_id"}
    if isinstance(fields.get("birthDate"), datetime):
        fields["birthDate"] = fields["birthDate"].date()
    return PatientView(id=str(record["_id"]), **fields).model_dump(mode="json")
