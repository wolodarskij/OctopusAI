#include "AssetTextExportCommandlet.h"

#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "BlueprintSummary.h"
#include "AssetTextExporter.h"

UAssetTextExportCommandlet::UAssetTextExportCommandlet()
{
	IsClient = false;
	IsServer = false;

	// IsEditor is what sets GIsEditor for the run. Without it the editor-only graph data does not
	// load and the dump carries no nodes.
	IsEditor = true;

	LogToConsole = true;

	// The exit code is the count of assets that failed to export. Loading a project whose Blueprints
	// carry compile errors would otherwise report failure for every run.
	ShowErrorCount = false;
}

int32 UAssetTextExportCommandlet::Main(const FString& Params)
{
	TArray<FString> Tokens;
	TArray<FString> Switches;
	TMap<FString, FString> Values;
	UCommandlet::ParseCommandLine(*Params, Tokens, Switches, Values);

	TArray<FString> Assets;

	// The sweeps fill these in step with Assets, so an index file can say how far from the seed each
	// Blueprint sits.
	TArray<int32> Levels;
	FString SeedName;

	if (const FString* List = Values.Find(TEXT("Assets")))
	{
		List->ParseIntoArray(Assets, TEXT(","), true);
	}

	if (const FString* ListFile = Values.Find(TEXT("AssetList")))
	{
		TArray<FString> Lines;
		if (!FFileHelper::LoadFileToStringArray(Lines, **ListFile))
		{
			UE_LOG(LogAssetTextExport, Error, TEXT("could not read '%s'"), **ListFile);
			return 1;
		}
		for (FString& Line : Lines)
		{
			Line.TrimStartAndEndInline();
			if (!Line.IsEmpty() && !Line.StartsWith(TEXT("#")))
			{
				Assets.Add(Line);
			}
		}
	}

	if (const FString* Dir = Values.Find(TEXT("Dir")))
	{
		FAssetTextExporter::FindBlueprintsUnder(*Dir, Assets);
	}

	// -Refs follows the package graph backwards: who reaches the seed.
	if (const FString* Seed = Values.Find(TEXT("Refs")))
	{
		int32 Depth = -1;
		if (const FString* DepthText = Values.Find(TEXT("Depth")))
		{
			Depth = FCString::Atoi(**DepthText);
		}

		SeedName = *Seed;
		FAssetTextExporter::FindBlueprintReferencers(*Seed, Depth, Assets, &Levels);
		UE_LOG(LogAssetTextExport, Display, TEXT("%d Blueprint(s) reach %s"), Assets.Num(), **Seed);
	}

	// -Deps follows what the seed asset references, and what those reference, to -Depth levels.
	if (const FString* Seed = Values.Find(TEXT("Deps")))
	{
		int32 Depth = -1;
		if (const FString* DepthText = Values.Find(TEXT("Depth")))
		{
			Depth = FCString::Atoi(**DepthText);
		}

		SeedName = *Seed;
		FAssetTextExporter::FindBlueprintDependencies(*Seed, Depth, Assets, &Levels);
		UE_LOG(LogAssetTextExport, Display, TEXT("%s reaches %d Blueprint(s)"), **Seed, Assets.Num());
	}

	if (Assets.Num() == 0)
	{
		UE_LOG(LogAssetTextExport, Error,
			TEXT("nothing to export. Give -Assets=/Game/A,/Game/B or -AssetList=<file> or -Dir=/Game/Blueprints"));
		return 1;
	}

	FString OutDir;
	if (const FString* Out = Values.Find(TEXT("Out")))
	{
		OutDir = FPaths::ConvertRelativePathToFull(*Out);
	}

	// -Summary writes the JSON of what each Blueprint declares. -NoT3D drops the text dump, so the
	// two switches together give the declarations alone.
	const bool bSummary = Switches.Contains(TEXT("Summary"));
	const bool bT3D = !Switches.Contains(TEXT("NoT3D"));
	const bool bBodies = !Switches.Contains(TEXT("NoBodies"));

	int32 Failed = 0;
	for (const FString& Asset : Assets)
	{
		FString File;
		FString Error;

		if (bT3D)
		{
			if (FAssetTextExporter::ExportAsset(Asset, OutDir, File, Error))
			{
				UE_LOG(LogAssetTextExport, Display, TEXT("%s -> %s"), *Asset, *File);
			}
			else
			{
				UE_LOG(LogAssetTextExport, Error, TEXT("%s: %s"), *Asset, *Error);
				++Failed;
				continue;
			}
		}

		if (bSummary)
		{
			if (FBlueprintSummary::WriteSummary(Asset, OutDir, bBodies, File, Error))
			{
				UE_LOG(LogAssetTextExport, Display, TEXT("%s -> %s"), *Asset, *File);
			}
			else
			{
				UE_LOG(LogAssetTextExport, Error, TEXT("%s: %s"), *Asset, *Error);
				++Failed;
			}
		}
	}

	if (Levels.Num() == Assets.Num() && Assets.Num() > 0)
	{
		FString Index = FString::Printf(TEXT("seed\t%s\nlevel\tasset\n"), *SeedName);
		for (int32 i = 0; i < Assets.Num(); ++i)
		{
			Index += FString::Printf(TEXT("%d\t%s\n"), Levels[i], *Assets[i]);
		}

		const FString Dir = OutDir.IsEmpty() ? FAssetTextExporter::DefaultOutDir() : OutDir;
		const FString Path = FPaths::Combine(Dir, TEXT("_levels.tsv"));
		if (FFileHelper::SaveStringToFile(Index, *Path, FFileHelper::EEncodingOptions::ForceUTF8))
		{
			UE_LOG(LogAssetTextExport, Display, TEXT("levels -> %s"), *Path);
		}
	}

	UE_LOG(LogAssetTextExport, Display, TEXT("%d exported, %d failed"), Assets.Num() - Failed, Failed);
	return Failed;
}
