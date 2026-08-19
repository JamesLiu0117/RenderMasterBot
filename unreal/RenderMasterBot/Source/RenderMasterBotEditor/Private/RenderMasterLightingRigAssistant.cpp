#include "RenderMasterLightingRigAssistant.h"

#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "CineCameraActor.h"
#include "Components/LightComponent.h"
#include "Components/SceneComponent.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/Light.h"
#include "Engine/World.h"
#include "HAL/FileManager.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "RenderMasterWorkflowController.h"
#include "ScopedTransaction.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
constexpr double RigValueTolerance = 0.0001;
constexpr double RigTransformTolerance = 0.01;
constexpr double MaxRigCoordinateCm = 100000000.0;

FString QuoteRigArgument(const FString& Value)
{
    return FString::Printf(TEXT("\"%s\""), *Value);
}

bool ReadObject(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field,
    TSharedPtr<FJsonObject>& Out)
{
    const TSharedPtr<FJsonObject>* Value = nullptr;
    if (!Parent.IsValid() || !Parent->TryGetObjectField(Field, Value) || Value == nullptr)
        return false;
    Out = *Value;
    return Out.IsValid();
}

bool ReadVector(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field,
    FVector& Out)
{
    TSharedPtr<FJsonObject> Value;
    double X = 0.0;
    double Y = 0.0;
    double Z = 0.0;
    if (!ReadObject(Parent, Field, Value)
        || !Value->TryGetNumberField(TEXT("x"), X)
        || !Value->TryGetNumberField(TEXT("y"), Y)
        || !Value->TryGetNumberField(TEXT("z"), Z)
        || !FMath::IsFinite(X) || !FMath::IsFinite(Y) || !FMath::IsFinite(Z))
    {
        return false;
    }
    Out = FVector(X, Y, Z);
    return true;
}

bool ReadRotator(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field,
    FRotator& Out)
{
    FVector Axes;
    if (!ReadVector(Parent, Field, Axes)) return false;
    Out = FRotator(Axes.Y, Axes.Z, Axes.X);
    return true;
}

bool ReadColor(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field,
    FLinearColor& Out)
{
    TSharedPtr<FJsonObject> Value;
    double R = 0.0;
    double G = 0.0;
    double B = 0.0;
    if (!ReadObject(Parent, Field, Value)
        || !Value->TryGetNumberField(TEXT("r"), R)
        || !Value->TryGetNumberField(TEXT("g"), G)
        || !Value->TryGetNumberField(TEXT("b"), B)
        || !FMath::IsFinite(R) || !FMath::IsFinite(G) || !FMath::IsFinite(B)
        || R < 0.0 || R > 1.0 || G < 0.0 || G > 1.0 || B < 0.0 || B > 1.0)
    {
        return false;
    }
    Out = FLinearColor(R, G, B, 1.0);
    return true;
}

bool ReadOptionalNumber(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field,
    TOptional<double>& Out)
{
    if (!Parent.IsValid() || !Parent->HasField(Field)) return false;
    const TSharedPtr<FJsonValue> Value = Parent->TryGetField(Field);
    if (!Value.IsValid()) return false;
    if (Value->Type == EJson::Null)
    {
        Out.Reset();
        return true;
    }
    double Number = 0.0;
    if (!Value->TryGetNumber(Number) || !FMath::IsFinite(Number)) return false;
    Out = Number;
    return true;
}

bool ReadLightSnapshot(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field,
    FRenderMasterLightSnapshot& Out)
{
    TSharedPtr<FJsonObject> Snapshot;
    if (!ReadObject(Parent, Field, Snapshot)
        || !ReadRotator(Snapshot, TEXT("rotation_deg"), Out.Rotation)
        || !Snapshot->TryGetNumberField(TEXT("intensity"), Out.Intensity)
        || !Snapshot->TryGetStringField(TEXT("intensity_unit"), Out.IntensityUnit)
        || !ReadColor(Snapshot, TEXT("color_rgb"), Out.Color)
        || !Snapshot->TryGetBoolField(TEXT("use_temperature"), Out.bUseTemperature)
        || !Snapshot->TryGetNumberField(TEXT("temperature_kelvin"), Out.TemperatureKelvin)
        || !Snapshot->TryGetBoolField(TEXT("cast_shadows"), Out.bCastShadows)
        || !ReadOptionalNumber(Snapshot, TEXT("attenuation_radius_cm"), Out.AttenuationRadiusCm)
        || !ReadOptionalNumber(Snapshot, TEXT("inner_cone_deg"), Out.InnerConeDeg)
        || !ReadOptionalNumber(Snapshot, TEXT("outer_cone_deg"), Out.OuterConeDeg)
        || !FMath::IsFinite(Out.Intensity)
        || !FMath::IsFinite(Out.TemperatureKelvin))
    {
        return false;
    }
    return true;
}

bool ReadRigState(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field,
    FRenderMasterRigLightState& Out)
{
    TSharedPtr<FJsonObject> State;
    return ReadObject(Parent, Field, State)
        && ReadVector(State, TEXT("location_cm"), Out.Location)
        && ReadLightSnapshot(State, TEXT("light"), Out.Light);
}

bool ReadTransformSnapshot(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field,
    FTransform& Out)
{
    TSharedPtr<FJsonObject> Transform;
    FVector Location;
    FRotator Rotation;
    FVector Scale;
    if (!ReadObject(Parent, Field, Transform)
        || !ReadVector(Transform, TEXT("location_cm"), Location)
        || !ReadRotator(Transform, TEXT("rotation_deg"), Rotation)
        || !ReadVector(Transform, TEXT("scale"), Scale))
    {
        return false;
    }
    Out = FTransform(Rotation, Location, Scale);
    return true;
}

bool OptionalNumbersMatch(
    const TOptional<double>& A,
    const TOptional<double>& B)
{
    if (A.IsSet() != B.IsSet()) return false;
    return !A.IsSet()
        || FMath::IsNearlyEqual(A.GetValue(), B.GetValue(), RigValueTolerance);
}

bool RotationsMatch(const FRotator& A, const FRotator& B)
{
    return FMath::Abs(FMath::FindDeltaAngleDegrees(A.Roll, B.Roll)) <= RigTransformTolerance
        && FMath::Abs(FMath::FindDeltaAngleDegrees(A.Pitch, B.Pitch)) <= RigTransformTolerance
        && FMath::Abs(FMath::FindDeltaAngleDegrees(A.Yaw, B.Yaw)) <= RigTransformTolerance;
}

bool ColorsMatch(const FLinearColor& A, const FLinearColor& B)
{
    return FMath::IsNearlyEqual(A.R, B.R, 0.0041)
        && FMath::IsNearlyEqual(A.G, B.G, 0.0041)
        && FMath::IsNearlyEqual(A.B, B.B, 0.0041);
}

bool LocationsMatch(const FVector& A, const FVector& B)
{
    return A.Equals(B, RigTransformTolerance);
}

bool TransformsMatch(const FTransform& A, const FTransform& B)
{
    return LocationsMatch(A.GetLocation(), B.GetLocation())
        && RotationsMatch(A.Rotator(), B.Rotator())
        && A.GetScale3D().Equals(B.GetScale3D(), RigTransformTolerance);
}

bool RigStatesMatch(
    const FRenderMasterRigLightState& A,
    const FRenderMasterRigLightState& B)
{
    return LocationsMatch(A.Location, B.Location)
        && RenderMasterLightSnapshotsMatch(A.Light, B.Light);
}

bool IsBoundedLocation(const FVector& Value)
{
    return !Value.ContainsNaN()
        && FMath::Abs(Value.X) <= MaxRigCoordinateCm
        && FMath::Abs(Value.Y) <= MaxRigCoordinateCm
        && FMath::Abs(Value.Z) <= MaxRigCoordinateCm;
}

FString MobilityText(const USceneComponent* Component)
{
    if (Component == nullptr) return TEXT("none");
    switch (Component->Mobility)
    {
        case EComponentMobility::Static: return TEXT("static");
        case EComponentMobility::Stationary: return TEXT("stationary");
        case EComponentMobility::Movable: return TEXT("movable");
        default: return TEXT("none");
    }
}

FString CameraKind(const ACameraActor* CameraActor)
{
    return Cast<ACineCameraActor>(CameraActor) != nullptr
        ? TEXT("cine_camera")
        : TEXT("camera");
}

FString MissingCapabilitiesText(const TSharedPtr<FJsonObject>& Root)
{
    const TArray<TSharedPtr<FJsonValue>>* Missing = nullptr;
    TArray<FString> Values;
    if (Root.IsValid()
        && Root->TryGetArrayField(TEXT("missing_capabilities"), Missing)
        && Missing != nullptr)
    {
        for (const TSharedPtr<FJsonValue>& Value : *Missing)
        {
            FString Item;
            if (Value.IsValid() && Value->TryGetString(Item) && !Item.IsEmpty())
                Values.Add(Item);
        }
    }
    return FString::Join(Values, TEXT(", "));
}

bool JsonObjectText(const TSharedPtr<FJsonObject>& Object, FString& Out)
{
    if (!Object.IsValid()) return false;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
    return FJsonSerializer::Serialize(Object.ToSharedRef(), Writer);
}

TSharedRef<FJsonObject> VectorJson(const FVector& Value)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetNumberField(TEXT("x"), Value.X);
    Json->SetNumberField(TEXT("y"), Value.Y);
    Json->SetNumberField(TEXT("z"), Value.Z);
    return Json;
}

TSharedRef<FJsonObject> ColorJson(const FLinearColor& Value)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetNumberField(TEXT("r"), Value.R);
    Json->SetNumberField(TEXT("g"), Value.G);
    Json->SetNumberField(TEXT("b"), Value.B);
    return Json;
}

void SetOptionalNumber(
    const TSharedRef<FJsonObject>& Json,
    const TCHAR* Field,
    const TOptional<double>& Value)
{
    if (Value.IsSet()) Json->SetNumberField(Field, Value.GetValue());
    else Json->SetField(Field, MakeShared<FJsonValueNull>());
}

TSharedRef<FJsonObject> LightSnapshotJson(const FRenderMasterLightSnapshot& Snapshot)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetObjectField(
        TEXT("rotation_deg"),
        VectorJson(FVector(Snapshot.Rotation.Roll, Snapshot.Rotation.Pitch, Snapshot.Rotation.Yaw)));
    Json->SetNumberField(TEXT("intensity"), Snapshot.Intensity);
    Json->SetStringField(TEXT("intensity_unit"), Snapshot.IntensityUnit);
    Json->SetObjectField(TEXT("color_rgb"), ColorJson(Snapshot.Color));
    Json->SetBoolField(TEXT("use_temperature"), Snapshot.bUseTemperature);
    Json->SetNumberField(TEXT("temperature_kelvin"), Snapshot.TemperatureKelvin);
    Json->SetBoolField(TEXT("cast_shadows"), Snapshot.bCastShadows);
    SetOptionalNumber(Json, TEXT("attenuation_radius_cm"), Snapshot.AttenuationRadiusCm);
    SetOptionalNumber(Json, TEXT("inner_cone_deg"), Snapshot.InnerConeDeg);
    SetOptionalNumber(Json, TEXT("outer_cone_deg"), Snapshot.OuterConeDeg);
    return Json;
}

TSharedRef<FJsonObject> TransformJson(const FTransform& Transform)
{
    const FRotator Rotation = Transform.Rotator();
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetObjectField(TEXT("location_cm"), VectorJson(Transform.GetLocation()));
    Json->SetObjectField(
        TEXT("rotation_deg"),
        VectorJson(FVector(Rotation.Roll, Rotation.Pitch, Rotation.Yaw)));
    Json->SetObjectField(TEXT("scale"), VectorJson(Transform.GetScale3D()));
    return Json;
}

TSet<FString> ChangedRigProperties(
    const FRenderMasterRigLightState& Before,
    const FRenderMasterRigLightState& After)
{
    TSet<FString> Changed;
    if (!LocationsMatch(Before.Location, After.Location)) Changed.Add(TEXT("location"));
    if (!RotationsMatch(Before.Light.Rotation, After.Light.Rotation)) Changed.Add(TEXT("rotation"));
    if (!FMath::IsNearlyEqual(Before.Light.Intensity, After.Light.Intensity, RigValueTolerance))
        Changed.Add(TEXT("intensity"));
    if (Before.Light.bUseTemperature != After.Light.bUseTemperature)
        Changed.Add(TEXT("use_temperature"));
    if (!FMath::IsNearlyEqual(
            Before.Light.TemperatureKelvin,
            After.Light.TemperatureKelvin,
            RigValueTolerance))
        Changed.Add(TEXT("temperature_kelvin"));
    if (!OptionalNumbersMatch(
            Before.Light.AttenuationRadiusCm,
            After.Light.AttenuationRadiusCm))
        Changed.Add(TEXT("attenuation_radius_cm"));
    if (!OptionalNumbersMatch(Before.Light.InnerConeDeg, After.Light.InnerConeDeg))
        Changed.Add(TEXT("inner_cone_deg"));
    if (!OptionalNumbersMatch(Before.Light.OuterConeDeg, After.Light.OuterConeDeg))
        Changed.Add(TEXT("outer_cone_deg"));
    return Changed;
}

bool SetsMatch(const TSet<FString>& A, const TSet<FString>& B)
{
    if (A.Num() != B.Num()) return false;
    for (const FString& Value : A) if (!B.Contains(Value)) return false;
    return true;
}

FString ChangeSummary(const TSet<FString>& Changes)
{
    static const TArray<FString> Order = {
        TEXT("location"), TEXT("rotation"), TEXT("intensity"),
        TEXT("use_temperature"), TEXT("temperature_kelvin"),
        TEXT("attenuation_radius_cm"), TEXT("inner_cone_deg"), TEXT("outer_cone_deg")};
    TArray<FString> Values;
    for (const FString& Name : Order) if (Changes.Contains(Name)) Values.Add(Name);
    return Values.IsEmpty() ? TEXT("No change") : FString::Join(Values, TEXT(", "));
}

bool IsAllowedStyle(const FString& Value, std::initializer_list<const TCHAR*> Allowed)
{
    for (const TCHAR* Item : Allowed) if (Value == Item) return true;
    return false;
}
}

bool FRenderMasterLightingRigProposal::LoadFromFile(
    const FString& Filename,
    FRenderMasterLightingRigProposal& OutProposal,
    FString& OutError)
{
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *Filename))
    {
        OutError = FString::Printf(TEXT("Could not read lighting-rig proposal: %s"), *Filename);
        return false;
    }
    return Parse(JsonText, OutProposal, OutError);
}

bool FRenderMasterLightingRigProposal::Parse(
    const FString& JsonText,
    FRenderMasterLightingRigProposal& OutProposal,
    FString& OutError)
{
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        OutError = TEXT("Lighting-rig proposal is not valid JSON.");
        return false;
    }

    FRenderMasterLightingRigProposal Parsed;
    bool bModifies = false;
    bool bAutoSave = true;
    bool bUndo = false;
    if (!Root->TryGetStringField(TEXT("proposal_id"), Parsed.ProposalId)
        || !Root->TryGetStringField(TEXT("status"), Parsed.Status)
        || !Root->TryGetStringField(TEXT("request"), Parsed.Request)
        || !Root->TryGetStringField(TEXT("rationale"), Parsed.Rationale)
        || !Root->TryGetStringField(TEXT("contrast"), Parsed.Contrast)
        || !Root->TryGetStringField(TEXT("palette"), Parsed.Palette)
        || !Root->TryGetStringField(TEXT("key_side"), Parsed.KeySide)
        || !Root->TryGetStringField(TEXT("spacing"), Parsed.Spacing)
        || !Root->TryGetStringField(TEXT("brightness"), Parsed.Brightness)
        || !Root->TryGetBoolField(TEXT("modifies_editor_scene"), bModifies)
        || !Root->TryGetBoolField(TEXT("auto_save"), bAutoSave)
        || !Root->TryGetBoolField(TEXT("undo_supported"), bUndo)
        || Parsed.ProposalId.IsEmpty() || Parsed.Request.IsEmpty() || Parsed.Rationale.IsEmpty()
        || !bModifies || bAutoSave || !bUndo
        || !IsAllowedStyle(Parsed.Contrast, {TEXT("soft"), TEXT("balanced"), TEXT("dramatic")})
        || !IsAllowedStyle(Parsed.Palette, {TEXT("preserve"), TEXT("neutral"), TEXT("warm_cool"), TEXT("cool_warm")})
        || !IsAllowedStyle(Parsed.KeySide, {TEXT("camera_left"), TEXT("camera_right")})
        || !IsAllowedStyle(Parsed.Spacing, {TEXT("tight"), TEXT("standard"), TEXT("wide")})
        || !IsAllowedStyle(Parsed.Brightness, {TEXT("dim"), TEXT("balanced"), TEXT("bright")}))
    {
        OutError = TEXT("Lighting-rig proposal metadata or safety flags are invalid.");
        return false;
    }
    Parsed.MissingCapabilities = MissingCapabilitiesText(Root);

    TSharedPtr<FJsonObject> Context;
    TSharedPtr<FJsonObject> Subject;
    TSharedPtr<FJsonObject> SubjectBounds;
    TSharedPtr<FJsonObject> Camera;
    const TArray<TSharedPtr<FJsonValue>>* SelectedLights = nullptr;
    if (!ReadObject(Root, TEXT("context"), Context)
        || !ReadObject(Context, TEXT("subject"), Subject)
        || !ReadObject(Subject, TEXT("bounds"), SubjectBounds)
        || !ReadObject(Context, TEXT("camera"), Camera)
        || !Context->TryGetArrayField(TEXT("lights"), SelectedLights)
        || SelectedLights == nullptr || SelectedLights->Num() != 3
        || !Subject->TryGetStringField(TEXT("actor_path"), Parsed.SubjectActorPath)
        || !ReadTransformSnapshot(Subject, TEXT("transform"), Parsed.SubjectTransform)
        || !ReadVector(SubjectBounds, TEXT("center_cm"), Parsed.SubjectBoundsCenter)
        || !ReadVector(SubjectBounds, TEXT("extent_cm"), Parsed.SubjectBoundsExtent)
        || !SubjectBounds->TryGetNumberField(
            TEXT("sphere_radius_cm"), Parsed.SubjectBoundsRadiusCm)
        || !Camera->TryGetStringField(TEXT("actor_path"), Parsed.CameraActorPath)
        || !ReadVector(Camera, TEXT("location_cm"), Parsed.CameraLocation)
        || !ReadRotator(Camera, TEXT("rotation_deg"), Parsed.CameraRotation)
        || Parsed.SubjectActorPath.IsEmpty() || Parsed.CameraActorPath.IsEmpty()
        || !FMath::IsFinite(Parsed.SubjectBoundsRadiusCm)
        || Parsed.SubjectBoundsRadiusCm <= 0.0)
    {
        OutError = TEXT("Lighting-rig subject, camera, or selection evidence is incomplete.");
        return false;
    }
    Subject->TryGetStringField(TEXT("actor_guid"), Parsed.SubjectActorGuid);
    Camera->TryGetStringField(TEXT("actor_guid"), Parsed.CameraActorGuid);

    const TArray<TSharedPtr<FJsonValue>>* Actions = nullptr;
    if (!Root->TryGetArrayField(TEXT("actions"), Actions) || Actions == nullptr)
    {
        OutError = TEXT("Lighting-rig proposal is missing its action array.");
        return false;
    }
    if (Parsed.Status == TEXT("unresolved"))
    {
        if (!Actions->IsEmpty() || Parsed.MissingCapabilities.IsEmpty())
        {
            OutError = TEXT("Unresolved lighting rigs require a capability gap and no actions.");
            return false;
        }
        OutProposal = MoveTemp(Parsed);
        return true;
    }
    if (Parsed.Status != TEXT("proposed")
        || Actions->Num() != 3
        || !Parsed.MissingCapabilities.IsEmpty())
    {
        OutError = TEXT("Proposed lighting rigs require exactly three actions and no gap.");
        return false;
    }

    TSet<FString> Roles;
    bool bAnyChanged = false;
    for (int32 Index = 0; Index < 3; ++Index)
    {
        const TSharedPtr<FJsonObject>* Selected = nullptr;
        const TSharedPtr<FJsonObject>* Action = nullptr;
        TSharedPtr<FJsonObject> ActionTarget;
        if (!(*SelectedLights)[Index].IsValid()
            || !(*SelectedLights)[Index]->TryGetObject(Selected) || Selected == nullptr
            || !(*Actions)[Index].IsValid()
            || !(*Actions)[Index]->TryGetObject(Action) || Action == nullptr
            || !ReadObject(*Action, TEXT("target"), ActionTarget))
        {
            OutError = TEXT("Lighting-rig action contains invalid target evidence.");
            return false;
        }
        FString SelectedText;
        FString TargetText;
        if (!JsonObjectText(*Selected, SelectedText)
            || !JsonObjectText(ActionTarget, TargetText)
            || SelectedText != TargetText)
        {
            OutError = TEXT("Lighting-rig action target does not match ordered context evidence.");
            return false;
        }

        FRenderMasterLightingRigAction ParsedAction;
        TSharedPtr<FJsonObject> LightTarget;
        FRenderMasterRigLightState TargetState;
        if (!(*Action)->TryGetStringField(TEXT("role"), ParsedAction.Role)
            || !ReadObject(ActionTarget, TEXT("target"), LightTarget))
        {
            OutError = TEXT("Lighting-rig action role or nested target is incomplete.");
            return false;
        }
        if (!LightTarget.IsValid()
            || !LightTarget->TryGetStringField(TEXT("actor_name"), ParsedAction.ActorName)
            || !LightTarget->TryGetStringField(TEXT("actor_path"), ParsedAction.ActorPath)
            || !LightTarget->TryGetStringField(TEXT("actor_class"), ParsedAction.ActorClass)
            || !LightTarget->TryGetStringField(TEXT("component_name"), ParsedAction.ComponentName)
            || !LightTarget->TryGetStringField(TEXT("component_mobility"), ParsedAction.ComponentMobility)
            || !LightTarget->TryGetStringField(TEXT("light_kind"), ParsedAction.LightKind)
            || !ReadVector(ActionTarget, TEXT("location_cm"), TargetState.Location)
            || !ReadLightSnapshot(LightTarget, TEXT("light"), TargetState.Light)
            || !ReadRigState(*Action, TEXT("before"), ParsedAction.Before)
            || !ReadRigState(*Action, TEXT("after"), ParsedAction.After))
        {
            OutError = TEXT("Lighting-rig action identity or Before/After evidence is incomplete.");
            return false;
        }
        LightTarget->TryGetStringField(TEXT("actor_guid"), ParsedAction.ActorGuid);
        ParsedAction.Role.ToLowerInline();
        ParsedAction.LightKind.ToLowerInline();
        ParsedAction.ComponentMobility.ToLowerInline();
        if (!IsAllowedStyle(ParsedAction.Role, {TEXT("key"), TEXT("fill"), TEXT("rim")})
            || Roles.Contains(ParsedAction.Role)
            || ParsedAction.LightKind == TEXT("directional")
            || ParsedAction.ComponentMobility != TEXT("movable")
            || TargetState.Light.IntensityUnit == TEXT("ev")
            || !RigStatesMatch(TargetState, ParsedAction.Before)
            || !IsBoundedLocation(ParsedAction.Before.Location)
            || !IsBoundedLocation(ParsedAction.After.Location)
            || !RenderMasterIsBoundedLightSnapshot(
                ParsedAction.Before.Light, ParsedAction.LightKind)
            || !RenderMasterIsBoundedLightSnapshot(
                ParsedAction.After.Light, ParsedAction.LightKind)
            || ParsedAction.Before.Light.IntensityUnit != ParsedAction.After.Light.IntensityUnit
            || !ColorsMatch(ParsedAction.Before.Light.Color, ParsedAction.After.Light.Color)
            || ParsedAction.Before.Light.bCastShadows != ParsedAction.After.Light.bCastShadows)
        {
            OutError = TEXT("Lighting-rig role or bounded action evidence is invalid.");
            return false;
        }
        Roles.Add(ParsedAction.Role);

        const TArray<TSharedPtr<FJsonValue>>* Changes = nullptr;
        if (!(*Action)->TryGetArrayField(TEXT("changes"), Changes) || Changes == nullptr)
        {
            OutError = TEXT("Lighting-rig action is missing its changed-property list.");
            return false;
        }
        TSet<FString> Declared;
        for (const TSharedPtr<FJsonValue>& ChangeValue : *Changes)
        {
            const TSharedPtr<FJsonObject>* Change = nullptr;
            FString Property;
            FString Operation;
            if (!ChangeValue.IsValid() || !ChangeValue->TryGetObject(Change) || Change == nullptr
                || !(*Change)->TryGetStringField(TEXT("property"), Property)
                || !(*Change)->TryGetStringField(TEXT("operation"), Operation)
                || Operation != TEXT("set") || Declared.Contains(Property)
                || !IsAllowedStyle(Property, {
                    TEXT("location"), TEXT("rotation"), TEXT("intensity"),
                    TEXT("use_temperature"), TEXT("temperature_kelvin"),
                    TEXT("attenuation_radius_cm"), TEXT("inner_cone_deg"),
                    TEXT("outer_cone_deg")}))
            {
                OutError = TEXT("Lighting-rig changed-property record is invalid.");
                return false;
            }
            Declared.Add(Property);
        }
        const TSet<FString> Actual = ChangedRigProperties(
            ParsedAction.Before,
            ParsedAction.After);
        if (!SetsMatch(Declared, Actual))
        {
            OutError = TEXT("Lighting-rig changed-property list does not match Before/After evidence.");
            return false;
        }
        ParsedAction.ChangeSummary = ChangeSummary(Actual);
        bAnyChanged |= !Actual.IsEmpty();
        Parsed.Actions.Add(MoveTemp(ParsedAction));
    }
    if (Roles.Num() != 3 || !Roles.Contains(TEXT("key"))
        || !Roles.Contains(TEXT("fill")) || !Roles.Contains(TEXT("rim")) || !bAnyChanged)
    {
        OutError = TEXT("Lighting-rig proposal must contain changed Key, Fill, and Rim actions.");
        return false;
    }
    OutProposal = MoveTemp(Parsed);
    return true;
}

bool RenderMasterApplyLightingRig(
    const TArray<ALight*>& LightActors,
    const TArray<ULightComponent*>& LightComponents,
    const TArray<FRenderMasterRigLightState>& Before,
    const TArray<FRenderMasterRigLightState>& After,
    FString& OutError,
    bool bMarkPackageDirty)
{
    if (LightActors.Num() != 3 || LightComponents.Num() != 3
        || Before.Num() != 3 || After.Num() != 3)
    {
        OutError = TEXT("A lighting rig requires exactly three consistent light arrays.");
        return false;
    }
    TSet<ALight*> UniqueActors;
    TSet<ULightComponent*> UniqueComponents;
    bool bAnyChanged = false;
    for (int32 Index = 0; Index < 3; ++Index)
    {
        ALight* Actor = LightActors[Index];
        ULightComponent* Component = LightComponents[Index];
        const FString Kind = RenderMasterGetLightKind(Component);
        if (Actor == nullptr || Component == nullptr
            || Kind.IsEmpty() || Kind == TEXT("directional")
            || Component->Mobility != EComponentMobility::Movable
            || UniqueActors.Contains(Actor) || UniqueComponents.Contains(Component)
            || !IsBoundedLocation(Before[Index].Location)
            || !IsBoundedLocation(After[Index].Location)
            || !RenderMasterIsBoundedLightSnapshot(Before[Index].Light, Kind)
            || !RenderMasterIsBoundedLightSnapshot(After[Index].Light, Kind)
            || Before[Index].Light.IntensityUnit != After[Index].Light.IntensityUnit
            || Before[Index].Light.IntensityUnit == TEXT("ev"))
        {
            OutError = TEXT("Lighting rig contains an invalid, repeated, or unbounded light.");
            return false;
        }
        UniqueActors.Add(Actor);
        UniqueComponents.Add(Component);
        bAnyChanged |= !RigStatesMatch(Before[Index], After[Index]);
    }
    if (!bAnyChanged)
    {
        OutError = TEXT("Lighting-rig action does not change any light.");
        return false;
    }

    FScopedTransaction Transaction(
        NSLOCTEXT("RenderMasterBot", "ApplyAssistantLightingRig", "RenderMasterBot: Apply Three-Point Lighting Rig"));
    TArray<FRenderMasterRigLightState> Originals;
    Originals.Reserve(3);
    for (int32 Index = 0; Index < 3; ++Index)
    {
        FRenderMasterRigLightState Original;
        Original.Location = LightActors[Index]->GetActorLocation();
        Original.Light = RenderMasterSnapshotLight(LightActors[Index], LightComponents[Index]);
        Originals.Add(Original);
        if (RigStatesMatch(Before[Index], After[Index])) continue;
        LightActors[Index]->Modify();
        LightComponents[Index]->Modify();
    }

    for (int32 Index = 0; Index < 3; ++Index)
    {
        if (RigStatesMatch(Before[Index], After[Index])) continue;
        const bool bMoved = LightActors[Index]->SetActorLocation(
            After[Index].Location,
            false,
            nullptr,
            ETeleportType::TeleportPhysics);
        if (!bMoved || !RenderMasterSetLightSnapshot(
                LightActors[Index],
                LightComponents[Index],
                Before[Index].Light,
                After[Index].Light))
        {
            for (int32 RestoreIndex = 0; RestoreIndex <= Index; ++RestoreIndex)
            {
                if (RigStatesMatch(Before[RestoreIndex], After[RestoreIndex])) continue;
                const FRenderMasterLightSnapshot Current = RenderMasterSnapshotLight(
                    LightActors[RestoreIndex], LightComponents[RestoreIndex]);
                LightActors[RestoreIndex]->SetActorLocation(
                    Originals[RestoreIndex].Location,
                    false,
                    nullptr,
                    ETeleportType::TeleportPhysics);
                RenderMasterSetLightSnapshot(
                    LightActors[RestoreIndex],
                    LightComponents[RestoreIndex],
                    Current,
                    Originals[RestoreIndex].Light);
                LightComponents[RestoreIndex]->PostEditChange();
                LightComponents[RestoreIndex]->MarkRenderStateDirty();
                LightActors[RestoreIndex]->PostEditMove(true);
            }
            Transaction.Cancel();
            OutError = FString::Printf(
                TEXT("Unreal rejected the rig Transform for %s; prior rig edits were restored."),
                *LightActors[Index]->GetActorLabel());
            return false;
        }
    }

    for (int32 Index = 0; Index < 3; ++Index)
    {
        if (RigStatesMatch(Before[Index], After[Index])) continue;
        LightComponents[Index]->PostEditChange();
        LightComponents[Index]->MarkRenderStateDirty();
        LightActors[Index]->PostEditMove(true);
        if (bMarkPackageDirty) LightActors[Index]->MarkPackageDirty();
    }
    if (GEditor != nullptr) GEditor->RedrawLevelEditingViewports();
    return true;
}

FRenderMasterLightingRigAssistant::FRenderMasterLightingRigAssistant(
    TSharedPtr<FRenderMasterWorkflowController> InWorkflowController)
    : WorkflowController(MoveTemp(InWorkflowController))
{
}

FRenderMasterLightingRigAssistant::~FRenderMasterLightingRigAssistant()
{
    Shutdown();
}

void FRenderMasterLightingRigAssistant::Initialize()
{
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateSP(AsShared(), &FRenderMasterLightingRigAssistant::Tick),
        0.2f);
}

void FRenderMasterLightingRigAssistant::Shutdown()
{
    if (TickHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
        TickHandle.Reset();
    }
    if (ProcessHandle.IsValid()) FPlatformProcess::TerminateProc(ProcessHandle, true);
    CloseProcessResources();
}

bool FRenderMasterLightingRigAssistant::StartProposal(
    const FString& Prompt,
    AActor* SubjectActor,
    ACameraActor* CameraActor,
    const TArray<ALight*>& LightActors)
{
    if (!CanStart()) return false;
    const FString CleanPrompt = Prompt.TrimStartAndEnd();
    if (CleanPrompt.IsEmpty())
    {
        Fail(TEXT("Enter a three-point-lighting request before preparing a rig."));
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
        Fail(TEXT("Configure an existing Python executable and workflow root in Render & Evaluate."));
        return false;
    }
    if (Python.Contains(TEXT("\"")) || Root.Contains(TEXT("\"")))
    {
        Fail(TEXT("Assistant runtime paths cannot contain a double quote."));
        return false;
    }

    const FString ProposalId = FString::Printf(
        TEXT("lighting_rig_%s"),
        *FDateTime::Now().ToString(TEXT("%Y%m%d_%H%M%S_%s")));
    const FString RequestDirectory = FPaths::Combine(
        Root, TEXT("assistant-lighting-rig"), ProposalId);
    IFileManager::Get().MakeDirectory(*RequestDirectory, true);
    const FString PromptPath = FPaths::Combine(RequestDirectory, TEXT("request.txt"));
    const FString ContextPath = FPaths::Combine(RequestDirectory, TEXT("lighting_rig_context.json"));
    ProposalOutputPath = FPaths::Combine(RequestDirectory, TEXT("lighting_rig_proposal.json"));

    FString Error;
    if (!FFileHelper::SaveStringToFile(
            CleanPrompt,
            *PromptPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)
        || !WriteContext(ContextPath, SubjectActor, CameraActor, LightActors, Error))
    {
        Fail(Error.IsEmpty() ? TEXT("Could not write the lighting-rig request.") : Error);
        return false;
    }

    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-lighting-rig-propose --prompt-file %s --context %s --proposal-id %s --output %s"),
        *QuoteRigArgument(PromptPath),
        *QuoteRigArgument(ContextPath),
        *QuoteRigArgument(ProposalId),
        *QuoteRigArgument(ProposalOutputPath));
    Proposal = FRenderMasterLightingRigProposal();
    ErrorText.Reset();
    ProcessLog.Reset();
    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        Fail(TEXT("Could not create lighting-rig assistant process pipes."));
        return false;
    }
    uint32 ProcessId = 0;
    ProcessHandle = FPlatformProcess::CreateProc(
        *Python, *Arguments, false, true, true, &ProcessId, 0, nullptr,
        StdOutWrite, nullptr, StdErrWrite);
    if (!ProcessHandle.IsValid())
    {
        CloseProcessResources();
        Fail(FString::Printf(TEXT("Could not start Python: %s"), *Python));
        return false;
    }
    State = ERenderMasterLightingRigAssistantState::Planning;
    AppendLog(FString::Printf(
        TEXT("Preparing one bounded Key/Fill/Rim proposal (process %u). Nothing is being changed."),
        ProcessId));
    return true;
}

bool FRenderMasterLightingRigAssistant::WriteContext(
    const FString& Filename,
    AActor* SubjectActor,
    ACameraActor* CameraActor,
    const TArray<ALight*>& LightActors,
    FString& OutError)
{
    if (SubjectActor == nullptr || CameraActor == nullptr || LightActors.Num() != 3)
    {
        OutError = TEXT("Select one subject Actor, one Camera, and exactly three local Lights.");
        return false;
    }
    UWorld* World = SubjectActor->GetWorld();
    USceneComponent* SubjectRoot = SubjectActor->GetRootComponent();
    UCameraComponent* CameraComponent = CameraActor->GetCameraComponent();
    if (World == nullptr || SubjectRoot == nullptr || CameraComponent == nullptr
        || CameraActor->GetWorld() != World
        || CameraComponent->ProjectionMode != ECameraProjectionMode::Perspective
        || !SubjectActor->IsEditable() || SubjectActor->IsLockLocation()
        || !CameraActor->IsEditable() || CameraActor->IsLockLocation())
    {
        OutError = TEXT("The subject and perspective Camera must be valid, editable, unlocked, and in one level.");
        return false;
    }
    FVector BoundsCenter;
    FVector BoundsExtent;
    SubjectActor->GetActorBounds(false, BoundsCenter, BoundsExtent, true);
    const double BoundsRadius = BoundsExtent.Size();
    if (!IsBoundedLocation(BoundsCenter) || BoundsExtent.ContainsNaN()
        || BoundsExtent.GetMin() < 0.0 || !FMath::IsFinite(BoundsRadius)
        || BoundsRadius <= RigValueTolerance)
    {
        OutError = TEXT("The selected subject has no usable world-space bounds.");
        return false;
    }

    Subject = FRenderMasterRigSubjectCapture();
    Subject.Actor = SubjectActor;
    Subject.RootComponent = SubjectRoot;
    Subject.ActorName = SubjectActor->GetActorLabel();
    Subject.ActorPath = SubjectActor->GetPathName();
    Subject.ActorClass = SubjectActor->GetClass()->GetName();
    Subject.ActorGuid = SubjectActor->GetActorGuid().IsValid()
        ? SubjectActor->GetActorGuid().ToString(EGuidFormats::Digits) : FString();
    Subject.RootComponentName = SubjectRoot->GetName();
    Subject.RootMobility = MobilityText(SubjectRoot);
    Subject.Transform = SubjectActor->GetActorTransform();
    Subject.BoundsCenter = BoundsCenter;
    Subject.BoundsExtent = BoundsExtent;
    Subject.BoundsRadiusCm = BoundsRadius;

    Camera = FRenderMasterRigCameraCapture();
    Camera.Actor = CameraActor;
    Camera.Component = CameraComponent;
    Camera.ActorName = CameraActor->GetActorLabel();
    Camera.ActorPath = CameraActor->GetPathName();
    Camera.ActorClass = CameraActor->GetClass()->GetName();
    Camera.ActorGuid = CameraActor->GetActorGuid().IsValid()
        ? CameraActor->GetActorGuid().ToString(EGuidFormats::Digits) : FString();
    Camera.ComponentName = CameraComponent->GetName();
    Camera.CameraKind = CameraKind(CameraActor);
    Camera.ComponentMobility = MobilityText(CameraComponent);
    Camera.Location = CameraActor->GetActorLocation();
    Camera.Rotation = CameraActor->GetActorRotation();

    TArray<ALight*> SortedLights = LightActors;
    SortedLights.Sort([](const ALight& Left, const ALight& Right)
    {
        return Left.GetPathName() < Right.GetPathName();
    });
    TSet<FString> ActorPaths = {Subject.ActorPath, Camera.ActorPath};
    TSet<FString> ActorGuids;
    if (!Subject.ActorGuid.IsEmpty()) ActorGuids.Add(Subject.ActorGuid);
    if (!Camera.ActorGuid.IsEmpty()) ActorGuids.Add(Camera.ActorGuid);
    FString SharedUnit;
    double MaxIntensity = 0.0;
    Lights.Reset();
    TArray<TSharedPtr<FJsonValue>> LightValues;
    for (ALight* LightActor : SortedLights)
    {
        ULightComponent* Component = LightActor != nullptr ? LightActor->GetLightComponent() : nullptr;
        const FString Kind = RenderMasterGetLightKind(Component);
        const FString Unit = RenderMasterGetLightUnit(Component);
        const FString Path = LightActor != nullptr ? LightActor->GetPathName() : FString();
        const FString Guid = LightActor != nullptr && LightActor->GetActorGuid().IsValid()
            ? LightActor->GetActorGuid().ToString(EGuidFormats::Digits) : FString();
        if (LightActor == nullptr || Component == nullptr || LightActor->GetWorld() != World
            || Kind.IsEmpty() || Kind == TEXT("directional")
            || Component->Mobility != EComponentMobility::Movable
            || !LightActor->IsEditable() || LightActor->IsLockLocation()
            || Unit == TEXT("ev") || Unit.IsEmpty()
            || ActorPaths.Contains(Path)
            || (!Guid.IsEmpty() && ActorGuids.Contains(Guid)))
        {
            OutError = TEXT("Rig lights must be three unique, editable, unlocked, Movable Point/Spot/Rect Lights in the same level.");
            Lights.Reset();
            return false;
        }
        if (SharedUnit.IsEmpty()) SharedUnit = Unit;
        if (Unit != SharedUnit)
        {
            OutError = TEXT("All three rig lights must use the same non-EV intensity unit.");
            Lights.Reset();
            return false;
        }
        ActorPaths.Add(Path);
        if (!Guid.IsEmpty()) ActorGuids.Add(Guid);

        FRenderMasterRigLightCapture Captured;
        Captured.Target.Actor = LightActor;
        Captured.Target.Component = Component;
        Captured.Target.ActorName = LightActor->GetActorLabel();
        Captured.Target.ActorPath = Path;
        Captured.Target.ActorClass = LightActor->GetClass()->GetName();
        Captured.Target.ActorGuid = Guid;
        Captured.Target.ComponentName = Component->GetName();
        Captured.Target.ComponentMobility = MobilityText(Component);
        Captured.Target.LightKind = Kind;
        Captured.Target.Light = RenderMasterSnapshotLight(LightActor, Component);
        Captured.Location = LightActor->GetActorLocation();
        if (!RenderMasterIsBoundedLightSnapshot(Captured.Target.Light, Kind)
            || !IsBoundedLocation(Captured.Location))
        {
            OutError = TEXT("A selected rig light has properties outside the safety boundary.");
            Lights.Reset();
            return false;
        }
        MaxIntensity = FMath::Max(MaxIntensity, Captured.Target.Light.Intensity);

        TSharedRef<FJsonObject> Target = MakeShared<FJsonObject>();
        Target->SetStringField(TEXT("schema_version"), TEXT("0.1"));
        Target->SetStringField(TEXT("project_name"), FApp::GetProjectName());
        Target->SetStringField(TEXT("level_path"), World->GetPackage()->GetName());
        Target->SetStringField(TEXT("actor_name"), Captured.Target.ActorName);
        Target->SetStringField(TEXT("actor_path"), Captured.Target.ActorPath);
        Target->SetStringField(TEXT("actor_class"), Captured.Target.ActorClass);
        if (!Guid.IsEmpty()) Target->SetStringField(TEXT("actor_guid"), Guid);
        Target->SetStringField(TEXT("component_name"), Captured.Target.ComponentName);
        Target->SetStringField(TEXT("light_kind"), Kind);
        Target->SetStringField(TEXT("component_mobility"), TEXT("movable"));
        Target->SetBoolField(TEXT("is_editable"), true);
        Target->SetBoolField(TEXT("is_locked"), false);
        Target->SetObjectField(TEXT("light"), LightSnapshotJson(Captured.Target.Light));
        TSharedRef<FJsonObject> RigLight = MakeShared<FJsonObject>();
        RigLight->SetObjectField(TEXT("target"), Target);
        RigLight->SetObjectField(TEXT("location_cm"), VectorJson(Captured.Location));
        LightValues.Add(MakeShared<FJsonValueObject>(RigLight));
        Lights.Add(MoveTemp(Captured));
    }
    if (MaxIntensity <= 0.0)
    {
        OutError = TEXT("At least one selected rig light needs a positive captured intensity.");
        Lights.Reset();
        return false;
    }

    TSharedRef<FJsonObject> SubjectJson = MakeShared<FJsonObject>();
    SubjectJson->SetStringField(TEXT("actor_name"), Subject.ActorName);
    SubjectJson->SetStringField(TEXT("actor_path"), Subject.ActorPath);
    SubjectJson->SetStringField(TEXT("actor_class"), Subject.ActorClass);
    if (!Subject.ActorGuid.IsEmpty())
        SubjectJson->SetStringField(TEXT("actor_guid"), Subject.ActorGuid);
    SubjectJson->SetStringField(TEXT("root_component_name"), Subject.RootComponentName);
    SubjectJson->SetStringField(TEXT("root_mobility"), Subject.RootMobility);
    SubjectJson->SetBoolField(TEXT("is_editable"), true);
    SubjectJson->SetBoolField(TEXT("is_locked"), false);
    SubjectJson->SetObjectField(TEXT("transform"), TransformJson(Subject.Transform));
    TSharedRef<FJsonObject> BoundsJson = MakeShared<FJsonObject>();
    BoundsJson->SetObjectField(TEXT("center_cm"), VectorJson(Subject.BoundsCenter));
    BoundsJson->SetObjectField(TEXT("extent_cm"), VectorJson(Subject.BoundsExtent));
    BoundsJson->SetNumberField(TEXT("sphere_radius_cm"), Subject.BoundsRadiusCm);
    SubjectJson->SetObjectField(TEXT("bounds"), BoundsJson);

    TSharedRef<FJsonObject> CameraJson = MakeShared<FJsonObject>();
    CameraJson->SetStringField(TEXT("actor_name"), Camera.ActorName);
    CameraJson->SetStringField(TEXT("actor_path"), Camera.ActorPath);
    CameraJson->SetStringField(TEXT("actor_class"), Camera.ActorClass);
    if (!Camera.ActorGuid.IsEmpty())
        CameraJson->SetStringField(TEXT("actor_guid"), Camera.ActorGuid);
    CameraJson->SetStringField(TEXT("component_name"), Camera.ComponentName);
    CameraJson->SetStringField(TEXT("camera_kind"), Camera.CameraKind);
    CameraJson->SetStringField(TEXT("component_mobility"), Camera.ComponentMobility);
    CameraJson->SetStringField(TEXT("projection_mode"), TEXT("perspective"));
    CameraJson->SetBoolField(TEXT("is_editable"), true);
    CameraJson->SetBoolField(TEXT("is_locked"), false);
    CameraJson->SetObjectField(TEXT("location_cm"), VectorJson(Camera.Location));
    CameraJson->SetObjectField(
        TEXT("rotation_deg"),
        VectorJson(FVector(Camera.Rotation.Roll, Camera.Rotation.Pitch, Camera.Rotation.Yaw)));

    TSharedRef<FJsonObject> RootJson = MakeShared<FJsonObject>();
    RootJson->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    RootJson->SetStringField(TEXT("project_name"), FApp::GetProjectName());
    RootJson->SetStringField(TEXT("level_path"), World->GetPackage()->GetName());
    RootJson->SetObjectField(TEXT("subject"), SubjectJson);
    RootJson->SetObjectField(TEXT("camera"), CameraJson);
    RootJson->SetArrayField(TEXT("lights"), LightValues);
    FString JsonText;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&JsonText);
    if (!FJsonSerializer::Serialize(RootJson, Writer)
        || !FFileHelper::SaveStringToFile(
            JsonText, *Filename, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        OutError = FString::Printf(TEXT("Could not write lighting-rig context: %s"), *Filename);
        Lights.Reset();
        return false;
    }
    return true;
}

bool FRenderMasterLightingRigAssistant::ApplyProposal()
{
    FString Error;
    if (!CanApply() || !RevalidateTargets(Error))
    {
        Fail(Error.IsEmpty() ? TEXT("No lighting-rig proposal is ready to apply.") : Error);
        return false;
    }
    TArray<ALight*> Actors;
    TArray<ULightComponent*> Components;
    TArray<FRenderMasterRigLightState> Before;
    TArray<FRenderMasterRigLightState> After;
    for (int32 Index = 0; Index < 3; ++Index)
    {
        Actors.Add(Lights[Index].Target.Actor.Get());
        Components.Add(Lights[Index].Target.Component.Get());
        Before.Add(Proposal.Actions[Index].Before);
        After.Add(Proposal.Actions[Index].After);
    }
    if (!RenderMasterApplyLightingRig(Actors, Components, Before, After, Error))
    {
        Fail(Error);
        return false;
    }
    State = ERenderMasterLightingRigAssistantState::Applied;
    AppendLog(TEXT("Applied the approved Key/Fill/Rim rig in one Undo transaction. The level was not saved."));
    return true;
}

bool FRenderMasterLightingRigAssistant::GetAppliedRig(
    FRenderMasterAppliedLightingRig& OutRig,
    FString& OutError) const
{
    if (State != ERenderMasterLightingRigAssistantState::Applied
        || Proposal.Actions.Num() != 3 || Lights.Num() != 3
        || !ProposalMatchesCapturedEvidence(OutError))
    {
        if (OutError.IsEmpty())
            OutError = TEXT("Apply one reviewed three-point rig before requesting a visual review.");
        return false;
    }

    AActor* SubjectActor = Subject.Actor.Get();
    USceneComponent* SubjectRoot = Subject.RootComponent.Get();
    FVector CurrentBoundsCenter;
    FVector CurrentBoundsExtent;
    if (SubjectActor != nullptr)
        SubjectActor->GetActorBounds(false, CurrentBoundsCenter, CurrentBoundsExtent, true);
    const FString CurrentSubjectGuid = SubjectActor != nullptr
        && SubjectActor->GetActorGuid().IsValid()
        ? SubjectActor->GetActorGuid().ToString(EGuidFormats::Digits) : FString();
    if (SubjectActor == nullptr || SubjectRoot == nullptr
        || SubjectActor->GetRootComponent() != SubjectRoot
        || SubjectActor->GetActorLabel() != Subject.ActorName
        || SubjectActor->GetPathName() != Subject.ActorPath
        || SubjectActor->GetClass()->GetName() != Subject.ActorClass
        || CurrentSubjectGuid != Subject.ActorGuid
        || SubjectRoot->GetName() != Subject.RootComponentName
        || MobilityText(SubjectRoot) != Subject.RootMobility
        || !SubjectActor->IsEditable() || SubjectActor->IsLockLocation()
        || !TransformsMatch(SubjectActor->GetActorTransform(), Subject.Transform)
        || !LocationsMatch(CurrentBoundsCenter, Subject.BoundsCenter)
        || !LocationsMatch(CurrentBoundsExtent, Subject.BoundsExtent))
    {
        OutError = TEXT("The lighting subject changed after rig approval; prepare a new rig.");
        return false;
    }

    ACameraActor* CameraActor = Camera.Actor.Get();
    UCameraComponent* CameraComponent = Camera.Component.Get();
    const FString CurrentCameraGuid = CameraActor != nullptr
        && CameraActor->GetActorGuid().IsValid()
        ? CameraActor->GetActorGuid().ToString(EGuidFormats::Digits) : FString();
    if (CameraActor == nullptr || CameraComponent == nullptr
        || CameraActor->GetCameraComponent() != CameraComponent
        || CameraActor->GetActorLabel() != Camera.ActorName
        || CameraActor->GetPathName() != Camera.ActorPath
        || CameraActor->GetClass()->GetName() != Camera.ActorClass
        || CurrentCameraGuid != Camera.ActorGuid
        || CameraComponent->GetName() != Camera.ComponentName
        || MobilityText(CameraComponent) != Camera.ComponentMobility
        || CameraKind(CameraActor) != Camera.CameraKind
        || CameraComponent->ProjectionMode != ECameraProjectionMode::Perspective
        || !CameraActor->IsEditable() || CameraActor->IsLockLocation()
        || !LocationsMatch(CameraActor->GetActorLocation(), Camera.Location)
        || !RotationsMatch(CameraActor->GetActorRotation(), Camera.Rotation))
    {
        OutError = TEXT("The rig camera changed after approval; prepare a new rig.");
        return false;
    }

    FRenderMasterAppliedLightingRig Applied;
    Applied.Subject = Subject;
    Applied.Camera = Camera;
    Applied.SourceRequest = Proposal.Request;
    for (int32 Index = 0; Index < 3; ++Index)
    {
        const FRenderMasterRigLightCapture& Captured = Lights[Index];
        const FRenderMasterLightingRigAction& Action = Proposal.Actions[Index];
        ALight* Actor = Captured.Target.Actor.Get();
        ULightComponent* Component = Captured.Target.Component.Get();
        const FString CurrentGuid = Actor != nullptr && Actor->GetActorGuid().IsValid()
            ? Actor->GetActorGuid().ToString(EGuidFormats::Digits) : FString();
        FRenderMasterRigLightState Current;
        if (Actor != nullptr && Component != nullptr)
        {
            Current.Location = Actor->GetActorLocation();
            Current.Light = RenderMasterSnapshotLight(Actor, Component);
        }
        if (Actor == nullptr || Component == nullptr
            || Actor->GetLightComponent() != Component
            || Actor->GetActorLabel() != Captured.Target.ActorName
            || Actor->GetPathName() != Captured.Target.ActorPath
            || Actor->GetClass()->GetName() != Captured.Target.ActorClass
            || CurrentGuid != Captured.Target.ActorGuid
            || Component->GetName() != Captured.Target.ComponentName
            || MobilityText(Component) != Captured.Target.ComponentMobility
            || RenderMasterGetLightKind(Component) != Captured.Target.LightKind
            || !Actor->IsEditable() || Actor->IsLockLocation()
            || !RigStatesMatch(Current, Action.After))
        {
            OutError = FString::Printf(
                TEXT("%s changed after rig approval; prepare a new rig."),
                *Captured.Target.ActorName);
            return false;
        }
        FRenderMasterRigLightCapture CurrentCapture = Captured;
        CurrentCapture.Location = Current.Location;
        CurrentCapture.Target.Light = Current.Light;
        Applied.Lights.Add(MoveTemp(CurrentCapture));
        Applied.Roles.Add(Action.Role);
    }
    OutRig = MoveTemp(Applied);
    return true;
}

bool FRenderMasterLightingRigAssistant::ProposalMatchesCapturedEvidence(FString& OutError) const
{
    if (Proposal.SubjectActorPath != Subject.ActorPath
        || Proposal.SubjectActorGuid != Subject.ActorGuid
        || !TransformsMatch(Proposal.SubjectTransform, Subject.Transform)
        || !LocationsMatch(Proposal.SubjectBoundsCenter, Subject.BoundsCenter)
        || !LocationsMatch(Proposal.SubjectBoundsExtent, Subject.BoundsExtent)
        || !FMath::IsNearlyEqual(
            Proposal.SubjectBoundsRadiusCm, Subject.BoundsRadiusCm, RigTransformTolerance))
    {
        OutError = TEXT("Lighting-rig proposal subject evidence does not match the frozen subject.");
        return false;
    }
    if (Proposal.CameraActorPath != Camera.ActorPath
        || Proposal.CameraActorGuid != Camera.ActorGuid
        || !LocationsMatch(Proposal.CameraLocation, Camera.Location)
        || !RotationsMatch(Proposal.CameraRotation, Camera.Rotation))
    {
        OutError = TEXT("Lighting-rig proposal camera evidence does not match the frozen camera.");
        return false;
    }
    if (Proposal.Actions.Num() != Lights.Num())
    {
        OutError = TEXT("Lighting-rig proposal does not cover the frozen light selection.");
        return false;
    }
    for (int32 Index = 0; Index < Lights.Num(); ++Index)
    {
        const FRenderMasterRigLightCapture& Captured = Lights[Index];
        const FRenderMasterLightingRigAction& Action = Proposal.Actions[Index];
        FRenderMasterRigLightState CapturedState;
        CapturedState.Location = Captured.Location;
        CapturedState.Light = Captured.Target.Light;
        if (Action.ActorName != Captured.Target.ActorName
            || Action.ActorPath != Captured.Target.ActorPath
            || Action.ActorClass != Captured.Target.ActorClass
            || Action.ActorGuid != Captured.Target.ActorGuid
            || Action.ComponentName != Captured.Target.ComponentName
            || Action.ComponentMobility != Captured.Target.ComponentMobility
            || Action.LightKind != Captured.Target.LightKind
            || !RigStatesMatch(Action.Before, CapturedState))
        {
            OutError = FString::Printf(
                TEXT("Lighting-rig proposal evidence does not match frozen light %d."),
                Index + 1);
            return false;
        }
    }
    return true;
}

bool FRenderMasterLightingRigAssistant::RevalidateTargets(FString& OutError) const
{
    if (!ProposalMatchesCapturedEvidence(OutError)) return false;
    AActor* SubjectActor = Subject.Actor.Get();
    USceneComponent* SubjectRoot = Subject.RootComponent.Get();
    if (SubjectActor == nullptr || SubjectRoot == nullptr
        || SubjectActor->GetRootComponent() != SubjectRoot
        || SubjectActor->GetActorLabel() != Subject.ActorName
        || SubjectActor->GetPathName() != Subject.ActorPath
        || SubjectActor->GetClass()->GetName() != Subject.ActorClass
        || SubjectRoot->GetName() != Subject.RootComponentName
        || MobilityText(SubjectRoot) != Subject.RootMobility
        || !SubjectActor->IsEditable() || SubjectActor->IsLockLocation()
        || !TransformsMatch(SubjectActor->GetActorTransform(), Subject.Transform))
    {
        OutError = TEXT("The subject identity or Transform changed after planning. Nothing was applied.");
        return false;
    }
    const FString CurrentSubjectGuid = SubjectActor->GetActorGuid().IsValid()
        ? SubjectActor->GetActorGuid().ToString(EGuidFormats::Digits) : FString();
    FVector BoundsCenter;
    FVector BoundsExtent;
    SubjectActor->GetActorBounds(false, BoundsCenter, BoundsExtent, true);
    if (CurrentSubjectGuid != Subject.ActorGuid
        || !LocationsMatch(BoundsCenter, Subject.BoundsCenter)
        || !LocationsMatch(BoundsExtent, Subject.BoundsExtent)
        || !FMath::IsNearlyEqual(
            BoundsExtent.Size(), Subject.BoundsRadiusCm, RigTransformTolerance))
    {
        OutError = TEXT("The subject identity or bounds changed after planning. Nothing was applied.");
        return false;
    }

    ACameraActor* CameraActor = Camera.Actor.Get();
    UCameraComponent* CameraComponent = Camera.Component.Get();
    const FString CurrentCameraGuid = CameraActor != nullptr && CameraActor->GetActorGuid().IsValid()
        ? CameraActor->GetActorGuid().ToString(EGuidFormats::Digits) : FString();
    if (CameraActor == nullptr || CameraComponent == nullptr
        || CameraActor->GetCameraComponent() != CameraComponent
        || CameraActor->GetActorLabel() != Camera.ActorName
        || CameraActor->GetPathName() != Camera.ActorPath
        || CameraActor->GetClass()->GetName() != Camera.ActorClass
        || CurrentCameraGuid != Camera.ActorGuid
        || CameraComponent->GetName() != Camera.ComponentName
        || MobilityText(CameraComponent) != Camera.ComponentMobility
        || CameraKind(CameraActor) != Camera.CameraKind
        || CameraComponent->ProjectionMode != ECameraProjectionMode::Perspective
        || !CameraActor->IsEditable() || CameraActor->IsLockLocation()
        || !LocationsMatch(CameraActor->GetActorLocation(), Camera.Location)
        || !RotationsMatch(CameraActor->GetActorRotation(), Camera.Rotation))
    {
        OutError = TEXT("The camera identity or viewpoint changed after planning. Nothing was applied.");
        return false;
    }

    for (int32 Index = 0; Index < Lights.Num(); ++Index)
    {
        const FRenderMasterRigLightCapture& Captured = Lights[Index];
        ALight* Actor = Captured.Target.Actor.Get();
        ULightComponent* Component = Captured.Target.Component.Get();
        const FString CurrentGuid = Actor != nullptr && Actor->GetActorGuid().IsValid()
            ? Actor->GetActorGuid().ToString(EGuidFormats::Digits) : FString();
        if (Actor == nullptr || Component == nullptr || Actor->GetLightComponent() != Component
            || Actor->GetActorLabel() != Captured.Target.ActorName
            || Actor->GetPathName() != Captured.Target.ActorPath
            || Actor->GetClass()->GetName() != Captured.Target.ActorClass
            || CurrentGuid != Captured.Target.ActorGuid
            || Component->GetName() != Captured.Target.ComponentName
            || MobilityText(Component) != Captured.Target.ComponentMobility
            || RenderMasterGetLightKind(Component) != Captured.Target.LightKind
            || !Actor->IsEditable() || Actor->IsLockLocation()
            || !LocationsMatch(Actor->GetActorLocation(), Captured.Location)
            || !RenderMasterLightSnapshotsMatch(
                RenderMasterSnapshotLight(Actor, Component), Captured.Target.Light))
        {
            OutError = FString::Printf(
                TEXT("%s changed after planning. Nothing was applied; prepare a new rig."),
                *Captured.Target.ActorName);
            return false;
        }
    }
    return true;
}

void FRenderMasterLightingRigAssistant::RejectProposal()
{
    if (State == ERenderMasterLightingRigAssistantState::Planning)
    {
        Cancel();
        CloseProcessResources();
    }
    State = ERenderMasterLightingRigAssistantState::Rejected;
    AppendLog(TEXT("Lighting-rig proposal rejected. No Editor scene change was applied."));
}

void FRenderMasterLightingRigAssistant::Cancel()
{
    if (ProcessHandle.IsValid()) FPlatformProcess::TerminateProc(ProcessHandle, true);
}

bool FRenderMasterLightingRigAssistant::CanStart() const
{
    return !ProcessHandle.IsValid();
}

bool FRenderMasterLightingRigAssistant::CanApply() const
{
    return State == ERenderMasterLightingRigAssistantState::Proposed
        && Subject.Actor.IsValid() && Camera.Actor.IsValid()
        && Lights.Num() == 3 && Proposal.Actions.Num() == 3;
}

bool FRenderMasterLightingRigAssistant::IsPlanning() const
{
    return State == ERenderMasterLightingRigAssistantState::Planning;
}

FText FRenderMasterLightingRigAssistant::GetStateText() const
{
    switch (State)
    {
        case ERenderMasterLightingRigAssistantState::Planning: return NSLOCTEXT("RenderMasterBot", "RigPlanning", "Planning");
        case ERenderMasterLightingRigAssistantState::Proposed: return NSLOCTEXT("RenderMasterBot", "RigProposed", "Approval required");
        case ERenderMasterLightingRigAssistantState::Unresolved: return NSLOCTEXT("RenderMasterBot", "RigUnresolved", "Unresolved");
        case ERenderMasterLightingRigAssistantState::Failed: return NSLOCTEXT("RenderMasterBot", "RigFailed", "Failed");
        case ERenderMasterLightingRigAssistantState::Applied: return NSLOCTEXT("RenderMasterBot", "RigApplied", "Applied");
        case ERenderMasterLightingRigAssistantState::Rejected: return NSLOCTEXT("RenderMasterBot", "RigRejected", "Rejected");
        default: return NSLOCTEXT("RenderMasterBot", "RigReady", "Ready");
    }
}

FText FRenderMasterLightingRigAssistant::GetSummaryText() const
{
    if (State == ERenderMasterLightingRigAssistantState::Planning)
    {
        return FText::FromString(FString::Printf(
            TEXT("Planning Key/Fill/Rim around %s from %s. The subject, camera, and lights are frozen; nothing is being changed."),
            *Subject.ActorName,
            *Camera.ActorName));
    }
    if (State == ERenderMasterLightingRigAssistantState::Proposed)
    {
        TArray<FString> Sections;
        for (const FRenderMasterLightingRigAction& Action : Proposal.Actions)
        {
            Sections.Add(FString::Printf(
                TEXT("%s — %s (%s, %s)\nChanges  %s\nBefore  Location %.1f, %.1f, %.1f cm | %.3f %s | Rot %.1f, %.1f, %.1f\nAfter   Location %.1f, %.1f, %.1f cm | %.3f %s | Rot %.1f, %.1f, %.1f"),
                *Action.Role.ToUpper(),
                *Action.ActorName,
                *Action.LightKind,
                *Action.Before.Light.IntensityUnit,
                *Action.ChangeSummary,
                Action.Before.Location.X, Action.Before.Location.Y, Action.Before.Location.Z,
                Action.Before.Light.Intensity, *Action.Before.Light.IntensityUnit,
                Action.Before.Light.Rotation.Roll, Action.Before.Light.Rotation.Pitch, Action.Before.Light.Rotation.Yaw,
                Action.After.Location.X, Action.After.Location.Y, Action.After.Location.Z,
                Action.After.Light.Intensity, *Action.After.Light.IntensityUnit,
                Action.After.Light.Rotation.Roll, Action.After.Light.Rotation.Pitch, Action.After.Light.Rotation.Yaw));
        }
        return FText::FromString(FString::Printf(
            TEXT("THREE-POINT LIGHTING RIG\nSubject  %s\nCamera  %s\nStyle  %s contrast | %s palette | %s | %s spacing | %s brightness\n\n%s\n\nRationale\n%s\n\nApproval revalidates all five Actors and applies one Undo transaction. The level is not saved automatically."),
            *Subject.ActorName,
            *Camera.ActorName,
            *Proposal.Contrast,
            *Proposal.Palette,
            *Proposal.KeySide,
            *Proposal.Spacing,
            *Proposal.Brightness,
            *FString::Join(Sections, TEXT("\n\n")),
            *Proposal.Rationale));
    }
    if (State == ERenderMasterLightingRigAssistantState::Unresolved)
    {
        return FText::FromString(FString::Printf(
            TEXT("This lighting-rig request was not converted into an action.\n\nReason\n%s\n\nMissing capability\n%s\n\nNo Editor scene change was applied."),
            *Proposal.Rationale,
            Proposal.MissingCapabilities.IsEmpty() ? TEXT("Not reported") : *Proposal.MissingCapabilities));
    }
    if (State == ERenderMasterLightingRigAssistantState::Failed)
        return FText::FromString(ErrorText);
    if (State == ERenderMasterLightingRigAssistantState::Applied)
        return NSLOCTEXT("RenderMasterBot", "RigAppliedSummary", "The approved Key/Fill/Rim rig was applied. One Ctrl+Z restores all three lights; save the level manually to keep it.");
    if (State == ERenderMasterLightingRigAssistantState::Rejected)
        return NSLOCTEXT("RenderMasterBot", "RigRejectedSummary", "The lighting-rig proposal was rejected. Nothing was changed.");
    return NSLOCTEXT("RenderMasterBot", "RigReadySummary", "Select one subject, one Camera, and exactly three Movable local Lights, then prepare a three-point rig.");
}

FText FRenderMasterLightingRigAssistant::GetLogText() const
{
    return FText::FromString(ProcessLog);
}

FLinearColor FRenderMasterLightingRigAssistant::GetStateColor() const
{
    switch (State)
    {
        case ERenderMasterLightingRigAssistantState::Proposed: return FLinearColor(0.16f, 0.58f, 0.95f, 0.18f);
        case ERenderMasterLightingRigAssistantState::Applied: return FLinearColor(0.12f, 0.68f, 0.38f, 0.18f);
        case ERenderMasterLightingRigAssistantState::Unresolved: return FLinearColor(0.95f, 0.60f, 0.12f, 0.18f);
        case ERenderMasterLightingRigAssistantState::Failed: return FLinearColor(0.85f, 0.18f, 0.18f, 0.18f);
        default: return FLinearColor(0.12f, 0.12f, 0.12f, 0.0f);
    }
}

bool FRenderMasterLightingRigAssistant::Tick(float DeltaTime)
{
    (void)DeltaTime;
    if (!ProcessHandle.IsValid()) return true;
    ReadProcessOutput();
    if (!FPlatformProcess::IsProcRunning(ProcessHandle)) FinishProcess();
    return true;
}

void FRenderMasterLightingRigAssistant::FinishProcess()
{
    ReadProcessOutput();
    int32 ReturnCode = -1;
    FPlatformProcess::GetProcReturnCode(ProcessHandle, &ReturnCode);
    CloseProcessResources();
    if (ReturnCode != 0)
    {
        Fail(FString::Printf(
            TEXT("Lighting-rig planner exited with code %d.\n%s"),
            ReturnCode,
            *ProcessLog));
        return;
    }
    FString Error;
    if (!FRenderMasterLightingRigProposal::LoadFromFile(
            ProposalOutputPath, Proposal, Error))
    {
        Fail(Error);
        return;
    }
    if (Proposal.Status == TEXT("unresolved"))
    {
        State = ERenderMasterLightingRigAssistantState::Unresolved;
        AppendLog(TEXT("The planner reported a bounded lighting-rig capability gap. Nothing was changed."));
        return;
    }
    if (!ProposalMatchesCapturedEvidence(Error))
    {
        Fail(Error);
        return;
    }
    State = ERenderMasterLightingRigAssistantState::Proposed;
    AppendLog(TEXT("Lighting-rig proposal is ready for explicit approval. Nothing has been applied."));
}

void FRenderMasterLightingRigAssistant::ReadProcessOutput()
{
    if (StdOutRead != nullptr) AppendLog(FPlatformProcess::ReadPipe(StdOutRead));
    if (StdErrRead != nullptr) AppendLog(FPlatformProcess::ReadPipe(StdErrRead));
}

void FRenderMasterLightingRigAssistant::CloseProcessResources()
{
    if (ProcessHandle.IsValid())
    {
        FPlatformProcess::CloseProc(ProcessHandle);
        ProcessHandle.Reset();
    }
    if (StdOutRead != nullptr || StdOutWrite != nullptr)
    {
        FPlatformProcess::ClosePipe(StdOutRead, StdOutWrite);
        StdOutRead = nullptr;
        StdOutWrite = nullptr;
    }
    if (StdErrRead != nullptr || StdErrWrite != nullptr)
    {
        FPlatformProcess::ClosePipe(StdErrRead, StdErrWrite);
        StdErrRead = nullptr;
        StdErrWrite = nullptr;
    }
}

void FRenderMasterLightingRigAssistant::AppendLog(const FString& Text)
{
    const FString Clean = Text.TrimStartAndEnd();
    if (Clean.IsEmpty()) return;
    if (!ProcessLog.IsEmpty()) ProcessLog += TEXT("\n");
    ProcessLog += Clean;
}

void FRenderMasterLightingRigAssistant::Fail(const FString& Error)
{
    State = ERenderMasterLightingRigAssistantState::Failed;
    ErrorText = Error;
    AppendLog(Error);
}
