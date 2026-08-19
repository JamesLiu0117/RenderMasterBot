#pragma once

#include "CoreMinimal.h"
#include "Widgets/SCompoundWidget.h"

class FRenderMasterWorkflowController;
class FRenderMasterMaterialAssistant;
class FRenderMasterTransformBatchAssistant;
class FRenderMasterLightBatchAssistant;
class FRenderMasterLightingRigAssistant;
class FRenderMasterLightingRigReviewAssistant;
class FRenderMasterCameraAssistant;
class FRenderMasterCameraBatchAssistant;
class FRenderMasterPerformanceAssistant;
class FRenderMasterRuntimePerformanceAssistant;
class FRenderMasterInsightsGpuAssistant;
class AActor;
class ACameraActor;
class ALight;
class AStaticMeshActor;
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
    FReply PrepareLightingRigAction();
    FReply ReviewAppliedLightingRigAction();
    FReply PrepareCameraAction();
    FReply ApproveCameraBatchAction();
    FReply RejectCameraBatchAction();
    FReply PreparePerformanceAction();
    FReply ApprovePerformanceAction();
    FReply RejectPerformanceAction();
    FReply PrepareRuntimePerformanceAction();
    FReply DismissRuntimePerformanceAction();
    FReply PrepareInsightsGpuAction();
    FReply PrepareActorGpuImpactAction();
    FReply OpenInsightsGpuTraceAction();
    FReply OpenInsightsGpuBaselineTraceAction();
    FReply DismissInsightsGpuAction();
    FReply ApproveAction();
    FReply RejectAction();
    FReply ApproveTransformAction();
    FReply RejectTransformAction();
    FReply ApproveLightAction();
    FReply RejectLightAction();
    FReply ApproveLightingRigAction();
    FReply RejectLightingRigAction();
    FReply ApproveLightingRigReviewAction();
    FReply RejectLightingRigReviewAction();
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
    EVisibility GetLightingRigProposalVisibility() const;
    EVisibility GetLightingRigApplyVisibility() const;
    EVisibility GetLightingRigRejectVisibility() const;
    EVisibility GetLightingRigReviewVisibility() const;
    EVisibility GetLightingRigReviewApplyVisibility() const;
    EVisibility GetLightingRigReviewRejectVisibility() const;
    EVisibility GetCameraProposalVisibility() const;
    EVisibility GetCameraApplyVisibility() const;
    EVisibility GetCameraRejectVisibility() const;
    EVisibility GetCameraBatchProposalVisibility() const;
    EVisibility GetCameraBatchApplyVisibility() const;
    EVisibility GetCameraBatchRejectVisibility() const;
    EVisibility GetPerformanceProposalVisibility() const;
    EVisibility GetPerformanceApplyVisibility() const;
    EVisibility GetPerformanceRejectVisibility() const;
    EVisibility GetRuntimePerformanceVisibility() const;
    EVisibility GetRuntimePerformanceDismissVisibility() const;
    EVisibility GetInsightsGpuVisibility() const;
    EVisibility GetInsightsGpuOpenVisibility() const;
    EVisibility GetInsightsGpuBaselineOpenVisibility() const;
    EVisibility GetInsightsGpuDismissVisibility() const;
    bool GetSelectedActorsForTransform(TArray<AActor*>& OutActors, FString& OutError) const;
    bool GetSelectedLights(TArray<ALight*>& OutLights, FString& OutError) const;
    bool GetSelectedLightingRig(
        AActor*& OutSubject,
        ACameraActor*& OutCamera,
        TArray<ALight*>& OutLights,
        FString& OutError) const;
    AActor* GetSingleSelectedActor(FString& OutError) const;
    ALight* GetSingleSelectedLight(FString& OutError) const;
    ACameraActor* GetSingleSelectedCamera(FString& OutError) const;
    bool GetSelectedCameras(
        TArray<ACameraActor*>& OutCameras,
        FString& OutError) const;
    bool GetSelectedStaticMeshActors(
        TArray<AStaticMeshActor*>& OutActors,
        FString& OutError) const;
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
    TSharedPtr<FRenderMasterLightingRigAssistant> LightingRigAssistant;
    TSharedPtr<FRenderMasterLightingRigReviewAssistant> LightingRigReviewAssistant;
    TSharedPtr<FRenderMasterCameraAssistant> CameraAssistant;
    TSharedPtr<FRenderMasterCameraBatchAssistant> CameraBatchAssistant;
    TSharedPtr<FRenderMasterPerformanceAssistant> PerformanceAssistant;
    TSharedPtr<FRenderMasterRuntimePerformanceAssistant> RuntimePerformanceAssistant;
    TSharedPtr<FRenderMasterInsightsGpuAssistant> InsightsGpuAssistant;
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
        LightingRig,
        LightingRigReview,
        Camera,
        CameraBatch,
        Performance,
        RuntimePerformance,
        InsightsGpu,
    };
    ELastAssistantAction LastAssistantAction = ELastAssistantAction::None;
};
