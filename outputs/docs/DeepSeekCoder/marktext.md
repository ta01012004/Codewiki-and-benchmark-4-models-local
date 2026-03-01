# Overview
MarkText is a powerful Markdown editor that provides a clean, simple, and minimalistic interface. It is designed with a modular approach, allowing for easy customization and extension. This documentation provides an overview, architecture, data flow, configuration, and extension points of the MarkText repository.

# Architecture
```mermaid
graph TD;
    A[Root Module] --> B[src/main/index.js]
    A --> C[src/main/app/index.js]
    A --> D[src/main/cli/index.js]