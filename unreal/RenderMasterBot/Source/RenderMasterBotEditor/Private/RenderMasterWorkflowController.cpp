#include "RenderMasterWorkflowController.h"

#include "Brushes/SlateDynamicImageBrush.h"
#include "Containers/Ticker.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformFileManager.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

namespace
{
constexpr TCHAR ConfigSection[] = TEXT("RenderMasterBot.EditorPanel");

FString EnvironmentValue(const TCHAR* Name)
{
    return FPlatformMisc::GetEnvironmentVariable(Name).TrimStartAndEnd();
}

FString QuoteArgument(const FString& Value)
{
    return FString::Printf(TEXT("\"%s\""), *Value);
}

int32 StageIndex(const FString& Stage)
{
    if (Stage == TEXT("initializing")) return 0;
    if (Stage == TEXT("retrieval")) return 1;
    if (Stage == TEXT("planning")) return 2;
    if (Stage == TEXT("preflight")) return 3;
    if (Stage == TEXT("rendering")) return 4;
    if (Stage == TEXT("evaluation")) return 5;
    if (Stage == TEXT("correction")) return 6;
    if (Stage == TEXT("complete")) return 7;
    return -1;
}

FString FindNewestFile(const FString& Root, const TCHAR* Filename)
{
    if (Root.IsEmpty() || !IFileManager::Get().DirectoryExists(*Root))
    {
        return FString();
    }

    TArray<FString> Matches;
    IFileManager::Get().FindFilesRecursive(Matches, *Root, Filename, true, false, false);
    Matches.Sort([](const FString& A, const FString& B)
    {
        return IFileManager::Get().GetTimeStamp(*A) > IFileManager::Get().GetTimeStamp(*B);
    });
    return Matches.IsEmpty() ? FString() : Matches[0];
}
}

FRenderMasterWorkflowController::FRenderMasterWorkflowController()
    : LastPreviewTimestamp(FDateTime::MinValue())
{
}

FRenderMasterWorkflowController::~FRenderMasterWorkflowController()
{
    Shutdown();
}

void FRenderMasterWorkflowController::Initialize()
{
    ProjectFile = FPaths::ConvertRelativePathToFull(FPaths::GetProjectFilePath());
    EngineRoot = FPaths::ConvertRelativePathToFull(FPaths::Combine(FPaths::EngineDir(), TEXT("..")));
    FPaths::CollapseRelativeDirectories(EngineRoot);
    LoadConfiguration();
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateSP(AsShared(), &FRenderMasterWorkflowController::Tick),
        0.2f);
}

void FRenderMasterWorkflowController::Shutdown()
{
    if (TickHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
        TickHandle.Reset();
    }
    if (ProcessHandle.IsValid())
    {
        FPlatformProcess::TerminateProc(ProcessHandle, true);
    }
    CloseProcessResources();
    PreviewBrush.Reset();
}

bool FRenderMasterWorkflowController::Start(const FString& Prompt, int32 MaxIterations)
{
    if (!CanStart())
    {
        return false;
    }

    const FString CleanPrompt = Prompt.TrimStartAndEnd();
    FString Error;
    if (CleanPrompt.IsEmpty())
    {
        Snapshot.Status = TEXT("failed");
        Snapshot.Stage = TEXT("complete");
        Snapshot.Error = TEXT("Enter a render request before starting.");
        return false;
    }
    if (CleanPrompt.Len() > 4000)
    {
        Snapshot.Status = TEXT("failed");
        Snapshot.Stage = TEXT("complete");
        Snapshot.Error = TEXT("The render request exceeds the 4,000 character limit.");
        return false;
    }
    if (!ValidateConfiguration(Error))
    {
        Snapshot.Status = TEXT("failed");
        Snapshot.Stage = TEXT("complete");
        Snapshot.Error = Error;
        return false;
    }

    SaveConfiguration();
    IFileManager::Get().MakeDirectory(*WorkflowRoot, true);
    const FString RequestsDirectory = FPaths::Combine(WorkflowRoot, TEXT("requests"));
    IFileManager::Get().MakeDirectory(*RequestsDirectory, true);

    const FString WorkflowId = FString::Printf(TEXT("panel_%s"), *FDateTime::Now().ToString(TEXT("%Y%m%d_%H%M%S_%s")));
    const FString PromptPath = FPaths::Combine(RequestsDirectory, WorkflowId + TEXT(".txt"));
    ActiveWorkflowDirectory = FPaths::Combine(WorkflowRoot, WorkflowId);
    if (!FFileHelper::SaveStringToFile(CleanPrompt, *PromptPath, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        Snapshot.Status = TEXT("failed");
        Snapshot.Stage = TEXT("complete");
        Snapshot.Error = FString::Printf(TEXT("Could not write the prompt file: %s"), *PromptPath);
        return false;
    }

    Snapshot = FRenderMasterManifestSnapshot();
    Snapshot.WorkflowId = WorkflowId;
    Snapshot.Status = TEXT("running");
    Snapshot.Stage = TEXT("initializing");
    LastActiveStage = Snapshot.Stage;
    Snapshot.MaxIterations = FMath::Clamp(MaxIterations, 1, 5);
    Evaluation = FRenderMasterEvaluation();
    Statistics = FRenderMasterImageStatistics();
    PreviewPath.Reset();
    PreviewBrush.Reset();
    LogText.Reset();
    bCancelRequested = false;
    LastPreviewTimestamp = FDateTime::MinValue();

    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        Snapshot.Status = TEXT("failed");
        Snapshot.Stage = TEXT("complete");
        Snapshot.Error = TEXT("Could not create process output pipes.");
        return false;
    }

    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot run %s --engine-root %s --prompt-file %s --assets %s --workflow-dir %s --workflow-id %s --max-iterations %d"),
        *QuoteArgument(ProjectFile),
        *QuoteArgument(EngineRoot),
        *QuoteArgument(PromptPath),
        *QuoteArgument(AssetCatalog),
        *QuoteArgument(ActiveWorkflowDirectory),
        *QuoteArgument(WorkflowId),
        Snapshot.MaxIterations);

    uint32 ProcessId = 0;
    ProcessHandle = FPlatformProcess::CreateProc(
        *PythonExecutable,
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
        Snapshot.Status = TEXT("failed");
        Snapshot.Stage = TEXT("complete");
        Snapshot.Error = FString::Printf(TEXT("Could not start Python: %s"), *PythonExecutable);
        return false;
    }

    AppendLog(FString::Printf(TEXT("Started %s (process %u)"), *WorkflowId, ProcessId));
    return true;
}

void FRenderMasterWorkflowController::Cancel()
{
    if (!ProcessHandle.IsValid())
    {
        return;
    }
    bCancelRequested = true;
    AppendLog(TEXT("Cancellation requested; stopping Python and its child Unreal process."));
    FPlatformProcess::TerminateProc(ProcessHandle, true);
}

void FRenderMasterWorkflowController::OpenWorkflowFolder() const
{
    if (HasWorkflowFolder())
    {
        FPlatformProcess::ExploreFolder(*ActiveWorkflowDirectory);
    }
}

void FRenderMasterWorkflowController::OpenPreview() const
{
    if (HasPreview())
    {
        FPlatformProcess::LaunchFileInDefaultExternalApplication(*PreviewPath);
    }
}

bool FRenderMasterWorkflowController::IsRunning()
{
    return ProcessHandle.IsValid() && FPlatformProcess::IsProcRunning(ProcessHandle);
}

bool FRenderMasterWorkflowController::CanStart() const
{
    return !ProcessHandle.IsValid();
}

bool FRenderMasterWorkflowController::HasWorkflowFolder() const
{
    return !ActiveWorkflowDirectory.IsEmpty() && IFileManager::Get().DirectoryExists(*ActiveWorkflowDirectory);
}

bool FRenderMasterWorkflowController::HasPreview() const
{
    return !PreviewPath.IsEmpty() && IFileManager::Get().FileExists(*PreviewPath);
}

void FRenderMasterWorkflowController::SetPythonExecutable(const FString& Value)
{
    PythonExecutable = Value.TrimStartAndEnd();
    SaveConfiguration();
}

void FRenderMasterWorkflowController::SetAssetCatalog(const FString& Value)
{
    AssetCatalog = Value.TrimStartAndEnd();
    SaveConfiguration();
}

void FRenderMasterWorkflowController::SetWorkflowRoot(const FString& Value)
{
    WorkflowRoot = Value.TrimStartAndEnd();
    SaveConfiguration();
}

FText FRenderMasterWorkflowController::GetStatusText() const
{
    FString Label = Snapshot.Status;
    if (!Label.IsEmpty())
    {
        Label[0] = FChar::ToUpper(Label[0]);
    }
    return FText::FromString(Label);
}

FText FRenderMasterWorkflowController::GetStageText() const
{
    FString Label = Snapshot.Stage.Replace(TEXT("_"), TEXT(" "));
    if (!Label.IsEmpty())
    {
        Label[0] = FChar::ToUpper(Label[0]);
    }
    return FText::FromString(Label);
}

FText FRenderMasterWorkflowController::GetDetailText() const
{
    if (!Snapshot.Error.IsEmpty())
    {
        return FText::FromString(Snapshot.Error);
    }
    if (!Snapshot.StopReason.IsEmpty())
    {
        return FText::FromString(Snapshot.StopReason.Replace(TEXT("_"), TEXT(" ")));
    }
    if (Snapshot.Status == TEXT("running"))
    {
        return FText::Format(
            NSLOCTEXT("RenderMasterBot", "IterationDetail", "Iteration {0} of {1}"),
            FText::AsNumber(FMath::Min(Snapshot.IterationCount + 1, Snapshot.MaxIterations)),
            FText::AsNumber(Snapshot.MaxIterations));
    }
    return NSLOCTEXT("RenderMasterBot", "ReadyDetail", "Ready for a render request");
}

FText FRenderMasterWorkflowController::GetEvaluationText() const
{
    if (Evaluation.Summary.IsEmpty())
    {
        return NSLOCTEXT("RenderMasterBot", "NoEvaluation", "The visual evaluator summary will appear after the first preview.");
    }
    return FText::FromString(FString::Printf(TEXT("%s — %s"), *Evaluation.Verdict.ToUpper(), *Evaluation.Summary));
}

FText FRenderMasterWorkflowController::GetStatisticsText() const
{
    if (!Statistics.bAvailable)
    {
        return NSLOCTEXT("RenderMasterBot", "NoStatistics", "No pixel evidence yet");
    }
    return FText::FromString(FString::Printf(
        TEXT("%d × %d  |  luminance %.3f  |  dark %.1f%%  |  clipped %.1f%%  |  foreground %.1f%%"),
        Statistics.Width,
        Statistics.Height,
        Statistics.MeanLuminance,
        Statistics.DarkPixelFraction * 100.0,
        Statistics.ClippedPixelFraction * 100.0,
        Statistics.ForegroundFraction * 100.0));
}

FText FRenderMasterWorkflowController::GetLogText() const
{
    return FText::FromString(LogText.IsEmpty() ? TEXT("Process output will appear here.") : LogText);
}

float FRenderMasterWorkflowController::GetProgress() const
{
    if (Snapshot.Status == TEXT("ready")) return 0.0f;
    if (Snapshot.IsTerminal()) return 1.0f;
    const int32 Index = FMath::Max(StageIndex(Snapshot.Stage), 0);
    return FMath::Clamp(static_cast<float>(Index + 1) / 8.0f, 0.04f, 0.95f);
}

FLinearColor FRenderMasterWorkflowController::GetStatusColor() const
{
    if (Snapshot.Status == TEXT("succeeded")) return FLinearColor(0.16f, 0.78f, 0.48f);
    if (Snapshot.Status == TEXT("failed") || Snapshot.Status == TEXT("cancelled")) return FLinearColor(0.96f, 0.30f, 0.30f);
    if (Snapshot.Status == TEXT("stopped")) return FLinearColor(1.0f, 0.62f, 0.18f);
    if (Snapshot.Status == TEXT("running")) return FLinearColor(0.18f, 0.64f, 1.0f);
    return FLinearColor(0.55f, 0.58f, 0.65f);
}

FLinearColor FRenderMasterWorkflowController::GetStageColor(const FString& StageName) const
{
    if (Snapshot.Status == TEXT("succeeded")) return FLinearColor(0.12f, 0.55f, 0.36f);
    const FString& EffectiveStage = Snapshot.Stage == TEXT("complete") ? LastActiveStage : Snapshot.Stage;
    const int32 Current = StageIndex(EffectiveStage);
    const int32 Target = StageIndex(StageName);
    if (Current < 0 || Target < 0) return FLinearColor(0.18f, 0.19f, 0.22f);
    if (Target < Current) return FLinearColor(0.12f, 0.55f, 0.36f);
    if (Target == Current) return Snapshot.IsTerminal() ? GetStatusColor() : FLinearColor(0.08f, 0.42f, 0.75f);
    return FLinearColor(0.18f, 0.19f, 0.22f);
}

const FSlateBrush* FRenderMasterWorkflowController::GetPreviewBrush() const
{
    return PreviewBrush.Get();
}

bool FRenderMasterWorkflowController::Tick(float DeltaTime)
{
    ReadProcessOutput();
    const double Now = FPlatformTime::Seconds();
    if (Now - LastPollSeconds >= 0.5)
    {
        PollManifest();
        RefreshArtifacts();
        LastPollSeconds = Now;
    }
    if (ProcessHandle.IsValid() && !FPlatformProcess::IsProcRunning(ProcessHandle))
    {
        FinishProcess();
    }
    return true;
}

bool FRenderMasterWorkflowController::ValidateConfiguration(FString& OutError) const
{
    const TArray<TPair<FString, FString>> Values = {
        { TEXT("Python executable"), PythonExecutable },
        { TEXT("asset catalog"), AssetCatalog },
        { TEXT("workflow root"), WorkflowRoot },
        { TEXT("project file"), ProjectFile },
        { TEXT("engine root"), EngineRoot },
    };
    for (const TPair<FString, FString>& Pair : Values)
    {
        if (Pair.Value.IsEmpty())
        {
            OutError = FString::Printf(TEXT("%s is not configured."), *Pair.Key);
            return false;
        }
        if (Pair.Value.Contains(TEXT("\"")))
        {
            OutError = FString::Printf(TEXT("%s cannot contain a double quote."), *Pair.Key);
            return false;
        }
    }
    if (!FPaths::FileExists(PythonExecutable))
    {
        OutError = FString::Printf(TEXT("Python executable does not exist: %s"), *PythonExecutable);
        return false;
    }
    if (!FPaths::FileExists(AssetCatalog))
    {
        OutError = FString::Printf(TEXT("Asset catalog does not exist: %s"), *AssetCatalog);
        return false;
    }
    if (!FPaths::FileExists(ProjectFile))
    {
        OutError = FString::Printf(TEXT("Project file does not exist: %s"), *ProjectFile);
        return false;
    }
    return true;
}

void FRenderMasterWorkflowController::LoadConfiguration()
{
    PythonExecutable = EnvironmentValue(TEXT("RENDERMASTER_PYTHON"));
    AssetCatalog = EnvironmentValue(TEXT("RENDERMASTER_ASSET_CATALOG"));
    WorkflowRoot = EnvironmentValue(TEXT("RENDERMASTER_WORKFLOW_ROOT"));

    if (PythonExecutable.IsEmpty()) GConfig->GetString(ConfigSection, TEXT("PythonExecutable"), PythonExecutable, GEditorPerProjectIni);
    if (AssetCatalog.IsEmpty()) GConfig->GetString(ConfigSection, TEXT("AssetCatalog"), AssetCatalog, GEditorPerProjectIni);
    if (WorkflowRoot.IsEmpty()) GConfig->GetString(ConfigSection, TEXT("WorkflowRoot"), WorkflowRoot, GEditorPerProjectIni);

    if (PythonExecutable.IsEmpty()) PythonExecutable = TEXT("python.exe");
    if (AssetCatalog.IsEmpty()) AssetCatalog = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("RenderMasterBot"), TEXT("asset_cards.json"));
    if (WorkflowRoot.IsEmpty()) WorkflowRoot = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("RenderMasterBot"), TEXT("Workflows"));
}

void FRenderMasterWorkflowController::SaveConfiguration() const
{
    if (GConfig == nullptr) return;
    GConfig->SetString(ConfigSection, TEXT("PythonExecutable"), *PythonExecutable, GEditorPerProjectIni);
    GConfig->SetString(ConfigSection, TEXT("AssetCatalog"), *AssetCatalog, GEditorPerProjectIni);
    GConfig->SetString(ConfigSection, TEXT("WorkflowRoot"), *WorkflowRoot, GEditorPerProjectIni);
    GConfig->Flush(false, GEditorPerProjectIni);
}

void FRenderMasterWorkflowController::PollManifest()
{
    if (ActiveWorkflowDirectory.IsEmpty()) return;
    const FString ManifestPath = FPaths::Combine(ActiveWorkflowDirectory, TEXT("workflow_manifest.json"));
    if (!FPaths::FileExists(ManifestPath)) return;
    FRenderMasterManifestSnapshot Updated;
    FString Error;
    if (FRenderMasterManifestSnapshot::LoadFromFile(ManifestPath, Updated, Error))
    {
        if (Updated.Stage != TEXT("complete")) LastActiveStage = Updated.Stage;
        Snapshot = MoveTemp(Updated);
    }
}

void FRenderMasterWorkflowController::RefreshArtifacts()
{
    const FString NewPreview = FindNewestFile(ActiveWorkflowDirectory, TEXT("beauty.png"));
    if (!NewPreview.IsEmpty())
    {
        const FDateTime Timestamp = IFileManager::Get().GetTimeStamp(*NewPreview);
        if (NewPreview != PreviewPath || Timestamp > LastPreviewTimestamp)
        {
            PreviewPath = NewPreview;
            LastPreviewTimestamp = Timestamp;
            PreviewBrush = MakeShared<FSlateDynamicImageBrush>(FName(*PreviewPath), FVector2D(1024.0f, 1024.0f));
        }
    }

    FString Error;
    const FString EvaluationPath = FindNewestFile(ActiveWorkflowDirectory, TEXT("evaluation.json"));
    if (!EvaluationPath.IsEmpty()) LoadRenderMasterEvaluation(EvaluationPath, Evaluation, Error);
    const FString StatisticsPath = FindNewestFile(ActiveWorkflowDirectory, TEXT("image_statistics.json"));
    if (!StatisticsPath.IsEmpty()) LoadRenderMasterImageStatistics(StatisticsPath, Statistics, Error);
}

void FRenderMasterWorkflowController::ReadProcessOutput()
{
    if (StdOutRead != nullptr) AppendLog(FPlatformProcess::ReadPipe(StdOutRead));
    if (StdErrRead != nullptr) AppendLog(FPlatformProcess::ReadPipe(StdErrRead));
}

void FRenderMasterWorkflowController::FinishProcess()
{
    ReadProcessOutput();
    PollManifest();
    RefreshArtifacts();
    int32 ExitCode = -1;
    FPlatformProcess::GetProcReturnCode(ProcessHandle, &ExitCode);
    AppendLog(FString::Printf(TEXT("Process finished with exit code %d."), ExitCode));

    FPlatformProcess::CloseProc(ProcessHandle);
    ProcessHandle.Reset();
    FPlatformProcess::ClosePipe(StdOutRead, StdOutWrite);
    FPlatformProcess::ClosePipe(StdErrRead, StdErrWrite);
    StdOutRead = StdOutWrite = StdErrRead = StdErrWrite = nullptr;

    if (bCancelRequested)
    {
        Snapshot.Status = TEXT("cancelled");
        Snapshot.Stage = TEXT("complete");
        Snapshot.StopReason = TEXT("cancelled by user");
    }
    else if (!Snapshot.IsTerminal())
    {
        Snapshot.Status = TEXT("failed");
        Snapshot.Stage = TEXT("complete");
        Snapshot.Error = FString::Printf(TEXT("Workflow exited with code %d before writing a terminal manifest."), ExitCode);
    }
}

void FRenderMasterWorkflowController::CloseProcessResources()
{
    if (ProcessHandle.IsValid())
    {
        FPlatformProcess::CloseProc(ProcessHandle);
        ProcessHandle.Reset();
    }
    if (StdOutRead != nullptr || StdOutWrite != nullptr) FPlatformProcess::ClosePipe(StdOutRead, StdOutWrite);
    if (StdErrRead != nullptr || StdErrWrite != nullptr) FPlatformProcess::ClosePipe(StdErrRead, StdErrWrite);
    StdOutRead = StdOutWrite = StdErrRead = StdErrWrite = nullptr;
}

void FRenderMasterWorkflowController::AppendLog(const FString& Text)
{
    if (Text.IsEmpty()) return;
    LogText += Text;
    if (!LogText.EndsWith(TEXT("\n"))) LogText += TEXT("\n");
    constexpr int32 MaximumLogCharacters = 24000;
    if (LogText.Len() > MaximumLogCharacters)
    {
        LogText.RightInline(MaximumLogCharacters, EAllowShrinking::No);
    }
}
