#pragma once

#include "Containers/Ticker.h"
#include "CoreMinimal.h"
#include "HAL/PlatformProcess.h"
#include "Templates/SharedPointer.h"
#include "UObject/WeakObjectPtr.h"

class FRenderMasterWorkflowController;
class UStaticMeshComponent;

enum class ERenderMasterMaterialAssistantState : uint8
{
    Ready,
    Searching,
    Proposed,
    Importing,
    Unresolved,
    Failed,
    Applied,
    Rejected,
};

enum class ERenderMasterMaterialProcess : uint8
{
    None,
    ProjectProposal,
    ExternalProposal,
    ExternalImport,
};

struct FRenderMasterMaterialProposal
{
    FString ProposalId;
    FString Status;
    FString Rationale;
    FString MissingCapabilities;
    FString ActorPath;
    FString ComponentName;
    FString MeshPath;
    int32 SlotIndex = INDEX_NONE;
    FString SlotName;
    FString CurrentMaterialPath;
    FString MaterialAssetId;
    FString MaterialDisplayName;
    FString MaterialPath;
    double Similarity = 0.0;

    static bool Parse(
        const FString& JsonText,
        FRenderMasterMaterialProposal& OutProposal,
        FString& OutError);
    static bool LoadFromFile(
        const FString& Filename,
        FRenderMasterMaterialProposal& OutProposal,
        FString& OutError);
};

struct FRenderMasterExternalMaterialProposal
{
    FString ProposalId;
    FString Status;
    FString ProviderCredit;
    FString ProviderAssetId;
    FString DisplayName;
    FString Description;
    FString SourceUrl;
    FString License;
    FString LicenseUrl;
    FString Resolution;
    FString ImageFormat;
    FString ImportProposalPath;
    FString ImportProposalSha256;
    FString DestinationPath;
    FString MaterialName;
    TArray<FString> PlannedAssetPaths;

    static bool Parse(
        const FString& JsonText,
        FRenderMasterExternalMaterialProposal& OutProposal,
        FString& OutError);
    static bool LoadFromFile(
        const FString& Filename,
        FRenderMasterExternalMaterialProposal& OutProposal,
        FString& OutError);
};

class FRenderMasterMaterialAssistant : public TSharedFromThis<FRenderMasterMaterialAssistant>
{
public:
    explicit FRenderMasterMaterialAssistant(
        TSharedPtr<FRenderMasterWorkflowController> InWorkflowController);
    ~FRenderMasterMaterialAssistant();

    void Initialize();
    void Shutdown();
    bool StartProposal(
        const FString& Prompt,
        UStaticMeshComponent* Component,
        int32 TargetSlotIndex);
    bool StartExternalProposal(
        const FString& Prompt,
        UStaticMeshComponent* Component,
        int32 TargetSlotIndex);
    bool ApplyProposal();
    void RejectProposal();
    void Cancel();

    bool CanStart() const;
    bool CanApply() const;
    bool IsSearching() const;
    ERenderMasterMaterialAssistantState GetState() const { return State; }
    FText GetStateText() const;
    FText GetSummaryText() const;
    FText GetApprovalButtonText() const;
    FText GetLogText() const;
    FLinearColor GetStateColor() const;

private:
    bool Tick(float DeltaTime);
    void FinishProcess();
    void ReadProcessOutput();
    void CloseProcessResources();
    void AppendLog(const FString& Text);
    void Fail(const FString& Error);
    bool LaunchPythonProcess(
        const FString& Arguments,
        const FString& Activity,
        ERenderMasterMaterialAssistantState ActiveState);
    bool StartExternalImport();
    bool ApplyLoadedMaterial();
    bool WriteSelectionContext(
        const FString& Filename,
        UStaticMeshComponent* Component,
        int32 TargetSlotIndex,
        FString& OutError);
    bool RevalidateTarget(FString& OutError) const;

    TSharedPtr<FRenderMasterWorkflowController> WorkflowController;
    TWeakObjectPtr<UStaticMeshComponent> TargetComponent;
    FRenderMasterMaterialProposal Proposal;
    FRenderMasterExternalMaterialProposal ExternalProposal;
    ERenderMasterMaterialAssistantState State = ERenderMasterMaterialAssistantState::Ready;
    ERenderMasterMaterialProcess ProcessKind = ERenderMasterMaterialProcess::None;
    FString ErrorText;
    FString ProcessLog;
    FString ProposalOutputPath;
    FString RequestDirectory;
    FString ExternalExecutionOutputPath;
    FString CapturedActorPath;
    FString CapturedComponentName;
    FString CapturedMeshPath;
    FString CapturedCurrentMaterialPath;
    FString CapturedSlotName;
    int32 CapturedSlotIndex = INDEX_NONE;
    bool bExternalProposal = false;

    FProcHandle ProcessHandle;
    void* StdOutRead = nullptr;
    void* StdOutWrite = nullptr;
    void* StdErrRead = nullptr;
    void* StdErrWrite = nullptr;
    FTSTicker::FDelegateHandle TickHandle;
};
