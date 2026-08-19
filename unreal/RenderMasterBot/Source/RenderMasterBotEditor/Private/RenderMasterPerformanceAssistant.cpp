#include "RenderMasterPerformanceAssistant.h"

#include "RenderMasterWorkflowController.h"

#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "ScopedTransaction.h"
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

FString MobilityText(const UStaticMeshComponent* Component)
{
    if (Component != nullptr && Component->Mobility == EComponentMobility::Static)
        return TEXT("static");
    if (Component != nullptr && Component->Mobility == EComponentMobility::Stationary)
        return TEXT("stationary");
    return TEXT("movable");
}

FString CollisionText(const UStaticMeshComponent* Component)
{
    if (Component == nullptr) return FString();
    switch (Component->GetCollisionEnabled())
    {
        case ECollisionEnabled::NoCollision:
            return TEXT("no_collision");
        case ECollisionEnabled::QueryOnly:
            return TEXT("query_only");
        case ECollisionEnabled::PhysicsOnly:
            return TEXT("physics_only");
        case ECollisionEnabled::QueryAndPhysics:
            return TEXT("query_and_physics");
        default:
            return FString();
    }
}

bool IsGuidText(const FString& Value)
{
    if (Value.IsEmpty()) return true;
    if (Value.Len() != 32) return false;
    for (const TCHAR Character : Value)
    {
        if (!FChar::IsHexDigit(Character)) return false;
    }
    return true;
}

bool IsBoundedSnapshot(const FRenderMasterPerformanceSnapshot& Snapshot)
{
    return FMath::IsFinite(Snapshot.MaxDrawDistanceCm)
        && Snapshot.MaxDrawDistanceCm >= 0.0f
        && Snapshot.MaxDrawDistanceCm <= 1000000.0f;
}

TSharedRef<FJsonObject> SnapshotJson(
    const FRenderMasterPerformanceSnapshot& Snapshot)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetBoolField(TEXT("cast_shadow"), Snapshot.bCastShadow);
    Json->SetNumberField(
        TEXT("max_draw_distance_cm"), Snapshot.MaxDrawDistanceCm);
    return Json;
}

bool ParseSnapshot(
    const TSharedPtr<FJsonObject>& Json,
    FRenderMasterPerformanceSnapshot& Out,
    FString& OutError)
{
    double MaxDrawDistance = 0.0;
    if (!Json.IsValid()
        || !Json->TryGetBoolField(TEXT("cast_shadow"), Out.bCastShadow)
        || !Json->TryGetNumberField(
            TEXT("max_draw_distance_cm"), MaxDrawDistance)
        || !FMath::IsFinite(MaxDrawDistance)
        || MaxDrawDistance < 0.0 || MaxDrawDistance > 1000000.0)
    {
        OutError = TEXT("Performance snapshot is outside the supported boundary.");
        return false;
    }
    Out.MaxDrawDistanceCm = static_cast<float>(MaxDrawDistance);
    return true;
}

bool EvidenceMatches(
    const FRenderMasterPerformanceEvidence& A,
    const FRenderMasterPerformanceEvidence& B)
{
    return A.ActorName == B.ActorName
        && A.ActorPath == B.ActorPath
        && A.ActorClass == B.ActorClass
        && A.ActorGuid == B.ActorGuid
        && A.ComponentName == B.ComponentName
        && A.ComponentMobility == B.ComponentMobility
        && A.MeshPath == B.MeshPath
        && A.LodCount == B.LodCount
        && A.Lod0Triangles == B.Lod0Triangles
        && A.MaterialSlotCount == B.MaterialSlotCount
        && A.bNaniteEnabled == B.bNaniteEnabled
        && A.CollisionMode == B.CollisionMode
        && A.bComponentTickEnabled == B.bComponentTickEnabled
        && FMath::IsNearlyEqual(A.BoundsRadiusCm, B.BoundsRadiusCm, 0.01f)
        && RenderMasterPerformanceSnapshotsMatch(A.Before, B.Before);
}

TSharedRef<FJsonObject> EvidenceJson(
    const FRenderMasterPerformanceEvidence& Evidence,
    const FString& ProjectName,
    const FString& LevelPath)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Json->SetStringField(TEXT("project_name"), ProjectName);
    Json->SetStringField(TEXT("level_path"), LevelPath);
    Json->SetStringField(TEXT("actor_name"), Evidence.ActorName);
    Json->SetStringField(TEXT("actor_path"), Evidence.ActorPath);
    Json->SetStringField(TEXT("actor_class"), Evidence.ActorClass);
    if (!Evidence.ActorGuid.IsEmpty())
        Json->SetStringField(TEXT("actor_guid"), Evidence.ActorGuid);
    Json->SetStringField(TEXT("component_name"), Evidence.ComponentName);
    Json->SetStringField(
        TEXT("component_mobility"), Evidence.ComponentMobility);
    Json->SetBoolField(TEXT("is_editable"), true);
    Json->SetBoolField(TEXT("is_locked"), false);
    Json->SetStringField(TEXT("mesh_path"), Evidence.MeshPath);
    Json->SetNumberField(TEXT("lod_count"), Evidence.LodCount);
    Json->SetNumberField(TEXT("lod0_triangles"), Evidence.Lod0Triangles);
    Json->SetNumberField(
        TEXT("material_slot_count"), Evidence.MaterialSlotCount);
    Json->SetBoolField(TEXT("nanite_enabled"), Evidence.bNaniteEnabled);
    Json->SetStringField(TEXT("collision_mode"), Evidence.CollisionMode);
    Json->SetBoolField(
        TEXT("component_tick_enabled"), Evidence.bComponentTickEnabled);
    Json->SetNumberField(TEXT("bounds_radius_cm"), Evidence.BoundsRadiusCm);
    Json->SetObjectField(TEXT("performance"), SnapshotJson(Evidence.Before));
    return Json;
}

bool ParseIntegralNumber(
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

bool ParseEvidence(
    const TSharedPtr<FJsonObject>& Json,
    const FString& ProjectName,
    const FString& LevelPath,
    FRenderMasterPerformanceEvidence& Out,
    FString& OutError)
{
    FString SchemaVersion;
    FString EvidenceProject;
    FString EvidenceLevel;
    bool bEditable = false;
    bool bLocked = true;
    double BoundsRadius = 0.0;
    int64 LodCount = 0;
    int64 Lod0Triangles = 0;
    int64 MaterialSlots = 0;
    TSharedPtr<FJsonObject> Snapshot;
    if (!Json.IsValid()
        || !Json->TryGetStringField(TEXT("schema_version"), SchemaVersion)
        || SchemaVersion != TEXT("0.1")
        || !Json->TryGetStringField(TEXT("project_name"), EvidenceProject)
        || !Json->TryGetStringField(TEXT("level_path"), EvidenceLevel)
        || EvidenceProject != ProjectName || EvidenceLevel != LevelPath
        || !Json->TryGetStringField(TEXT("actor_name"), Out.ActorName)
        || !Json->TryGetStringField(TEXT("actor_path"), Out.ActorPath)
        || !Json->TryGetStringField(TEXT("actor_class"), Out.ActorClass)
        || Out.ActorClass != TEXT("StaticMeshActor")
        || !Json->TryGetStringField(TEXT("component_name"), Out.ComponentName)
        || !Json->TryGetStringField(
            TEXT("component_mobility"), Out.ComponentMobility)
        || (Out.ComponentMobility != TEXT("static")
            && Out.ComponentMobility != TEXT("stationary")
            && Out.ComponentMobility != TEXT("movable"))
        || !Json->TryGetBoolField(TEXT("is_editable"), bEditable)
        || !Json->TryGetBoolField(TEXT("is_locked"), bLocked)
        || !bEditable || bLocked
        || !Json->TryGetStringField(TEXT("mesh_path"), Out.MeshPath)
        || !ParseIntegralNumber(Json, TEXT("lod_count"), 1, 64, LodCount)
        || !ParseIntegralNumber(
            Json, TEXT("lod0_triangles"), 0, 2000000000LL, Lod0Triangles)
        || !ParseIntegralNumber(
            Json, TEXT("material_slot_count"), 0, 512, MaterialSlots)
        || !Json->TryGetBoolField(TEXT("nanite_enabled"), Out.bNaniteEnabled)
        || !Json->TryGetStringField(TEXT("collision_mode"), Out.CollisionMode)
        || (Out.CollisionMode != TEXT("no_collision")
            && Out.CollisionMode != TEXT("query_only")
            && Out.CollisionMode != TEXT("physics_only")
            && Out.CollisionMode != TEXT("query_and_physics"))
        || !Json->TryGetBoolField(
            TEXT("component_tick_enabled"), Out.bComponentTickEnabled)
        || !Json->TryGetNumberField(TEXT("bounds_radius_cm"), BoundsRadius)
        || !FMath::IsFinite(BoundsRadius)
        || BoundsRadius <= 0.0 || BoundsRadius > 10000000.0
        || !ReadObject(Json, TEXT("performance"), Snapshot))
    {
        OutError = TEXT("Performance selection contains invalid or incomplete Actor evidence.");
        return false;
    }
    Json->TryGetStringField(TEXT("actor_guid"), Out.ActorGuid);
    if (!IsGuidText(Out.ActorGuid)
        || Out.ActorName.IsEmpty() || Out.ActorPath.IsEmpty()
        || Out.ComponentName.IsEmpty() || Out.MeshPath.IsEmpty()
        || !ParseSnapshot(Snapshot, Out.Before, OutError))
    {
        if (OutError.IsEmpty())
            OutError = TEXT("Performance Actor identity or snapshot is invalid.");
        return false;
    }
    Out.LodCount = static_cast<int32>(LodCount);
    Out.Lod0Triangles = Lod0Triangles;
    Out.MaterialSlotCount = static_cast<int32>(MaterialSlots);
    Out.BoundsRadiusCm = static_cast<float>(BoundsRadius);
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

FString EvidenceValue(
    const FRenderMasterPerformanceEvidence& Evidence,
    const FString& Field)
{
    if (Field == TEXT("lod0_triangles"))
        return FString::Printf(TEXT("%lld"), Evidence.Lod0Triangles);
    if (Field == TEXT("lod_count"))
        return FString::FromInt(Evidence.LodCount);
    if (Field == TEXT("material_slot_count"))
        return FString::FromInt(Evidence.MaterialSlotCount);
    if (Field == TEXT("nanite_enabled"))
        return Evidence.bNaniteEnabled ? TEXT("true") : TEXT("false");
    if (Field == TEXT("collision_mode")) return Evidence.CollisionMode;
    if (Field == TEXT("component_mobility")) return Evidence.ComponentMobility;
    if (Field == TEXT("component_tick_enabled"))
        return Evidence.bComponentTickEnabled ? TEXT("true") : TEXT("false");
    if (Field == TEXT("cast_shadow"))
        return Evidence.Before.bCastShadow ? TEXT("true") : TEXT("false");
    if (Field == TEXT("max_draw_distance_cm"))
        return FString::Printf(TEXT("%.1f cm"), Evidence.Before.MaxDrawDistanceCm);
    if (Field == TEXT("bounds_radius_cm"))
        return FString::Printf(TEXT("%.1f cm"), Evidence.BoundsRadiusCm);
    return FString();
}

bool ParseFindings(
    const TSharedPtr<FJsonObject>& Root,
    const TArray<FRenderMasterPerformanceEvidence>& Selected,
    TArray<FString>& OutFindings,
    FString& OutError)
{
    const TArray<TSharedPtr<FJsonValue>>* Findings = nullptr;
    if (!Root.IsValid()
        || !Root->TryGetArrayField(TEXT("findings"), Findings)
        || Findings == nullptr || Findings->Num() > 64)
    {
        OutError = TEXT("Performance proposal findings are invalid.");
        return false;
    }
    const TSet<FString> AllowedFields = {
        TEXT("lod0_triangles"), TEXT("lod_count"), TEXT("material_slot_count"),
        TEXT("nanite_enabled"), TEXT("collision_mode"),
        TEXT("component_mobility"), TEXT("component_tick_enabled"),
        TEXT("cast_shadow"), TEXT("max_draw_distance_cm"),
        TEXT("bounds_radius_cm")};
    const TSet<FString> AllowedSeverities = {
        TEXT("info"), TEXT("warning"), TEXT("critical")};
    const TSet<FString> AllowedCategories = {
        TEXT("geometry"), TEXT("materials"), TEXT("nanite"),
        TEXT("collision"), TEXT("tick"), TEXT("shadows"), TEXT("culling")};
    for (const TSharedPtr<FJsonValue>& Value : *Findings)
    {
        const TSharedPtr<FJsonObject>* Finding = nullptr;
        FString ActorPath;
        FString Severity;
        FString Category;
        FString Recommendation;
        const TArray<TSharedPtr<FJsonValue>>* EvidenceFields = nullptr;
        if (!Value.IsValid() || !Value->TryGetObject(Finding) || Finding == nullptr
            || !(*Finding)->TryGetStringField(TEXT("actor_path"), ActorPath)
            || !(*Finding)->TryGetStringField(TEXT("severity"), Severity)
            || !(*Finding)->TryGetStringField(TEXT("category"), Category)
            || !(*Finding)->TryGetStringField(
                TEXT("recommendation"), Recommendation)
            || !AllowedSeverities.Contains(Severity)
            || !AllowedCategories.Contains(Category)
            || Recommendation.IsEmpty()
            || !(*Finding)->TryGetArrayField(
                TEXT("evidence_fields"), EvidenceFields)
            || EvidenceFields == nullptr || EvidenceFields->IsEmpty()
            || EvidenceFields->Num() > 10)
        {
            OutError = TEXT("Performance proposal contains an invalid finding.");
            return false;
        }
        const FRenderMasterPerformanceEvidence* ActorEvidence =
            Selected.FindByPredicate(
                [&ActorPath](const FRenderMasterPerformanceEvidence& Evidence)
                {
                    return Evidence.ActorPath == ActorPath;
                });
        if (ActorEvidence == nullptr)
        {
            OutError = TEXT("Performance finding references an unselected Actor.");
            return false;
        }
        TSet<FString> SeenFields;
        TArray<FString> Values;
        for (const TSharedPtr<FJsonValue>& EvidenceValueJson : *EvidenceFields)
        {
            FString Field;
            if (!EvidenceValueJson.IsValid()
                || !EvidenceValueJson->TryGetString(Field)
                || !AllowedFields.Contains(Field) || SeenFields.Contains(Field))
            {
                OutError = TEXT("Performance finding cites invalid evidence.");
                return false;
            }
            SeenFields.Add(Field);
            Values.Add(FString::Printf(
                TEXT("%s=%s"), *Field, *EvidenceValue(*ActorEvidence, Field)));
        }
        OutFindings.Add(FString::Printf(
            TEXT("[%s / %s] %s\nEvidence: %s\nRecommendation: %s"),
            *Severity,
            *Category,
            *ActorEvidence->ActorName,
            *FString::Join(Values, TEXT(", ")),
            *Recommendation));
    }
    return true;
}

bool ParseAction(
    const TSharedPtr<FJsonObject>& Json,
    const FString& ProjectName,
    const FString& LevelPath,
    const FRenderMasterPerformanceEvidence& Selected,
    FRenderMasterPerformanceAction& Out,
    FString& OutError)
{
    TSharedPtr<FJsonObject> Target;
    TSharedPtr<FJsonObject> Before;
    TSharedPtr<FJsonObject> After;
    const TArray<TSharedPtr<FJsonValue>>* Changes = nullptr;
    if (!Json.IsValid()
        || !ReadObject(Json, TEXT("target"), Target)
        || !ReadObject(Json, TEXT("before"), Before)
        || !ReadObject(Json, TEXT("after"), After)
        || !Json->TryGetArrayField(TEXT("changes"), Changes)
        || Changes == nullptr || Changes->Num() > 2
        || !Json->TryGetStringField(TEXT("rationale"), Out.Rationale)
        || Out.Rationale.IsEmpty()
        || !ParseEvidence(
            Target, ProjectName, LevelPath, Out.Target, OutError)
        || !EvidenceMatches(Selected, Out.Target)
        || !ParseSnapshot(Before, Out.Before, OutError)
        || !ParseSnapshot(After, Out.After, OutError)
        || !RenderMasterPerformanceSnapshotsMatch(
            Out.Target.Before, Out.Before))
    {
        if (OutError.IsEmpty())
            OutError = TEXT("Performance action does not match ordered selection evidence.");
        return false;
    }

    const bool bShadowChanged = Out.Before.bCastShadow != Out.After.bCastShadow;
    const bool bDistanceChanged = !FMath::IsNearlyEqual(
        Out.Before.MaxDrawDistanceCm, Out.After.MaxDrawDistanceCm, 0.01f);
    if (bDistanceChanged
        && Out.After.MaxDrawDistanceCm > 0.0f
        && Out.After.MaxDrawDistanceCm < 500.0f)
    {
        OutError = TEXT("Non-zero max draw distance must be at least 500 cm.");
        return false;
    }

    TSet<FString> Properties;
    for (const TSharedPtr<FJsonValue>& Value : *Changes)
    {
        const TSharedPtr<FJsonObject>* Change = nullptr;
        FString Property;
        if (!Value.IsValid() || !Value->TryGetObject(Change) || Change == nullptr
            || !(*Change)->TryGetStringField(TEXT("property"), Property)
            || Properties.Contains(Property))
        {
            OutError = TEXT("Performance action contains an invalid repeated change.");
            return false;
        }
        Properties.Add(Property);
        if (Property == TEXT("cast_shadow"))
        {
            bool BeforeValue = false;
            bool AfterValue = false;
            if (!(*Change)->TryGetBoolField(TEXT("before"), BeforeValue)
                || !(*Change)->TryGetBoolField(TEXT("after"), AfterValue)
                || BeforeValue != Out.Before.bCastShadow
                || AfterValue != Out.After.bCastShadow || !bShadowChanged)
            {
                OutError = TEXT("cast_shadow change does not match Before/After.");
                return false;
            }
        }
        else if (Property == TEXT("max_draw_distance_cm"))
        {
            double BeforeValue = 0.0;
            double AfterValue = 0.0;
            if (!(*Change)->TryGetNumberField(TEXT("before"), BeforeValue)
                || !(*Change)->TryGetNumberField(TEXT("after"), AfterValue)
                || !FMath::IsNearlyEqual(
                    BeforeValue, static_cast<double>(Out.Before.MaxDrawDistanceCm), 0.01)
                || !FMath::IsNearlyEqual(
                    AfterValue, static_cast<double>(Out.After.MaxDrawDistanceCm), 0.01)
                || !bDistanceChanged)
            {
                OutError = TEXT("max_draw_distance change does not match Before/After.");
                return false;
            }
        }
        else
        {
            OutError = TEXT("Performance action contains an unsupported property.");
            return false;
        }
    }
    if (Properties.Contains(TEXT("cast_shadow")) != bShadowChanged
        || Properties.Contains(TEXT("max_draw_distance_cm")) != bDistanceChanged)
    {
        OutError = TEXT("Performance changes do not cover Before/After exactly.");
        return false;
    }
    TArray<FString> Summary;
    if (bShadowChanged)
        Summary.Add(FString::Printf(
            TEXT("Cast Shadow: %s -> %s"),
            Out.Before.bCastShadow ? TEXT("true") : TEXT("false"),
            Out.After.bCastShadow ? TEXT("true") : TEXT("false")));
    if (bDistanceChanged)
        Summary.Add(FString::Printf(
            TEXT("Max Draw Distance: %.1f -> %.1f cm"),
            Out.Before.MaxDrawDistanceCm,
            Out.After.MaxDrawDistanceCm));
    Out.ChangeSummary = Summary.IsEmpty()
        ? TEXT("No change (preserved)")
        : FString::Join(Summary, TEXT("\n"));
    return true;
}
}

FRenderMasterPerformanceSnapshot RenderMasterSnapshotPerformance(
    const UStaticMeshComponent* Component)
{
    FRenderMasterPerformanceSnapshot Snapshot;
    if (Component != nullptr)
    {
        Snapshot.bCastShadow = Component->CastShadow;
        Snapshot.MaxDrawDistanceCm = Component->LDMaxDrawDistance;
    }
    return Snapshot;
}

bool RenderMasterPerformanceSnapshotsMatch(
    const FRenderMasterPerformanceSnapshot& A,
    const FRenderMasterPerformanceSnapshot& B)
{
    return A.bCastShadow == B.bCastShadow
        && FMath::IsNearlyEqual(
            A.MaxDrawDistanceCm, B.MaxDrawDistanceCm, 0.01f);
}

bool RenderMasterCapturePerformanceEvidence(
    AStaticMeshActor* Actor,
    UStaticMeshComponent* Component,
    FRenderMasterPerformanceEvidence& OutEvidence,
    FString& OutError)
{
    if (Actor == nullptr || Component == nullptr
        || Actor->GetStaticMeshComponent() != Component
        || Actor->GetClass() != AStaticMeshActor::StaticClass()
        || Actor->GetWorld() == nullptr || Actor->IsTemplate()
        || !Actor->IsEditable() || Actor->IsLockLocation())
    {
        OutError = TEXT("Select editable, unlocked StaticMeshActors from one level.");
        return false;
    }
    UStaticMesh* Mesh = Component->GetStaticMesh();
    const FString CollisionMode = CollisionText(Component);
    if (Mesh == nullptr || Mesh->GetNumLODs() < 1 || CollisionMode.IsEmpty())
    {
        OutError = FString::Printf(
            TEXT("Static mesh evidence is unavailable for %s."),
            *Actor->GetActorLabel());
        return false;
    }
    const float Scale = FMath::Max(
        Component->GetComponentScale().GetAbsMax(), KINDA_SMALL_NUMBER);
    const float BoundsRadius = Component->Bounds.SphereRadius > 0.0f
        ? Component->Bounds.SphereRadius
        : Mesh->GetBounds().SphereRadius * Scale;
    if (!FMath::IsFinite(BoundsRadius) || BoundsRadius <= 0.0f)
    {
        OutError = FString::Printf(
            TEXT("Bounds evidence is unavailable for %s."),
            *Actor->GetActorLabel());
        return false;
    }

    OutEvidence = FRenderMasterPerformanceEvidence();
    OutEvidence.ActorName = Actor->GetActorLabel();
    OutEvidence.ActorPath = Actor->GetPathName();
    OutEvidence.ActorClass = Actor->GetClass()->GetName();
    OutEvidence.ActorGuid = Actor->GetActorGuid().IsValid()
        ? Actor->GetActorGuid().ToString(EGuidFormats::Digits)
        : FString();
    OutEvidence.ComponentName = Component->GetName();
    OutEvidence.ComponentMobility = MobilityText(Component);
    OutEvidence.MeshPath = Mesh->GetPathName();
    OutEvidence.LodCount = Mesh->GetNumLODs();
    OutEvidence.Lod0Triangles = Mesh->GetNumTriangles(0);
    OutEvidence.MaterialSlotCount = Mesh->GetStaticMaterials().Num();
    OutEvidence.bNaniteEnabled = Mesh->GetNaniteSettings().bEnabled;
    OutEvidence.CollisionMode = CollisionMode;
    OutEvidence.bComponentTickEnabled =
        Component->PrimaryComponentTick.IsTickFunctionEnabled();
    OutEvidence.BoundsRadiusCm = BoundsRadius;
    OutEvidence.Before = RenderMasterSnapshotPerformance(Component);
    if (!IsBoundedSnapshot(OutEvidence.Before))
    {
        OutError = FString::Printf(
            TEXT("Performance settings are outside the supported boundary for %s."),
            *Actor->GetActorLabel());
        return false;
    }
    return true;
}

bool RenderMasterParsePerformanceProposalFile(
    const FString& Filename,
    FRenderMasterPerformanceProposal& OutProposal,
    FString& OutError)
{
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *Filename))
    {
        OutError = FString::Printf(
            TEXT("Could not read performance proposal: %s"), *Filename);
        return false;
    }
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        OutError = TEXT("Performance proposal is not valid JSON.");
        return false;
    }

    FRenderMasterPerformanceProposal Parsed;
    FString SchemaVersion;
    bool bModifiesScene = false;
    bool bAutoSave = true;
    bool bUndoSupported = false;
    if (!Root->TryGetStringField(TEXT("schema_version"), SchemaVersion)
        || SchemaVersion != TEXT("0.1")
        || !Root->TryGetStringField(TEXT("proposal_id"), Parsed.ProposalId)
        || !Root->TryGetStringField(TEXT("status"), Parsed.Status)
        || !Root->TryGetStringField(TEXT("request"), Parsed.Request)
        || !Root->TryGetStringField(TEXT("summary"), Parsed.Summary)
        || !Root->TryGetBoolField(
            TEXT("modifies_editor_scene"), bModifiesScene)
        || !Root->TryGetBoolField(TEXT("auto_save"), bAutoSave)
        || !Root->TryGetBoolField(TEXT("undo_supported"), bUndoSupported)
        || bAutoSave || !bUndoSupported || Parsed.Summary.IsEmpty())
    {
        OutError = TEXT("Performance proposal metadata or safety flags are invalid.");
        return false;
    }
    Parsed.Status.ToLowerInline();
    ReadMissingCapabilities(Root, Parsed.MissingCapabilities);

    TSharedPtr<FJsonObject> Selection;
    FString ProjectName;
    FString LevelPath;
    const TArray<TSharedPtr<FJsonValue>>* Actors = nullptr;
    if (!ReadObject(Root, TEXT("selection"), Selection)
        || !Selection->TryGetStringField(TEXT("project_name"), ProjectName)
        || !Selection->TryGetStringField(TEXT("level_path"), LevelPath)
        || !Selection->TryGetArrayField(TEXT("actors"), Actors)
        || Actors == nullptr || Actors->IsEmpty() || Actors->Num() > 32)
    {
        OutError = TEXT("Performance selection must contain 1-32 StaticMeshActors.");
        return false;
    }
    TArray<FRenderMasterPerformanceEvidence> Selected;
    TSet<FString> Paths;
    TSet<FString> Guids;
    for (const TSharedPtr<FJsonValue>& Value : *Actors)
    {
        const TSharedPtr<FJsonObject>* ActorJson = nullptr;
        FRenderMasterPerformanceEvidence Evidence;
        if (!Value.IsValid() || !Value->TryGetObject(ActorJson)
            || ActorJson == nullptr
            || !ParseEvidence(
                *ActorJson, ProjectName, LevelPath, Evidence, OutError))
        {
            if (OutError.IsEmpty())
                OutError = TEXT("Performance selection contains invalid evidence.");
            return false;
        }
        if (Paths.Contains(Evidence.ActorPath)
            || (!Evidence.ActorGuid.IsEmpty()
                && Guids.Contains(Evidence.ActorGuid)))
        {
            OutError = TEXT("Performance selection repeats an Actor identity.");
            return false;
        }
        Paths.Add(Evidence.ActorPath);
        if (!Evidence.ActorGuid.IsEmpty()) Guids.Add(Evidence.ActorGuid);
        Selected.Add(MoveTemp(Evidence));
    }
    if (!ParseFindings(Root, Selected, Parsed.Findings, OutError)) return false;

    const TArray<TSharedPtr<FJsonValue>>* Actions = nullptr;
    if (!Root->TryGetArrayField(TEXT("actions"), Actions) || Actions == nullptr)
    {
        OutError = TEXT("Performance proposal must include an actions array.");
        return false;
    }
    if (Parsed.Status == TEXT("unresolved"))
    {
        if (bModifiesScene || Parsed.MissingCapabilities.IsEmpty()
            || !Parsed.Findings.IsEmpty() || !Actions->IsEmpty())
        {
            OutError = TEXT("Unresolved performance proposals contain only capability gaps.");
            return false;
        }
        OutProposal = MoveTemp(Parsed);
        return true;
    }
    if (Parsed.Status == TEXT("review_only"))
    {
        if (bModifiesScene || !Parsed.MissingCapabilities.IsEmpty()
            || !Actions->IsEmpty())
        {
            OutError = TEXT("Review-only performance proposals cannot contain actions or capability gaps.");
            return false;
        }
        OutProposal = MoveTemp(Parsed);
        return true;
    }
    if (Parsed.Status != TEXT("proposed") || !bModifiesScene
        || !Parsed.MissingCapabilities.IsEmpty()
        || Actions->Num() != Selected.Num())
    {
        OutError = TEXT("Executable performance proposals must cover the ordered selection.");
        return false;
    }
    bool bAnyChange = false;
    for (int32 Index = 0; Index < Actions->Num(); ++Index)
    {
        const TSharedPtr<FJsonObject>* ActionJson = nullptr;
        FRenderMasterPerformanceAction Action;
        if (!(*Actions)[Index].IsValid()
            || !(*Actions)[Index]->TryGetObject(ActionJson)
            || ActionJson == nullptr
            || !ParseAction(
                *ActionJson,
                ProjectName,
                LevelPath,
                Selected[Index],
                Action,
                OutError))
        {
            if (OutError.IsEmpty())
                OutError = TEXT("Performance proposal contains an invalid action.");
            return false;
        }
        bAnyChange = bAnyChange
            || !RenderMasterPerformanceSnapshotsMatch(Action.Before, Action.After);
        Parsed.Actions.Add(MoveTemp(Action));
    }
    if (!bAnyChange)
    {
        OutError = TEXT("Performance proposal does not change any selected Actor.");
        return false;
    }
    OutProposal = MoveTemp(Parsed);
    return true;
}

bool RenderMasterApplyPerformanceBatch(
    const TArray<AStaticMeshActor*>& Actors,
    const TArray<UStaticMeshComponent*>& Components,
    const TArray<FRenderMasterPerformanceAction>& Actions,
    FString& OutError,
    bool bMarkPackageDirty)
{
    if (Actors.IsEmpty() || Actors.Num() > 32
        || Components.Num() != Actors.Num() || Actions.Num() != Actors.Num())
    {
        OutError = TEXT("A performance batch requires 1-32 complete ordered actions.");
        return false;
    }
    bool bAnyChange = false;
    for (int32 Index = 0; Index < Actors.Num(); ++Index)
    {
        FRenderMasterPerformanceEvidence Current;
        if (!RenderMasterCapturePerformanceEvidence(
                Actors[Index], Components[Index], Current, OutError)
            || !EvidenceMatches(Current, Actions[Index].Target)
            || !RenderMasterPerformanceSnapshotsMatch(
                Current.Before, Actions[Index].Before)
            || !IsBoundedSnapshot(Actions[Index].After)
            || (Actions[Index].After.MaxDrawDistanceCm > 0.0f
                && Actions[Index].After.MaxDrawDistanceCm < 500.0f))
        {
            if (OutError.IsEmpty())
                OutError = FString::Printf(
                    TEXT("Performance action %d no longer matches its frozen evidence."),
                    Index + 1);
            return false;
        }
        bAnyChange = bAnyChange
            || !RenderMasterPerformanceSnapshotsMatch(
                Actions[Index].Before, Actions[Index].After);
    }
    if (!bAnyChange)
    {
        OutError = TEXT("Performance batch contains no observable change.");
        return false;
    }

    FScopedTransaction Transaction(NSLOCTEXT(
        "RenderMasterBot",
        "ApplyAssistantPerformanceBatch",
        "RenderMasterBot: Apply Performance Settings"));
    TArray<int32> AppliedIndices;
    for (int32 Index = 0; Index < Actions.Num(); ++Index)
    {
        const FRenderMasterPerformanceAction& Action = Actions[Index];
        if (RenderMasterPerformanceSnapshotsMatch(Action.Before, Action.After))
            continue;
        Actors[Index]->Modify();
        Components[Index]->Modify();
        Components[Index]->SetCastShadow(Action.After.bCastShadow);
        Components[Index]->SetCullDistance(Action.After.MaxDrawDistanceCm);
        Components[Index]->PostEditChange();
        if (!RenderMasterPerformanceSnapshotsMatch(
                RenderMasterSnapshotPerformance(Components[Index]), Action.After))
        {
            for (int32 Restore = AppliedIndices.Num() - 1; Restore >= 0; --Restore)
            {
                const int32 RestoreIndex = AppliedIndices[Restore];
                Components[RestoreIndex]->SetCastShadow(
                    Actions[RestoreIndex].Before.bCastShadow);
                Components[RestoreIndex]->SetCullDistance(
                    Actions[RestoreIndex].Before.MaxDrawDistanceCm);
                Components[RestoreIndex]->PostEditChange();
            }
            Components[Index]->SetCastShadow(Action.Before.bCastShadow);
            Components[Index]->SetCullDistance(Action.Before.MaxDrawDistanceCm);
            Components[Index]->PostEditChange();
            Transaction.Cancel();
            OutError = FString::Printf(
                TEXT("Unreal did not retain performance action %d; the batch was rolled back."),
                Index + 1);
            return false;
        }
        AppliedIndices.Add(Index);
    }
    if (bMarkPackageDirty)
    {
        for (const int32 Index : AppliedIndices) Actors[Index]->MarkPackageDirty();
    }
    return true;
}

FRenderMasterPerformanceAssistant::FRenderMasterPerformanceAssistant(
    TSharedPtr<FRenderMasterWorkflowController> InWorkflowController)
    : WorkflowController(MoveTemp(InWorkflowController))
{
}

FRenderMasterPerformanceAssistant::~FRenderMasterPerformanceAssistant()
{
    Shutdown();
}

void FRenderMasterPerformanceAssistant::Initialize()
{
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateSP(
            AsShared(), &FRenderMasterPerformanceAssistant::Tick),
        0.2f);
}

void FRenderMasterPerformanceAssistant::Shutdown()
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

bool FRenderMasterPerformanceAssistant::StartProposal(
    const FString& Prompt,
    const TArray<AStaticMeshActor*>& Actors)
{
    if (!CanStart()) return false;
    const FString CleanPrompt = Prompt.TrimStartAndEnd();
    if (CleanPrompt.IsEmpty())
    {
        Fail(TEXT("Enter a performance review request before preparing an action."));
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

    const FString ProposalId = FString::Printf(
        TEXT("performance_%s"),
        *FDateTime::Now().ToString(TEXT("%Y%m%d_%H%M%S_%s")));
    const FString RequestDirectory = FPaths::Combine(
        Root, TEXT("assistant-performance"), ProposalId);
    IFileManager::Get().MakeDirectory(*RequestDirectory, true);
    const FString PromptPath = FPaths::Combine(
        RequestDirectory, TEXT("request.txt"));
    const FString ContextPath = FPaths::Combine(
        RequestDirectory, TEXT("performance_selection_context.json"));
    ProposalOutputPath = FPaths::Combine(
        RequestDirectory, TEXT("performance_proposal.json"));

    Proposal = FRenderMasterPerformanceProposal();
    ErrorText.Reset();
    ProcessLog.Reset();
    FString Error;
    if (!FFileHelper::SaveStringToFile(
            CleanPrompt,
            *PromptPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)
        || !WriteSelectionContext(ContextPath, Actors, Error))
    {
        Fail(Error.IsEmpty()
            ? TEXT("Could not write the performance review request.")
            : Error);
        return false;
    }

    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-performance-propose --prompt-file %s --context %s --proposal-id %s --output %s"),
        *QuoteArgument(PromptPath),
        *QuoteArgument(ContextPath),
        *QuoteArgument(ProposalId),
        *QuoteArgument(ProposalOutputPath));
    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        Fail(TEXT("Could not create performance assistant process pipes."));
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
    State = ERenderMasterPerformanceAssistantState::Planning;
    AppendLog(FString::Printf(
        TEXT("Reviewing measured evidence for %d StaticMeshActors (process %u)."),
        Actors.Num(),
        ProcessId));
    return true;
}

bool FRenderMasterPerformanceAssistant::WriteSelectionContext(
    const FString& Filename,
    const TArray<AStaticMeshActor*>& Actors,
    FString& OutError)
{
    CapturedTargets.Reset();
    TargetActors.Reset();
    TargetComponents.Reset();
    if (Actors.IsEmpty() || Actors.Num() > 32)
    {
        OutError = TEXT("Select 1-32 StaticMeshActors for a performance review.");
        return false;
    }
    UWorld* World = Actors[0] != nullptr ? Actors[0]->GetWorld() : nullptr;
    if (World == nullptr)
    {
        OutError = TEXT("The selected StaticMeshActors are not in an editable level.");
        return false;
    }
    const FString ProjectName = FApp::GetProjectName();
    const FString LevelPath = World->GetPackage()->GetName();
    TArray<TSharedPtr<FJsonValue>> ActorJson;
    TSet<FString> Paths;
    TSet<FString> Guids;
    for (AStaticMeshActor* Actor : Actors)
    {
        if (Actor == nullptr || Actor->GetWorld() != World)
        {
            OutError = TEXT("Every selected StaticMeshActor must belong to one level.");
            return false;
        }
        UStaticMeshComponent* Component = Actor->GetStaticMeshComponent();
        FRenderMasterPerformanceEvidence Evidence;
        if (!RenderMasterCapturePerformanceEvidence(
                Actor, Component, Evidence, OutError))
        {
            return false;
        }
        if (Paths.Contains(Evidence.ActorPath)
            || (!Evidence.ActorGuid.IsEmpty()
                && Guids.Contains(Evidence.ActorGuid)))
        {
            OutError = TEXT("Performance selection contains a duplicate Actor identity.");
            return false;
        }
        Paths.Add(Evidence.ActorPath);
        if (!Evidence.ActorGuid.IsEmpty()) Guids.Add(Evidence.ActorGuid);
        ActorJson.Add(MakeShared<FJsonValueObject>(
            EvidenceJson(Evidence, ProjectName, LevelPath)));
        CapturedTargets.Add(Evidence);
        TargetActors.Add(Actor);
        TargetComponents.Add(Component);
    }

    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Root->SetStringField(TEXT("project_name"), ProjectName);
    Root->SetStringField(TEXT("level_path"), LevelPath);
    Root->SetArrayField(TEXT("actors"), ActorJson);
    FString JsonText;
    if (!SerializeJsonObject(Root, JsonText)
        || !FFileHelper::SaveStringToFile(
            JsonText,
            *Filename,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        OutError = FString::Printf(
            TEXT("Could not write performance selection context: %s"), *Filename);
        CapturedTargets.Reset();
        TargetActors.Reset();
        TargetComponents.Reset();
        return false;
    }
    return true;
}

bool FRenderMasterPerformanceAssistant::RevalidateTargets(FString& OutError) const
{
    if (CapturedTargets.IsEmpty()
        || CapturedTargets.Num() != TargetActors.Num()
        || CapturedTargets.Num() != TargetComponents.Num()
        || (Proposal.Status == TEXT("proposed")
            && CapturedTargets.Num() != Proposal.Actions.Num()))
    {
        OutError = TEXT("Performance selection evidence is incomplete.");
        return false;
    }
    for (int32 Index = 0; Index < CapturedTargets.Num(); ++Index)
    {
        AStaticMeshActor* Actor = TargetActors[Index].Get();
        UStaticMeshComponent* Component = TargetComponents[Index].Get();
        FRenderMasterPerformanceEvidence Current;
        if (!RenderMasterCapturePerformanceEvidence(
                Actor, Component, Current, OutError)
            || !EvidenceMatches(Current, CapturedTargets[Index])
            || (Proposal.Status == TEXT("proposed")
                && !EvidenceMatches(
                    CapturedTargets[Index], Proposal.Actions[Index].Target)))
        {
            if (OutError.IsEmpty())
                OutError = FString::Printf(
                    TEXT("StaticMeshActor %s changed after the review was created. Prepare a new review."),
                    *CapturedTargets[Index].ActorName);
            return false;
        }
    }
    return true;
}

bool FRenderMasterPerformanceAssistant::ApplyProposal()
{
    FString Error;
    if (!CanApply() || !RevalidateTargets(Error))
    {
        Fail(Error.IsEmpty()
            ? TEXT("No executable performance proposal is ready to apply.")
            : Error);
        return false;
    }
    TArray<AStaticMeshActor*> Actors;
    TArray<UStaticMeshComponent*> Components;
    for (int32 Index = 0; Index < Proposal.Actions.Num(); ++Index)
    {
        Actors.Add(TargetActors[Index].Get());
        Components.Add(TargetComponents[Index].Get());
    }
    if (!RenderMasterApplyPerformanceBatch(
            Actors, Components, Proposal.Actions, Error))
    {
        Fail(Error.IsEmpty()
            ? TEXT("Unreal rejected the performance action.")
            : Error);
        return false;
    }
    State = ERenderMasterPerformanceAssistantState::Applied;
    AppendLog(FString::Printf(
        TEXT("Applied approved settings to %d StaticMeshActors in one Undo transaction. The level was not saved."),
        Proposal.Actions.Num()));
    return true;
}

void FRenderMasterPerformanceAssistant::RejectProposal()
{
    if (State == ERenderMasterPerformanceAssistantState::Planning)
    {
        Cancel();
        CloseProcessResources();
    }
    State = ERenderMasterPerformanceAssistantState::Rejected;
    AppendLog(TEXT("Performance proposal rejected. No Editor scene change was applied."));
}

void FRenderMasterPerformanceAssistant::Cancel()
{
    if (ProcessHandle.IsValid())
        FPlatformProcess::TerminateProc(ProcessHandle, true);
}

bool FRenderMasterPerformanceAssistant::CanStart() const
{
    return !ProcessHandle.IsValid();
}

bool FRenderMasterPerformanceAssistant::CanApply() const
{
    return State == ERenderMasterPerformanceAssistantState::Proposed
        && !Proposal.Actions.IsEmpty();
}

bool FRenderMasterPerformanceAssistant::IsPlanning() const
{
    return State == ERenderMasterPerformanceAssistantState::Planning;
}

FText FRenderMasterPerformanceAssistant::GetStateText() const
{
    switch (State)
    {
        case ERenderMasterPerformanceAssistantState::Planning:
            return NSLOCTEXT("RenderMasterBot", "PerformancePlanning", "Reviewing evidence");
        case ERenderMasterPerformanceAssistantState::Proposed:
            return NSLOCTEXT("RenderMasterBot", "PerformanceProposed", "Approval required");
        case ERenderMasterPerformanceAssistantState::ReviewOnly:
            return NSLOCTEXT("RenderMasterBot", "PerformanceReviewOnly", "Review complete");
        case ERenderMasterPerformanceAssistantState::Unresolved:
            return NSLOCTEXT("RenderMasterBot", "PerformanceUnresolved", "Unresolved");
        case ERenderMasterPerformanceAssistantState::Failed:
            return NSLOCTEXT("RenderMasterBot", "PerformanceFailed", "Failed");
        case ERenderMasterPerformanceAssistantState::Applied:
            return NSLOCTEXT("RenderMasterBot", "PerformanceApplied", "Applied");
        case ERenderMasterPerformanceAssistantState::Rejected:
            return NSLOCTEXT("RenderMasterBot", "PerformanceRejected", "Rejected");
        default:
            return NSLOCTEXT("RenderMasterBot", "PerformanceReady", "Ready");
    }
}

FText FRenderMasterPerformanceAssistant::GetSummaryText() const
{
    if (State == ERenderMasterPerformanceAssistantState::Planning)
        return FText::FromString(ProcessLog.IsEmpty()
            ? TEXT("Reviewing measured StaticMeshActor evidence...")
            : ProcessLog);
    if (State == ERenderMasterPerformanceAssistantState::Proposed)
    {
        TArray<FString> ActionSummaries;
        for (const FRenderMasterPerformanceAction& Action : Proposal.Actions)
        {
            ActionSummaries.Add(FString::Printf(
                TEXT("%s\n%s\nWhy: %s"),
                *Action.Target.ActorName,
                *Action.ChangeSummary,
                *Action.Rationale));
        }
        FString FindingsText = Proposal.Findings.IsEmpty()
            ? TEXT("No additional diagnostic findings.")
            : FString::Join(Proposal.Findings, TEXT("\n\n"));
        return FText::FromString(FString::Printf(
            TEXT("%s\n\nMeasured findings\n%s\n\nProposed component settings\n%s\n\nNo level change has been applied."),
            *Proposal.Summary,
            *FindingsText,
            *FString::Join(ActionSummaries, TEXT("\n\n----------------\n\n"))));
    }
    if (State == ERenderMasterPerformanceAssistantState::ReviewOnly)
    {
        const FString FindingsText = Proposal.Findings.IsEmpty()
            ? TEXT("No obvious risk was identified in the captured fields.")
            : FString::Join(Proposal.Findings, TEXT("\n\n"));
        return FText::FromString(FString::Printf(
            TEXT("%s\n\nMeasured findings\n%s\n\nThis review is read-only; no action was proposed."),
            *Proposal.Summary,
            *FindingsText));
    }
    if (State == ERenderMasterPerformanceAssistantState::Unresolved)
        return FText::FromString(FString::Printf(
            TEXT("%s\n\nMissing capability\n%s"),
            *Proposal.Summary,
            *Proposal.MissingCapabilities));
    if (State == ERenderMasterPerformanceAssistantState::Failed)
        return FText::FromString(ErrorText);
    if (State == ERenderMasterPerformanceAssistantState::Applied)
        return FText::FromString(TEXT(
            "The approved component performance settings were applied in one transaction. "
            "The level was not saved; use Ctrl+Z once to undo the complete batch."));
    if (State == ERenderMasterPerformanceAssistantState::Rejected)
        return FText::FromString(TEXT(
            "The performance proposal was rejected. No scene change was applied."));
    return FText::FromString(TEXT(
        "Select 1-32 StaticMeshActors to review measured mesh, LOD, material, Nanite, "
        "collision, Tick, shadow, culling, and bounds evidence."));
}

FLinearColor FRenderMasterPerformanceAssistant::GetStateColor() const
{
    switch (State)
    {
        case ERenderMasterPerformanceAssistantState::Proposed:
            return FLinearColor(0.85f, 0.48f, 0.08f, 1.0f);
        case ERenderMasterPerformanceAssistantState::ReviewOnly:
            return FLinearColor(0.16f, 0.50f, 0.78f, 1.0f);
        case ERenderMasterPerformanceAssistantState::Applied:
            return FLinearColor(0.12f, 0.55f, 0.28f, 1.0f);
        case ERenderMasterPerformanceAssistantState::Unresolved:
            return FLinearColor(0.65f, 0.45f, 0.08f, 1.0f);
        case ERenderMasterPerformanceAssistantState::Failed:
            return FLinearColor(0.70f, 0.14f, 0.12f, 1.0f);
        default:
            return FLinearColor(0.14f, 0.20f, 0.30f, 1.0f);
    }
}

bool FRenderMasterPerformanceAssistant::Tick(float DeltaTime)
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

void FRenderMasterPerformanceAssistant::CompleteProcess()
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
                TEXT("Performance proposal process exited with code %d."),
                ReturnCode)
            : ProcessLog);
        return;
    }
    FString Error;
    if (!RenderMasterParsePerformanceProposalFile(
            ProposalOutputPath, Proposal, Error))
    {
        Fail(Error);
        return;
    }
    if (Proposal.Status == TEXT("proposed"))
    {
        if (Proposal.Actions.Num() != CapturedTargets.Num())
        {
            Fail(TEXT("Performance proposal does not cover the captured selection."));
            return;
        }
        for (int32 Index = 0; Index < CapturedTargets.Num(); ++Index)
        {
            if (!EvidenceMatches(
                    CapturedTargets[Index], Proposal.Actions[Index].Target))
            {
                Fail(TEXT(
                    "Performance proposal evidence does not match the Editor capture."));
                return;
            }
        }
        State = ERenderMasterPerformanceAssistantState::Proposed;
    }
    else if (Proposal.Status == TEXT("review_only"))
    {
        State = ERenderMasterPerformanceAssistantState::ReviewOnly;
    }
    else
    {
        State = ERenderMasterPerformanceAssistantState::Unresolved;
    }
}

void FRenderMasterPerformanceAssistant::CloseProcessResources()
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

void FRenderMasterPerformanceAssistant::AppendLog(const FString& Line)
{
    const FString Clean = Line.TrimStartAndEnd();
    if (Clean.IsEmpty()) return;
    if (!ProcessLog.IsEmpty()) ProcessLog += TEXT("\n");
    ProcessLog += Clean;
    constexpr int32 MaxLogChars = 8000;
    if (ProcessLog.Len() > MaxLogChars)
        ProcessLog.RightChopInline(ProcessLog.Len() - MaxLogChars);
}

void FRenderMasterPerformanceAssistant::Fail(const FString& Error)
{
    ErrorText = Error.IsEmpty()
        ? TEXT("Performance proposal failed.")
        : Error;
    State = ERenderMasterPerformanceAssistantState::Failed;
    AppendLog(ErrorText);
}
