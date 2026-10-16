"""The public JSON API, /api/v1/ (docs at /api/, OpenAPI at /api/v1/openapi.json).

- core.py: endpoint registry, problem+json errors, token auth, rate limits, CORS, ETags, cursor pagination
- endpoints.py: the endpoints (reads with the site's visibility, writes through the Action classes)
- data.py / schemas.py: the objects returned and their JSON Schemas
- openapi.py: the OpenAPI 3.1 document, generated from the registry
- urls.py: the URL patterns, generated from the registry

Personal access tokens are forum.models.ApiToken (page: forum/views/api.py).
"""
