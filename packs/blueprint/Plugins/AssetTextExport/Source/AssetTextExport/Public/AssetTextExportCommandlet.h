#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"
#include "AssetTextExportCommandlet.generated.h"

/** T3D export from the command line.
 *
 *  The class name must end in `Commandlet`: `-run=AssetTextExport` is suffixed with that word
 *  before the class lookup (LaunchEngineLoop.cpp). */
UCLASS()
class UAssetTextExportCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	UAssetTextExportCommandlet();

	/** Returns the number of assets that failed, so 0 means every asset was written. */
	virtual int32 Main(const FString& Params) override;
};
