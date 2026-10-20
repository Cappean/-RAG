"""Validated patient HTTP contracts; date-only input never derives from age."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PatientInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    gender: str | None = Field(default=None, max_length=40)
    birthDate: date | None = None
    allergies: list[str] = Field(default_factory=list, max_length=100)
    medicalHistory: list[str] = Field(default_factory=list, max_length=100)
    medications: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("birthDate")
    @classmethod
    def valid_birth_date(cls, value):
        if value is not None and value > date.today():
            raise ValueError("birthDate cannot be in the future")
        return value

    @field_validator("allergies", "medicalHistory", "medications")
    @classmethod
    def valid_entries(cls, values):
        if any(not value.strip() or len(value) > 2000 for value in values):
            raise ValueError("entries must contain 1 to 2000 characters")
        return [value.strip() for value in values]


class PatientView(PatientInput):
    id: str
    ownerId: None = None
    createdAt: datetime
    updatedAt: datetime
