Staged diff summary:
- api/serializers/user.ts (modified): removes the deprecated snake_case aliases from the user payload; responses now emit camelCase keys only
- api/docs/openapi.yaml (modified): updates the user schema to camelCase-only keys
