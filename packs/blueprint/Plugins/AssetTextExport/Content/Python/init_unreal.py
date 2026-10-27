"""Registers the plugin's MCP toolset when the engine has a ToolsetRegistry (UE 5.8+).

On an engine without it the import fails and nothing else happens: `UAssetTextExportLibrary` is C++
and stays callable from Blueprint, Python and AngelScript on every version.
"""

try:
    from asset_text_export import toolsets

    toolsets._registration.register()
except ImportError as error:
    print(f"AssetTextExport: MCP toolset not registered ({error}). "
          f"unreal.AssetTextExportLibrary remains callable from Python.")
