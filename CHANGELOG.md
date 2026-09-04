## 0.1.0 (2026-09-04)

### Feat

- expose rsgi_version on scope types (RSGI 1.6)
- typed RSGI toolkit wrapping granian

### Fix

- **sse**: frame every line of multi-line string payloads
- **testing**: read chunked ASGI bodies, complete lifespan, propagate headers
- reject websocket scopes via protocol close, mark authority optional
- correct request validation, route matching, and OpenAPI generation
