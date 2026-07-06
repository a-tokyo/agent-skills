Staged diff summary:
- web/lib/session.ts (modified): session tokens are now stored in an httpOnly cookie instead of localStorage; the exported getToken() helper is removed
- web/components/AuthProvider.tsx (modified): reads the session from the new cookie-backed store
