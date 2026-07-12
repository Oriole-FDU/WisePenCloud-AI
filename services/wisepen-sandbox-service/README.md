# wisepen-sandbox-service

Platform-independent sandbox pool, scheduler, lease and watcher service.

This service depends only on the SandboxProvider and WorkspaceStore ports. A
platform adapter is injected by the application composition root.
