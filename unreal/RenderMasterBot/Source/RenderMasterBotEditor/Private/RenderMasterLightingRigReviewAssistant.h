#pragma once

#include "Containers/Ticker.h"
#include "CoreMinimal.h"
#include "HAL/PlatformProcess.h"
#include "RenderMasterLightingRigAssistant.h"
#include "Templates/SharedPointer.h"

class FLevelEditorViewportClient;
class FRenderMasterWorkflowController;

enum class ERenderMasterLightingRigReviewState : uint8
{
    Ready,
    Capturing,
    Evaluating,
    Proposed,
    Passed,
    Unresolved,
    Failed,
    Applied,
    Rejected,
};

struct FRenderMasterLightingRigReviewProposal
{
    FString ProposalId;
    FString Status;
    FString Request;
    FString Summary;
    FString Rationale;
    FString MissingCapabilities;
    FString Exposure;
    FString FillBalance;
    FString RimSeparation;
    FString PreviewSha256;
    double Confidence = 0.0;
    double CenterLuminance = 0.0;
    double DarkPixelFraction = 0.0;
    double ClippedPixelFraction = 0.0;
    double ForegroundFraction = 0.0;
    bool bBlankLike = false;
    bool bUnderexposedLike = false;
    bool bOverexposedLike = false;
    FString SubjectActorPath;
    FString SubjectActorGuid;
    FTransform SubjectTransform = FTransform::Identity;
    FVector SubjectBoundsCenter = FVector::ZeroVector;
    FVector SubjectBoundsExtent = FVector::ZeroVector;
    double SubjectBoundsRadiusCm = 0.0;
    FString CameraActorPath;
    FString CameraActorGuid;
    FVector CameraLocation = FVector::ZeroVector;
    FRotator CameraRotation = FRotator::ZeroRotator;
    TArray<FRenderMasterLightingRigAction> ContextLights;
    TArray<FRenderMasterLightingRigAction> Actions;

    static bool Parse(
        const FString& JsonText,
        FRenderMasterLightingRigReviewProposal& OutProposal,
        FString& OutError);
    static bool LoadFromFile(
        const FString& Filename,
        FRenderMasterLightingRigReviewProposal& OutProposal,
        FString& OutError);
};

class FRenderMasterLightingRigReviewAssistant
    : public TSharedFromThis<FRenderMasterLightingRigReviewAssistant>
{
public:
    FRenderMasterLightingRigReviewAssistant(
        TSharedPtr<FRenderMasterWorkflowController> InWorkflowController,
        TSharedPtr<FRenderMasterLightingRigAssistant> InRigAssistant);
    ~FRenderMasterLightingRigReviewAssistant();

    void Initialize();
    void Shutdown();
    bool StartReview();
    bool ApplyProposal();
    void RejectProposal();
    void Cancel();
    void Reset();

    bool CanStart() const;
    bool CanApply() const;
    bool IsBusy() const;
    ERenderMasterLightingRigReviewState GetState() const { return State; }
    FText GetStateText() const;
    FText GetSummaryText() const;
    FText GetLogText() const;
    FLinearColor GetStateColor() const;
    const FString& GetPreviewPath() const { return PreviewPath; }

private:
    bool Tick(float DeltaTime);
    bool WriteContext(const FString& Filename, FString& OutError) const;
    bool BeginViewportCapture(FString& OutError);
    void OnScreenshotProcessed();
    void RestoreViewport();
    bool StartEvaluationProcess(FString& OutError);
    void FinishProcess();
    void ReadProcessOutput();
    void CloseProcessResources();
    bool ProposalMatchesCapturedEvidence(FString& OutError) const;
    bool RevalidateTargets(FString& OutError) const;
    void AppendLog(const FString& Text);
    void Fail(const FString& Error);

    TSharedPtr<FRenderMasterWorkflowController> WorkflowController;
    TSharedPtr<FRenderMasterLightingRigAssistant> RigAssistant;
    FRenderMasterAppliedLightingRig AppliedRig;
    FRenderMasterLightingRigReviewProposal Proposal;
    ERenderMasterLightingRigReviewState State = ERenderMasterLightingRigReviewState::Ready;
    FString ErrorText;
    FString ProcessLog;
    FString ReviewRequest;
    FString ReviewContextPath;
    FString PreviewPath;
    FString ProposalOutputPath;
    FString RawOutputPath;
    FString ReviewId;

    FLevelEditorViewportClient* CapturedViewportClient = nullptr;
    FVector OriginalViewLocation = FVector::ZeroVector;
    FRotator OriginalViewRotation = FRotator::ZeroRotator;
    float OriginalViewFOV = 90.0f;
    float OriginalFOVAngle = 90.0f;
    int32 OriginalViewMode = 0;
    bool bOriginalGameView = false;
    double CaptureStartedSeconds = 0.0;
    FDelegateHandle ScreenshotProcessedHandle;

    FProcHandle ProcessHandle;
    void* StdOutRead = nullptr;
    void* StdOutWrite = nullptr;
    void* StdErrRead = nullptr;
    void* StdErrWrite = nullptr;
    FTSTicker::FDelegateHandle TickHandle;
};
