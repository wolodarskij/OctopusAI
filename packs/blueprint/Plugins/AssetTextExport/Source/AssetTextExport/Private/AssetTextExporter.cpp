#include "AssetTextExporter.h"

#include "Misc/EngineVersionComparison.h"

#if UE_VERSION_OLDER_THAN(5, 0, 0)
#include "AssetRegistryModule.h"
#else
#include "AssetRegistry/AssetRegistryModule.h"
#endif
#include "Containers/UnrealString.h"
#include "Engine/Blueprint.h"
#include "Exporters/Exporter.h"
#include "UnrealExporter.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#if !UE_VERSION_OLDER_THAN(5, 0, 0)
#include "Misc/StringOutputDevice.h"   // FStringOutputDevice; 4.x gets it through UnrealExporter.h
#endif
#include "Modules/ModuleManager.h"
#include "UObject/UObjectGlobals.h"
#include "UObject/UObjectMarks.h"

DEFINE_LOG_CATEGORY(LogAssetTextExport);

namespace
{
	/** The short class name of an asset, on every engine version. */
	FString ClassNameOf(const FAssetData& Data)
	{
#if UE_VERSION_OLDER_THAN(5, 1, 0)
		return Data.AssetClass.ToString();
#else
		return Data.AssetClassPath.GetAssetName().ToString();
#endif
	}

	/** `/Game/A/B.B` for the asset, on every engine version. */
	FString ObjectPathOf(const FAssetData& Data)
	{
#if UE_VERSION_OLDER_THAN(5, 1, 0)
		return Data.ObjectPath.ToString();
#else
		return Data.GetObjectPathString();
#endif
	}

	/** Does this asset carry Blueprint graphs?
	 *
	 *  The asset data holds the concrete class name, so a comparison against `Blueprint` alone drops
	 *  every `WidgetBlueprint` and `AnimBlueprint`. Resolve the class and ask the hierarchy instead.
	 *  A class the process has not loaded resolves to null, and the name test answers then. */
	bool IsBlueprintAsset(const FAssetData& Data)
	{
		const FString ClassName = ClassNameOf(Data);
		if (ClassName == UBlueprint::StaticClass()->GetName())
		{
			return true;
		}

		if (UClass* Resolved = Data.GetClass())
		{
			return Resolved->IsChildOf(UBlueprint::StaticClass());
		}

		return ClassName.EndsWith(TEXT("Blueprint"));
	}
}

FString FAssetTextExporter::ToObjectPath(const FString& AssetPath)
{
	FString Trimmed = AssetPath;
	Trimmed.TrimStartAndEndInline();
	while (Trimmed.EndsWith(TEXT("/")) && Trimmed.Len() > 1)
	{
		Trimmed.LeftChopInline(1);
	}

	FString PackagePart;
	FString AssetPart;
	if (Trimmed.Split(TEXT("."), &PackagePart, &AssetPart))
	{
		return Trimmed;
	}

	return Trimmed + TEXT(".") + FPackageName::GetShortName(Trimmed);
}

FString FAssetTextExporter::DefaultOutDir()
{
	return FPaths::ConvertRelativePathToFull(FPaths::Combine(FPaths::ProjectDir(), TEXT("T3D")));
}

FString FAssetTextExporter::NextFreeName(const FString& Dir, const FString& AssetName)
{
	for (int32 n = 0; ; ++n)
	{
		const FString Candidate = FPaths::Combine(Dir, FString::Printf(TEXT("Text%s-%d.txt"), *AssetName, n));
		if (!IFileManager::Get().FileExists(*Candidate))
		{
			return Candidate;
		}
	}
}

bool FAssetTextExporter::ExportAsset(const FString& AssetPath, const FString& OutDir,
                                     FString& OutFile, FString& OutError)
{
	OutFile.Empty();
	OutError.Empty();

	if (AssetPath.IsEmpty())
	{
		OutError = TEXT("empty asset path");
		return false;
	}

	const FString ObjectPath = FAssetTextExporter::ToObjectPath(AssetPath);
	UObject* Asset = LoadObject<UObject>(nullptr, *ObjectPath);
	if (Asset == nullptr)
	{
		// A package whose primary asset does not share the package's short name.
		Asset = LoadObject<UObject>(nullptr, *AssetPath);
	}

	// The engine's own DumpAssetToTempFile asserts on a null asset. An error string is the answer a
	// batch run can act on.
	if (Asset == nullptr)
	{
		OutError = FString::Printf(TEXT("could not load '%s'"), *ObjectPath);
		return false;
	}

	return ExportObject(Asset, OutDir, OutFile, OutError);
}

bool FAssetTextExporter::ExportObject(UObject* Asset, const FString& OutDir,
                                      FString& OutFile, FString& OutError)
{
	OutFile.Empty();
	OutError.Empty();

	if (Asset == nullptr)
	{
		OutError = TEXT("null asset");
		return false;
	}

	UnMarkAllObjects(EObjectMark(OBJECTMARK_TagExp | OBJECTMARK_TagImp));

	FStringOutputDevice Archive;
	const FExportObjectInnerContext Context;

	// The whole object, with its inner UEdGraphs and UEdGraphNodes. The three flags are what the
	// engine's own asset dump (IAssetTools::DumpAssetToTempFile) uses; they decide the `Begin Object`
	// nesting and the quoting that every consumer of this text parses.
	UExporter::ExportToOutputDevice(&Context, Asset, nullptr, Archive, TEXT("copy"), 0,
		PPF_ExportsNotFullyQualified | PPF_Copy | PPF_Delimited, false, Asset->GetOuter());

	const FString Dir = OutDir.IsEmpty() ? FAssetTextExporter::DefaultOutDir() : OutDir;
	if (!IFileManager::Get().MakeDirectory(*Dir, true))
	{
		if (!IFileManager::Get().DirectoryExists(*Dir))
		{
			OutError = FString::Printf(TEXT("could not create '%s'"), *Dir);
			return false;
		}
	}

	const FString Path = FPaths::ConvertRelativePathToFull(NextFreeName(Dir, Asset->GetName()));

	// One encoding, always. The engine default writes UTF-16LE only when the dump holds a non-ANSI
	// character, so a directory of exports ends up mixed and a byte-oriented search reports a
	// confident zero on the files it cannot decode.
	if (!FFileHelper::SaveStringToFile(Archive, *Path, FFileHelper::EEncodingOptions::ForceUTF8))
	{
		OutError = FString::Printf(TEXT("could not write '%s'"), *Path);
		return false;
	}

	OutFile = Path;
	return true;
}

void FAssetTextExporter::FindBlueprintsUnder(const FString& PackageDir, TArray<FString>& OutPackagePaths)
{
	FAssetRegistryModule& Registry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
	Registry.Get().SearchAllAssets(true);

	FString Dir = PackageDir;
	while (Dir.EndsWith(TEXT("/")) && Dir.Len() > 1)
	{
		Dir.LeftChopInline(1);
	}

	TArray<FAssetData> Found;
	Registry.Get().GetAssetsByPath(FName(*Dir), Found, true);

	for (const FAssetData& Data : Found)
	{
		if (IsBlueprintAsset(Data))
		{
			OutPackagePaths.Add(ObjectPathOf(Data));
		}
	}

	OutPackagePaths.Sort();
}

// One breadth-first walk over the package graph. bReferencers reverses which way the edges point.
static void SweepPackageGraph(const FString& SeedPath, int32 Depth, bool bReferencers,
                              TArray<FString>& OutPackagePaths, TArray<int32>* OutLevels)
{
	FAssetRegistryModule& Registry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
	Registry.Get().SearchAllAssets(true);

	// The seed may arrive as an object path. An edge of this graph is keyed by package.
	FString Package = FAssetTextExporter::ToObjectPath(SeedPath);
	FString PackagePart;
	FString AssetPart;
	if (Package.Split(TEXT("."), &PackagePart, &AssetPart))
	{
		Package = PackagePart;
	}

	TSet<FName> Seen;
	TArray<FName> Frontier;
	Frontier.Add(FName(*Package));
	Seen.Add(FName(*Package));

	TArray<FName> Ordered;
	TArray<int32> OrderedLevels;
	int32 Level = 0;

	while (Frontier.Num() > 0 && (Depth < 0 || Level <= Depth))
	{
		Ordered.Append(Frontier);
		for (int32 i = 0; i < Frontier.Num(); ++i)
		{
			OrderedLevels.Add(Level);
		}

		if (Depth >= 0 && Level == Depth)
		{
			break;
		}

		TArray<FName> Next;
		for (const FName& Name : Frontier)
		{
			TArray<FName> Dependencies;
#if UE_VERSION_OLDER_THAN(4, 26, 0)
			const EAssetRegistryDependencyType::Type Category = EAssetRegistryDependencyType::Packages;
#else
			const UE::AssetRegistry::EDependencyCategory Category = UE::AssetRegistry::EDependencyCategory::Package;
#endif
			if (bReferencers)
			{
				Registry.Get().GetReferencers(Name, Dependencies, Category);
			}
			else
			{
				Registry.Get().GetDependencies(Name, Dependencies, Category);
			}

			for (const FName& Dependency : Dependencies)
			{
				// The engine and every plugin are outside the game, and following them turns a
				// sweep of one Blueprint into a sweep of the whole install.
				if (!Dependency.ToString().StartsWith(TEXT("/Game/")))
				{
					continue;
				}
				if (!Seen.Contains(Dependency))
				{
					Seen.Add(Dependency);
					Next.Add(Dependency);
				}
			}
		}

		Frontier = MoveTemp(Next);
		++Level;
	}

	// A package holds assets of every kind. Only the Blueprints have a graph to read.
	for (int32 i = 0; i < Ordered.Num(); ++i)
	{
		TArray<FAssetData> Assets;
		Registry.Get().GetAssetsByPackageName(Ordered[i], Assets);
		for (const FAssetData& Data : Assets)
		{
			if (!IsBlueprintAsset(Data))
			{
				continue;
			}

			const FString Path = ObjectPathOf(Data);
			if (OutPackagePaths.Contains(Path))
			{
				continue;
			}

			OutPackagePaths.Add(Path);
			if (OutLevels != nullptr)
			{
				OutLevels->Add(OrderedLevels[i]);
			}
		}
	}
}

void FAssetTextExporter::FindBlueprintDependencies(const FString& SeedPath, int32 Depth,
                                                   TArray<FString>& OutPackagePaths,
                                                   TArray<int32>* OutLevels)
{
	SweepPackageGraph(SeedPath, Depth, false, OutPackagePaths, OutLevels);
}

void FAssetTextExporter::FindBlueprintReferencers(const FString& SeedPath, int32 Depth,
                                                  TArray<FString>& OutPackagePaths,
                                                  TArray<int32>* OutLevels)
{
	SweepPackageGraph(SeedPath, Depth, true, OutPackagePaths, OutLevels);
}
