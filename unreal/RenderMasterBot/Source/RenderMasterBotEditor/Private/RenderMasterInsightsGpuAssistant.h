#pragma once

#include "Containers/Ticker.h"
#include "CoreMinimal.h"
#include "HAL/PlatformProcess.h"
#include "RenderMasterPerformanceAssistant.h"
#include "Templates/SharedPointer.h"

class FRenderMasterWorkflowController;
class AActor;
class UWorld;

namespace TraceServices
{
class IAnalysisService;
class IAnalysisSession;
}

enum class ERenderMasterInsightsGpuState : uint8
{
    Ready,
    Capturing,
    WarmingVariant,
    WarmingStudyCapture,
    Analyzing,
    Reviewing,
    Complete,
    Unresolved,
    Failed,
    Dismissed,
};

struct FRenderMasterInsightsScopeSample
{
    FString QueueId;
    FString QueueName;
    FString ScopeName;
    double InclusiveMs = 0.0;
    uint32 Depth = 0;
};

struct FRenderMasterInsightsScopeAggregate
{
    FString ScopeId;
    FString QueueId;
    FString QueueName;
    FString Name;
    int32 InstanceCount = 0;
    double TotalInclusiveMs = 0.0;
    double MeanInclusiveMs = 0.0;
    double MaxInclusiveMs = 0.0;
    uint32 MinDepth = 0;
    uint32 MaxDepth = 0;
};

struct FRenderMasterInsightsGpuQueue
{
    FString QueueId;
    FString DisplayName;
    uint32 EngineQueueId = 0;
    uint8 GpuIndex = 0;
    uint8 QueueIndex = 0;
    uint8 QueueType = 0;
    int32 EventCount = 0;
};

struct FRenderMasterInsightsGpuCapture
{
    FString CaptureId;
    FString CapturedAtUtc;
    FString ProjectName;
    FString WorldPath;
    FString CaptureMode;
    double RequestedDurationSeconds = 5.0;
    double CapturedDurationSeconds = 0.0;
    double AnalyzedTraceDurationSeconds = 0.0;
    FIntPoint ViewportSize = FIntPoint::ZeroValue;
    FString GpuName;
    FString TraceFileName;
    int64 TraceFileSizeBytes = 0;
    FString TraceSha256;
    TArray<FString> Channels;
    int32 TotalGpuEventCount = 0;
    TArray<FRenderMasterInsightsGpuQueue> Queues;
    TArray<FRenderMasterInsightsScopeAggregate> Scopes;
};

struct FRenderMasterInsightsGpuReport
{
    FString ReportId;
    FString Status;
    FString Request;
    FString CaptureId;
    FString CaptureSha256;
    FString CaptureFileSha256;
    FString Summary;
    FString PrimaryScopeId;
    TArray<FString> Findings;
    TArray<FString> CitedScopeIds;
    FString MissingCapabilities;
    bool bModifiesEditorScene = true;
};

struct FRenderMasterActorGpuScopeDelta
{
    FString DeltaId;
    FString BaselineScopeId;
    FString VariantScopeId;
    FString QueueName;
    FString ScopeName;
    double BaselineTotalMsPerSecond = 0.0;
    double VariantTotalMsPerSecond = 0.0;
    double BaselineInstancesPerSecond = 0.0;
    double VariantInstancesPerSecond = 0.0;
    double BaselineMinusVariantMsPerSecond = 0.0;
    double RelativeReductionPercent = 0.0;
    FString DirectionWhenHidden;
};

struct FRenderMasterActorGpuImpactReport
{
    FString ReportId;
    FString Status;
    FString Request;
    FString ExperimentId;
    FString ExperimentSha256;
    FString ExperimentFileSha256;
    FString Summary;
    FString PrimaryDeltaId;
    TArray<FString> Findings;
    TArray<FString> CitedDeltaIds;
    FString MissingCapabilities;
    bool bModifiesEditorScene = true;
};

struct FRenderMasterActorGpuStudyAggregate
{
    FString AggregateId;
    FString QueueName;
    FString ScopeName;
    FString Classification;
    FString DominantDirection;
    double MedianVisibleMsPerSecond = 0.0;
    double MedianHiddenMsPerSecond = 0.0;
    double MedianDeltaMsPerSecond = 0.0;
    double DeltaMadMsPerSecond = 0.0;
    double ControlNoiseMsPerSecond = 0.0;
    double SignalExcessMsPerSecond = 0.0;
    double RelativeReductionPercent = 0.0;
    int32 DecreasedVotes = 0;
    int32 IncreasedVotes = 0;
    int32 UnchangedVotes = 0;
};

struct FRenderMasterActorGpuImpactStudyReport
{
    FString ReportId;
    FString Status;
    FString Request;
    FString StudyId;
    FString CaptureSetSha256;
    FString CaptureSetFileSha256;
    FString Summary;
    FString PrimaryAggregateId;
    TArray<FRenderMasterActorGpuStudyAggregate> Aggregates;
    TArray<FString> Findings;
    TArray<FString> CitedAggregateIds;
    FString MissingCapabilities;
    bool bModifiesEditorScene = true;
};

struct FRenderMasterActorGpuStudyCapturePlan
{
    FString Label;
    FString TracePath;
    FString CapturePath;
    bool bVariant = false;
    FRenderMasterInsightsGpuCapture Capture;
};

struct FRenderMasterShadowOptimizationAggregate
{
    FString AggregateId;
    FString QueueName;
    FString ScopeName;
    FString Classification;
    FString DominantDirection;
    double MedianBaselineMsPerSecond = 0.0;
    double MedianCandidateMsPerSecond = 0.0;
    double MedianDeltaMsPerSecond = 0.0;
    double DeltaMadMsPerSecond = 0.0;
    double ControlNoiseMsPerSecond = 0.0;
    double SignalExcessMsPerSecond = 0.0;
    double RelativeReductionPercent = 0.0;
    int32 DecreasedVotes = 0;
    int32 IncreasedVotes = 0;
    int32 UnchangedVotes = 0;
};

struct FRenderMasterShadowOptimizationReport
{
    FString ReportId;
    FString Status;
    FString Decision;
    FString ExperimentId;
    FString ProjectName;
    FString WorldPath;
    FString CaptureMode;
    FIntPoint ViewportSize = FIntPoint::ZeroValue;
    FString GpuName;
    FString CaptureSetSha256;
    FString CaptureSetFileSha256;
    FString Summary;
    FString PrimaryAggregateId;
    TArray<FRenderMasterShadowOptimizationAggregate> Aggregates;
    FRenderMasterPerformanceAction ProposedAction;
    TArray<FString> RejectionReasons;
    bool bHasProposedAction = false;
    bool bRuntimeStateRestored = false;
    bool bModifiesEditorScene = true;
    bool bApprovalRequired = false;
    bool bAutoSave = true;
    bool bUndoSupported = false;
};

bool RenderMasterAggregateInsightsGpuScopes(
    const TArray<FRenderMasterInsightsScopeSample>& Samples,
    int32 MaximumScopes,
    TArray<FRenderMasterInsightsScopeAggregate>& OutScopes,
    FString& OutError);

bool RenderMasterParseInsightsGpuReportFile(
    const FString& Filename,
    FRenderMasterInsightsGpuReport& OutReport,
    FString& OutError);

bool RenderMasterCompareActorGpuCaptures(
    const FRenderMasterInsightsGpuCapture& Baseline,
    const FRenderMasterInsightsGpuCapture& Variant,
    TArray<FRenderMasterActorGpuScopeDelta>& OutDeltas,
    TArray<FString>& OutUnmatchedBaselineScopeIds,
    TArray<FString>& OutUnmatchedVariantScopeIds,
    FString& OutError);

bool RenderMasterParseActorGpuImpactReportFile(
    const FString& Filename,
    FRenderMasterActorGpuImpactReport& OutReport,
    FString& OutError);

bool RenderMasterParseActorGpuImpactStudyReportFile(
    const FString& Filename,
    FRenderMasterActorGpuImpactStudyReport& OutReport,
    FString& OutError);

bool RenderMasterParseShadowOptimizationReportFile(
    const FString& Filename,
    FRenderMasterShadowOptimizationReport& OutReport,
    FString& OutError);

class FRenderMasterInsightsGpuAssistant
    : public TSharedFromThis<FRenderMasterInsightsGpuAssistant>
{
public:
    explicit FRenderMasterInsightsGpuAssistant(
        TSharedPtr<FRenderMasterWorkflowController> InWorkflowController);
    ~FRenderMasterInsightsGpuAssistant();

    void Initialize();
    void Shutdown();
    bool StartReview(const FString& Prompt);
    bool StartActorImpactReview(const FString& Prompt, AActor* SelectedActor);
    bool StartActorImpactStudy(const FString& Prompt, AActor* SelectedActor);
    bool StartActorShadowOptimizationStudy(
        const FString& Prompt,
        AActor* SelectedActor);
    bool ApplyShadowOptimizationProposal();
    void RejectShadowOptimizationProposal();
    void Dismiss();
    void Cancel();
    bool OpenTraceInInsights(FString& OutError) const;
    bool OpenBaselineTraceInInsights(FString& OutError) const;

    bool CanStart() const;
    bool IsBusy() const;
    bool CanOpenTrace() const;
    bool CanOpenBaselineTrace() const;
    bool IsActorImpactExperiment() const { return bActorImpactExperiment; }
    bool IsRepeatableActorImpactStudy() const { return bRepeatableActorImpactStudy; }
    bool IsShadowOptimizationStudy() const { return bShadowOptimizationStudy; }
    bool CanApplyShadowOptimization() const;
    bool CanRejectShadowOptimization() const;
    bool WasShadowOptimizationApplied() const
    {
        return bShadowOptimizationStudy && bShadowOptimizationApplied;
    }
    ERenderMasterInsightsGpuState GetState() const { return State; }
    FText GetTitleText() const;
    FText GetStateText() const;
    FText GetSummaryText() const;
    FLinearColor GetStateColor() const;
    const FRenderMasterInsightsGpuCapture& GetCapture() const { return Capture; }
    const FRenderMasterInsightsGpuReport& GetReport() const { return Report; }
    const FRenderMasterActorGpuImpactStudyReport& GetActorImpactStudyReport() const
    {
        return ActorImpactStudyReport;
    }
    const FString& GetReportOutputPath() const { return ReportOutputPath; }
    const FRenderMasterShadowOptimizationReport& GetShadowOptimizationReport() const
    {
        return ShadowOptimizationReport;
    }

private:
    bool Tick(float DeltaTime);
    bool TickCapture();
    bool TickVariantWarmup();
    bool TickStudyCaptureWarmup();
    bool BeginTraceAnalysis(FString& OutError);
    bool FinalizeTraceAnalysis(FString& OutError);
    bool WriteCapture(const FString& Filename, FString& OutError) const;
    bool BeginActorVariantCapture(FString& OutError);
    bool BeginActorStudyCapture(FString& OutError);
    bool CompleteActorStudyCapture(FString& OutError);
    bool StartRepeatableActorStudy(
        const FString& Prompt,
        AActor* SelectedActor,
        bool bShadowOptimization);
    bool ApplyRepeatableStudyVariant(bool bVariant, FString& OutError);
    bool WriteActorImpactExperiment(FString& OutError);
    bool WriteActorImpactStudyCaptureSet(FString& OutError);
    bool WriteShadowOptimizationCaptureSet(FString& OutError);
    bool StartReviewProcess(FString& OutError);
    bool StartActorImpactReviewProcess(FString& OutError);
    bool StartActorImpactStudyReviewProcess(FString& OutError);
    bool StartShadowOptimizationProcess(FString& OutError);
    void CompleteProcess();
    void CompleteActorImpactProcess();
    void CompleteActorImpactStudyProcess();
    void CompleteShadowOptimizationProcess();
    bool StopOwnedTrace(FString& OutError);
    bool RestoreImpactActor(FString& OutError);
    bool OpenTracePathInInsights(const FString& InTracePath, FString& OutError) const;
    void CloseProcessResources();
    void AppendLog(const FString& Line);
    void Fail(const FString& Error);

    TSharedPtr<FRenderMasterWorkflowController> WorkflowController;
    ERenderMasterInsightsGpuState State = ERenderMasterInsightsGpuState::Ready;
    FRenderMasterInsightsGpuCapture Capture;
    FRenderMasterInsightsGpuReport Report;
    FRenderMasterInsightsGpuCapture BaselineCapture;
    FRenderMasterActorGpuImpactReport ActorImpactReport;
    FRenderMasterActorGpuImpactStudyReport ActorImpactStudyReport;
    FRenderMasterShadowOptimizationReport ShadowOptimizationReport;
    TArray<FRenderMasterActorGpuScopeDelta> ActorImpactDeltas;
    TArray<FString> UnmatchedBaselineScopeIds;
    TArray<FString> UnmatchedVariantScopeIds;
    TArray<FRenderMasterActorGpuStudyCapturePlan> ActorImpactStudyCaptures;
    TWeakObjectPtr<UWorld> CaptureWorld;
    TWeakObjectPtr<AActor> ImpactEditorActor;
    TWeakObjectPtr<AActor> ImpactRuntimeActor;
    TWeakObjectPtr<UStaticMeshComponent> ShadowEditorComponent;
    TWeakObjectPtr<UStaticMeshComponent> ShadowRuntimeComponent;
    FRenderMasterPerformanceEvidence ShadowTargetEvidence;
    TSharedPtr<TraceServices::IAnalysisService> AnalysisService;
    TSharedPtr<const TraceServices::IAnalysisSession> AnalysisSession;
    FString ReviewRequest;
    FString RequestDirectory;
    FString PromptPath;
    FString TracePath;
    FString CapturePath;
    FString CaptureFileSha256;
    FString BaselineTracePath;
    FString BaselineCapturePath;
    FString BaselineCaptureFileSha256;
    FString ActorImpactExperimentId;
    FString ActorImpactExperimentPath;
    FString ActorImpactExperimentFileSha256;
    FString ActorImpactStudyId;
    FString ActorImpactStudyCaptureSetPath;
    FString ActorImpactStudyCaptureSetFileSha256;
    FString ShadowOptimizationExperimentId;
    FString ShadowOptimizationCaptureSetPath;
    FString ShadowOptimizationCaptureSetFileSha256;
    FString ImpactEditorActorPath;
    FString ImpactRuntimeActorPath;
    FString ImpactActorLabel;
    FString ImpactActorClass;
    int32 ImpactPrimitiveComponentCount = 0;
    FString ReportOutputPath;
    FString ErrorText;
    FString ProcessLog;
    double CaptureStartedSeconds = 0.0;
    double VariantWarmupStartedSeconds = 0.0;
    int32 ActorImpactStudyCaptureIndex = INDEX_NONE;
    bool bActorImpactExperiment = false;
    bool bRepeatableActorImpactStudy = false;
    bool bShadowOptimizationStudy = false;
    bool bShadowOptimizationApplied = false;
    bool bShadowOptimizationRejected = false;
    bool bCapturingActorVariant = false;
    bool bImpactActorHidden = false;
    bool bOriginalImpactActorHidden = false;
    bool bOriginalRuntimeCastShadow = true;
    bool bShadowCandidateActive = false;
    bool bOwnsTrace = false;
    FProcHandle ProcessHandle;
    void* StdOutRead = nullptr;
    void* StdOutWrite = nullptr;
    void* StdErrRead = nullptr;
    void* StdErrWrite = nullptr;
    FTSTicker::FDelegateHandle TickHandle;
};
