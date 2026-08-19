#include "RenderMasterInsightsGpuAssistant.h"

#include "Dom/JsonObject.h"
#include "DynamicRHI.h"
#include "Editor.h"
#include "Components/PrimitiveComponent.h"
#include "Engine/GameViewportClient.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "GenericPlatform/GenericPlatformMisc.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformFileManager.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "ProfilingDebugging/TraceAuxiliary.h"
#include "RenderMasterWorkflowController.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "TraceServices/AnalysisService.h"
#include "TraceServices/ITraceServicesModule.h"
#include "TraceServices/Model/AnalysisSession.h"
#include "TraceServices/Model/TimingProfiler.h"
#include "UnrealClient.h"

namespace
{
constexpr int32 MaximumCapturedScopes = 64;
constexpr double MinimumAnalyzedTraceDurationSeconds = 0.001;
const TCHAR* InsightsTraceChannels = TEXT("cpu,gpu,frame,bookmark");

FString QuoteInsightsArgument(const FString& Value)
{
    return FString::Printf(TEXT("\"%s\""), *Value);
}

bool SerializeInsightsJson(
    const TSharedRef<FJsonObject>& Object,
    FString& OutText)
{
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&OutText);
    return FJsonSerializer::Serialize(Object, Writer);
}

bool ReadInsightsObject(
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

FString ReadInsightsStringArray(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field,
    TArray<FString>* OutStrings = nullptr)
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
            if (OutStrings != nullptr)
            {
                OutStrings->AddUnique(Item);
            }
        }
    }
    return FString::Join(Strings, TEXT(", "));
}

bool IsLowerHexInsightsSha256(const FString& Value)
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

bool HashInsightsFile(
    const FString& Filename,
    FString& OutSha256,
    int64& OutSizeBytes,
    FString& OutError)
{
    TArray<uint8> Bytes;
    if (!FFileHelper::LoadFileToArray(Bytes, *Filename) || Bytes.IsEmpty())
    {
        OutError = FString::Printf(
            TEXT("The evidence file is empty or unreadable: %s"),
            *Filename);
        return false;
    }
    FSHA256Signature Signature;
    if (!FPlatformMisc::GetSHA256Signature(
            Bytes.GetData(), static_cast<uint32>(Bytes.Num()), Signature))
    {
        OutError = TEXT("The platform could not compute the evidence-file SHA-256.");
        return false;
    }
    OutSizeBytes = Bytes.Num();
    OutSha256 = Signature.ToString();
    return true;
}

struct FInsightsGpuQueueSource
{
    uint32 EngineQueueId = 0;
    uint32 TimelineIndex = ~0u;
    uint8 GpuIndex = 0;
    uint8 QueueIndex = 0;
    uint8 QueueType = 0;
    FString DisplayName;
};
}

bool RenderMasterAggregateInsightsGpuScopes(
    const TArray<FRenderMasterInsightsScopeSample>& Samples,
    int32 MaximumScopes,
    TArray<FRenderMasterInsightsScopeAggregate>& OutScopes,
    FString& OutError)
{
    OutScopes.Reset();
    OutError.Reset();
    if (Samples.IsEmpty())
    {
        OutError = TEXT("No GPU timing scope samples were captured.");
        return false;
    }
    if (MaximumScopes < 1 || MaximumScopes > 1024)
    {
        OutError = TEXT("The maximum GPU scope count is outside the safe range.");
        return false;
    }

    TMap<FString, int32> AggregateIndices;
    for (const FRenderMasterInsightsScopeSample& Sample : Samples)
    {
        const FString QueueId = Sample.QueueId.TrimStartAndEnd();
        const FString QueueName = Sample.QueueName.TrimStartAndEnd();
        const FString ScopeName = Sample.ScopeName.TrimStartAndEnd();
        if (QueueId.IsEmpty() || QueueName.IsEmpty() || ScopeName.IsEmpty()
            || !FMath::IsFinite(Sample.InclusiveMs)
            || Sample.InclusiveMs < 0.0)
        {
            OutError = TEXT("GPU scope samples contain an invalid identity or duration.");
            OutScopes.Reset();
            return false;
        }
        const FString Key = QueueId + TEXT("\x1f") + ScopeName;
        int32* ExistingIndex = AggregateIndices.Find(Key);
        if (ExistingIndex == nullptr)
        {
            FRenderMasterInsightsScopeAggregate& Aggregate = OutScopes.AddDefaulted_GetRef();
            Aggregate.QueueId = QueueId;
            Aggregate.QueueName = QueueName.Left(240);
            Aggregate.Name = ScopeName.Left(240);
            Aggregate.InstanceCount = 1;
            Aggregate.TotalInclusiveMs = Sample.InclusiveMs;
            Aggregate.MeanInclusiveMs = Sample.InclusiveMs;
            Aggregate.MaxInclusiveMs = Sample.InclusiveMs;
            Aggregate.MinDepth = Sample.Depth;
            Aggregate.MaxDepth = Sample.Depth;
            AggregateIndices.Add(Key, OutScopes.Num() - 1);
        }
        else
        {
            FRenderMasterInsightsScopeAggregate& Aggregate = OutScopes[*ExistingIndex];
            ++Aggregate.InstanceCount;
            Aggregate.TotalInclusiveMs += Sample.InclusiveMs;
            Aggregate.MaxInclusiveMs = FMath::Max(
                Aggregate.MaxInclusiveMs, Sample.InclusiveMs);
            Aggregate.MinDepth = FMath::Min(Aggregate.MinDepth, Sample.Depth);
            Aggregate.MaxDepth = FMath::Max(Aggregate.MaxDepth, Sample.Depth);
        }
    }
    for (FRenderMasterInsightsScopeAggregate& Aggregate : OutScopes)
    {
        Aggregate.MeanInclusiveMs = Aggregate.TotalInclusiveMs
            / static_cast<double>(Aggregate.InstanceCount);
    }
    OutScopes.Sort([](
        const FRenderMasterInsightsScopeAggregate& Left,
        const FRenderMasterInsightsScopeAggregate& Right)
    {
        if (!FMath::IsNearlyEqual(
                Left.TotalInclusiveMs, Right.TotalInclusiveMs, 0.000001))
        {
            return Left.TotalInclusiveMs > Right.TotalInclusiveMs;
        }
        if (Left.QueueId != Right.QueueId) return Left.QueueId < Right.QueueId;
        return Left.Name < Right.Name;
    });
    if (OutScopes.Num() > MaximumScopes)
    {
        OutScopes.SetNum(MaximumScopes, EAllowShrinking::Yes);
    }
    for (int32 Index = 0; Index < OutScopes.Num(); ++Index)
    {
        OutScopes[Index].ScopeId = FString::Printf(TEXT("scope_%03d"), Index);
    }
    return true;
}

bool RenderMasterParseInsightsGpuReportFile(
    const FString& Filename,
    FRenderMasterInsightsGpuReport& OutReport,
    FString& OutError)
{
    OutReport = FRenderMasterInsightsGpuReport();
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *Filename))
    {
        OutError = FString::Printf(
            TEXT("Could not read GPU scope report: %s"), *Filename);
        return false;
    }
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        OutError = TEXT("GPU scope report is not valid JSON.");
        return false;
    }
    TSharedPtr<FJsonObject> CaptureObject;
    if (!Root->TryGetStringField(TEXT("report_id"), OutReport.ReportId)
        || !Root->TryGetStringField(TEXT("status"), OutReport.Status)
        || !Root->TryGetStringField(TEXT("request"), OutReport.Request)
        || !Root->TryGetStringField(
            TEXT("capture_sha256"), OutReport.CaptureSha256)
        || !Root->TryGetStringField(
            TEXT("capture_file_sha256"), OutReport.CaptureFileSha256)
        || !Root->TryGetStringField(TEXT("summary"), OutReport.Summary)
        || !Root->TryGetBoolField(
            TEXT("modifies_editor_scene"), OutReport.bModifiesEditorScene)
        || !ReadInsightsObject(Root, TEXT("capture"), CaptureObject)
        || !CaptureObject->TryGetStringField(
            TEXT("capture_id"), OutReport.CaptureId))
    {
        OutError = TEXT("GPU scope report is missing required evidence fields.");
        return false;
    }
    Root->TryGetStringField(TEXT("primary_scope_id"), OutReport.PrimaryScopeId);
    if (OutReport.ReportId.IsEmpty() || OutReport.Request.IsEmpty()
        || OutReport.CaptureId.IsEmpty() || OutReport.Summary.IsEmpty()
        || !IsLowerHexInsightsSha256(OutReport.CaptureSha256)
        || !IsLowerHexInsightsSha256(OutReport.CaptureFileSha256)
        || OutReport.bModifiesEditorScene
        || (OutReport.Status != TEXT("review_complete")
            && OutReport.Status != TEXT("unresolved")))
    {
        OutError = TEXT("GPU scope report has an unsafe status or identity.");
        return false;
    }

    const TArray<TSharedPtr<FJsonValue>>* FindingValues = nullptr;
    if (!Root->TryGetArrayField(TEXT("findings"), FindingValues)
        || FindingValues == nullptr || FindingValues->Num() > 16)
    {
        OutError = TEXT("GPU scope report findings are missing or invalid.");
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
        const FString Evidence = ReadInsightsStringArray(
            Finding,
            TEXT("evidence_scope_ids"),
            &OutReport.CitedScopeIds);
        if (!Finding.IsValid()
            || !Finding->TryGetStringField(TEXT("severity"), Severity)
            || !Finding->TryGetStringField(TEXT("category"), Category)
            || !Finding->TryGetStringField(TEXT("observation"), Observation)
            || !Finding->TryGetStringField(
                TEXT("recommendation"), Recommendation)
            || Evidence.IsEmpty() || Observation.IsEmpty()
            || Recommendation.IsEmpty())
        {
            OutError = TEXT("GPU scope report contains an invalid finding.");
            return false;
        }
        OutReport.Findings.Add(FString::Printf(
            TEXT("[%s / %s] %s\nEvidence scopes: %s\nRecommendation: %s"),
            *Severity,
            *Category,
            *Observation,
            *Evidence,
            *Recommendation));
    }
    OutReport.MissingCapabilities = ReadInsightsStringArray(
        Root, TEXT("missing_capabilities"));
    if (OutReport.Status == TEXT("review_complete")
        && !OutReport.MissingCapabilities.IsEmpty())
    {
        OutError = TEXT("A complete GPU scope review cannot contain capability gaps.");
        return false;
    }
    if (OutReport.Status == TEXT("unresolved")
        && (!OutReport.PrimaryScopeId.IsEmpty()
            || !OutReport.Findings.IsEmpty()
            || OutReport.MissingCapabilities.IsEmpty()))
    {
        OutError = TEXT("An unresolved GPU scope review must contain only capability gaps.");
        return false;
    }
    return true;
}

bool RenderMasterCompareActorGpuCaptures(
    const FRenderMasterInsightsGpuCapture& Baseline,
    const FRenderMasterInsightsGpuCapture& Variant,
    TArray<FRenderMasterActorGpuScopeDelta>& OutDeltas,
    TArray<FString>& OutUnmatchedBaselineScopeIds,
    TArray<FString>& OutUnmatchedVariantScopeIds,
    FString& OutError)
{
    OutDeltas.Reset();
    OutUnmatchedBaselineScopeIds.Reset();
    OutUnmatchedVariantScopeIds.Reset();
    OutError.Reset();
    if (!FMath::IsFinite(Baseline.CapturedDurationSeconds)
        || !FMath::IsFinite(Variant.CapturedDurationSeconds)
        || Baseline.CapturedDurationSeconds <= 0.0
        || Variant.CapturedDurationSeconds <= 0.0
        || Baseline.Scopes.IsEmpty() || Variant.Scopes.IsEmpty())
    {
        OutError = TEXT("Actor GPU comparison requires two non-empty timed captures.");
        return false;
    }
    if (Baseline.ProjectName != Variant.ProjectName
        || Baseline.WorldPath != Variant.WorldPath
        || Baseline.CaptureMode != Variant.CaptureMode
        || Baseline.ViewportSize != Variant.ViewportSize
        || Baseline.GpuName != Variant.GpuName
        || Baseline.Channels != Variant.Channels)
    {
        OutError = TEXT("Actor GPU baseline and variant environments do not match.");
        return false;
    }

    TMap<FString, FString> BaselineQueueNames;
    for (const FRenderMasterInsightsGpuQueue& Queue : Baseline.Queues)
        BaselineQueueNames.Add(Queue.QueueId, Queue.DisplayName);
    TMap<FString, FString> VariantQueueNames;
    for (const FRenderMasterInsightsGpuQueue& Queue : Variant.Queues)
        VariantQueueNames.Add(Queue.QueueId, Queue.DisplayName);

    auto ScopeKey = [](const FRenderMasterInsightsScopeAggregate& Scope)
    {
        return Scope.QueueId + TEXT("\x1f") + Scope.Name;
    };
    TMap<FString, const FRenderMasterInsightsScopeAggregate*> VariantByKey;
    for (const FRenderMasterInsightsScopeAggregate& Scope : Variant.Scopes)
        VariantByKey.Add(ScopeKey(Scope), &Scope);
    TSet<FString> MatchedVariantIds;
    for (const FRenderMasterInsightsScopeAggregate& BaselineScope : Baseline.Scopes)
    {
        if (!FMath::IsFinite(BaselineScope.TotalInclusiveMs)
            || BaselineScope.TotalInclusiveMs <= 0.0)
        {
            OutUnmatchedBaselineScopeIds.Add(BaselineScope.ScopeId);
            continue;
        }
        const FRenderMasterInsightsScopeAggregate* const* VariantScopeValue =
            VariantByKey.Find(ScopeKey(BaselineScope));
        if (VariantScopeValue == nullptr || *VariantScopeValue == nullptr)
        {
            OutUnmatchedBaselineScopeIds.Add(BaselineScope.ScopeId);
            continue;
        }
        const FRenderMasterInsightsScopeAggregate& VariantScope = **VariantScopeValue;
        const FString* BaselineQueueName = BaselineQueueNames.Find(BaselineScope.QueueId);
        const FString* VariantQueueName = VariantQueueNames.Find(VariantScope.QueueId);
        if (BaselineQueueName == nullptr || VariantQueueName == nullptr
            || *BaselineQueueName != *VariantQueueName)
        {
            OutUnmatchedBaselineScopeIds.Add(BaselineScope.ScopeId);
            continue;
        }
        MatchedVariantIds.Add(VariantScope.ScopeId);
        FRenderMasterActorGpuScopeDelta& Delta = OutDeltas.AddDefaulted_GetRef();
        Delta.BaselineScopeId = BaselineScope.ScopeId;
        Delta.VariantScopeId = VariantScope.ScopeId;
        Delta.QueueName = *BaselineQueueName;
        Delta.ScopeName = BaselineScope.Name;
        Delta.BaselineTotalMsPerSecond =
            BaselineScope.TotalInclusiveMs / Baseline.CapturedDurationSeconds;
        Delta.VariantTotalMsPerSecond =
            VariantScope.TotalInclusiveMs / Variant.CapturedDurationSeconds;
        Delta.BaselineInstancesPerSecond =
            static_cast<double>(BaselineScope.InstanceCount)
            / Baseline.CapturedDurationSeconds;
        Delta.VariantInstancesPerSecond =
            static_cast<double>(VariantScope.InstanceCount)
            / Variant.CapturedDurationSeconds;
        Delta.BaselineMinusVariantMsPerSecond =
            Delta.BaselineTotalMsPerSecond - Delta.VariantTotalMsPerSecond;
        Delta.RelativeReductionPercent =
            Delta.BaselineMinusVariantMsPerSecond
            / Delta.BaselineTotalMsPerSecond * 100.0;
        constexpr double DirectionEpsilon = 0.001;
        Delta.DirectionWhenHidden =
            Delta.BaselineMinusVariantMsPerSecond > DirectionEpsilon
            ? TEXT("decreased")
            : Delta.BaselineMinusVariantMsPerSecond < -DirectionEpsilon
            ? TEXT("increased")
            : TEXT("unchanged");
    }
    for (const FRenderMasterInsightsScopeAggregate& VariantScope : Variant.Scopes)
    {
        if (!MatchedVariantIds.Contains(VariantScope.ScopeId))
            OutUnmatchedVariantScopeIds.Add(VariantScope.ScopeId);
    }
    if (OutDeltas.IsEmpty())
    {
        OutError = TEXT(
            "The baseline and Actor-hidden Top-64 sets have no queue-local scopes in common.");
        return false;
    }
    OutDeltas.Sort([](
        const FRenderMasterActorGpuScopeDelta& Left,
        const FRenderMasterActorGpuScopeDelta& Right)
    {
        const double LeftMagnitude = FMath::Abs(
            Left.BaselineMinusVariantMsPerSecond);
        const double RightMagnitude = FMath::Abs(
            Right.BaselineMinusVariantMsPerSecond);
        if (!FMath::IsNearlyEqual(LeftMagnitude, RightMagnitude, 0.000001))
            return LeftMagnitude > RightMagnitude;
        if (Left.QueueName != Right.QueueName)
            return Left.QueueName < Right.QueueName;
        return Left.ScopeName < Right.ScopeName;
    });
    for (int32 Index = 0; Index < OutDeltas.Num(); ++Index)
        OutDeltas[Index].DeltaId = FString::Printf(TEXT("delta_%03d"), Index);
    return true;
}

bool RenderMasterParseActorGpuImpactReportFile(
    const FString& Filename,
    FRenderMasterActorGpuImpactReport& OutReport,
    FString& OutError)
{
    OutReport = FRenderMasterActorGpuImpactReport();
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *Filename))
    {
        OutError = FString::Printf(
            TEXT("Could not read Actor GPU impact report: %s"), *Filename);
        return false;
    }
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        OutError = TEXT("Actor GPU impact report is not valid JSON.");
        return false;
    }
    TSharedPtr<FJsonObject> ExperimentObject;
    if (!Root->TryGetStringField(TEXT("report_id"), OutReport.ReportId)
        || !Root->TryGetStringField(TEXT("status"), OutReport.Status)
        || !Root->TryGetStringField(TEXT("request"), OutReport.Request)
        || !Root->TryGetStringField(
            TEXT("experiment_sha256"), OutReport.ExperimentSha256)
        || !Root->TryGetStringField(
            TEXT("experiment_file_sha256"), OutReport.ExperimentFileSha256)
        || !Root->TryGetStringField(TEXT("summary"), OutReport.Summary)
        || !Root->TryGetBoolField(
            TEXT("modifies_editor_scene"), OutReport.bModifiesEditorScene)
        || !ReadInsightsObject(Root, TEXT("experiment"), ExperimentObject)
        || !ExperimentObject->TryGetStringField(
            TEXT("experiment_id"), OutReport.ExperimentId))
    {
        OutError = TEXT("Actor GPU impact report is missing required evidence fields.");
        return false;
    }
    Root->TryGetStringField(TEXT("primary_delta_id"), OutReport.PrimaryDeltaId);
    if (OutReport.ReportId.IsEmpty() || OutReport.Request.IsEmpty()
        || OutReport.ExperimentId.IsEmpty() || OutReport.Summary.IsEmpty()
        || !IsLowerHexInsightsSha256(OutReport.ExperimentSha256)
        || !IsLowerHexInsightsSha256(OutReport.ExperimentFileSha256)
        || OutReport.bModifiesEditorScene
        || (OutReport.Status != TEXT("review_complete")
            && OutReport.Status != TEXT("unresolved")))
    {
        OutError = TEXT("Actor GPU impact report has an unsafe status or identity.");
        return false;
    }
    const TArray<TSharedPtr<FJsonValue>>* FindingValues = nullptr;
    if (!Root->TryGetArrayField(TEXT("findings"), FindingValues)
        || FindingValues == nullptr || FindingValues->Num() > 16)
    {
        OutError = TEXT("Actor GPU impact report findings are missing or invalid.");
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
        const FString Evidence = ReadInsightsStringArray(
            Finding,
            TEXT("evidence_delta_ids"),
            &OutReport.CitedDeltaIds);
        if (!Finding.IsValid()
            || !Finding->TryGetStringField(TEXT("severity"), Severity)
            || !Finding->TryGetStringField(TEXT("category"), Category)
            || !Finding->TryGetStringField(TEXT("observation"), Observation)
            || !Finding->TryGetStringField(
                TEXT("recommendation"), Recommendation)
            || Evidence.IsEmpty() || Observation.IsEmpty()
            || Recommendation.IsEmpty())
        {
            OutError = TEXT("Actor GPU impact report contains an invalid finding.");
            return false;
        }
        OutReport.Findings.Add(FString::Printf(
            TEXT("[%s / %s] %s\nEvidence deltas: %s\nRecommendation: %s"),
            *Severity,
            *Category,
            *Observation,
            *Evidence,
            *Recommendation));
    }
    OutReport.MissingCapabilities = ReadInsightsStringArray(
        Root, TEXT("missing_capabilities"));
    if (OutReport.Status == TEXT("review_complete")
        && OutReport.MissingCapabilities.IsEmpty() == false)
    {
        OutError = TEXT("A complete Actor GPU review cannot contain capability gaps.");
        return false;
    }
    if (OutReport.Status == TEXT("unresolved")
        && (!OutReport.PrimaryDeltaId.IsEmpty()
            || !OutReport.Findings.IsEmpty()
            || OutReport.MissingCapabilities.IsEmpty()))
    {
        OutError = TEXT("An unresolved Actor GPU review must contain only capability gaps.");
        return false;
    }
    return true;
}

FRenderMasterInsightsGpuAssistant::FRenderMasterInsightsGpuAssistant(
    TSharedPtr<FRenderMasterWorkflowController> InWorkflowController)
    : WorkflowController(MoveTemp(InWorkflowController))
{
}

FRenderMasterInsightsGpuAssistant::~FRenderMasterInsightsGpuAssistant()
{
    Shutdown();
}

void FRenderMasterInsightsGpuAssistant::Initialize()
{
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateSP(
            AsShared(), &FRenderMasterInsightsGpuAssistant::Tick));
}

void FRenderMasterInsightsGpuAssistant::Shutdown()
{
    if (TickHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
        TickHandle.Reset();
    }
    Cancel();
    CloseProcessResources();
    AnalysisSession.Reset();
    AnalysisService.Reset();
}

bool FRenderMasterInsightsGpuAssistant::StartReview(const FString& Prompt)
{
    if (!CanStart()) return false;
    const FString CleanPrompt = Prompt.TrimStartAndEnd();
    if (CleanPrompt.IsEmpty())
    {
        Fail(TEXT("Enter a GPU scope question before starting the trace."));
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
    if (FTraceAuxiliary::IsConnected())
    {
        Fail(FString::Printf(
            TEXT("Another Unreal trace is already active at %s. Stop it before starting an Assistant-owned GPU trace; the Assistant will not interrupt external trace sessions."),
            *FTraceAuxiliary::GetTraceDestinationString()));
        return false;
    }
    UWorld* PlayWorld = GEditor != nullptr ? GEditor->PlayWorld : nullptr;
    if (PlayWorld == nullptr)
    {
        Fail(TEXT(
            "Start Play In Editor or Simulate In Editor, establish a representative workload, then capture again."));
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

    Capture = FRenderMasterInsightsGpuCapture();
    Report = FRenderMasterInsightsGpuReport();
    ActorImpactReport = FRenderMasterActorGpuImpactReport();
    bActorImpactExperiment = false;
    bCapturingActorVariant = false;
    AnalysisSession.Reset();
    AnalysisService.Reset();
    ErrorText.Reset();
    ProcessLog.Reset();
    ReviewRequest = CleanPrompt;
    Capture.CaptureId = FString::Printf(
        TEXT("insights_%s"),
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
        : GRHIAdapterName.Left(240);
    Capture.Channels = {TEXT("cpu"), TEXT("gpu"), TEXT("frame"), TEXT("bookmark")};
    CaptureWorld = PlayWorld;

    RequestDirectory = FPaths::Combine(
        Root, TEXT("assistant-insights-gpu"), Capture.CaptureId);
    IFileManager::Get().MakeDirectory(*RequestDirectory, true);
    PromptPath = FPaths::Combine(RequestDirectory, TEXT("request.txt"));
    TracePath = FPaths::Combine(RequestDirectory, TEXT("gpu_scope_capture.utrace"));
    CapturePath = FPaths::Combine(
        RequestDirectory, TEXT("insights_gpu_capture.json"));
    CaptureFileSha256.Reset();
    ReportOutputPath = FPaths::Combine(
        RequestDirectory, TEXT("insights_gpu_report.json"));
    Capture.TraceFileName = FPaths::GetCleanFilename(TracePath);
    if (!FFileHelper::SaveStringToFile(
            ReviewRequest,
            *PromptPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        Fail(TEXT("Could not write the GPU scope review request."));
        return false;
    }

    FTraceAuxiliary::FOptions Options;
    Options.bTruncateFile = true;
    Options.bExcludeTail = true;
    if (!FTraceAuxiliary::Start(
            FTraceAuxiliary::EConnectionType::File,
            *TracePath,
            InsightsTraceChannels,
            &Options))
    {
        Fail(TEXT("Unreal could not start the Assistant-owned GPU trace."));
        return false;
    }
    bOwnsTrace = true;
    CaptureStartedSeconds = FPlatformTime::Seconds();
    State = ERenderMasterInsightsGpuState::Capturing;
    AppendLog(FString::Printf(
        TEXT("Capturing %.1f seconds of CPU, GPU, frame, and bookmark trace channels at %dx%d."),
        Capture.RequestedDurationSeconds,
        Capture.ViewportSize.X,
        Capture.ViewportSize.Y));
    return true;
}

bool FRenderMasterInsightsGpuAssistant::StartActorImpactReview(
    const FString& Prompt,
    AActor* SelectedActor)
{
    if (!CanStart()) return false;
    const FString CleanPrompt = Prompt.TrimStartAndEnd();
    if (CleanPrompt.IsEmpty())
    {
        Fail(TEXT("Enter a selected-Actor GPU impact question before starting the experiment."));
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
    if (FTraceAuxiliary::IsConnected())
    {
        Fail(FString::Printf(
            TEXT("Another Unreal trace is already active at %s. Stop it before starting the selected-Actor experiment; the Assistant will not interrupt external trace sessions."),
            *FTraceAuxiliary::GetTraceDestinationString()));
        return false;
    }
    UWorld* PlayWorld = GEditor != nullptr ? GEditor->PlayWorld : nullptr;
    if (PlayWorld == nullptr)
    {
        Fail(TEXT(
            "Start PIE or SIE, keep one Editor Actor selected, establish a representative workload, then try again."));
        return false;
    }
    if (!IsValid(SelectedActor))
    {
        Fail(TEXT("Select exactly one Actor that already existed before PIE/SIE started."));
        return false;
    }
    AActor* EditorActor = SelectedActor;
    AActor* RuntimeActor = nullptr;
    if (SelectedActor->GetOutermost()->HasAnyPackageFlags(PKG_PlayInEditor))
    {
        RuntimeActor = SelectedActor;
        EditorActor = EditorUtilities::GetEditorWorldCounterpartActor(SelectedActor);
    }
    else
    {
        RuntimeActor = EditorUtilities::GetSimWorldCounterpartActor(SelectedActor);
    }
    if (!IsValid(EditorActor) || !IsValid(RuntimeActor)
        || RuntimeActor->GetWorld() != PlayWorld)
    {
        Fail(TEXT(
            "The selected Actor has no stable PIE/SIE counterpart. Select an Actor placed in the level before play started."));
        return false;
    }
    TInlineComponentArray<UPrimitiveComponent*> PrimitiveComponents;
    RuntimeActor->GetComponents(PrimitiveComponents);
    PrimitiveComponents.RemoveAll([](const UPrimitiveComponent* Component)
    {
        return !IsValid(Component) || !Component->IsRegistered();
    });
    if (PrimitiveComponents.IsEmpty())
    {
        Fail(TEXT("The selected Actor has no registered runtime Primitive Component to measure."));
        return false;
    }
    if (RuntimeActor->IsHidden())
    {
        Fail(TEXT("The selected runtime Actor is already hidden, so a visible baseline cannot be captured."));
        return false;
    }
    FViewport* Viewport = nullptr;
    if (UGameViewportClient* GameViewport = PlayWorld->GetGameViewport())
        Viewport = GameViewport->Viewport;
    if (Viewport == nullptr && GEditor != nullptr)
        Viewport = GEditor->GetActiveViewport();
    const FIntPoint ViewportSize = Viewport != nullptr
        ? Viewport->GetSizeXY()
        : FIntPoint::ZeroValue;
    if (ViewportSize.X < 1 || ViewportSize.Y < 1)
    {
        Fail(TEXT("The active PIE/SIE viewport has no measurable render size."));
        return false;
    }

    Capture = FRenderMasterInsightsGpuCapture();
    BaselineCapture = FRenderMasterInsightsGpuCapture();
    Report = FRenderMasterInsightsGpuReport();
    ActorImpactReport = FRenderMasterActorGpuImpactReport();
    ActorImpactDeltas.Reset();
    UnmatchedBaselineScopeIds.Reset();
    UnmatchedVariantScopeIds.Reset();
    AnalysisSession.Reset();
    AnalysisService.Reset();
    ErrorText.Reset();
    ProcessLog.Reset();
    ReviewRequest = CleanPrompt;
    bActorImpactExperiment = true;
    bCapturingActorVariant = false;
    bImpactActorHidden = false;
    bOriginalImpactActorHidden = RuntimeActor->IsHidden();
    ImpactEditorActor = EditorActor;
    ImpactRuntimeActor = RuntimeActor;
    ImpactEditorActorPath = EditorActor->GetPathName().Left(500);
    ImpactRuntimeActorPath = RuntimeActor->GetPathName().Left(500);
    ImpactActorLabel = EditorActor->GetActorLabel().Left(240);
    ImpactActorClass = EditorActor->GetClass()->GetPathName().Left(500);
    ImpactPrimitiveComponentCount = PrimitiveComponents.Num();
    CaptureWorld = PlayWorld;
    ActorImpactExperimentId = FString::Printf(
        TEXT("actor_gpu_%s"),
        *FGuid::NewGuid().ToString(EGuidFormats::Digits).ToLower());
    RequestDirectory = FPaths::Combine(
        Root, TEXT("assistant-actor-gpu-impact"), ActorImpactExperimentId);
    IFileManager::Get().MakeDirectory(*RequestDirectory, true);
    PromptPath = FPaths::Combine(RequestDirectory, TEXT("request.txt"));
    BaselineTracePath = FPaths::Combine(RequestDirectory, TEXT("baseline.utrace"));
    BaselineCapturePath = FPaths::Combine(
        RequestDirectory, TEXT("baseline_capture.json"));
    TracePath = BaselineTracePath;
    CapturePath = BaselineCapturePath;
    ActorImpactExperimentPath = FPaths::Combine(
        RequestDirectory, TEXT("actor_gpu_impact_experiment.json"));
    ReportOutputPath = FPaths::Combine(
        RequestDirectory, TEXT("actor_gpu_impact_report.json"));
    BaselineCaptureFileSha256.Reset();
    ActorImpactExperimentFileSha256.Reset();
    CaptureFileSha256.Reset();

    Capture.CaptureId = ActorImpactExperimentId + TEXT("_baseline");
    Capture.CapturedAtUtc = FDateTime::UtcNow().ToIso8601();
    Capture.ProjectName = FApp::GetProjectName();
    Capture.WorldPath = PlayWorld->GetPackage()->GetName();
    Capture.CaptureMode = GEditor->IsSimulatingInEditor()
        ? TEXT("simulate")
        : TEXT("pie");
    Capture.ViewportSize = ViewportSize;
    Capture.GpuName = GRHIAdapterName.IsEmpty()
        ? TEXT("Unknown RHI adapter")
        : GRHIAdapterName.Left(240);
    Capture.TraceFileName = FPaths::GetCleanFilename(TracePath);
    Capture.Channels = {TEXT("cpu"), TEXT("gpu"), TEXT("frame"), TEXT("bookmark")};
    if (!FFileHelper::SaveStringToFile(
            ReviewRequest,
            *PromptPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        Fail(TEXT("Could not write the selected-Actor GPU experiment request."));
        return false;
    }
    FTraceAuxiliary::FOptions Options;
    Options.bTruncateFile = true;
    Options.bExcludeTail = true;
    if (!FTraceAuxiliary::Start(
            FTraceAuxiliary::EConnectionType::File,
            *TracePath,
            InsightsTraceChannels,
            &Options))
    {
        Fail(TEXT("Unreal could not start the Actor-visible baseline trace."));
        return false;
    }
    bOwnsTrace = true;
    CaptureStartedSeconds = FPlatformTime::Seconds();
    State = ERenderMasterInsightsGpuState::Capturing;
    AppendLog(FString::Printf(
        TEXT("Capturing %.1f-second Actor-visible baseline for %s at %dx%d."),
        Capture.RequestedDurationSeconds,
        *ImpactActorLabel,
        Capture.ViewportSize.X,
        Capture.ViewportSize.Y));
    return true;
}

bool FRenderMasterInsightsGpuAssistant::Tick(float DeltaTime)
{
    (void)DeltaTime;
    if (State == ERenderMasterInsightsGpuState::Capturing)
    {
        TickCapture();
        return true;
    }
    if (State == ERenderMasterInsightsGpuState::WarmingVariant)
    {
        TickVariantWarmup();
        return true;
    }
    if (State == ERenderMasterInsightsGpuState::Analyzing
        && AnalysisSession.IsValid()
        && AnalysisSession->IsAnalysisComplete())
    {
        FString Error;
        if (!FinalizeTraceAnalysis(Error))
        {
            Fail(Error.IsEmpty()
                ? TEXT("Unreal TraceServices could not aggregate GPU scopes.")
                : Error);
        }
        return true;
    }
    if (State == ERenderMasterInsightsGpuState::Reviewing
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

bool FRenderMasterInsightsGpuAssistant::TickCapture()
{
    UWorld* World = CaptureWorld.Get();
    if (World == nullptr || GEditor == nullptr || GEditor->PlayWorld != World)
    {
        FString StopError;
        StopOwnedTrace(StopError);
        Fail(TEXT("PIE/SIE ended before the GPU scope trace completed."));
        return false;
    }
    const double Elapsed = FPlatformTime::Seconds() - CaptureStartedSeconds;
    if (Elapsed < Capture.RequestedDurationSeconds) return true;

    Capture.CapturedDurationSeconds = Elapsed;
    FString Error;
    if (!StopOwnedTrace(Error))
    {
        Fail(Error.IsEmpty() ? TEXT("Could not stop the GPU trace.") : Error);
        return false;
    }
    if (bActorImpactExperiment && bCapturingActorVariant
        && !RestoreImpactActor(Error))
    {
        Fail(Error);
        return false;
    }
    if (!BeginTraceAnalysis(Error))
    {
        Fail(Error.IsEmpty()
            ? TEXT("Could not finish and analyze the GPU trace.")
            : Error);
        return false;
    }
    return true;
}

bool FRenderMasterInsightsGpuAssistant::TickVariantWarmup()
{
    UWorld* World = CaptureWorld.Get();
    AActor* RuntimeActor = ImpactRuntimeActor.Get();
    if (World == nullptr || GEditor == nullptr || GEditor->PlayWorld != World
        || !IsValid(RuntimeActor) || !bImpactActorHidden)
    {
        FString RestoreError;
        RestoreImpactActor(RestoreError);
        Fail(TEXT("PIE/SIE or the selected Actor changed during the hidden-variant warmup."));
        return false;
    }
    constexpr double VariantWarmupSeconds = 1.0;
    if (FPlatformTime::Seconds() - VariantWarmupStartedSeconds
        < VariantWarmupSeconds)
    {
        return true;
    }
    FString Error;
    if (!BeginActorVariantCapture(Error))
    {
        Fail(Error);
        return false;
    }
    return true;
}

bool FRenderMasterInsightsGpuAssistant::BeginActorVariantCapture(
    FString& OutError)
{
    UWorld* PlayWorld = CaptureWorld.Get();
    AActor* RuntimeActor = ImpactRuntimeActor.Get();
    if (!bActorImpactExperiment || !IsValid(PlayWorld)
        || GEditor == nullptr || GEditor->PlayWorld != PlayWorld
        || !IsValid(RuntimeActor) || !bImpactActorHidden
        || !RuntimeActor->IsHidden())
    {
        OutError = TEXT("The selected Actor hidden state is not stable for variant capture.");
        return false;
    }
    if (FTraceAuxiliary::IsConnected())
    {
        OutError = TEXT("Another Unreal trace started during the Actor experiment.");
        return false;
    }
    FViewport* Viewport = nullptr;
    if (UGameViewportClient* GameViewport = PlayWorld->GetGameViewport())
        Viewport = GameViewport->Viewport;
    if (Viewport == nullptr && GEditor != nullptr)
        Viewport = GEditor->GetActiveViewport();
    const FIntPoint ViewportSize = Viewport != nullptr
        ? Viewport->GetSizeXY()
        : FIntPoint::ZeroValue;
    if (ViewportSize != BaselineCapture.ViewportSize)
    {
        OutError = TEXT(
            "The PIE/SIE viewport size changed between baseline and Actor-hidden capture.");
        return false;
    }
    Capture = FRenderMasterInsightsGpuCapture();
    Capture.CaptureId = ActorImpactExperimentId + TEXT("_variant");
    Capture.CapturedAtUtc = FDateTime::UtcNow().ToIso8601();
    Capture.ProjectName = BaselineCapture.ProjectName;
    Capture.WorldPath = BaselineCapture.WorldPath;
    Capture.CaptureMode = BaselineCapture.CaptureMode;
    Capture.ViewportSize = BaselineCapture.ViewportSize;
    Capture.GpuName = BaselineCapture.GpuName;
    Capture.Channels = BaselineCapture.Channels;
    TracePath = FPaths::Combine(RequestDirectory, TEXT("actor_hidden.utrace"));
    CapturePath = FPaths::Combine(
        RequestDirectory, TEXT("actor_hidden_capture.json"));
    Capture.TraceFileName = FPaths::GetCleanFilename(TracePath);
    CaptureFileSha256.Reset();
    FTraceAuxiliary::FOptions Options;
    Options.bTruncateFile = true;
    Options.bExcludeTail = true;
    if (!FTraceAuxiliary::Start(
            FTraceAuxiliary::EConnectionType::File,
            *TracePath,
            InsightsTraceChannels,
            &Options))
    {
        OutError = TEXT("Unreal could not start the Actor-hidden variant trace.");
        return false;
    }
    bOwnsTrace = true;
    bCapturingActorVariant = true;
    CaptureStartedSeconds = FPlatformTime::Seconds();
    State = ERenderMasterInsightsGpuState::Capturing;
    AppendLog(FString::Printf(
        TEXT("Capturing %.1f-second Actor-hidden variant for %s."),
        Capture.RequestedDurationSeconds,
        *ImpactActorLabel));
    return true;
}

bool FRenderMasterInsightsGpuAssistant::BeginTraceAnalysis(FString& OutError)
{
    if (!FPaths::FileExists(TracePath)
        || IFileManager::Get().FileSize(*TracePath) <= 0)
    {
        OutError = TEXT("Unreal stopped tracing but did not produce a readable .utrace file.");
        return false;
    }
    ITraceServicesModule* TraceServicesModule =
        FModuleManager::LoadModulePtr<ITraceServicesModule>(TEXT("TraceServices"));
    if (TraceServicesModule == nullptr)
    {
        OutError = TEXT("The Unreal TraceServices module is unavailable in this Editor build.");
        return false;
    }
    AnalysisService = TraceServicesModule->CreateAnalysisService();
    if (!AnalysisService.IsValid())
    {
        OutError = TEXT("Could not create an Unreal trace analysis service.");
        return false;
    }
    AnalysisSession = AnalysisService->StartAnalysis(*TracePath);
    if (!AnalysisSession.IsValid())
    {
        OutError = TEXT("Unreal TraceServices could not open the captured .utrace file.");
        return false;
    }
    State = ERenderMasterInsightsGpuState::Analyzing;
    AppendLog(TEXT("Trace saved. Unreal TraceServices is parsing GPU queue timelines."));
    return true;
}

bool FRenderMasterInsightsGpuAssistant::FinalizeTraceAnalysis(FString& OutError)
{
    if (!AnalysisSession.IsValid())
    {
        OutError = TEXT("The Unreal trace analysis session is unavailable.");
        return false;
    }
    Capture.AnalyzedTraceDurationSeconds = AnalysisSession->GetDurationSeconds();
    if (!FMath::IsFinite(Capture.AnalyzedTraceDurationSeconds)
        || Capture.AnalyzedTraceDurationSeconds
            < MinimumAnalyzedTraceDurationSeconds)
    {
        OutError = TEXT("The analyzed trace has no measurable duration.");
        return false;
    }

    TArray<FInsightsGpuQueueSource> QueueSources;
    TArray<FRenderMasterInsightsScopeSample> ScopeSamples;
    {
        TraceServices::FAnalysisSessionReadScope ReadScope(*AnalysisSession);
        const TraceServices::ITimingProfilerProvider* Provider =
            TraceServices::ReadTimingProfilerProvider(*AnalysisSession);
        if (Provider == nullptr || !Provider->HasGpuTiming())
        {
            OutError = TEXT(
                "The trace contains no GPU timing provider. Verify that the current RHI supports GPU timestamp tracing and that rendered frames occurred during the capture.");
            return false;
        }
        Provider->EnumerateGpuQueues(
            [&QueueSources](const TraceServices::FGpuQueueInfo& Queue)
            {
                if (Queue.TimelineIndex == ~0u) return;
                FInsightsGpuQueueSource& Source = QueueSources.AddDefaulted_GetRef();
                Source.EngineQueueId = Queue.Id;
                Source.TimelineIndex = Queue.TimelineIndex;
                Source.GpuIndex = Queue.GPU;
                Source.QueueIndex = Queue.Index;
                Source.QueueType = Queue.Type;
                Source.DisplayName = Queue.GetDisplayName().Left(240);
            });
        QueueSources.Sort([](
            const FInsightsGpuQueueSource& Left,
            const FInsightsGpuQueueSource& Right)
        {
            if (Left.GpuIndex != Right.GpuIndex)
                return Left.GpuIndex < Right.GpuIndex;
            if (Left.QueueType != Right.QueueType)
                return Left.QueueType < Right.QueueType;
            if (Left.QueueIndex != Right.QueueIndex)
                return Left.QueueIndex < Right.QueueIndex;
            return Left.EngineQueueId < Right.EngineQueueId;
        });

        Provider->ReadTimers(
            [this, Provider, &QueueSources, &ScopeSamples](
                const TraceServices::ITimingProfilerTimerReader& TimerReader)
            {
                for (int32 SourceIndex = 0;
                    SourceIndex < QueueSources.Num();
                    ++SourceIndex)
                {
                    const FInsightsGpuQueueSource& Source = QueueSources[SourceIndex];
                    FRenderMasterInsightsGpuQueue Queue;
                    Queue.QueueId = FString::Printf(TEXT("queue_%03d"), SourceIndex);
                    Queue.DisplayName = Source.DisplayName;
                    Queue.EngineQueueId = Source.EngineQueueId;
                    Queue.GpuIndex = Source.GpuIndex;
                    Queue.QueueIndex = Source.QueueIndex;
                    Queue.QueueType = Source.QueueType;
                    Provider->ReadTimeline(
                        Source.TimelineIndex,
                        [this, &TimerReader, &Queue, &ScopeSamples](
                            const TraceServices::ITimingProfilerProvider::Timeline& Timeline)
                        {
                            Timeline.EnumerateEvents(
                                0.0,
                                Capture.AnalyzedTraceDurationSeconds + 1.0,
                                [&TimerReader, &Queue, &ScopeSamples](
                                    double StartTime,
                                    double EndTime,
                                    uint32 Depth,
                                    const TraceServices::FTimingProfilerEvent& Event)
                                {
                                    ++Queue.EventCount;
                                    const TraceServices::FTimingProfilerTimer* Timer =
                                        TimerReader.GetTimer(Event.TimerIndex);
                                    if (Timer == nullptr || Timer->Name == nullptr
                                        || Timer->Name[0] == TEXT('\0'))
                                    {
                                        return TraceServices::EEventEnumerate::Continue;
                                    }
                                    const double InclusiveMs =
                                        FMath::Max(0.0, EndTime - StartTime) * 1000.0;
                                    FRenderMasterInsightsScopeSample& Sample =
                                        ScopeSamples.AddDefaulted_GetRef();
                                    Sample.QueueId = Queue.QueueId;
                                    Sample.QueueName = Queue.DisplayName;
                                    Sample.ScopeName = FString(Timer->Name).Left(240);
                                    Sample.InclusiveMs = InclusiveMs;
                                    Sample.Depth = Depth;
                                    return TraceServices::EEventEnumerate::Continue;
                                });
                        });
                    if (Queue.EventCount > 0)
                    {
                        Capture.TotalGpuEventCount += Queue.EventCount;
                        Capture.Queues.Add(MoveTemp(Queue));
                    }
                }
            });
    }
    if (Capture.Queues.IsEmpty() || Capture.TotalGpuEventCount < 1)
    {
        OutError = TEXT(
            "Unreal parsed the GPU provider but found no timed GPU queue events in this capture.");
        return false;
    }
    if (!RenderMasterAggregateInsightsGpuScopes(
            ScopeSamples, MaximumCapturedScopes, Capture.Scopes, OutError))
    {
        return false;
    }
    if (!HashInsightsFile(
            TracePath,
            Capture.TraceSha256,
            Capture.TraceFileSizeBytes,
            OutError))
    {
        return false;
    }
    if (!WriteCapture(CapturePath, OutError)) return false;
    int64 CaptureFileSizeBytes = 0;
    if (!HashInsightsFile(
            CapturePath,
            CaptureFileSha256,
            CaptureFileSizeBytes,
            OutError))
    {
        return false;
    }
    AnalysisSession.Reset();
    AnalysisService.Reset();
    if (bActorImpactExperiment && !bCapturingActorVariant)
    {
        BaselineCapture = Capture;
        BaselineTracePath = TracePath;
        BaselineCapturePath = CapturePath;
        BaselineCaptureFileSha256 = CaptureFileSha256;
        AActor* RuntimeActor = ImpactRuntimeActor.Get();
        if (!IsValid(RuntimeActor) || RuntimeActor->GetWorld() != CaptureWorld.Get()
            || RuntimeActor->IsHidden())
        {
            OutError = TEXT(
                "The selected runtime Actor changed before the hidden variant could start.");
            return false;
        }
        RuntimeActor->SetActorHiddenInGame(true);
        if (!RuntimeActor->IsHidden())
        {
            OutError = TEXT(
                "Unreal did not apply the temporary runtime-only hidden state.");
            return false;
        }
        bImpactActorHidden = true;
        VariantWarmupStartedSeconds = FPlatformTime::Seconds();
        State = ERenderMasterInsightsGpuState::WarmingVariant;
        AppendLog(FString::Printf(
            TEXT("Baseline parsed. %s is hidden only in PIE/SIE for a 1.0-second warmup."),
            *ImpactActorLabel));
        return true;
    }
    if (bActorImpactExperiment && bCapturingActorVariant)
    {
        if (!RenderMasterCompareActorGpuCaptures(
                BaselineCapture,
                Capture,
                ActorImpactDeltas,
                UnmatchedBaselineScopeIds,
                UnmatchedVariantScopeIds,
                OutError))
        {
            return false;
        }
        if (!WriteActorImpactExperiment(OutError)) return false;
        int64 ExperimentFileSizeBytes = 0;
        if (!HashInsightsFile(
                ActorImpactExperimentPath,
                ActorImpactExperimentFileSha256,
                ExperimentFileSizeBytes,
                OutError))
        {
            return false;
        }
        return StartActorImpactReviewProcess(OutError);
    }
    return StartReviewProcess(OutError);
}

bool FRenderMasterInsightsGpuAssistant::WriteCapture(
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
    Root->SetNumberField(
        TEXT("requested_duration_seconds"), Capture.RequestedDurationSeconds);
    Root->SetNumberField(
        TEXT("captured_duration_seconds"), Capture.CapturedDurationSeconds);
    Root->SetNumberField(
        TEXT("analyzed_trace_duration_seconds"),
        Capture.AnalyzedTraceDurationSeconds);
    Root->SetNumberField(TEXT("viewport_width_px"), Capture.ViewportSize.X);
    Root->SetNumberField(TEXT("viewport_height_px"), Capture.ViewportSize.Y);
    Root->SetStringField(TEXT("gpu_name"), Capture.GpuName);
    Root->SetStringField(TEXT("trace_file_name"), Capture.TraceFileName);
    Root->SetNumberField(
        TEXT("trace_file_size_bytes"), Capture.TraceFileSizeBytes);
    Root->SetStringField(TEXT("trace_sha256"), Capture.TraceSha256);
    TArray<TSharedPtr<FJsonValue>> Channels;
    for (const FString& Channel : Capture.Channels)
        Channels.Add(MakeShared<FJsonValueString>(Channel));
    Root->SetArrayField(TEXT("channels"), Channels);
    Root->SetNumberField(TEXT("gpu_queue_count"), Capture.Queues.Num());
    Root->SetNumberField(
        TEXT("total_gpu_event_count"), Capture.TotalGpuEventCount);

    TArray<TSharedPtr<FJsonValue>> Queues;
    for (const FRenderMasterInsightsGpuQueue& Queue : Capture.Queues)
    {
        TSharedRef<FJsonObject> Value = MakeShared<FJsonObject>();
        Value->SetStringField(TEXT("queue_id"), Queue.QueueId);
        Value->SetStringField(TEXT("display_name"), Queue.DisplayName);
        Value->SetNumberField(TEXT("gpu_index"), Queue.GpuIndex);
        Value->SetNumberField(TEXT("queue_index"), Queue.QueueIndex);
        Value->SetNumberField(TEXT("queue_type"), Queue.QueueType);
        Value->SetNumberField(TEXT("event_count"), Queue.EventCount);
        Queues.Add(MakeShared<FJsonValueObject>(Value));
    }
    Root->SetArrayField(TEXT("queues"), Queues);

    TArray<TSharedPtr<FJsonValue>> Scopes;
    for (const FRenderMasterInsightsScopeAggregate& Scope : Capture.Scopes)
    {
        TSharedRef<FJsonObject> Value = MakeShared<FJsonObject>();
        Value->SetStringField(TEXT("scope_id"), Scope.ScopeId);
        Value->SetStringField(TEXT("queue_id"), Scope.QueueId);
        Value->SetStringField(TEXT("name"), Scope.Name);
        Value->SetNumberField(TEXT("instance_count"), Scope.InstanceCount);
        Value->SetNumberField(
            TEXT("total_inclusive_ms"), Scope.TotalInclusiveMs);
        Value->SetNumberField(
            TEXT("mean_inclusive_ms"), Scope.MeanInclusiveMs);
        Value->SetNumberField(
            TEXT("max_inclusive_ms"), Scope.MaxInclusiveMs);
        Value->SetNumberField(TEXT("min_depth"), Scope.MinDepth);
        Value->SetNumberField(TEXT("max_depth"), Scope.MaxDepth);
        Scopes.Add(MakeShared<FJsonValueObject>(Value));
    }
    Root->SetArrayField(TEXT("scopes"), Scopes);
    TArray<TSharedPtr<FJsonValue>> Notes;
    Notes.Add(MakeShared<FJsonValueString>(TEXT(
        "GPU scope times are inclusive and nested totals can overlap; do not sum them as frame time.")));
    Notes.Add(MakeShared<FJsonValueString>(TEXT(
        "This trace was captured in Unreal Editor PIE/SIE and does not prove packaged-build performance.")));
    Notes.Add(MakeShared<FJsonValueString>(TEXT(
        "GPU scopes identify render work, not the Actor, asset, material, shader, or draw call that caused it.")));
    Notes.Add(MakeShared<FJsonValueString>(TEXT(
        "The preserved .utrace is the authoritative artifact for manual Unreal Insights inspection.")));
    Root->SetArrayField(TEXT("measurement_notes"), Notes);

    FString JsonText;
    if (!SerializeInsightsJson(Root, JsonText)
        || !FFileHelper::SaveStringToFile(
            JsonText,
            *Filename,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        OutError = FString::Printf(
            TEXT("Could not write GPU scope capture: %s"), *Filename);
        return false;
    }
    return true;
}

bool FRenderMasterInsightsGpuAssistant::WriteActorImpactExperiment(
    FString& OutError)
{
    AActor* RuntimeActor = ImpactRuntimeActor.Get();
    if (!bActorImpactExperiment || ActorImpactDeltas.IsEmpty()
        || !IsValid(RuntimeActor) || bImpactActorHidden
        || RuntimeActor->IsHidden() != bOriginalImpactActorHidden)
    {
        OutError = TEXT(
            "The Actor GPU experiment cannot be written until runtime state is restored.");
        return false;
    }
    auto LoadJsonObject = [&OutError](
        const FString& Filename,
        TSharedPtr<FJsonObject>& OutObject)
    {
        FString Text;
        if (!FFileHelper::LoadFileToString(Text, *Filename))
        {
            OutError = FString::Printf(TEXT("Could not read capture JSON: %s"), *Filename);
            return false;
        }
        const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
        if (!FJsonSerializer::Deserialize(Reader, OutObject) || !OutObject.IsValid())
        {
            OutError = FString::Printf(TEXT("Capture JSON is invalid: %s"), *Filename);
            return false;
        }
        return true;
    };
    TSharedPtr<FJsonObject> BaselineObject;
    TSharedPtr<FJsonObject> VariantObject;
    if (!LoadJsonObject(BaselineCapturePath, BaselineObject)
        || !LoadJsonObject(CapturePath, VariantObject))
    {
        return false;
    }
    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Root->SetStringField(TEXT("experiment_id"), ActorImpactExperimentId);
    Root->SetStringField(TEXT("created_at_utc"), FDateTime::UtcNow().ToIso8601());
    Root->SetStringField(TEXT("method"), TEXT("temporary_runtime_actor_hidden"));
    TSharedRef<FJsonObject> Target = MakeShared<FJsonObject>();
    Target->SetStringField(TEXT("editor_actor_path"), ImpactEditorActorPath);
    Target->SetStringField(TEXT("runtime_actor_path"), ImpactRuntimeActorPath);
    Target->SetStringField(TEXT("actor_label"), ImpactActorLabel);
    Target->SetStringField(TEXT("actor_class"), ImpactActorClass);
    Target->SetNumberField(
        TEXT("primitive_component_count"), ImpactPrimitiveComponentCount);
    Target->SetBoolField(TEXT("baseline_hidden_in_game"), false);
    Target->SetBoolField(TEXT("restored_hidden_in_game"), false);
    Root->SetObjectField(TEXT("target"), Target);
    Root->SetNumberField(TEXT("variant_warmup_seconds"), 1.0);
    Root->SetObjectField(TEXT("baseline"), BaselineObject.ToSharedRef());
    Root->SetObjectField(TEXT("variant"), VariantObject.ToSharedRef());
    Root->SetNumberField(TEXT("comparison_count"), ActorImpactDeltas.Num());
    TArray<TSharedPtr<FJsonValue>> DeltaValues;
    for (const FRenderMasterActorGpuScopeDelta& Delta : ActorImpactDeltas)
    {
        TSharedRef<FJsonObject> Value = MakeShared<FJsonObject>();
        Value->SetStringField(TEXT("delta_id"), Delta.DeltaId);
        Value->SetStringField(
            TEXT("baseline_scope_id"), Delta.BaselineScopeId);
        Value->SetStringField(TEXT("variant_scope_id"), Delta.VariantScopeId);
        Value->SetStringField(TEXT("queue_name"), Delta.QueueName);
        Value->SetStringField(TEXT("scope_name"), Delta.ScopeName);
        Value->SetNumberField(
            TEXT("baseline_total_ms_per_second"),
            Delta.BaselineTotalMsPerSecond);
        Value->SetNumberField(
            TEXT("variant_total_ms_per_second"),
            Delta.VariantTotalMsPerSecond);
        Value->SetNumberField(
            TEXT("baseline_instances_per_second"),
            Delta.BaselineInstancesPerSecond);
        Value->SetNumberField(
            TEXT("variant_instances_per_second"),
            Delta.VariantInstancesPerSecond);
        Value->SetNumberField(
            TEXT("baseline_minus_variant_ms_per_second"),
            Delta.BaselineMinusVariantMsPerSecond);
        Value->SetNumberField(
            TEXT("relative_reduction_percent"),
            Delta.RelativeReductionPercent);
        Value->SetStringField(
            TEXT("direction_when_hidden"), Delta.DirectionWhenHidden);
        DeltaValues.Add(MakeShared<FJsonValueObject>(Value));
    }
    Root->SetArrayField(TEXT("deltas"), DeltaValues);
    auto ToStringValues = [](const TArray<FString>& Strings)
    {
        TArray<TSharedPtr<FJsonValue>> Values;
        for (const FString& String : Strings)
            Values.Add(MakeShared<FJsonValueString>(String));
        return Values;
    };
    Root->SetArrayField(
        TEXT("unmatched_baseline_scope_ids"),
        ToStringValues(UnmatchedBaselineScopeIds));
    Root->SetArrayField(
        TEXT("unmatched_variant_scope_ids"),
        ToStringValues(UnmatchedVariantScopeIds));
    Root->SetNumberField(TEXT("trial_count"), 1);
    Root->SetStringField(TEXT("repeatability"), TEXT("single_trial"));
    Root->SetBoolField(TEXT("runtime_state_restored"), true);
    TArray<TSharedPtr<FJsonValue>> Notes;
    Notes.Add(MakeShared<FJsonValueString>(TEXT(
        "Rates are normalized by each capture duration before comparison.")));
    Notes.Add(MakeShared<FJsonValueString>(TEXT(
        "Only queue-local scopes present in both Top-64 capture sets are compared.")));
    Notes.Add(MakeShared<FJsonValueString>(TEXT(
        "Positive delta means the scope decreased while the selected Actor was hidden.")));
    Notes.Add(MakeShared<FJsonValueString>(TEXT(
        "This sequential single trial is impact-candidate evidence, not direct draw-call causation.")));
    Root->SetArrayField(TEXT("measurement_notes"), Notes);
    FString JsonText;
    if (!SerializeInsightsJson(Root, JsonText)
        || !FFileHelper::SaveStringToFile(
            JsonText,
            *ActorImpactExperimentPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        OutError = FString::Printf(
            TEXT("Could not write Actor GPU impact experiment: %s"),
            *ActorImpactExperimentPath);
        return false;
    }
    return true;
}

bool FRenderMasterInsightsGpuAssistant::StartReviewProcess(FString& OutError)
{
    const FString Python = WorkflowController->GetPythonExecutable();
    const FString ReportId = Capture.CaptureId + TEXT("_review");
    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-insights-gpu-review --prompt-file %s --capture %s --report-id %s --output %s"),
        *QuoteInsightsArgument(PromptPath),
        *QuoteInsightsArgument(CapturePath),
        *QuoteInsightsArgument(ReportId),
        *QuoteInsightsArgument(ReportOutputPath));
    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        OutError = TEXT("Could not create GPU scope review process pipes.");
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
    State = ERenderMasterInsightsGpuState::Reviewing;
    AppendLog(FString::Printf(
        TEXT("Parsed %d GPU events into %d ranked scopes; local review process %u started."),
        Capture.TotalGpuEventCount,
        Capture.Scopes.Num(),
        ProcessId));
    return true;
}

bool FRenderMasterInsightsGpuAssistant::StartActorImpactReviewProcess(
    FString& OutError)
{
    const FString Python = WorkflowController->GetPythonExecutable();
    const FString ReportId = ActorImpactExperimentId + TEXT("_review");
    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-actor-gpu-impact-review --prompt-file %s --experiment %s --report-id %s --output %s"),
        *QuoteInsightsArgument(PromptPath),
        *QuoteInsightsArgument(ActorImpactExperimentPath),
        *QuoteInsightsArgument(ReportId),
        *QuoteInsightsArgument(ReportOutputPath));
    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        OutError = TEXT("Could not create Actor GPU review process pipes.");
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
    State = ERenderMasterInsightsGpuState::Reviewing;
    AppendLog(FString::Printf(
        TEXT("Compared %d matched GPU scopes for %s; local review process %u started."),
        ActorImpactDeltas.Num(),
        *ImpactActorLabel,
        ProcessId));
    return true;
}

void FRenderMasterInsightsGpuAssistant::CompleteProcess()
{
    if (bActorImpactExperiment)
    {
        CompleteActorImpactProcess();
        return;
    }
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
                TEXT("GPU scope review exited with code %d."), ReturnCode)
            : ProcessLog);
        return;
    }
    FString Error;
    if (!RenderMasterParseInsightsGpuReportFile(
            ReportOutputPath, Report, Error))
    {
        Fail(Error);
        return;
    }
    if (Report.CaptureId != Capture.CaptureId)
    {
        Fail(TEXT("GPU scope report does not match the host capture."));
        return;
    }
    if (Report.CaptureFileSha256 != CaptureFileSha256)
    {
        Fail(TEXT("GPU scope report does not match the host capture file hash."));
        return;
    }
    if (!Report.PrimaryScopeId.IsEmpty()
        && !Capture.Scopes.ContainsByPredicate(
            [this](const FRenderMasterInsightsScopeAggregate& Scope)
            {
                return Scope.ScopeId == Report.PrimaryScopeId;
            }))
    {
        Fail(TEXT("GPU scope report names a primary scope absent from the capture."));
        return;
    }
    for (const FString& CitedScopeId : Report.CitedScopeIds)
    {
        if (!Capture.Scopes.ContainsByPredicate(
                [&CitedScopeId](const FRenderMasterInsightsScopeAggregate& Scope)
                {
                    return Scope.ScopeId == CitedScopeId;
                }))
        {
            Fail(TEXT("GPU scope report cites evidence absent from the capture."));
            return;
        }
    }
    State = Report.Status == TEXT("review_complete")
        ? ERenderMasterInsightsGpuState::Complete
        : ERenderMasterInsightsGpuState::Unresolved;
}

void FRenderMasterInsightsGpuAssistant::CompleteActorImpactProcess()
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
                TEXT("Actor GPU impact review exited with code %d."), ReturnCode)
            : ProcessLog);
        return;
    }
    FString Error;
    if (!RenderMasterParseActorGpuImpactReportFile(
            ReportOutputPath, ActorImpactReport, Error))
    {
        Fail(Error);
        return;
    }
    if (ActorImpactReport.ExperimentId != ActorImpactExperimentId)
    {
        Fail(TEXT("Actor GPU report does not match the host experiment."));
        return;
    }
    if (ActorImpactReport.ExperimentFileSha256
        != ActorImpactExperimentFileSha256)
    {
        Fail(TEXT("Actor GPU report does not match the host experiment file hash."));
        return;
    }
    auto HasDelta = [this](const FString& DeltaId)
    {
        return ActorImpactDeltas.ContainsByPredicate(
            [&DeltaId](const FRenderMasterActorGpuScopeDelta& Delta)
            {
                return Delta.DeltaId == DeltaId;
            });
    };
    if (!ActorImpactReport.PrimaryDeltaId.IsEmpty()
        && !HasDelta(ActorImpactReport.PrimaryDeltaId))
    {
        Fail(TEXT("Actor GPU report names a primary delta absent from the experiment."));
        return;
    }
    for (const FString& CitedDeltaId : ActorImpactReport.CitedDeltaIds)
    {
        if (!HasDelta(CitedDeltaId))
        {
            Fail(TEXT("Actor GPU report cites evidence absent from the experiment."));
            return;
        }
    }
    State = ActorImpactReport.Status == TEXT("review_complete")
        ? ERenderMasterInsightsGpuState::Complete
        : ERenderMasterInsightsGpuState::Unresolved;
}

bool FRenderMasterInsightsGpuAssistant::StopOwnedTrace(FString& OutError)
{
    if (!bOwnsTrace) return true;
    if (!FTraceAuxiliary::IsConnected())
    {
        bOwnsTrace = false;
        OutError = TEXT("The Assistant-owned trace ended unexpectedly.");
        return false;
    }
    if (!FTraceAuxiliary::Stop())
    {
        OutError = TEXT("Unreal could not stop the Assistant-owned trace cleanly.");
        return false;
    }
    bOwnsTrace = false;
    return true;
}

bool FRenderMasterInsightsGpuAssistant::RestoreImpactActor(FString& OutError)
{
    if (!bImpactActorHidden) return true;
    AActor* RuntimeActor = ImpactRuntimeActor.Get();
    if (!IsValid(RuntimeActor))
    {
        OutError = TEXT(
            "The selected runtime Actor was destroyed before its hidden state could be restored.");
        return false;
    }
    RuntimeActor->SetActorHiddenInGame(bOriginalImpactActorHidden);
    if (RuntimeActor->IsHidden() != bOriginalImpactActorHidden)
    {
        OutError = TEXT("Unreal could not restore the selected runtime Actor hidden state.");
        return false;
    }
    bImpactActorHidden = false;
    AppendLog(FString::Printf(
        TEXT("Restored %s to its original PIE/SIE hidden state."),
        *ImpactActorLabel));
    return true;
}

void FRenderMasterInsightsGpuAssistant::Dismiss()
{
    if (IsBusy()) Cancel();
    CloseProcessResources();
    AnalysisSession.Reset();
    AnalysisService.Reset();
    State = ERenderMasterInsightsGpuState::Dismissed;
    AppendLog(bActorImpactExperiment
        ? TEXT("Actor GPU impact review dismissed. Preserved traces remain available.")
        : TEXT("GPU scope review dismissed. The .utrace artifact was preserved."));
}

void FRenderMasterInsightsGpuAssistant::Cancel()
{
    FString StopError;
    StopOwnedTrace(StopError);
    FString RestoreError;
    RestoreImpactActor(RestoreError);
    if (AnalysisSession.IsValid() && !AnalysisSession->IsAnalysisComplete())
    {
        AnalysisSession->Stop(false);
    }
    if (ProcessHandle.IsValid())
    {
        FPlatformProcess::TerminateProc(ProcessHandle, true);
    }
}

bool FRenderMasterInsightsGpuAssistant::OpenTraceInInsights(
    FString& OutError) const
{
    return OpenTracePathInInsights(TracePath, OutError);
}

bool FRenderMasterInsightsGpuAssistant::OpenBaselineTraceInInsights(
    FString& OutError) const
{
    return OpenTracePathInInsights(BaselineTracePath, OutError);
}

bool FRenderMasterInsightsGpuAssistant::OpenTracePathInInsights(
    const FString& InTracePath,
    FString& OutError) const
{
    if (InTracePath.IsEmpty() || !FPaths::FileExists(InTracePath))
    {
        OutError = TEXT("No preserved Unreal Insights trace is available.");
        return false;
    }
    if (!WorkflowController.IsValid())
    {
        OutError = TEXT("The configured Unreal Engine root is unavailable.");
        return false;
    }
    const FString InsightsExe = FPaths::Combine(
        WorkflowController->GetEngineRoot(),
        TEXT("Engine/Binaries/Win64/UnrealInsights.exe"));
    if (!FPaths::FileExists(InsightsExe))
    {
        OutError = FString::Printf(
            TEXT("UnrealInsights.exe was not found at %s"), *InsightsExe);
        return false;
    }
    const FString Arguments = FString::Printf(
        TEXT("-OpenTraceFile=%s"), *QuoteInsightsArgument(InTracePath));
    FProcHandle Handle = FPlatformProcess::CreateProc(
        *InsightsExe,
        *Arguments,
        true,
        false,
        false,
        nullptr,
        0,
        nullptr,
        nullptr);
    if (!Handle.IsValid())
    {
        OutError = TEXT("Could not launch Unreal Insights for the captured trace.");
        return false;
    }
    FPlatformProcess::CloseProc(Handle);
    return true;
}

bool FRenderMasterInsightsGpuAssistant::CanStart() const
{
    return !IsBusy() && !ProcessHandle.IsValid() && !bOwnsTrace;
}

bool FRenderMasterInsightsGpuAssistant::IsBusy() const
{
    return State == ERenderMasterInsightsGpuState::Capturing
        || State == ERenderMasterInsightsGpuState::WarmingVariant
        || State == ERenderMasterInsightsGpuState::Analyzing
        || State == ERenderMasterInsightsGpuState::Reviewing;
}

bool FRenderMasterInsightsGpuAssistant::CanOpenTrace() const
{
    return (!bActorImpactExperiment || bCapturingActorVariant)
        && !TracePath.IsEmpty()
        && FPaths::FileExists(TracePath);
}

bool FRenderMasterInsightsGpuAssistant::CanOpenBaselineTrace() const
{
    return bActorImpactExperiment
        && !BaselineTracePath.IsEmpty()
        && FPaths::FileExists(BaselineTracePath);
}

FText FRenderMasterInsightsGpuAssistant::GetTitleText() const
{
    return bActorImpactExperiment
        ? NSLOCTEXT(
            "RenderMasterBot",
            "ActorGpuImpactTitle",
            "Selected Actor GPU Impact Experiment")
        : NSLOCTEXT(
            "RenderMasterBot",
            "InsightsGpuTitle",
            "Unreal Insights GPU Scope Review");
}

FText FRenderMasterInsightsGpuAssistant::GetStateText() const
{
    switch (State)
    {
        case ERenderMasterInsightsGpuState::Capturing:
            return bActorImpactExperiment
                ? FText::FromString(bCapturingActorVariant
                    ? TEXT("Capturing Actor-hidden variant")
                    : TEXT("Capturing Actor-visible baseline"))
                : NSLOCTEXT("RenderMasterBot", "InsightsGpuCapturing", "Capturing Unreal trace");
        case ERenderMasterInsightsGpuState::WarmingVariant:
            return NSLOCTEXT(
                "RenderMasterBot",
                "ActorGpuWarmingVariant",
                "Warming Actor-hidden variant");
        case ERenderMasterInsightsGpuState::Analyzing:
            return NSLOCTEXT("RenderMasterBot", "InsightsGpuAnalyzing", "Parsing GPU timelines");
        case ERenderMasterInsightsGpuState::Reviewing:
            return bActorImpactExperiment
                ? NSLOCTEXT("RenderMasterBot", "ActorGpuReviewing", "Reviewing measured Actor impact")
                : NSLOCTEXT("RenderMasterBot", "InsightsGpuReviewing", "Reviewing measured GPU scopes");
        case ERenderMasterInsightsGpuState::Complete:
            return bActorImpactExperiment
                ? NSLOCTEXT("RenderMasterBot", "ActorGpuComplete", "Actor GPU impact review complete")
                : NSLOCTEXT("RenderMasterBot", "InsightsGpuComplete", "Read-only GPU scope review complete");
        case ERenderMasterInsightsGpuState::Unresolved:
            return NSLOCTEXT("RenderMasterBot", "InsightsGpuUnresolved", "More attribution evidence required");
        case ERenderMasterInsightsGpuState::Failed:
            return NSLOCTEXT("RenderMasterBot", "InsightsGpuFailed", "Trace capture or review failed");
        case ERenderMasterInsightsGpuState::Dismissed:
            return NSLOCTEXT("RenderMasterBot", "InsightsGpuDismissed", "Dismissed");
        default:
            return NSLOCTEXT("RenderMasterBot", "InsightsGpuReady", "Ready for PIE/SIE GPU trace");
    }
}

FText FRenderMasterInsightsGpuAssistant::GetSummaryText() const
{
    if (bActorImpactExperiment)
    {
        if (State == ERenderMasterInsightsGpuState::Capturing)
        {
            const double Remaining = FMath::Max(
                0.0,
                Capture.RequestedDurationSeconds
                    - (FPlatformTime::Seconds() - CaptureStartedSeconds));
            return FText::FromString(FString::Printf(
                TEXT("%s: %.1f seconds remain for %s at %dx%d. Keep camera and workload unchanged."),
                bCapturingActorVariant
                    ? TEXT("Actor-hidden capture")
                    : TEXT("Actor-visible baseline"),
                Remaining,
                *ImpactActorLabel,
                Capture.ViewportSize.X,
                Capture.ViewportSize.Y));
        }
        if (State == ERenderMasterInsightsGpuState::WarmingVariant)
        {
            const double Remaining = FMath::Max(
                0.0,
                1.0 - (FPlatformTime::Seconds() - VariantWarmupStartedSeconds));
            return FText::FromString(FString::Printf(
                TEXT("The baseline is preserved. %s is temporarily hidden only in PIE/SIE; %.1f seconds of render warmup remain before the variant trace."),
                *ImpactActorLabel,
                Remaining));
        }
        if (State == ERenderMasterInsightsGpuState::Analyzing)
        {
            return FText::FromString(FString::Printf(
                TEXT("Unreal TraceServices is parsing the %s trace. The selected Actor is %s."),
                bCapturingActorVariant ? TEXT("Actor-hidden") : TEXT("baseline"),
                bImpactActorHidden
                    ? TEXT("temporarily hidden in PIE/SIE")
                    : TEXT("restored to its original runtime state")));
        }
        FString Evidence;
        if (!ActorImpactDeltas.IsEmpty())
        {
            Evidence = FString::Printf(
                TEXT("\n\nA/B evidence for %s: %d matched queue-local scopes | baseline %.2f s | hidden %.2f s\nLargest normalized changes (positive means lower while hidden):"),
                *ImpactActorLabel,
                ActorImpactDeltas.Num(),
                BaselineCapture.CapturedDurationSeconds,
                Capture.CapturedDurationSeconds);
            const int32 VisibleDeltas = FMath::Min(5, ActorImpactDeltas.Num());
            for (int32 Index = 0; Index < VisibleDeltas; ++Index)
            {
                const FRenderMasterActorGpuScopeDelta& Delta = ActorImpactDeltas[Index];
                Evidence += FString::Printf(
                    TEXT("\n%s  %s [%s] — baseline %.3f ms/s | hidden %.3f ms/s | delta %+.3f ms/s (%+.1f%%)"),
                    *Delta.DeltaId,
                    *Delta.ScopeName,
                    *Delta.QueueName,
                    Delta.BaselineTotalMsPerSecond,
                    Delta.VariantTotalMsPerSecond,
                    Delta.BaselineMinusVariantMsPerSecond,
                    Delta.RelativeReductionPercent);
            }
            Evidence += FString::Printf(
                TEXT("\nUnmatched Top-64 scopes: %d baseline | %d hidden"),
                UnmatchedBaselineScopeIds.Num(),
                UnmatchedVariantScopeIds.Num());
        }
        if (State == ERenderMasterInsightsGpuState::Reviewing)
        {
            return FText::FromString(
                TEXT("The Actor is restored. The local model is reviewing only host-recomputed matched-scope deltas.")
                + Evidence);
        }
        if (State == ERenderMasterInsightsGpuState::Complete)
        {
            FString Text = ActorImpactReport.Summary + Evidence;
            if (!ActorImpactReport.PrimaryDeltaId.IsEmpty())
                Text += TEXT("\n\nPrimary measured delta: ")
                    + ActorImpactReport.PrimaryDeltaId;
            if (!ActorImpactReport.Findings.IsEmpty())
            {
                Text += TEXT("\n\nFindings:\n");
                for (int32 Index = 0;
                    Index < ActorImpactReport.Findings.Num();
                    ++Index)
                {
                    Text += FString::Printf(
                        TEXT("%d. %s%s"),
                        Index + 1,
                        *ActorImpactReport.Findings[Index],
                        Index + 1 < ActorImpactReport.Findings.Num()
                            ? TEXT("\n") : TEXT(""));
                }
            }
            Text += TEXT(
                "\n\nThis is a sequential single-trial impact candidate, not direct Actor/draw-call causation. The runtime Actor was restored; no Editor scene change was made.");
            return FText::FromString(Text);
        }
        if (State == ERenderMasterInsightsGpuState::Unresolved)
        {
            return FText::FromString(
                ActorImpactReport.Summary + Evidence
                + TEXT("\n\nRequired next evidence: ")
                + ActorImpactReport.MissingCapabilities);
        }
        if (State == ERenderMasterInsightsGpuState::Failed)
            return FText::FromString(ErrorText + Evidence);
        if (State == ERenderMasterInsightsGpuState::Dismissed)
        {
            return NSLOCTEXT(
                "RenderMasterBot",
                "ActorGpuDismissedSummary",
                "The Actor GPU experiment was dismissed. Preserved traces remain in the workflow folder, and the runtime Actor is restored.");
        }
    }
    if (State == ERenderMasterInsightsGpuState::Capturing)
    {
        const double Remaining = FMath::Max(
            0.0,
            Capture.RequestedDurationSeconds
                - (FPlatformTime::Seconds() - CaptureStartedSeconds));
        return FText::FromString(FString::Printf(
            TEXT("Capturing CPU, GPU, frame, and bookmark channels for %.1f more seconds at %dx%d. Keep the camera and workload representative."),
            Remaining,
            Capture.ViewportSize.X,
            Capture.ViewportSize.Y));
    }
    if (State == ERenderMasterInsightsGpuState::Analyzing)
    {
        return NSLOCTEXT(
            "RenderMasterBot",
            "InsightsGpuAnalyzingSummary",
            "The .utrace file is preserved. Unreal TraceServices is decoding the GPU queues and scope timelines; no scene property is being changed.");
    }

    FString Evidence;
    if (!Capture.Scopes.IsEmpty())
    {
        Evidence = FString::Printf(
            TEXT("\n\nTrace evidence: %.2f s captured at %dx%d | %d GPU queues | %d timed events\nTop accumulated inclusive scopes (nested totals can overlap):"),
            Capture.CapturedDurationSeconds,
            Capture.ViewportSize.X,
            Capture.ViewportSize.Y,
            Capture.Queues.Num(),
            Capture.TotalGpuEventCount);
        const int32 VisibleScopes = FMath::Min(5, Capture.Scopes.Num());
        for (int32 Index = 0; Index < VisibleScopes; ++Index)
        {
            const FRenderMasterInsightsScopeAggregate& Scope = Capture.Scopes[Index];
            Evidence += FString::Printf(
                TEXT("\n%s  %s [%s] — total %.2f ms | mean %.3f ms | max %.3f ms | %d instances"),
                *Scope.ScopeId,
                *Scope.Name,
                *Scope.QueueName,
                Scope.TotalInclusiveMs,
                Scope.MeanInclusiveMs,
                Scope.MaxInclusiveMs,
                Scope.InstanceCount);
        }
    }
    if (State == ERenderMasterInsightsGpuState::Reviewing)
    {
        return FText::FromString(
            TEXT("Unreal parsed the captured GPU timelines. The local model is reviewing only the ranked, host-owned scope evidence.")
            + Evidence);
    }
    if (State == ERenderMasterInsightsGpuState::Complete)
    {
        FString Text = Report.Summary + Evidence;
        if (!Report.PrimaryScopeId.IsEmpty())
        {
            Text += TEXT("\n\nPrimary measured scope: ") + Report.PrimaryScopeId;
        }
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
        Text += TEXT(
            "\n\nInclusive scope totals may overlap. This is PIE/SIE evidence, not per-Actor attribution or packaged-build proof. No Editor scene change was made.");
        return FText::FromString(Text);
    }
    if (State == ERenderMasterInsightsGpuState::Unresolved)
    {
        return FText::FromString(
            Report.Summary + Evidence
            + TEXT("\n\nRequired next evidence: ")
            + Report.MissingCapabilities);
    }
    if (State == ERenderMasterInsightsGpuState::Failed)
        return FText::FromString(ErrorText + Evidence);
    if (State == ERenderMasterInsightsGpuState::Dismissed)
        return NSLOCTEXT(
            "RenderMasterBot",
            "InsightsGpuDismissedSummary",
            "The GPU scope review was dismissed. Its .utrace artifact remains in the workflow folder.");
    return NSLOCTEXT(
        "RenderMasterBot",
        "InsightsGpuReadySummary",
        "Start PIE or SIE with a representative camera and workload, enter a GPU performance question, then capture five seconds of Unreal Insights evidence. The review is read-only.");
}

FLinearColor FRenderMasterInsightsGpuAssistant::GetStateColor() const
{
    switch (State)
    {
        case ERenderMasterInsightsGpuState::Complete:
            return FLinearColor(0.12f, 0.55f, 0.28f, 1.0f);
        case ERenderMasterInsightsGpuState::Capturing:
        case ERenderMasterInsightsGpuState::WarmingVariant:
        case ERenderMasterInsightsGpuState::Analyzing:
        case ERenderMasterInsightsGpuState::Reviewing:
            return FLinearColor(0.16f, 0.50f, 0.78f, 1.0f);
        case ERenderMasterInsightsGpuState::Unresolved:
            return FLinearColor(0.65f, 0.45f, 0.08f, 1.0f);
        case ERenderMasterInsightsGpuState::Failed:
            return FLinearColor(0.70f, 0.14f, 0.12f, 1.0f);
        default:
            return FLinearColor(0.14f, 0.20f, 0.30f, 1.0f);
    }
}

void FRenderMasterInsightsGpuAssistant::CloseProcessResources()
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

void FRenderMasterInsightsGpuAssistant::AppendLog(const FString& Line)
{
    const FString Clean = Line.TrimStartAndEnd();
    if (Clean.IsEmpty()) return;
    if (!ProcessLog.IsEmpty()) ProcessLog += TEXT("\n");
    ProcessLog += Clean;
    constexpr int32 MaxLogChars = 8000;
    if (ProcessLog.Len() > MaxLogChars)
        ProcessLog.RightChopInline(ProcessLog.Len() - MaxLogChars);
}

void FRenderMasterInsightsGpuAssistant::Fail(const FString& Error)
{
    FString StopError;
    if (bOwnsTrace) StopOwnedTrace(StopError);
    FString RestoreError;
    RestoreImpactActor(RestoreError);
    ErrorText = Error.IsEmpty()
        ? TEXT("GPU scope trace review failed.")
        : Error;
    if (!StopError.IsEmpty()) ErrorText += TEXT(" ") + StopError;
    if (!RestoreError.IsEmpty()) ErrorText += TEXT(" ") + RestoreError;
    State = ERenderMasterInsightsGpuState::Failed;
    AppendLog(ErrorText);
}
