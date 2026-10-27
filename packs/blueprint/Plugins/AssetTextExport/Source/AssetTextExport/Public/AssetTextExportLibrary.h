#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "AssetTextExportLibrary.generated.h"

/** The T3D text dump of an asset, reachable from Blueprint, Python, AngelScript and an MCP toolset.
 *
 *  Nothing here loads, stages or writes a package. The live object goes straight to text, and a
 *  `.txt` is not a package, so no other package can import it. The editor mounts `Saved/` as the
 *  package root `/Temp/`; never add a route that writes a `.uasset` under `Saved/`. */
UCLASS()
class ASSETTEXTEXPORT_API UAssetTextExportLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/** Dumps a loaded asset AS IT IS NOW to the engine's diff text form: a T3D object tree with
	 *  graph nodes, pins and instanced subobjects.
	 *
	 *  @param Asset   Any loaded object. A Blueprint asset dumps its whole graph set.
	 *  @param OutDir  Absolute directory. Empty writes to the project's `T3D/`.
	 *  @return Absolute path of the written `Text<Asset>-<n>.txt`, empty on failure. */
	UFUNCTION(BlueprintCallable, Category = "Asset Text Export")
	static FString ExportAssetToText(UObject* Asset, const FString& OutDir);

	/** Loads the asset at a package or object path and dumps it.
	 *
	 *  @param AssetPath  `/Game/A/B` or `/Game/A/B.B`.
	 *  @param OutDir     Absolute directory. Empty writes to the project's `T3D/`.
	 *  @return Absolute path of the written text file, empty on failure. */
	UFUNCTION(BlueprintCallable, Category = "Asset Text Export")
	static FString ExportAssetPathToText(const FString& AssetPath, const FString& OutDir);

	/** Dumps every Blueprint under a package directory, recursive.
	 *
	 *  @return One line per asset: `<object path>\t<written file>` on success, `<object path>\t!<error>`
	 *          on failure. */
	UFUNCTION(BlueprintCallable, Category = "Asset Text Export")
	static TArray<FString> ExportBlueprintsUnder(const FString& PackageDir, const FString& OutDir);

	/** Writes what a Blueprint declares - variables, functions, events, dispatchers, macros - as
	 *  `<AssetName>.json`, with each body as indented pseudocode when bBodies is true.
	 *
	 *  @return Absolute path of the written JSON, empty on failure. */
	UFUNCTION(BlueprintCallable, Category = "Asset Text Export")
	static FString WriteBlueprintSummary(const FString& AssetPath, const FString& OutDir, bool bBodies);

	/** The error text of the last call on this thread that returned empty. */
	UFUNCTION(BlueprintPure, Category = "Asset Text Export")
	static FString LastError();
};
