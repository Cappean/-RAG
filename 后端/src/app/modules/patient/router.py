"""HTTP-only profile routes; services own all business and storage behavior."""

from fastapi import APIRouter, Request

from app.modules.patient.schema import PatientInput, PatientView

router = APIRouter(prefix="/patients", tags=["patients"])


@router.get("", response_model=list[PatientView])
async def list_patients(request: Request):
    return await request.app.state.services.patient.list()


@router.get("/{patient_id}", response_model=PatientView)
async def get_patient(patient_id: str, request: Request):
    return await request.app.state.services.patient.get(patient_id)


@router.post("", response_model=PatientView, status_code=201)
async def create_patient(data: PatientInput, request: Request):
    return await request.app.state.services.patient.create(data)


@router.put("/{patient_id}", response_model=PatientView)
async def update_patient(patient_id: str, data: PatientInput, request: Request):
    return await request.app.state.services.patient.update(patient_id, data)
