#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleInterface.h"

struct IConsoleCommand;

/** Registers the two editor console commands. */
class FAssetTextExportModule : public IModuleInterface
{
public:
	virtual void StartupModule() override;
	virtual void ShutdownModule() override;

private:
	TArray<IConsoleCommand*> Commands;
};
