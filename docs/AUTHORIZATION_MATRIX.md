# Authorization matrix

Last verified: 2026-09-09

Glycofy uses an authenticated, account-scoped access model. User-owned records
are selected with the authenticated user ID; object-ID endpoints return `404`
for another user's records so they do not disclose whether an object exists.

| Surface | Read | Create/update | Delete/action | Cross-account result |
| --- | --- | --- | --- | --- |
| Account and export | Current user only | Current user only | Current user only | No user-ID selector is accepted |
| Preferences | Current user only | Current user only | N/A | Separate record per account |
| Energy targets | User + date | User + date | N/A | Same date creates/updates caller's record only |
| Meal plans | User + date | User + date | Lock/regenerate restricted to owner | `404` for another user's plan |
| Plan meals and feedback | Owner through parent plan | Owner through parent plan | Owner through parent plan | `404` for another user's meal |
| Activities | Current user's collection | Provider sync binds current user | N/A | Other users' rows absent from list and CSV |
| Planned training | Current user's collection | Current user | Owner only | `404` for another user's event |
| Grocery preferences | Current user's collection | Current user + ingredient key | N/A | Separate state per account |
| Grocery approvals and handoff | User + date range | User + date range | N/A | Other users' snapshots and URLs are absent |
| Weekly AI jobs | Current user or user + job ID | Current user | Retry/cancel restricted to owner | `404` for another user's job |
| Beta feedback and analytics | Current user submissions | Current user | N/A | No user-selected ownership fields |
| Operations endpoints | Configured administrators only | Administrators only | Administrators only | `404` for non-admin accounts |

Automated coverage lives in `tests/test_authorization_matrix.py`. It uses two
simultaneously authenticated clients over one database and verifies:

- unauthenticated requests are rejected;
- cross-account object reads and mutations are concealed;
- collection, CSV, insight, and latest-job responses exclude foreign data;
- identical dates and ingredient keys remain isolated by user;
- self-service exports do not contain another account's data; and
- operations endpoints are concealed from normal users.

Run the matrix with:

```bash
pytest -q tests/test_authorization_matrix.py
```
