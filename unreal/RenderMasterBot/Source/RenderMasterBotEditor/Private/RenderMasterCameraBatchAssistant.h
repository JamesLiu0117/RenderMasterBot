#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"
#include "RenderMasterCameraAssistant.h"

class ACameraActor;
class UCameraComponent;
class FRenderMasterWorkflowController;

enum class ERenderMasterCameraBatchAssistantState : uint8
{
    Ready,
    Planning,
    Proposed,
    Unresolved,
    Failed,
    Applied,
    Rejected,
};

struct FRenderMasterCameraBatchProposal
{
    FString ProposalId;
    FString Status;
    FString Request;
    TArray<FRenderMasterCameraProposal> Actions;
    FString Rationale;
    FString MissingCapabilities;
};

bool RenderMasterParseCameraBatchProposalFile(
    const FString& Filename,
    FRenderMasterCameraBatchProposal& OutProposal,
    FString& OutError);

bool RenderMasterApplyCameraPropertiesBatch(
    const TArray<ACameraActor*>& CameraActors,
    const TArray<UCameraComponent*>& CameraComponents,
    const TArray<FRenderMasterCameraProposal>& Actions,
    FString& OutError,
    bool bMarkPackageDirty = true);

class FRenderMasterCameraBatchAssistant
    : public TSharedFromThis<FRenderMasterCameraBatchAssistant>
{
public:
    explicit FRenderMasterCameraBatchAssistant(
        TSharedPtr<FRenderMasterWorkflowController> InWorkflowController);
    ~FRenderMasterCameraBatchAssistant();

    void Initialize();
    void Shutdown();
    bool StartProposal(
        const FString& Prompt,
        const TArray<ACameraActor*>& CameraActors);
    bool ApplyProposal();
    void RejectProposal();
    void Cancel();

    bool CanStart() const;
    bool CanApply() const;
    bool IsPlanning() const;
    ERenderMasterCameraBatchAssistantState GetState() const { return State; }
    FText GetStateText() const;
    FText GetSummaryText() const;
    FLinearColor GetStateColor() const;

private:
    bool Tick(float DeltaTime);
    bool WriteCameraSelectionContext(
        const FString& Filename,
        const TArray<ACameraActor*>& CameraActors,
        FString& OutError);
    bool RevalidateTargets(FString& OutError) const;
    void CompleteProcess();
    void CloseProcessResources();
    void AppendLog(const FString& Line);
    void Fail(const FString& Error);

    TSharedPtr<FRenderMasterWorkflowController> WorkflowController;
    ERenderMasterCameraBatchAssistantState State =
        ERenderMasterCameraBatchAssistantState::Ready;
    FRenderMasterCameraBatchProposal Proposal;
    TArray<FRenderMasterCameraProposal> CapturedTargets;
    TArray<TWeakObjectPtr<ACameraActor>> TargetCameraActors;
    TArray<TWeakObjectPtr<UCameraComponent>> TargetCameraComponents;
    FString ProposalOutputPath;
    FString ErrorText;
    FString ProcessLog;
    FProcHandle ProcessHandle;
    void* StdOutRead = nullptr;
    void* StdOutWrite = nullptr;
    void* StdErrRead = nullptr;
    void* StdErrWrite = nullptr;
    FTSTicker::FDelegateHandle TickHandle;
};
