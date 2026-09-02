#pragma once

#include "Containers/Ticker.h"
#include "CoreMinimal.h"
#include "HAL/PlatformProcess.h"
#include "RenderMasterInsightsGpuAssistant.h"
#include "RenderMasterLevelProfilingAssistant.h"
#include "RenderMasterPerformanceAssistant.h"
#include "Templates/SharedPointer.h"

class AStaticMeshActor;
class FRenderMasterWorkflowController;
class UStaticMeshComponent;

enum class ERenderMasterShadowVerificationState : uint8
{
    Ready,
    Armed,
    Profiling,
    Analyzing,
    RetainRecommended,
    RollbackRecommended,
    Unresolved,
    Retained,
    RolledBack,
    Failed,
    Cancelled,
    Dismissed,
};

struct FRenderMasterShadowVerificationMetrics
{
    double BaselineFrameP95Ms = 0.0;
    double CandidateFrameP95Ms = 0.0;
    double FrameP95DeltaMs = 0.0;
    TOptional<double> FrameP95DeltaPercent;
    double BaselineFrameBudgetMissFraction = 0.0;
    double CandidateFrameBudgetMissFraction = 0.0;
    double FrameBudgetMissDeltaFraction = 0.0;
    TOptional<double> BaselineGpuP95Ms;
    TOptional<double> CandidateGpuP95Ms;
    TOptional<double> GpuP95DeltaMs;
    TOptional<double> GpuP95DeltaPercent;
    FString TargetScopeQueueName;
    FString TargetScopeName;
    TOptional<double> BaselineTargetScopeMsPerSecond;
    TOptional<double> CandidateTargetScopeMsPerSecond;
    TOptional<double> TargetScopeDeltaMsPerSecond;
    TOptional<double> TargetScopeDeltaPercent;
};

struct FRenderMasterShadowVerificationReport
{
    FString ReportId;
    FString Status;
    FString Decision;
    FString ShadowReportId;
    FString ShadowExperimentId;
    FString ShadowReportFileSha256;
    FString BaselineReportId;
    FString BaselineProfilingRunId;
    FString BaselineReportFileSha256;
    FString CandidateReportId;
    FString CandidateProfilingRunId;
    FString CandidateReportFileSha256;
    bool bWorkloadMatches = false;
    bool bTargetTransitionMatches = false;
    bool bSourcesAreDistinct = false;
    FRenderMasterShadowVerificationMetrics Metrics;
    FString Summary;
    TArray<FString> DecisionReasons;
    bool bFormalChangeApplied = false;
    bool bModifiesEditorScene = true;
    bool bRollbackRequiresApproval = false;
    bool bRollbackUndoSupported = false;
    bool bAutoSave = true;
};

bool RenderMasterParseShadowVerificationReportFile(
    const FString& Filename,
    FRenderMasterShadowVerificationReport& OutReport,
    FString& OutError);

class FRenderMasterShadowOptimizationVerificationAssistant
    : public TSharedFromThis<FRenderMasterShadowOptimizationVerificationAssistant>
{
public:
    FRenderMasterShadowOptimizationVerificationAssistant(
        TSharedPtr<FRenderMasterWorkflowController> InWorkflowController,
        TSharedPtr<FRenderMasterLevelProfilingAssistant> InLevelProfilingAssistant);
    ~FRenderMasterShadowOptimizationVerificationAssistant();

    void Initialize();
    void Shutdown();
    bool ArmAfterApply(
        AStaticMeshActor* Actor,
        UStaticMeshComponent* Component,
        const FRenderMasterPerformanceAction& AppliedAction,
        const FString& BaselineReportPath,
        const FRenderMasterLevelProfilingReport& BaselineReport,
        const FString& ShadowReportPath,
        const FRenderMasterShadowOptimizationReport& ShadowReport);
    bool StartVerification(const FString& Prompt);
    bool KeepAppliedOptimization();
    bool RollbackAppliedOptimization();
    void Cancel();
    void Dismiss();

    bool CanStartVerification() const;
    bool CanKeepAppliedOptimization() const;
    bool CanRollbackAppliedOptimization() const;
    bool IsBusy() const;
    ERenderMasterShadowVerificationState GetState() const { return State; }
    const FRenderMasterShadowVerificationReport& GetReport() const
    {
        return Report;
    }
    FText GetStateText() const;
    FText GetSummaryText() const;
    FLinearColor GetStateColor() const;

private:
    bool Tick(float DeltaTime);
    bool StartAnalysis(FString& OutError);
    void CompleteAnalysisProcess();
    bool RevalidateAppliedTarget(FString& OutError) const;
    void CloseProcessResources();
    void AppendLog(const FString& Line);
    void Fail(const FString& Error);

    TSharedPtr<FRenderMasterWorkflowController> WorkflowController;
    TSharedPtr<FRenderMasterLevelProfilingAssistant> LevelProfilingAssistant;
    ERenderMasterShadowVerificationState State =
        ERenderMasterShadowVerificationState::Ready;
    FRenderMasterShadowVerificationReport Report;
    TWeakObjectPtr<AStaticMeshActor> TargetActor;
    TWeakObjectPtr<UStaticMeshComponent> TargetComponent;
    FRenderMasterPerformanceAction AppliedAction;
    FRenderMasterLevelProfilingReport BaselineReport;
    FRenderMasterLevelProfilingReport CandidateReport;
    FRenderMasterShadowOptimizationReport ShadowReport;
    FString VerificationId;
    FString BaselineReportPath;
    FString BaselineReportFileSha256;
    FString ShadowReportPath;
    FString ShadowReportFileSha256;
    FString CandidateReportPath;
    FString CandidateReportFileSha256;
    FString RequestDirectory;
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
