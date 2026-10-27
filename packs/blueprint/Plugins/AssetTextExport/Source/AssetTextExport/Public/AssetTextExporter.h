#pragma once

#include "CoreMinimal.h"

DECLARE_LOG_CATEGORY_EXTERN(LogAssetTextExport, Log, All);

/** Writes one asset to a T3D text file. */
class ASSETTEXTEXPORT_API FAssetTextExporter
{
public:
	/** Export one asset.
	 *
	 *  AssetPath takes either form: the package path `/Game/A/B` or the object path `/Game/A/B.B`.
	 *  An empty OutDir writes to DefaultOutDir().
	 *  Returns true and fills OutFile with the absolute path; returns false and fills OutError. */
	static bool ExportAsset(const FString& AssetPath, const FString& OutDir,
	                        FString& OutFile, FString& OutError);

	/** Export one loaded object, as it is in memory right now. Same output as ExportAsset. */
	static bool ExportObject(UObject* Asset, const FString& OutDir,
	                         FString& OutFile, FString& OutError);

	/** Every Blueprint under a package directory, recursive. */
	static void FindBlueprintsUnder(const FString& PackageDir, TArray<FString>& OutPackagePaths);

	/** Every Blueprint the seed asset reaches, following what it references.
	 *
	 *  Level 0 is the seed. Level 1 is what the seed references, level 2 is what those reference, and
	 *  so on to Depth. A negative depth follows the whole graph. Only assets under /Game are
	 *  followed, so the sweep stops at the engine.
	 *
	 *  OutLevels, when given, receives the level of each path, in the same order. */
	static void FindBlueprintDependencies(const FString& SeedPath, int32 Depth,
	                                      TArray<FString>& OutPackagePaths,
	                                      TArray<int32>* OutLevels = nullptr);

	/** Every Blueprint that reaches the seed asset - the same sweep with the edges reversed. */
	static void FindBlueprintReferencers(const FString& SeedPath, int32 Depth,
	                                     TArray<FString>& OutPackagePaths,
	                                     TArray<int32>* OutLevels = nullptr);

	/** `/Game/A/B` -> `/Game/A/B.B`. An object path comes back unchanged. */
	static FString ToObjectPath(const FString& AssetPath);

	/** `<project>/T3D/`, absolute: where an empty OutDir writes. Never a directory under `Saved/`: the
	 *  editor mounts `Saved/` as the package root `/Temp/`. */
	static FString DefaultOutDir();

	/** `Text<AssetName>-<n>.txt` in Dir, with n the lowest integer that names no existing file. */
	static FString NextFreeName(const FString& Dir, const FString& AssetName);
};
