## 2024-05-24 - [CORS Verification Bypass]
**Vulnerability:** Worker processed API requests to Supabase even when the `Origin` header was invalid or not in the allowed list, instead of rejecting the request.
**Learning:** Only setting the `Access-Control-Allow-Origin` header to an empty string does not prevent the server from processing the request. Browsers block the response, but SSRF, unwanted load, and side-effects on the backend can still occur.
**Prevention:** Always validate the `Origin` header (if present) and return an early HTTP error status (like `403 Forbidden`) to halt request processing entirely before forwarding or evaluating any data.
