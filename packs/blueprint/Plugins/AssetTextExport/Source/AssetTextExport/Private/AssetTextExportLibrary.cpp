#include "AssetTextExportLibrary.h"

#include "AssetTextExporter.h"
#include "BlueprintSummary.h"

namespace
{
	FString GLastError;
}

FString UAssetTextExportLibrary::ExportAssetToText(UObject* Asset, const FString& OutDir)
{
	FString File;
	if (!FAssetTextExporter::ExportObject(Asset, OutDir, File, GLastError))
	{
		UE_LOG(LogAssetTextExport, Error, TEXT("%s"), *GLastError);
		return FString();
	}
	GLastError.Empty();
	return File;
}

FString UAssetTextExportLibrary::ExportAssetPathToText(const FString& AssetPath, const FString& OutDir)
{
	FString File;
	if (!FAssetTextExporter::ExportAsset(AssetPath, OutDir, File, GLastError))
	{
		UE_LOG(LogAssetTextExport, Error, TEXT("%s: %s"), *AssetPath, *GLastError);
		return FString();
	}
	GLastError.Empty();
	return File;
}

TArray<FString> UAssetTextExportLibrary::ExportBlueprintsUnder(const FString& PackageDir, const FString& OutDir)
{
	TArray<FString> Assets;
	FAssetTextExporter::FindBlueprintsUnder(PackageDir, Assets);

	TArray<FString> Lines;
	for (const FString& Asset : Assets)
	{
		FString File;
		FString Error;
		if (FAssetTextExporter::ExportAsset(Asset, OutDir, File, Error))
		{
			Lines.Add(Asset + TEXT("\t") + File);
		}
		else
		{
			Lines.Add(Asset + TEXT("\t!") + Error);
		}
	}
	return Lines;
}

FString UAssetTextExportLibrary::WriteBlueprintSummary(const FString& AssetPath, const FString& OutDir, bool bBodies)
{
	FString File;
	if (!FBlueprintSummary::WriteSummary(AssetPath, OutDir, bBodies, File, GLastError))
	{
		UE_LOG(LogAssetTextExport, Error, TEXT("%s: %s"), *AssetPath, *GLastError);
		return FString();
	}
	GLastError.Empty();
	return File;
}

FString UAssetTextExportLibrary::LastError()
{
	return GLastError;
}
