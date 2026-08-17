#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"

class ACameraActor;
class UCameraComponent;
class FRenderMasterWorkflowController;

enum class ERenderMasterCameraAssistantState : uint8
{
    Ready,
    Planning,
    Proposed,
    Unresolved,
    Failed,
    Applied,
    Rejected,
};

struct FRenderMasterCameraBounds
{
    TOptional<double> MinFocalLengthMm;
    TOptional<double> MaxFocalLengthMm;
    double MinApertureFstop = 1.0;
    double MaxApertureFstop = 32.0;
    double MinimumFocusDistanceCm = 0.0;
};

struct FRenderMasterCameraSnapshot
{
    FVector Location = FVector::ZeroVector;
    FRotator Rotation = FRotator::ZeroRotator;
    TOptional<double> FieldOfViewDeg;
    TOptional<double> FocalLengthMm;
    double ApertureFstop = 4.0;
    FString FocusMode = TEXT("project_default");
    double FocusDistanceCm = 1000.0;
    bool bExposureCompensationEnabled = false;
    double ExposureCompensationEv = 0.0;
    double PostProcessBlendWeight = 1.0;
};

struct FRenderMasterCameraProposal
{
    FString ProposalId;
    FString Status;
    FString Request;
    FString ActorName;
    FString ActorPath;
    FString ActorClass;
    FString ActorGuid;
    FString ComponentName;
    FString CameraKind;
    FRenderMasterCameraBounds Bounds;
    FRenderMasterCameraSnapshot Before;
    FRenderMasterCameraSnapshot After;
    FString ChangeSummary;
    FString Rationale;
    FString MissingCapabilities;
};

bool RenderMasterParseCameraProposalFile(
    const FString& Filename,
    FRenderMasterCameraProposal& OutProposal,
    FString& OutError);

bool RenderMasterApplyCameraProperties(
    ACameraActor* CameraActor,
    UCameraComponent* CameraComponent,
    const FString& CameraKind,
    const FRenderMasterCameraBounds& Bounds,
    const FRenderMasterCameraSnapshot& Before,
    const FRenderMasterCameraSnapshot& After,
    FString& OutError,
    bool bMarkPackageDirty = true);

class FRenderMasterCameraAssistant : public TSharedFromThis<FRenderMasterCameraAssistant>
{
public:
    explicit FRenderMasterCameraAssistant(
        TSharedPtr<FRenderMasterWorkflowController> InWorkflowController);
    ~FRenderMasterCameraAssistant();

    void Initialize();
    void Shutdown();
    bool StartProposal(const FString& Prompt, ACameraActor* CameraActor);
    bool ApplyProposal();
    void RejectProposal();
    void Cancel();

    bool CanStart() const;
    bool CanApply() const;
    bool IsPlanning() const;
    ERenderMasterCameraAssistantState GetState() const { return State; }
    FText GetStateText() const;
    FText GetSummaryText() const;
    FLinearColor GetStateColor() const;

private:
    bool Tick(float DeltaTime);
    bool WriteCameraContext(
        const FString& Filename,
        ACameraActor* CameraActor,
        FString& OutError);
    bool RevalidateTarget(FString& OutError) const;
    void CompleteProcess();
    void CloseProcessResources();
    void AppendLog(const FString& Line);
    void Fail(const FString& Error);

    TSharedPtr<FRenderMasterWorkflowController> WorkflowController;
    ERenderMasterCameraAssistantState State = ERenderMasterCameraAssistantState::Ready;
    FRenderMasterCameraProposal Proposal;
    FRenderMasterCameraSnapshot CapturedCamera;
    FRenderMasterCameraBounds CapturedBounds;
    TWeakObjectPtr<ACameraActor> TargetCameraActor;
    TWeakObjectPtr<UCameraComponent> TargetCameraComponent;
    FString CapturedActorPath;
    FString CapturedActorClass;
    FString CapturedActorGuid;
    FString CapturedComponentName;
    FString CapturedCameraKind;
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
