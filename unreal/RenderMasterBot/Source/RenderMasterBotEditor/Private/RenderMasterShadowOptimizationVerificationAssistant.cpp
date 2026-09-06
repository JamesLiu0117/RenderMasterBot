#include "RenderMasterShadowOptimizationVerificationAssistant.h"

#include "RenderMasterWorkflowController.h"

#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/StaticMeshActor.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"
#include "RenderMasterSha256.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
FString QuoteVerificationArgument(const FString& Value)
{
    return FString::Printf(
        TEXT("\"%s\""), *Value.Replace(TEXT("\""), TEXT("\\\"")));
}

bool IsVerificationSha256(const FString& Value)
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

bool HashVerificationFile(
    const FString& Filename,
    FString& OutSha256,
    FString& OutError)
{
    int64 SizeBytes = 0;
    if (!RenderMasterSha256File(Filename, OutSha256, &SizeBytes)
        || SizeBytes <= 0)
    {
        OutError = FString::Printf(
            TEXT("Verification evidence is empty or unreadable: %s"), *Filename);
        return false;
    }
    return true;
}

bool ReadVerificationObject(
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

bool ReadVerificationNumber(
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

bool ReadOptionalVerificationNumber(
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

bool ReadVerificationReasons(
    const TSharedPtr<FJsonObject>& Object,
    TArray<FString>& OutReasons)
{
    OutReasons.Reset();
    const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
    if (!Object.IsValid()
        || !Object->TryGetArrayField(TEXT("decision_reasons"), Values)
        || Values == nullptr || Values->IsEmpty() || Values->Num() > 8)
    {
        return false;
    }
    for (const TSharedPtr<FJsonValue>& Value : *Values)
    {
        FString Reason;
        if (!Value.IsValid() || !Value->TryGetString(Reason)
            || Reason.IsEmpty() || Reason.Len() > 240)
        {
            return false;
        }
        OutReasons.Add(Reason);
    }
    return true;
}

bool OptionalNumbersMatch(
    const TOptional<double>& A,
    const TOptional<double>& B,
    const double Tolerance = 0.001)
{
    return A.IsSet() == B.IsSet()
        && (!A.IsSet()
            || FMath::IsNearlyEqual(A.GetValue(), B.GetValue(), Tolerance));
}

bool VerificationRegressed(
    const double Baseline,
    const double Candidate,
    const double AbsoluteLimit,
    const double RelativeLimitPercent)
{
    return Candidate - Baseline > FMath::Max(
        AbsoluteLimit, Baseline * RelativeLimitPercent / 100.0) + 0.001;
}

bool WorkloadsMatch(
    const FRenderMasterLevelProfilingReport& Baseline,
    const FRenderMasterLevelProfilingReport& Candidate,
    const FRenderMasterShadowOptimizationReport& Shadow)
{
    return Baseline.ProjectName == Candidate.ProjectName
        && Baseline.ProjectName == Shadow.ProjectName
        && Baseline.WorldPath == Candidate.WorldPath
        && Baseline.WorldPath == Shadow.WorldPath
        && Baseline.CaptureMode == Candidate.CaptureMode
        && Baseline.CaptureMode == Shadow.CaptureMode
        && Baseline.ViewportSize == Candidate.ViewportSize
        && Baseline.ViewportSize == Shadow.ViewportSize
        && Baseline.GpuName == Candidate.GpuName
        && Baseline.GpuName == Shadow.GpuName;
}
}

bool RenderMasterParseShadowVerificationReportFile(
    const FString& Filename,
    FRenderMasterShadowVerificationReport& OutReport,
    FString& OutError)
{
    OutReport = FRenderMasterShadowVerificationReport();
    FString Text;
    if (!FFileHelper::LoadFileToString(Text, *Filename))
    {
        OutError = FString::Printf(
            TEXT("Could not read shadow verification report: %s"), *Filename);
        return false;
    }
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        OutError = TEXT("Shadow verification output is not valid JSON.");
        return false;
    }

    TSharedPtr<FJsonObject> ShadowSource;
    TSharedPtr<FJsonObject> ShadowCaptureSet;
    TSharedPtr<FJsonObject> BaselineSource;
    TSharedPtr<FJsonObject> BaselineRun;
    TSharedPtr<FJsonObject> CandidateSource;
    TSharedPtr<FJsonObject> CandidateRun;
    TSharedPtr<FJsonObject> Thresholds;
    TSharedPtr<FJsonObject> Metrics;
    FString SchemaVersion;
    FString AnalysisMethod;
    FString VisualRole;
    FString ShadowCanonicalSha;
    FString BaselineCanonicalSha;
    FString CandidateCanonicalSha;
    bool bRetainingRequiresAction = true;
    double FrameAbsolute = 0.0;
    double FrameRelative = 0.0;
    double GpuAbsolute = 0.0;
    double GpuRelative = 0.0;
    double BudgetAbsolute = 0.0;
    double ScopeAbsolute = 0.0;
    double ScopeRelative = 0.0;
    if (!Root->TryGetStringField(TEXT("schema_version"), SchemaVersion)
        || SchemaVersion != TEXT("0.1")
        || !Root->TryGetStringField(TEXT("report_id"), OutReport.ReportId)
        || !Root->TryGetStringField(TEXT("status"), OutReport.Status)
        || !Root->TryGetStringField(TEXT("decision"), OutReport.Decision)
        || !Root->TryGetStringField(TEXT("analysis_method"), AnalysisMethod)
        || AnalysisMethod != TEXT("deterministic_host")
        || !Root->TryGetStringField(TEXT("summary"), OutReport.Summary)
        || !Root->TryGetBoolField(
            TEXT("workload_matches"), OutReport.bWorkloadMatches)
        || !Root->TryGetBoolField(
            TEXT("target_transition_matches"),
            OutReport.bTargetTransitionMatches)
        || !Root->TryGetBoolField(
            TEXT("sources_are_distinct"), OutReport.bSourcesAreDistinct)
        || !Root->TryGetBoolField(
            TEXT("formal_change_applied"), OutReport.bFormalChangeApplied)
        || !Root->TryGetBoolField(
            TEXT("modifies_editor_scene"), OutReport.bModifiesEditorScene)
        || !Root->TryGetBoolField(
            TEXT("retaining_requires_action"), bRetainingRequiresAction)
        || !Root->TryGetBoolField(
            TEXT("rollback_requires_approval"),
            OutReport.bRollbackRequiresApproval)
        || !Root->TryGetBoolField(
            TEXT("rollback_undo_supported"),
            OutReport.bRollbackUndoSupported)
        || !Root->TryGetBoolField(TEXT("auto_save"), OutReport.bAutoSave)
        || !Root->TryGetStringField(TEXT("visual_evidence_role"), VisualRole)
        || VisualRole != TEXT("operator_regression_review")
        || !Root->TryGetStringField(
            TEXT("shadow_optimization_report_sha256"), ShadowCanonicalSha)
        || !Root->TryGetStringField(
            TEXT("shadow_optimization_report_file_sha256"),
            OutReport.ShadowReportFileSha256)
        || !Root->TryGetStringField(
            TEXT("baseline_level_profiling_report_sha256"),
            BaselineCanonicalSha)
        || !Root->TryGetStringField(
            TEXT("baseline_level_profiling_report_file_sha256"),
            OutReport.BaselineReportFileSha256)
        || !Root->TryGetStringField(
            TEXT("candidate_level_profiling_report_sha256"),
            CandidateCanonicalSha)
        || !Root->TryGetStringField(
            TEXT("candidate_level_profiling_report_file_sha256"),
            OutReport.CandidateReportFileSha256)
        || !ReadVerificationObject(
            Root, TEXT("shadow_optimization_report"), ShadowSource)
        || !ShadowSource->TryGetStringField(
            TEXT("report_id"), OutReport.ShadowReportId)
        || !ReadVerificationObject(
            ShadowSource, TEXT("capture_set"), ShadowCaptureSet)
        || !ShadowCaptureSet->TryGetStringField(
            TEXT("experiment_id"), OutReport.ShadowExperimentId)
        || !ReadVerificationObject(
            Root, TEXT("baseline_level_profiling_report"), BaselineSource)
        || !BaselineSource->TryGetStringField(
            TEXT("report_id"), OutReport.BaselineReportId)
        || !ReadVerificationObject(BaselineSource, TEXT("run"), BaselineRun)
        || !BaselineRun->TryGetStringField(
            TEXT("profiling_run_id"), OutReport.BaselineProfilingRunId)
        || !ReadVerificationObject(
            Root, TEXT("candidate_level_profiling_report"), CandidateSource)
        || !CandidateSource->TryGetStringField(
            TEXT("report_id"), OutReport.CandidateReportId)
        || !ReadVerificationObject(CandidateSource, TEXT("run"), CandidateRun)
        || !CandidateRun->TryGetStringField(
            TEXT("profiling_run_id"), OutReport.CandidateProfilingRunId)
        || !ReadVerificationObject(Root, TEXT("thresholds"), Thresholds)
        || !Thresholds->TryGetNumberField(
            TEXT("maximum_frame_p95_regression_ms"), FrameAbsolute)
        || !Thresholds->TryGetNumberField(
            TEXT("maximum_frame_p95_regression_percent"), FrameRelative)
        || !Thresholds->TryGetNumberField(
            TEXT("maximum_gpu_p95_regression_ms"), GpuAbsolute)
        || !Thresholds->TryGetNumberField(
            TEXT("maximum_gpu_p95_regression_percent"), GpuRelative)
        || !Thresholds->TryGetNumberField(
            TEXT("maximum_frame_budget_miss_increase_fraction"), BudgetAbsolute)
        || !Thresholds->TryGetNumberField(
            TEXT("maximum_target_scope_regression_ms_per_second"), ScopeAbsolute)
        || !Thresholds->TryGetNumberField(
            TEXT("maximum_target_scope_regression_percent"), ScopeRelative)
        || !FMath::IsNearlyEqual(FrameAbsolute, 0.5)
        || !FMath::IsNearlyEqual(FrameRelative, 2.0)
        || !FMath::IsNearlyEqual(GpuAbsolute, 0.5)
        || !FMath::IsNearlyEqual(GpuRelative, 2.0)
        || !FMath::IsNearlyEqual(BudgetAbsolute, 0.02)
        || !FMath::IsNearlyEqual(ScopeAbsolute, 1.0)
        || !FMath::IsNearlyEqual(ScopeRelative, 2.0)
        || !ReadVerificationObject(Root, TEXT("metrics"), Metrics)
        || !ReadVerificationNumber(
            Metrics,
            TEXT("baseline_frame_p95_ms"),
            0.0,
            60000.0,
            OutReport.Metrics.BaselineFrameP95Ms)
        || !ReadVerificationNumber(
            Metrics,
            TEXT("candidate_frame_p95_ms"),
            0.0,
            60000.0,
            OutReport.Metrics.CandidateFrameP95Ms)
        || !ReadVerificationNumber(
            Metrics,
            TEXT("frame_p95_delta_ms"),
            -60000.0,
            60000.0,
            OutReport.Metrics.FrameP95DeltaMs)
        || !ReadOptionalVerificationNumber(
            Metrics,
            TEXT("frame_p95_delta_percent"),
            OutReport.Metrics.FrameP95DeltaPercent)
        || !ReadVerificationNumber(
            Metrics,
            TEXT("baseline_frame_budget_miss_fraction"),
            0.0,
            1.0,
            OutReport.Metrics.BaselineFrameBudgetMissFraction)
        || !ReadVerificationNumber(
            Metrics,
            TEXT("candidate_frame_budget_miss_fraction"),
            0.0,
            1.0,
            OutReport.Metrics.CandidateFrameBudgetMissFraction)
        || !ReadVerificationNumber(
            Metrics,
            TEXT("frame_budget_miss_delta_fraction"),
            -1.0,
            1.0,
            OutReport.Metrics.FrameBudgetMissDeltaFraction)
        || !ReadOptionalVerificationNumber(
            Metrics, TEXT("baseline_gpu_p95_ms"), OutReport.Metrics.BaselineGpuP95Ms)
        || !ReadOptionalVerificationNumber(
            Metrics, TEXT("candidate_gpu_p95_ms"), OutReport.Metrics.CandidateGpuP95Ms)
        || !ReadOptionalVerificationNumber(
            Metrics, TEXT("gpu_p95_delta_ms"), OutReport.Metrics.GpuP95DeltaMs)
        || !ReadOptionalVerificationNumber(
            Metrics,
            TEXT("gpu_p95_delta_percent"),
            OutReport.Metrics.GpuP95DeltaPercent)
        || !Metrics->TryGetStringField(
            TEXT("target_scope_queue_name"),
            OutReport.Metrics.TargetScopeQueueName)
        || !Metrics->TryGetStringField(
            TEXT("target_scope_name"), OutReport.Metrics.TargetScopeName)
        || !ReadOptionalVerificationNumber(
            Metrics,
            TEXT("baseline_target_scope_ms_per_second"),
            OutReport.Metrics.BaselineTargetScopeMsPerSecond)
        || !ReadOptionalVerificationNumber(
            Metrics,
            TEXT("candidate_target_scope_ms_per_second"),
            OutReport.Metrics.CandidateTargetScopeMsPerSecond)
        || !ReadOptionalVerificationNumber(
            Metrics,
            TEXT("target_scope_delta_ms_per_second"),
            OutReport.Metrics.TargetScopeDeltaMsPerSecond)
        || !ReadOptionalVerificationNumber(
            Metrics,
            TEXT("target_scope_delta_percent"),
            OutReport.Metrics.TargetScopeDeltaPercent)
        || !ReadVerificationReasons(Root, OutReport.DecisionReasons))
    {
        OutError = TEXT("Shadow verification metadata, thresholds, or metrics are invalid.");
        return false;
    }

    if (OutReport.ReportId.IsEmpty() || OutReport.Summary.IsEmpty()
        || OutReport.ShadowReportId.IsEmpty()
        || OutReport.ShadowExperimentId.IsEmpty()
        || OutReport.BaselineReportId.IsEmpty()
        || OutReport.BaselineProfilingRunId.IsEmpty()
        || OutReport.CandidateReportId.IsEmpty()
        || OutReport.CandidateProfilingRunId.IsEmpty()
        || OutReport.Metrics.TargetScopeQueueName.IsEmpty()
        || OutReport.Metrics.TargetScopeName.IsEmpty()
        || !IsVerificationSha256(ShadowCanonicalSha)
        || !IsVerificationSha256(OutReport.ShadowReportFileSha256)
        || !IsVerificationSha256(BaselineCanonicalSha)
        || !IsVerificationSha256(OutReport.BaselineReportFileSha256)
        || !IsVerificationSha256(CandidateCanonicalSha)
        || !IsVerificationSha256(OutReport.CandidateReportFileSha256)
        || !OutReport.bFormalChangeApplied || OutReport.bModifiesEditorScene
        || bRetainingRequiresAction || !OutReport.bRollbackRequiresApproval
        || !OutReport.bRollbackUndoSupported || OutReport.bAutoSave)
    {
        OutError = TEXT("Shadow verification safety flags or identities are invalid.");
        return false;
    }

    TOptional<double> BaselineSourceGpu;
    TOptional<double> CandidateSourceGpu;
    if (!ReadOptionalVerificationNumber(
            BaselineSource, TEXT("gpu_p95_ms"), BaselineSourceGpu)
        || !ReadOptionalVerificationNumber(
            CandidateSource, TEXT("gpu_p95_ms"), CandidateSourceGpu))
    {
        OutError = TEXT("Shadow verification embedded GPU summaries are invalid.");
        return false;
    }
    double BaselineSourceFrame = 0.0;
    double CandidateSourceFrame = 0.0;
    double BaselineSourceMisses = 0.0;
    double CandidateSourceMisses = 0.0;
    if (!ReadVerificationNumber(
            BaselineSource,
            TEXT("frame_p95_ms"),
            0.0,
            60000.0,
            BaselineSourceFrame)
        || !ReadVerificationNumber(
            CandidateSource,
            TEXT("frame_p95_ms"),
            0.0,
            60000.0,
            CandidateSourceFrame)
        || !ReadVerificationNumber(
            BaselineSource,
            TEXT("frame_budget_miss_fraction"),
            0.0,
            1.0,
            BaselineSourceMisses)
        || !ReadVerificationNumber(
            CandidateSource,
            TEXT("frame_budget_miss_fraction"),
            0.0,
            1.0,
            CandidateSourceMisses)
        || !FMath::IsNearlyEqual(
            BaselineSourceFrame, OutReport.Metrics.BaselineFrameP95Ms, 0.001)
        || !FMath::IsNearlyEqual(
            CandidateSourceFrame, OutReport.Metrics.CandidateFrameP95Ms, 0.001)
        || !FMath::IsNearlyEqual(
            CandidateSourceFrame - BaselineSourceFrame,
            OutReport.Metrics.FrameP95DeltaMs,
            0.001)
        || !FMath::IsNearlyEqual(
            BaselineSourceMisses,
            OutReport.Metrics.BaselineFrameBudgetMissFraction,
            0.0001)
        || !FMath::IsNearlyEqual(
            CandidateSourceMisses,
            OutReport.Metrics.CandidateFrameBudgetMissFraction,
            0.0001)
        || !FMath::IsNearlyEqual(
            CandidateSourceMisses - BaselineSourceMisses,
            OutReport.Metrics.FrameBudgetMissDeltaFraction,
            0.0001)
        || !OptionalNumbersMatch(
            BaselineSourceGpu, OutReport.Metrics.BaselineGpuP95Ms)
        || !OptionalNumbersMatch(
            CandidateSourceGpu, OutReport.Metrics.CandidateGpuP95Ms))
    {
        OutError = TEXT("Shadow verification metrics do not match embedded source summaries.");
        return false;
    }
    if (BaselineSourceGpu.IsSet() && CandidateSourceGpu.IsSet())
    {
        const TOptional<double> ExpectedDelta(
            CandidateSourceGpu.GetValue() - BaselineSourceGpu.GetValue());
        if (!OptionalNumbersMatch(ExpectedDelta, OutReport.Metrics.GpuP95DeltaMs))
        {
            OutError = TEXT("Shadow verification GPU delta is not recomputable.");
            return false;
        }
    }
    else if (BaselineSourceGpu.IsSet() || CandidateSourceGpu.IsSet()
        || OutReport.Metrics.GpuP95DeltaMs.IsSet())
    {
        OutError = TEXT("Shadow verification GPU availability is inconsistent.");
        return false;
    }

    const bool bScopeAllSet =
        OutReport.Metrics.BaselineTargetScopeMsPerSecond.IsSet()
        && OutReport.Metrics.CandidateTargetScopeMsPerSecond.IsSet()
        && OutReport.Metrics.TargetScopeDeltaMsPerSecond.IsSet();
    const bool bScopeAnySet =
        OutReport.Metrics.BaselineTargetScopeMsPerSecond.IsSet()
        || OutReport.Metrics.CandidateTargetScopeMsPerSecond.IsSet()
        || OutReport.Metrics.TargetScopeDeltaMsPerSecond.IsSet()
        || OutReport.Metrics.TargetScopeDeltaPercent.IsSet();
    if ((bScopeAnySet && !bScopeAllSet)
        || (bScopeAllSet
            && !FMath::IsNearlyEqual(
                OutReport.Metrics.CandidateTargetScopeMsPerSecond.GetValue()
                    - OutReport.Metrics.BaselineTargetScopeMsPerSecond.GetValue(),
                OutReport.Metrics.TargetScopeDeltaMsPerSecond.GetValue(),
                0.001)))
    {
        OutError = TEXT("Shadow verification target-scope metrics are inconsistent.");
        return false;
    }

    FString ExpectedDecision;
    if (!OutReport.bWorkloadMatches
        || !OutReport.bTargetTransitionMatches
        || !OutReport.bSourcesAreDistinct)
    {
        ExpectedDecision = TEXT("unresolved");
    }
    else
    {
        bool bRegression = VerificationRegressed(
            OutReport.Metrics.BaselineFrameP95Ms,
            OutReport.Metrics.CandidateFrameP95Ms,
            0.5,
            2.0)
            || OutReport.Metrics.FrameBudgetMissDeltaFraction > 0.0201;
        if (OutReport.Metrics.BaselineGpuP95Ms.IsSet()
            && OutReport.Metrics.CandidateGpuP95Ms.IsSet())
        {
            bRegression = bRegression || VerificationRegressed(
                OutReport.Metrics.BaselineGpuP95Ms.GetValue(),
                OutReport.Metrics.CandidateGpuP95Ms.GetValue(),
                0.5,
                2.0);
        }
        if (bScopeAllSet)
        {
            bRegression = bRegression || VerificationRegressed(
                OutReport.Metrics.BaselineTargetScopeMsPerSecond.GetValue(),
                OutReport.Metrics.CandidateTargetScopeMsPerSecond.GetValue(),
                1.0,
                2.0);
        }
        ExpectedDecision = bRegression ? TEXT("rollback") : TEXT("retain");
    }
    const FString ExpectedStatus = ExpectedDecision == TEXT("unresolved")
        ? TEXT("unresolved")
        : TEXT("complete");
    if (OutReport.Decision != ExpectedDecision
        || OutReport.Status != ExpectedStatus)
    {
        OutError = TEXT("Shadow verification decision is not recomputable.");
        return false;
    }
    return true;
}

FRenderMasterShadowOptimizationVerificationAssistant::
    FRenderMasterShadowOptimizationVerificationAssistant(
        TSharedPtr<FRenderMasterWorkflowController> InWorkflowController,
        TSharedPtr<FRenderMasterLevelProfilingAssistant> InLevelProfilingAssistant)
    : WorkflowController(MoveTemp(InWorkflowController))
    , LevelProfilingAssistant(MoveTemp(InLevelProfilingAssistant))
{
}

FRenderMasterShadowOptimizationVerificationAssistant::
    ~FRenderMasterShadowOptimizationVerificationAssistant()
{
    Shutdown();
}

void FRenderMasterShadowOptimizationVerificationAssistant::Initialize()
{
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateSP(
            AsShared(),
            &FRenderMasterShadowOptimizationVerificationAssistant::Tick),
        0.2f);
}

void FRenderMasterShadowOptimizationVerificationAssistant::Shutdown()
{
    if (TickHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
        TickHandle.Reset();
    }
    if (IsBusy()) Cancel();
    CloseProcessResources();
}

bool FRenderMasterShadowOptimizationVerificationAssistant::ArmAfterApply(
    AStaticMeshActor* Actor,
    UStaticMeshComponent* Component,
    const FRenderMasterPerformanceAction& InAppliedAction,
    const FString& InBaselineReportPath,
    const FRenderMasterLevelProfilingReport& InBaselineReport,
    const FString& InShadowReportPath,
    const FRenderMasterShadowOptimizationReport& InShadowReport)
{
    if (IsBusy() || Actor == nullptr || Component == nullptr
        || Actor->GetStaticMeshComponent() != Component
        || GEditor == nullptr || GEditor->PlayWorld != nullptr
        || InShadowReport.Status != TEXT("proposed")
        || InShadowReport.Decision != TEXT("propose_apply")
        || !InShadowReport.bHasProposedAction
        || !RenderMasterPerformanceEvidenceMatches(
            InAppliedAction.Target, InShadowReport.ProposedAction.Target)
        || !RenderMasterPerformanceSnapshotsMatch(
            InAppliedAction.Before, InShadowReport.ProposedAction.Before)
        || !RenderMasterPerformanceSnapshotsMatch(
            InAppliedAction.After, InShadowReport.ProposedAction.After)
        || !InAppliedAction.Before.bCastShadow
        || InAppliedAction.After.bCastShadow
        || !FMath::IsNearlyEqual(
            InAppliedAction.Before.MaxDrawDistanceCm,
            InAppliedAction.After.MaxDrawDistanceCm)
        || !WorkloadsMatch(InBaselineReport, InBaselineReport, InShadowReport))
    {
        Fail(TEXT(
            "Could not freeze the applied shadow optimization and its baseline evidence safely."));
        return false;
    }
    bool bStableTarget = false;
    for (const FRenderMasterLevelProfilingActorFinding& Finding
        : InBaselineReport.ActorFindings)
    {
        if (Finding.ActorPath == InAppliedAction.Target.ActorPath
            && Finding.Outcome == TEXT("stable_candidate"))
        {
            bStableTarget = true;
            break;
        }
    }
    FRenderMasterPerformanceEvidence Current;
    FRenderMasterPerformanceEvidence ExpectedCurrent = InAppliedAction.Target;
    ExpectedCurrent.Before = InAppliedAction.After;
    FString Error;
    if (!bStableTarget
        || !RenderMasterCapturePerformanceEvidence(
            Actor, Component, Current, Error)
        || !RenderMasterPerformanceEvidenceMatches(Current, ExpectedCurrent)
        || !HashVerificationFile(
            InBaselineReportPath, BaselineReportFileSha256, Error)
        || !HashVerificationFile(
            InShadowReportPath, ShadowReportFileSha256, Error))
    {
        Fail(Error.IsEmpty()
            ? TEXT("Applied shadow evidence changed before verification was armed.")
            : Error);
        return false;
    }

    TargetActor = Actor;
    TargetComponent = Component;
    AppliedAction = InAppliedAction;
    BaselineReportPath = InBaselineReportPath;
    BaselineReport = InBaselineReport;
    ShadowReportPath = InShadowReportPath;
    ShadowReport = InShadowReport;
    CandidateReport = FRenderMasterLevelProfilingReport();
    CandidateReportPath.Reset();
    CandidateReportFileSha256.Reset();
    Report = FRenderMasterShadowVerificationReport();
    VerificationId = FString::Printf(
        TEXT("shadow_verify_%s"),
        *FGuid::NewGuid().ToString(EGuidFormats::Digits).ToLower());
    const FString Root = WorkflowController.IsValid()
        ? WorkflowController->GetWorkflowRoot()
        : FString();
    RequestDirectory = FPaths::Combine(
        Root,
        TEXT("assistant-shadow-optimization-verification"),
        VerificationId);
    IFileManager::Get().MakeDirectory(*RequestDirectory, true);
    ReportOutputPath = FPaths::Combine(
        RequestDirectory, TEXT("shadow_optimization_verification_report.json"));
    ErrorText.Reset();
    ProcessLog.Reset();
    State = ERenderMasterShadowVerificationState::Armed;
    AppendLog(TEXT(
        "The approved formal change and all Before evidence were frozen. Start the same PIE/SIE workload to run fresh whole-level verification."));
    return true;
}

bool FRenderMasterShadowOptimizationVerificationAssistant::StartVerification(
    const FString& Prompt)
{
    if (!CanStartVerification()) return false;
    FString Error;
    if (!RevalidateAppliedTarget(Error))
    {
        Fail(Error);
        return false;
    }
    const FString Request = Prompt.TrimStartAndEnd().IsEmpty()
        ? TEXT(
            "Repeat the complete fixed profiling workload after the approved Cast Shadow optimization. Do not propose or apply any additional scene action.")
        : Prompt.TrimStartAndEnd();
    if (!LevelProfilingAssistant->Start(Request))
    {
        Fail(LevelProfilingAssistant->GetSummaryText().ToString());
        return false;
    }
    State = ERenderMasterShadowVerificationState::Profiling;
    AppendLog(TEXT(
        "Running the full post-apply static audit, 120-frame baseline, GPU trace, and deterministic Actor studies. Keep the workload unchanged."));
    return true;
}

bool FRenderMasterShadowOptimizationVerificationAssistant::Tick(float DeltaTime)
{
    (void)DeltaTime;
    if (State == ERenderMasterShadowVerificationState::Profiling
        && LevelProfilingAssistant.IsValid()
        && !LevelProfilingAssistant->IsBusy())
    {
        const ERenderMasterLevelProfilingState ChildState =
            LevelProfilingAssistant->GetState();
        if (ChildState == ERenderMasterLevelProfilingState::Complete
            || ChildState == ERenderMasterLevelProfilingState::Partial)
        {
            CandidateReport = LevelProfilingAssistant->GetReport();
            CandidateReportPath = LevelProfilingAssistant->GetReportOutputPath();
            FString Error;
            if (!StartAnalysis(Error)) Fail(Error);
        }
        else if (ChildState == ERenderMasterLevelProfilingState::Cancelled)
        {
            State = ERenderMasterShadowVerificationState::Cancelled;
            AppendLog(TEXT(
                "Post-apply profiling was cancelled. The formal shadow change remains unsaved and can still be rolled back explicitly."));
        }
        else
        {
            Fail(LevelProfilingAssistant->GetSummaryText().ToString());
        }
    }
    else if (State == ERenderMasterShadowVerificationState::Analyzing
        && ProcessHandle.IsValid())
    {
        const FString StdOut = FPlatformProcess::ReadPipe(StdOutRead);
        const FString StdErr = FPlatformProcess::ReadPipe(StdErrRead);
        if (!StdOut.IsEmpty()) AppendLog(StdOut.TrimStartAndEnd());
        if (!StdErr.IsEmpty()) AppendLog(StdErr.TrimStartAndEnd());
        if (!FPlatformProcess::IsProcRunning(ProcessHandle))
            CompleteAnalysisProcess();
    }
    return true;
}

bool FRenderMasterShadowOptimizationVerificationAssistant::StartAnalysis(
    FString& OutError)
{
    FString CurrentBaselineHash;
    FString CurrentShadowHash;
    if (!HashVerificationFile(
            BaselineReportPath, CurrentBaselineHash, OutError)
        || !HashVerificationFile(
            ShadowReportPath, CurrentShadowHash, OutError)
        || CurrentBaselineHash != BaselineReportFileSha256
        || CurrentShadowHash != ShadowReportFileSha256
        || !HashVerificationFile(
            CandidateReportPath, CandidateReportFileSha256, OutError))
    {
        if (OutError.IsEmpty())
            OutError = TEXT(
                "Frozen Before or shadow evidence changed before post-apply analysis.");
        return false;
    }
    const FString Python = WorkflowController->GetPythonExecutable();
    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-shadow-optimization-verification --shadow-report %s --baseline-level-report %s --candidate-level-report %s --report-id %s --output %s"),
        *QuoteVerificationArgument(ShadowReportPath),
        *QuoteVerificationArgument(BaselineReportPath),
        *QuoteVerificationArgument(CandidateReportPath),
        *QuoteVerificationArgument(VerificationId + TEXT("_report")),
        *QuoteVerificationArgument(ReportOutputPath));
    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        OutError = TEXT("Could not create shadow verification analysis pipes.");
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
    State = ERenderMasterShadowVerificationState::Analyzing;
    AppendLog(FString::Printf(
        TEXT("Comparing fresh whole-level evidence with fixed host guardrails (process %u)."),
        ProcessId));
    return true;
}

void FRenderMasterShadowOptimizationVerificationAssistant::CompleteAnalysisProcess()
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
                TEXT("Shadow verification analysis exited with code %d."), ReturnCode)
            : ProcessLog);
        return;
    }
    FString Error;
    if (!RenderMasterParseShadowVerificationReportFile(
            ReportOutputPath, Report, Error))
    {
        Fail(Error);
        return;
    }
    const bool bExpectedWorkload = WorkloadsMatch(
        BaselineReport, CandidateReport, ShadowReport);
    const bool bExpectedDistinct = BaselineReportFileSha256
            != CandidateReportFileSha256
        && BaselineReport.ReportId != CandidateReport.ReportId
        && BaselineReport.ProfilingRunId != CandidateReport.ProfilingRunId
        && BaselineReport.RuntimeCaptureId != CandidateReport.RuntimeCaptureId
        && BaselineReport.GpuCaptureId != CandidateReport.GpuCaptureId;
    if (Report.ShadowReportId != ShadowReport.ReportId
        || Report.ShadowExperimentId != ShadowReport.ExperimentId
        || Report.ShadowReportFileSha256 != ShadowReportFileSha256
        || Report.BaselineReportId != BaselineReport.ReportId
        || Report.BaselineProfilingRunId != BaselineReport.ProfilingRunId
        || Report.BaselineReportFileSha256 != BaselineReportFileSha256
        || Report.CandidateReportId != CandidateReport.ReportId
        || Report.CandidateProfilingRunId != CandidateReport.ProfilingRunId
        || Report.CandidateReportFileSha256 != CandidateReportFileSha256
        || Report.bWorkloadMatches != bExpectedWorkload
        || Report.bSourcesAreDistinct != bExpectedDistinct
        || !FMath::IsNearlyEqual(
            Report.Metrics.BaselineFrameP95Ms,
            BaselineReport.FrameP95Ms,
            0.001)
        || !FMath::IsNearlyEqual(
            Report.Metrics.CandidateFrameP95Ms,
            CandidateReport.FrameP95Ms,
            0.001)
        || !FMath::IsNearlyEqual(
            Report.Metrics.BaselineFrameBudgetMissFraction,
            BaselineReport.FrameBudgetMissFraction,
            0.0001)
        || !FMath::IsNearlyEqual(
            Report.Metrics.CandidateFrameBudgetMissFraction,
            CandidateReport.FrameBudgetMissFraction,
            0.0001)
        || !OptionalNumbersMatch(
            Report.Metrics.BaselineGpuP95Ms, BaselineReport.GpuP95Ms)
        || !OptionalNumbersMatch(
            Report.Metrics.CandidateGpuP95Ms, CandidateReport.GpuP95Ms)
        || !RevalidateAppliedTarget(Error))
    {
        Fail(Error.IsEmpty()
            ? TEXT(
                "Shadow verification output does not match the frozen sources or current formal target.")
            : Error);
        return;
    }
    State = Report.Decision == TEXT("retain")
        ? ERenderMasterShadowVerificationState::RetainRecommended
        : Report.Decision == TEXT("rollback")
        ? ERenderMasterShadowVerificationState::RollbackRecommended
        : ERenderMasterShadowVerificationState::Unresolved;
}

bool FRenderMasterShadowOptimizationVerificationAssistant::
    RevalidateAppliedTarget(FString& OutError) const
{
    AStaticMeshActor* Actor = TargetActor.Get();
    UStaticMeshComponent* Component = TargetComponent.Get();
    FRenderMasterPerformanceEvidence Current;
    FRenderMasterPerformanceEvidence ExpectedCurrent = AppliedAction.Target;
    ExpectedCurrent.Before = AppliedAction.After;
    if (!RenderMasterCapturePerformanceEvidence(
            Actor, Component, Current, OutError)
        || !RenderMasterPerformanceEvidenceMatches(Current, ExpectedCurrent)
        || !RenderMasterPerformanceSnapshotsMatch(
            Current.Before, AppliedAction.After))
    {
        if (OutError.IsEmpty())
            OutError = TEXT(
                "The formal shadow target changed after the approved optimization.");
        return false;
    }
    return true;
}

bool FRenderMasterShadowOptimizationVerificationAssistant::
    KeepAppliedOptimization()
{
    if (!CanKeepAppliedOptimization()) return false;
    FString Error;
    if (!RevalidateAppliedTarget(Error))
    {
        Fail(Error);
        return false;
    }
    State = ERenderMasterShadowVerificationState::Retained;
    AppendLog(Report.Decision == TEXT("retain")
        ? TEXT(
            "The data-backed retain recommendation was accepted. The level remains unsaved; Ctrl+Z can still undo the original formal action.")
        : TEXT(
            "The operator kept the applied change despite an unresolved or rollback recommendation. No performance-success claim was created and the level remains unsaved."));
    return true;
}

bool FRenderMasterShadowOptimizationVerificationAssistant::
    RollbackAppliedOptimization()
{
    if (!CanRollbackAppliedOptimization()) return false;
    FString Error;
    if (!RevalidateAppliedTarget(Error))
    {
        Fail(Error);
        return false;
    }
    FRenderMasterPerformanceAction RollbackAction;
    RollbackAction.Target = AppliedAction.Target;
    RollbackAction.Target.Before = AppliedAction.After;
    RollbackAction.Before = AppliedAction.After;
    RollbackAction.After = AppliedAction.Before;
    RollbackAction.ChangeSummary = TEXT("Cast Shadow: false -> true");
    RollbackAction.Rationale = TEXT(
        "Restore the exact pre-optimization shadow state after explicit operator approval.");
    TArray<AStaticMeshActor*> Actors = {TargetActor.Get()};
    TArray<UStaticMeshComponent*> Components = {TargetComponent.Get()};
    TArray<FRenderMasterPerformanceAction> Actions = {RollbackAction};
    if (!RenderMasterApplyPerformanceBatch(
            Actors, Components, Actions, Error))
    {
        Fail(Error.IsEmpty()
            ? TEXT("Unreal rejected the explicit shadow rollback.")
            : Error);
        return false;
    }
    State = ERenderMasterShadowVerificationState::RolledBack;
    AppendLog(TEXT(
        "The exact Cast Shadow setting was restored as a new Unreal transaction. The level was not saved; one Ctrl+Z reapplies the optimized setting."));
    return true;
}

bool FRenderMasterShadowOptimizationVerificationAssistant::
    CanStartVerification() const
{
    return State == ERenderMasterShadowVerificationState::Armed
        && GEditor != nullptr && GEditor->PlayWorld != nullptr
        && LevelProfilingAssistant.IsValid()
        && LevelProfilingAssistant->CanStart();
}

bool FRenderMasterShadowOptimizationVerificationAssistant::
    CanKeepAppliedOptimization() const
{
    return State == ERenderMasterShadowVerificationState::RetainRecommended
        || State == ERenderMasterShadowVerificationState::RollbackRecommended
        || State == ERenderMasterShadowVerificationState::Unresolved;
}

bool FRenderMasterShadowOptimizationVerificationAssistant::
    CanRollbackAppliedOptimization() const
{
    const bool bStateAllows =
        State == ERenderMasterShadowVerificationState::Armed
        || State == ERenderMasterShadowVerificationState::RetainRecommended
        || State == ERenderMasterShadowVerificationState::RollbackRecommended
        || State == ERenderMasterShadowVerificationState::Unresolved
        || State == ERenderMasterShadowVerificationState::Failed
        || State == ERenderMasterShadowVerificationState::Cancelled;
    return bStateAllows && GEditor != nullptr && GEditor->PlayWorld == nullptr
        && TargetActor.IsValid() && TargetComponent.IsValid()
        && RenderMasterPerformanceSnapshotsMatch(
            RenderMasterSnapshotPerformance(TargetComponent.Get()),
            AppliedAction.After);
}

bool FRenderMasterShadowOptimizationVerificationAssistant::IsBusy() const
{
    return State == ERenderMasterShadowVerificationState::Profiling
        || State == ERenderMasterShadowVerificationState::Analyzing;
}

void FRenderMasterShadowOptimizationVerificationAssistant::Cancel()
{
    if (ProcessHandle.IsValid())
        FPlatformProcess::TerminateProc(ProcessHandle, true);
    CloseProcessResources();
    if (LevelProfilingAssistant.IsValid()
        && LevelProfilingAssistant->IsBusy())
    {
        LevelProfilingAssistant->Cancel();
    }
    State = ERenderMasterShadowVerificationState::Cancelled;
    AppendLog(TEXT(
        "Post-apply verification was cancelled. The formal change remains unsaved and can be rolled back explicitly."));
}

void FRenderMasterShadowOptimizationVerificationAssistant::Dismiss()
{
    if (IsBusy()) Cancel();
    State = ERenderMasterShadowVerificationState::Dismissed;
}

FText FRenderMasterShadowOptimizationVerificationAssistant::GetStateText() const
{
    switch (State)
    {
        case ERenderMasterShadowVerificationState::Armed:
            return FText::FromString(TEXT("Awaiting the same PIE/SIE workload"));
        case ERenderMasterShadowVerificationState::Profiling:
            return FText::FromString(TEXT("Running fresh whole-level profiling"));
        case ERenderMasterShadowVerificationState::Analyzing:
            return FText::FromString(TEXT("Applying fixed retain/rollback guards"));
        case ERenderMasterShadowVerificationState::RetainRecommended:
            return FText::FromString(TEXT("Retain recommended"));
        case ERenderMasterShadowVerificationState::RollbackRecommended:
            return FText::FromString(TEXT("Rollback recommended"));
        case ERenderMasterShadowVerificationState::Unresolved:
            return FText::FromString(TEXT("Verification unresolved"));
        case ERenderMasterShadowVerificationState::Retained:
            return FText::FromString(TEXT("Applied optimization retained"));
        case ERenderMasterShadowVerificationState::RolledBack:
            return FText::FromString(TEXT("Applied optimization rolled back"));
        case ERenderMasterShadowVerificationState::Failed:
            return FText::FromString(TEXT("Verification failed safely"));
        case ERenderMasterShadowVerificationState::Cancelled:
            return FText::FromString(TEXT("Verification cancelled"));
        default:
            return FText::FromString(TEXT("Ready"));
    }
}

FText FRenderMasterShadowOptimizationVerificationAssistant::GetSummaryText() const
{
    if (State == ERenderMasterShadowVerificationState::Armed)
        return FText::FromString(
            ProcessLog + TEXT(
                "\n\nRestart the representative PIE/SIE workload with the same camera, viewport, resolution, and content state, then run post-apply verification."));
    if (State == ERenderMasterShadowVerificationState::Profiling)
        return FText::FromString(
            ProcessLog + TEXT("\n\n")
            + LevelProfilingAssistant->GetSummaryText().ToString());
    if (State == ERenderMasterShadowVerificationState::Analyzing)
        return FText::FromString(ProcessLog);
    if (State == ERenderMasterShadowVerificationState::Failed)
        return FText::FromString(ErrorText);
    if (State == ERenderMasterShadowVerificationState::Cancelled)
        return FText::FromString(TEXT(
            "Verification was cancelled. The unsaved applied change remains exact and can be rolled back after PIE/SIE stops."));
    if (State == ERenderMasterShadowVerificationState::Retained)
        return FText::FromString(ProcessLog);
    if (State == ERenderMasterShadowVerificationState::RolledBack)
        return FText::FromString(ProcessLog);
    if (State == ERenderMasterShadowVerificationState::RetainRecommended
        || State == ERenderMasterShadowVerificationState::RollbackRecommended
        || State == ERenderMasterShadowVerificationState::Unresolved)
    {
        const FString GpuDelta = Report.Metrics.GpuP95DeltaMs.IsSet()
            ? FString::Printf(
                TEXT("%.3f ms"), Report.Metrics.GpuP95DeltaMs.GetValue())
            : TEXT("unavailable");
        const FString ScopeDelta =
            Report.Metrics.TargetScopeDeltaMsPerSecond.IsSet()
            ? FString::Printf(
                TEXT("%.3f ms/s"),
                Report.Metrics.TargetScopeDeltaMsPerSecond.GetValue())
            : TEXT("unavailable in one Top-64 trace");
        return FText::FromString(FString::Printf(
            TEXT("%s\n\nWhole-level Before -> After\nFrame p95: %.3f -> %.3f ms  (delta %+.3f ms)\nGPU p95 delta: %s\nFrame-budget miss delta: %+.2f percentage points\nQualified scope: %s / %s\nScope delta: %s\n\nDecision evidence\n%s\n\nThe level is still unsaved. Keeping requires no scene action; rollback requires explicit approval and exact target revalidation."),
            *Report.Summary,
            Report.Metrics.BaselineFrameP95Ms,
            Report.Metrics.CandidateFrameP95Ms,
            Report.Metrics.FrameP95DeltaMs,
            *GpuDelta,
            Report.Metrics.FrameBudgetMissDeltaFraction * 100.0,
            *Report.Metrics.TargetScopeQueueName,
            *Report.Metrics.TargetScopeName,
            *ScopeDelta,
            *FString::Join(Report.DecisionReasons, TEXT("\n"))));
    }
    return FText::FromString(TEXT(
        "Apply a qualified Cast Shadow optimization first. This card will then freeze Before evidence and guide the fresh whole-level verification."));
}

FLinearColor FRenderMasterShadowOptimizationVerificationAssistant::GetStateColor() const
{
    switch (State)
    {
        case ERenderMasterShadowVerificationState::RetainRecommended:
        case ERenderMasterShadowVerificationState::Retained:
            return FLinearColor(0.12f, 0.55f, 0.28f, 1.0f);
        case ERenderMasterShadowVerificationState::RollbackRecommended:
        case ERenderMasterShadowVerificationState::Failed:
            return FLinearColor(0.70f, 0.14f, 0.12f, 1.0f);
        case ERenderMasterShadowVerificationState::Unresolved:
        case ERenderMasterShadowVerificationState::Cancelled:
            return FLinearColor(0.68f, 0.45f, 0.08f, 1.0f);
        case ERenderMasterShadowVerificationState::RolledBack:
            return FLinearColor(0.24f, 0.42f, 0.58f, 1.0f);
        default:
            return FLinearColor(0.12f, 0.38f, 0.62f, 1.0f);
    }
}

void FRenderMasterShadowOptimizationVerificationAssistant::CloseProcessResources()
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

void FRenderMasterShadowOptimizationVerificationAssistant::AppendLog(
    const FString& Line)
{
    if (Line.IsEmpty()) return;
    if (!ProcessLog.IsEmpty()) ProcessLog += TEXT("\n");
    ProcessLog += Line;
}

void FRenderMasterShadowOptimizationVerificationAssistant::Fail(
    const FString& Error)
{
    if (ProcessHandle.IsValid())
        FPlatformProcess::TerminateProc(ProcessHandle, true);
    CloseProcessResources();
    if (LevelProfilingAssistant.IsValid()
        && LevelProfilingAssistant->IsBusy())
    {
        LevelProfilingAssistant->Cancel();
    }
    ErrorText = Error.IsEmpty()
        ? TEXT("Post-apply shadow verification failed safely.")
        : Error;
    State = ERenderMasterShadowVerificationState::Failed;
    AppendLog(ErrorText);
}
