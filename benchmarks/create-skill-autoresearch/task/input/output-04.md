refactor(api)!: emit camelCase-only keys in user payloads

- remove deprecated snake_case aliases from the user serializer
- update the OpenAPI user schema to camelCase-only keys

BREAKING CHANGE: user API responses no longer include snake_case aliases; clients must read camelCase keys.
