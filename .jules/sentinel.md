## 2024-05-24 - [CORS Verification Bypass]
**Vulnerability:** Worker sent API requests to Supabase even with invalid or disallowed `Origin`.
**Learning:** `Access-Control-Allow-Origin` alone does not stop request processing. Browsers block the response, but SSRF, backend load, and side effects still happen.
**Prevention:** Validate `Origin` when present. Return early HTTP error (`403 Forbidden`) before forwarding or evaluating data.
