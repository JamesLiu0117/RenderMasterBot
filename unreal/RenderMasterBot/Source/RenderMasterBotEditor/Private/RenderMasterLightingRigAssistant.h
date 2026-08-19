#pragma once

#include "Containers/Ticker.h"
#include "CoreMinimal.h"
#include "HAL/PlatformProcess.h"
#include "RenderMasterLightAssistant.h"
#include "Templates/SharedPointer.h"
#include "UObject/WeakObjectPtr.h"

class AActor;
class ACameraActor;
class ALight;
class FRenderMasterWorkflowController;
class UCameraComponent;
class ULightComponent;
class USceneComponent;

enum class ERenderMasterLightingRigAssistantState : uint8
{
    Ready,
    Planning,
    Proposed,
    Unresolved,
    Failed,
    Applied,
    Rejected,
};

struct FRenderMasterRigSubjectCapture
{
    TWeakObjectPtr<AActor> Actor;
    TWeakObjectPtr<USceneComponent> RootComponent;
    FString ActorName;
    FString ActorPath;
    FString ActorClass;
    FString ActorGuid;
    FString RootComponentName;
    FString RootMobility;
    FTransform Transform = FTransform::Identity;
    FVector BoundsCenter = FVector::ZeroVector;
    FVector BoundsExtent = FVector::ZeroVector;
    double BoundsRadiusCm = 0.0;
};

struct FRenderMasterRigCameraCapture
{
    TWeakObjectPtr<ACameraActor> Actor;
    TWeakObjectPtr<UCameraComponent> Component;
    FString ActorName;
    FString ActorPath;
    FString ActorClass;
    FString ActorGuid;
    FString ComponentName;
    FString CameraKind;
    FString ComponentMobility;
    FVector Location = FVector::ZeroVector;
    FRotator Rotation = FRotator::ZeroRotator;
};

struct FRenderMasterRigLightCapture
{
    FRenderMasterCapturedLightTarget Target;
    FVector Location = FVector::ZeroVector;
};

struct FRenderMasterRigLightState
{
    FVector Location = FVector::ZeroVector;
    FRenderMasterLightSnapshot Light;
};

struct FRenderMasterLightingRigAction
{
    FString Role;
    FString ActorName;
    FString ActorPath;
    FString ActorClass;
    FString ActorGuid;
    FString ComponentName;
    FString ComponentMobility;
    FString LightKind;
    FString ChangeSummary;
    FRenderMasterRigLightState Before;
    FRenderMasterRigLightState After;
};

struct FRenderMasterLightingRigProposal
{
    FString ProposalId;
    FString Status;
    FString Request;
    FString Rationale;
    FString MissingCapabilities;
    FString Contrast;
    FString Palette;
    FString KeySide;
    FString Spacing;
    FString Brightness;
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
    TArray<FRenderMasterLightingRigAction> Actions;

    static bool Parse(
        const FString& JsonText,
        FRenderMasterLightingRigProposal& OutProposal,
        FString& OutError);
    static bool LoadFromFile(
        const FString& Filename,
        FRenderMasterLightingRigProposal& OutProposal,
        FString& OutError);
};

struct FRenderMasterAppliedLightingRig
{
    FRenderMasterRigSubjectCapture Subject;
    FRenderMasterRigCameraCapture Camera;
    TArray<FRenderMasterRigLightCapture> Lights;
    TArray<FString> Roles;
    FString SourceRequest;
};

bool RenderMasterApplyLightingRig(
    const TArray<ALight*>& LightActors,
    const TArray<ULightComponent*>& LightComponents,
    const TArray<FRenderMasterRigLightState>& Before,
    const TArray<FRenderMasterRigLightState>& After,
    FString& OutError,
    bool bMarkPackageDirty = true);

class FRenderMasterLightingRigAssistant
    : public TSharedFromThis<FRenderMasterLightingRigAssistant>
{
public:
    explicit FRenderMasterLightingRigAssistant(
        TSharedPtr<FRenderMasterWorkflowController> InWorkflowController);
    ~FRenderMasterLightingRigAssistant();

    void Initialize();
    void Shutdown();
    bool StartProposal(
        const FString& Prompt,
        AActor* SubjectActor,
        ACameraActor* CameraActor,
        const TArray<ALight*>& LightActors);
    bool ApplyProposal();
    void RejectProposal();
    void Cancel();

    bool CanStart() const;
    bool CanApply() const;
    bool IsPlanning() const;
    bool GetAppliedRig(
        FRenderMasterAppliedLightingRig& OutRig,
        FString& OutError) const;
    ERenderMasterLightingRigAssistantState GetState() const { return State; }
    FText GetStateText() const;
    FText GetSummaryText() const;
    FText GetLogText() const;
    FLinearColor GetStateColor() const;

private:
    bool Tick(float DeltaTime);
    void FinishProcess();
    void ReadProcessOutput();
    void CloseProcessResources();
    void AppendLog(const FString& Text);
    void Fail(const FString& Error);
    bool WriteContext(
        const FString& Filename,
        AActor* SubjectActor,
        ACameraActor* CameraActor,
        const TArray<ALight*>& LightActors,
        FString& OutError);
    bool RevalidateTargets(FString& OutError) const;
    bool ProposalMatchesCapturedEvidence(FString& OutError) const;

    TSharedPtr<FRenderMasterWorkflowController> WorkflowController;
    FRenderMasterRigSubjectCapture Subject;
    FRenderMasterRigCameraCapture Camera;
    TArray<FRenderMasterRigLightCapture> Lights;
    FRenderMasterLightingRigProposal Proposal;
    ERenderMasterLightingRigAssistantState State = ERenderMasterLightingRigAssistantState::Ready;
    FString ErrorText;
    FString ProcessLog;
    FString ProposalOutputPath;

    FProcHandle ProcessHandle;
    void* StdOutRead = nullptr;
    void* StdOutWrite = nullptr;
    void* StdErrRead = nullptr;
    void* StdErrWrite = nullptr;
    FTSTicker::FDelegateHandle TickHandle;
};
