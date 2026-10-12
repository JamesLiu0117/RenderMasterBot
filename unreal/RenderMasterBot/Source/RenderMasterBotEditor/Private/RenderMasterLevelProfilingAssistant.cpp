#include "RenderMasterLevelProfilingAssistant.h"

#include "RenderMasterInsightsGpuAssistant.h"
#include "RenderMasterLevelAuditAssistant.h"
#include "RenderMasterRuntimePerformanceAssistant.h"
#include "RenderMasterWorkflowController.h"

#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "HAL/FileManager.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

namespace
{
FString QuoteProfilingArgument(const FString& Value)
{
    return FString::Printf(
        TEXT("\"%s\""), *Value.Replace(TEXT("\""), TEXT("\\\"")));
}

bool ReadProfilingObject(
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

bool ReadProfilingInteger(
    const TSharedPtr<FJsonObject>& Object,
    const TCHAR* Field,
    const int32 Minimum,
    const int32 Maximum,
    int32& OutValue)
{
    double Value = 0.0;
    if (!Object.IsValid() || !Object->TryGetNumberField(Field, Value)
        || !FMath::IsFinite(Value)
        || !FMath::IsNearlyEqual(Value, FMath::RoundToDouble(Value))
        || Value < Minimum || Value > Maximum)
    {
        return false;
    }
    OutValue = static_cast<int32>(Value);
    return true;
}

bool ReadProfilingNumber(
    const TSharedPtr<FJsonObject>& Object,
    const TCHAR* Field,
    const double Minimum,
    const double Maximum,
    double& OutValue)
{
    return Object.IsValid()
        && Object->TryGetNumberField(Field, OutValue)
        && FMath::IsFinite(OutValue)
        && OutValue >= Minimum
        && OutValue <= Maximum;
}

bool ReadOptionalProfilingNumber(
    const TSharedPtr<FJsonObject>& Object,
    const TCHAR* Field,
    TOptional<double>& OutValue)
{
    OutValue.Reset();
    if (!Object.IsValid()) return false;
    const TSharedPtr<FJsonValue> Value = Object->TryGetField(Field);
    if (!Value.IsValid() || Value->Type == EJson::Null) return true;
    double Number = 0.0;
    if (!Value->TryGetNumber(Number) || !FMath::IsFinite(Number)) return false;
    OutValue = Number;
    return true;
}

bool ReadProfilingStringArray(
    const TSharedPtr<FJsonObject>& Object,
    const TCHAR* Field,
    const int32 Maximum,
    TArray<FString>& OutValues)
{
    OutValues.Reset();
    const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
    if (!Object.IsValid() || !Object->TryGetArrayField(Field, Values)
        || Values == nullptr || Values->Num() > Maximum)
    {
        return false;
    }
    TSet<FString> Seen;
    for (const TSharedPtr<FJsonValue>& Value : *Values)
    {
        FString Item;
        if (!Value.IsValid() || !Value->TryGetString(Item)
            || Item.IsEmpty() || Seen.Contains(Item))
        {
            return false;
        }
        Seen.Add(Item);
        OutValues.Add(Item);
    }
    return true;
}

bool SerializeProfilingJson(
    const TSharedRef<FJsonObject>& Object,
    FString& OutText)
{
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&OutText);
    return FJsonSerializer::Serialize(Object, Writer);
}

FString BoundedReason(const FString& Value)
{
    FString Result = Value.TrimStartAndEnd();
    Result.ReplaceInline(TEXT("\r"), TEXT(" "));
    Result.ReplaceInline(TEXT("\n"), TEXT(" "));
    while (Result.ReplaceInline(TEXT("  "), TEXT(" ")) > 0) {}
    if (Result.IsEmpty()) Result = TEXT("The Actor study could not start safely.");
    return Result.Left(240);
}
}

bool RenderMasterParseLevelProfilingReportFile(
    const FString& Filename,
    FRenderMasterLevelProfilingReport& OutReport,
    FString& OutError)
{
    FString Text;
    if (!FFileHelper::LoadFileToString(Text, *Filename))
    {
        OutError = FString::Printf(
            TEXT("Could not read level profiling report: %s"), *Filename);
        return false;
    }
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        OutError = TEXT("Level profiling output is not valid JSON.");
        return false;
    }

    FRenderMasterLevelProfilingReport Parsed;
    FString SchemaVersion;
    TSharedPtr<FJsonObject> Run;
    TSharedPtr<FJsonObject> RuntimeReport;
    TSharedPtr<FJsonObject> RuntimeCapture;
    bool bTopRestored = false;
    bool bTopModifies = true;
    bool bRunRestored = false;
    bool bRunModifies = true;
    if (!Root->TryGetStringField(TEXT("schema_version"), SchemaVersion)
        || SchemaVersion != TEXT("0.1")
        || !Root->TryGetStringField(TEXT("report_id"), Parsed.ReportId)
        || !Root->TryGetStringField(TEXT("status"), Parsed.Status)
        || (Parsed.Status != TEXT("complete") && Parsed.Status != TEXT("partial"))
        || !ReadProfilingObject(Root, TEXT("run"), Run)
        || !ReadProfilingObject(
            Run, TEXT("runtime_performance_report"), RuntimeReport)
        || !ReadProfilingObject(RuntimeReport, TEXT("capture"), RuntimeCapture)
        || !Run->TryGetStringField(TEXT("profiling_run_id"), Parsed.ProfilingRunId)
        || !RuntimeCapture->TryGetStringField(
            TEXT("project_name"), Parsed.ProjectName)
        || !RuntimeCapture->TryGetStringField(
            TEXT("world_path"), Parsed.WorldPath)
        || !RuntimeCapture->TryGetStringField(
            TEXT("capture_mode"), Parsed.CaptureMode)
        || !ReadProfilingInteger(
            RuntimeCapture,
            TEXT("viewport_width_px"),
            1,
            16384,
            Parsed.ViewportSize.X)
        || !ReadProfilingInteger(
            RuntimeCapture,
            TEXT("viewport_height_px"),
            1,
            16384,
            Parsed.ViewportSize.Y)
        || !RuntimeCapture->TryGetStringField(TEXT("gpu_name"), Parsed.GpuName)
        || !Root->TryGetStringField(TEXT("level_audit_id"), Parsed.LevelAuditId)
        || !Root->TryGetStringField(TEXT("runtime_capture_id"), Parsed.RuntimeCaptureId)
        || !Root->TryGetStringField(TEXT("gpu_capture_id"), Parsed.GpuCaptureId)
        || !ReadProfilingStringArray(
            Root, TEXT("actor_study_ids"), 2, Parsed.ActorStudyIds)
        || !Root->TryGetStringField(TEXT("summary"), Parsed.Summary)
        || !Root->TryGetStringField(
            TEXT("primary_bottleneck"), Parsed.PrimaryBottleneck)
        || !ReadProfilingNumber(
            Root, TEXT("frame_p95_ms"), 0.0, 60000.0, Parsed.FrameP95Ms)
        || !ReadProfilingNumber(
            Root,
            TEXT("frame_budget_miss_fraction"),
            0.0,
            1.0,
            Parsed.FrameBudgetMissFraction)
        || !ReadOptionalProfilingNumber(Root, TEXT("gpu_p95_ms"), Parsed.GpuP95Ms)
        || !Root->TryGetStringField(
            TEXT("primary_gpu_scope_id"), Parsed.PrimaryGpuScopeId)
        || !Root->TryGetStringField(
            TEXT("primary_gpu_scope_name"), Parsed.PrimaryGpuScopeName)
        || !ReadProfilingNumber(
            Root,
            TEXT("primary_gpu_scope_ms_per_second"),
            0.0,
            1000000000.0,
            Parsed.PrimaryGpuScopeMsPerSecond)
        || !ReadProfilingInteger(
            Root, TEXT("measured_actor_count"), 0, 2, Parsed.MeasuredActorCount)
        || !ReadProfilingInteger(
            Root, TEXT("skipped_actor_count"), 0, 2, Parsed.SkippedActorCount)
        || !ReadProfilingInteger(
            Root,
            TEXT("stable_actor_candidate_count"),
            0,
            2,
            Parsed.StableActorCandidateCount)
        || !Root->TryGetBoolField(
            TEXT("runtime_state_restored"), bTopRestored)
        || !Root->TryGetBoolField(TEXT("modifies_editor_scene"), bTopModifies)
        || !Run->TryGetBoolField(TEXT("runtime_state_restored"), bRunRestored)
        || !Run->TryGetBoolField(TEXT("modifies_editor_scene"), bRunModifies)
        || Parsed.ReportId.IsEmpty()
        || Parsed.ProfilingRunId.IsEmpty()
        || Parsed.LevelAuditId.IsEmpty()
        || Parsed.RuntimeCaptureId.IsEmpty()
        || Parsed.GpuCaptureId.IsEmpty()
        || Parsed.ProjectName.IsEmpty()
        || Parsed.WorldPath.IsEmpty()
        || (Parsed.CaptureMode != TEXT("pie")
            && Parsed.CaptureMode != TEXT("simulate"))
        || Parsed.GpuName.IsEmpty()
        || Parsed.Summary.IsEmpty()
        || Parsed.PrimaryBottleneck.IsEmpty()
        || Parsed.PrimaryGpuScopeId.IsEmpty()
        || Parsed.PrimaryGpuScopeName.IsEmpty()
        || !bTopRestored
        || !bRunRestored
        || bTopModifies
        || bRunModifies)
    {
        OutError = TEXT("Level profiling report metadata or safety flags are invalid.");
        return false;
    }
    if (Parsed.GpuP95Ms.IsSet()
        && (Parsed.GpuP95Ms.GetValue() < 0.0
            || Parsed.GpuP95Ms.GetValue() > 60000.0))
    {
        OutError = TEXT("Level profiling GPU p95 is outside the safe numeric range.");
        return false;
    }

    const TArray<TSharedPtr<FJsonValue>>* FindingValues = nullptr;
    if (!Root->TryGetArrayField(TEXT("actor_findings"), FindingValues)
        || FindingValues == nullptr || FindingValues->Num() > 2)
    {
        OutError = TEXT("Level profiling Actor findings are invalid.");
        return false;
    }
    TSet<FString> AssessmentIds;
    int32 MeasuredCount = 0;
    int32 SkippedCount = 0;
    int32 StableCount = 0;
    for (const TSharedPtr<FJsonValue>& Value : *FindingValues)
    {
        const TSharedPtr<FJsonObject> Json = Value.IsValid() ? Value->AsObject() : nullptr;
        FRenderMasterLevelProfilingActorFinding Finding;
        if (!Json.IsValid()
            || !Json->TryGetStringField(TEXT("assessment_id"), Finding.AssessmentId)
            || !Json->TryGetStringField(TEXT("actor_path"), Finding.ActorPath)
            || !Json->TryGetStringField(TEXT("actor_name"), Finding.ActorName)
            || !ReadProfilingInteger(
                Json, TEXT("static_risk_score"), 50, 100, Finding.StaticRiskScore)
            || !Json->TryGetStringField(TEXT("static_risk_band"), Finding.StaticRiskBand)
            || (Finding.StaticRiskBand != TEXT("high")
                && Finding.StaticRiskBand != TEXT("critical"))
            || !Json->TryGetStringField(TEXT("outcome"), Finding.Outcome)
            || !Json->TryGetStringField(TEXT("conclusion"), Finding.Conclusion)
            || Finding.AssessmentId.IsEmpty()
            || Finding.ActorPath.IsEmpty()
            || Finding.ActorName.IsEmpty()
            || Finding.Conclusion.IsEmpty()
            || AssessmentIds.Contains(Finding.AssessmentId))
        {
            OutError = TEXT("Level profiling Actor finding identity is invalid.");
            return false;
        }
        AssessmentIds.Add(Finding.AssessmentId);
        if (Finding.Outcome == TEXT("skipped"))
        {
            ++SkippedCount;
        }
        else
        {
            const bool bUnresolved = Finding.Outcome == TEXT("unresolved");
            const bool bMeasuredOutcome = Finding.Outcome == TEXT("stable_candidate")
                || Finding.Outcome == TEXT("noise_limited")
                || Finding.Outcome == TEXT("inconsistent_direction");
            if (!bUnresolved && !bMeasuredOutcome)
            {
                OutError = TEXT("Level profiling Actor finding outcome is invalid.");
                return false;
            }
            ++MeasuredCount;
            if (Finding.Outcome == TEXT("stable_candidate")) ++StableCount;
            if (bMeasuredOutcome)
            {
                if (!Json->TryGetStringField(
                        TEXT("primary_aggregate_id"), Finding.PrimaryAggregateId)
                    || !Json->TryGetStringField(TEXT("scope_name"), Finding.ScopeName)
                    || !ReadOptionalProfilingNumber(
                        Json,
                        TEXT("median_delta_ms_per_second"),
                        Finding.MedianDeltaMsPerSecond)
                    || !ReadOptionalProfilingNumber(
                        Json,
                        TEXT("control_noise_ms_per_second"),
                        Finding.ControlNoiseMsPerSecond)
                    || !Finding.MedianDeltaMsPerSecond.IsSet()
                    || !Finding.ControlNoiseMsPerSecond.IsSet()
                    || Finding.ControlNoiseMsPerSecond.GetValue() < 0.0)
                {
                    OutError = TEXT("Level profiling Actor measurement is invalid.");
                    return false;
                }
            }
        }
        Parsed.ActorFindings.Add(Finding);
    }
    if (MeasuredCount != Parsed.MeasuredActorCount
        || SkippedCount != Parsed.SkippedActorCount
        || StableCount != Parsed.StableActorCandidateCount
        || FindingValues->Num() != MeasuredCount + SkippedCount
        || Parsed.ActorStudyIds.Num() != MeasuredCount
        || (Parsed.Status == TEXT("complete") && SkippedCount > 0))
    {
        OutError = TEXT("Level profiling Actor counts are inconsistent.");
        return false;
    }
    if (!ReadProfilingStringArray(
            Root, TEXT("limitations"), 16, Parsed.Limitations)
        || Parsed.Limitations.IsEmpty())
    {
        OutError = TEXT("Level profiling limitations are missing or invalid.");
        return false;
    }
    Parsed.bRuntimeStateRestored = bTopRestored;
    Parsed.bModifiesEditorScene = bTopModifies;
    OutReport = MoveTemp(Parsed);
    return true;
}

FRenderMasterLevelProfilingAssistant::FRenderMasterLevelProfilingAssistant(
    TSharedPtr<FRenderMasterWorkflowController> InWorkflowController,
    TSharedPtr<FRenderMasterLevelAuditAssistant> InLevelAuditAssistant,
    TSharedPtr<FRenderMasterRuntimePerformanceAssistant> InRuntimeAssistant,
    TSharedPtr<FRenderMasterInsightsGpuAssistant> InInsightsGpuAssistant)
    : WorkflowController(MoveTemp(InWorkflowController))
    , LevelAuditAssistant(MoveTemp(InLevelAuditAssistant))
    , RuntimeAssistant(MoveTemp(InRuntimeAssistant))
    , InsightsGpuAssistant(MoveTemp(InInsightsGpuAssistant))
{
}

FRenderMasterLevelProfilingAssistant::~FRenderMasterLevelProfilingAssistant()
{
    Shutdown();
}

void FRenderMasterLevelProfilingAssistant::Initialize()
{
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateSP(
            AsShared(), &FRenderMasterLevelProfilingAssistant::Tick),
        0.2f);
}

void FRenderMasterLevelProfilingAssistant::Shutdown()
{
    OnResultReady.Unbind();
    if (TickHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
        TickHandle.Reset();
    }
    if (IsBusy()) Cancel();
    CloseProcessResources();
}

bool FRenderMasterLevelProfilingAssistant::Start(const FString& Prompt)
{
    if (!CanStart()) return false;
    if (!WorkflowController.IsValid() || !LevelAuditAssistant.IsValid()
        || !RuntimeAssistant.IsValid() || !InsightsGpuAssistant.IsValid())
    {
        Fail(TEXT("The profiling orchestrator is not fully connected."));
        return false;
    }
    UWorld* CurrentPlayWorld = GEditor != nullptr ? GEditor->PlayWorld : nullptr;
    if (CurrentPlayWorld == nullptr)
    {
        Fail(TEXT(
            "Start PIE or SIE, establish a representative camera and workload, then run the profiling audit."));
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
        Fail(TEXT("Profiling runtime paths cannot contain a double quote."));
        return false;
    }

    Request = Prompt.TrimStartAndEnd().IsEmpty()
        ? TEXT("Profile this complete level and identify measured rendering bottlenecks.")
        : Prompt.TrimStartAndEnd();
    ProfilingRunId = FString::Printf(
        TEXT("level_profile_%s"),
        *FGuid::NewGuid().ToString(EGuidFormats::Digits).ToLower());
    CreatedAtUtc = FDateTime::UtcNow().ToIso8601();
    RequestDirectory = FPaths::Combine(
        Root, TEXT("assistant-level-profiling"), ProfilingRunId);
    IFileManager::Get().MakeDirectory(*RequestDirectory, true);
    PromptPath = FPaths::Combine(RequestDirectory, TEXT("request.txt"));
    SkipManifestPath = FPaths::Combine(
        RequestDirectory, TEXT("actor_study_skips.json"));
    ReportOutputPath = FPaths::Combine(
        RequestDirectory, TEXT("level_profiling_report.json"));
    if (!FFileHelper::SaveStringToFile(
            Request,
            *PromptPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        Fail(TEXT("Could not write the level profiling request."));
        return false;
    }

    Report = FRenderMasterLevelProfilingReport();
    Candidates.Reset();
    Skips.Reset();
    ActorStudyReportPaths.Reset();
    CandidateIndex = 0;
    ErrorText.Reset();
    ProcessLog.Reset();
    LevelAuditReportPath.Reset();
    RuntimeReportPath.Reset();
    GpuReportPath.Reset();
    ExpectedLevelAuditId.Reset();
    ExpectedRuntimeCaptureId.Reset();
    ExpectedGpuCaptureId.Reset();
    ExpectedActorStudyIds.Reset();
    PlayWorld = CurrentPlayWorld;

    const FString StaticPrompt = TEXT(
        "Perform a read-only whole-level static triage for a profiling run. "
        "Do not propose any setting changes. Rank only the next measurements and do not claim runtime cost.");
    if (!LevelAuditAssistant->StartAudit(StaticPrompt, true))
    {
        Fail(LevelAuditAssistant->GetSummaryText().ToString());
        return false;
    }
    State = ERenderMasterLevelProfilingState::StaticAudit;
    AppendLog(TEXT(
        "Stage 1/4: capturing read-only whole-level evidence and selecting at most two profiling candidates."));
    return true;
}

bool FRenderMasterLevelProfilingAssistant::Tick(float DeltaTime)
{
    (void)DeltaTime;
    if (IsBusy() && State != ERenderMasterLevelProfilingState::Synthesizing)
    {
        if (GEditor == nullptr || GEditor->PlayWorld != PlayWorld.Get())
        {
            Fail(TEXT("PIE/SIE ended or changed before the profiling run completed."));
            return true;
        }
    }

    switch (State)
    {
        case ERenderMasterLevelProfilingState::StaticAudit:
            if (!LevelAuditAssistant->IsBusy()) AdvanceAfterStaticAudit();
            break;
        case ERenderMasterLevelProfilingState::RuntimeBaseline:
            if (!RuntimeAssistant->IsBusy()) AdvanceAfterRuntimeBaseline();
            break;
        case ERenderMasterLevelProfilingState::GpuScopeTrace:
            if (!InsightsGpuAssistant->IsBusy()) AdvanceAfterGpuScopeTrace();
            break;
        case ERenderMasterLevelProfilingState::ActorStudy:
            if (!InsightsGpuAssistant->IsBusy()) AdvanceAfterActorStudy();
            break;
        case ERenderMasterLevelProfilingState::Synthesizing:
            if (ProcessHandle.IsValid())
            {
                const FString StdOut = FPlatformProcess::ReadPipe(StdOutRead);
                const FString StdErr = FPlatformProcess::ReadPipe(StdErrRead);
                if (!StdOut.IsEmpty()) AppendLog(StdOut.TrimStartAndEnd());
                if (!StdErr.IsEmpty()) AppendLog(StdErr.TrimStartAndEnd());
                if (!FPlatformProcess::IsProcRunning(ProcessHandle))
                    CompleteSynthesisProcess();
            }
            break;
        default:
            break;
    }
    return true;
}

bool FRenderMasterLevelProfilingAssistant::AdvanceAfterStaticAudit()
{
    if (LevelAuditAssistant->GetState() != ERenderMasterLevelAuditState::ReviewOnly)
    {
        Fail(FString::Printf(
            TEXT("Read-only static audit did not complete safely: %s"),
            *LevelAuditAssistant->GetSummaryText().ToString()));
        return false;
    }
    LevelAuditReportPath = LevelAuditAssistant->GetReportOutputPath();
    const FRenderMasterLevelAuditReport& Audit = LevelAuditAssistant->GetReport();
    ExpectedLevelAuditId = Audit.AuditId;
    for (const FRenderMasterLevelAuditAssessment& Assessment : Audit.Assessments)
    {
        if (Candidates.Num() >= 2) break;
        if (Assessment.RecommendedNextTest != TEXT("repeatable_actor_gpu_study"))
            continue;
        FCandidate Candidate;
        Candidate.AssessmentId = Assessment.AssessmentId;
        Candidate.ActorPath = Assessment.ActorPath;
        for (const FRenderMasterPerformanceEvidence& Evidence : Audit.ContextActors)
        {
            if (Evidence.ActorPath == Candidate.ActorPath)
            {
                Candidate.ActorName = Evidence.ActorName;
                break;
            }
        }
        if (Candidate.ActorName.IsEmpty()) Candidate.ActorName = Candidate.ActorPath;
        Candidates.Add(MoveTemp(Candidate));
    }

    const FString RuntimePrompt = TEXT(
        "Diagnose this fixed PIE/SIE baseline against 60 FPS using only the captured runtime timings and memory evidence.");
    if (!RuntimeAssistant->StartReview(RuntimePrompt))
    {
        Fail(RuntimeAssistant->GetSummaryText().ToString());
        return false;
    }
    State = ERenderMasterLevelProfilingState::RuntimeBaseline;
    AppendLog(TEXT(
        "Stage 2/4: warming 30 frames and capturing 120 consecutive runtime frames."));
    return true;
}

bool FRenderMasterLevelProfilingAssistant::AdvanceAfterRuntimeBaseline()
{
    const ERenderMasterRuntimePerformanceState ChildState = RuntimeAssistant->GetState();
    if (ChildState != ERenderMasterRuntimePerformanceState::Complete
        && ChildState != ERenderMasterRuntimePerformanceState::Unresolved)
    {
        Fail(FString::Printf(
            TEXT("Runtime baseline did not complete safely: %s"),
            *RuntimeAssistant->GetSummaryText().ToString()));
        return false;
    }
    RuntimeReportPath = RuntimeAssistant->GetReportOutputPath();
    ExpectedRuntimeCaptureId = RuntimeAssistant->GetCapture().CaptureId;
    const FString GpuPrompt = TEXT(
        "Rank the measured inclusive GPU scopes in this fixed PIE/SIE workload. Do not infer Actor, asset, material, shader, or draw attribution from scope names alone.");
    if (!InsightsGpuAssistant->StartReview(GpuPrompt))
    {
        Fail(InsightsGpuAssistant->GetSummaryText().ToString());
        return false;
    }
    State = ERenderMasterLevelProfilingState::GpuScopeTrace;
    AppendLog(TEXT(
        "Stage 3/4: capturing and parsing five seconds of Unreal Insights GPU scopes."));
    return true;
}

bool FRenderMasterLevelProfilingAssistant::AdvanceAfterGpuScopeTrace()
{
    const ERenderMasterInsightsGpuState ChildState = InsightsGpuAssistant->GetState();
    if (ChildState != ERenderMasterInsightsGpuState::Complete
        && ChildState != ERenderMasterInsightsGpuState::Unresolved)
    {
        Fail(FString::Printf(
            TEXT("GPU scope trace did not complete safely: %s"),
            *InsightsGpuAssistant->GetSummaryText().ToString()));
        return false;
    }
    GpuReportPath = InsightsGpuAssistant->GetReportOutputPath();
    ExpectedGpuCaptureId = InsightsGpuAssistant->GetReport().CaptureId;
    CandidateIndex = 0;
    return StartNextActorStudy();
}

bool FRenderMasterLevelProfilingAssistant::AdvanceAfterActorStudy()
{
    check(Candidates.IsValidIndex(CandidateIndex));
    const FCandidate& Candidate = Candidates[CandidateIndex];
    const ERenderMasterInsightsGpuState ChildState = InsightsGpuAssistant->GetState();
    if ((ChildState == ERenderMasterInsightsGpuState::Complete
            || ChildState == ERenderMasterInsightsGpuState::Unresolved)
        && InsightsGpuAssistant->IsRepeatableActorImpactStudy())
    {
        ActorStudyReportPaths.Add(InsightsGpuAssistant->GetReportOutputPath());
        ExpectedActorStudyIds.Add(
            InsightsGpuAssistant->GetActorImpactStudyReport().StudyId);
        AppendLog(FString::Printf(
            TEXT("Completed repeatable Actor study %d/%d for %s."),
            CandidateIndex + 1,
            Candidates.Num(),
            *Candidate.ActorName));
    }
    else
    {
        FSkip Skip;
        Skip.AssessmentId = Candidate.AssessmentId;
        Skip.Reason = BoundedReason(InsightsGpuAssistant->GetSummaryText().ToString());
        Skips.Add(MoveTemp(Skip));
        AppendLog(FString::Printf(
            TEXT("Skipped Actor study %d/%d for %s: %s"),
            CandidateIndex + 1,
            Candidates.Num(),
            *Candidate.ActorName,
            *Skips.Last().Reason));
    }
    ++CandidateIndex;
    return StartNextActorStudy();
}

bool FRenderMasterLevelProfilingAssistant::StartNextActorStudy()
{
    while (CandidateIndex < Candidates.Num())
    {
        const FCandidate& Candidate = Candidates[CandidateIndex];
        AStaticMeshActor* Actor =
            LevelAuditAssistant->FindCapturedActorByPath(Candidate.ActorPath);
        if (Actor == nullptr)
        {
            FSkip Skip;
            Skip.AssessmentId = Candidate.AssessmentId;
            Skip.Reason = TEXT(
                "The static audit Actor no longer resolves in the Editor world.");
            Skips.Add(MoveTemp(Skip));
            ++CandidateIndex;
            continue;
        }
        const FString StudyPrompt = TEXT(
            "Measure whether temporarily hiding this runtime Actor produces a repeatable GPU-scope change above the two-capture A/A control noise. Use only the eight host-owned captures.");
        if (InsightsGpuAssistant->StartActorImpactStudy(StudyPrompt, Actor))
        {
            State = ERenderMasterLevelProfilingState::ActorStudy;
            AppendLog(FString::Printf(
                TEXT("Stage 4/4: running eight counterbalanced traces for Top-%d Actor %s."),
                CandidateIndex + 1,
                *Candidate.ActorName));
            return true;
        }
        FSkip Skip;
        Skip.AssessmentId = Candidate.AssessmentId;
        Skip.Reason = BoundedReason(InsightsGpuAssistant->GetSummaryText().ToString());
        Skips.Add(MoveTemp(Skip));
        ++CandidateIndex;
    }

    FString Error;
    if (!StartSynthesis(Error))
    {
        Fail(Error);
        return false;
    }
    return true;
}

bool FRenderMasterLevelProfilingAssistant::StartSynthesis(FString& OutError)
{
    if (!WriteSkipManifest(SkipManifestPath, OutError)) return false;
    const FString Python = WorkflowController->GetPythonExecutable();
    FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-level-profiling --prompt-file %s --run-id %s --created-at-utc %s --level-audit-report %s --runtime-report %s --gpu-report %s"),
        *QuoteProfilingArgument(PromptPath),
        *QuoteProfilingArgument(ProfilingRunId),
        *QuoteProfilingArgument(CreatedAtUtc),
        *QuoteProfilingArgument(LevelAuditReportPath),
        *QuoteProfilingArgument(RuntimeReportPath),
        *QuoteProfilingArgument(GpuReportPath));
    for (const FString& StudyReportPath : ActorStudyReportPaths)
    {
        Arguments += TEXT(" --actor-study-report ")
            + QuoteProfilingArgument(StudyReportPath);
    }
    Arguments += FString::Printf(
        TEXT(" --skip-manifest %s --report-id %s --output %s"),
        *QuoteProfilingArgument(SkipManifestPath),
        *QuoteProfilingArgument(ProfilingRunId + TEXT("_report")),
        *QuoteProfilingArgument(ReportOutputPath));
    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        OutError = TEXT("Could not create level profiling synthesis pipes.");
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
    State = ERenderMasterLevelProfilingState::Synthesizing;
    AppendLog(FString::Printf(
        TEXT("Synthesizing the recomputable cross-stage profiling report (process %u)."),
        ProcessId));
    return true;
}

bool FRenderMasterLevelProfilingAssistant::WriteSkipManifest(
    const FString& Filename,
    FString& OutError) const
{
    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    TArray<TSharedPtr<FJsonValue>> Values;
    for (const FSkip& Skip : Skips)
    {
        TSharedRef<FJsonObject> Value = MakeShared<FJsonObject>();
        Value->SetStringField(TEXT("assessment_id"), Skip.AssessmentId);
        Value->SetStringField(TEXT("reason"), BoundedReason(Skip.Reason));
        Values.Add(MakeShared<FJsonValueObject>(Value));
    }
    Root->SetArrayField(TEXT("skips"), Values);
    FString Text;
    if (!SerializeProfilingJson(Root, Text)
        || !FFileHelper::SaveStringToFile(
            Text,
            *Filename,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        OutError = FString::Printf(
            TEXT("Could not write Actor study skip manifest: %s"), *Filename);
        return false;
    }
    return true;
}

void FRenderMasterLevelProfilingAssistant::CompleteSynthesisProcess()
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
                TEXT("Level profiling synthesis exited with code %d."), ReturnCode)
            : ProcessLog);
        return;
    }
    FString Error;
    if (!RenderMasterParseLevelProfilingReportFile(
            ReportOutputPath, Report, Error))
    {
        Fail(Error);
        return;
    }
    if (Report.ProfilingRunId != ProfilingRunId
        || Report.LevelAuditId != ExpectedLevelAuditId
        || Report.RuntimeCaptureId != ExpectedRuntimeCaptureId
        || Report.GpuCaptureId != ExpectedGpuCaptureId
        || Report.ActorStudyIds != ExpectedActorStudyIds)
    {
        Fail(TEXT("Level profiling report does not match the orchestrated captures."));
        return;
    }
    State = Report.Status == TEXT("complete")
        ? ERenderMasterLevelProfilingState::Complete
        : ERenderMasterLevelProfilingState::Partial;
    OnResultReady.ExecuteIfBound();
}

void FRenderMasterLevelProfilingAssistant::Cancel()
{
    if (ProcessHandle.IsValid())
        FPlatformProcess::TerminateProc(ProcessHandle, true);
    CloseProcessResources();
    if (LevelAuditAssistant.IsValid() && LevelAuditAssistant->IsBusy())
        LevelAuditAssistant->Cancel();
    if (RuntimeAssistant.IsValid() && RuntimeAssistant->IsBusy())
        RuntimeAssistant->Dismiss();
    if (InsightsGpuAssistant.IsValid() && InsightsGpuAssistant->IsBusy())
        InsightsGpuAssistant->Dismiss();
    State = ERenderMasterLevelProfilingState::Cancelled;
    AppendLog(TEXT(
        "Profiling audit cancelled. Any temporary runtime Actor state was restored; no level content was saved."));
    OnResultReady.ExecuteIfBound();
}

bool FRenderMasterLevelProfilingAssistant::CanStart() const
{
    return !IsBusy()
        && WorkflowController.IsValid()
        && LevelAuditAssistant.IsValid()
        && RuntimeAssistant.IsValid()
        && InsightsGpuAssistant.IsValid()
        && LevelAuditAssistant->CanStart()
        && RuntimeAssistant->CanStart()
        && InsightsGpuAssistant->CanStart();
}

bool FRenderMasterLevelProfilingAssistant::IsBusy() const
{
    return State == ERenderMasterLevelProfilingState::StaticAudit
        || State == ERenderMasterLevelProfilingState::RuntimeBaseline
        || State == ERenderMasterLevelProfilingState::GpuScopeTrace
        || State == ERenderMasterLevelProfilingState::ActorStudy
        || State == ERenderMasterLevelProfilingState::Synthesizing;
}

FText FRenderMasterLevelProfilingAssistant::GetStateText() const
{
    switch (State)
    {
        case ERenderMasterLevelProfilingState::StaticAudit:
            return FText::FromString(TEXT("Stage 1/4 — selecting candidates"));
        case ERenderMasterLevelProfilingState::RuntimeBaseline:
            return FText::FromString(TEXT("Stage 2/4 — measuring runtime baseline"));
        case ERenderMasterLevelProfilingState::GpuScopeTrace:
            return FText::FromString(TEXT("Stage 3/4 — tracing GPU scopes"));
        case ERenderMasterLevelProfilingState::ActorStudy:
            return FText::FromString(TEXT("Stage 4/4 — repeatable Actor studies"));
        case ERenderMasterLevelProfilingState::Synthesizing:
            return FText::FromString(TEXT("Synthesizing profiling evidence"));
        case ERenderMasterLevelProfilingState::Complete:
            return FText::FromString(TEXT("Profiling audit complete"));
        case ERenderMasterLevelProfilingState::Partial:
            return FText::FromString(TEXT("Profiling audit complete with explicit gaps"));
        case ERenderMasterLevelProfilingState::Failed:
            return FText::FromString(TEXT("Profiling audit failed safely"));
        case ERenderMasterLevelProfilingState::Cancelled:
            return FText::FromString(TEXT("Profiling audit cancelled"));
        default:
            return FText::FromString(TEXT("Ready for a profiling-first level audit"));
    }
}

FText FRenderMasterLevelProfilingAssistant::GetSummaryText() const
{
    if (IsBusy())
    {
        FString ChildSummary;
        if (State == ERenderMasterLevelProfilingState::StaticAudit)
            ChildSummary = LevelAuditAssistant->GetSummaryText().ToString();
        else if (State == ERenderMasterLevelProfilingState::RuntimeBaseline)
            ChildSummary = RuntimeAssistant->GetSummaryText().ToString();
        else if (State == ERenderMasterLevelProfilingState::GpuScopeTrace
            || State == ERenderMasterLevelProfilingState::ActorStudy)
            ChildSummary = InsightsGpuAssistant->GetSummaryText().ToString();
        return FText::FromString(FString::Printf(
            TEXT("%s\n\n%s"),
            *ProcessLog,
            *ChildSummary));
    }
    if (State == ERenderMasterLevelProfilingState::Failed)
        return FText::FromString(ErrorText);
    if (State == ERenderMasterLevelProfilingState::Cancelled)
        return FText::FromString(TEXT(
            "The profiling audit was cancelled. No formal level content was changed or saved."));
    if (State == ERenderMasterLevelProfilingState::Complete
        || State == ERenderMasterLevelProfilingState::Partial)
    {
        TArray<FString> ActorLines;
        for (const FRenderMasterLevelProfilingActorFinding& Finding : Report.ActorFindings)
        {
            ActorLines.Add(FString::Printf(
                TEXT("• %s — %s\n  %s"),
                *Finding.ActorName,
                *Finding.Outcome,
                *Finding.Conclusion));
        }
        const FString GpuP95 = Report.GpuP95Ms.IsSet()
            ? FString::Printf(TEXT("%.3f ms"), Report.GpuP95Ms.GetValue())
            : TEXT("unavailable");
        return FText::FromString(FString::Printf(
            TEXT("%s\n\nMeasured baseline\nFrame p95: %.3f ms\nGPU p95: %s\nFrame-budget misses: %.1f%%\nPrimary bottleneck: %s\n\nTop inclusive GPU scope\n%s — %.3f ms/s\n\nTop-N Actor studies\n%s\n\n%d measured, %d skipped, %d stable above A/A noise. No production level property was changed or saved."),
            *Report.Summary,
            Report.FrameP95Ms,
            *GpuP95,
            Report.FrameBudgetMissFraction * 100.0,
            *Report.PrimaryBottleneck,
            *Report.PrimaryGpuScopeName,
            Report.PrimaryGpuScopeMsPerSecond,
            ActorLines.IsEmpty()
                ? TEXT("No high-priority Actor qualified for the repeatable study.")
                : *FString::Join(ActorLines, TEXT("\n\n")),
            Report.MeasuredActorCount,
            Report.SkippedActorCount,
            Report.StableActorCandidateCount));
    }
    return FText::FromString(TEXT(
        "Start PIE or SIE with a representative fixed camera and workload. The Assistant will run a static funnel, 120-frame baseline, five-second GPU trace, and up to two repeatable eight-capture Actor studies. Images are not used to claim performance."));
}

FLinearColor FRenderMasterLevelProfilingAssistant::GetStateColor() const
{
    switch (State)
    {
        case ERenderMasterLevelProfilingState::Complete:
            return FLinearColor(0.12f, 0.55f, 0.28f, 1.0f);
        case ERenderMasterLevelProfilingState::Partial:
            return FLinearColor(0.68f, 0.45f, 0.08f, 1.0f);
        case ERenderMasterLevelProfilingState::Failed:
            return FLinearColor(0.70f, 0.14f, 0.12f, 1.0f);
        case ERenderMasterLevelProfilingState::Cancelled:
            return FLinearColor(0.32f, 0.32f, 0.34f, 1.0f);
        default:
            return FLinearColor(0.12f, 0.38f, 0.62f, 1.0f);
    }
}

void FRenderMasterLevelProfilingAssistant::CloseProcessResources()
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

void FRenderMasterLevelProfilingAssistant::AppendLog(const FString& Line)
{
    const FString Clean = Line.TrimStartAndEnd();
    if (Clean.IsEmpty()) return;
    if (!ProcessLog.IsEmpty()) ProcessLog += TEXT("\n");
    ProcessLog += Clean;
    constexpr int32 MaxLogChars = 8000;
    if (ProcessLog.Len() > MaxLogChars)
        ProcessLog.RightChopInline(ProcessLog.Len() - MaxLogChars);
}

void FRenderMasterLevelProfilingAssistant::Fail(const FString& Error)
{
    if (ProcessHandle.IsValid())
        FPlatformProcess::TerminateProc(ProcessHandle, true);
    CloseProcessResources();
    if (LevelAuditAssistant.IsValid() && LevelAuditAssistant->IsBusy())
        LevelAuditAssistant->Cancel();
    if (RuntimeAssistant.IsValid() && RuntimeAssistant->IsBusy())
        RuntimeAssistant->Dismiss();
    if (InsightsGpuAssistant.IsValid() && InsightsGpuAssistant->IsBusy())
        InsightsGpuAssistant->Dismiss();
    ErrorText = Error.IsEmpty()
        ? TEXT("The profiling audit failed safely.")
        : Error;
    State = ERenderMasterLevelProfilingState::Failed;
    OnResultReady.ExecuteIfBound();
}
