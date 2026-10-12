#include "RenderMasterInsightsGpuAssistant.h"

#include "Dom/JsonObject.h"
#include "DynamicRHI.h"
#include "Editor.h"
#include "Components/PrimitiveComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/GameViewportClient.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
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
#include "RenderMasterSha256.h"
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

bool ReadInsightsInteger(
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
    if (!RenderMasterSha256File(Filename, OutSha256, &OutSizeBytes)
        || OutSizeBytes <= 0)
    {
        OutError = FString::Printf(
            TEXT("The evidence file is empty or unreadable: %s"),
            *Filename);
        return false;
    }
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

bool RenderMasterParseActorGpuImpactStudyReportFile(
    const FString& Filename,
    FRenderMasterActorGpuImpactStudyReport& OutReport,
    FString& OutError)
{
    OutReport = FRenderMasterActorGpuImpactStudyReport();
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *Filename))
    {
        OutError = FString::Printf(
            TEXT("Could not read Actor GPU impact study report: %s"), *Filename);
        return false;
    }
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        OutError = TEXT("Actor GPU impact study report is not valid JSON.");
        return false;
    }
    TSharedPtr<FJsonObject> CaptureSetObject;
    if (!Root->TryGetStringField(TEXT("report_id"), OutReport.ReportId)
        || !Root->TryGetStringField(TEXT("status"), OutReport.Status)
        || !Root->TryGetStringField(TEXT("request"), OutReport.Request)
        || !Root->TryGetStringField(
            TEXT("capture_set_sha256"), OutReport.CaptureSetSha256)
        || !Root->TryGetStringField(
            TEXT("capture_set_file_sha256"), OutReport.CaptureSetFileSha256)
        || !Root->TryGetStringField(TEXT("summary"), OutReport.Summary)
        || !Root->TryGetBoolField(
            TEXT("modifies_editor_scene"), OutReport.bModifiesEditorScene)
        || !ReadInsightsObject(Root, TEXT("capture_set"), CaptureSetObject)
        || !CaptureSetObject->TryGetStringField(
            TEXT("study_id"), OutReport.StudyId))
    {
        OutError = TEXT("Actor GPU impact study report is missing evidence fields.");
        return false;
    }
    Root->TryGetStringField(
        TEXT("primary_aggregate_id"), OutReport.PrimaryAggregateId);
    if (OutReport.ReportId.IsEmpty() || OutReport.Request.IsEmpty()
        || OutReport.StudyId.IsEmpty() || OutReport.Summary.IsEmpty()
        || !IsLowerHexInsightsSha256(OutReport.CaptureSetSha256)
        || !IsLowerHexInsightsSha256(OutReport.CaptureSetFileSha256)
        || OutReport.bModifiesEditorScene
        || (OutReport.Status != TEXT("review_complete")
            && OutReport.Status != TEXT("unresolved")))
    {
        OutError = TEXT("Actor GPU impact study report has an unsafe identity.");
        return false;
    }

    const TArray<TSharedPtr<FJsonValue>>* AggregateValues = nullptr;
    if (!Root->TryGetArrayField(TEXT("aggregates"), AggregateValues)
        || AggregateValues == nullptr || AggregateValues->Num() > 64)
    {
        OutError = TEXT("Actor GPU impact study aggregates are missing or invalid.");
        return false;
    }
    TSet<FString> AggregateIds;
    for (const TSharedPtr<FJsonValue>& AggregateValue : *AggregateValues)
    {
        const TSharedPtr<FJsonObject> Aggregate =
            AggregateValue.IsValid() ? AggregateValue->AsObject() : nullptr;
        FRenderMasterActorGpuStudyAggregate Parsed;
        double DecreasedVotes = -1.0;
        double IncreasedVotes = -1.0;
        double UnchangedVotes = -1.0;
        if (!Aggregate.IsValid()
            || !Aggregate->TryGetStringField(
                TEXT("aggregate_id"), Parsed.AggregateId)
            || !Aggregate->TryGetStringField(TEXT("queue_name"), Parsed.QueueName)
            || !Aggregate->TryGetStringField(TEXT("scope_name"), Parsed.ScopeName)
            || !Aggregate->TryGetStringField(
                TEXT("classification"), Parsed.Classification)
            || !Aggregate->TryGetStringField(
                TEXT("dominant_direction_when_hidden"), Parsed.DominantDirection)
            || !Aggregate->TryGetNumberField(
                TEXT("median_visible_total_ms_per_second"),
                Parsed.MedianVisibleMsPerSecond)
            || !Aggregate->TryGetNumberField(
                TEXT("median_hidden_total_ms_per_second"),
                Parsed.MedianHiddenMsPerSecond)
            || !Aggregate->TryGetNumberField(
                TEXT("median_baseline_minus_hidden_ms_per_second"),
                Parsed.MedianDeltaMsPerSecond)
            || !Aggregate->TryGetNumberField(
                TEXT("delta_mad_ms_per_second"), Parsed.DeltaMadMsPerSecond)
            || !Aggregate->TryGetNumberField(
                TEXT("control_noise_abs_ms_per_second"),
                Parsed.ControlNoiseMsPerSecond)
            || !Aggregate->TryGetNumberField(
                TEXT("signal_excess_over_control_noise_ms_per_second"),
                Parsed.SignalExcessMsPerSecond)
            || !Aggregate->TryGetNumberField(
                TEXT("relative_reduction_percent"),
                Parsed.RelativeReductionPercent)
            || !Aggregate->TryGetNumberField(
                TEXT("decreased_vote_count"), DecreasedVotes)
            || !Aggregate->TryGetNumberField(
                TEXT("increased_vote_count"), IncreasedVotes)
            || !Aggregate->TryGetNumberField(
                TEXT("unchanged_vote_count"), UnchangedVotes))
        {
            OutError = TEXT("Actor GPU impact study contains an invalid aggregate.");
            return false;
        }
        const bool bClassificationValid =
            Parsed.Classification == TEXT("stable_candidate")
            || Parsed.Classification == TEXT("noise_limited")
            || Parsed.Classification == TEXT("inconsistent_direction");
        const bool bDirectionValid = Parsed.DominantDirection == TEXT("decreased")
            || Parsed.DominantDirection == TEXT("increased")
            || Parsed.DominantDirection == TEXT("unchanged")
            || Parsed.DominantDirection == TEXT("mixed");
        if (Parsed.AggregateId.IsEmpty() || Parsed.QueueName.IsEmpty()
            || Parsed.ScopeName.IsEmpty() || !bClassificationValid
            || !bDirectionValid || AggregateIds.Contains(Parsed.AggregateId)
            || !FMath::IsFinite(Parsed.MedianVisibleMsPerSecond)
            || !FMath::IsFinite(Parsed.MedianHiddenMsPerSecond)
            || !FMath::IsFinite(Parsed.MedianDeltaMsPerSecond)
            || !FMath::IsFinite(Parsed.DeltaMadMsPerSecond)
            || !FMath::IsFinite(Parsed.ControlNoiseMsPerSecond)
            || !FMath::IsFinite(Parsed.SignalExcessMsPerSecond)
            || !FMath::IsFinite(Parsed.RelativeReductionPercent)
            || DecreasedVotes < 0.0 || IncreasedVotes < 0.0
            || UnchangedVotes < 0.0
            || !FMath::IsNearlyEqual(
                DecreasedVotes + IncreasedVotes + UnchangedVotes, 3.0)
            || !FMath::IsNearlyEqual(DecreasedVotes, FMath::RoundToDouble(DecreasedVotes))
            || !FMath::IsNearlyEqual(IncreasedVotes, FMath::RoundToDouble(IncreasedVotes))
            || !FMath::IsNearlyEqual(UnchangedVotes, FMath::RoundToDouble(UnchangedVotes)))
        {
            OutError = TEXT("Actor GPU impact study aggregate values are unsafe.");
            return false;
        }
        Parsed.DecreasedVotes = static_cast<int32>(DecreasedVotes);
        Parsed.IncreasedVotes = static_cast<int32>(IncreasedVotes);
        Parsed.UnchangedVotes = static_cast<int32>(UnchangedVotes);
        AggregateIds.Add(Parsed.AggregateId);
        OutReport.Aggregates.Add(MoveTemp(Parsed));
    }

    const TArray<TSharedPtr<FJsonValue>>* FindingValues = nullptr;
    if (!Root->TryGetArrayField(TEXT("findings"), FindingValues)
        || FindingValues == nullptr || FindingValues->Num() > 16)
    {
        OutError = TEXT("Actor GPU impact study findings are missing or invalid.");
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
            TEXT("evidence_aggregate_ids"),
            &OutReport.CitedAggregateIds);
        if (!Finding.IsValid()
            || !Finding->TryGetStringField(TEXT("severity"), Severity)
            || !Finding->TryGetStringField(TEXT("category"), Category)
            || !Finding->TryGetStringField(TEXT("observation"), Observation)
            || !Finding->TryGetStringField(
                TEXT("recommendation"), Recommendation)
            || Evidence.IsEmpty() || Observation.IsEmpty()
            || Recommendation.IsEmpty())
        {
            OutError = TEXT("Actor GPU impact study contains an invalid finding.");
            return false;
        }
        OutReport.Findings.Add(FString::Printf(
            TEXT("[%s / %s] %s\nEvidence aggregates: %s\nRecommendation: %s"),
            *Severity,
            *Category,
            *Observation,
            *Evidence,
            *Recommendation));
    }
    OutReport.MissingCapabilities = ReadInsightsStringArray(
        Root, TEXT("missing_capabilities"));
    if (OutReport.Status == TEXT("review_complete")
        && (!OutReport.MissingCapabilities.IsEmpty()
            || OutReport.Aggregates.IsEmpty()))
    {
        OutError = TEXT("A complete Actor GPU impact study requires aggregates only.");
        return false;
    }
    if (OutReport.Status == TEXT("unresolved")
        && (!OutReport.PrimaryAggregateId.IsEmpty()
            || !OutReport.Findings.IsEmpty()
            || OutReport.MissingCapabilities.IsEmpty()))
    {
        OutError = TEXT("An unresolved Actor GPU study must contain only gaps.");
        return false;
    }
    return true;
}

bool RenderMasterParseShadowOptimizationReportFile(
    const FString& Filename,
    FRenderMasterShadowOptimizationReport& OutReport,
    FString& OutError)
{
    OutReport = FRenderMasterShadowOptimizationReport();
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *Filename))
    {
        OutError = FString::Printf(
            TEXT("Could not read shadow optimization report: %s"), *Filename);
        return false;
    }
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        OutError = TEXT("Shadow optimization report is not valid JSON.");
        return false;
    }
    FString SchemaVersion;
    FString AnalysisMethod;
    FString VisualEvidenceRole;
    TSharedPtr<FJsonObject> CaptureSet;
    TSharedPtr<FJsonObject> Target;
    TSharedPtr<FJsonObject> WorkloadCapture;
    FString ProjectName;
    FString LevelPath;
    bool bCaptureSetRestored = false;
    bool bCaptureSetModifies = true;
    bool bCandidateCastShadow = true;
    if (!Root->TryGetStringField(TEXT("schema_version"), SchemaVersion)
        || SchemaVersion != TEXT("0.1")
        || !Root->TryGetStringField(TEXT("report_id"), OutReport.ReportId)
        || !Root->TryGetStringField(TEXT("status"), OutReport.Status)
        || !Root->TryGetStringField(TEXT("decision"), OutReport.Decision)
        || !Root->TryGetStringField(
            TEXT("capture_set_sha256"), OutReport.CaptureSetSha256)
        || !Root->TryGetStringField(
            TEXT("capture_set_file_sha256"), OutReport.CaptureSetFileSha256)
        || !Root->TryGetStringField(TEXT("analysis_method"), AnalysisMethod)
        || AnalysisMethod != TEXT("deterministic_host")
        || !Root->TryGetStringField(TEXT("summary"), OutReport.Summary)
        || !Root->TryGetBoolField(
            TEXT("runtime_state_restored"), OutReport.bRuntimeStateRestored)
        || !Root->TryGetBoolField(
            TEXT("modifies_editor_scene"), OutReport.bModifiesEditorScene)
        || !Root->TryGetBoolField(
            TEXT("approval_required"), OutReport.bApprovalRequired)
        || !Root->TryGetBoolField(TEXT("auto_save"), OutReport.bAutoSave)
        || !Root->TryGetBoolField(
            TEXT("undo_supported"), OutReport.bUndoSupported)
        || !Root->TryGetStringField(
            TEXT("visual_evidence_role"), VisualEvidenceRole)
        || VisualEvidenceRole != TEXT("operator_regression_review")
        || !ReadInsightsObject(Root, TEXT("capture_set"), CaptureSet)
        || !CaptureSet->TryGetStringField(
            TEXT("experiment_id"), OutReport.ExperimentId)
        || !CaptureSet->TryGetBoolField(
            TEXT("runtime_state_restored"), bCaptureSetRestored)
        || !CaptureSet->TryGetBoolField(
            TEXT("modifies_editor_scene"), bCaptureSetModifies)
        || !CaptureSet->TryGetBoolField(
            TEXT("candidate_cast_shadow"), bCandidateCastShadow)
        || !ReadInsightsObject(CaptureSet, TEXT("target"), Target)
        || !Target->TryGetStringField(TEXT("project_name"), ProjectName)
        || !Target->TryGetStringField(TEXT("level_path"), LevelPath)
        || !RenderMasterParsePerformanceEvidenceJson(
            Target,
            ProjectName,
            LevelPath,
            OutReport.ProposedAction.Target,
            OutError))
    {
        if (OutError.IsEmpty())
            OutError = TEXT("Shadow optimization report is missing safety evidence.");
        return false;
    }
    const TArray<TSharedPtr<FJsonValue>>* ControlCaptures = nullptr;
    if (!CaptureSet->TryGetArrayField(
            TEXT("noise_control_baseline"), ControlCaptures)
        || ControlCaptures == nullptr || ControlCaptures->Num() != 2
        || !(*ControlCaptures)[0].IsValid()
        || !(WorkloadCapture = (*ControlCaptures)[0]->AsObject()).IsValid()
        || !WorkloadCapture->TryGetStringField(
            TEXT("project_name"), OutReport.ProjectName)
        || !WorkloadCapture->TryGetStringField(
            TEXT("world_path"), OutReport.WorldPath)
        || !WorkloadCapture->TryGetStringField(
            TEXT("capture_mode"), OutReport.CaptureMode)
        || !ReadInsightsInteger(
            WorkloadCapture,
            TEXT("viewport_width_px"),
            1,
            16384,
            OutReport.ViewportSize.X)
        || !ReadInsightsInteger(
            WorkloadCapture,
            TEXT("viewport_height_px"),
            1,
            16384,
            OutReport.ViewportSize.Y)
        || !WorkloadCapture->TryGetStringField(
            TEXT("gpu_name"), OutReport.GpuName)
        || OutReport.ProjectName.IsEmpty()
        || OutReport.WorldPath.IsEmpty()
        || (OutReport.CaptureMode != TEXT("pie")
            && OutReport.CaptureMode != TEXT("simulate"))
        || OutReport.GpuName.IsEmpty())
    {
        OutError = TEXT("Shadow optimization workload identity is invalid.");
        return false;
    }
    Root->TryGetStringField(
        TEXT("primary_aggregate_id"), OutReport.PrimaryAggregateId);
    if (OutReport.ReportId.IsEmpty() || OutReport.ExperimentId.IsEmpty()
        || OutReport.Summary.IsEmpty()
        || !IsLowerHexInsightsSha256(OutReport.CaptureSetSha256)
        || !IsLowerHexInsightsSha256(OutReport.CaptureSetFileSha256)
        || !OutReport.bRuntimeStateRestored || !bCaptureSetRestored
        || OutReport.bModifiesEditorScene || bCaptureSetModifies
        || bCandidateCastShadow
        || OutReport.bAutoSave || !OutReport.bUndoSupported
        || !OutReport.ProposedAction.Target.Before.bCastShadow)
    {
        OutError = TEXT("Shadow optimization report safety flags are invalid.");
        return false;
    }

    TSharedPtr<FJsonObject> Thresholds;
    double MinimumSignal = 0.0;
    double MinimumRelative = 0.0;
    double MaximumMadRatio = 0.0;
    double MinimumVotes = 0.0;
    if (!ReadInsightsObject(Root, TEXT("thresholds"), Thresholds)
        || !Thresholds->TryGetNumberField(
            TEXT("minimum_signal_excess_ms_per_second"), MinimumSignal)
        || !Thresholds->TryGetNumberField(
            TEXT("minimum_relative_reduction_percent"), MinimumRelative)
        || !Thresholds->TryGetNumberField(
            TEXT("maximum_mad_to_median_ratio"), MaximumMadRatio)
        || !Thresholds->TryGetNumberField(
            TEXT("minimum_decreased_votes"), MinimumVotes)
        || !FMath::IsNearlyEqual(MinimumSignal, 1.0)
        || !FMath::IsNearlyEqual(MinimumRelative, 1.0)
        || !FMath::IsNearlyEqual(MaximumMadRatio, 1.0)
        || !FMath::IsNearlyEqual(MinimumVotes, 2.0))
    {
        OutError = TEXT("Shadow optimization decision thresholds are invalid.");
        return false;
    }

    const TArray<TSharedPtr<FJsonValue>>* AggregateValues = nullptr;
    if (!Root->TryGetArrayField(TEXT("aggregates"), AggregateValues)
        || AggregateValues == nullptr || AggregateValues->Num() > 64)
    {
        OutError = TEXT("Shadow optimization aggregates are missing or invalid.");
        return false;
    }
    TSet<FString> AggregateIds;
    for (const TSharedPtr<FJsonValue>& Value : *AggregateValues)
    {
        const TSharedPtr<FJsonObject> Json =
            Value.IsValid() ? Value->AsObject() : nullptr;
        FRenderMasterShadowOptimizationAggregate Parsed;
        double Decreased = -1.0;
        double Increased = -1.0;
        double Unchanged = -1.0;
        if (!Json.IsValid()
            || !Json->TryGetStringField(TEXT("aggregate_id"), Parsed.AggregateId)
            || !Json->TryGetStringField(TEXT("queue_name"), Parsed.QueueName)
            || !Json->TryGetStringField(TEXT("scope_name"), Parsed.ScopeName)
            || !Json->TryGetStringField(
                TEXT("classification"), Parsed.Classification)
            || !Json->TryGetStringField(
                TEXT("dominant_direction_when_disabled"),
                Parsed.DominantDirection)
            || !Json->TryGetNumberField(
                TEXT("median_baseline_total_ms_per_second"),
                Parsed.MedianBaselineMsPerSecond)
            || !Json->TryGetNumberField(
                TEXT("median_candidate_total_ms_per_second"),
                Parsed.MedianCandidateMsPerSecond)
            || !Json->TryGetNumberField(
                TEXT("median_baseline_minus_candidate_ms_per_second"),
                Parsed.MedianDeltaMsPerSecond)
            || !Json->TryGetNumberField(
                TEXT("delta_mad_ms_per_second"), Parsed.DeltaMadMsPerSecond)
            || !Json->TryGetNumberField(
                TEXT("control_noise_abs_ms_per_second"),
                Parsed.ControlNoiseMsPerSecond)
            || !Json->TryGetNumberField(
                TEXT("signal_excess_over_control_noise_ms_per_second"),
                Parsed.SignalExcessMsPerSecond)
            || !Json->TryGetNumberField(
                TEXT("relative_reduction_percent"),
                Parsed.RelativeReductionPercent)
            || !Json->TryGetNumberField(TEXT("decreased_vote_count"), Decreased)
            || !Json->TryGetNumberField(TEXT("increased_vote_count"), Increased)
            || !Json->TryGetNumberField(TEXT("unchanged_vote_count"), Unchanged))
        {
            OutError = TEXT("Shadow optimization contains an invalid aggregate.");
            return false;
        }
        if (Parsed.AggregateId.IsEmpty() || Parsed.QueueName.IsEmpty()
            || Parsed.ScopeName.IsEmpty() || AggregateIds.Contains(Parsed.AggregateId)
            || !FMath::IsFinite(Parsed.MedianBaselineMsPerSecond)
            || Parsed.MedianBaselineMsPerSecond <= 0.0
            || !FMath::IsFinite(Parsed.MedianCandidateMsPerSecond)
            || !FMath::IsFinite(Parsed.MedianDeltaMsPerSecond)
            || !FMath::IsFinite(Parsed.DeltaMadMsPerSecond)
            || !FMath::IsFinite(Parsed.ControlNoiseMsPerSecond)
            || !FMath::IsFinite(Parsed.SignalExcessMsPerSecond)
            || !FMath::IsFinite(Parsed.RelativeReductionPercent)
            || Decreased < 0.0 || Increased < 0.0 || Unchanged < 0.0
            || !FMath::IsNearlyEqual(Decreased + Increased + Unchanged, 3.0)
            || !FMath::IsNearlyEqual(Decreased, FMath::RoundToDouble(Decreased))
            || !FMath::IsNearlyEqual(Increased, FMath::RoundToDouble(Increased))
            || !FMath::IsNearlyEqual(Unchanged, FMath::RoundToDouble(Unchanged)))
        {
            OutError = TEXT("Shadow optimization aggregate values are unsafe.");
            return false;
        }
        Parsed.DecreasedVotes = static_cast<int32>(Decreased);
        Parsed.IncreasedVotes = static_cast<int32>(Increased);
        Parsed.UnchangedVotes = static_cast<int32>(Unchanged);
        AggregateIds.Add(Parsed.AggregateId);
        OutReport.Aggregates.Add(MoveTemp(Parsed));
    }

    const FString RejectionText = ReadInsightsStringArray(
        Root, TEXT("rejection_reasons"), &OutReport.RejectionReasons);
    TSharedPtr<FJsonObject> ProposedAction;
    OutReport.bHasProposedAction = ReadInsightsObject(
        Root, TEXT("proposed_action"), ProposedAction);
    if (OutReport.bHasProposedAction
        && !RenderMasterParsePerformanceActionJson(
            ProposedAction,
            ProjectName,
            LevelPath,
            OutReport.ProposedAction.Target,
            OutReport.ProposedAction,
            OutError))
    {
        return false;
    }
    auto IsQualifying = [](const FRenderMasterShadowOptimizationAggregate& Item)
    {
        return Item.Classification == TEXT("stable_candidate")
            && Item.DominantDirection == TEXT("decreased")
            && Item.SignalExcessMsPerSecond >= 1.0
            && Item.RelativeReductionPercent >= 1.0
            && Item.DecreasedVotes >= 2
            && Item.DeltaMadMsPerSecond
                <= FMath::Abs(Item.MedianDeltaMsPerSecond);
    };
    const FRenderMasterShadowOptimizationAggregate* Primary =
        OutReport.Aggregates.FindByPredicate(
            [&OutReport](const FRenderMasterShadowOptimizationAggregate& Item)
            {
                return Item.AggregateId == OutReport.PrimaryAggregateId;
            });
    const bool bAnyQualifying = OutReport.Aggregates.ContainsByPredicate(
        IsQualifying);
    if (OutReport.Status == TEXT("proposed"))
    {
        if (OutReport.Decision != TEXT("propose_apply")
            || !OutReport.bApprovalRequired || !OutReport.bHasProposedAction
            || Primary == nullptr || !IsQualifying(*Primary)
            || !RejectionText.IsEmpty()
            || OutReport.ProposedAction.Before.bCastShadow != true
            || OutReport.ProposedAction.After.bCastShadow != false
            || !FMath::IsNearlyEqual(
                OutReport.ProposedAction.Before.MaxDrawDistanceCm,
                OutReport.ProposedAction.After.MaxDrawDistanceCm,
                0.01f))
        {
            OutError = TEXT("Shadow optimization proposal is not evidence-bound.");
            return false;
        }
    }
    else if (OutReport.Status == TEXT("rejected"))
    {
        if (OutReport.Decision != TEXT("reject") || OutReport.bApprovalRequired
            || OutReport.bHasProposedAction || bAnyQualifying
            || RejectionText.IsEmpty() || !OutReport.PrimaryAggregateId.IsEmpty())
        {
            OutError = TEXT("Rejected shadow optimization report is inconsistent.");
            return false;
        }
    }
    else if (OutReport.Status == TEXT("unresolved"))
    {
        if (OutReport.Decision != TEXT("unresolved")
            || OutReport.bApprovalRequired || OutReport.bHasProposedAction
            || !OutReport.Aggregates.IsEmpty() || RejectionText.IsEmpty()
            || !OutReport.PrimaryAggregateId.IsEmpty())
        {
            OutError = TEXT("Unresolved shadow optimization report is inconsistent.");
            return false;
        }
    }
    else
    {
        OutError = TEXT("Shadow optimization report status is unsupported.");
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
    bRepeatableActorImpactStudy = false;
    bShadowOptimizationStudy = false;
    bShadowOptimizationApplied = false;
    bShadowOptimizationRejected = false;
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
    bRepeatableActorImpactStudy = false;
    bShadowOptimizationStudy = false;
    bShadowOptimizationApplied = false;
    bShadowOptimizationRejected = false;
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

bool FRenderMasterInsightsGpuAssistant::StartActorImpactStudy(
    const FString& Prompt,
    AActor* SelectedActor)
{
    return StartRepeatableActorStudy(Prompt, SelectedActor, false);
}

bool FRenderMasterInsightsGpuAssistant::StartActorShadowOptimizationStudy(
    const FString& Prompt,
    AActor* SelectedActor)
{
    return StartRepeatableActorStudy(Prompt, SelectedActor, true);
}

bool FRenderMasterInsightsGpuAssistant::StartRepeatableActorStudy(
    const FString& Prompt,
    AActor* SelectedActor,
    bool bShadowOptimization)
{
    if (!CanStart()) return false;
    const FString CleanPrompt = Prompt.TrimStartAndEnd();
    if (CleanPrompt.IsEmpty())
    {
        Fail(bShadowOptimization
            ? TEXT("Enter a Cast Shadow optimization question before starting the experiment.")
            : TEXT("Enter a repeatable selected-Actor GPU question before starting the study."));
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
            TEXT("Another Unreal trace is already active at %s. Stop it before starting the repeatable Actor study; the Assistant will not interrupt external traces."),
            *FTraceAuxiliary::GetTraceDestinationString()));
        return false;
    }
    UWorld* PlayWorld = GEditor != nullptr ? GEditor->PlayWorld : nullptr;
    if (PlayWorld == nullptr)
    {
        Fail(TEXT(
            "Start PIE or SIE, keep one level Actor selected, stabilize the workload, then start the repeatable study."));
        return false;
    }
    if (!IsValid(SelectedActor))
    {
        Fail(TEXT("Select exactly one Actor that existed before PIE/SIE started."));
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
        Fail(TEXT("The selected runtime Actor must be visible before the study starts."));
        return false;
    }
    AStaticMeshActor* EditorStaticMeshActor = Cast<AStaticMeshActor>(EditorActor);
    AStaticMeshActor* RuntimeStaticMeshActor = Cast<AStaticMeshActor>(RuntimeActor);
    UStaticMeshComponent* EditorStaticMeshComponent = EditorStaticMeshActor != nullptr
        ? EditorStaticMeshActor->GetStaticMeshComponent()
        : nullptr;
    UStaticMeshComponent* RuntimeStaticMeshComponent = RuntimeStaticMeshActor != nullptr
        ? RuntimeStaticMeshActor->GetStaticMeshComponent()
        : nullptr;
    FRenderMasterPerformanceEvidence ShadowEvidence;
    FString ShadowEvidenceError;
    if (bShadowOptimization
        && (EditorStaticMeshActor == nullptr || RuntimeStaticMeshActor == nullptr
            || EditorStaticMeshComponent == nullptr
            || RuntimeStaticMeshComponent == nullptr
            || !RuntimeStaticMeshComponent->IsRegistered()
            || !RenderMasterCapturePerformanceEvidence(
                EditorStaticMeshActor,
                EditorStaticMeshComponent,
                ShadowEvidence,
                ShadowEvidenceError)
            || !ShadowEvidence.Before.bCastShadow
            || !RuntimeStaticMeshComponent->CastShadow))
    {
        Fail(ShadowEvidenceError.IsEmpty()
            ? TEXT("Shadow optimization requires one editable native StaticMeshActor whose Editor and PIE/SIE components currently have Cast Shadow enabled.")
            : ShadowEvidenceError);
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
    ActorImpactStudyReport = FRenderMasterActorGpuImpactStudyReport();
    ShadowOptimizationReport = FRenderMasterShadowOptimizationReport();
    ActorImpactDeltas.Reset();
    ActorImpactStudyCaptures.Reset();
    UnmatchedBaselineScopeIds.Reset();
    UnmatchedVariantScopeIds.Reset();
    AnalysisSession.Reset();
    AnalysisService.Reset();
    ErrorText.Reset();
    ProcessLog.Reset();
    ReviewRequest = CleanPrompt;
    bActorImpactExperiment = true;
    bRepeatableActorImpactStudy = true;
    bShadowOptimizationStudy = bShadowOptimization;
    bShadowOptimizationApplied = false;
    bShadowOptimizationRejected = false;
    bCapturingActorVariant = false;
    bImpactActorHidden = false;
    bOriginalImpactActorHidden = RuntimeActor->IsHidden();
    bOriginalRuntimeCastShadow = RuntimeStaticMeshComponent != nullptr
        ? RuntimeStaticMeshComponent->CastShadow
        : true;
    bShadowCandidateActive = false;
    ImpactEditorActor = EditorActor;
    ImpactRuntimeActor = RuntimeActor;
    ShadowEditorComponent = bShadowOptimization
        ? EditorStaticMeshComponent
        : nullptr;
    ShadowRuntimeComponent = bShadowOptimization
        ? RuntimeStaticMeshComponent
        : nullptr;
    ShadowTargetEvidence = bShadowOptimization
        ? ShadowEvidence
        : FRenderMasterPerformanceEvidence();
    ImpactEditorActorPath = EditorActor->GetPathName().Left(500);
    ImpactRuntimeActorPath = RuntimeActor->GetPathName().Left(500);
    ImpactActorLabel = EditorActor->GetActorLabel().Left(240);
    ImpactActorClass = EditorActor->GetClass()->GetPathName().Left(500);
    ImpactPrimitiveComponentCount = PrimitiveComponents.Num();
    CaptureWorld = PlayWorld;
    const FString StudyGuid = FGuid::NewGuid().ToString(
        EGuidFormats::Digits).ToLower();
    ActorImpactStudyId = bShadowOptimization
        ? FString::Printf(TEXT("shadow_optimization_%s"), *StudyGuid)
        : FString::Printf(TEXT("actor_gpu_study_%s"), *StudyGuid);
    ShadowOptimizationExperimentId = bShadowOptimization
        ? ActorImpactStudyId
        : FString();
    ActorImpactExperimentId.Reset();
    RequestDirectory = FPaths::Combine(
        Root,
        bShadowOptimization
            ? TEXT("assistant-shadow-optimization")
            : TEXT("assistant-actor-gpu-impact-study"),
        ActorImpactStudyId);
    IFileManager::Get().MakeDirectory(*RequestDirectory, true);
    PromptPath = FPaths::Combine(RequestDirectory, TEXT("request.txt"));
    ActorImpactStudyCaptureSetPath = FPaths::Combine(
        RequestDirectory, TEXT("actor_gpu_impact_study_capture_set.json"));
    ShadowOptimizationCaptureSetPath = FPaths::Combine(
        RequestDirectory, TEXT("shadow_optimization_capture_set.json"));
    ReportOutputPath = FPaths::Combine(
        RequestDirectory,
        bShadowOptimization
            ? TEXT("shadow_optimization_report.json")
            : TEXT("actor_gpu_impact_study_report.json"));
    ActorImpactStudyCaptureSetFileSha256.Reset();
    ShadowOptimizationCaptureSetFileSha256.Reset();
    CaptureFileSha256.Reset();

    auto AddStudyCapture = [this](
        const TCHAR* Label,
        const TCHAR* FileStem,
        bool bVariant)
    {
        FRenderMasterActorGpuStudyCapturePlan& Plan =
            ActorImpactStudyCaptures.AddDefaulted_GetRef();
        Plan.Label = Label;
        Plan.bVariant = bVariant;
        Plan.TracePath = FPaths::Combine(
            RequestDirectory, FString(FileStem) + TEXT(".utrace"));
        Plan.CapturePath = FPaths::Combine(
            RequestDirectory, FString(FileStem) + TEXT("_capture.json"));
    };
    if (bShadowOptimization)
    {
        AddStudyCapture(TEXT("A/A control 1 of 2 (Cast Shadow on)"), TEXT("control_shadow_on_01"), false);
        AddStudyCapture(TEXT("A/A control 2 of 2 (Cast Shadow on)"), TEXT("control_shadow_on_02"), false);
        AddStudyCapture(TEXT("trial 1 A (Cast Shadow on)"), TEXT("trial_001_shadow_on"), false);
        AddStudyCapture(TEXT("trial 1 B (Cast Shadow off)"), TEXT("trial_001_shadow_off"), true);
        AddStudyCapture(TEXT("trial 2 B (Cast Shadow off)"), TEXT("trial_002_shadow_off"), true);
        AddStudyCapture(TEXT("trial 2 A (Cast Shadow on)"), TEXT("trial_002_shadow_on"), false);
        AddStudyCapture(TEXT("trial 3 A (Cast Shadow on)"), TEXT("trial_003_shadow_on"), false);
        AddStudyCapture(TEXT("trial 3 B (Cast Shadow off)"), TEXT("trial_003_shadow_off"), true);
    }
    else
    {
        AddStudyCapture(TEXT("A/A control 1 of 2 (visible)"), TEXT("control_visible_01"), false);
        AddStudyCapture(TEXT("A/A control 2 of 2 (visible)"), TEXT("control_visible_02"), false);
        AddStudyCapture(TEXT("trial 1 A (visible)"), TEXT("trial_001_visible"), false);
        AddStudyCapture(TEXT("trial 1 B (hidden)"), TEXT("trial_001_hidden"), true);
        AddStudyCapture(TEXT("trial 2 B (hidden)"), TEXT("trial_002_hidden"), true);
        AddStudyCapture(TEXT("trial 2 A (visible)"), TEXT("trial_002_visible"), false);
        AddStudyCapture(TEXT("trial 3 A (visible)"), TEXT("trial_003_visible"), false);
        AddStudyCapture(TEXT("trial 3 B (hidden)"), TEXT("trial_003_hidden"), true);
    }
    BaselineTracePath = ActorImpactStudyCaptures[0].TracePath;
    BaselineCapturePath = ActorImpactStudyCaptures[0].CapturePath;

    BaselineCapture.ProjectName = FApp::GetProjectName();
    BaselineCapture.WorldPath = PlayWorld->GetPackage()->GetName();
    BaselineCapture.CaptureMode = GEditor->IsSimulatingInEditor()
        ? TEXT("simulate")
        : TEXT("pie");
    BaselineCapture.ViewportSize = ViewportSize;
    BaselineCapture.GpuName = GRHIAdapterName.IsEmpty()
        ? TEXT("Unknown RHI adapter")
        : GRHIAdapterName.Left(240);
    BaselineCapture.Channels = {
        TEXT("cpu"), TEXT("gpu"), TEXT("frame"), TEXT("bookmark")};

    if (!FFileHelper::SaveStringToFile(
            ReviewRequest,
            *PromptPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        Fail(bShadowOptimization
            ? TEXT("Could not write the Cast Shadow optimization request.")
            : TEXT("Could not write the repeatable Actor GPU study request."));
        return false;
    }
    ActorImpactStudyCaptureIndex = 0;
    VariantWarmupStartedSeconds = FPlatformTime::Seconds();
    State = ERenderMasterInsightsGpuState::WarmingStudyCapture;
    AppendLog(bShadowOptimization
        ? FString::Printf(
            TEXT("Starting Cast Shadow optimization experiment for %s: 8 captures in AA_AB_BA_AB order, with runtime-only shadow changes and 1.0-second warmups."),
            *ImpactActorLabel)
        : FString::Printf(
            TEXT("Starting repeatable GPU study for %s: 8 captures in AA_AB_BA_AB order, with 1.0-second warmup before every capture."),
            *ImpactActorLabel));
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
    if (State == ERenderMasterInsightsGpuState::WarmingStudyCapture)
    {
        TickStudyCaptureWarmup();
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
    if (bRepeatableActorImpactStudy
        && ActorImpactStudyCaptureIndex == ActorImpactStudyCaptures.Num() - 1
        && (bImpactActorHidden || bShadowCandidateActive)
        && !RestoreImpactActor(Error))
    {
        Fail(Error);
        return false;
    }
    if (bActorImpactExperiment && !bRepeatableActorImpactStudy
        && bCapturingActorVariant
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

bool FRenderMasterInsightsGpuAssistant::TickStudyCaptureWarmup()
{
    UWorld* World = CaptureWorld.Get();
    AActor* RuntimeActor = ImpactRuntimeActor.Get();
    if (!bRepeatableActorImpactStudy || World == nullptr
        || GEditor == nullptr || GEditor->PlayWorld != World
        || !IsValid(RuntimeActor)
        || !ActorImpactStudyCaptures.IsValidIndex(ActorImpactStudyCaptureIndex))
    {
        FString RestoreError;
        RestoreImpactActor(RestoreError);
        Fail(TEXT("PIE/SIE or the selected Actor changed during study warmup."));
        return false;
    }
    const bool bExpectedVariant =
        ActorImpactStudyCaptures[ActorImpactStudyCaptureIndex].bVariant;
    UStaticMeshComponent* RuntimeShadowComponent = ShadowRuntimeComponent.Get();
    const bool bStateMatches = bShadowOptimizationStudy
        ? IsValid(RuntimeShadowComponent)
            && RuntimeShadowComponent->CastShadow == !bExpectedVariant
            && bShadowCandidateActive == bExpectedVariant
        : RuntimeActor->IsHidden() == bExpectedVariant;
    if (!bStateMatches)
    {
        FString RestoreError;
        RestoreImpactActor(RestoreError);
        Fail(TEXT("The selected Actor state changed during study warmup."));
        return false;
    }
    constexpr double StudyWarmupSeconds = 1.0;
    if (FPlatformTime::Seconds() - VariantWarmupStartedSeconds
        < StudyWarmupSeconds)
    {
        return true;
    }
    FString Error;
    if (!BeginActorStudyCapture(Error))
    {
        Fail(Error);
        return false;
    }
    return true;
}

bool FRenderMasterInsightsGpuAssistant::BeginActorStudyCapture(
    FString& OutError)
{
    UWorld* PlayWorld = CaptureWorld.Get();
    AActor* RuntimeActor = ImpactRuntimeActor.Get();
    if (!bRepeatableActorImpactStudy || !IsValid(PlayWorld)
        || GEditor == nullptr || GEditor->PlayWorld != PlayWorld
        || !IsValid(RuntimeActor)
        || !ActorImpactStudyCaptures.IsValidIndex(ActorImpactStudyCaptureIndex))
    {
        OutError = TEXT("The repeatable Actor GPU study state is unavailable.");
        return false;
    }
    const FRenderMasterActorGpuStudyCapturePlan& Plan =
        ActorImpactStudyCaptures[ActorImpactStudyCaptureIndex];
    UStaticMeshComponent* RuntimeShadowComponent = ShadowRuntimeComponent.Get();
    const bool bStateMatches = bShadowOptimizationStudy
        ? IsValid(RuntimeShadowComponent)
            && RuntimeShadowComponent->CastShadow == !Plan.bVariant
            && bShadowCandidateActive == Plan.bVariant
        : RuntimeActor->IsHidden() == Plan.bVariant;
    if (!bStateMatches)
    {
        OutError = TEXT("The selected Actor state does not match the study schedule.");
        return false;
    }
    if (FTraceAuxiliary::IsConnected())
    {
        OutError = TEXT("Another Unreal trace started during the Actor GPU study.");
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
        OutError = TEXT("The PIE/SIE viewport changed during the Actor GPU study.");
        return false;
    }

    Capture = FRenderMasterInsightsGpuCapture();
    Capture.CaptureId = FString::Printf(
        TEXT("%s_capture_%03d"),
        *ActorImpactStudyId,
        ActorImpactStudyCaptureIndex + 1);
    Capture.CapturedAtUtc = FDateTime::UtcNow().ToIso8601();
    Capture.ProjectName = BaselineCapture.ProjectName;
    Capture.WorldPath = BaselineCapture.WorldPath;
    Capture.CaptureMode = BaselineCapture.CaptureMode;
    Capture.ViewportSize = BaselineCapture.ViewportSize;
    Capture.GpuName = BaselineCapture.GpuName;
    Capture.Channels = BaselineCapture.Channels;
    TracePath = Plan.TracePath;
    CapturePath = Plan.CapturePath;
    Capture.TraceFileName = FPaths::GetCleanFilename(TracePath);
    CaptureFileSha256.Reset();
    bCapturingActorVariant = Plan.bVariant;

    FTraceAuxiliary::FOptions Options;
    Options.bTruncateFile = true;
    Options.bExcludeTail = true;
    if (!FTraceAuxiliary::Start(
            FTraceAuxiliary::EConnectionType::File,
            *TracePath,
            InsightsTraceChannels,
            &Options))
    {
        OutError = TEXT("Unreal could not start the scheduled Actor GPU trace.");
        return false;
    }
    bOwnsTrace = true;
    CaptureStartedSeconds = FPlatformTime::Seconds();
    State = ERenderMasterInsightsGpuState::Capturing;
    AppendLog(FString::Printf(
        TEXT("Capture %d of %d: %s for %.1f seconds."),
        ActorImpactStudyCaptureIndex + 1,
        ActorImpactStudyCaptures.Num(),
        *Plan.Label,
        Capture.RequestedDurationSeconds));
    return true;
}

bool FRenderMasterInsightsGpuAssistant::CompleteActorStudyCapture(
    FString& OutError)
{
    if (!ActorImpactStudyCaptures.IsValidIndex(ActorImpactStudyCaptureIndex))
    {
        OutError = TEXT("The completed Actor GPU study capture has no schedule entry.");
        return false;
    }
    ActorImpactStudyCaptures[ActorImpactStudyCaptureIndex].Capture = Capture;
    if (ActorImpactStudyCaptureIndex + 1 < ActorImpactStudyCaptures.Num())
    {
        ++ActorImpactStudyCaptureIndex;
        const bool bNextVariant =
            ActorImpactStudyCaptures[ActorImpactStudyCaptureIndex].bVariant;
        AActor* RuntimeActor = ImpactRuntimeActor.Get();
        if (!IsValid(RuntimeActor) || RuntimeActor->GetWorld() != CaptureWorld.Get())
        {
            OutError = TEXT("The selected runtime Actor disappeared during the study.");
            return false;
        }
        if (!ApplyRepeatableStudyVariant(bNextVariant, OutError)) return false;
        VariantWarmupStartedSeconds = FPlatformTime::Seconds();
        State = ERenderMasterInsightsGpuState::WarmingStudyCapture;
        AppendLog(FString::Printf(
            TEXT("Capture %d parsed. Preparing %s with a 1.0-second warmup."),
            ActorImpactStudyCaptureIndex,
            *ActorImpactStudyCaptures[ActorImpactStudyCaptureIndex].Label));
        return true;
    }

    if (!RestoreImpactActor(OutError)) return false;
    if (bShadowOptimizationStudy)
    {
        if (!WriteShadowOptimizationCaptureSet(OutError)) return false;
    }
    else if (!WriteActorImpactStudyCaptureSet(OutError)) return false;
    int64 CaptureSetFileSizeBytes = 0;
    if (!HashInsightsFile(
            bShadowOptimizationStudy
                ? ShadowOptimizationCaptureSetPath
                : ActorImpactStudyCaptureSetPath,
            bShadowOptimizationStudy
                ? ShadowOptimizationCaptureSetFileSha256
                : ActorImpactStudyCaptureSetFileSha256,
            CaptureSetFileSizeBytes,
            OutError))
    {
        return false;
    }
    return bShadowOptimizationStudy
        ? StartShadowOptimizationProcess(OutError)
        : StartActorImpactStudyReviewProcess(OutError);
}

bool FRenderMasterInsightsGpuAssistant::ApplyRepeatableStudyVariant(
    bool bVariant,
    FString& OutError)
{
    AActor* RuntimeActor = ImpactRuntimeActor.Get();
    if (!IsValid(RuntimeActor) || RuntimeActor->GetWorld() != CaptureWorld.Get())
    {
        OutError = TEXT("The selected runtime Actor disappeared during the study.");
        return false;
    }
    if (bShadowOptimizationStudy)
    {
        UStaticMeshComponent* Component = ShadowRuntimeComponent.Get();
        if (!IsValid(Component) || !Component->IsRegistered())
        {
            OutError = TEXT("The runtime Static Mesh Component disappeared during shadow optimization.");
            return false;
        }
        const bool bExpectedCastShadow = bVariant ? false : bOriginalRuntimeCastShadow;
        Component->SetCastShadow(bExpectedCastShadow);
        if (Component->CastShadow != bExpectedCastShadow)
        {
            OutError = TEXT("Unreal could not apply the scheduled runtime Cast Shadow state.");
            return false;
        }
        bShadowCandidateActive = bVariant;
        return true;
    }
    if (bVariant)
    {
        RuntimeActor->SetActorHiddenInGame(true);
        if (!RuntimeActor->IsHidden())
        {
            OutError = TEXT("Unreal could not apply the scheduled hidden state.");
            return false;
        }
        bImpactActorHidden = true;
        return true;
    }
    return RestoreImpactActor(OutError);
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
    // TraceServices owns a process-wide service. Insights may have initialized
    // it already; CreateAnalysisService asserts when a service exists.
    AnalysisService = TraceServicesModule->GetAnalysisService();
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
    {
        TraceServices::FAnalysisSessionReadScope ReadScope(*AnalysisSession);
        Capture.AnalyzedTraceDurationSeconds = AnalysisSession->GetDurationSeconds();
    }
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
    if (bRepeatableActorImpactStudy)
    {
        return CompleteActorStudyCapture(OutError);
    }
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

bool FRenderMasterInsightsGpuAssistant::WriteActorImpactStudyCaptureSet(
    FString& OutError)
{
    AActor* RuntimeActor = ImpactRuntimeActor.Get();
    if (!bRepeatableActorImpactStudy || ActorImpactStudyCaptures.Num() != 8
        || !IsValid(RuntimeActor) || bImpactActorHidden
        || RuntimeActor->IsHidden() != bOriginalImpactActorHidden)
    {
        OutError = TEXT(
            "The repeatable Actor GPU capture set requires eight captures and verified restoration.");
        return false;
    }
    const bool ExpectedHiddenStates[8] = {
        false, false, false, true, true, false, false, true};
    for (int32 Index = 0; Index < ActorImpactStudyCaptures.Num(); ++Index)
    {
        if (ActorImpactStudyCaptures[Index].bVariant != ExpectedHiddenStates[Index])
        {
            OutError = TEXT("The Actor GPU study schedule no longer matches AA_AB_BA_AB.");
            return false;
        }
    }
    TArray<TSharedPtr<FJsonObject>> CaptureObjects;
    for (const FRenderMasterActorGpuStudyCapturePlan& Plan : ActorImpactStudyCaptures)
    {
        if (Plan.Capture.CaptureId.IsEmpty()
            || !FPaths::FileExists(Plan.TracePath)
            || !FPaths::FileExists(Plan.CapturePath))
        {
            OutError = TEXT("A scheduled Actor GPU study artifact is missing.");
            return false;
        }
        FString Text;
        TSharedPtr<FJsonObject> Object;
        if (!FFileHelper::LoadFileToString(Text, *Plan.CapturePath))
        {
            OutError = FString::Printf(
                TEXT("Could not read study capture JSON: %s"), *Plan.CapturePath);
            return false;
        }
        const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
        FString StoredCaptureId;
        if (!FJsonSerializer::Deserialize(Reader, Object) || !Object.IsValid()
            || !Object->TryGetStringField(TEXT("capture_id"), StoredCaptureId)
            || StoredCaptureId != Plan.Capture.CaptureId)
        {
            OutError = FString::Printf(
                TEXT("Study capture JSON identity is invalid: %s"), *Plan.CapturePath);
            return false;
        }
        CaptureObjects.Add(Object);
    }

    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Root->SetStringField(TEXT("study_id"), ActorImpactStudyId);
    Root->SetStringField(TEXT("created_at_utc"), FDateTime::UtcNow().ToIso8601());
    Root->SetStringField(
        TEXT("method"), TEXT("temporary_runtime_actor_hidden_counterbalanced"));
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
    Root->SetNumberField(
        TEXT("requested_capture_duration_seconds"),
        ActorImpactStudyCaptures[0].Capture.RequestedDurationSeconds);
    Root->SetNumberField(TEXT("warmup_seconds"), 1.0);
    Root->SetStringField(TEXT("schedule"), TEXT("AA_AB_BA_AB"));

    TArray<TSharedPtr<FJsonValue>> ControlValues;
    ControlValues.Add(MakeShared<FJsonValueObject>(CaptureObjects[0].ToSharedRef()));
    ControlValues.Add(MakeShared<FJsonValueObject>(CaptureObjects[1].ToSharedRef()));
    Root->SetArrayField(TEXT("noise_control_visible"), ControlValues);

    auto MakeTrial = [&CaptureObjects](
        const TCHAR* TrialId,
        const TCHAR* Order,
        int32 VisibleIndex,
        int32 HiddenIndex)
    {
        TSharedRef<FJsonObject> Trial = MakeShared<FJsonObject>();
        Trial->SetStringField(TEXT("trial_id"), TrialId);
        Trial->SetStringField(TEXT("order"), Order);
        Trial->SetObjectField(
            TEXT("visible"), CaptureObjects[VisibleIndex].ToSharedRef());
        Trial->SetObjectField(
            TEXT("hidden"), CaptureObjects[HiddenIndex].ToSharedRef());
        return MakeShared<FJsonValueObject>(Trial);
    };
    TArray<TSharedPtr<FJsonValue>> Trials;
    Trials.Add(MakeTrial(
        TEXT("trial_001"), TEXT("visible_then_hidden"), 2, 3));
    Trials.Add(MakeTrial(
        TEXT("trial_002"), TEXT("hidden_then_visible"), 5, 4));
    Trials.Add(MakeTrial(
        TEXT("trial_003"), TEXT("visible_then_hidden"), 6, 7));
    Root->SetArrayField(TEXT("trials"), Trials);

    TArray<TSharedPtr<FJsonValue>> SequenceIds;
    for (const FRenderMasterActorGpuStudyCapturePlan& Plan : ActorImpactStudyCaptures)
        SequenceIds.Add(MakeShared<FJsonValueString>(Plan.Capture.CaptureId));
    Root->SetArrayField(TEXT("sequence_capture_ids"), SequenceIds);
    Root->SetNumberField(TEXT("trial_count"), 3);
    Root->SetStringField(
        TEXT("repeatability"), TEXT("counterbalanced_three_trial"));
    Root->SetBoolField(TEXT("runtime_state_restored"), true);
    TArray<TSharedPtr<FJsonValue>> Notes;
    Notes.Add(MakeShared<FJsonValueString>(TEXT(
        "Two Actor-visible A/A captures estimate no-change scope noise.")));
    Notes.Add(MakeShared<FJsonValueString>(TEXT(
        "Three paired trials use counterbalanced AB, BA, AB order.")));
    Notes.Add(MakeShared<FJsonValueString>(TEXT(
        "Every trace is preceded by a one-second state warmup.")));
    Notes.Add(MakeShared<FJsonValueString>(TEXT(
        "Only scopes present in all eight Top-64 sets can produce an aggregate.")));
    Notes.Add(MakeShared<FJsonValueString>(TEXT(
        "The study measures association and does not prove direct Actor draw ownership.")));
    Root->SetArrayField(TEXT("measurement_notes"), Notes);

    FString JsonText;
    if (!SerializeInsightsJson(Root, JsonText)
        || !FFileHelper::SaveStringToFile(
            JsonText,
            *ActorImpactStudyCaptureSetPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        OutError = FString::Printf(
            TEXT("Could not write Actor GPU impact study capture set: %s"),
            *ActorImpactStudyCaptureSetPath);
        return false;
    }
    return true;
}

bool FRenderMasterInsightsGpuAssistant::WriteShadowOptimizationCaptureSet(
    FString& OutError)
{
    AActor* EditorActor = ImpactEditorActor.Get();
    UStaticMeshComponent* RuntimeComponent = ShadowRuntimeComponent.Get();
    if (!bShadowOptimizationStudy || ActorImpactStudyCaptures.Num() != 8
        || !IsValid(EditorActor) || !IsValid(RuntimeComponent)
        || bShadowCandidateActive
        || RuntimeComponent->CastShadow != bOriginalRuntimeCastShadow
        || !bOriginalRuntimeCastShadow)
    {
        OutError = TEXT(
            "The shadow optimization capture set requires eight captures and verified Cast Shadow restoration.");
        return false;
    }
    const bool ExpectedCandidateStates[8] = {
        false, false, false, true, true, false, false, true};
    for (int32 Index = 0; Index < ActorImpactStudyCaptures.Num(); ++Index)
    {
        if (ActorImpactStudyCaptures[Index].bVariant
            != ExpectedCandidateStates[Index])
        {
            OutError = TEXT(
                "The shadow optimization schedule no longer matches AA_AB_BA_AB.");
            return false;
        }
    }

    TArray<TSharedPtr<FJsonObject>> CaptureObjects;
    for (const FRenderMasterActorGpuStudyCapturePlan& Plan : ActorImpactStudyCaptures)
    {
        if (Plan.Capture.CaptureId.IsEmpty()
            || !FPaths::FileExists(Plan.TracePath)
            || !FPaths::FileExists(Plan.CapturePath))
        {
            OutError = TEXT("A scheduled shadow optimization artifact is missing.");
            return false;
        }
        FString Text;
        TSharedPtr<FJsonObject> Object;
        if (!FFileHelper::LoadFileToString(Text, *Plan.CapturePath))
        {
            OutError = FString::Printf(
                TEXT("Could not read shadow capture JSON: %s"), *Plan.CapturePath);
            return false;
        }
        const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
        FString StoredCaptureId;
        if (!FJsonSerializer::Deserialize(Reader, Object) || !Object.IsValid()
            || !Object->TryGetStringField(TEXT("capture_id"), StoredCaptureId)
            || StoredCaptureId != Plan.Capture.CaptureId)
        {
            OutError = FString::Printf(
                TEXT("Shadow capture JSON identity is invalid: %s"),
                *Plan.CapturePath);
            return false;
        }
        CaptureObjects.Add(Object);
    }

    const FString ProjectName = FApp::GetProjectName();
    const FString LevelPath = EditorActor->GetWorld() != nullptr
        ? EditorActor->GetWorld()->GetPackage()->GetName()
        : FString();
    if (LevelPath.IsEmpty())
    {
        OutError = TEXT("The formal Editor level identity is unavailable.");
        return false;
    }
    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Root->SetStringField(
        TEXT("experiment_id"), ShadowOptimizationExperimentId);
    Root->SetStringField(TEXT("created_at_utc"), FDateTime::UtcNow().ToIso8601());
    Root->SetStringField(TEXT("request"), ReviewRequest);
    Root->SetObjectField(
        TEXT("target"),
        RenderMasterPerformanceEvidenceJson(
            ShadowTargetEvidence, ProjectName, LevelPath));
    Root->SetStringField(TEXT("runtime_actor_path"), ImpactRuntimeActorPath);
    Root->SetBoolField(TEXT("candidate_cast_shadow"), false);
    Root->SetNumberField(
        TEXT("requested_capture_duration_seconds"),
        ActorImpactStudyCaptures[0].Capture.RequestedDurationSeconds);
    Root->SetNumberField(TEXT("warmup_seconds_per_capture"), 1.0);
    Root->SetStringField(TEXT("schedule"), TEXT("AA_AB_BA_AB"));
    Root->SetBoolField(TEXT("runtime_state_restored"), true);
    Root->SetBoolField(TEXT("modifies_editor_scene"), false);

    TArray<TSharedPtr<FJsonValue>> Controls;
    Controls.Add(MakeShared<FJsonValueObject>(CaptureObjects[0].ToSharedRef()));
    Controls.Add(MakeShared<FJsonValueObject>(CaptureObjects[1].ToSharedRef()));
    Root->SetArrayField(TEXT("noise_control_baseline"), Controls);
    auto MakeTrial = [&CaptureObjects](
        const TCHAR* TrialId,
        const TCHAR* Order,
        int32 BaselineIndex,
        int32 CandidateIndex)
    {
        TSharedRef<FJsonObject> Trial = MakeShared<FJsonObject>();
        Trial->SetStringField(TEXT("trial_id"), TrialId);
        Trial->SetStringField(TEXT("order"), Order);
        Trial->SetObjectField(
            TEXT("baseline"), CaptureObjects[BaselineIndex].ToSharedRef());
        Trial->SetObjectField(
            TEXT("candidate"), CaptureObjects[CandidateIndex].ToSharedRef());
        return MakeShared<FJsonValueObject>(Trial);
    };
    TArray<TSharedPtr<FJsonValue>> Trials;
    Trials.Add(MakeTrial(
        TEXT("trial_001"), TEXT("baseline_then_candidate"), 2, 3));
    Trials.Add(MakeTrial(
        TEXT("trial_002"), TEXT("candidate_then_baseline"), 5, 4));
    Trials.Add(MakeTrial(
        TEXT("trial_003"), TEXT("baseline_then_candidate"), 6, 7));
    Root->SetArrayField(TEXT("trials"), Trials);

    FString JsonText;
    if (!SerializeInsightsJson(Root, JsonText)
        || !FFileHelper::SaveStringToFile(
            JsonText,
            *ShadowOptimizationCaptureSetPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        OutError = FString::Printf(
            TEXT("Could not write shadow optimization capture set: %s"),
            *ShadowOptimizationCaptureSetPath);
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

bool FRenderMasterInsightsGpuAssistant::StartActorImpactStudyReviewProcess(
    FString& OutError)
{
    const FString Python = WorkflowController->GetPythonExecutable();
    const FString ReportId = ActorImpactStudyId + TEXT("_review");
    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-actor-gpu-impact-study-review --prompt-file %s --capture-set %s --report-id %s --output %s"),
        *QuoteInsightsArgument(PromptPath),
        *QuoteInsightsArgument(ActorImpactStudyCaptureSetPath),
        *QuoteInsightsArgument(ReportId),
        *QuoteInsightsArgument(ReportOutputPath));
    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        OutError = TEXT("Could not create Actor GPU study review process pipes.");
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
        TEXT("Eight Actor GPU traces are complete and restored; deterministic study review process %u started."),
        ProcessId));
    return true;
}

bool FRenderMasterInsightsGpuAssistant::StartShadowOptimizationProcess(
    FString& OutError)
{
    const FString Python = WorkflowController->GetPythonExecutable();
    const FString ReportId = ShadowOptimizationExperimentId + TEXT("_report");
    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-shadow-optimization --capture-set %s --report-id %s --output %s"),
        *QuoteInsightsArgument(ShadowOptimizationCaptureSetPath),
        *QuoteInsightsArgument(ReportId),
        *QuoteInsightsArgument(ReportOutputPath));
    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        OutError = TEXT("Could not create shadow optimization process pipes.");
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
        TEXT("Eight Cast Shadow traces are complete and runtime state is restored; deterministic decision process %u started."),
        ProcessId));
    return true;
}

void FRenderMasterInsightsGpuAssistant::CompleteProcess()
{
    if (bShadowOptimizationStudy)
    {
        CompleteShadowOptimizationProcess();
        return;
    }
    if (bRepeatableActorImpactStudy)
    {
        CompleteActorImpactStudyProcess();
        return;
    }
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

void FRenderMasterInsightsGpuAssistant::CompleteActorImpactStudyProcess()
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
                TEXT("Actor GPU impact study review exited with code %d."),
                ReturnCode)
            : ProcessLog);
        return;
    }
    FString Error;
    if (!RenderMasterParseActorGpuImpactStudyReportFile(
            ReportOutputPath, ActorImpactStudyReport, Error))
    {
        Fail(Error);
        return;
    }
    if (ActorImpactStudyReport.StudyId != ActorImpactStudyId)
    {
        Fail(TEXT("Actor GPU study report does not match the host capture set."));
        return;
    }
    if (ActorImpactStudyReport.CaptureSetFileSha256
        != ActorImpactStudyCaptureSetFileSha256)
    {
        Fail(TEXT("Actor GPU study report does not match the capture-set file hash."));
        return;
    }
    auto HasAggregate = [this](const FString& AggregateId)
    {
        return ActorImpactStudyReport.Aggregates.ContainsByPredicate(
            [&AggregateId](const FRenderMasterActorGpuStudyAggregate& Aggregate)
            {
                return Aggregate.AggregateId == AggregateId;
            });
    };
    if (!ActorImpactStudyReport.PrimaryAggregateId.IsEmpty()
        && !HasAggregate(ActorImpactStudyReport.PrimaryAggregateId))
    {
        Fail(TEXT("Actor GPU study names an unavailable primary aggregate."));
        return;
    }
    for (const FString& CitedAggregateId : ActorImpactStudyReport.CitedAggregateIds)
    {
        if (!HasAggregate(CitedAggregateId))
        {
            Fail(TEXT("Actor GPU study cites an unavailable aggregate."));
            return;
        }
    }
    State = ActorImpactStudyReport.Status == TEXT("review_complete")
        ? ERenderMasterInsightsGpuState::Complete
        : ERenderMasterInsightsGpuState::Unresolved;
}

void FRenderMasterInsightsGpuAssistant::CompleteShadowOptimizationProcess()
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
                TEXT("Shadow optimization analysis exited with code %d."),
                ReturnCode)
            : ProcessLog);
        return;
    }
    FString Error;
    if (!RenderMasterParseShadowOptimizationReportFile(
            ReportOutputPath, ShadowOptimizationReport, Error))
    {
        Fail(Error);
        return;
    }
    if (ShadowOptimizationReport.ExperimentId
            != ShadowOptimizationExperimentId
        || ShadowOptimizationReport.CaptureSetFileSha256
            != ShadowOptimizationCaptureSetFileSha256)
    {
        Fail(TEXT(
            "Shadow optimization report does not match the host capture-set identity."));
        return;
    }
    if (!RenderMasterPerformanceEvidenceMatches(
            ShadowOptimizationReport.ProposedAction.Target,
            ShadowTargetEvidence))
    {
        Fail(TEXT(
            "Shadow optimization report target does not match the host-frozen Actor evidence."));
        return;
    }
    State = ShadowOptimizationReport.Status == TEXT("unresolved")
        ? ERenderMasterInsightsGpuState::Unresolved
        : ERenderMasterInsightsGpuState::Complete;
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
    bool bRestored = true;
    if (bImpactActorHidden)
    {
        AActor* RuntimeActor = ImpactRuntimeActor.Get();
        if (!IsValid(RuntimeActor))
        {
            OutError = TEXT(
                "The selected runtime Actor was destroyed before its hidden state could be restored.");
            bRestored = false;
        }
        else
        {
            RuntimeActor->SetActorHiddenInGame(bOriginalImpactActorHidden);
            if (RuntimeActor->IsHidden() != bOriginalImpactActorHidden)
            {
                OutError = TEXT(
                    "Unreal could not restore the selected runtime Actor hidden state.");
                bRestored = false;
            }
            else
            {
                bImpactActorHidden = false;
                AppendLog(FString::Printf(
                    TEXT("Restored %s to its original PIE/SIE hidden state."),
                    *ImpactActorLabel));
            }
        }
    }
    if (bShadowOptimizationStudy && bShadowCandidateActive)
    {
        UStaticMeshComponent* Component = ShadowRuntimeComponent.Get();
        if (!IsValid(Component))
        {
            if (!OutError.IsEmpty()) OutError += TEXT(" ");
            OutError += TEXT(
                "The runtime Static Mesh Component was destroyed before Cast Shadow restoration.");
            bRestored = false;
        }
        else
        {
            Component->SetCastShadow(bOriginalRuntimeCastShadow);
            if (Component->CastShadow != bOriginalRuntimeCastShadow)
            {
                if (!OutError.IsEmpty()) OutError += TEXT(" ");
                OutError += TEXT(
                    "Unreal could not restore the runtime Cast Shadow state.");
                bRestored = false;
            }
            else
            {
                bShadowCandidateActive = false;
                AppendLog(FString::Printf(
                    TEXT("Restored Cast Shadow for %s after the temporary PIE/SIE experiment."),
                    *ImpactActorLabel));
            }
        }
    }
    return bRestored;
}

bool FRenderMasterInsightsGpuAssistant::CanApplyShadowOptimization() const
{
    return bShadowOptimizationStudy
        && !bShadowOptimizationApplied
        && !bShadowOptimizationRejected
        && State == ERenderMasterInsightsGpuState::Complete
        && ShadowOptimizationReport.Status == TEXT("proposed")
        && ShadowOptimizationReport.bApprovalRequired
        && ShadowOptimizationReport.bHasProposedAction
        && GEditor != nullptr && GEditor->PlayWorld == nullptr
        && IsValid(ImpactEditorActor.Get())
        && IsValid(ShadowEditorComponent.Get());
}

bool FRenderMasterInsightsGpuAssistant::CanRejectShadowOptimization() const
{
    return bShadowOptimizationStudy
        && !bShadowOptimizationApplied
        && !bShadowOptimizationRejected
        && State == ERenderMasterInsightsGpuState::Complete
        && ShadowOptimizationReport.Status == TEXT("proposed");
}

bool FRenderMasterInsightsGpuAssistant::ApplyShadowOptimizationProposal()
{
    if (!CanApplyShadowOptimization()) return false;
    AStaticMeshActor* EditorActor = Cast<AStaticMeshActor>(ImpactEditorActor.Get());
    UStaticMeshComponent* EditorComponent = ShadowEditorComponent.Get();
    FString Error;
    FRenderMasterPerformanceEvidence Current;
    if (!IsValid(EditorActor) || !IsValid(EditorComponent)
        || !RenderMasterCapturePerformanceEvidence(
            EditorActor, EditorComponent, Current, Error)
        || !RenderMasterPerformanceEvidenceMatches(Current, ShadowTargetEvidence)
        || !RenderMasterPerformanceSnapshotsMatch(
            Current.Before, ShadowOptimizationReport.ProposedAction.Before))
    {
        Fail(Error.IsEmpty()
            ? TEXT("The formal Actor changed after profiling; the shadow proposal was rejected as stale.")
            : Error);
        return false;
    }
    TArray<AStaticMeshActor*> Actors = {EditorActor};
    TArray<UStaticMeshComponent*> Components = {EditorComponent};
    TArray<FRenderMasterPerformanceAction> Actions = {
        ShadowOptimizationReport.ProposedAction};
    if (!RenderMasterApplyPerformanceBatch(
            Actors, Components, Actions, Error, true))
    {
        Fail(Error.IsEmpty()
            ? TEXT("Unreal rejected the approved shadow optimization.")
            : Error);
        return false;
    }
    bShadowOptimizationApplied = true;
    AppendLog(FString::Printf(
        TEXT("Applied the profiled Cast Shadow change to %s as one Undo transaction; the level was not saved."),
        *ImpactActorLabel));
    return true;
}

void FRenderMasterInsightsGpuAssistant::RejectShadowOptimizationProposal()
{
    if (!CanRejectShadowOptimization()) return;
    bShadowOptimizationRejected = true;
    AppendLog(TEXT(
        "The measured shadow proposal was rejected. No formal Editor change was applied."));
}

void FRenderMasterInsightsGpuAssistant::Dismiss()
{
    if (IsBusy()) Cancel();
    CloseProcessResources();
    AnalysisSession.Reset();
    AnalysisService.Reset();
    State = ERenderMasterInsightsGpuState::Dismissed;
    AppendLog(bShadowOptimizationStudy
        ? TEXT("Shadow optimization dismissed. Preserved traces remain available; runtime state is restored.")
        : bRepeatableActorImpactStudy
        ? TEXT("Repeatable Actor GPU study dismissed. Preserved traces remain available.")
        : bActorImpactExperiment
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
    return !IsBusy() && !ProcessHandle.IsValid() && !bOwnsTrace
        && !CanRejectShadowOptimization();
}

bool FRenderMasterInsightsGpuAssistant::IsBusy() const
{
    return State == ERenderMasterInsightsGpuState::Capturing
        || State == ERenderMasterInsightsGpuState::WarmingVariant
        || State == ERenderMasterInsightsGpuState::WarmingStudyCapture
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
    return bShadowOptimizationStudy
        ? NSLOCTEXT(
            "RenderMasterBot",
            "ShadowOptimizationTitle",
            "Cast Shadow Optimization Experiment")
        : bRepeatableActorImpactStudy
        ? NSLOCTEXT(
            "RenderMasterBot",
            "ActorGpuImpactStudyTitle",
            "Repeatable Selected Actor GPU Study")
        : bActorImpactExperiment
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
            if (bRepeatableActorImpactStudy
                && ActorImpactStudyCaptures.IsValidIndex(
                    ActorImpactStudyCaptureIndex))
            {
                return FText::FromString(FString::Printf(
                    TEXT("Capture %d/8: %s"),
                    ActorImpactStudyCaptureIndex + 1,
                    *ActorImpactStudyCaptures[ActorImpactStudyCaptureIndex].Label));
            }
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
        case ERenderMasterInsightsGpuState::WarmingStudyCapture:
            return NSLOCTEXT(
                "RenderMasterBot",
                "ActorGpuStudyWarming",
                "Warming scheduled study state");
        case ERenderMasterInsightsGpuState::Analyzing:
            return NSLOCTEXT("RenderMasterBot", "InsightsGpuAnalyzing", "Parsing GPU timelines");
        case ERenderMasterInsightsGpuState::Reviewing:
            return bShadowOptimizationStudy
                ? NSLOCTEXT("RenderMasterBot", "ShadowOptimizationAnalyzing", "Evaluating fixed performance gates")
                : bRepeatableActorImpactStudy
                ? NSLOCTEXT("RenderMasterBot", "ActorGpuStudyReviewing", "Reviewing repeatable Actor evidence")
                : bActorImpactExperiment
                ? NSLOCTEXT("RenderMasterBot", "ActorGpuReviewing", "Reviewing measured Actor impact")
                : NSLOCTEXT("RenderMasterBot", "InsightsGpuReviewing", "Reviewing measured GPU scopes");
        case ERenderMasterInsightsGpuState::Complete:
            return bShadowOptimizationStudy
                ? FText::FromString(
                    ShadowOptimizationReport.Status == TEXT("proposed")
                        ? TEXT("Measured shadow proposal ready for approval")
                        : TEXT("Shadow candidate rejected by profiling"))
                : bRepeatableActorImpactStudy
                ? NSLOCTEXT("RenderMasterBot", "ActorGpuStudyComplete", "Repeatable Actor GPU study complete")
                : bActorImpactExperiment
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
    if (bShadowOptimizationStudy)
    {
        if (State == ERenderMasterInsightsGpuState::Capturing
            && ActorImpactStudyCaptures.IsValidIndex(ActorImpactStudyCaptureIndex))
        {
            const double Remaining = FMath::Max(
                0.0,
                Capture.RequestedDurationSeconds
                    - (FPlatformTime::Seconds() - CaptureStartedSeconds));
            return FText::FromString(FString::Printf(
                TEXT("Capture %d of 8 (%s): %.1f seconds remain for %s at %dx%d. Keep camera, effects, motion, and workload unchanged."),
                ActorImpactStudyCaptureIndex + 1,
                *ActorImpactStudyCaptures[ActorImpactStudyCaptureIndex].Label,
                Remaining,
                *ImpactActorLabel,
                Capture.ViewportSize.X,
                Capture.ViewportSize.Y));
        }
        if (State == ERenderMasterInsightsGpuState::WarmingStudyCapture
            && ActorImpactStudyCaptures.IsValidIndex(ActorImpactStudyCaptureIndex))
        {
            const double Remaining = FMath::Max(
                0.0,
                1.0 - (FPlatformTime::Seconds() - VariantWarmupStartedSeconds));
            return FText::FromString(FString::Printf(
                TEXT("Preparing capture %d of 8 (%s). %.1f seconds of fixed warmup remain; Cast Shadow is %s only on the PIE/SIE component."),
                ActorImpactStudyCaptureIndex + 1,
                *ActorImpactStudyCaptures[ActorImpactStudyCaptureIndex].Label,
                Remaining,
                bShadowCandidateActive ? TEXT("disabled") : TEXT("enabled")));
        }
        if (State == ERenderMasterInsightsGpuState::Analyzing)
        {
            return FText::FromString(FString::Printf(
                TEXT("Unreal TraceServices is parsing shadow capture %d of 8. Completed traces remain preserved and the fixed AA_AB_BA_AB schedule is enforced."),
                ActorImpactStudyCaptureIndex + 1));
        }
        if (State == ERenderMasterInsightsGpuState::Reviewing)
        {
            return FText::FromString(FString::Printf(
                TEXT("All 8 traces are preserved and Cast Shadow is restored for %s. The deterministic host is recomputing common scopes, A/A noise, paired medians, MAD, direction votes, and fixed decision thresholds. No model decides the performance result."),
                *ImpactActorLabel));
        }
        FString Evidence;
        if (!ShadowOptimizationReport.Aggregates.IsEmpty())
        {
            Evidence = FString::Printf(
                TEXT("\n\nMeasured evidence for %s: 2 Cast-Shadow-on controls | 3 counterbalanced on/off trials | %d scopes common to all 8 traces\nLargest paired median changes:"),
                *ImpactActorLabel,
                ShadowOptimizationReport.Aggregates.Num());
            const int32 VisibleAggregates = FMath::Min(
                5, ShadowOptimizationReport.Aggregates.Num());
            for (int32 Index = 0; Index < VisibleAggregates; ++Index)
            {
                const FRenderMasterShadowOptimizationAggregate& Aggregate =
                    ShadowOptimizationReport.Aggregates[Index];
                Evidence += FString::Printf(
                    TEXT("\n%s  %s [%s] - median %+.3f ms/s | MAD %.3f | A/A noise %.3f | signal excess %.3f | reduction %+.2f%% | votes %d down / %d up / %d same | %s"),
                    *Aggregate.AggregateId,
                    *Aggregate.ScopeName,
                    *Aggregate.QueueName,
                    Aggregate.MedianDeltaMsPerSecond,
                    Aggregate.DeltaMadMsPerSecond,
                    Aggregate.ControlNoiseMsPerSecond,
                    Aggregate.SignalExcessMsPerSecond,
                    Aggregate.RelativeReductionPercent,
                    Aggregate.DecreasedVotes,
                    Aggregate.IncreasedVotes,
                    Aggregate.UnchangedVotes,
                    *Aggregate.Classification);
            }
        }
        if (State == ERenderMasterInsightsGpuState::Complete)
        {
            if (bShadowOptimizationApplied)
            {
                return FText::FromString(
                    ShadowOptimizationReport.Summary + Evidence
                    + TEXT("\n\nThe approved Cast Shadow change was applied to the formal Actor as one Undo transaction. The level was not saved; one Ctrl+Z restores the original setting."));
            }
            if (bShadowOptimizationRejected)
            {
                return FText::FromString(
                    ShadowOptimizationReport.Summary + Evidence
                    + TEXT("\n\nThe proposal was rejected. Runtime state is restored and no formal level property changed."));
            }
            if (ShadowOptimizationReport.Status == TEXT("proposed"))
            {
                const FString ApprovalInstruction =
                    GEditor != nullptr && GEditor->PlayWorld != nullptr
                    ? TEXT("Stop PIE/SIE, inspect the visual consequence in the Editor viewport, then use Approve & Apply Shadow Optimization. The formal Actor is revalidated before one Undo transaction.")
                    : TEXT("Inspect the visual consequence in the Editor viewport, then use Approve & Apply Shadow Optimization. The formal Actor is revalidated before one Undo transaction.");
                return FText::FromString(
                    ShadowOptimizationReport.Summary + Evidence
                    + TEXT("\n\nPrimary qualifying aggregate: ")
                    + ShadowOptimizationReport.PrimaryAggregateId
                    + TEXT("\n\n") + ApprovalInstruction
                    + TEXT(" Images are not used to award the performance win; operator inspection is only a visual regression gate."));
            }
            return FText::FromString(
                ShadowOptimizationReport.Summary + Evidence
                + TEXT("\n\nThe candidate was rolled back automatically in PIE/SIE. No approval action is available and the formal level remains unchanged."));
        }
        if (State == ERenderMasterInsightsGpuState::Unresolved)
        {
            return FText::FromString(
                ShadowOptimizationReport.Summary + Evidence
                + TEXT("\n\nThe temporary candidate was restored and no formal change is available."));
        }
        if (State == ERenderMasterInsightsGpuState::Failed)
            return FText::FromString(ErrorText + Evidence);
        if (State == ERenderMasterInsightsGpuState::Dismissed)
        {
            return NSLOCTEXT(
                "RenderMasterBot",
                "ShadowOptimizationDismissedSummary",
                "The shadow optimization experiment was dismissed. Completed traces remain preserved, runtime Cast Shadow was restored, and no formal level change was applied.");
        }
    }
    if (bRepeatableActorImpactStudy)
    {
        if (State == ERenderMasterInsightsGpuState::Capturing
            && ActorImpactStudyCaptures.IsValidIndex(ActorImpactStudyCaptureIndex))
        {
            const double Remaining = FMath::Max(
                0.0,
                Capture.RequestedDurationSeconds
                    - (FPlatformTime::Seconds() - CaptureStartedSeconds));
            return FText::FromString(FString::Printf(
                TEXT("Capture %d of 8 (%s): %.1f seconds remain for %s at %dx%d. Keep camera, effects, motion, and workload unchanged."),
                ActorImpactStudyCaptureIndex + 1,
                *ActorImpactStudyCaptures[ActorImpactStudyCaptureIndex].Label,
                Remaining,
                *ImpactActorLabel,
                Capture.ViewportSize.X,
                Capture.ViewportSize.Y));
        }
        if (State == ERenderMasterInsightsGpuState::WarmingStudyCapture
            && ActorImpactStudyCaptures.IsValidIndex(ActorImpactStudyCaptureIndex))
        {
            const double Remaining = FMath::Max(
                0.0,
                1.0 - (FPlatformTime::Seconds() - VariantWarmupStartedSeconds));
            return FText::FromString(FString::Printf(
                TEXT("Preparing capture %d of 8 (%s). %.1f seconds of fixed warmup remain; the Actor is %s only in PIE/SIE."),
                ActorImpactStudyCaptureIndex + 1,
                *ActorImpactStudyCaptures[ActorImpactStudyCaptureIndex].Label,
                Remaining,
                bImpactActorHidden ? TEXT("hidden") : TEXT("visible")));
        }
        if (State == ERenderMasterInsightsGpuState::Analyzing)
        {
            return FText::FromString(FString::Printf(
                TEXT("Unreal TraceServices is parsing capture %d of 8. Completed traces remain preserved; the runtime Actor follows the fixed AA_AB_BA_AB schedule."),
                ActorImpactStudyCaptureIndex + 1));
        }
        if (State == ERenderMasterInsightsGpuState::Reviewing)
        {
            return FText::FromString(FString::Printf(
                TEXT("All 8 traces are preserved and %s is restored. The deterministic host is intersecting common scopes, computing A/A noise, three paired deltas, median, MAD, and direction votes before model interpretation."),
                *ImpactActorLabel));
        }
        FString Evidence;
        if (!ActorImpactStudyReport.Aggregates.IsEmpty())
        {
            Evidence = FString::Printf(
                TEXT("\n\nRepeatable evidence for %s: 2 visible control captures | 3 counterbalanced trials | %d scopes common to all 8 traces\nLargest paired median changes:"),
                *ImpactActorLabel,
                ActorImpactStudyReport.Aggregates.Num());
            const int32 VisibleAggregates = FMath::Min(
                5, ActorImpactStudyReport.Aggregates.Num());
            for (int32 Index = 0; Index < VisibleAggregates; ++Index)
            {
                const FRenderMasterActorGpuStudyAggregate& Aggregate =
                    ActorImpactStudyReport.Aggregates[Index];
                Evidence += FString::Printf(
                    TEXT("\n%s  %s [%s] — median %+.3f ms/s | MAD %.3f | A/A noise %.3f | votes %d↓ %d↑ %d= | %s"),
                    *Aggregate.AggregateId,
                    *Aggregate.ScopeName,
                    *Aggregate.QueueName,
                    Aggregate.MedianDeltaMsPerSecond,
                    Aggregate.DeltaMadMsPerSecond,
                    Aggregate.ControlNoiseMsPerSecond,
                    Aggregate.DecreasedVotes,
                    Aggregate.IncreasedVotes,
                    Aggregate.UnchangedVotes,
                    *Aggregate.Classification);
            }
        }
        if (State == ERenderMasterInsightsGpuState::Complete)
        {
            FString Text = ActorImpactStudyReport.Summary + Evidence;
            if (!ActorImpactStudyReport.PrimaryAggregateId.IsEmpty())
                Text += TEXT("\n\nPrimary measured aggregate: ")
                    + ActorImpactStudyReport.PrimaryAggregateId;
            if (!ActorImpactStudyReport.Findings.IsEmpty())
            {
                Text += TEXT("\n\nFindings:\n");
                for (int32 Index = 0;
                    Index < ActorImpactStudyReport.Findings.Num();
                    ++Index)
                {
                    Text += FString::Printf(
                        TEXT("%d. %s%s"),
                        Index + 1,
                        *ActorImpactStudyReport.Findings[Index],
                        Index + 1 < ActorImpactStudyReport.Findings.Num()
                            ? TEXT("\n") : TEXT(""));
                }
            }
            Text += TEXT(
                "\n\nA stable candidate exceeds the single A/A control difference and agrees in at least two of three paired trials. It remains association evidence, not direct pass/draw/material/shader causation. The runtime Actor was restored; no Editor scene change was made.");
            return FText::FromString(Text);
        }
        if (State == ERenderMasterInsightsGpuState::Unresolved)
        {
            return FText::FromString(
                ActorImpactStudyReport.Summary + Evidence
                + TEXT("\n\nRequired next evidence: ")
                + ActorImpactStudyReport.MissingCapabilities);
        }
        if (State == ERenderMasterInsightsGpuState::Failed)
            return FText::FromString(ErrorText + Evidence);
        if (State == ERenderMasterInsightsGpuState::Dismissed)
        {
            return NSLOCTEXT(
                "RenderMasterBot",
                "ActorGpuStudyDismissedSummary",
                "The repeatable Actor GPU study was dismissed. All completed traces remain in the workflow folder, and the runtime Actor is restored.");
        }
    }
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
        case ERenderMasterInsightsGpuState::WarmingStudyCapture:
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
