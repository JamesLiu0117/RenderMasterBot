#include "RenderMasterLevelAuditAssistant.h"

#include "RenderMasterWorkflowController.h"

#include "Editor.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

namespace
{
bool ReadObject(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field,
    TSharedPtr<FJsonObject>& Out)
{
    const TSharedPtr<FJsonObject>* Value = nullptr;
    if (!Parent.IsValid() || !Parent->TryGetObjectField(Field, Value)
        || Value == nullptr || !Value->IsValid())
    {
        return false;
    }
    Out = *Value;
    return true;
}

bool SerializeJsonObject(const TSharedRef<FJsonObject>& Json, FString& Out)
{
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
    return FJsonSerializer::Serialize(Json, Writer);
}

FString QuoteArgument(const FString& Value)
{
    return FString::Printf(
        TEXT("\"%s\""), *Value.Replace(TEXT("\""), TEXT("\\\"")));
}

bool IsSha256(const FString& Value)
{
    if (Value.Len() != 64) return false;
    for (const TCHAR Character : Value)
    {
        if (!FChar::IsHexDigit(Character) || FChar::IsUpper(Character))
            return false;
    }
    return true;
}

bool ParseInteger(
    const TSharedPtr<FJsonObject>& Json,
    const TCHAR* Field,
    int64 Minimum,
    int64 Maximum,
    int64& Out)
{
    double Value = 0.0;
    if (!Json.IsValid() || !Json->TryGetNumberField(Field, Value)
        || !FMath::IsFinite(Value)
        || !FMath::IsNearlyEqual(Value, FMath::RoundToDouble(Value))
        || Value < static_cast<double>(Minimum)
        || Value > static_cast<double>(Maximum))
    {
        return false;
    }
    Out = static_cast<int64>(Value);
    return true;
}

FString RiskBand(const int32 Score)
{
    if (Score >= 75) return TEXT("critical");
    if (Score >= 50) return TEXT("high");
    if (Score >= 25) return TEXT("medium");
    return TEXT("low");
}

FString RecommendedTest(const int32 Score)
{
    if (Score >= 50) return TEXT("repeatable_actor_gpu_study");
    if (Score >= 25) return TEXT("selected_mesh_review");
    return TEXT("none");
}

FRenderMasterLevelAuditStatistics ComputeStatistics(
    const TArray<FRenderMasterPerformanceEvidence>& Actors,
    const int32 ExcludedActorCount)
{
    FRenderMasterLevelAuditStatistics Result;
    Result.CapturedActorCount = Actors.Num();
    Result.ExcludedActorCount = ExcludedActorCount;
    for (const FRenderMasterPerformanceEvidence& Actor : Actors)
    {
        Result.TotalLod0Triangles += Actor.Lod0Triangles;
        Result.TotalMaterialSlots += Actor.MaterialSlotCount;
        Result.ActorsWithoutNaniteCount += Actor.bNaniteEnabled ? 0 : 1;
        Result.SingleLodActorCount += Actor.LodCount == 1 ? 1 : 0;
        Result.TickingComponentCount += Actor.bComponentTickEnabled ? 1 : 0;
        Result.ShadowCastingActorCount += Actor.Before.bCastShadow ? 1 : 0;
        Result.UnboundedDrawDistanceCount +=
            FMath::IsNearlyZero(Actor.Before.MaxDrawDistanceCm, 0.01f) ? 1 : 0;
        Result.QueryAndPhysicsCollisionCount +=
            Actor.CollisionMode == TEXT("query_and_physics") ? 1 : 0;
        Result.HighPriorityActorCount +=
            RenderMasterComputeLevelAuditRiskScore(Actor) >= 50 ? 1 : 0;
    }
    return Result;
}

bool StatisticsMatch(
    const FRenderMasterLevelAuditStatistics& A,
    const FRenderMasterLevelAuditStatistics& B)
{
    return A.CapturedActorCount == B.CapturedActorCount
        && A.ExcludedActorCount == B.ExcludedActorCount
        && A.TotalLod0Triangles == B.TotalLod0Triangles
        && A.TotalMaterialSlots == B.TotalMaterialSlots
        && A.ActorsWithoutNaniteCount == B.ActorsWithoutNaniteCount
        && A.SingleLodActorCount == B.SingleLodActorCount
        && A.TickingComponentCount == B.TickingComponentCount
        && A.ShadowCastingActorCount == B.ShadowCastingActorCount
        && A.UnboundedDrawDistanceCount == B.UnboundedDrawDistanceCount
        && A.QueryAndPhysicsCollisionCount == B.QueryAndPhysicsCollisionCount
        && A.HighPriorityActorCount == B.HighPriorityActorCount;
}

bool ParseStatistics(
    const TSharedPtr<FJsonObject>& Json,
    FRenderMasterLevelAuditStatistics& Out)
{
    int64 Captured = 0;
    int64 Excluded = 0;
    int64 TotalTriangles = 0;
    int64 TotalMaterials = 0;
    int64 WithoutNanite = 0;
    int64 SingleLod = 0;
    int64 Ticking = 0;
    int64 Shadows = 0;
    int64 Unbounded = 0;
    int64 Collision = 0;
    int64 HighPriority = 0;
    if (!ParseInteger(Json, TEXT("captured_actor_count"), 1, 4096, Captured)
        || !ParseInteger(Json, TEXT("excluded_actor_count"), 0, 100000, Excluded)
        || !ParseInteger(Json, TEXT("total_lod0_triangles"), 0, 1000000000000000LL, TotalTriangles)
        || !ParseInteger(Json, TEXT("total_material_slots"), 0, 1000000000LL, TotalMaterials)
        || !ParseInteger(Json, TEXT("actors_without_nanite_count"), 0, 4096, WithoutNanite)
        || !ParseInteger(Json, TEXT("single_lod_actor_count"), 0, 4096, SingleLod)
        || !ParseInteger(Json, TEXT("ticking_component_count"), 0, 4096, Ticking)
        || !ParseInteger(Json, TEXT("shadow_casting_actor_count"), 0, 4096, Shadows)
        || !ParseInteger(Json, TEXT("unbounded_draw_distance_count"), 0, 4096, Unbounded)
        || !ParseInteger(Json, TEXT("query_and_physics_collision_count"), 0, 4096, Collision)
        || !ParseInteger(Json, TEXT("high_priority_actor_count"), 0, 4096, HighPriority))
    {
        return false;
    }
    Out.CapturedActorCount = static_cast<int32>(Captured);
    Out.ExcludedActorCount = static_cast<int32>(Excluded);
    Out.TotalLod0Triangles = TotalTriangles;
    Out.TotalMaterialSlots = TotalMaterials;
    Out.ActorsWithoutNaniteCount = static_cast<int32>(WithoutNanite);
    Out.SingleLodActorCount = static_cast<int32>(SingleLod);
    Out.TickingComponentCount = static_cast<int32>(Ticking);
    Out.ShadowCastingActorCount = static_cast<int32>(Shadows);
    Out.UnboundedDrawDistanceCount = static_cast<int32>(Unbounded);
    Out.QueryAndPhysicsCollisionCount = static_cast<int32>(Collision);
    Out.HighPriorityActorCount = static_cast<int32>(HighPriority);
    return true;
}

void ReadMissingCapabilities(
    const TSharedPtr<FJsonObject>& Root,
    FString& Out)
{
    const TArray<TSharedPtr<FJsonValue>>* Missing = nullptr;
    if (!Root.IsValid()
        || !Root->TryGetArrayField(TEXT("missing_capabilities"), Missing)
        || Missing == nullptr)
    {
        return;
    }
    TArray<FString> Values;
    for (const TSharedPtr<FJsonValue>& Value : *Missing)
    {
        FString Item;
        if (Value.IsValid() && Value->TryGetString(Item) && !Item.IsEmpty())
            Values.Add(Item);
    }
    Out = FString::Join(Values, TEXT(", "));
}
}

int32 RenderMasterComputeLevelAuditRiskScore(
    const FRenderMasterPerformanceEvidence& Evidence)
{
    int32 Score = 0;
    if (Evidence.Lod0Triangles >= 2000000) Score += 30;
    else if (Evidence.Lod0Triangles >= 500000) Score += 20;
    else if (Evidence.Lod0Triangles >= 100000) Score += 10;

    if (!Evidence.bNaniteEnabled && Evidence.Lod0Triangles >= 500000)
        Score += 25;
    else if (!Evidence.bNaniteEnabled && Evidence.Lod0Triangles >= 100000)
        Score += 15;

    if (Evidence.LodCount == 1 && Evidence.Lod0Triangles >= 100000)
        Score += 20;
    else if (Evidence.LodCount == 1)
        Score += 5;

    if (Evidence.MaterialSlotCount >= 8) Score += 12;
    else if (Evidence.MaterialSlotCount >= 4) Score += 6;
    if (Evidence.bComponentTickEnabled) Score += 15;
    if (Evidence.CollisionMode == TEXT("query_and_physics")) Score += 5;
    if (Evidence.Before.bCastShadow && Evidence.BoundsRadiusCm >= 5000.0f)
        Score += 10;
    if (FMath::IsNearlyZero(Evidence.Before.MaxDrawDistanceCm, 0.01f)
        && Evidence.BoundsRadiusCm <= 500.0f)
    {
        Score += 5;
    }
    return FMath::Min(Score, 100);
}

bool RenderMasterParseLevelAuditReportFile(
    const FString& Filename,
    FRenderMasterLevelAuditReport& OutReport,
    FString& OutError)
{
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *Filename))
    {
        OutError = FString::Printf(TEXT("Could not read level audit report: %s"), *Filename);
        return false;
    }
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        OutError = TEXT("Level audit report is not valid JSON.");
        return false;
    }

    FRenderMasterLevelAuditReport Parsed;
    FString SchemaVersion;
    bool bModifiesScene = false;
    bool bAutoSave = true;
    bool bUndoSupported = false;
    if (!Root->TryGetStringField(TEXT("schema_version"), SchemaVersion)
        || SchemaVersion != TEXT("0.1")
        || !Root->TryGetStringField(TEXT("audit_id"), Parsed.AuditId)
        || !Root->TryGetStringField(TEXT("status"), Parsed.Status)
        || !Root->TryGetStringField(TEXT("request"), Parsed.Request)
        || !Root->TryGetStringField(TEXT("context_sha256"), Parsed.ContextSha256)
        || !Root->TryGetStringField(TEXT("summary"), Parsed.Summary)
        || !Root->TryGetBoolField(TEXT("modifies_editor_scene"), bModifiesScene)
        || !Root->TryGetBoolField(TEXT("auto_save"), bAutoSave)
        || !Root->TryGetBoolField(TEXT("undo_supported"), bUndoSupported)
        || Parsed.AuditId.IsEmpty() || Parsed.Request.IsEmpty()
        || Parsed.Summary.IsEmpty() || !IsSha256(Parsed.ContextSha256)
        || bAutoSave || !bUndoSupported)
    {
        OutError = TEXT("Level audit metadata or safety flags are invalid.");
        return false;
    }
    Parsed.Status.ToLowerInline();
    Root->TryGetStringField(
        TEXT("primary_assessment_id"), Parsed.PrimaryAssessmentId);
    ReadMissingCapabilities(Root, Parsed.MissingCapabilities);

    TSharedPtr<FJsonObject> Context;
    FString ContextAuditId;
    FString ProjectName;
    FString LevelPath;
    int64 StaticMeshActorCount = 0;
    int64 ExcludedActorCount = 0;
    const TArray<TSharedPtr<FJsonValue>>* ActorValues = nullptr;
    if (!ReadObject(Root, TEXT("context"), Context)
        || !Context->TryGetStringField(TEXT("audit_id"), ContextAuditId)
        || ContextAuditId != Parsed.AuditId
        || !Context->TryGetStringField(TEXT("project_name"), ProjectName)
        || !Context->TryGetStringField(TEXT("level_path"), LevelPath)
        || !ParseInteger(Context, TEXT("static_mesh_actor_count"), 1, 100000, StaticMeshActorCount)
        || !ParseInteger(Context, TEXT("excluded_actor_count"), 0, 100000, ExcludedActorCount)
        || !Context->TryGetArrayField(TEXT("actors"), ActorValues)
        || ActorValues == nullptr || ActorValues->IsEmpty()
        || ActorValues->Num() > 4096
        || StaticMeshActorCount != ActorValues->Num() + ExcludedActorCount)
    {
        OutError = TEXT("Level audit context metadata or Actor counts are invalid.");
        return false;
    }
    TSet<FString> ActorPaths;
    TSet<FString> ActorGuids;
    for (const TSharedPtr<FJsonValue>& Value : *ActorValues)
    {
        const TSharedPtr<FJsonObject>* ActorJson = nullptr;
        FRenderMasterPerformanceEvidence Evidence;
        if (!Value.IsValid() || !Value->TryGetObject(ActorJson)
            || ActorJson == nullptr
            || !RenderMasterParsePerformanceEvidenceJson(
                *ActorJson, ProjectName, LevelPath, Evidence, OutError)
            || ActorPaths.Contains(Evidence.ActorPath)
            || (!Evidence.ActorGuid.IsEmpty()
                && ActorGuids.Contains(Evidence.ActorGuid)))
        {
            if (OutError.IsEmpty())
                OutError = TEXT("Level audit context repeats or contains invalid Actor evidence.");
            return false;
        }
        ActorPaths.Add(Evidence.ActorPath);
        if (!Evidence.ActorGuid.IsEmpty()) ActorGuids.Add(Evidence.ActorGuid);
        Parsed.ContextActors.Add(MoveTemp(Evidence));
    }

    TSharedPtr<FJsonObject> StatisticsJson;
    if (!ReadObject(Root, TEXT("statistics"), StatisticsJson)
        || !ParseStatistics(StatisticsJson, Parsed.Statistics)
        || !StatisticsMatch(
            Parsed.Statistics,
            ComputeStatistics(
                Parsed.ContextActors, static_cast<int32>(ExcludedActorCount))))
    {
        OutError = TEXT("Level audit statistics do not match the embedded Actor evidence.");
        return false;
    }

    TArray<int32> ExpectedOrder;
    for (int32 Index = 0; Index < Parsed.ContextActors.Num(); ++Index)
        ExpectedOrder.Add(Index);
    ExpectedOrder.Sort(
        [&Parsed](const int32 A, const int32 B)
        {
            const int32 ScoreA = RenderMasterComputeLevelAuditRiskScore(
                Parsed.ContextActors[A]);
            const int32 ScoreB = RenderMasterComputeLevelAuditRiskScore(
                Parsed.ContextActors[B]);
            if (ScoreA != ScoreB) return ScoreA > ScoreB;
            return Parsed.ContextActors[A].ActorPath.Compare(
                Parsed.ContextActors[B].ActorPath,
                ESearchCase::IgnoreCase) < 0;
        });

    const TArray<TSharedPtr<FJsonValue>>* AssessmentValues = nullptr;
    if (!Root->TryGetArrayField(TEXT("assessments"), AssessmentValues)
        || AssessmentValues == nullptr
        || AssessmentValues->Num() != Parsed.ContextActors.Num())
    {
        OutError = TEXT("Level audit assessments do not cover the captured context.");
        return false;
    }
    TSet<FString> AssessmentIds;
    for (int32 Index = 0; Index < AssessmentValues->Num(); ++Index)
    {
        const TSharedPtr<FJsonObject>* AssessmentJson = nullptr;
        FRenderMasterLevelAuditAssessment Assessment;
        int64 RiskScore = 0;
        const TArray<TSharedPtr<FJsonValue>>* EvidenceFields = nullptr;
        const TArray<TSharedPtr<FJsonValue>>* Reasons = nullptr;
        const FRenderMasterPerformanceEvidence& Expected =
            Parsed.ContextActors[ExpectedOrder[Index]];
        const int32 ExpectedScore = RenderMasterComputeLevelAuditRiskScore(Expected);
        const FString ExpectedId = FString::Printf(TEXT("audit_actor_%04d"), Index);
        if (!(*AssessmentValues)[Index].IsValid()
            || !(*AssessmentValues)[Index]->TryGetObject(AssessmentJson)
            || AssessmentJson == nullptr
            || !(*AssessmentJson)->TryGetStringField(
                TEXT("assessment_id"), Assessment.AssessmentId)
            || Assessment.AssessmentId != ExpectedId
            || !(*AssessmentJson)->TryGetStringField(
                TEXT("actor_path"), Assessment.ActorPath)
            || Assessment.ActorPath != Expected.ActorPath
            || !ParseInteger(*AssessmentJson, TEXT("risk_score"), 0, 100, RiskScore)
            || RiskScore != ExpectedScore
            || !(*AssessmentJson)->TryGetStringField(
                TEXT("risk_band"), Assessment.RiskBand)
            || Assessment.RiskBand != RiskBand(ExpectedScore)
            || !(*AssessmentJson)->TryGetStringField(
                TEXT("recommended_next_test"), Assessment.RecommendedNextTest)
            || Assessment.RecommendedNextTest != RecommendedTest(ExpectedScore)
            || !(*AssessmentJson)->TryGetArrayField(
                TEXT("evidence_fields"), EvidenceFields)
            || EvidenceFields == nullptr || EvidenceFields->Num() > 10
            || !(*AssessmentJson)->TryGetArrayField(TEXT("reasons"), Reasons)
            || Reasons == nullptr || Reasons->Num() > 10
            || AssessmentIds.Contains(Assessment.AssessmentId))
        {
            OutError = TEXT("Level audit assessment order or deterministic score is invalid.");
            return false;
        }
        TSet<FString> SeenFields;
        for (const TSharedPtr<FJsonValue>& Value : *EvidenceFields)
        {
            FString Field;
            if (!Value.IsValid() || !Value->TryGetString(Field)
                || Field.IsEmpty() || SeenFields.Contains(Field))
            {
                OutError = TEXT("Level audit assessment evidence fields are invalid.");
                return false;
            }
            SeenFields.Add(Field);
        }
        for (const TSharedPtr<FJsonValue>& Value : *Reasons)
        {
            FString Reason;
            if (!Value.IsValid() || !Value->TryGetString(Reason)
                || Reason.IsEmpty())
            {
                OutError = TEXT("Level audit assessment reasons are invalid.");
                return false;
            }
            Assessment.Reasons.Add(Reason);
        }
        Assessment.ActorName = Expected.ActorName;
        Assessment.RiskScore = ExpectedScore;
        AssessmentIds.Add(Assessment.AssessmentId);
        Parsed.Assessments.Add(MoveTemp(Assessment));
    }
    if (!Parsed.PrimaryAssessmentId.IsEmpty()
        && !AssessmentIds.Contains(Parsed.PrimaryAssessmentId))
    {
        OutError = TEXT("Primary level audit assessment is unavailable.");
        return false;
    }

    const TSet<FString> AllowedSeverities = {
        TEXT("info"), TEXT("warning"), TEXT("critical")};
    const TSet<FString> AllowedCategories = {
        TEXT("level_overview"), TEXT("geometry"), TEXT("materials"),
        TEXT("nanite"), TEXT("collision"), TEXT("tick"),
        TEXT("shadows"), TEXT("culling"), TEXT("measurement")};
    const TArray<TSharedPtr<FJsonValue>>* FindingValues = nullptr;
    if (!Root->TryGetArrayField(TEXT("findings"), FindingValues)
        || FindingValues == nullptr || FindingValues->Num() > 64)
    {
        OutError = TEXT("Level audit findings array is invalid.");
        return false;
    }
    for (const TSharedPtr<FJsonValue>& Value : *FindingValues)
    {
        const TSharedPtr<FJsonObject>* Finding = nullptr;
        FString Severity;
        FString Category;
        FString Observation;
        FString Recommendation;
        const TArray<TSharedPtr<FJsonValue>>* CitationValues = nullptr;
        if (!Value.IsValid() || !Value->TryGetObject(Finding) || Finding == nullptr
            || !(*Finding)->TryGetStringField(TEXT("severity"), Severity)
            || !(*Finding)->TryGetStringField(TEXT("category"), Category)
            || !AllowedSeverities.Contains(Severity)
            || !AllowedCategories.Contains(Category)
            || !(*Finding)->TryGetStringField(TEXT("observation"), Observation)
            || !(*Finding)->TryGetStringField(TEXT("recommendation"), Recommendation)
            || Observation.IsEmpty() || Recommendation.IsEmpty()
            || !(*Finding)->TryGetArrayField(
                TEXT("evidence_assessment_ids"), CitationValues)
            || CitationValues == nullptr || CitationValues->IsEmpty()
            || CitationValues->Num() > 12)
        {
            OutError = TEXT("Level audit contains an invalid finding.");
            return false;
        }
        TSet<FString> Citations;
        for (const TSharedPtr<FJsonValue>& CitationValue : *CitationValues)
        {
            FString Citation;
            if (!CitationValue.IsValid() || !CitationValue->TryGetString(Citation)
                || !AssessmentIds.Contains(Citation)
                || Citations.Contains(Citation))
            {
                OutError = TEXT("Level audit finding cites an unavailable assessment.");
                return false;
            }
            Citations.Add(Citation);
        }
        Parsed.Findings.Add(FString::Printf(
            TEXT("[%s / %s] %s\nEvidence: %s\nRecommendation: %s"),
            *Severity,
            *Category,
            *Observation,
            *FString::Join(Citations.Array(), TEXT(", ")),
            *Recommendation));
    }

    const TArray<TSharedPtr<FJsonValue>>* ActionValues = nullptr;
    if (!Root->TryGetArrayField(TEXT("actions"), ActionValues)
        || ActionValues == nullptr || ActionValues->Num() > 32)
    {
        OutError = TEXT("Level audit actions array is invalid.");
        return false;
    }
    TSet<FString> ActionAssessmentIds;
    for (const TSharedPtr<FJsonValue>& Value : *ActionValues)
    {
        const TSharedPtr<FJsonObject>* ActionJson = nullptr;
        FRenderMasterLevelAuditAction Action;
        if (!Value.IsValid() || !Value->TryGetObject(ActionJson)
            || ActionJson == nullptr
            || !(*ActionJson)->TryGetStringField(
                TEXT("assessment_id"), Action.AssessmentId)
            || !AssessmentIds.Contains(Action.AssessmentId)
            || ActionAssessmentIds.Contains(Action.AssessmentId))
        {
            OutError = TEXT("Level audit action references an invalid assessment.");
            return false;
        }
        const FRenderMasterLevelAuditAssessment* Assessment =
            Parsed.Assessments.FindByPredicate(
                [&Action](const FRenderMasterLevelAuditAssessment& Item)
                {
                    return Item.AssessmentId == Action.AssessmentId;
                });
        const FRenderMasterPerformanceEvidence* Expected =
            Assessment == nullptr
            ? nullptr
            : Parsed.ContextActors.FindByPredicate(
                [Assessment](const FRenderMasterPerformanceEvidence& Item)
                {
                    return Item.ActorPath == Assessment->ActorPath;
                });
        if (Expected == nullptr
            || !RenderMasterParsePerformanceActionJson(
                *ActionJson,
                ProjectName,
                LevelPath,
                *Expected,
                Action.PerformanceAction,
                OutError)
            || RenderMasterPerformanceSnapshotsMatch(
                Action.PerformanceAction.Before,
                Action.PerformanceAction.After))
        {
            if (OutError.IsEmpty())
                OutError = TEXT("Level audit action does not contain a safe observable change.");
            return false;
        }
        ActionAssessmentIds.Add(Action.AssessmentId);
        Parsed.Actions.Add(MoveTemp(Action));
    }

    if (Parsed.Status == TEXT("proposed"))
    {
        if (!bModifiesScene || Parsed.Actions.IsEmpty()
            || !Parsed.MissingCapabilities.IsEmpty())
        {
            OutError = TEXT("Proposed level audits require safe actions and no capability gaps.");
            return false;
        }
    }
    else if (Parsed.Status == TEXT("review_only"))
    {
        if (bModifiesScene || !Parsed.Actions.IsEmpty()
            || !Parsed.MissingCapabilities.IsEmpty())
        {
            OutError = TEXT("Review-only level audits cannot modify the scene.");
            return false;
        }
    }
    else if (Parsed.Status == TEXT("unresolved"))
    {
        if (bModifiesScene || !Parsed.PrimaryAssessmentId.IsEmpty()
            || !Parsed.Findings.IsEmpty() || !Parsed.Actions.IsEmpty()
            || Parsed.MissingCapabilities.IsEmpty())
        {
            OutError = TEXT("Unresolved level audits contain only capability gaps.");
            return false;
        }
    }
    else
    {
        OutError = TEXT("Level audit status is unsupported.");
        return false;
    }
    OutReport = MoveTemp(Parsed);
    return true;
}

FRenderMasterLevelAuditAssistant::FRenderMasterLevelAuditAssistant(
    TSharedPtr<FRenderMasterWorkflowController> InWorkflowController)
    : WorkflowController(MoveTemp(InWorkflowController))
{
}

FRenderMasterLevelAuditAssistant::~FRenderMasterLevelAuditAssistant()
{
    Shutdown();
}

void FRenderMasterLevelAuditAssistant::Initialize()
{
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateSP(
            AsShared(), &FRenderMasterLevelAuditAssistant::Tick),
        0.2f);
}

void FRenderMasterLevelAuditAssistant::Shutdown()
{
    if (TickHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
        TickHandle.Reset();
    }
    if (ProcessHandle.IsValid())
        FPlatformProcess::TerminateProc(ProcessHandle, true);
    CloseProcessResources();
}

bool FRenderMasterLevelAuditAssistant::StartAudit(const FString& Prompt)
{
    if (!CanStart()) return false;
    const FString CleanPrompt = Prompt.TrimStartAndEnd().IsEmpty()
        ? TEXT("Audit this complete level, rank static performance risks, and recommend bounded next measurements without claiming measured runtime cost.")
        : Prompt.TrimStartAndEnd();
    if (!WorkflowController.IsValid())
    {
        Fail(TEXT("RenderMasterBot runtime configuration is unavailable."));
        return false;
    }
    const FString Python = WorkflowController->GetPythonExecutable();
    const FString Root = WorkflowController->GetWorkflowRoot();
    if (!FPaths::FileExists(Python) || Root.IsEmpty())
    {
        Fail(TEXT("Configure an existing Python executable and workflow root in Render & Evaluate."));
        return false;
    }
    if (Python.Contains(TEXT("\"")) || Root.Contains(TEXT("\"")))
    {
        Fail(TEXT("Assistant runtime paths cannot contain a double quote."));
        return false;
    }

    ExpectedAuditId = FString::Printf(
        TEXT("level_audit_%s"),
        *FDateTime::Now().ToString(TEXT("%Y%m%d_%H%M%S_%s")));
    const FString RequestDirectory = FPaths::Combine(
        Root, TEXT("assistant-level-audit"), ExpectedAuditId);
    IFileManager::Get().MakeDirectory(*RequestDirectory, true);
    const FString PromptPath = FPaths::Combine(RequestDirectory, TEXT("request.txt"));
    const FString ContextPath = FPaths::Combine(
        RequestDirectory, TEXT("level_audit_context.json"));
    ReportOutputPath = FPaths::Combine(
        RequestDirectory, TEXT("level_audit_report.json"));

    Report = FRenderMasterLevelAuditReport();
    ErrorText.Reset();
    ProcessLog.Reset();
    FString Error;
    if (!FFileHelper::SaveStringToFile(
            CleanPrompt,
            *PromptPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)
        || !CaptureAndWriteContext(ContextPath, ExpectedAuditId, Error))
    {
        Fail(Error.IsEmpty()
            ? TEXT("Could not write the whole-level audit request.")
            : Error);
        return false;
    }

    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-level-audit --prompt-file %s --context %s --output %s"),
        *QuoteArgument(PromptPath),
        *QuoteArgument(ContextPath),
        *QuoteArgument(ReportOutputPath));
    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        Fail(TEXT("Could not create level audit process pipes."));
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
        Fail(FString::Printf(TEXT("Could not start Python: %s"), *Python));
        return false;
    }
    State = ERenderMasterLevelAuditState::Analyzing;
    AppendLog(FString::Printf(
        TEXT("Auditing %d eligible StaticMeshActors (process %u)."),
        CapturedTargets.Num(),
        ProcessId));
    return true;
}

bool FRenderMasterLevelAuditAssistant::CaptureAndWriteContext(
    const FString& Filename,
    const FString& AuditId,
    FString& OutError)
{
    CapturedTargets.Reset();
    TargetActors.Reset();
    TargetComponents.Reset();
    UWorld* World = GEditor != nullptr
        ? GEditor->GetEditorWorldContext(false).World()
        : nullptr;
    if (World == nullptr || World->GetPackage() == nullptr)
    {
        OutError = TEXT("Open an editable Unreal level before running a full audit.");
        return false;
    }

    struct FCapturedEntry
    {
        FRenderMasterPerformanceEvidence Evidence;
        TWeakObjectPtr<AStaticMeshActor> Actor;
        TWeakObjectPtr<UStaticMeshComponent> Component;
    };
    TArray<FCapturedEntry> Entries;
    int32 StaticMeshActorCount = 0;
    int32 ExcludedActorCount = 0;
    for (TActorIterator<AStaticMeshActor> It(World); It; ++It)
    {
        AStaticMeshActor* Actor = *It;
        if (Actor == nullptr || Actor->GetClass() != AStaticMeshActor::StaticClass())
            continue;
        ++StaticMeshActorCount;
        UStaticMeshComponent* Component = Actor->GetStaticMeshComponent();
        FRenderMasterPerformanceEvidence Evidence;
        FString CaptureError;
        if (!RenderMasterCapturePerformanceEvidence(
                Actor, Component, Evidence, CaptureError))
        {
            ++ExcludedActorCount;
            continue;
        }
        FCapturedEntry Entry;
        Entry.Evidence = MoveTemp(Evidence);
        Entry.Actor = Actor;
        Entry.Component = Component;
        Entries.Add(MoveTemp(Entry));
    }
    if (Entries.IsEmpty())
    {
        OutError = TEXT("The current level has no eligible editable native StaticMeshActors to audit.");
        return false;
    }
    if (Entries.Num() > 4096)
    {
        OutError = TEXT("The level contains more than 4096 eligible StaticMeshActors; partition the audit before continuing.");
        return false;
    }
    Entries.Sort(
        [](const FCapturedEntry& A, const FCapturedEntry& B)
        {
            return A.Evidence.ActorPath.Compare(
                B.Evidence.ActorPath, ESearchCase::IgnoreCase) < 0;
        });

    const FString ProjectName = FApp::GetProjectName();
    const FString LevelPath = World->GetPackage()->GetName();
    TArray<TSharedPtr<FJsonValue>> ActorJson;
    for (const FCapturedEntry& Entry : Entries)
    {
        CapturedTargets.Add(Entry.Evidence);
        TargetActors.Add(Entry.Actor);
        TargetComponents.Add(Entry.Component);
        ActorJson.Add(MakeShared<FJsonValueObject>(
            RenderMasterPerformanceEvidenceJson(
                Entry.Evidence, ProjectName, LevelPath)));
    }

    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Root->SetStringField(TEXT("audit_id"), AuditId);
    Root->SetStringField(TEXT("captured_at_utc"), FDateTime::UtcNow().ToIso8601());
    Root->SetStringField(TEXT("project_name"), ProjectName);
    Root->SetStringField(TEXT("level_path"), LevelPath);
    Root->SetNumberField(TEXT("static_mesh_actor_count"), StaticMeshActorCount);
    Root->SetNumberField(TEXT("excluded_actor_count"), ExcludedActorCount);
    Root->SetArrayField(TEXT("actors"), ActorJson);
    FString JsonText;
    if (!SerializeJsonObject(Root, JsonText)
        || !FFileHelper::SaveStringToFile(
            JsonText,
            *Filename,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        OutError = FString::Printf(TEXT("Could not write level audit context: %s"), *Filename);
        CapturedTargets.Reset();
        TargetActors.Reset();
        TargetComponents.Reset();
        return false;
    }
    return true;
}

bool FRenderMasterLevelAuditAssistant::RevalidateActions(
    TArray<AStaticMeshActor*>& OutActors,
    TArray<UStaticMeshComponent*>& OutComponents,
    TArray<FRenderMasterPerformanceAction>& OutActions,
    FString& OutError) const
{
    if (Report.Actions.IsEmpty()
        || CapturedTargets.Num() != TargetActors.Num()
        || CapturedTargets.Num() != TargetComponents.Num())
    {
        OutError = TEXT("Level audit action evidence is incomplete.");
        return false;
    }
    for (const FRenderMasterLevelAuditAction& Action : Report.Actions)
    {
        const int32 Index = CapturedTargets.IndexOfByPredicate(
            [&Action](const FRenderMasterPerformanceEvidence& Evidence)
            {
                return Evidence.ActorPath
                    == Action.PerformanceAction.Target.ActorPath;
            });
        if (Index == INDEX_NONE)
        {
            OutError = TEXT("A level audit action target is no longer in the frozen context.");
            return false;
        }
        AStaticMeshActor* Actor = TargetActors[Index].Get();
        UStaticMeshComponent* Component = TargetComponents[Index].Get();
        FRenderMasterPerformanceEvidence Current;
        if (!RenderMasterCapturePerformanceEvidence(
                Actor, Component, Current, OutError)
            || !RenderMasterPerformanceEvidenceMatches(
                Current, CapturedTargets[Index])
            || !RenderMasterPerformanceEvidenceMatches(
                CapturedTargets[Index], Action.PerformanceAction.Target))
        {
            if (OutError.IsEmpty())
                OutError = FString::Printf(
                    TEXT("Level audit evidence is stale for action %s."),
                    *Action.AssessmentId);
            return false;
        }
        OutActors.Add(Actor);
        OutComponents.Add(Component);
        OutActions.Add(Action.PerformanceAction);
    }
    return true;
}

bool FRenderMasterLevelAuditAssistant::ApplyActions()
{
    if (!CanApply()) return false;
    TArray<AStaticMeshActor*> Actors;
    TArray<UStaticMeshComponent*> Components;
    TArray<FRenderMasterPerformanceAction> Actions;
    FString Error;
    if (!RevalidateActions(Actors, Components, Actions, Error)
        || !RenderMasterApplyPerformanceBatch(
            Actors, Components, Actions, Error, true))
    {
        Fail(Error.IsEmpty()
            ? TEXT("The level audit actions could not be applied safely.")
            : Error);
        return false;
    }
    State = ERenderMasterLevelAuditState::Applied;
    return true;
}

void FRenderMasterLevelAuditAssistant::Reject()
{
    if (ProcessHandle.IsValid())
        FPlatformProcess::TerminateProc(ProcessHandle, true);
    CloseProcessResources();
    Report = FRenderMasterLevelAuditReport();
    State = ERenderMasterLevelAuditState::Rejected;
}

void FRenderMasterLevelAuditAssistant::Cancel()
{
    if (ProcessHandle.IsValid())
        FPlatformProcess::TerminateProc(ProcessHandle, true);
    CloseProcessResources();
    State = ERenderMasterLevelAuditState::Rejected;
}

bool FRenderMasterLevelAuditAssistant::CanStart() const
{
    return State != ERenderMasterLevelAuditState::Analyzing
        && State != ERenderMasterLevelAuditState::Proposed;
}

bool FRenderMasterLevelAuditAssistant::CanApply() const
{
    return State == ERenderMasterLevelAuditState::Proposed
        && !Report.Actions.IsEmpty();
}

bool FRenderMasterLevelAuditAssistant::IsBusy() const
{
    return State == ERenderMasterLevelAuditState::Analyzing;
}

AStaticMeshActor* FRenderMasterLevelAuditAssistant::FindCapturedActorByPath(
    const FString& ActorPath) const
{
    for (int32 Index = 0; Index < CapturedTargets.Num(); ++Index)
    {
        if (CapturedTargets[Index].ActorPath == ActorPath
            && TargetActors.IsValidIndex(Index))
        {
            return TargetActors[Index].Get();
        }
    }
    return nullptr;
}

FText FRenderMasterLevelAuditAssistant::GetStateText() const
{
    switch (State)
    {
        case ERenderMasterLevelAuditState::Analyzing:
            return FText::FromString(TEXT("Capturing and reviewing the complete level"));
        case ERenderMasterLevelAuditState::Proposed:
            return FText::FromString(TEXT("Safe actions awaiting approval"));
        case ERenderMasterLevelAuditState::ReviewOnly:
            return FText::FromString(TEXT("Whole-level static audit complete"));
        case ERenderMasterLevelAuditState::Unresolved:
            return FText::FromString(TEXT("Audit requires a missing capability"));
        case ERenderMasterLevelAuditState::Failed:
            return FText::FromString(TEXT("Level audit failed safely"));
        case ERenderMasterLevelAuditState::Applied:
            return FText::FromString(TEXT("Approved audit actions applied"));
        case ERenderMasterLevelAuditState::Rejected:
            return FText::FromString(TEXT("Level audit dismissed"));
        default:
            return FText::FromString(TEXT("Ready for a whole-level audit"));
    }
}

FText FRenderMasterLevelAuditAssistant::GetSummaryText() const
{
    if (State == ERenderMasterLevelAuditState::Analyzing)
        return FText::FromString(ProcessLog.IsEmpty()
            ? TEXT("Capturing every eligible native StaticMeshActor and computing deterministic priorities.")
            : ProcessLog);
    if (State == ERenderMasterLevelAuditState::Failed)
        return FText::FromString(ErrorText);
    if (State == ERenderMasterLevelAuditState::Applied)
        return FText::FromString(TEXT(
            "The approved whole-level audit actions were applied in one transaction. "
            "The level was not saved; use Ctrl+Z once to undo the complete batch."));
    if (State == ERenderMasterLevelAuditState::Rejected)
        return FText::FromString(TEXT("The level audit was dismissed. No scene change was applied."));
    if (State == ERenderMasterLevelAuditState::Unresolved)
        return FText::FromString(FString::Printf(
            TEXT("%s\n\nMissing capability\n%s"),
            *Report.Summary,
            *Report.MissingCapabilities));
    if (State == ERenderMasterLevelAuditState::Proposed
        || State == ERenderMasterLevelAuditState::ReviewOnly)
    {
        TArray<FString> Top;
        for (int32 Index = 0; Index < FMath::Min(5, Report.Assessments.Num()); ++Index)
        {
            const FRenderMasterLevelAuditAssessment& Assessment = Report.Assessments[Index];
            Top.Add(FString::Printf(
                TEXT("%d. %s — %s (%d/100), next: %s"),
                Index + 1,
                *Assessment.ActorName,
                *Assessment.RiskBand,
                Assessment.RiskScore,
                *Assessment.RecommendedNextTest));
        }
        const FString FindingsText = Report.Findings.IsEmpty()
            ? TEXT("No model-authored finding was required beyond deterministic ranking.")
            : FString::Join(Report.Findings, TEXT("\n\n"));
        FString ActionsText = TEXT("No executable setting change was proposed.");
        if (!Report.Actions.IsEmpty())
        {
            TArray<FString> Items;
            for (const FRenderMasterLevelAuditAction& Action : Report.Actions)
            {
                Items.Add(FString::Printf(
                    TEXT("%s\n%s\nWhy: %s"),
                    *Action.PerformanceAction.Target.ActorName,
                    *Action.PerformanceAction.ChangeSummary,
                    *Action.PerformanceAction.Rationale));
            }
            ActionsText = FString::Join(Items, TEXT("\n\n----------------\n\n"));
        }
        return FText::FromString(FString::Printf(
            TEXT("%s\n\nLevel evidence\n%d captured, %d excluded, %lld LOD0 triangles, %d high-priority candidates.\n\nTop deterministic priorities\n%s\n\nFindings\n%s\n\nProposed safe settings\n%s\n\nStatic priority is not measured runtime cost. No level save has occurred."),
            *Report.Summary,
            Report.Statistics.CapturedActorCount,
            Report.Statistics.ExcludedActorCount,
            Report.Statistics.TotalLod0Triangles,
            Report.Statistics.HighPriorityActorCount,
            *FString::Join(Top, TEXT("\n")),
            *FindingsText,
            *ActionsText));
    }
    return FText::FromString(TEXT(
        "Run a deterministic audit of every eligible native StaticMeshActor in the current Editor level. "
        "The first stage ranks static evidence and may propose only approval-gated Cast Shadow or Max Draw Distance changes."));
}

FLinearColor FRenderMasterLevelAuditAssistant::GetStateColor() const
{
    switch (State)
    {
        case ERenderMasterLevelAuditState::Proposed:
            return FLinearColor(0.85f, 0.48f, 0.08f, 1.0f);
        case ERenderMasterLevelAuditState::ReviewOnly:
            return FLinearColor(0.16f, 0.50f, 0.78f, 1.0f);
        case ERenderMasterLevelAuditState::Applied:
            return FLinearColor(0.12f, 0.55f, 0.28f, 1.0f);
        case ERenderMasterLevelAuditState::Unresolved:
            return FLinearColor(0.65f, 0.45f, 0.08f, 1.0f);
        case ERenderMasterLevelAuditState::Failed:
            return FLinearColor(0.70f, 0.14f, 0.12f, 1.0f);
        default:
            return FLinearColor(0.14f, 0.20f, 0.30f, 1.0f);
    }
}

bool FRenderMasterLevelAuditAssistant::Tick(float DeltaTime)
{
    (void)DeltaTime;
    if (!ProcessHandle.IsValid()) return true;
    const FString StdOut = FPlatformProcess::ReadPipe(StdOutRead);
    const FString StdErr = FPlatformProcess::ReadPipe(StdErrRead);
    if (!StdOut.IsEmpty()) AppendLog(StdOut.TrimStartAndEnd());
    if (!StdErr.IsEmpty()) AppendLog(StdErr.TrimStartAndEnd());
    if (!FPlatformProcess::IsProcRunning(ProcessHandle)) CompleteProcess();
    return true;
}

void FRenderMasterLevelAuditAssistant::CompleteProcess()
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
            ? FString::Printf(TEXT("Level audit process exited with code %d."), ReturnCode)
            : ProcessLog);
        return;
    }
    FString Error;
    if (!RenderMasterParseLevelAuditReportFile(
            ReportOutputPath, Report, Error))
    {
        Fail(Error);
        return;
    }
    if (Report.AuditId != ExpectedAuditId
        || Report.ContextActors.Num() != CapturedTargets.Num())
    {
        Fail(TEXT("Level audit report identity or Actor count does not match the Editor capture."));
        return;
    }
    for (int32 Index = 0; Index < CapturedTargets.Num(); ++Index)
    {
        if (!RenderMasterPerformanceEvidenceMatches(
                CapturedTargets[Index], Report.ContextActors[Index]))
        {
            Fail(TEXT("Level audit report evidence does not match the frozen Editor context."));
            return;
        }
    }
    if (Report.Status == TEXT("proposed"))
        State = ERenderMasterLevelAuditState::Proposed;
    else if (Report.Status == TEXT("review_only"))
        State = ERenderMasterLevelAuditState::ReviewOnly;
    else
        State = ERenderMasterLevelAuditState::Unresolved;
}

void FRenderMasterLevelAuditAssistant::CloseProcessResources()
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

void FRenderMasterLevelAuditAssistant::AppendLog(const FString& Line)
{
    const FString Clean = Line.TrimStartAndEnd();
    if (Clean.IsEmpty()) return;
    if (!ProcessLog.IsEmpty()) ProcessLog += TEXT("\n");
    ProcessLog += Clean;
    constexpr int32 MaxLogChars = 8000;
    if (ProcessLog.Len() > MaxLogChars)
        ProcessLog.RightChopInline(ProcessLog.Len() - MaxLogChars);
}

void FRenderMasterLevelAuditAssistant::Fail(const FString& Error)
{
    if (ProcessHandle.IsValid())
        FPlatformProcess::TerminateProc(ProcessHandle, true);
    CloseProcessResources();
    ErrorText = Error.IsEmpty() ? TEXT("Level audit failed safely.") : Error;
    State = ERenderMasterLevelAuditState::Failed;
}
