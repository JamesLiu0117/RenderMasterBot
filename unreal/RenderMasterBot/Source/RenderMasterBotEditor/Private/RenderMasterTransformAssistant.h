#pragma once

#include "Containers/Ticker.h"
#include "CoreMinimal.h"
#include "HAL/PlatformProcess.h"
#include "Templates/SharedPointer.h"
#include "UObject/WeakObjectPtr.h"

class AActor;
class FRenderMasterWorkflowController;

enum class ERenderMasterTransformAssistantState : uint8
{
    Ready,
    Planning,
    Proposed,
    Unresolved,
    Failed,
    Applied,
    Rejected,
};

struct FRenderMasterTransformSnapshot
{
    FVector Location = FVector::ZeroVector;
    FRotator Rotation = FRotator::ZeroRotator;
    FVector Scale = FVector::OneVector;
};

struct FRenderMasterTransformProposal
{
    FString ProposalId;
    FString Status;
    FString CoordinateSpace = TEXT("world");
    FString Rationale;
    FString MissingCapabilities;
    FString ActorName;
    FString ActorPath;
    FString ActorClass;
    FString ActorGuid;
    FString RootComponentName;
    FString RootMobility;
    FString ChangeSummary;
    FRenderMasterTransformSnapshot Before;
    FRenderMasterTransformSnapshot After;

    static bool Parse(
        const FString& JsonText,
        FRenderMasterTransformProposal& OutProposal,
        FString& OutError);
    static bool LoadFromFile(
        const FString& Filename,
        FRenderMasterTransformProposal& OutProposal,
        FString& OutError);
};

struct FRenderMasterTransformBatchProposal
{
    FString ProposalId;
    FString Status;
    FString CoordinateSpace = TEXT("world");
    FString Rationale;
    FString MissingCapabilities;
    int32 SelectedActorCount = 0;
    TArray<FRenderMasterTransformProposal> Actions;

    static bool Parse(
        const FString& JsonText,
        FRenderMasterTransformBatchProposal& OutProposal,
        FString& OutError);
    static bool LoadFromFile(
        const FString& Filename,
        FRenderMasterTransformBatchProposal& OutProposal,
        FString& OutError);
};

struct FRenderMasterCapturedTransformTarget
{
    TWeakObjectPtr<AActor> Actor;
    FString ActorName;
    FString ActorPath;
    FString ActorClass;
    FString ActorGuid;
    FString RootComponentName;
    FString RootMobility;
    FRenderMasterTransformSnapshot Transform;
};

bool RenderMasterApplyActorTransform(
    AActor* Actor,
    const FRenderMasterTransformSnapshot& After,
    FString& OutError,
    bool bMarkPackageDirty = true);

bool RenderMasterApplyActorTransformBatch(
    const TArray<AActor*>& Actors,
    const TArray<FRenderMasterTransformSnapshot>& Before,
    const TArray<FRenderMasterTransformSnapshot>& After,
    FString& OutError,
    bool bMarkPackageDirty = true);

class FRenderMasterTransformBatchAssistant : public TSharedFromThis<FRenderMasterTransformBatchAssistant>
{
public:
    explicit FRenderMasterTransformBatchAssistant(
        TSharedPtr<FRenderMasterWorkflowController> InWorkflowController);
    ~FRenderMasterTransformBatchAssistant();

    void Initialize();
    void Shutdown();
    bool StartProposal(const FString& Prompt, const TArray<AActor*>& Actors);
    bool ApplyProposal();
    void RejectProposal();
    void Cancel();

    bool CanStart() const;
    bool CanApply() const;
    bool IsPlanning() const;
    ERenderMasterTransformAssistantState GetState() const { return State; }
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
    bool WriteSelectionContext(
        const FString& Filename,
        const TArray<AActor*>& Actors,
        FString& OutError);
    bool RevalidateTargets(FString& OutError) const;

    TSharedPtr<FRenderMasterWorkflowController> WorkflowController;
    TArray<FRenderMasterCapturedTransformTarget> CapturedTargets;
    FRenderMasterTransformBatchProposal Proposal;
    ERenderMasterTransformAssistantState State = ERenderMasterTransformAssistantState::Ready;
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

class FRenderMasterTransformAssistant : public TSharedFromThis<FRenderMasterTransformAssistant>
{
public:
    explicit FRenderMasterTransformAssistant(
        TSharedPtr<FRenderMasterWorkflowController> InWorkflowController);
    ~FRenderMasterTransformAssistant();

    void Initialize();
    void Shutdown();
    bool StartProposal(const FString& Prompt, AActor* Actor);
    bool ApplyProposal();
    void RejectProposal();
    void Cancel();

    bool CanStart() const;
    bool CanApply() const;
    bool IsPlanning() const;
    ERenderMasterTransformAssistantState GetState() const { return State; }
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
    bool WriteActorContext(const FString& Filename, AActor* Actor, FString& OutError);
    bool RevalidateTarget(FString& OutError) const;

    TSharedPtr<FRenderMasterWorkflowController> WorkflowController;
    TWeakObjectPtr<AActor> TargetActor;
    FRenderMasterTransformProposal Proposal;
    ERenderMasterTransformAssistantState State = ERenderMasterTransformAssistantState::Ready;
    FString ErrorText;
    FString ProcessLog;
    FString ProposalOutputPath;
    FString CapturedActorPath;
    FString CapturedActorClass;
    FString CapturedActorGuid;
    FRenderMasterTransformSnapshot CapturedTransform;

    FProcHandle ProcessHandle;
    void* StdOutRead = nullptr;
    void* StdOutWrite = nullptr;
    void* StdErrRead = nullptr;
    void* StdErrWrite = nullptr;
    FTSTicker::FDelegateHandle TickHandle;
};
