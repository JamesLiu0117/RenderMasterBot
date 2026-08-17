#pragma once

#include "CoreMinimal.h"
#include "Widgets/SCompoundWidget.h"

class FRenderMasterWorkflowController;
class FRenderMasterMaterialAssistant;
class FRenderMasterTransformBatchAssistant;
class FRenderMasterLightBatchAssistant;
class FRenderMasterCameraAssistant;
class AActor;
class ACameraActor;
class ALight;
class SComboButton;
class SMultiLineEditableTextBox;
class SWidgetSwitcher;
class UStaticMeshComponent;

class SRenderMasterWorkspace : public SCompoundWidget
{
public:
    SLATE_BEGIN_ARGS(SRenderMasterWorkspace) {}
        SLATE_ARGUMENT(TSharedPtr<FRenderMasterWorkflowController>, Controller)
    SLATE_END_ARGS()

    void Construct(const FArguments& InArgs);
    virtual ~SRenderMasterWorkspace() override;

private:
    TSharedRef<SWidget> BuildNavigation();
    TSharedRef<SWidget> BuildAssistantPage();
    TSharedRef<SWidget> BuildMaterialSlotMenu();
    TSharedRef<SWidget> MakeNavigationButton(const FText& Label, int32 PageIndex);
    TSharedRef<SWidget> MakeMessage(const FText& Speaker, TAttribute<FText> Message, const FLinearColor& Accent);

    FReply AnalyzeContext();
    FReply PrepareAction();
    FReply PrepareExternalAction();
    FReply PrepareTransformAction();
    FReply PrepareLightAction();
    FReply PrepareCameraAction();
    FReply ApproveAction();
    FReply RejectAction();
    FReply ApproveTransformAction();
    FReply RejectTransformAction();
    FReply ApproveLightAction();
    FReply RejectLightAction();
    FReply ApproveCameraAction();
    FReply RejectCameraAction();
    void SetPage(int32 PageIndex);
    bool CanPrepareAssistantAction() const;

    FText GetProjectContext() const;
    FText GetSelectionContext() const;
    FText GetAssistantReply() const;
    FText GetLastRequest() const;
    FText GetTargetMaterialSlotText() const;
    EVisibility GetLastRequestVisibility() const;
    EVisibility GetProposalVisibility() const;
    EVisibility GetApplyVisibility() const;
    EVisibility GetRejectVisibility() const;
    EVisibility GetTransformProposalVisibility() const;
    EVisibility GetTransformApplyVisibility() const;
    EVisibility GetTransformRejectVisibility() const;
    EVisibility GetLightProposalVisibility() const;
    EVisibility GetLightApplyVisibility() const;
    EVisibility GetLightRejectVisibility() const;
    EVisibility GetCameraProposalVisibility() const;
    EVisibility GetCameraApplyVisibility() const;
    EVisibility GetCameraRejectVisibility() const;
    bool GetSelectedActorsForTransform(TArray<AActor*>& OutActors, FString& OutError) const;
    bool GetSelectedLights(TArray<ALight*>& OutLights, FString& OutError) const;
    AActor* GetSingleSelectedActor(FString& OutError) const;
    ALight* GetSingleSelectedLight(FString& OutError) const;
    ACameraActor* GetSingleSelectedCamera(FString& OutError) const;
    UStaticMeshComponent* GetSingleSelectedStaticMeshComponent(FString& OutError) const;
    bool ResolveTargetMaterialSlot(
        UStaticMeshComponent* Component,
        int32& OutSlotIndex,
        FString& OutError) const;
    void SelectTargetMaterialSlot(UStaticMeshComponent* Component, int32 SlotIndex);

    TSharedPtr<FRenderMasterWorkflowController> Controller;
    TSharedPtr<FRenderMasterMaterialAssistant> MaterialAssistant;
    TSharedPtr<FRenderMasterTransformBatchAssistant> TransformAssistant;
    TSharedPtr<FRenderMasterLightBatchAssistant> LightAssistant;
    TSharedPtr<FRenderMasterCameraAssistant> CameraAssistant;
    TSharedPtr<SWidgetSwitcher> PageSwitcher;
    TSharedPtr<SMultiLineEditableTextBox> AssistantPromptBox;
    TSharedPtr<SComboButton> MaterialSlotComboButton;
    TWeakObjectPtr<UStaticMeshComponent> SelectedSlotComponent;
    FString LastRequest;
    FString AssistantReply;
    int32 SelectedMaterialSlotIndex = INDEX_NONE;
    int32 ActivePage = 0;
    enum class ELastAssistantAction : uint8
    {
        None,
        Material,
        Transform,
        Light,
        Camera,
    };
    ELastAssistantAction LastAssistantAction = ELastAssistantAction::None;
};
