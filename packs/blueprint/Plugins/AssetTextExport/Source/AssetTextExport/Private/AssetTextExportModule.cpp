#include "AssetTextExportModule.h"

#include "HAL/IConsoleManager.h"
#include "BlueprintSummary.h"
#include "AssetTextExporter.h"

IMPLEMENT_MODULE(FAssetTextExportModule, AssetTextExport)

namespace
{
	void ExportOne(const FString& Asset, const FString& OutDir)
	{
		FString File;
		FString Error;
		if (FAssetTextExporter::ExportAsset(Asset, OutDir, File, Error))
		{
			UE_LOG(LogAssetTextExport, Display, TEXT("%s -> %s"), *Asset, *File);
		}
		else
		{
			UE_LOG(LogAssetTextExport, Error, TEXT("%s: %s"), *Asset, *Error);
		}
	}

	void ExportAssetCommand(const TArray<FString>& Args)
	{
		if (Args.Num() < 1)
		{
			UE_LOG(LogAssetTextExport, Error, TEXT("usage: AssetTextExport.Asset /Game/Path/Asset [OutDir]"));
			return;
		}

		ExportOne(Args[0], Args.Num() > 1 ? Args[1] : FString());
	}

	void SummariseOne(const FString& Asset, const FString& OutDir)
	{
		FString File;
		FString Error;
		if (FBlueprintSummary::WriteSummary(Asset, OutDir, true, File, Error))
		{
			UE_LOG(LogAssetTextExport, Display, TEXT("%s -> %s"), *Asset, *File);
		}
		else
		{
			UE_LOG(LogAssetTextExport, Error, TEXT("%s: %s"), *Asset, *Error);
		}
	}

	void SummaryCommand(const TArray<FString>& Args)
	{
		if (Args.Num() < 1)
		{
			UE_LOG(LogAssetTextExport, Error, TEXT("usage: AssetTextExport.Summary /Game/Path/Asset [OutDir]"));
			return;
		}

		SummariseOne(Args[0], Args.Num() > 1 ? Args[1] : FString());
	}

	void SummaryDirCommand(const TArray<FString>& Args)
	{
		if (Args.Num() < 1)
		{
			UE_LOG(LogAssetTextExport, Error, TEXT("usage: AssetTextExport.SummaryDir /Game/Path [OutDir]"));
			return;
		}

		TArray<FString> Assets;
		FAssetTextExporter::FindBlueprintsUnder(Args[0], Assets);
		const FString OutDir = Args.Num() > 1 ? Args[1] : FString();
		for (const FString& Asset : Assets)
		{
			SummariseOne(Asset, OutDir);
		}
	}

	void SummaryDepsCommand(const TArray<FString>& Args)
	{
		if (Args.Num() < 1)
		{
			UE_LOG(LogAssetTextExport, Error,
				TEXT("usage: AssetTextExport.SummaryDeps /Game/Path/Asset [OutDir] [Depth]"));
			return;
		}

		const int32 Depth = Args.Num() > 2 ? FCString::Atoi(*Args[2]) : -1;

		TArray<FString> Assets;
		FAssetTextExporter::FindBlueprintDependencies(Args[0], Depth, Assets);
		UE_LOG(LogAssetTextExport, Display, TEXT("%s reaches %d Blueprint(s)"), *Args[0], Assets.Num());

		const FString OutDir = Args.Num() > 1 ? Args[1] : FString();
		for (const FString& Asset : Assets)
		{
			SummariseOne(Asset, OutDir);
		}
	}

	void ExportDirCommand(const TArray<FString>& Args)
	{
		if (Args.Num() < 1)
		{
			UE_LOG(LogAssetTextExport, Error, TEXT("usage: AssetTextExport.Dir /Game/Path [OutDir]"));
			return;
		}

		TArray<FString> Assets;
		FAssetTextExporter::FindBlueprintsUnder(Args[0], Assets);
		if (Assets.Num() == 0)
		{
			UE_LOG(LogAssetTextExport, Warning, TEXT("no Blueprint under '%s'"), *Args[0]);
			return;
		}

		const FString OutDir = Args.Num() > 1 ? Args[1] : FString();
		for (const FString& Asset : Assets)
		{
			ExportOne(Asset, OutDir);
		}
	}
}

void FAssetTextExportModule::StartupModule()
{
	Commands.Add(IConsoleManager::Get().RegisterConsoleCommand(
		TEXT("AssetTextExport.Asset"),
		TEXT("Export one asset to T3D text. Args: <package path> [out dir]"),
		FConsoleCommandWithArgsDelegate::CreateStatic(&ExportAssetCommand)));

	Commands.Add(IConsoleManager::Get().RegisterConsoleCommand(
		TEXT("AssetTextExport.Dir"),
		TEXT("Export every Blueprint under a package path. Args: <package path> [out dir]"),
		FConsoleCommandWithArgsDelegate::CreateStatic(&ExportDirCommand)));

	Commands.Add(IConsoleManager::Get().RegisterConsoleCommand(
		TEXT("AssetTextExport.Summary"),
		TEXT("Write what one Blueprint declares as JSON. Args: <package path> [out dir]"),
		FConsoleCommandWithArgsDelegate::CreateStatic(&SummaryCommand)));

	Commands.Add(IConsoleManager::Get().RegisterConsoleCommand(
		TEXT("AssetTextExport.SummaryDir"),
		TEXT("Write the JSON of every Blueprint under a package path. Args: <package path> [out dir]"),
		FConsoleCommandWithArgsDelegate::CreateStatic(&SummaryDirCommand)));

	Commands.Add(IConsoleManager::Get().RegisterConsoleCommand(
		TEXT("AssetTextExport.SummaryDeps"),
		TEXT("Write the JSON of a Blueprint and every Blueprint it reaches. Args: <package path> [out dir] [depth]"),
		FConsoleCommandWithArgsDelegate::CreateStatic(&SummaryDepsCommand)));
}

void FAssetTextExportModule::ShutdownModule()
{
	for (IConsoleCommand* Command : Commands)
	{
		IConsoleManager::Get().UnregisterConsoleObject(Command);
	}
	Commands.Empty();
}
