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

`GET /api/auth/me` returns `200` with `{ "user": <public-user> }`. It returns `401` when the header is missing or malformed, `403` when the JWT is invalid or expired, and `401` with `code: "token_revoked"` when the account no longer exists or its stored email or role differs from the token. Every protected request reloads the account and applies this same identity check, so changing a user's email or role invalidates existing tokens, including prior admin tokens.

`PUT /api/users/:id` requires at least one supported field (`email`, `password`, or `userType`). Missing, empty, malformed, or unsupported update fields return `400` with `code: "invalid_user_update"`. An email already assigned to another account after trimming and case normalization returns `400` with `code: "email_already_in_use"`.

## Disabled account creation

`POST /api/auth/register`, `/send-code`, and `/verify-code` return `410` with:

```json
{
  "code": "registration_disabled",
  "message": "Public registration and email verification are disabled."
}
```

The service creates accounts only from the local `SEED_ADMIN_*` and `SEED_USER_*` environment pairs at startup. It does not use SMTP or print passwords/codes.
