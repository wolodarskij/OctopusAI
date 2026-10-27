"""The T3D text dump of an asset, over MCP.

`UAssetTextExportLibrary` (C++) does the work. The live object goes straight to text: no package is
loaded, staged or written. Registered by `init_unreal.py` on an engine with the ToolsetRegistry
plugin (UE 5.8+); the toolset name is `asset_text_export.toolsets.text.AssetTextExportTools`.
"""

import unreal

import toolset_registry


@unreal.uclass()
class AssetTextExportTools(unreal.ToolsetDefinition):
    """T3D text export and Blueprint summaries. One call, one file, no package staged."""

    @toolset_registry.tool_call
    @staticmethod
    def export_asset_text(asset_path: str, out_dir: str | None = None) -> str:
        """Dumps the asset AS IT IS NOW to the engine's diff text form: a T3D object tree with graph
        nodes, pins and instanced subobjects.

        Args:
            asset_path: Package or object path, e.g. "/Game/Blueprints/BP_Door".
            out_dir: Absolute directory. Empty or omitted writes to the project's T3D/.

        Returns:
            Absolute path of the written Text<Asset>-<n>.txt (UTF-8 with BOM).
        """
        path = unreal.AssetTextExportLibrary.export_asset_path_to_text(asset_path, out_dir or "")
        if not path:
            raise RuntimeError(f"Text export failed for '{asset_path}': "
                               f"{unreal.AssetTextExportLibrary.last_error()}")
        return path

    @toolset_registry.tool_call
    @staticmethod
    def export_blueprints_under(package_dir: str, out_dir: str | None = None) -> list[str]:
        """Dumps every Blueprint under a package directory, recursive.

        Args:
            package_dir: e.g. "/Game/Blueprints".
            out_dir: Absolute directory. Empty or omitted writes to the project's T3D/.

        Returns:
            One "<object path>\\t<file>" line per asset; a failed asset reads "<object path>\\t!<error>".
        """
        return list(unreal.AssetTextExportLibrary.export_blueprints_under(package_dir, out_dir or ""))

    @toolset_registry.tool_call
    @staticmethod
    def write_blueprint_summary(asset_path: str, out_dir: str | None = None, bodies: bool | None = None) -> str:
        """Writes what a Blueprint declares - variables, functions, events, dispatchers, macros - as
        <AssetName>.json, with each body rendered as indented pseudocode when bodies is true.

        Args:
            asset_path: Package or object path of a Blueprint.
            out_dir: Absolute directory. Empty or omitted writes to the project's T3D/.
            bodies: Include the pseudocode bodies. Omitted means true.

        Returns:
            Absolute path of the written JSON.
        """
        path = unreal.AssetTextExportLibrary.write_blueprint_summary(asset_path, out_dir or "", bodies is not False)
        if not path:
            raise RuntimeError(f"Summary failed for '{asset_path}': "
                               f"{unreal.AssetTextExportLibrary.last_error()}")
        return path
