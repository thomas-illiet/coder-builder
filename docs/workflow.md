# Build Workflow

```mermaid
flowchart TD
    A["Start build"] --> B["Resolve Coder ref"]
    B --> C["Fetch coder/coder cache"]
    C --> D["Create isolated worktree"]
    D --> E["Apply path overrides"]
    E --> F["Read go.mod and install Go"]
    F --> G["Download Go modules"]
    G --> H["Mark generated files fresh"]
    H --> I["Build linux/amd64 Docker image"]
    I --> J["Build embedded slim binaries: linux_amd64, darwin_arm64"]
    J --> K["Validate image platform and version"]
    K --> L["Smoke-run /healthz"]
    L --> M["Push amd64 image and latest alias"]
```

The Docker wrapper adds one outer step: build or reuse the `linux/amd64` builder
image, then run this workflow inside it. GitHub Actions builds exactly one
Docker image platform, `linux/amd64`, and limits upstream's embedded slim
binary archive to `linux_amd64,darwin_arm64`.

In GitHub Actions, the `linux/amd64` image also goes through a runtime smoke
test before it is pushed: the workflow starts Coder with temporary Postgres and
waits for `/healthz` to return `OK`.
