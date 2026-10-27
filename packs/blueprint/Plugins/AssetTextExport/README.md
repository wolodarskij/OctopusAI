# AssetTextExport

Dumps any asset to the engine's own T3D text form: a Blueprint with every graph, node, pin,
literal and instanced subobject. One source builds on UE 4.25 through 5.8. The text has the grammar
`IAssetTools::DumpAssetToTempFile` and the editor's `diff` command produce, so the same parsers
read every version (`tools/t3d_graphs.py`, `tools/t3d_node_chain.py`, `tools/link_diff.py`).

The plugin loads, stages and writes no package. The live object goes straight to text, and a `.txt`
is not a package, so no other package can import it.

## Five ways in

| Route | Call | Needs |
|---|---|---|
| commandlet, no editor session | `UnrealEditor-Cmd.exe <project>.uproject -run=AssetTextExport -Assets=/Game/A,/Game/B -Out=D:\t3d -stdout -unattended -nopause -nosplash` (`UE4Editor-Cmd.exe` on 4.x) | the plugin enabled |
| editor console | `AssetTextExport.Asset /Game/Path/Asset [OutDir]`, `.Dir`, `.Summary`, `.SummaryDir`, `.SummaryDeps` | the plugin enabled |
| Blueprint / AngelScript | `UAssetTextExportLibrary::ExportAssetToText(UObject Asset, FString OutDir) -> FString` and the three others below | the plugin enabled |
| Python | `unreal.AssetTextExportLibrary.export_asset_path_to_text("/Game/A/B", "")` | PythonScriptPlugin |
| MCP toolset | `asset_text_export.toolsets.text.AssetTextExportTools.export_asset_text(asset_path, out_dir="")`, `export_blueprints_under`, `write_blueprint_summary` | UE 5.8: Epic's `ModelContextProtocol` and `ToolsetRegistry` engine plugins enabled. Registered by `Content/Python/init_unreal.py`; on an engine without `ToolsetRegistry` the registration is skipped and logged |

`UAssetTextExportLibrary` (`Source/AssetTextExport/Public/AssetTextExportLibrary.h`):

| Function | Returns |
|---|---|
| `ExportAssetToText(UObject* Asset, const FString& OutDir) -> FString` | absolute path of `Text<Asset>-<n>.txt`, empty on failure |
| `ExportAssetPathToText(const FString& AssetPath, const FString& OutDir) -> FString` | same, for `/Game/A/B` or `/Game/A/B.B` |
| `ExportBlueprintsUnder(const FString& PackageDir, const FString& OutDir) -> TArray<FString>` | one `<object path>\t<file>` line per asset, `\t!<error>` on failure |
| `WriteBlueprintSummary(const FString& AssetPath, const FString& OutDir, bool bBodies) -> FString` | absolute path of `<AssetName>.json` |
| `LastError() -> FString` | the error behind the last empty return |

An empty `OutDir` writes to the project's `T3D/`, never under `Saved/`. `<n>` is the lowest integer
that names no existing file in the output directory.

## The commandlet switches

| Switch | Meaning |
|---|---|
| `-Assets=/Game/A,/Game/B` | package paths, comma separated |
| `-AssetList=<file>` | one package path per line; `#` starts a comment |
| `-Dir=/Game/Blueprints` | every Blueprint under the path, recursive |
| `-Deps=/Game/A/B` | that Blueprint and every Blueprint it reaches |
| `-Refs=/Game/A/B` | every Blueprint that reaches it |
| `-Depth=<n>` | how far `-Deps` / `-Refs` follow; default the whole graph |
| `-Out=<dir>` | output directory; default `<project>/T3D/` |
| `-Summary` | also write `<AssetName>.json` |
| `-NoT3D` | with `-Summary`: the JSON alone |
| `-NoBodies` | headers only in the JSON |

The exit code is the number of assets that failed. `-Deps` and `-Refs` also write `_levels.tsv`
(seed, then `level<TAB>asset` per line).

## The summary JSON

What the Blueprint declares (variables with CDO defaults and flags, functions with resolved
signatures, purity, net mode and the interface that declares them, events split into `override`,
`interface` and `custom`, dispatchers, macros),
and each body as indented pseudocode:

```
Branch
  Condition = Get AI(self: <AddedItemToInventory>.po)
  then:
    Finish Turn
      NewParam = <AddedItemToInventory>.po
  else:
    (nothing)
```

A line at the margin is one statement; `Name = value` under it is one input; `Title(a: x)` is a
pure node printed inline; `<Title>.Pin` is an output of an earlier statement; `goto <Title>` is a
chain that came back to a printed statement. Project macros are rendered inline, bound to the
instance's arguments; engine `StandardMacros` are not.

## Building

```
powershell -File Build\build-plugin.ps1 -Engine "C:\Program Files\Epic Games\UE_5.8" -Out D:\out\5.8
powershell -File Build\build-plugin.ps1 -Engine "C:\Program Files\Epic Games\UE_4.25" -Out D:\out\4.25 -Compiler VisualStudio2019
```

For a 4.x engine the script swaps the machine-global `BuildConfiguration.xml` (a UE5 install writes
a `Compiler` value 4.x's UnrealBuildTool cannot parse) and restores it in a `finally` block. Build
results per engine are in `../../ENGINE-VERSIONS.md`.

## Two things to know before you change this plugin

- **The module loads at `PreLoadingScreen`.** `-run=` looks the commandlet class up with
  `FindObject` before the `Default` phase, and a later-loading module answers *"looked like a
  commandlet, but we could not find the class"*.
- **The commandlet class is `UAssetTextExportCommandlet`.** The engine appends `Commandlet` to
  the `-run=` token before the lookup.

## Encoding

Always UTF-8 with a BOM. The engine's own dump picks UTF-16LE when the text holds a non-ANSI
character, which leaves a directory of mixed files that a byte-oriented search reads wrong.

## Version gates

`Misc/EngineVersionComparison.h` and `UE_VERSION_OLDER_THAN` gate the six places 4.25 and 5.8
disagree: `FAssetData::AssetClass` → `AssetClassPath`, `ObjectPath` → `GetObjectPathString()`,
`EAssetRegistryDependencyType` → `UE::AssetRegistry::EDependencyCategory`,
`ExportTextItem` → `ExportTextItem_Direct`, the `AssetRegistryModule.h` include path, and the
`FStringOutputDevice` include. `ANY_PACKAGE` is avoided through `FAssetData::GetClass()`.

## Lineage

An Unreal 4.25 export plugin contributed the commandlet, the console commands, the summary and
the pseudocode renderer. A 5.8 function library contributed the `UFUNCTION` library and the MCP
toolset. This plugin is the union, renamed and gated.
