# Supabase identity with MSG91 phone OTP

ALIGNED uses Supabase for Google/email identity and MSG91 OTP Widget for mobile verification. Supabase Phone Auth and the Supabase Send SMS hook remain disabled.

## Browser settings

- `VITE_MSG91_WIDGET_ID`: public OTP Widget identifier.
- `VITE_MSG91_WIDGET_TOKEN`: restricted OTP Widget client token. This is browser-visible by design; it is not the MSG91 account auth key.

## Backend setting

- `MSG91_AUTH_KEY`: secret account credential used only by FastAPI to call MSG91 `verifyAccessToken`.

## Trust flow

1. Supabase creates or authenticates the Google/email identity.
2. The MSG91 Web SDK sends and verifies the six-digit SMS OTP.
3. The browser sends the resulting MSG91 access token and Supabase access token to `/api/auth/msg91/verify-phone`.
4. FastAPI verifies both tokens server-side and returns a ten-minute, HMAC-signed phone receipt bound to the Supabase subject.
5. `/api/auth/supabase/register` accepts the workspace only when that receipt is valid.

The current MSG91 widget uses a 60-second resend interval and the provider-enforced minimum 15-minute OTP expiry. Never put `MSG91_AUTH_KEY` in a Vite variable.
