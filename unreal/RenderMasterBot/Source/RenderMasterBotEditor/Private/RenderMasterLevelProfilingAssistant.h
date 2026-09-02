#pragma once

#include "Containers/Ticker.h"
#include "CoreMinimal.h"
#include "HAL/PlatformProcess.h"
#include "Templates/SharedPointer.h"

class AStaticMeshActor;
class FRenderMasterInsightsGpuAssistant;
class FRenderMasterLevelAuditAssistant;
class FRenderMasterRuntimePerformanceAssistant;
class FRenderMasterWorkflowController;
class UWorld;

enum class ERenderMasterLevelProfilingState : uint8
{
    Ready,
    StaticAudit,
    RuntimeBaseline,
    GpuScopeTrace,
    ActorStudy,
    Synthesizing,
    Complete,
    Partial,
    Failed,
    Cancelled,
};

struct FRenderMasterLevelProfilingActorFinding
{
    FString AssessmentId;
    FString ActorPath;
    FString ActorName;
    int32 StaticRiskScore = 0;
    FString StaticRiskBand;
    FString Outcome;
    FString PrimaryAggregateId;
    FString ScopeName;
    TOptional<double> MedianDeltaMsPerSecond;
    TOptional<double> ControlNoiseMsPerSecond;
    FString Conclusion;
};

struct FRenderMasterLevelProfilingReport
{
    FString ReportId;
    FString Status;
    FString ProfilingRunId;
    FString LevelAuditId;
    FString RuntimeCaptureId;
    FString GpuCaptureId;
    FString ProjectName;
    FString WorldPath;
    FString CaptureMode;
    FIntPoint ViewportSize = FIntPoint::ZeroValue;
    FString GpuName;
    TArray<FString> ActorStudyIds;
    FString Summary;
    FString PrimaryBottleneck;
    double FrameP95Ms = 0.0;
    double FrameBudgetMissFraction = 0.0;
    TOptional<double> GpuP95Ms;
    FString PrimaryGpuScopeId;
    FString PrimaryGpuScopeName;
    double PrimaryGpuScopeMsPerSecond = 0.0;
    int32 MeasuredActorCount = 0;
    int32 SkippedActorCount = 0;
    int32 StableActorCandidateCount = 0;
    TArray<FRenderMasterLevelProfilingActorFinding> ActorFindings;
    TArray<FString> Limitations;
    bool bRuntimeStateRestored = false;
    bool bModifiesEditorScene = true;
};

bool RenderMasterParseLevelProfilingReportFile(
    const FString& Filename,
    FRenderMasterLevelProfilingReport& OutReport,
    FString& OutError);

class FRenderMasterLevelProfilingAssistant
    : public TSharedFromThis<FRenderMasterLevelProfilingAssistant>
{
public:
    FRenderMasterLevelProfilingAssistant(
        TSharedPtr<FRenderMasterWorkflowController> InWorkflowController,
        TSharedPtr<FRenderMasterLevelAuditAssistant> InLevelAuditAssistant,
        TSharedPtr<FRenderMasterRuntimePerformanceAssistant> InRuntimeAssistant,
        TSharedPtr<FRenderMasterInsightsGpuAssistant> InInsightsGpuAssistant);
    ~FRenderMasterLevelProfilingAssistant();

    void Initialize();
    void Shutdown();
    bool Start(const FString& Prompt);
    void Cancel();

    bool CanStart() const;
    bool IsBusy() const;
    ERenderMasterLevelProfilingState GetState() const { return State; }
    const FRenderMasterLevelProfilingReport& GetReport() const { return Report; }
    const FString& GetReportOutputPath() const { return ReportOutputPath; }
    FText GetStateText() const;
    FText GetSummaryText() const;
    FLinearColor GetStateColor() const;

private:
    struct FCandidate
    {
        FString AssessmentId;
        FString ActorPath;
        FString ActorName;
    };

    struct FSkip
    {
        FString AssessmentId;
        FString Reason;
    };

    bool Tick(float DeltaTime);
    bool AdvanceAfterStaticAudit();
    bool AdvanceAfterRuntimeBaseline();
    bool AdvanceAfterGpuScopeTrace();
    bool AdvanceAfterActorStudy();
    bool StartNextActorStudy();
    bool StartSynthesis(FString& OutError);
    bool WriteSkipManifest(const FString& Filename, FString& OutError) const;
    void CompleteSynthesisProcess();
    void CloseProcessResources();
    void AppendLog(const FString& Line);
    void Fail(const FString& Error);

    TSharedPtr<FRenderMasterWorkflowController> WorkflowController;
    TSharedPtr<FRenderMasterLevelAuditAssistant> LevelAuditAssistant;
    TSharedPtr<FRenderMasterRuntimePerformanceAssistant> RuntimeAssistant;
    TSharedPtr<FRenderMasterInsightsGpuAssistant> InsightsGpuAssistant;
    ERenderMasterLevelProfilingState State = ERenderMasterLevelProfilingState::Ready;
    FRenderMasterLevelProfilingReport Report;
    TWeakObjectPtr<UWorld> PlayWorld;
    FString Request;
    FString ProfilingRunId;
    FString CreatedAtUtc;
    FString RequestDirectory;
    FString PromptPath;
    FString LevelAuditReportPath;
    FString RuntimeReportPath;
    FString GpuReportPath;
    TArray<FString> ActorStudyReportPaths;
    FString ExpectedLevelAuditId;
    FString ExpectedRuntimeCaptureId;
    FString ExpectedGpuCaptureId;
    TArray<FString> ExpectedActorStudyIds;
    FString SkipManifestPath;
    FString ReportOutputPath;
    FString ErrorText;
    FString ProcessLog;
    TArray<FCandidate> Candidates;
    TArray<FSkip> Skips;
    int32 CandidateIndex = 0;
    FProcHandle ProcessHandle;
    void* StdOutRead = nullptr;
    void* StdOutWrite = nullptr;
    void* StdErrRead = nullptr;
    void* StdErrWrite = nullptr;
    FTSTicker::FDelegateHandle TickHandle;
};
