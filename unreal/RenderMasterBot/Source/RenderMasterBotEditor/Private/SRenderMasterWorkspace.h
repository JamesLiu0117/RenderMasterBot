#pragma once

#include "CoreMinimal.h"
#include "RenderMasterConversationAssistant.h"
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
class FRenderMasterLevelAuditAssistant;
class FRenderMasterRuntimePerformanceAssistant;
class FRenderMasterInsightsGpuAssistant;
class FRenderMasterLevelProfilingAssistant;
class FRenderMasterShadowOptimizationVerificationAssistant;
class AActor;
class ACameraActor;
class ALight;
class AStaticMeshActor;
class SComboButton;
class SMultiLineEditableTextBox;
class SScrollBox;
class SVerticalBox;
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

    FReply SendConversationMessage();
    FReply ClearConversation();
    void HandleConversationTurnCompleted(
        const FRenderMasterConversationTurn& Turn);
    void HandleConversationTurnFailed(const FString& Error);
    void HandleTransformResultReady();
    void HandleLevelProfilingResultReady();
    void DispatchConversationIntent(
        const FString& Intent,
        const FString& ActionPrompt);
    bool CaptureConversationContext(
        FRenderMasterConversationContext& OutContext,
        FString& OutError) const;
    void AddConversationMessage(const FString& Role, const FString& Content);
    void AddConversationResultEvent(
        const FString& Intent,
        const FString& Phase,
        const FString& Status,
        const FString& Summary,
        const FString& ProposalId,
        const TArray<FString>& SubjectIds,
        const FString& EvidenceKind = FString(),
        const FString& EvidencePath = FString());
    void RecordConversationActionFailure(
        const FString& Intent,
        const FString& Error);
    void ClearActiveConversationAction();
    FString GetConversationSessionPath() const;
    TOptional<FRenderMasterConversationTaskState> BuildConversationTaskState() const;
    void SaveConversationSession();
    bool RestoreConversationSession(FString& OutStatus);
    void RefreshConversationMessages();
    bool CanSendConversationMessage() const;

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
    FReply PrepareLevelAuditAction();
    FReply ApproveLevelAuditAction();
    FReply RejectLevelAuditAction();
    FReply PrepareRuntimePerformanceAction();
    FReply DismissRuntimePerformanceAction();
    FReply PrepareInsightsGpuAction();
    FReply PrepareActorGpuImpactAction();
    FReply PrepareShadowOptimizationAction();
    FReply ApproveShadowOptimizationAction();
    FReply RejectShadowOptimizationAction();
    FReply PrepareShadowVerificationAction();
    FReply KeepShadowOptimizationAction();
    FReply RollbackShadowOptimizationAction();
    FReply DismissShadowVerificationAction();
    FReply PrepareLevelProfilingAction();
    FReply DismissLevelProfilingAction();
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
    EVisibility GetLevelAuditVisibility() const;
    EVisibility GetLevelAuditApplyVisibility() const;
    EVisibility GetLevelAuditRejectVisibility() const;
    EVisibility GetRuntimePerformanceVisibility() const;
    EVisibility GetRuntimePerformanceDismissVisibility() const;
    EVisibility GetInsightsGpuVisibility() const;
    EVisibility GetInsightsGpuOpenVisibility() const;
    EVisibility GetInsightsGpuBaselineOpenVisibility() const;
    EVisibility GetInsightsGpuDismissVisibility() const;
    EVisibility GetShadowOptimizationApplyVisibility() const;
    EVisibility GetShadowOptimizationRejectVisibility() const;
    EVisibility GetShadowVerificationVisibility() const;
    EVisibility GetShadowVerificationStartVisibility() const;
    EVisibility GetShadowVerificationKeepVisibility() const;
    EVisibility GetShadowVerificationRollbackVisibility() const;
    EVisibility GetShadowVerificationDismissVisibility() const;
    EVisibility GetLevelProfilingVisibility() const;
    EVisibility GetLevelProfilingDismissVisibility() const;
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
    TSharedPtr<FRenderMasterLevelAuditAssistant> LevelAuditAssistant;
    TSharedPtr<FRenderMasterRuntimePerformanceAssistant> RuntimePerformanceAssistant;
    TSharedPtr<FRenderMasterInsightsGpuAssistant> InsightsGpuAssistant;
    TSharedPtr<FRenderMasterLevelProfilingAssistant> LevelProfilingAssistant;
    TSharedPtr<FRenderMasterShadowOptimizationVerificationAssistant>
        ShadowVerificationAssistant;
    TSharedPtr<FRenderMasterConversationAssistant> ConversationAssistant;
    TSharedPtr<SWidgetSwitcher> PageSwitcher;
    TSharedPtr<SMultiLineEditableTextBox> AssistantPromptBox;
    TSharedPtr<SScrollBox> ConversationScrollBox;
    TSharedPtr<SVerticalBox> ConversationMessagesBox;
    TSharedPtr<SComboButton> MaterialSlotComboButton;
    TWeakObjectPtr<UStaticMeshComponent> SelectedSlotComponent;
    FString LastRequest;
    FString AssistantReply;
    TArray<FRenderMasterConversationMessage> ConversationMessages;
    TArray<FRenderMasterConversationResultEvent> ConversationResultEvents;
    FString ActiveConversationActionTurnId;
    FString ActiveConversationActionIntent;
    int32 NextConversationMessageNumber = 1;
    int32 NextConversationResultNumber = 1;
    bool bRestoringConversationSession = false;
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
        LevelAudit,
        RuntimePerformance,
        InsightsGpu,
        ShadowOptimization,
        ShadowVerification,
        LevelProfiling,
    };
    ELastAssistantAction LastAssistantAction = ELastAssistantAction::None;
};
