#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"
#include "RenderMasterPerformanceAssistant.h"

class AStaticMeshActor;
class UStaticMeshComponent;
class FRenderMasterWorkflowController;

enum class ERenderMasterLevelAuditState : uint8
{
    Ready,
    Analyzing,
    Proposed,
    ReviewOnly,
    Unresolved,
    Failed,
    Applied,
    Rejected,
};

struct FRenderMasterLevelAuditStatistics
{
    int32 CapturedActorCount = 0;
    int32 ExcludedActorCount = 0;
    int64 TotalLod0Triangles = 0;
    int64 TotalMaterialSlots = 0;
    int32 ActorsWithoutNaniteCount = 0;
    int32 SingleLodActorCount = 0;
    int32 TickingComponentCount = 0;
    int32 ShadowCastingActorCount = 0;
    int32 UnboundedDrawDistanceCount = 0;
    int32 QueryAndPhysicsCollisionCount = 0;
    int32 HighPriorityActorCount = 0;
};

struct FRenderMasterLevelAuditAssessment
{
    FString AssessmentId;
    FString ActorPath;
    FString ActorName;
    int32 RiskScore = 0;
    FString RiskBand;
    FString RecommendedNextTest;
    TArray<FString> Reasons;
};

struct FRenderMasterLevelAuditAction
{
    FString AssessmentId;
    FRenderMasterPerformanceAction PerformanceAction;
};

struct FRenderMasterLevelAuditReport
{
    FString AuditId;
    FString Status;
    FString Request;
    FString ContextSha256;
    FString Summary;
    FString PrimaryAssessmentId;
    TArray<FRenderMasterPerformanceEvidence> ContextActors;
    FRenderMasterLevelAuditStatistics Statistics;
    TArray<FRenderMasterLevelAuditAssessment> Assessments;
    TArray<FString> Findings;
    TArray<FRenderMasterLevelAuditAction> Actions;
    FString MissingCapabilities;
};

int32 RenderMasterComputeLevelAuditRiskScore(
    const FRenderMasterPerformanceEvidence& Evidence);

bool RenderMasterParseLevelAuditReportFile(
    const FString& Filename,
    FRenderMasterLevelAuditReport& OutReport,
    FString& OutError);

class FRenderMasterLevelAuditAssistant
    : public TSharedFromThis<FRenderMasterLevelAuditAssistant>
{
public:
    explicit FRenderMasterLevelAuditAssistant(
        TSharedPtr<FRenderMasterWorkflowController> InWorkflowController);
    ~FRenderMasterLevelAuditAssistant();

    void Initialize();
    void Shutdown();
    bool StartAudit(const FString& Prompt);
    bool ApplyActions();
    void Reject();
    void Cancel();

    bool CanStart() const;
    bool CanApply() const;
    bool IsBusy() const;
    ERenderMasterLevelAuditState GetState() const { return State; }
    FText GetStateText() const;
    FText GetSummaryText() const;
    FLinearColor GetStateColor() const;
    const FRenderMasterLevelAuditReport& GetReport() const { return Report; }
    const FString& GetReportOutputPath() const { return ReportOutputPath; }
    AStaticMeshActor* FindCapturedActorByPath(const FString& ActorPath) const;

private:
    bool Tick(float DeltaTime);
    bool CaptureAndWriteContext(
        const FString& Filename,
        const FString& AuditId,
        FString& OutError);
    bool RevalidateActions(
        TArray<AStaticMeshActor*>& OutActors,
        TArray<UStaticMeshComponent*>& OutComponents,
        TArray<FRenderMasterPerformanceAction>& OutActions,
        FString& OutError) const;
    void CompleteProcess();
    void CloseProcessResources();
    void AppendLog(const FString& Line);
    void Fail(const FString& Error);

    TSharedPtr<FRenderMasterWorkflowController> WorkflowController;
    ERenderMasterLevelAuditState State = ERenderMasterLevelAuditState::Ready;
    FRenderMasterLevelAuditReport Report;
    TArray<FRenderMasterPerformanceEvidence> CapturedTargets;
    TArray<TWeakObjectPtr<AStaticMeshActor>> TargetActors;
    TArray<TWeakObjectPtr<UStaticMeshComponent>> TargetComponents;
    FString ExpectedAuditId;
    FString ReportOutputPath;
    FString ErrorText;
    FString ProcessLog;
    FProcHandle ProcessHandle;
    void* StdOutRead = nullptr;
    void* StdOutWrite = nullptr;
    void* StdErrRead = nullptr;
    void* StdErrWrite = nullptr;
    FTSTicker::FDelegateHandle TickHandle;
};
