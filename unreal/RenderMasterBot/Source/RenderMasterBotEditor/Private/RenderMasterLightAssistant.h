#pragma once

#include "Containers/Ticker.h"
#include "CoreMinimal.h"
#include "HAL/PlatformProcess.h"
#include "Templates/SharedPointer.h"
#include "UObject/WeakObjectPtr.h"

class ALight;
class FRenderMasterWorkflowController;
class ULightComponent;

enum class ERenderMasterLightAssistantState : uint8
{
    Ready,
    Planning,
    Proposed,
    Unresolved,
    Failed,
    Applied,
    Rejected,
};

struct FRenderMasterLightSnapshot
{
    FRotator Rotation = FRotator::ZeroRotator;
    double Intensity = 0.0;
    FString IntensityUnit;
    FLinearColor Color = FLinearColor::White;
    bool bUseTemperature = false;
    double TemperatureKelvin = 6500.0;
    bool bCastShadows = true;
    TOptional<double> AttenuationRadiusCm;
    TOptional<double> InnerConeDeg;
    TOptional<double> OuterConeDeg;
};

struct FRenderMasterLightProposal
{
    FString ProposalId;
    FString Status;
    FString Rationale;
    FString MissingCapabilities;
    FString ActorName;
    FString ActorPath;
    FString ActorClass;
    FString ActorGuid;
    FString ComponentName;
    FString ComponentMobility;
    FString LightKind;
    FString ChangeSummary;
    FRenderMasterLightSnapshot Before;
    FRenderMasterLightSnapshot After;

    static bool Parse(
        const FString& JsonText,
        FRenderMasterLightProposal& OutProposal,
        FString& OutError);
    static bool LoadFromFile(
        const FString& Filename,
        FRenderMasterLightProposal& OutProposal,
        FString& OutError);
};

struct FRenderMasterLightBatchProposal
{
    FString ProposalId;
    FString Status;
    FString Rationale;
    FString MissingCapabilities;
    int32 SelectedLightCount = 0;
    TArray<FRenderMasterLightProposal> Actions;

    static bool Parse(
        const FString& JsonText,
        FRenderMasterLightBatchProposal& OutProposal,
        FString& OutError);
    static bool LoadFromFile(
        const FString& Filename,
        FRenderMasterLightBatchProposal& OutProposal,
        FString& OutError);
};

struct FRenderMasterCapturedLightTarget
{
    TWeakObjectPtr<ALight> Actor;
    TWeakObjectPtr<ULightComponent> Component;
    FString ActorName;
    FString ActorPath;
    FString ActorClass;
    FString ActorGuid;
    FString ComponentName;
    FString ComponentMobility;
    FString LightKind;
    FRenderMasterLightSnapshot Light;
};

FString RenderMasterGetLightKind(const ULightComponent* LightComponent);
FString RenderMasterGetLightUnit(const ULightComponent* LightComponent);
FRenderMasterLightSnapshot RenderMasterSnapshotLight(
    const ALight* LightActor,
    const ULightComponent* LightComponent);
bool RenderMasterLightSnapshotsMatch(
    const FRenderMasterLightSnapshot& A,
    const FRenderMasterLightSnapshot& B);
bool RenderMasterIsBoundedLightSnapshot(
    const FRenderMasterLightSnapshot& Snapshot,
    const FString& LightKind);
bool RenderMasterSetLightSnapshot(
    ALight* LightActor,
    ULightComponent* LightComponent,
    const FRenderMasterLightSnapshot& Before,
    const FRenderMasterLightSnapshot& After);

bool RenderMasterApplyLightProperties(
    ALight* LightActor,
    ULightComponent* LightComponent,
    const FRenderMasterLightSnapshot& Before,
    const FRenderMasterLightSnapshot& After,
    FString& OutError,
    bool bMarkPackageDirty = true);

bool RenderMasterApplyLightPropertiesBatch(
    const TArray<ALight*>& LightActors,
    const TArray<ULightComponent*>& LightComponents,
    const TArray<FRenderMasterLightSnapshot>& Before,
    const TArray<FRenderMasterLightSnapshot>& After,
    FString& OutError,
    bool bMarkPackageDirty = true);

class FRenderMasterLightBatchAssistant : public TSharedFromThis<FRenderMasterLightBatchAssistant>
{
public:
    explicit FRenderMasterLightBatchAssistant(
        TSharedPtr<FRenderMasterWorkflowController> InWorkflowController);
    ~FRenderMasterLightBatchAssistant();

    void Initialize();
    void Shutdown();
    bool StartProposal(const FString& Prompt, const TArray<ALight*>& LightActors);
    bool ApplyProposal();
    void RejectProposal();
    void Cancel();

    bool CanStart() const;
    bool CanApply() const;
    bool IsPlanning() const;
    ERenderMasterLightAssistantState GetState() const { return State; }
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
    bool WriteLightSelectionContext(
        const FString& Filename,
        const TArray<ALight*>& LightActors,
        FString& OutError);
    bool RevalidateTargets(FString& OutError) const;

    TSharedPtr<FRenderMasterWorkflowController> WorkflowController;
    TArray<FRenderMasterCapturedLightTarget> CapturedTargets;
    FRenderMasterLightBatchProposal Proposal;
    ERenderMasterLightAssistantState State = ERenderMasterLightAssistantState::Ready;
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

class FRenderMasterLightAssistant : public TSharedFromThis<FRenderMasterLightAssistant>
{
public:
    explicit FRenderMasterLightAssistant(
        TSharedPtr<FRenderMasterWorkflowController> InWorkflowController);
    ~FRenderMasterLightAssistant();

    void Initialize();
    void Shutdown();
    bool StartProposal(const FString& Prompt, ALight* LightActor);
    bool ApplyProposal();
    void RejectProposal();
    void Cancel();

    bool CanStart() const;
    bool CanApply() const;
    bool IsPlanning() const;
    ERenderMasterLightAssistantState GetState() const { return State; }
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
    bool WriteLightContext(const FString& Filename, ALight* LightActor, FString& OutError);
    bool RevalidateTarget(FString& OutError) const;

    TSharedPtr<FRenderMasterWorkflowController> WorkflowController;
    TWeakObjectPtr<ALight> TargetLightActor;
    TWeakObjectPtr<ULightComponent> TargetLightComponent;
    FRenderMasterLightProposal Proposal;
    ERenderMasterLightAssistantState State = ERenderMasterLightAssistantState::Ready;
    FString ErrorText;
    FString ProcessLog;
    FString ProposalOutputPath;
    FString CapturedActorPath;
    FString CapturedActorClass;
    FString CapturedActorGuid;
    FString CapturedComponentName;
    FString CapturedLightKind;
    FRenderMasterLightSnapshot CapturedLight;

    FProcHandle ProcessHandle;
    void* StdOutRead = nullptr;
    void* StdOutWrite = nullptr;
    void* StdErrRead = nullptr;
    void* StdErrWrite = nullptr;
    FTSTicker::FDelegateHandle TickHandle;
};
