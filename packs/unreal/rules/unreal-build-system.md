# UE Build System

*Applies to: `*.Build.cs`, `*.Target.cs`, `*.uproject`, `*.uplugin`.*

## Module Structure

Each module has: `ModuleName.Build.cs`, `Public/`, `Private/`, and optionally `Classes/`.

### .Build.cs

```csharp
public class MyGame : ModuleRules
{
    public MyGame(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new string[] {
            "Core", "CoreUObject", "Engine", "InputCore"
        });

        PrivateDependencyModuleNames.AddRange(new string[] {
            "Slate", "SlateCore"
        });

        if (Target.bBuildEditor)
        {
            // Editor-only modules do not exist in a cooked build.
            PrivateDependencyModuleNames.Add("UnrealEd");
        }
    }
}
```

### .Target.cs

```csharp
public class MyGameTarget : TargetRules
{
    public MyGameTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Game;
        DefaultBuildSettings = BuildSettingsVersion.V5;
        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
        ExtraModuleNames.Add("MyGame");
    }
}
```

## Adding Dependencies

- `PublicDependencyModuleNames`: modules that depend on YOUR module also get these.
- `PrivateDependencyModuleNames`: only YOUR module gets these. Prefer Private.

## Plugin Structure

A plugin has a `.uplugin` descriptor and one or more modules in `Source/`. An `Editor` module with
`LoadingPhase: PreLoadingScreen` is required for a commandlet class that `-run=` must find
(the `AssetTextExport` plugin in `{{OCTOPUS}}/packs/blueprint/Plugins/` is a worked example).

## Building

```
powershell -ExecutionPolicy Bypass -File "{{OCTOPUS}}/packs/unreal/tools/build_editor.ps1" -ProjectDir "{{PROJECT}}"
```

It runs `<EngineRoot>\Engine\Build\BatchFiles\Build.bat <Project>Editor Win64 Development
-project="{{PROJECT}}/<Project>.uproject" -WaitMutex`. `<EngineRoot>` is the engine that
`.octopus/unreal.json` names, and `<Project>` is the name of the `.uproject` file. For example,
`MyGameEditor` is the editor target of `MyGame.uproject`. A full build cannot run while the editor
has the project open: the script then builds nothing, keeps `.octopus/build-pending`, and exits 3.

A plugin on its own, against an installed engine:

```
<EngineRoot>\Engine\Build\BatchFiles\RunUAT.bat BuildPlugin -Plugin=<path>.uplugin -Package=<out> -TargetPlatforms=Win64 -Rocket
```

`{{OCTOPUS}}/packs/blueprint/Plugins/AssetTextExport/Build/build-plugin.ps1` wraps that for 4.x and 5.x engines on one machine.

## Version gates in one source

`#include "Misc/EngineVersionComparison.h"` and `UE_VERSION_OLDER_THAN(5, 1, 0)` exist from 4.25
on. The gates a 4.25-to-5.8 plugin needs: `FAssetData::AssetClass` → `AssetClassPath` and
`ObjectPath` → `GetObjectPathString()` (5.1), `EAssetRegistryDependencyType` →
`UE::AssetRegistry::EDependencyCategory` (4.26), `FProperty::ExportTextItem` →
`ExportTextItem_Direct` (5.1), `AssetRegistryModule.h` → `AssetRegistry/AssetRegistryModule.h`
(5.0), the `bAllowShrinking` bool arguments (drop them), `WhitelistPlatforms` → `PlatformAllowList`
in `.uplugin` (drop the list). `ANY_PACKAGE` is gone in 5.1; use `FAssetData::GetClass()`.
