# Authentication contract v1

All protected requests use the header `Authorization: Bearer <JWT>`. Query-string tokens are not accepted.

## Login

`POST /api/auth/login`

Request:

```json
{ "email": "local-account@example.test", "password": "<local-only-secret>" }
```

Successful response (`200`):

```json
{
  "message": "Login successful",
  "token": "<jwt>",
  "user": {
    "id": "1",
    "email": "local-account@example.test",
    "userType": "user",
    "createdAt": "2026-01-01T00:00:00.000Z"
  }
}
```

Missing fields return `400`. Unknown accounts and invalid passwords return the same `401` response so account existence is not disclosed.

## Current user

`GET /api/auth/me` returns `200` with `{ "user": <public-user> }`. It returns `401` when the header is missing or malformed, `403` when the JWT is invalid or expired, and `404` when the signed user no longer exists.

## Disabled account creation

`POST /api/auth/register`, `/send-code`, and `/verify-code` return `410` with:

```json
{
  "code": "registration_disabled",
  "message": "Public registration and email verification are disabled."
}
```

The service creates accounts only from the local `SEED_ADMIN_*` and `SEED_USER_*` environment pairs at startup. It does not use SMTP or print passwords/codes.
