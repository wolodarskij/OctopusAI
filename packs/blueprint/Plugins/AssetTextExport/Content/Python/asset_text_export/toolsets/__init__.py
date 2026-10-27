"""The plugin's MCP toolsets. Importing this module needs the engine's ToolsetRegistry plugin."""

from toolset_registry.registration import Registration

from asset_text_export.toolsets import text

_registration = Registration([
    text.AssetTextExportTools,
])
