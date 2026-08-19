#pragma once

#include "Containers/Ticker.h"
#include "CoreMinimal.h"
#include "HAL/PlatformProcess.h"
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
    void Dismiss();
    void Cancel();
    bool OpenTraceInInsights(FString& OutError) const;
    bool OpenBaselineTraceInInsights(FString& OutError) const;

    bool CanStart() const;
    bool IsBusy() const;
    bool CanOpenTrace() const;
    bool CanOpenBaselineTrace() const;
    bool IsActorImpactExperiment() const { return bActorImpactExperiment; }
    ERenderMasterInsightsGpuState GetState() const { return State; }
    FText GetTitleText() const;
    FText GetStateText() const;
    FText GetSummaryText() const;
    FLinearColor GetStateColor() const;
    const FRenderMasterInsightsGpuCapture& GetCapture() const { return Capture; }

private:
    bool Tick(float DeltaTime);
    bool TickCapture();
    bool TickVariantWarmup();
    bool BeginTraceAnalysis(FString& OutError);
    bool FinalizeTraceAnalysis(FString& OutError);
    bool WriteCapture(const FString& Filename, FString& OutError) const;
    bool BeginActorVariantCapture(FString& OutError);
    bool WriteActorImpactExperiment(FString& OutError);
    bool StartReviewProcess(FString& OutError);
    bool StartActorImpactReviewProcess(FString& OutError);
    void CompleteProcess();
    void CompleteActorImpactProcess();
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
    TArray<FRenderMasterActorGpuScopeDelta> ActorImpactDeltas;
    TArray<FString> UnmatchedBaselineScopeIds;
    TArray<FString> UnmatchedVariantScopeIds;
    TWeakObjectPtr<UWorld> CaptureWorld;
    TWeakObjectPtr<AActor> ImpactEditorActor;
    TWeakObjectPtr<AActor> ImpactRuntimeActor;
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
    bool bActorImpactExperiment = false;
    bool bCapturingActorVariant = false;
    bool bImpactActorHidden = false;
    bool bOriginalImpactActorHidden = false;
    bool bOwnsTrace = false;
    FProcHandle ProcessHandle;
    void* StdOutRead = nullptr;
    void* StdOutWrite = nullptr;
    void* StdErrRead = nullptr;
    void* StdErrWrite = nullptr;
    FTSTicker::FDelegateHandle TickHandle;
};
