#pragma once

#include "CoreMinimal.h"

/** Writes what a Blueprint declares — variables, functions, events, dispatchers, macros — as JSON. */
class ASSETTEXTEXPORT_API FBlueprintSummary
{
public:
	/** Read the asset and write `<AssetName>.json` into OutDir.
	 *
	 *  AssetPath takes the package path `/Game/A/B` or the object path `/Game/A/B.B`.
	 *  An empty OutDir writes to FAssetTextExporter::DefaultOutDir(), `<project>/T3D/`.
	 *  Returns true and fills OutFile; returns false and fills OutError. */
	static bool WriteSummary(const FString& AssetPath, const FString& OutDir,
	                         bool bBodies, FString& OutFile, FString& OutError);

	/** The summary as a JSON string, for a caller that writes the file itself. */
	static bool BuildSummary(const FString& AssetPath, bool bBodies,
	                         FString& OutJson, FString& OutError);
};
