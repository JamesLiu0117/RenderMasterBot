#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"

class AStaticMeshActor;
class UStaticMeshComponent;
class FRenderMasterWorkflowController;

enum class ERenderMasterPerformanceAssistantState : uint8
{
    Ready,
    Planning,
    Proposed,
    ReviewOnly,
    Unresolved,
    Failed,
    Applied,
    Rejected,
};

struct FRenderMasterPerformanceSnapshot
{
    bool bCastShadow = true;
    float MaxDrawDistanceCm = 0.0f;
};

struct FRenderMasterPerformanceEvidence
{
    FString ActorName;
    FString ActorPath;
    FString ActorClass;
    FString ActorGuid;
    FString ComponentName;
    FString ComponentMobility;
    FString MeshPath;
    int32 LodCount = 0;
    int64 Lod0Triangles = 0;
    int32 MaterialSlotCount = 0;
    bool bNaniteEnabled = false;
    FString CollisionMode;
    bool bComponentTickEnabled = false;
    float BoundsRadiusCm = 0.0f;
    FRenderMasterPerformanceSnapshot Before;
};

struct FRenderMasterPerformanceAction
{
    FRenderMasterPerformanceEvidence Target;
    FRenderMasterPerformanceSnapshot Before;
    FRenderMasterPerformanceSnapshot After;
    FString ChangeSummary;
    FString Rationale;
};

struct FRenderMasterPerformanceProposal
{
    FString ProposalId;
    FString Status;
    FString Request;
    FString Summary;
    TArray<FString> Findings;
    TArray<FRenderMasterPerformanceAction> Actions;
    FString MissingCapabilities;
};

FRenderMasterPerformanceSnapshot RenderMasterSnapshotPerformance(
    const UStaticMeshComponent* Component);

bool RenderMasterCapturePerformanceEvidence(
    AStaticMeshActor* Actor,
    UStaticMeshComponent* Component,
    FRenderMasterPerformanceEvidence& OutEvidence,
    FString& OutError);

bool RenderMasterPerformanceSnapshotsMatch(
    const FRenderMasterPerformanceSnapshot& A,
    const FRenderMasterPerformanceSnapshot& B);

bool RenderMasterParsePerformanceProposalFile(
    const FString& Filename,
    FRenderMasterPerformanceProposal& OutProposal,
    FString& OutError);

bool RenderMasterApplyPerformanceBatch(
    const TArray<AStaticMeshActor*>& Actors,
    const TArray<UStaticMeshComponent*>& Components,
    const TArray<FRenderMasterPerformanceAction>& Actions,
    FString& OutError,
    bool bMarkPackageDirty = true);

class FRenderMasterPerformanceAssistant
    : public TSharedFromThis<FRenderMasterPerformanceAssistant>
{
public:
    explicit FRenderMasterPerformanceAssistant(
        TSharedPtr<FRenderMasterWorkflowController> InWorkflowController);
    ~FRenderMasterPerformanceAssistant();

    void Initialize();
    void Shutdown();
    bool StartProposal(
        const FString& Prompt,
        const TArray<AStaticMeshActor*>& Actors);
    bool ApplyProposal();
    void RejectProposal();
    void Cancel();

    bool CanStart() const;
    bool CanApply() const;
    bool IsPlanning() const;
    ERenderMasterPerformanceAssistantState GetState() const { return State; }
    FText GetStateText() const;
    FText GetSummaryText() const;
    FLinearColor GetStateColor() const;

private:
    bool Tick(float DeltaTime);
    bool WriteSelectionContext(
        const FString& Filename,
        const TArray<AStaticMeshActor*>& Actors,
        FString& OutError);
    bool RevalidateTargets(FString& OutError) const;
    void CompleteProcess();
    void CloseProcessResources();
    void AppendLog(const FString& Line);
    void Fail(const FString& Error);

    TSharedPtr<FRenderMasterWorkflowController> WorkflowController;
    ERenderMasterPerformanceAssistantState State =
        ERenderMasterPerformanceAssistantState::Ready;
    FRenderMasterPerformanceProposal Proposal;
    TArray<FRenderMasterPerformanceEvidence> CapturedTargets;
    TArray<TWeakObjectPtr<AStaticMeshActor>> TargetActors;
    TArray<TWeakObjectPtr<UStaticMeshComponent>> TargetComponents;
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
