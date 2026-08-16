#pragma once

#include "CoreMinimal.h"
#include "Widgets/SCompoundWidget.h"

class FRenderMasterWorkflowController;
class SMultiLineEditableTextBox;

class SRenderMasterPanel : public SCompoundWidget
{
public:
    SLATE_BEGIN_ARGS(SRenderMasterPanel) {}
        SLATE_ARGUMENT(TSharedPtr<FRenderMasterWorkflowController>, Controller)
    SLATE_END_ARGS()

    void Construct(const FArguments& InArgs);

private:
    TSharedRef<SWidget> MakeStageChip(const FText& Label, const FString& StageName);
    TSharedRef<SWidget> MakePathSetting(
        const FText& Label,
        TFunction<FString()> Getter,
        TFunction<void(const FString&)> Setter,
        TFunction<void()> BrowseAction);
    FReply OnRunClicked();
    FReply OnCancelClicked();
    FReply BrowsePython();
    FReply BrowseAssetCatalog();
    FReply BrowseWorkflowRoot();

    TSharedPtr<FRenderMasterWorkflowController> Controller;
    TSharedPtr<SMultiLineEditableTextBox> PromptBox;
    int32 MaxIterations = 3;
};
