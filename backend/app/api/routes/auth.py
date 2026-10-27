from fastapi import APIRouter, Depends, Request, Response

from backend.app.api.auth_schemas import (DeleteAccountRequest, EmailRequest,
    LoginRequest, ProfileUpdateRequest, RegisterRequest, ResetPasswordRequest,
    SupabaseRegisterRequest, SupabaseTokenRequest, TokenRequest)
from backend.app.auth.config import auth_settings
from backend.app.auth.dependencies import current_user, require_signed_out
from backend.app.auth.security import issue_csrf, limit_auth, validate_csrf, validate_origin
from backend.app.db.models import User
from backend.app.services import auth_service

router = APIRouter()
mutation = APIRouter(dependencies=[Depends(validate_csrf)])
ACCEPTED = {"message": "If this address is eligible, verification instructions will be available in the local development mailbox. No internet email is sent in this local build."}


def _set_session_cookie(response: Response, settings, token: str):
    response.set_cookie(
        settings.cookie_name, token, max_age=settings.absolute_seconds,
        httponly=True, secure=settings.secure_cookies,
        samesite=settings.cookie_samesite, path="/",
    )


def _clear_auth_cookies(response: Response, settings):
    response.delete_cookie(settings.cookie_name, path="/", secure=settings.secure_cookies, samesite=settings.cookie_samesite)
    response.delete_cookie(settings.csrf_cookie_name, path="/", secure=settings.secure_cookies, samesite=settings.cookie_samesite)


@router.get("/auth/csrf")
def csrf(request: Request, response: Response):
    validate_origin(request)
    limit_auth(request)
    return {"csrf_token": issue_csrf(request, response), "local_mail": True}


@mutation.post("/auth/register", status_code=202)
def register(data: RegisterRequest, request: Request, response: Response):
    limit_auth(request, str(data.email))
    settings = auth_settings()
    token = auth_service.register(data, request.cookies.get(settings.cookie_name, ''))
    if token:
        _set_session_cookie(response, settings, token)
        response.delete_cookie(settings.csrf_cookie_name, path='/', secure=settings.secure_cookies, samesite=settings.cookie_samesite)
    return {**ACCEPTED, 'signed_in': bool(token)}


@mutation.post("/auth/login")
def login(data: LoginRequest, request: Request, response: Response):
    limit_auth(request, str(data.email))
    settings = auth_settings()
    token = auth_service.login(str(data.email), data.password, request.cookies.get(settings.cookie_name, ""))
    _set_session_cookie(response, settings, token)
    response.delete_cookie(settings.csrf_cookie_name, path="/", secure=settings.secure_cookies, samesite=settings.cookie_samesite)
    return {"message": "Signed in"}


@mutation.post("/auth/supabase/login")
def supabase_login(data: SupabaseTokenRequest, request: Request, response: Response):
    limit_auth(request)
    settings = auth_settings()
    token = auth_service.supabase_login(data.access_token, request.cookies.get(settings.cookie_name, ""))
    _set_session_cookie(response, settings, token)
    response.delete_cookie(settings.csrf_cookie_name, path="/", secure=settings.secure_cookies, samesite=settings.cookie_samesite)
    return {"message": "Signed in"}


@mutation.post("/auth/supabase/register", status_code=201)
def supabase_register(data: SupabaseRegisterRequest, request: Request, response: Response):
    limit_auth(request)
    settings = auth_settings()
    token = auth_service.register_supabase(data, request.cookies.get(settings.cookie_name, ""))
    _set_session_cookie(response, settings, token)
    response.delete_cookie(settings.csrf_cookie_name, path="/", secure=settings.secure_cookies, samesite=settings.cookie_samesite)
    return {"message": "Workspace created", "signed_in": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return auth_service.profile(user)


@mutation.patch("/auth/profile")
def update_profile(data: ProfileUpdateRequest, user: User = Depends(current_user)):
    return auth_service.update_profile(user, data.name, str(data.email), data.current_password,
                                       data.supabase_access_token)


@mutation.post("/auth/logout")
def logout(request: Request, response: Response):
    settings = auth_settings()
    auth_service.logout(request.cookies.get(settings.cookie_name, ""))
    _clear_auth_cookies(response, settings)
    return {"message": "Signed out"}


@mutation.post("/auth/logout-all")
def logout_all(response: Response, user: User = Depends(current_user)):
    settings = auth_settings()
    auth_service.logout("", user.id)
    _clear_auth_cookies(response, settings)
    return {"message": "Signed out on all devices"}


@mutation.delete('/auth/account')
def delete_account(data: DeleteAccountRequest, request: Request, response: Response,
                   user: User = Depends(current_user)):
    limit_auth(request, str(user.id))
    from backend.app.services.data_management_service import check_account_deletion, delete_account as remove
    supabase_identity = auth_service.supabase_reauthenticate(user, data.supabase_access_token)
    status = check_account_deletion(user, data.password, data.confirmation, bool(supabase_identity))
    if status['has_external_identity']:
        if not supabase_identity:
            raise RuntimeError("Supabase identity validation was required but not returned")
        auth_service.delete_supabase_identity(supabase_identity)
    result = remove(user, data.password, data.confirmation, bool(supabase_identity))
    settings = auth_settings()
    _clear_auth_cookies(response, settings)
    return result


@mutation.post("/auth/verification/request", status_code=202)
def request_verification(data: EmailRequest, request: Request):
    limit_auth(request, str(data.email))
    auth_service.request_email(str(data.email), "VERIFY_EMAIL")
    return ACCEPTED


@mutation.post('/auth/verification/resend', status_code=202)
def resend_current(request: Request, user: User = Depends(current_user)):
    limit_auth(request,str(user.id))
    auth_service.request_email(user.email_normalized,'VERIFY_EMAIL')
    return {**ACCEPTED, 'address':user.email}


@mutation.post("/auth/verification/confirm")
def confirm_verification(data: TokenRequest, request: Request):
    limit_auth(request)
    auth_service.consume_action(data.token, "VERIFY_EMAIL")
    return {"message": "Email verified. You can now sign in."}


@mutation.post("/auth/password/forgot", status_code=202)
def forgot(data: EmailRequest, request: Request):
    limit_auth(request, str(data.email))
    auth_service.request_email(str(data.email), "RESET_PASSWORD")
    return {"message": "If this address is eligible, reset instructions will be available in the local development mailbox. No internet email is sent in this local build."}


@mutation.post("/auth/password/reset")
def reset(data: ResetPasswordRequest, request: Request):
    limit_auth(request)
    auth_service.consume_action(data.token, "RESET_PASSWORD", data.password)
    return {"message": "Password changed. Sign in again with your new password."}


@mutation.post('/auth/account/activate', dependencies=[Depends(require_signed_out)])
def activate(data: ResetPasswordRequest, request: Request):
    limit_auth(request)
    auth_service.consume_action(data.token, 'ACTIVATE_ACCOUNT', data.password)
    return {'message':'Account activated. Sign in with your firm login ID or contact email.'}


router.include_router(mutation)
