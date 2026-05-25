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
    H --> I["Select platform targets"]
    I --> J["Build linux/amd64 image"]
    I --> K["Build linux/arm64 image"]
    J --> L["Validate image platform and version"]
    K --> L
    L --> M{"Single platform?"}
    M -->|Yes| N["Tag optional alias"]
    M -->|No| O{"--push provided?"}
    N --> P{"--push provided?"}
    P -->|Yes| Q["Push image and alias"]
    P -->|No| R["Done"]
    O -->|Yes| S["Push arch images"]
    O -->|No| T["Keep local arch images"]
    S --> U["Create and push multi-arch manifests"]
    Q --> R
    T --> R
    U --> R
```

The Docker wrapper adds one outer step: build or reuse the `linux/amd64` builder
image, then run this workflow inside it. `PLATFORM=all` builds both supported
architecture images. Multi-arch manifests are created only when images are
pushed, because Docker manifests reference registry images.
