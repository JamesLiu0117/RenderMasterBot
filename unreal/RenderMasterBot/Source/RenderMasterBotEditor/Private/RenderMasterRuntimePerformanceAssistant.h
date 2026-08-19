#pragma once

#include "Containers/Ticker.h"
#include "CoreMinimal.h"
#include "HAL/PlatformProcess.h"
#include "Templates/SharedPointer.h"

class FRenderMasterWorkflowController;
class UWorld;

enum class ERenderMasterRuntimePerformanceState : uint8
{
    Ready,
    Capturing,
    Reviewing,
    Complete,
    Unresolved,
    Failed,
    Dismissed,
};

struct FRenderMasterRuntimeFrameSample
{
    int32 FrameIndex = 0;
    double FrameTimeMs = 0.0;
    double GameThreadMs = 0.0;
    double RenderThreadMs = 0.0;
    TOptional<double> RhiThreadMs;
    TOptional<double> GpuMs;
};

struct FRenderMasterRuntimeTimingSummary
{
    bool bAvailable = false;
    int32 SampleCount = 0;
    double MeanMs = 0.0;
    double P50Ms = 0.0;
    double P95Ms = 0.0;
    double MaxMs = 0.0;
};

struct FRenderMasterRuntimeCapture
{
    FString CaptureId;
    FString CapturedAtUtc;
    FString ProjectName;
    FString WorldPath;
    FString CaptureMode;
    double TargetFps = 60.0;
    double TargetFrameMs = 1000.0 / 60.0;
    int32 WarmupFrames = 30;
    int32 SampleCount = 120;
    FIntPoint ViewportSize = FIntPoint::ZeroValue;
    FString GpuName;
    TArray<FRenderMasterRuntimeFrameSample> Samples;
    FRenderMasterRuntimeTimingSummary FrameTime;
    FRenderMasterRuntimeTimingSummary GameThread;
    FRenderMasterRuntimeTimingSummary RenderThread;
    FRenderMasterRuntimeTimingSummary RhiThread;
    FRenderMasterRuntimeTimingSummary Gpu;
    int32 FrameBudgetMissCount = 0;
    double FrameBudgetMissFraction = 0.0;
    FString LargestMeasuredComponent;
    double ProcessWorkingSetMb = 0.0;
    double ProcessPeakWorkingSetMb = 0.0;
    double TextureStreamingMemoryMb = 0.0;
    double TextureNonStreamingMemoryMb = 0.0;
    TOptional<double> TexturePoolMb;
    TOptional<double> DedicatedVideoMemoryMb;
};

struct FRenderMasterRuntimePerformanceReport
{
    FString ReportId;
    FString Status;
    FString Request;
    FString CaptureId;
    FString CaptureSha256;
    FString Summary;
    FString PrimaryBottleneck;
    TArray<FString> Findings;
    FString MissingCapabilities;
    bool bModifiesEditorScene = true;
};

bool RenderMasterSummarizeRuntimeTimings(
    const TArray<double>& Values,
    FRenderMasterRuntimeTimingSummary& OutSummary);

bool RenderMasterParseRuntimePerformanceReportFile(
    const FString& Filename,
    FRenderMasterRuntimePerformanceReport& OutReport,
    FString& OutError);

class FRenderMasterRuntimePerformanceAssistant
    : public TSharedFromThis<FRenderMasterRuntimePerformanceAssistant>
{
public:
    explicit FRenderMasterRuntimePerformanceAssistant(
        TSharedPtr<FRenderMasterWorkflowController> InWorkflowController);
    ~FRenderMasterRuntimePerformanceAssistant();

    void Initialize();
    void Shutdown();
    bool StartReview(const FString& Prompt);
    void Dismiss();
    void Cancel();

    bool CanStart() const;
    bool IsBusy() const;
    ERenderMasterRuntimePerformanceState GetState() const { return State; }
    FText GetStateText() const;
    FText GetSummaryText() const;
    FLinearColor GetStateColor() const;
    const FRenderMasterRuntimeCapture& GetCapture() const { return Capture; }

private:
    bool Tick(float DeltaTime);
    bool TickCapture();
    bool FinalizeCapture(FString& OutError);
    bool WriteCapture(const FString& Filename, FString& OutError) const;
    bool StartReviewProcess(FString& OutError);
    void CompleteProcess();
    void CloseProcessResources();
    void AppendLog(const FString& Line);
    void Fail(const FString& Error);

    TSharedPtr<FRenderMasterWorkflowController> WorkflowController;
    ERenderMasterRuntimePerformanceState State =
        ERenderMasterRuntimePerformanceState::Ready;
    FRenderMasterRuntimeCapture Capture;
    FRenderMasterRuntimePerformanceReport Report;
    TWeakObjectPtr<UWorld> CaptureWorld;
    FString ReviewRequest;
    FString RequestDirectory;
    FString PromptPath;
    FString CapturePath;
    FString ReportOutputPath;
    FString ErrorText;
    FString ProcessLog;
    uint64 LastObservedFrame = 0;
    int32 ObservedFrames = 0;
    FProcHandle ProcessHandle;
    void* StdOutRead = nullptr;
    void* StdOutWrite = nullptr;
    void* StdErrRead = nullptr;
    void* StdErrWrite = nullptr;
    FTSTicker::FDelegateHandle TickHandle;
};
