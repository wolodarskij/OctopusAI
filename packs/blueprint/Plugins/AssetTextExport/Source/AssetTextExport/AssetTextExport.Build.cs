using UnrealBuildTool;

public class AssetTextExport : ModuleRules
{
	public AssetTextExport(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[] { "Core" });

		// UExporter::ExportToOutputDevice and FExportObjectInnerContext are ENGINE_API, so the export
		// needs no UnrealEd and no AssetTools. That is what keeps one source building from 4.25 to
		// 5.8. AssetRegistry serves the directory and dependency sweeps; Json serves the summary.
		PrivateDependencyModuleNames.AddRange(new string[] {
			"CoreUObject", "Engine", "Projects", "AssetRegistry", "Json" });
	}
}
