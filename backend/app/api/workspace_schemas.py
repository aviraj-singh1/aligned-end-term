"""Explicit writable fields; never accept tenant IDs or actor/permission fields from forms."""
from typing import Literal
from uuid import UUID
from pydantic import Field, field_validator
from .auth_schemas import StrictRequest, EmailRequest, TokenRequest, strong_password


class ClientInput(StrictRequest):
    name: str = Field(min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, max_length=255)
    pan: str | None = Field(default=None, pattern=r'^[A-Z]{5}[0-9]{4}[A-Z]$')

    @field_validator('name', 'legal_name', 'pan', mode='before')
    @classmethod
    def clean(cls, value):
        return value.strip() if isinstance(value, str) else value


class ClientStatus(StrictRequest):
    status: Literal['ACTIVE', 'ARCHIVED']


class OwnershipRequest(StrictRequest):
    recipient_user_id: UUID


class WorkspaceResetRequest(StrictRequest):
    confirmation: str = Field(min_length=1, max_length=200)


class GSTINInput(StrictRequest):
    gstin: str = Field(pattern=r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]$')

    @field_validator('gstin', mode='before')
    @classmethod
    def clean(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


class GSTINStatus(StrictRequest):
    status: Literal['ACTIVE', 'INACTIVE']


class InviteInput(EmailRequest):
    role: Literal['ADMIN', 'ACCOUNTANT'] = 'ACCOUNTANT'


class ManagedAccountInput(InviteInput):
    name: str = Field(min_length=1, max_length=200)
    username: str = Field(pattern=r'^[a-z][a-z0-9.-]{1,39}$')

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError('Name must not be blank')
        return value.strip()


class MemberInput(StrictRequest):
    role: Literal['ADMIN', 'ACCOUNTANT']
    status: Literal['ACTIVE', 'SUSPENDED', 'REMOVED']


class FirmInput(StrictRequest):
    name: str = Field(min_length=1, max_length=200)

    @field_validator('name')
    @classmethod
    def clean(cls, value):
        if not value.strip():
            raise ValueError('Firm name must not be blank')
        return value.strip()


class InvitedRegistration(EmailRequest, TokenRequest):
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=15, max_length=128, repr=False)
    _password = field_validator('password')(strong_password)

    @field_validator('name')
    @classmethod
    def clean(cls, value):
        if not value.strip():
            raise ValueError('Name must not be blank')
        return value.strip()
