#include "RenderMasterBotEditor.h"

#include "Containers/Ticker.h"
#include "Framework/Docking/TabManager.h"
#include "Framework/MultiBox/MultiBoxBuilder.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "RenderMasterWorkflowController.h"
#include "SRenderMasterWorkspace.h"
#include "Styling/AppStyle.h"
#include "ToolMenus.h"
#include "Widgets/Docking/SDockTab.h"
#include "WorkspaceMenuStructure.h"
#include "WorkspaceMenuStructureModule.h"

#define LOCTEXT_NAMESPACE "RenderMasterBotEditorModule"

DEFINE_LOG_CATEGORY_STATIC(LogRenderMasterBotEditor, Log, All);

const FName FRenderMasterBotEditorModule::PanelTabName(TEXT("RenderMasterBotPanel"));

void FRenderMasterBotEditorModule::StartupModule()
{
    Controller = MakeShared<FRenderMasterWorkflowController>();
    Controller->Initialize();

    FGlobalTabmanager::Get()->RegisterNomadTabSpawner(
        PanelTabName,
        FOnSpawnTab::CreateRaw(this, &FRenderMasterBotEditorModule::SpawnPanelTab))
        .SetDisplayName(LOCTEXT("PanelTitle", "RenderMasterBot"))
        .SetTooltipText(LOCTEXT("PanelTooltip", "Run and monitor a RenderMasterBot workflow"))
        .SetIcon(FSlateIcon(FAppStyle::GetAppStyleSetName(), TEXT("Icons.Image")))
        .SetGroup(WorkspaceMenu::GetMenuStructure().GetToolsCategory());

    MenuStartupHandle = UToolMenus::RegisterStartupCallback(
        FSimpleMulticastDelegate::FDelegate::CreateRaw(this, &FRenderMasterBotEditorModule::RegisterMenus));

    if (FParse::Param(FCommandLine::Get(), TEXT("RenderMasterOpenPanel")))
    {
        AutoOpenTickerHandle = FTSTicker::GetCoreTicker().AddTicker(
            FTickerDelegate::CreateLambda([this](float)
            {
                OpenPanel();
                AutoOpenTickerHandle.Reset();
                return false;
            }),
            1.0f);
    }

    UE_LOG(LogRenderMasterBotEditor, Display, TEXT("RenderMasterBot editor panel registered."));
}

void FRenderMasterBotEditorModule::ShutdownModule()
{
    if (AutoOpenTickerHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(AutoOpenTickerHandle);
        AutoOpenTickerHandle.Reset();
    }

    if (UToolMenus::IsToolMenuUIEnabled())
    {
        UToolMenus::UnRegisterStartupCallback(this);
        UToolMenus::UnregisterOwner(this);
    }
    FGlobalTabmanager::Get()->UnregisterNomadTabSpawner(PanelTabName);

    if (Controller.IsValid())
    {
        Controller->Shutdown();
        Controller.Reset();
    }
}

TSharedRef<SDockTab> FRenderMasterBotEditorModule::SpawnPanelTab(const FSpawnTabArgs& Args)
{
    return SNew(SDockTab)
        .TabRole(ETabRole::NomadTab)
        [
            SNew(SRenderMasterWorkspace)
            .Controller(Controller)
        ];
}

void FRenderMasterBotEditorModule::RegisterMenus()
{
    FToolMenuOwnerScoped Owner(this);
    UToolMenu* Menu = UToolMenus::Get()->ExtendMenu(TEXT("LevelEditor.MainMenu.Tools"));
    FToolMenuSection& Section = Menu->FindOrAddSection(TEXT("RenderMasterBot"), LOCTEXT("MenuSection", "RenderMasterBot"));
    Section.AddMenuEntry(
        TEXT("OpenRenderMasterBot"),
        LOCTEXT("OpenPanel", "RenderMasterBot Assistant"),
        LOCTEXT("OpenPanelTooltip", "Open the RenderMasterBot graphics assistant"),
        FSlateIcon(FAppStyle::GetAppStyleSetName(), TEXT("Icons.Image")),
        FUIAction(FExecuteAction::CreateRaw(this, &FRenderMasterBotEditorModule::OpenPanel)));
}

void FRenderMasterBotEditorModule::OpenPanel()
{
    FGlobalTabmanager::Get()->TryInvokeTab(PanelTabName);
}

IMPLEMENT_MODULE(FRenderMasterBotEditorModule, RenderMasterBotEditor)

#undef LOCTEXT_NAMESPACE
