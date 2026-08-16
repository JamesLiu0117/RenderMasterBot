#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"
#include "Modules/ModuleManager.h"

class FRenderMasterWorkflowController;
class SDockTab;
class FSpawnTabArgs;

class FRenderMasterBotEditorModule : public IModuleInterface
{
public:
    virtual void StartupModule() override;
    virtual void ShutdownModule() override;

    static const FName PanelTabName;

private:
    TSharedRef<SDockTab> SpawnPanelTab(const FSpawnTabArgs& Args);
    void RegisterMenus();
    void OpenPanel();

    TSharedPtr<FRenderMasterWorkflowController> Controller;
    FDelegateHandle MenuStartupHandle;
    FTSTicker::FDelegateHandle AutoOpenTickerHandle;
};
