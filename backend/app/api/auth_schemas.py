from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmailRequest(StrictRequest):
    email: EmailStr = Field(max_length=254)

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value: str):
        return value.strip().lower()


class LoginRequest(EmailRequest):
    password: str = Field(min_length=1, max_length=128, repr=False)


def strong_password(value: str):
    if len(value) < 15 or value.lower() in {"password123456789", "123456789012345", "qwertyuiop123456", "thisisapassword"}:
        raise ValueError("Use a less predictable password with at least 15 characters")
    return value


class RegisterRequest(LoginRequest):
    name: str = Field(min_length=1, max_length=200)
    workspace_type: Literal["INDIVIDUAL", "FIRM"]
    firm_name: str | None = Field(default=None, max_length=200)
    phone_number: str | None = Field(default=None, pattern=r"^\+[1-9]\d{7,14}$", max_length=16)
    address_line1: str | None = Field(default=None, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9 -]{3,20}$", max_length=20)
    country_code: str = Field(default="IN", pattern=r"^[A-Z]{2}$", max_length=2)

    _password = field_validator("password")(strong_password)

    @field_validator('email')
    @classmethod
    def contact_email(cls, value):
        if str(value).split('@')[-1].endswith('.aligned.com'):
            raise ValueError('Use your real contact email; firm login IDs are generated separately')
        return value

    @field_validator("name", "firm_name")
    @classmethod
    def trim_name(cls, value):
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("Name must not be blank")
        return value

    @model_validator(mode="after")
    def firm_required(self):
        if self.workspace_type == "FIRM" and not self.firm_name:
            raise ValueError("Firm name is required")
        if self.workspace_type == "INDIVIDUAL" and self.firm_name is not None:
            raise ValueError("Firm name is only accepted for firms")
        return self


class SupabaseTokenRequest(StrictRequest):
    access_token: str = Field(min_length=20, max_length=8192, repr=False)


class SupabaseRegisterRequest(SupabaseTokenRequest):
    name: str = Field(min_length=1, max_length=200)
    workspace_type: Literal["INDIVIDUAL", "FIRM"]
    firm_name: str | None = Field(default=None, max_length=200)
    address_line1: str = Field(min_length=2, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    city: str = Field(min_length=2, max_length=120)
    state: str = Field(min_length=2, max_length=120)
    postal_code: str = Field(pattern=r"^[A-Za-z0-9 -]{3,20}$", max_length=20)
    country_code: str = Field(default="IN", pattern=r"^[A-Z]{2}$", max_length=2)

    @field_validator("name", "firm_name", "address_line1", "address_line2", "city", "state")
    @classmethod
    def trim_supabase_profile(cls, value):
        return value.strip() if value is not None else value

    @model_validator(mode="after")
    def complete_supabase_workspace(self):
        if self.workspace_type == "FIRM" and not self.firm_name:
            raise ValueError("Firm name is required")
        if self.workspace_type == "INDIVIDUAL" and self.firm_name is not None:
            raise ValueError("Firm name is only accepted for firms")
        return self


class ProfileUpdateRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr = Field(max_length=254)
    current_password: str | None = Field(default=None, min_length=1, max_length=128, repr=False)
    supabase_access_token: str | None = Field(default=None, min_length=20, max_length=8192, repr=False)


class TokenRequest(StrictRequest):
    token: str = Field(min_length=32, max_length=256, repr=False)


class ResetPasswordRequest(TokenRequest):
    password: str = Field(min_length=15, max_length=128, repr=False)
    _password = field_validator("password")(strong_password)


class DeleteAccountRequest(StrictRequest):
    password: str | None = Field(default=None, min_length=1, max_length=128, repr=False)
    supabase_access_token: str | None = Field(default=None, min_length=20, max_length=8192, repr=False)
    confirmation: Literal['DELETE MY ACCOUNT']

    @model_validator(mode="after")
    def reauthentication_required(self):
        if not self.password and not self.supabase_access_token:
            raise ValueError("Confirm with your password or Google sign-in")
        return self
