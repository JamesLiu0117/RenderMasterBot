#include "RenderMasterRuntimePerformanceAssistant.h"

#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/GameViewportClient.h"
#include "Engine/World.h"
#include "GenericPlatform/GenericPlatformMemory.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformMemory.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"
#include "RenderMasterWorkflowController.h"
#include "RenderTimer.h"
#include "DynamicRHI.h"
#include "RHIGlobals.h"
#include "RHIStats.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "UnrealClient.h"

namespace
{
constexpr double BytesPerMiB = 1024.0 * 1024.0;

FString QuoteRuntimeArgument(const FString& Value)
{
    return FString::Printf(TEXT("\"%s\""), *Value);
}

bool SerializeRuntimeJson(
    const TSharedRef<FJsonObject>& Object,
    FString& OutText)
{
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&OutText);
    return FJsonSerializer::Serialize(Object, Writer);
}

void SetOptionalRuntimeNumber(
    const TSharedRef<FJsonObject>& Object,
    const TCHAR* Field,
    const TOptional<double>& Value)
{
    if (Value.IsSet()) Object->SetNumberField(Field, Value.GetValue());
    else Object->SetField(Field, MakeShared<FJsonValueNull>());
}

TSharedRef<FJsonObject> RuntimeSummaryJson(
    const FRenderMasterRuntimeTimingSummary& Summary)
{
    TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetBoolField(TEXT("available"), Summary.bAvailable);
    Result->SetNumberField(TEXT("sample_count"), Summary.SampleCount);
    if (Summary.bAvailable)
    {
        Result->SetNumberField(TEXT("mean_ms"), Summary.MeanMs);
        Result->SetNumberField(TEXT("p50_ms"), Summary.P50Ms);
        Result->SetNumberField(TEXT("p95_ms"), Summary.P95Ms);
        Result->SetNumberField(TEXT("max_ms"), Summary.MaxMs);
    }
    else
    {
        Result->SetField(TEXT("mean_ms"), MakeShared<FJsonValueNull>());
        Result->SetField(TEXT("p50_ms"), MakeShared<FJsonValueNull>());
        Result->SetField(TEXT("p95_ms"), MakeShared<FJsonValueNull>());
        Result->SetField(TEXT("max_ms"), MakeShared<FJsonValueNull>());
    }
    return Result;
}

bool ReadRuntimeObject(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field,
    TSharedPtr<FJsonObject>& OutObject)
{
    const TSharedPtr<FJsonObject>* Value = nullptr;
    if (!Parent.IsValid() || !Parent->TryGetObjectField(Field, Value)
        || Value == nullptr || !Value->IsValid())
    {
        return false;
    }
    OutObject = *Value;
    return true;
}

FString ReadRuntimeStringArray(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field)
{
    const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
    if (!Parent.IsValid()
        || !Parent->TryGetArrayField(Field, Values)
        || Values == nullptr)
    {
        return FString();
    }
    TArray<FString> Strings;
    for (const TSharedPtr<FJsonValue>& Value : *Values)
    {
        FString Item;
        if (Value.IsValid() && Value->TryGetString(Item) && !Item.IsEmpty())
        {
            Strings.Add(Item);
        }
    }
    return FString::Join(Strings, TEXT(", "));
}

bool IsLowerHexSha256(const FString& Value)
{
    if (Value.Len() != 64) return false;
    for (const TCHAR Character : Value)
    {
        if (!((Character >= TEXT('0') && Character <= TEXT('9'))
            || (Character >= TEXT('a') && Character <= TEXT('f'))))
        {
            return false;
        }
    }
    return true;
}
}

bool RenderMasterSummarizeRuntimeTimings(
    const TArray<double>& Values,
    FRenderMasterRuntimeTimingSummary& OutSummary)
{
    OutSummary = FRenderMasterRuntimeTimingSummary();
    if (Values.IsEmpty()) return false;
    TArray<double> Ordered = Values;
    double Sum = 0.0;
    for (const double Value : Ordered)
    {
        if (!FMath::IsFinite(Value) || Value < 0.0) return false;
        Sum += Value;
    }
    Ordered.Sort();
    const int32 Count = Ordered.Num();
    const int32 P50Index = FMath::Max(
        0, FMath::CeilToInt(0.50 * static_cast<double>(Count)) - 1);
    const int32 P95Index = FMath::Max(
        0, FMath::CeilToInt(0.95 * static_cast<double>(Count)) - 1);
    OutSummary.bAvailable = true;
    OutSummary.SampleCount = Count;
    OutSummary.MeanMs = Sum / static_cast<double>(Count);
    OutSummary.P50Ms = Ordered[P50Index];
    OutSummary.P95Ms = Ordered[P95Index];
    OutSummary.MaxMs = Ordered.Last();
    return true;
}

bool RenderMasterParseRuntimePerformanceReportFile(
    const FString& Filename,
    FRenderMasterRuntimePerformanceReport& OutReport,
    FString& OutError)
{
    OutReport = FRenderMasterRuntimePerformanceReport();
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *Filename))
    {
        OutError = FString::Printf(
            TEXT("Could not read runtime performance report: %s"), *Filename);
        return false;
    }
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        OutError = TEXT("Runtime performance report is not valid JSON.");
        return false;
    }
    TSharedPtr<FJsonObject> CaptureObject;
    if (!Root->TryGetStringField(TEXT("report_id"), OutReport.ReportId)
        || !Root->TryGetStringField(TEXT("status"), OutReport.Status)
        || !Root->TryGetStringField(TEXT("request"), OutReport.Request)
        || !Root->TryGetStringField(TEXT("capture_sha256"), OutReport.CaptureSha256)
        || !Root->TryGetStringField(TEXT("summary"), OutReport.Summary)
        || !Root->TryGetStringField(
            TEXT("primary_bottleneck"), OutReport.PrimaryBottleneck)
        || !Root->TryGetBoolField(
            TEXT("modifies_editor_scene"), OutReport.bModifiesEditorScene)
        || !ReadRuntimeObject(Root, TEXT("capture"), CaptureObject)
        || !CaptureObject->TryGetStringField(
            TEXT("capture_id"), OutReport.CaptureId))
    {
        OutError = TEXT("Runtime performance report is missing required evidence fields.");
        return false;
    }
    if (OutReport.ReportId.IsEmpty() || OutReport.Request.IsEmpty()
        || OutReport.CaptureId.IsEmpty() || OutReport.Summary.IsEmpty()
        || !IsLowerHexSha256(OutReport.CaptureSha256)
        || OutReport.bModifiesEditorScene
        || (OutReport.Status != TEXT("review_complete")
            && OutReport.Status != TEXT("unresolved")))
    {
        OutError = TEXT("Runtime performance report has an unsafe status or identity.");
        return false;
    }

    const TArray<TSharedPtr<FJsonValue>>* FindingValues = nullptr;
    if (!Root->TryGetArrayField(TEXT("findings"), FindingValues)
        || FindingValues == nullptr || FindingValues->Num() > 16)
    {
        OutError = TEXT("Runtime performance report findings are missing or invalid.");
        return false;
    }
    for (const TSharedPtr<FJsonValue>& FindingValue : *FindingValues)
    {
        const TSharedPtr<FJsonObject> Finding =
            FindingValue.IsValid() ? FindingValue->AsObject() : nullptr;
        FString Severity;
        FString Category;
        FString Observation;
        FString Recommendation;
        const FString Evidence = ReadRuntimeStringArray(
            Finding, TEXT("evidence_fields"));
        if (!Finding.IsValid()
            || !Finding->TryGetStringField(TEXT("severity"), Severity)
            || !Finding->TryGetStringField(TEXT("category"), Category)
            || !Finding->TryGetStringField(TEXT("observation"), Observation)
            || !Finding->TryGetStringField(
                TEXT("recommendation"), Recommendation)
            || Evidence.IsEmpty() || Observation.IsEmpty()
            || Recommendation.IsEmpty())
        {
            OutError = TEXT("Runtime performance report contains an invalid finding.");
            return false;
        }
        OutReport.Findings.Add(FString::Printf(
            TEXT("[%s / %s] %s\nEvidence: %s\nRecommendation: %s"),
            *Severity,
            *Category,
            *Observation,
            *Evidence,
            *Recommendation));
    }
    OutReport.MissingCapabilities = ReadRuntimeStringArray(
        Root, TEXT("missing_capabilities"));
    if (OutReport.Status == TEXT("review_complete")
        && !OutReport.MissingCapabilities.IsEmpty())
    {
        OutError = TEXT("A complete runtime review cannot contain capability gaps.");
        return false;
    }
    if (OutReport.Status == TEXT("unresolved")
        && (!OutReport.Findings.IsEmpty()
            || OutReport.MissingCapabilities.IsEmpty()))
    {
        OutError = TEXT("An unresolved runtime review must contain only capability gaps.");
        return false;
    }
    return true;
}

FRenderMasterRuntimePerformanceAssistant::FRenderMasterRuntimePerformanceAssistant(
    TSharedPtr<FRenderMasterWorkflowController> InWorkflowController)
    : WorkflowController(MoveTemp(InWorkflowController))
{
}

FRenderMasterRuntimePerformanceAssistant::~FRenderMasterRuntimePerformanceAssistant()
{
    Shutdown();
}

void FRenderMasterRuntimePerformanceAssistant::Initialize()
{
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateSP(
            AsShared(), &FRenderMasterRuntimePerformanceAssistant::Tick));
}

void FRenderMasterRuntimePerformanceAssistant::Shutdown()
{
    if (TickHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
        TickHandle.Reset();
    }
    Cancel();
    CloseProcessResources();
}

bool FRenderMasterRuntimePerformanceAssistant::StartReview(
    const FString& Prompt)
{
    if (!CanStart()) return false;
    const FString CleanPrompt = Prompt.TrimStartAndEnd();
    if (CleanPrompt.IsEmpty())
    {
        Fail(TEXT("Enter a runtime performance question before starting a capture."));
        return false;
    }
    if (!WorkflowController.IsValid())
    {
        Fail(TEXT("RenderMasterBot runtime configuration is unavailable."));
        return false;
    }
    const FString Python = WorkflowController->GetPythonExecutable();
    const FString Root = WorkflowController->GetWorkflowRoot();
    if (!FPaths::FileExists(Python) || Root.IsEmpty())
    {
        Fail(TEXT(
            "Configure an existing Python executable and workflow root in Render & Evaluate."));
        return false;
    }
    if (Python.Contains(TEXT("\"")) || Root.Contains(TEXT("\"")))
    {
        Fail(TEXT("Assistant runtime paths cannot contain a double quote."));
        return false;
    }
    UWorld* PlayWorld = GEditor != nullptr ? GEditor->PlayWorld : nullptr;
    if (PlayWorld == nullptr)
    {
        Fail(TEXT(
            "Start Play In Editor or Simulate In Editor, establish the representative camera and workload, then capture again."));
        return false;
    }
    FViewport* Viewport = nullptr;
    if (UGameViewportClient* GameViewport = PlayWorld->GetGameViewport())
    {
        Viewport = GameViewport->Viewport;
    }
    if (Viewport == nullptr && GEditor != nullptr)
    {
        Viewport = GEditor->GetActiveViewport();
    }
    const FIntPoint ViewportSize = Viewport != nullptr
        ? Viewport->GetSizeXY()
        : FIntPoint::ZeroValue;
    if (ViewportSize.X < 1 || ViewportSize.Y < 1)
    {
        Fail(TEXT("The active PIE/SIE viewport has no measurable render size."));
        return false;
    }

    Capture = FRenderMasterRuntimeCapture();
    Report = FRenderMasterRuntimePerformanceReport();
    ErrorText.Reset();
    ProcessLog.Reset();
    ReviewRequest = CleanPrompt;
    Capture.CaptureId = FString::Printf(
        TEXT("runtime_%s"),
        *FGuid::NewGuid().ToString(EGuidFormats::Digits).ToLower());
    Capture.CapturedAtUtc = FDateTime::UtcNow().ToIso8601();
    Capture.ProjectName = FApp::GetProjectName();
    Capture.WorldPath = PlayWorld->GetPackage()->GetName();
    Capture.CaptureMode = GEditor->IsSimulatingInEditor()
        ? TEXT("simulate")
        : TEXT("pie");
    Capture.ViewportSize = ViewportSize;
    Capture.GpuName = GRHIAdapterName.IsEmpty()
        ? TEXT("Unknown RHI adapter")
        : GRHIAdapterName;
    CaptureWorld = PlayWorld;
    ObservedFrames = 0;
    LastObservedFrame = GFrameCounter;

    RequestDirectory = FPaths::Combine(
        Root, TEXT("assistant-runtime-performance"), Capture.CaptureId);
    IFileManager::Get().MakeDirectory(*RequestDirectory, true);
    PromptPath = FPaths::Combine(RequestDirectory, TEXT("request.txt"));
    CapturePath = FPaths::Combine(
        RequestDirectory, TEXT("runtime_performance_capture.json"));
    ReportOutputPath = FPaths::Combine(
        RequestDirectory, TEXT("runtime_performance_report.json"));
    if (!FFileHelper::SaveStringToFile(
            ReviewRequest,
            *PromptPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        Fail(TEXT("Could not write the runtime performance request."));
        return false;
    }
    State = ERenderMasterRuntimePerformanceState::Capturing;
    AppendLog(FString::Printf(
        TEXT("Warming up %d frames, then capturing %d consecutive %dx%d PIE/SIE frames."),
        Capture.WarmupFrames,
        Capture.SampleCount,
        Capture.ViewportSize.X,
        Capture.ViewportSize.Y));
    return true;
}

bool FRenderMasterRuntimePerformanceAssistant::Tick(float DeltaTime)
{
    (void)DeltaTime;
    if (State == ERenderMasterRuntimePerformanceState::Capturing)
    {
        TickCapture();
        return true;
    }
    if (State == ERenderMasterRuntimePerformanceState::Reviewing
        && ProcessHandle.IsValid())
    {
        const FString StdOut = FPlatformProcess::ReadPipe(StdOutRead);
        const FString StdErr = FPlatformProcess::ReadPipe(StdErrRead);
        if (!StdOut.IsEmpty()) AppendLog(StdOut.TrimStartAndEnd());
        if (!StdErr.IsEmpty()) AppendLog(StdErr.TrimStartAndEnd());
        if (!FPlatformProcess::IsProcRunning(ProcessHandle)) CompleteProcess();
    }
    return true;
}

bool FRenderMasterRuntimePerformanceAssistant::TickCapture()
{
    UWorld* World = CaptureWorld.Get();
    if (World == nullptr || GEditor == nullptr || GEditor->PlayWorld != World)
    {
        Fail(TEXT("PIE/SIE ended before the runtime capture completed."));
        return false;
    }
    if (GFrameCounter == LastObservedFrame) return true;
    LastObservedFrame = GFrameCounter;
    ++ObservedFrames;
    if (ObservedFrames <= Capture.WarmupFrames) return true;

    FRenderMasterRuntimeFrameSample Sample;
    Sample.FrameIndex = Capture.Samples.Num();
    Sample.FrameTimeMs = FMath::Max(0.0, FApp::GetDeltaTime() * 1000.0);
    Sample.GameThreadMs = FMath::Max(
        0.0, FPlatformTime::ToMilliseconds(GGameThreadTime));
    Sample.RenderThreadMs = FMath::Max(
        0.0, FPlatformTime::ToMilliseconds(GRenderThreadTime));
    const uint32 RhiCycles = GRHIThreadTime;
    if (RhiCycles > 0)
    {
        Sample.RhiThreadMs = FMath::Max(
            0.0, FPlatformTime::ToMilliseconds(RhiCycles));
    }
    const uint32 GpuCycles = RHIGetGPUFrameCycles();
    if (GpuCycles > 0)
    {
        Sample.GpuMs = FMath::Max(
            0.0, FPlatformTime::ToMilliseconds(GpuCycles));
    }
    Capture.Samples.Add(Sample);
    if (Capture.Samples.Num() < Capture.SampleCount) return true;

    FString Error;
    if (!FinalizeCapture(Error))
    {
        Fail(Error.IsEmpty()
            ? TEXT("Could not finalize the runtime performance capture.")
            : Error);
        return false;
    }
    return true;
}

bool FRenderMasterRuntimePerformanceAssistant::FinalizeCapture(FString& OutError)
{
    TArray<double> FrameValues;
    TArray<double> GameValues;
    TArray<double> RenderValues;
    TArray<double> RhiValues;
    TArray<double> GpuValues;
    for (const FRenderMasterRuntimeFrameSample& Sample : Capture.Samples)
    {
        FrameValues.Add(Sample.FrameTimeMs);
        GameValues.Add(Sample.GameThreadMs);
        RenderValues.Add(Sample.RenderThreadMs);
        if (Sample.RhiThreadMs.IsSet()) RhiValues.Add(Sample.RhiThreadMs.GetValue());
        if (Sample.GpuMs.IsSet()) GpuValues.Add(Sample.GpuMs.GetValue());
        if (Sample.FrameTimeMs > Capture.TargetFrameMs)
            ++Capture.FrameBudgetMissCount;
    }
    if (!RenderMasterSummarizeRuntimeTimings(FrameValues, Capture.FrameTime)
        || !RenderMasterSummarizeRuntimeTimings(GameValues, Capture.GameThread)
        || !RenderMasterSummarizeRuntimeTimings(RenderValues, Capture.RenderThread))
    {
        OutError = TEXT("Required frame timing samples are invalid.");
        return false;
    }
    RenderMasterSummarizeRuntimeTimings(RhiValues, Capture.RhiThread);
    RenderMasterSummarizeRuntimeTimings(GpuValues, Capture.Gpu);
    Capture.FrameBudgetMissFraction =
        static_cast<double>(Capture.FrameBudgetMissCount)
        / static_cast<double>(Capture.SampleCount);

    Capture.LargestMeasuredComponent = TEXT("game_thread");
    double LargestP95 = Capture.GameThread.P95Ms;
    if (Capture.RenderThread.P95Ms > LargestP95)
    {
        Capture.LargestMeasuredComponent = TEXT("render_thread");
        LargestP95 = Capture.RenderThread.P95Ms;
    }
    if (Capture.RhiThread.bAvailable && Capture.RhiThread.P95Ms > LargestP95)
    {
        Capture.LargestMeasuredComponent = TEXT("rhi_thread");
        LargestP95 = Capture.RhiThread.P95Ms;
    }
    if (Capture.Gpu.bAvailable && Capture.Gpu.P95Ms > LargestP95)
    {
        Capture.LargestMeasuredComponent = TEXT("gpu");
    }

    const FPlatformMemoryStats MemoryStats = FPlatformMemory::GetStats();
    Capture.ProcessWorkingSetMb = MemoryStats.UsedPhysical / BytesPerMiB;
    Capture.ProcessPeakWorkingSetMb = MemoryStats.PeakUsedPhysical / BytesPerMiB;
    if (GDynamicRHI != nullptr)
    {
        FTextureMemoryStats TextureStats;
        RHIGetTextureMemoryStats(TextureStats);
        Capture.TextureStreamingMemoryMb =
            TextureStats.StreamingMemorySize / BytesPerMiB;
        Capture.TextureNonStreamingMemoryMb =
            TextureStats.NonStreamingMemorySize / BytesPerMiB;
        if (TextureStats.TexturePoolSize > 0)
            Capture.TexturePoolMb = TextureStats.TexturePoolSize / BytesPerMiB;
        if (TextureStats.DedicatedVideoMemory >= 0)
            Capture.DedicatedVideoMemoryMb =
                TextureStats.DedicatedVideoMemory / BytesPerMiB;
    }
    if (!WriteCapture(CapturePath, OutError)) return false;
    return StartReviewProcess(OutError);
}

bool FRenderMasterRuntimePerformanceAssistant::WriteCapture(
    const FString& Filename,
    FString& OutError) const
{
    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Root->SetStringField(TEXT("capture_id"), Capture.CaptureId);
    Root->SetStringField(TEXT("captured_at_utc"), Capture.CapturedAtUtc);
    Root->SetStringField(TEXT("project_name"), Capture.ProjectName);
    Root->SetStringField(TEXT("world_path"), Capture.WorldPath);
    Root->SetStringField(TEXT("capture_mode"), Capture.CaptureMode);
    Root->SetNumberField(TEXT("target_fps"), Capture.TargetFps);
    Root->SetNumberField(TEXT("target_frame_ms"), Capture.TargetFrameMs);
    Root->SetNumberField(TEXT("warmup_frames"), Capture.WarmupFrames);
    Root->SetNumberField(TEXT("sample_count"), Capture.SampleCount);
    Root->SetNumberField(TEXT("viewport_width_px"), Capture.ViewportSize.X);
    Root->SetNumberField(TEXT("viewport_height_px"), Capture.ViewportSize.Y);
    Root->SetStringField(TEXT("gpu_name"), Capture.GpuName);

    TArray<TSharedPtr<FJsonValue>> Samples;
    for (const FRenderMasterRuntimeFrameSample& Sample : Capture.Samples)
    {
        TSharedRef<FJsonObject> Value = MakeShared<FJsonObject>();
        Value->SetNumberField(TEXT("frame_index"), Sample.FrameIndex);
        Value->SetNumberField(TEXT("frame_time_ms"), Sample.FrameTimeMs);
        Value->SetNumberField(TEXT("game_thread_ms"), Sample.GameThreadMs);
        Value->SetNumberField(TEXT("render_thread_ms"), Sample.RenderThreadMs);
        SetOptionalRuntimeNumber(Value, TEXT("rhi_thread_ms"), Sample.RhiThreadMs);
        SetOptionalRuntimeNumber(Value, TEXT("gpu_ms"), Sample.GpuMs);
        Samples.Add(MakeShared<FJsonValueObject>(Value));
    }
    Root->SetArrayField(TEXT("samples"), Samples);
    Root->SetObjectField(TEXT("frame_time"), RuntimeSummaryJson(Capture.FrameTime));
    Root->SetObjectField(TEXT("game_thread"), RuntimeSummaryJson(Capture.GameThread));
    Root->SetObjectField(TEXT("render_thread"), RuntimeSummaryJson(Capture.RenderThread));
    Root->SetObjectField(TEXT("rhi_thread"), RuntimeSummaryJson(Capture.RhiThread));
    Root->SetObjectField(TEXT("gpu"), RuntimeSummaryJson(Capture.Gpu));
    Root->SetNumberField(
        TEXT("frame_budget_miss_count"), Capture.FrameBudgetMissCount);
    Root->SetNumberField(
        TEXT("frame_budget_miss_fraction"), Capture.FrameBudgetMissFraction);
    Root->SetStringField(
        TEXT("largest_measured_component"), Capture.LargestMeasuredComponent);
    Root->SetNumberField(
        TEXT("process_working_set_mb"), Capture.ProcessWorkingSetMb);
    Root->SetNumberField(
        TEXT("process_peak_working_set_mb"), Capture.ProcessPeakWorkingSetMb);
    Root->SetNumberField(
        TEXT("texture_streaming_memory_mb"), Capture.TextureStreamingMemoryMb);
    Root->SetNumberField(
        TEXT("texture_non_streaming_memory_mb"),
        Capture.TextureNonStreamingMemoryMb);
    SetOptionalRuntimeNumber(Root, TEXT("texture_pool_mb"), Capture.TexturePoolMb);
    SetOptionalRuntimeNumber(
        Root,
        TEXT("dedicated_video_memory_mb"),
        Capture.DedicatedVideoMemoryMb);
    TArray<TSharedPtr<FJsonValue>> Notes;
    Notes.Add(MakeShared<FJsonValueString>(TEXT(
        "Editor PIE/SIE timings include Editor overhead and do not guarantee packaged-build parity.")));
    Notes.Add(MakeShared<FJsonValueString>(TEXT(
        "GPU timing is total-frame busy time; no per-pass GPU breakdown was captured.")));
    Notes.Add(MakeShared<FJsonValueString>(TEXT(
        "RHI texture memory is not total GPU memory usage.")));
    Root->SetArrayField(TEXT("measurement_notes"), Notes);

    FString JsonText;
    if (!SerializeRuntimeJson(Root, JsonText)
        || !FFileHelper::SaveStringToFile(
            JsonText,
            *Filename,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        OutError = FString::Printf(
            TEXT("Could not write runtime performance capture: %s"), *Filename);
        return false;
    }
    return true;
}

bool FRenderMasterRuntimePerformanceAssistant::StartReviewProcess(
    FString& OutError)
{
    const FString Python = WorkflowController->GetPythonExecutable();
    const FString ReportId = Capture.CaptureId + TEXT("_review");
    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-runtime-performance-review --prompt-file %s --capture %s --report-id %s --output %s"),
        *QuoteRuntimeArgument(PromptPath),
        *QuoteRuntimeArgument(CapturePath),
        *QuoteRuntimeArgument(ReportId),
        *QuoteRuntimeArgument(ReportOutputPath));
    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        OutError = TEXT("Could not create runtime review process pipes.");
        return false;
    }
    uint32 ProcessId = 0;
    ProcessHandle = FPlatformProcess::CreateProc(
        *Python,
        *Arguments,
        false,
        true,
        true,
        &ProcessId,
        0,
        nullptr,
        StdOutWrite,
        nullptr,
        StdErrWrite);
    if (!ProcessHandle.IsValid())
    {
        CloseProcessResources();
        OutError = FString::Printf(TEXT("Could not start Python: %s"), *Python);
        return false;
    }
    State = ERenderMasterRuntimePerformanceState::Reviewing;
    AppendLog(FString::Printf(
        TEXT("Captured %d frames; reviewing the recomputable evidence (process %u)."),
        Capture.SampleCount,
        ProcessId));
    return true;
}

void FRenderMasterRuntimePerformanceAssistant::CompleteProcess()
{
    int32 ReturnCode = -1;
    FPlatformProcess::GetProcReturnCode(ProcessHandle, &ReturnCode);
    const FString StdOut = FPlatformProcess::ReadPipe(StdOutRead);
    const FString StdErr = FPlatformProcess::ReadPipe(StdErrRead);
    if (!StdOut.IsEmpty()) AppendLog(StdOut.TrimStartAndEnd());
    if (!StdErr.IsEmpty()) AppendLog(StdErr.TrimStartAndEnd());
    CloseProcessResources();
    if (ReturnCode != 0)
    {
        Fail(ProcessLog.IsEmpty()
            ? FString::Printf(
                TEXT("Runtime performance review exited with code %d."),
                ReturnCode)
            : ProcessLog);
        return;
    }
    FString Error;
    if (!RenderMasterParseRuntimePerformanceReportFile(
            ReportOutputPath, Report, Error))
    {
        Fail(Error);
        return;
    }
    if (Report.CaptureId != Capture.CaptureId)
    {
        Fail(TEXT("Runtime performance report does not match the host capture."));
        return;
    }
    State = Report.Status == TEXT("review_complete")
        ? ERenderMasterRuntimePerformanceState::Complete
        : ERenderMasterRuntimePerformanceState::Unresolved;
}

void FRenderMasterRuntimePerformanceAssistant::Dismiss()
{
    if (IsBusy())
    {
        Cancel();
        CloseProcessResources();
    }
    State = ERenderMasterRuntimePerformanceState::Dismissed;
    AppendLog(TEXT("Runtime performance review dismissed. No Editor scene change was applied."));
}

void FRenderMasterRuntimePerformanceAssistant::Cancel()
{
    if (ProcessHandle.IsValid())
        FPlatformProcess::TerminateProc(ProcessHandle, true);
}

bool FRenderMasterRuntimePerformanceAssistant::CanStart() const
{
    return State != ERenderMasterRuntimePerformanceState::Capturing
        && !ProcessHandle.IsValid();
}

bool FRenderMasterRuntimePerformanceAssistant::IsBusy() const
{
    return State == ERenderMasterRuntimePerformanceState::Capturing
        || State == ERenderMasterRuntimePerformanceState::Reviewing;
}

FText FRenderMasterRuntimePerformanceAssistant::GetStateText() const
{
    switch (State)
    {
        case ERenderMasterRuntimePerformanceState::Capturing:
            return NSLOCTEXT("RenderMasterBot", "RuntimePerformanceCapturing", "Capturing PIE/SIE frames");
        case ERenderMasterRuntimePerformanceState::Reviewing:
            return NSLOCTEXT("RenderMasterBot", "RuntimePerformanceReviewing", "Reviewing measured evidence");
        case ERenderMasterRuntimePerformanceState::Complete:
            return NSLOCTEXT("RenderMasterBot", "RuntimePerformanceComplete", "Read-only review complete");
        case ERenderMasterRuntimePerformanceState::Unresolved:
            return NSLOCTEXT("RenderMasterBot", "RuntimePerformanceUnresolved", "More profiler evidence required");
        case ERenderMasterRuntimePerformanceState::Failed:
            return NSLOCTEXT("RenderMasterBot", "RuntimePerformanceFailed", "Capture or review failed");
        case ERenderMasterRuntimePerformanceState::Dismissed:
            return NSLOCTEXT("RenderMasterBot", "RuntimePerformanceDismissed", "Dismissed");
        default:
            return NSLOCTEXT("RenderMasterBot", "RuntimePerformanceReady", "Ready for PIE/SIE capture");
    }
}

FText FRenderMasterRuntimePerformanceAssistant::GetSummaryText() const
{
    if (State == ERenderMasterRuntimePerformanceState::Capturing)
    {
        const int32 WarmupRemaining = FMath::Max(
            0, Capture.WarmupFrames - ObservedFrames);
        if (WarmupRemaining > 0)
        {
            return FText::FromString(FString::Printf(
                TEXT("Warming up the active %dx%d %s viewport: %d frames remain. Keep the camera and workload representative."),
                Capture.ViewportSize.X,
                Capture.ViewportSize.Y,
                *Capture.CaptureMode.ToUpper(),
                WarmupRemaining));
        }
        return FText::FromString(FString::Printf(
            TEXT("Captured %d / %d consecutive runtime frames. No Editor property is being changed."),
            Capture.Samples.Num(),
            Capture.SampleCount));
    }
    const FString Evidence = Capture.Samples.IsEmpty()
        ? FString()
        : FString::Printf(
            TEXT("\n\nMeasured evidence (%d frames at %dx%d, %.0f FPS target / %.2f ms):\nFrame P50 %.2f ms | P95 %.2f ms | Max %.2f ms\nGame P95 %.2f ms | Render P95 %.2f ms | RHI P95 %s | GPU P95 %s\nBudget misses %d (%.1f%%) | Largest measured component %s\nEditor process %.0f MiB | Streaming textures %.0f MiB | Non-streaming textures %.0f MiB"),
            Capture.SampleCount,
            Capture.ViewportSize.X,
            Capture.ViewportSize.Y,
            Capture.TargetFps,
            Capture.TargetFrameMs,
            Capture.FrameTime.P50Ms,
            Capture.FrameTime.P95Ms,
            Capture.FrameTime.MaxMs,
            Capture.GameThread.P95Ms,
            Capture.RenderThread.P95Ms,
            Capture.RhiThread.bAvailable
                ? *FString::Printf(TEXT("%.2f ms"), Capture.RhiThread.P95Ms)
                : TEXT("unavailable"),
            Capture.Gpu.bAvailable
                ? *FString::Printf(TEXT("%.2f ms"), Capture.Gpu.P95Ms)
                : TEXT("unavailable"),
            Capture.FrameBudgetMissCount,
            Capture.FrameBudgetMissFraction * 100.0,
            *Capture.LargestMeasuredComponent,
            Capture.ProcessWorkingSetMb,
            Capture.TextureStreamingMemoryMb,
            Capture.TextureNonStreamingMemoryMb);
    if (State == ERenderMasterRuntimePerformanceState::Reviewing)
    {
        return FText::FromString(
            TEXT("The raw runtime samples and host-computed summaries were saved. The local model is reviewing that evidence; no scene change is available from this card.")
            + Evidence);
    }
    if (State == ERenderMasterRuntimePerformanceState::Complete)
    {
        FString Text = Report.Summary + Evidence
            + FString::Printf(
                TEXT("\n\nPrimary diagnosis: %s"),
                *Report.PrimaryBottleneck);
        if (!Report.Findings.IsEmpty())
        {
            Text += TEXT("\n\nFindings:\n");
            for (int32 Index = 0; Index < Report.Findings.Num(); ++Index)
            {
                Text += FString::Printf(
                    TEXT("%d. %s%s"),
                    Index + 1,
                    *Report.Findings[Index],
                    Index + 1 < Report.Findings.Num() ? TEXT("\n") : TEXT(""));
            }
        }
        Text += TEXT("\n\nThis is an Editor PIE/SIE sample, not packaged-build proof. No Editor scene change was made.");
        return FText::FromString(Text);
    }
    if (State == ERenderMasterRuntimePerformanceState::Unresolved)
    {
        return FText::FromString(
            Report.Summary + Evidence
            + TEXT("\n\nRequired next evidence: ")
            + Report.MissingCapabilities);
    }
    if (State == ERenderMasterRuntimePerformanceState::Failed)
        return FText::FromString(ErrorText + Evidence);
    if (State == ERenderMasterRuntimePerformanceState::Dismissed)
        return NSLOCTEXT(
            "RenderMasterBot",
            "RuntimePerformanceDismissedSummary",
            "The runtime performance review was dismissed. No Editor scene change was applied.");
    return NSLOCTEXT(
        "RenderMasterBot",
        "RuntimePerformanceReadySummary",
        "Start PIE or SIE with a representative camera and workload, enter a performance question, then capture 30 warmup frames plus 120 measured frames. The review is read-only.");
}

FLinearColor FRenderMasterRuntimePerformanceAssistant::GetStateColor() const
{
    switch (State)
    {
        case ERenderMasterRuntimePerformanceState::Complete:
            return FLinearColor(0.12f, 0.55f, 0.28f, 1.0f);
        case ERenderMasterRuntimePerformanceState::Capturing:
        case ERenderMasterRuntimePerformanceState::Reviewing:
            return FLinearColor(0.16f, 0.50f, 0.78f, 1.0f);
        case ERenderMasterRuntimePerformanceState::Unresolved:
            return FLinearColor(0.65f, 0.45f, 0.08f, 1.0f);
        case ERenderMasterRuntimePerformanceState::Failed:
            return FLinearColor(0.70f, 0.14f, 0.12f, 1.0f);
        default:
            return FLinearColor(0.14f, 0.20f, 0.30f, 1.0f);
    }
}

void FRenderMasterRuntimePerformanceAssistant::CloseProcessResources()
{
    if (StdOutRead != nullptr || StdOutWrite != nullptr)
        FPlatformProcess::ClosePipe(StdOutRead, StdOutWrite);
    if (StdErrRead != nullptr || StdErrWrite != nullptr)
        FPlatformProcess::ClosePipe(StdErrRead, StdErrWrite);
    StdOutRead = nullptr;
    StdOutWrite = nullptr;
    StdErrRead = nullptr;
    StdErrWrite = nullptr;
    if (ProcessHandle.IsValid()) FPlatformProcess::CloseProc(ProcessHandle);
    ProcessHandle.Reset();
}

void FRenderMasterRuntimePerformanceAssistant::AppendLog(const FString& Line)
{
    const FString Clean = Line.TrimStartAndEnd();
    if (Clean.IsEmpty()) return;
    if (!ProcessLog.IsEmpty()) ProcessLog += TEXT("\n");
    ProcessLog += Clean;
    constexpr int32 MaxLogChars = 8000;
    if (ProcessLog.Len() > MaxLogChars)
        ProcessLog.RightChopInline(ProcessLog.Len() - MaxLogChars);
}

void FRenderMasterRuntimePerformanceAssistant::Fail(const FString& Error)
{
    ErrorText = Error.IsEmpty()
        ? TEXT("Runtime performance review failed.")
        : Error;
    State = ERenderMasterRuntimePerformanceState::Failed;
    AppendLog(ErrorText);
}
