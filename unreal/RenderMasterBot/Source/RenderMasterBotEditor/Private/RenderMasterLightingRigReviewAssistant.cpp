#include "RenderMasterLightingRigReviewAssistant.h"

#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/LightComponent.h"
#include "Components/SceneComponent.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/Light.h"
#include "Engine/World.h"
#include "GenericPlatform/GenericPlatformMisc.h"
#include "HAL/FileManager.h"
#include "LevelEditorViewport.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"
#include "Misc/ScopeExit.h"
#include "Misc/Timespan.h"
#include "RenderMasterWorkflowController.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "UnrealClient.h"

namespace
{
constexpr double ReviewTolerance = 0.0001;
constexpr double ReviewTransformTolerance = 0.01;
constexpr double MaxReviewIntensity = 1000000000.0;
constexpr double CaptureTimeoutSeconds = 10.0;

FString QuoteReviewArgument(const FString& Value)
{
    return FString::Printf(TEXT("\"%s\""), *Value);
}

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
    Out = FLinearColor(R, G, B, 1.0f);
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
        || !Snapshot->TryGetNumberField(
            TEXT("temperature_kelvin"), Out.TemperatureKelvin)
        || !Snapshot->TryGetBoolField(TEXT("cast_shadows"), Out.bCastShadows)
        || !ReadOptionalNumber(
            Snapshot, TEXT("attenuation_radius_cm"), Out.AttenuationRadiusCm)
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

bool ReadTransform(
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

TSharedRef<FJsonObject> VectorJson(const FVector& Value)
{
    TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetNumberField(TEXT("x"), Value.X);
    Result->SetNumberField(TEXT("y"), Value.Y);
    Result->SetNumberField(TEXT("z"), Value.Z);
    return Result;
}

TSharedRef<FJsonObject> ColorJson(const FLinearColor& Value)
{
    TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetNumberField(TEXT("r"), Value.R);
    Result->SetNumberField(TEXT("g"), Value.G);
    Result->SetNumberField(TEXT("b"), Value.B);
    return Result;
}

void SetOptionalNumber(
    const TSharedRef<FJsonObject>& Object,
    const TCHAR* Field,
    const TOptional<double>& Value)
{
    if (Value.IsSet()) Object->SetNumberField(Field, Value.GetValue());
    else Object->SetField(Field, MakeShared<FJsonValueNull>());
}

TSharedRef<FJsonObject> LightSnapshotJson(const FRenderMasterLightSnapshot& Value)
{
    TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetObjectField(
        TEXT("rotation_deg"),
        VectorJson(FVector(Value.Rotation.Roll, Value.Rotation.Pitch, Value.Rotation.Yaw)));
    Result->SetNumberField(TEXT("intensity"), Value.Intensity);
    Result->SetStringField(TEXT("intensity_unit"), Value.IntensityUnit);
    Result->SetObjectField(TEXT("color_rgb"), ColorJson(Value.Color));
    Result->SetBoolField(TEXT("use_temperature"), Value.bUseTemperature);
    Result->SetNumberField(TEXT("temperature_kelvin"), Value.TemperatureKelvin);
    Result->SetBoolField(TEXT("cast_shadows"), Value.bCastShadows);
    SetOptionalNumber(Result, TEXT("attenuation_radius_cm"), Value.AttenuationRadiusCm);
    SetOptionalNumber(Result, TEXT("inner_cone_deg"), Value.InnerConeDeg);
    SetOptionalNumber(Result, TEXT("outer_cone_deg"), Value.OuterConeDeg);
    return Result;
}

TSharedRef<FJsonObject> TransformJson(const FTransform& Value)
{
    TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    const FRotator Rotation = Value.Rotator();
    Result->SetObjectField(TEXT("location_cm"), VectorJson(Value.GetLocation()));
    Result->SetObjectField(
        TEXT("rotation_deg"),
        VectorJson(FVector(Rotation.Roll, Rotation.Pitch, Rotation.Yaw)));
    Result->SetObjectField(TEXT("scale"), VectorJson(Value.GetScale3D()));
    return Result;
}

bool JsonObjectText(const TSharedPtr<FJsonObject>& Value, FString& Out)
{
    if (!Value.IsValid()) return false;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
    return FJsonSerializer::Serialize(Value.ToSharedRef(), Writer);
}

FString MissingCapabilitiesText(const TSharedPtr<FJsonObject>& Root)
{
    const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
    if (!Root.IsValid()
        || !Root->TryGetArrayField(TEXT("missing_capabilities"), Values)
        || Values == nullptr)
    {
        return FString();
    }
    TArray<FString> Text;
    for (const TSharedPtr<FJsonValue>& Value : *Values)
    {
        FString Item;
        if (Value.IsValid() && Value->TryGetString(Item) && !Item.IsEmpty()) Text.Add(Item);
    }
    return FString::Join(Text, TEXT("; "));
}

bool IsAllowed(const FString& Value, std::initializer_list<const TCHAR*> Allowed)
{
    for (const TCHAR* Item : Allowed) if (Value == Item) return true;
    return false;
}

bool LocationsMatch(const FVector& A, const FVector& B)
{
    return A.Equals(B, ReviewTransformTolerance);
}

bool RotationsMatch(const FRotator& A, const FRotator& B)
{
    return FMath::Abs(FMath::FindDeltaAngleDegrees(A.Roll, B.Roll))
            <= ReviewTransformTolerance
        && FMath::Abs(FMath::FindDeltaAngleDegrees(A.Pitch, B.Pitch))
            <= ReviewTransformTolerance
        && FMath::Abs(FMath::FindDeltaAngleDegrees(A.Yaw, B.Yaw))
            <= ReviewTransformTolerance;
}

bool TransformsMatch(const FTransform& A, const FTransform& B)
{
    return LocationsMatch(A.GetLocation(), B.GetLocation())
        && RotationsMatch(A.Rotator(), B.Rotator())
        && A.GetScale3D().Equals(B.GetScale3D(), ReviewTransformTolerance);
}

bool RigStatesMatch(
    const FRenderMasterRigLightState& A,
    const FRenderMasterRigLightState& B)
{
    return LocationsMatch(A.Location, B.Location)
        && RenderMasterLightSnapshotsMatch(A.Light, B.Light);
}

bool LightSnapshotsMatchExceptIntensity(
    const FRenderMasterLightSnapshot& A,
    const FRenderMasterLightSnapshot& B)
{
    FRenderMasterLightSnapshot Normalized = B;
    Normalized.Intensity = A.Intensity;
    return RenderMasterLightSnapshotsMatch(A, Normalized);
}

bool IsLowerSha256(const FString& Value)
{
    if (Value.Len() != 64) return false;
    for (const TCHAR Character : Value)
    {
        if (!FChar::IsDigit(Character)
            && (Character < TEXT('a') || Character > TEXT('f')))
        {
            return false;
        }
    }
    return true;
}

bool IsUnitValue(double Value)
{
    return FMath::IsFinite(Value) && Value >= 0.0 && Value <= 1.0;
}

bool ParseContextLight(
    const TSharedPtr<FJsonObject>& RigLight,
    FRenderMasterLightingRigAction& Out,
    FString& OutProject,
    FString& OutLevel)
{
    TSharedPtr<FJsonObject> Target;
    if (!ReadObject(RigLight, TEXT("target"), Target)
        || !Target->TryGetStringField(TEXT("project_name"), OutProject)
        || !Target->TryGetStringField(TEXT("level_path"), OutLevel)
        || !Target->TryGetStringField(TEXT("actor_name"), Out.ActorName)
        || !Target->TryGetStringField(TEXT("actor_path"), Out.ActorPath)
        || !Target->TryGetStringField(TEXT("actor_class"), Out.ActorClass)
        || !Target->TryGetStringField(TEXT("component_name"), Out.ComponentName)
        || !Target->TryGetStringField(
            TEXT("component_mobility"), Out.ComponentMobility)
        || !Target->TryGetStringField(TEXT("light_kind"), Out.LightKind)
        || !ReadVector(RigLight, TEXT("location_cm"), Out.Before.Location)
        || !ReadLightSnapshot(Target, TEXT("light"), Out.Before.Light))
    {
        return false;
    }
    Target->TryGetStringField(TEXT("actor_guid"), Out.ActorGuid);
    Out.ComponentMobility.ToLowerInline();
    Out.LightKind.ToLowerInline();
    Out.After = Out.Before;
    Out.ChangeSummary = TEXT("No change");
    return !Out.ActorName.IsEmpty() && !Out.ActorPath.IsEmpty()
        && !Out.ActorClass.IsEmpty() && !Out.ComponentName.IsEmpty()
        && Out.ComponentMobility == TEXT("movable")
        && IsAllowed(Out.LightKind, {TEXT("point"), TEXT("spot"), TEXT("rect")})
        && Out.Before.Light.IntensityUnit != TEXT("ev")
        && RenderMasterIsBoundedLightSnapshot(Out.Before.Light, Out.LightKind);
}

bool ParseCorrectionAction(
    const TSharedPtr<FJsonObject>& Action,
    FRenderMasterLightingRigAction& Out,
    FString& OutError)
{
    TSharedPtr<FJsonObject> Target;
    TSharedPtr<FJsonObject> NestedTarget;
    if (!Action->TryGetStringField(TEXT("role"), Out.Role)
        || !ReadObject(Action, TEXT("target"), Target)
        || !ReadObject(Target, TEXT("target"), NestedTarget)
        || !NestedTarget->TryGetStringField(TEXT("actor_name"), Out.ActorName)
        || !NestedTarget->TryGetStringField(TEXT("actor_path"), Out.ActorPath)
        || !NestedTarget->TryGetStringField(TEXT("actor_class"), Out.ActorClass)
        || !NestedTarget->TryGetStringField(TEXT("component_name"), Out.ComponentName)
        || !NestedTarget->TryGetStringField(
            TEXT("component_mobility"), Out.ComponentMobility)
        || !NestedTarget->TryGetStringField(TEXT("light_kind"), Out.LightKind)
        || !ReadRigState(Action, TEXT("before"), Out.Before)
        || !ReadRigState(Action, TEXT("after"), Out.After))
    {
        OutError = TEXT("Lighting-rig review action identity or state is incomplete.");
        return false;
    }
    NestedTarget->TryGetStringField(TEXT("actor_guid"), Out.ActorGuid);
    Out.Role.ToLowerInline();
    Out.ComponentMobility.ToLowerInline();
    Out.LightKind.ToLowerInline();
    if (!IsAllowed(Out.Role, {TEXT("key"), TEXT("fill"), TEXT("rim")})
        || Out.ComponentMobility != TEXT("movable")
        || !IsAllowed(Out.LightKind, {TEXT("point"), TEXT("spot"), TEXT("rect")})
        || !LocationsMatch(Out.Before.Location, Out.After.Location)
        || !LightSnapshotsMatchExceptIntensity(Out.Before.Light, Out.After.Light)
        || !FMath::IsFinite(Out.After.Light.Intensity)
        || Out.After.Light.Intensity < 0.0
        || Out.After.Light.Intensity > MaxReviewIntensity)
    {
        OutError = TEXT("Lighting-rig review action exceeds the intensity-only boundary.");
        return false;
    }

    const bool bIntensityChanged = !FMath::IsNearlyEqual(
        Out.Before.Light.Intensity,
        Out.After.Light.Intensity,
        ReviewTolerance);
    const TArray<TSharedPtr<FJsonValue>>* Changes = nullptr;
    if (!Action->TryGetArrayField(TEXT("changes"), Changes) || Changes == nullptr
        || Changes->Num() != (bIntensityChanged ? 1 : 0))
    {
        OutError = TEXT("Lighting-rig review changed-property evidence is inconsistent.");
        return false;
    }
    if (bIntensityChanged)
    {
        const TSharedPtr<FJsonObject>* Change = nullptr;
        FString Property;
        FString Operation;
        if (!(*Changes)[0].IsValid()
            || !(*Changes)[0]->TryGetObject(Change) || Change == nullptr
            || !(*Change)->TryGetStringField(TEXT("property"), Property)
            || !(*Change)->TryGetStringField(TEXT("operation"), Operation)
            || Property != TEXT("intensity") || Operation != TEXT("set"))
        {
            OutError = TEXT("Lighting-rig review can declare only an intensity set.");
            return false;
        }
    }
    Out.ChangeSummary = bIntensityChanged ? TEXT("intensity") : TEXT("No change");
    return true;
}

bool HashFileSha256(const FString& Filename, FString& OutHash)
{
    TArray<uint8> Bytes;
    if (!FFileHelper::LoadFileToArray(Bytes, *Filename) || Bytes.IsEmpty()) return false;
    FSHA256Signature Signature;
    if (!FPlatformMisc::GetSHA256Signature(
            Bytes.GetData(), static_cast<uint32>(Bytes.Num()), Signature))
    {
        return false;
    }
    OutHash = Signature.ToString().ToLower();
    return true;
}
}

bool FRenderMasterLightingRigReviewProposal::LoadFromFile(
    const FString& Filename,
    FRenderMasterLightingRigReviewProposal& OutProposal,
    FString& OutError)
{
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *Filename))
    {
        OutError = FString::Printf(
            TEXT("Could not read lighting-rig review proposal: %s"), *Filename);
        return false;
    }
    return Parse(JsonText, OutProposal, OutError);
}

bool FRenderMasterLightingRigReviewProposal::Parse(
    const FString& JsonText,
    FRenderMasterLightingRigReviewProposal& OutProposal,
    FString& OutError)
{
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        OutError = TEXT("Lighting-rig review proposal is not valid JSON.");
        return false;
    }

    FRenderMasterLightingRigReviewProposal Parsed;
    bool bModifies = false;
    bool bAutoSave = true;
    bool bUndo = false;
    if (!Root->TryGetStringField(TEXT("proposal_id"), Parsed.ProposalId)
        || !Root->TryGetStringField(TEXT("status"), Parsed.Status)
        || !Root->TryGetStringField(TEXT("request"), Parsed.Request)
        || !Root->TryGetStringField(TEXT("summary"), Parsed.Summary)
        || !Root->TryGetStringField(TEXT("rationale"), Parsed.Rationale)
        || !Root->TryGetStringField(TEXT("exposure"), Parsed.Exposure)
        || !Root->TryGetStringField(TEXT("fill_balance"), Parsed.FillBalance)
        || !Root->TryGetStringField(TEXT("rim_separation"), Parsed.RimSeparation)
        || !Root->TryGetNumberField(TEXT("confidence"), Parsed.Confidence)
        || !Root->TryGetBoolField(TEXT("modifies_editor_scene"), bModifies)
        || !Root->TryGetBoolField(TEXT("auto_save"), bAutoSave)
        || !Root->TryGetBoolField(TEXT("undo_supported"), bUndo)
        || Parsed.ProposalId.IsEmpty() || Parsed.Request.IsEmpty()
        || Parsed.Summary.IsEmpty() || Parsed.Rationale.IsEmpty()
        || !IsUnitValue(Parsed.Confidence)
        || !IsAllowed(
            Parsed.Exposure,
            {TEXT("too_dark"), TEXT("balanced"), TEXT("too_bright")})
        || !IsAllowed(
            Parsed.FillBalance,
            {TEXT("too_weak"), TEXT("balanced"), TEXT("too_strong")})
        || !IsAllowed(
            Parsed.RimSeparation,
            {TEXT("too_weak"), TEXT("balanced"), TEXT("too_strong")})
        || !bModifies || bAutoSave || !bUndo)
    {
        OutError = TEXT("Lighting-rig review metadata or safety flags are invalid.");
        return false;
    }
    Parsed.MissingCapabilities = MissingCapabilitiesText(Root);

    TSharedPtr<FJsonObject> Preview;
    if (!ReadObject(Root, TEXT("preview"), Preview)
        || !Preview->TryGetStringField(TEXT("sha256"), Parsed.PreviewSha256)
        || !Preview->TryGetNumberField(
            TEXT("center_luminance"), Parsed.CenterLuminance)
        || !Preview->TryGetNumberField(
            TEXT("dark_pixel_fraction"), Parsed.DarkPixelFraction)
        || !Preview->TryGetNumberField(
            TEXT("clipped_pixel_fraction"), Parsed.ClippedPixelFraction)
        || !Preview->TryGetNumberField(
            TEXT("foreground_fraction"), Parsed.ForegroundFraction)
        || !Preview->TryGetBoolField(TEXT("blank_like"), Parsed.bBlankLike)
        || !Preview->TryGetBoolField(
            TEXT("underexposed_like"), Parsed.bUnderexposedLike)
        || !Preview->TryGetBoolField(
            TEXT("overexposed_like"), Parsed.bOverexposedLike)
        || !IsLowerSha256(Parsed.PreviewSha256)
        || !IsUnitValue(Parsed.CenterLuminance)
        || !IsUnitValue(Parsed.DarkPixelFraction)
        || !IsUnitValue(Parsed.ClippedPixelFraction)
        || !IsUnitValue(Parsed.ForegroundFraction))
    {
        OutError = TEXT("Lighting-rig review preview evidence is invalid.");
        return false;
    }

    TSharedPtr<FJsonObject> Context;
    TSharedPtr<FJsonObject> Rig;
    TSharedPtr<FJsonObject> Subject;
    TSharedPtr<FJsonObject> Bounds;
    TSharedPtr<FJsonObject> Camera;
    FString SourceRequest;
    FString ProjectName;
    FString LevelPath;
    const TArray<TSharedPtr<FJsonValue>>* SelectedLights = nullptr;
    const TArray<TSharedPtr<FJsonValue>>* Assignments = nullptr;
    if (!ReadObject(Root, TEXT("context"), Context)
        || !Context->TryGetStringField(TEXT("source_request"), SourceRequest)
        || !ReadObject(Context, TEXT("rig"), Rig)
        || !Rig->TryGetStringField(TEXT("project_name"), ProjectName)
        || !Rig->TryGetStringField(TEXT("level_path"), LevelPath)
        || !ReadObject(Rig, TEXT("subject"), Subject)
        || !ReadObject(Subject, TEXT("bounds"), Bounds)
        || !ReadObject(Rig, TEXT("camera"), Camera)
        || !Rig->TryGetArrayField(TEXT("lights"), SelectedLights)
        || !Context->TryGetArrayField(TEXT("assignments"), Assignments)
        || SelectedLights == nullptr || Assignments == nullptr
        || SelectedLights->Num() != 3 || Assignments->Num() != 3
        || SourceRequest.IsEmpty() || ProjectName.IsEmpty() || LevelPath.IsEmpty()
        || !Subject->TryGetStringField(
            TEXT("actor_path"), Parsed.SubjectActorPath)
        || !ReadTransform(Subject, TEXT("transform"), Parsed.SubjectTransform)
        || !ReadVector(Bounds, TEXT("center_cm"), Parsed.SubjectBoundsCenter)
        || !ReadVector(Bounds, TEXT("extent_cm"), Parsed.SubjectBoundsExtent)
        || !Bounds->TryGetNumberField(
            TEXT("sphere_radius_cm"), Parsed.SubjectBoundsRadiusCm)
        || !Camera->TryGetStringField(TEXT("actor_path"), Parsed.CameraActorPath)
        || !ReadVector(Camera, TEXT("location_cm"), Parsed.CameraLocation)
        || !ReadRotator(Camera, TEXT("rotation_deg"), Parsed.CameraRotation)
        || !FMath::IsFinite(Parsed.SubjectBoundsRadiusCm)
        || Parsed.SubjectBoundsRadiusCm <= 0.0)
    {
        OutError = TEXT("Lighting-rig review context is incomplete.");
        return false;
    }
    Subject->TryGetStringField(TEXT("actor_guid"), Parsed.SubjectActorGuid);
    Camera->TryGetStringField(TEXT("actor_guid"), Parsed.CameraActorGuid);

    TSet<FString> Roles;
    for (int32 Index = 0; Index < 3; ++Index)
    {
        const TSharedPtr<FJsonObject>* Selected = nullptr;
        const TSharedPtr<FJsonObject>* Assignment = nullptr;
        if (!(*SelectedLights)[Index].IsValid()
            || !(*SelectedLights)[Index]->TryGetObject(Selected) || Selected == nullptr
            || !(*Assignments)[Index].IsValid()
            || !(*Assignments)[Index]->TryGetObject(Assignment) || Assignment == nullptr)
        {
            OutError = TEXT("Lighting-rig review selection or role evidence is invalid.");
            return false;
        }
        FRenderMasterLightingRigAction ContextLight;
        FString TargetProject;
        FString TargetLevel;
        FString AssignedPath;
        if (!ParseContextLight(*Selected, ContextLight, TargetProject, TargetLevel)
            || !(*Assignment)->TryGetStringField(TEXT("actor_path"), AssignedPath)
            || !(*Assignment)->TryGetStringField(TEXT("role"), ContextLight.Role)
            || TargetProject != ProjectName || TargetLevel != LevelPath
            || AssignedPath != ContextLight.ActorPath)
        {
            OutError = TEXT("Lighting-rig review target does not match ordered role evidence.");
            return false;
        }
        ContextLight.Role.ToLowerInline();
        if (!IsAllowed(ContextLight.Role, {TEXT("key"), TEXT("fill"), TEXT("rim")})
            || Roles.Contains(ContextLight.Role))
        {
            OutError = TEXT("Lighting-rig review roles must contain Key, Fill, and Rim once.");
            return false;
        }
        Roles.Add(ContextLight.Role);
        Parsed.ContextLights.Add(MoveTemp(ContextLight));
    }

    const TArray<TSharedPtr<FJsonValue>>* Actions = nullptr;
    if (!Root->TryGetArrayField(TEXT("actions"), Actions) || Actions == nullptr)
    {
        OutError = TEXT("Lighting-rig review proposal is missing its action array.");
        return false;
    }
    const bool bAdjusted = Parsed.Exposure != TEXT("balanced")
        || Parsed.FillBalance != TEXT("balanced")
        || Parsed.RimSeparation != TEXT("balanced");
    if (Parsed.Status == TEXT("pass"))
    {
        if (bAdjusted || !Actions->IsEmpty() || !Parsed.MissingCapabilities.IsEmpty()
            || Parsed.bBlankLike || Parsed.bUnderexposedLike || Parsed.bOverexposedLike)
        {
            OutError = TEXT("Passing lighting-rig reviews contradict their evidence.");
            return false;
        }
        OutProposal = MoveTemp(Parsed);
        return true;
    }
    if (Parsed.Status == TEXT("unresolved"))
    {
        if (bAdjusted || !Actions->IsEmpty() || Parsed.MissingCapabilities.IsEmpty())
        {
            OutError = TEXT("Unresolved lighting-rig reviews require one capability gap.");
            return false;
        }
        OutProposal = MoveTemp(Parsed);
        return true;
    }
    if (Parsed.Status != TEXT("proposed") || !bAdjusted
        || Actions->Num() != 3 || !Parsed.MissingCapabilities.IsEmpty()
        || Parsed.bBlankLike
        || (Parsed.bUnderexposedLike && Parsed.Exposure != TEXT("too_dark"))
        || (Parsed.bOverexposedLike && Parsed.Exposure != TEXT("too_bright")))
    {
        OutError = TEXT("Proposed lighting-rig review contradicts its visual evidence.");
        return false;
    }

    bool bAnyChanged = false;
    for (int32 Index = 0; Index < 3; ++Index)
    {
        const TSharedPtr<FJsonObject>* Selected = nullptr;
        const TSharedPtr<FJsonObject>* ActionObject = nullptr;
        TSharedPtr<FJsonObject> ActionTarget;
        if (!(*SelectedLights)[Index].IsValid()
            || !(*SelectedLights)[Index]->TryGetObject(Selected) || Selected == nullptr
            || !(*Actions)[Index].IsValid()
            || !(*Actions)[Index]->TryGetObject(ActionObject) || ActionObject == nullptr
            || !ReadObject(*ActionObject, TEXT("target"), ActionTarget))
        {
            OutError = TEXT("Lighting-rig review action target is invalid.");
            return false;
        }
        FString SelectedText;
        FString TargetText;
        if (!JsonObjectText(*Selected, SelectedText)
            || !JsonObjectText(ActionTarget, TargetText)
            || SelectedText != TargetText)
        {
            OutError = TEXT("Lighting-rig review action changed its frozen target evidence.");
            return false;
        }
        FRenderMasterLightingRigAction Action;
        if (!ParseCorrectionAction(*ActionObject, Action, OutError)
            || Action.Role != Parsed.ContextLights[Index].Role
            || Action.ActorName != Parsed.ContextLights[Index].ActorName
            || Action.ActorPath != Parsed.ContextLights[Index].ActorPath
            || Action.ActorClass != Parsed.ContextLights[Index].ActorClass
            || Action.ActorGuid != Parsed.ContextLights[Index].ActorGuid
            || Action.ComponentName != Parsed.ContextLights[Index].ComponentName
            || Action.ComponentMobility != Parsed.ContextLights[Index].ComponentMobility
            || Action.LightKind != Parsed.ContextLights[Index].LightKind
            || !RigStatesMatch(Action.Before, Parsed.ContextLights[Index].Before))
        {
            if (OutError.IsEmpty())
                OutError = TEXT("Lighting-rig review action does not match its frozen context.");
            return false;
        }
        bAnyChanged |= Action.ChangeSummary == TEXT("intensity");
        Parsed.Actions.Add(MoveTemp(Action));
    }
    if (!bAnyChanged)
    {
        OutError = TEXT("Lighting-rig review proposal does not change any intensity.");
        return false;
    }
    OutProposal = MoveTemp(Parsed);
    return true;
}

FRenderMasterLightingRigReviewAssistant::FRenderMasterLightingRigReviewAssistant(
    TSharedPtr<FRenderMasterWorkflowController> InWorkflowController,
    TSharedPtr<FRenderMasterLightingRigAssistant> InRigAssistant)
    : WorkflowController(MoveTemp(InWorkflowController))
    , RigAssistant(MoveTemp(InRigAssistant))
{
}

FRenderMasterLightingRigReviewAssistant::~FRenderMasterLightingRigReviewAssistant()
{
    Shutdown();
}

void FRenderMasterLightingRigReviewAssistant::Initialize()
{
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateSP(
            AsShared(), &FRenderMasterLightingRigReviewAssistant::Tick),
        0.2f);
}

void FRenderMasterLightingRigReviewAssistant::Shutdown()
{
    Cancel();
    if (TickHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
        TickHandle.Reset();
    }
    CloseProcessResources();
}

void FRenderMasterLightingRigReviewAssistant::Reset()
{
    Cancel();
    CloseProcessResources();
    AppliedRig = FRenderMasterAppliedLightingRig();
    Proposal = FRenderMasterLightingRigReviewProposal();
    State = ERenderMasterLightingRigReviewState::Ready;
    ErrorText.Reset();
    ProcessLog.Reset();
    ReviewRequest.Reset();
    ReviewContextPath.Reset();
    PreviewPath.Reset();
    ProposalOutputPath.Reset();
    RawOutputPath.Reset();
    ReviewId.Reset();
}

bool FRenderMasterLightingRigReviewAssistant::StartReview()
{
    if (!CanStart()) return false;
    FString Error;
    if (!RigAssistant.IsValid() || !RigAssistant->GetAppliedRig(AppliedRig, Error))
    {
        Fail(Error.IsEmpty()
            ? TEXT("Apply one three-point rig before visual review.") : Error);
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

    ReviewId = FString::Printf(
        TEXT("lighting_review_%s_%s"),
        *FDateTime::Now().ToString(TEXT("%Y%m%d_%H%M%S")),
        *FGuid::NewGuid().ToString(EGuidFormats::Digits).Left(8));
    const FString Directory = FPaths::Combine(
        Root, TEXT("assistant-lighting-rig-review"), ReviewId);
    IFileManager::Get().MakeDirectory(*Directory, true);
    ReviewRequest = TEXT(
        "Review the applied three-point lighting against the original request. "
        "If the visible lighting is not balanced, propose one safe intensity-only correction.");
    const FString RequestPath = FPaths::Combine(Directory, TEXT("review_request.txt"));
    ReviewContextPath = FPaths::Combine(
        Directory, TEXT("lighting_rig_review_context.json"));
    PreviewPath = FPaths::Combine(Directory, TEXT("lighting_rig_preview.png"));
    ProposalOutputPath = FPaths::Combine(
        Directory, TEXT("lighting_rig_review_proposal.json"));
    RawOutputPath = FPaths::Combine(
        Directory, TEXT("lighting_rig_review_raw.json"));
    if (!FFileHelper::SaveStringToFile(
            ReviewRequest,
            *RequestPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)
        || !WriteContext(ReviewContextPath, Error))
    {
        Fail(Error.IsEmpty() ? TEXT("Could not write lighting-rig review evidence.") : Error);
        return false;
    }
    Proposal = FRenderMasterLightingRigReviewProposal();
    ErrorText.Reset();
    ProcessLog.Reset();
    if (!BeginViewportCapture(Error))
    {
        Fail(Error);
        return false;
    }
    return true;
}

bool FRenderMasterLightingRigReviewAssistant::WriteContext(
    const FString& Filename,
    FString& OutError) const
{
    if (AppliedRig.Lights.Num() != 3 || AppliedRig.Roles.Num() != 3
        || AppliedRig.SourceRequest.IsEmpty())
    {
        OutError = TEXT("Applied lighting-rig evidence is incomplete.");
        return false;
    }
    AActor* SubjectActor = AppliedRig.Subject.Actor.Get();
    ACameraActor* CameraActor = AppliedRig.Camera.Actor.Get();
    UWorld* World = SubjectActor != nullptr ? SubjectActor->GetWorld() : nullptr;
    if (SubjectActor == nullptr || CameraActor == nullptr || World == nullptr
        || CameraActor->GetWorld() != World)
    {
        OutError = TEXT("Applied lighting-rig Actors are no longer in one level.");
        return false;
    }

    TSharedRef<FJsonObject> Subject = MakeShared<FJsonObject>();
    Subject->SetStringField(TEXT("actor_name"), AppliedRig.Subject.ActorName);
    Subject->SetStringField(TEXT("actor_path"), AppliedRig.Subject.ActorPath);
    Subject->SetStringField(TEXT("actor_class"), AppliedRig.Subject.ActorClass);
    if (!AppliedRig.Subject.ActorGuid.IsEmpty())
        Subject->SetStringField(TEXT("actor_guid"), AppliedRig.Subject.ActorGuid);
    Subject->SetStringField(
        TEXT("root_component_name"), AppliedRig.Subject.RootComponentName);
    Subject->SetStringField(TEXT("root_mobility"), AppliedRig.Subject.RootMobility);
    Subject->SetBoolField(TEXT("is_editable"), true);
    Subject->SetBoolField(TEXT("is_locked"), false);
    Subject->SetObjectField(TEXT("transform"), TransformJson(AppliedRig.Subject.Transform));
    TSharedRef<FJsonObject> Bounds = MakeShared<FJsonObject>();
    Bounds->SetObjectField(
        TEXT("center_cm"), VectorJson(AppliedRig.Subject.BoundsCenter));
    Bounds->SetObjectField(
        TEXT("extent_cm"), VectorJson(AppliedRig.Subject.BoundsExtent));
    Bounds->SetNumberField(
        TEXT("sphere_radius_cm"), AppliedRig.Subject.BoundsRadiusCm);
    Subject->SetObjectField(TEXT("bounds"), Bounds);

    TSharedRef<FJsonObject> Camera = MakeShared<FJsonObject>();
    Camera->SetStringField(TEXT("actor_name"), AppliedRig.Camera.ActorName);
    Camera->SetStringField(TEXT("actor_path"), AppliedRig.Camera.ActorPath);
    Camera->SetStringField(TEXT("actor_class"), AppliedRig.Camera.ActorClass);
    if (!AppliedRig.Camera.ActorGuid.IsEmpty())
        Camera->SetStringField(TEXT("actor_guid"), AppliedRig.Camera.ActorGuid);
    Camera->SetStringField(TEXT("component_name"), AppliedRig.Camera.ComponentName);
    Camera->SetStringField(TEXT("camera_kind"), AppliedRig.Camera.CameraKind);
    Camera->SetStringField(
        TEXT("component_mobility"), AppliedRig.Camera.ComponentMobility);
    Camera->SetStringField(TEXT("projection_mode"), TEXT("perspective"));
    Camera->SetBoolField(TEXT("is_editable"), true);
    Camera->SetBoolField(TEXT("is_locked"), false);
    Camera->SetObjectField(TEXT("location_cm"), VectorJson(AppliedRig.Camera.Location));
    Camera->SetObjectField(
        TEXT("rotation_deg"),
        VectorJson(FVector(
            AppliedRig.Camera.Rotation.Roll,
            AppliedRig.Camera.Rotation.Pitch,
            AppliedRig.Camera.Rotation.Yaw)));

    TArray<TSharedPtr<FJsonValue>> Lights;
    TArray<TSharedPtr<FJsonValue>> Assignments;
    for (int32 Index = 0; Index < 3; ++Index)
    {
        const FRenderMasterRigLightCapture& Capture = AppliedRig.Lights[Index];
        TSharedRef<FJsonObject> Target = MakeShared<FJsonObject>();
        Target->SetStringField(TEXT("schema_version"), TEXT("0.1"));
        Target->SetStringField(TEXT("project_name"), FApp::GetProjectName());
        Target->SetStringField(TEXT("level_path"), World->GetPackage()->GetName());
        Target->SetStringField(TEXT("actor_name"), Capture.Target.ActorName);
        Target->SetStringField(TEXT("actor_path"), Capture.Target.ActorPath);
        Target->SetStringField(TEXT("actor_class"), Capture.Target.ActorClass);
        if (!Capture.Target.ActorGuid.IsEmpty())
            Target->SetStringField(TEXT("actor_guid"), Capture.Target.ActorGuid);
        Target->SetStringField(TEXT("component_name"), Capture.Target.ComponentName);
        Target->SetStringField(TEXT("light_kind"), Capture.Target.LightKind);
        Target->SetStringField(TEXT("component_mobility"), TEXT("movable"));
        Target->SetBoolField(TEXT("is_editable"), true);
        Target->SetBoolField(TEXT("is_locked"), false);
        Target->SetObjectField(TEXT("light"), LightSnapshotJson(Capture.Target.Light));
        TSharedRef<FJsonObject> RigLight = MakeShared<FJsonObject>();
        RigLight->SetObjectField(TEXT("target"), Target);
        RigLight->SetObjectField(TEXT("location_cm"), VectorJson(Capture.Location));
        Lights.Add(MakeShared<FJsonValueObject>(RigLight));

        TSharedRef<FJsonObject> Assignment = MakeShared<FJsonObject>();
        Assignment->SetStringField(TEXT("actor_path"), Capture.Target.ActorPath);
        Assignment->SetStringField(TEXT("role"), AppliedRig.Roles[Index]);
        Assignments.Add(MakeShared<FJsonValueObject>(Assignment));
    }

    TSharedRef<FJsonObject> Rig = MakeShared<FJsonObject>();
    Rig->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Rig->SetStringField(TEXT("project_name"), FApp::GetProjectName());
    Rig->SetStringField(TEXT("level_path"), World->GetPackage()->GetName());
    Rig->SetObjectField(TEXT("subject"), Subject);
    Rig->SetObjectField(TEXT("camera"), Camera);
    Rig->SetArrayField(TEXT("lights"), Lights);

    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Root->SetStringField(TEXT("source_request"), AppliedRig.SourceRequest);
    Root->SetObjectField(TEXT("rig"), Rig);
    Root->SetArrayField(TEXT("assignments"), Assignments);
    FString JsonText;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&JsonText);
    if (!FJsonSerializer::Serialize(Root, Writer)
        || !FFileHelper::SaveStringToFile(
            JsonText, *Filename, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        OutError = FString::Printf(
            TEXT("Could not write lighting-rig review context: %s"), *Filename);
        return false;
    }
    return true;
}

bool FRenderMasterLightingRigReviewAssistant::BeginViewportCapture(FString& OutError)
{
    if (FScreenshotRequest::IsScreenshotRequested())
    {
        OutError = TEXT("Another Unreal screenshot request is already active.");
        return false;
    }
    FLevelEditorViewportClient* Client = GCurrentLevelEditingViewportClient;
    UCameraComponent* CameraComponent = AppliedRig.Camera.Component.Get();
    if (Client == nullptr || Client->Viewport == nullptr || !Client->IsPerspective())
    {
        OutError = TEXT(
            "Activate one perspective Level Editor viewport before evaluating the rig.");
        return false;
    }
    if (CameraComponent == nullptr
        || CameraComponent->ProjectionMode != ECameraProjectionMode::Perspective)
    {
        OutError = TEXT("The frozen rig camera is no longer a perspective camera.");
        return false;
    }

    CapturedViewportClient = Client;
    OriginalViewLocation = Client->GetViewLocation();
    OriginalViewRotation = Client->GetViewRotation();
    OriginalViewFOV = Client->ViewFOV;
    OriginalFOVAngle = Client->FOVAngle;
    OriginalViewMode = static_cast<int32>(Client->GetViewMode());
    bOriginalGameView = Client->IsInGameView();
    Client->SetViewLocation(AppliedRig.Camera.Location);
    Client->SetViewRotation(AppliedRig.Camera.Rotation);
    Client->ViewFOV = CameraComponent->FieldOfView;
    Client->FOVAngle = CameraComponent->FieldOfView;
    Client->SetViewMode(VMI_Lit);
    Client->SetGameView(true);
    Client->Invalidate();

    State = ERenderMasterLightingRigReviewState::Capturing;
    CaptureStartedSeconds = FPlatformTime::Seconds();
    ScreenshotProcessedHandle = FScreenshotRequest::OnScreenshotRequestProcessed().AddSP(
        AsShared(), &FRenderMasterLightingRigReviewAssistant::OnScreenshotProcessed);
    FScreenshotRequest::RequestScreenshot(PreviewPath, false, false, false);
    AppendLog(TEXT(
        "Capturing one Lit camera-view PNG from the active perspective viewport. "
        "The viewport will be restored immediately."));
    Client->Viewport->Draw(false);
    return State != ERenderMasterLightingRigReviewState::Failed;
}

void FRenderMasterLightingRigReviewAssistant::OnScreenshotProcessed()
{
    if (ScreenshotProcessedHandle.IsValid())
    {
        FScreenshotRequest::OnScreenshotRequestProcessed().Remove(
            ScreenshotProcessedHandle);
        ScreenshotProcessedHandle.Reset();
    }
    RestoreViewport();
    if (State != ERenderMasterLightingRigReviewState::Capturing) return;
    if (!FPaths::FileExists(PreviewPath))
    {
        Fail(TEXT("Unreal processed the camera screenshot but did not write the PNG."));
        return;
    }
    FString Error;
    if (!StartEvaluationProcess(Error)) Fail(Error);
}

void FRenderMasterLightingRigReviewAssistant::RestoreViewport()
{
    if (CapturedViewportClient == nullptr) return;
    CapturedViewportClient->SetViewLocation(OriginalViewLocation);
    CapturedViewportClient->SetViewRotation(OriginalViewRotation);
    CapturedViewportClient->ViewFOV = OriginalViewFOV;
    CapturedViewportClient->FOVAngle = OriginalFOVAngle;
    CapturedViewportClient->SetViewMode(
        static_cast<EViewModeIndex>(OriginalViewMode));
    CapturedViewportClient->SetGameView(bOriginalGameView);
    CapturedViewportClient->Invalidate();
    CapturedViewportClient = nullptr;
}

bool FRenderMasterLightingRigReviewAssistant::StartEvaluationProcess(
    FString& OutError)
{
    const FString Python = WorkflowController->GetPythonExecutable();
    const FString RequestPath = FPaths::Combine(
        FPaths::GetPath(ReviewContextPath), TEXT("review_request.txt"));
    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-lighting-rig-review --prompt-file %s --context %s --preview %s --proposal-id %s --raw-output %s --output %s"),
        *QuoteReviewArgument(RequestPath),
        *QuoteReviewArgument(ReviewContextPath),
        *QuoteReviewArgument(PreviewPath),
        *QuoteReviewArgument(ReviewId),
        *QuoteReviewArgument(RawOutputPath),
        *QuoteReviewArgument(ProposalOutputPath));
    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        OutError = TEXT("Could not create lighting-rig review process pipes.");
        return false;
    }
    uint32 ProcessId = 0;
    ProcessHandle = FPlatformProcess::CreateProc(
        *Python, *Arguments, false, true, true, &ProcessId, 0, nullptr,
        StdOutWrite, nullptr, StdErrWrite);
    if (!ProcessHandle.IsValid())
    {
        CloseProcessResources();
        OutError = FString::Printf(TEXT("Could not start Python: %s"), *Python);
        return false;
    }
    State = ERenderMasterLightingRigReviewState::Evaluating;
    AppendLog(FString::Printf(
        TEXT("Evaluating the verified camera PNG with the local vision model (process %u)."),
        ProcessId));
    return true;
}

bool FRenderMasterLightingRigReviewAssistant::ApplyProposal()
{
    FString Error;
    if (!CanApply() || !RevalidateTargets(Error))
    {
        Fail(Error.IsEmpty()
            ? TEXT("No lighting-rig correction is ready to apply.") : Error);
        return false;
    }
    TArray<ALight*> Actors;
    TArray<ULightComponent*> Components;
    TArray<FRenderMasterRigLightState> Before;
    TArray<FRenderMasterRigLightState> After;
    for (int32 Index = 0; Index < 3; ++Index)
    {
        Actors.Add(AppliedRig.Lights[Index].Target.Actor.Get());
        Components.Add(AppliedRig.Lights[Index].Target.Component.Get());
        Before.Add(Proposal.Actions[Index].Before);
        After.Add(Proposal.Actions[Index].After);
    }
    if (!RenderMasterApplyLightingRig(Actors, Components, Before, After, Error))
    {
        Fail(Error);
        return false;
    }
    State = ERenderMasterLightingRigReviewState::Applied;
    AppendLog(TEXT(
        "Applied the approved visual intensity correction in one Undo transaction. "
        "The level was not saved."));
    return true;
}

bool FRenderMasterLightingRigReviewAssistant::ProposalMatchesCapturedEvidence(
    FString& OutError) const
{
    if (Proposal.SubjectActorPath != AppliedRig.Subject.ActorPath
        || Proposal.SubjectActorGuid != AppliedRig.Subject.ActorGuid
        || !TransformsMatch(Proposal.SubjectTransform, AppliedRig.Subject.Transform)
        || !LocationsMatch(
            Proposal.SubjectBoundsCenter, AppliedRig.Subject.BoundsCenter)
        || !LocationsMatch(
            Proposal.SubjectBoundsExtent, AppliedRig.Subject.BoundsExtent)
        || !FMath::IsNearlyEqual(
            Proposal.SubjectBoundsRadiusCm,
            AppliedRig.Subject.BoundsRadiusCm,
            ReviewTransformTolerance)
        || Proposal.CameraActorPath != AppliedRig.Camera.ActorPath
        || Proposal.CameraActorGuid != AppliedRig.Camera.ActorGuid
        || !LocationsMatch(Proposal.CameraLocation, AppliedRig.Camera.Location)
        || !RotationsMatch(Proposal.CameraRotation, AppliedRig.Camera.Rotation)
        || Proposal.ContextLights.Num() != 3
        || AppliedRig.Lights.Num() != 3 || AppliedRig.Roles.Num() != 3)
    {
        OutError = TEXT("Lighting-rig visual review does not match the frozen rig context.");
        return false;
    }
    for (int32 Index = 0; Index < 3; ++Index)
    {
        const FRenderMasterRigLightCapture& Captured = AppliedRig.Lights[Index];
        const FRenderMasterLightingRigAction& ContextLight = Proposal.ContextLights[Index];
        FRenderMasterRigLightState CapturedState;
        CapturedState.Location = Captured.Location;
        CapturedState.Light = Captured.Target.Light;
        if (ContextLight.Role != AppliedRig.Roles[Index]
            || ContextLight.ActorName != Captured.Target.ActorName
            || ContextLight.ActorPath != Captured.Target.ActorPath
            || ContextLight.ActorClass != Captured.Target.ActorClass
            || ContextLight.ActorGuid != Captured.Target.ActorGuid
            || ContextLight.ComponentName != Captured.Target.ComponentName
            || ContextLight.ComponentMobility != Captured.Target.ComponentMobility
            || ContextLight.LightKind != Captured.Target.LightKind
            || !RigStatesMatch(ContextLight.Before, CapturedState))
        {
            OutError = FString::Printf(
                TEXT("Lighting-rig review context does not match frozen light %d."),
                Index + 1);
            return false;
        }
    }
    return true;
}

bool FRenderMasterLightingRigReviewAssistant::RevalidateTargets(FString& OutError) const
{
    if (!ProposalMatchesCapturedEvidence(OutError)
        || Proposal.Actions.Num() != 3)
    {
        return false;
    }
    AActor* SubjectActor = AppliedRig.Subject.Actor.Get();
    USceneComponent* SubjectRoot = AppliedRig.Subject.RootComponent.Get();
    FVector BoundsCenter;
    FVector BoundsExtent;
    if (SubjectActor != nullptr)
        SubjectActor->GetActorBounds(false, BoundsCenter, BoundsExtent, true);
    const FString SubjectGuid = SubjectActor != nullptr
        && SubjectActor->GetActorGuid().IsValid()
        ? SubjectActor->GetActorGuid().ToString(EGuidFormats::Digits) : FString();
    if (SubjectActor == nullptr || SubjectRoot == nullptr
        || SubjectActor->GetRootComponent() != SubjectRoot
        || SubjectActor->GetActorLabel() != AppliedRig.Subject.ActorName
        || SubjectActor->GetPathName() != AppliedRig.Subject.ActorPath
        || SubjectGuid != AppliedRig.Subject.ActorGuid
        || !SubjectActor->IsEditable() || SubjectActor->IsLockLocation()
        || !TransformsMatch(
            SubjectActor->GetActorTransform(), AppliedRig.Subject.Transform)
        || !LocationsMatch(BoundsCenter, AppliedRig.Subject.BoundsCenter)
        || !LocationsMatch(BoundsExtent, AppliedRig.Subject.BoundsExtent))
    {
        OutError = TEXT("The subject changed after visual review. Nothing was applied.");
        return false;
    }

    ACameraActor* CameraActor = AppliedRig.Camera.Actor.Get();
    UCameraComponent* CameraComponent = AppliedRig.Camera.Component.Get();
    const FString CameraGuid = CameraActor != nullptr
        && CameraActor->GetActorGuid().IsValid()
        ? CameraActor->GetActorGuid().ToString(EGuidFormats::Digits) : FString();
    if (CameraActor == nullptr || CameraComponent == nullptr
        || CameraActor->GetCameraComponent() != CameraComponent
        || CameraActor->GetActorLabel() != AppliedRig.Camera.ActorName
        || CameraActor->GetPathName() != AppliedRig.Camera.ActorPath
        || CameraGuid != AppliedRig.Camera.ActorGuid
        || CameraComponent->ProjectionMode != ECameraProjectionMode::Perspective
        || !CameraActor->IsEditable() || CameraActor->IsLockLocation()
        || !LocationsMatch(CameraActor->GetActorLocation(), AppliedRig.Camera.Location)
        || !RotationsMatch(CameraActor->GetActorRotation(), AppliedRig.Camera.Rotation))
    {
        OutError = TEXT("The camera changed after visual review. Nothing was applied.");
        return false;
    }

    for (int32 Index = 0; Index < 3; ++Index)
    {
        const FRenderMasterRigLightCapture& Captured = AppliedRig.Lights[Index];
        ALight* Actor = Captured.Target.Actor.Get();
        ULightComponent* Component = Captured.Target.Component.Get();
        const FRenderMasterLightingRigAction& Action = Proposal.Actions[Index];
        const FString Guid = Actor != nullptr && Actor->GetActorGuid().IsValid()
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
            || Guid != Captured.Target.ActorGuid
            || Component->GetName() != Captured.Target.ComponentName
            || RenderMasterGetLightKind(Component) != Captured.Target.LightKind
            || Component->Mobility != EComponentMobility::Movable
            || !Actor->IsEditable() || Actor->IsLockLocation()
            || !RigStatesMatch(Current, Action.Before))
        {
            OutError = FString::Printf(
                TEXT("%s changed after visual review. Nothing was applied."),
                *Captured.Target.ActorName);
            return false;
        }
    }
    return true;
}

void FRenderMasterLightingRigReviewAssistant::RejectProposal()
{
    if (IsBusy()) Cancel();
    State = ERenderMasterLightingRigReviewState::Rejected;
    AppendLog(TEXT("Lighting-rig visual correction rejected. Nothing was changed."));
}

void FRenderMasterLightingRigReviewAssistant::Cancel()
{
    if (ProcessHandle.IsValid()) FPlatformProcess::TerminateProc(ProcessHandle, true);
    if (State == ERenderMasterLightingRigReviewState::Capturing)
        FScreenshotRequest::Reset();
    if (ScreenshotProcessedHandle.IsValid())
    {
        FScreenshotRequest::OnScreenshotRequestProcessed().Remove(
            ScreenshotProcessedHandle);
        ScreenshotProcessedHandle.Reset();
    }
    RestoreViewport();
}

bool FRenderMasterLightingRigReviewAssistant::CanStart() const
{
    const bool bReviewCanBegin =
        State == ERenderMasterLightingRigReviewState::Ready
        || State == ERenderMasterLightingRigReviewState::Failed;
    return bReviewCanBegin
        && !ProcessHandle.IsValid()
        && RigAssistant.IsValid()
        && RigAssistant->GetState() == ERenderMasterLightingRigAssistantState::Applied;
}

bool FRenderMasterLightingRigReviewAssistant::CanApply() const
{
    return State == ERenderMasterLightingRigReviewState::Proposed
        && Proposal.Actions.Num() == 3;
}

bool FRenderMasterLightingRigReviewAssistant::IsBusy() const
{
    return State == ERenderMasterLightingRigReviewState::Capturing
        || State == ERenderMasterLightingRigReviewState::Evaluating;
}

FText FRenderMasterLightingRigReviewAssistant::GetStateText() const
{
    switch (State)
    {
        case ERenderMasterLightingRigReviewState::Capturing:
            return NSLOCTEXT("RenderMasterBot", "RigReviewCapturing", "Capturing preview");
        case ERenderMasterLightingRigReviewState::Evaluating:
            return NSLOCTEXT("RenderMasterBot", "RigReviewEvaluating", "Evaluating");
        case ERenderMasterLightingRigReviewState::Proposed:
            return NSLOCTEXT("RenderMasterBot", "RigReviewProposed", "Correction approval required");
        case ERenderMasterLightingRigReviewState::Passed:
            return NSLOCTEXT("RenderMasterBot", "RigReviewPassed", "Passed");
        case ERenderMasterLightingRigReviewState::Unresolved:
            return NSLOCTEXT("RenderMasterBot", "RigReviewUnresolved", "Unresolved");
        case ERenderMasterLightingRigReviewState::Failed:
            return NSLOCTEXT("RenderMasterBot", "RigReviewFailed", "Failed");
        case ERenderMasterLightingRigReviewState::Applied:
            return NSLOCTEXT("RenderMasterBot", "RigReviewApplied", "Correction applied");
        case ERenderMasterLightingRigReviewState::Rejected:
            return NSLOCTEXT("RenderMasterBot", "RigReviewRejected", "Rejected");
        default:
            return NSLOCTEXT("RenderMasterBot", "RigReviewReady", "Ready after rig approval");
    }
}

FText FRenderMasterLightingRigReviewAssistant::GetSummaryText() const
{
    if (State == ERenderMasterLightingRigReviewState::Capturing)
        return NSLOCTEXT(
            "RenderMasterBot", "RigReviewCapturingSummary",
            "Capturing one Lit PNG from the selected rig camera. The active viewport is temporary evidence and will be restored.");
    if (State == ERenderMasterLightingRigReviewState::Evaluating)
        return FText::FromString(FString::Printf(
            TEXT("The local vision model is reviewing %s. No level change is being made."),
            *PreviewPath));
    if (State == ERenderMasterLightingRigReviewState::Proposed)
    {
        TArray<FString> Sections;
        for (const FRenderMasterLightingRigAction& Action : Proposal.Actions)
        {
            Sections.Add(FString::Printf(
                TEXT("%s — %s\n%s | %.3f -> %.3f %s"),
                *Action.Role.ToUpper(),
                *Action.ActorName,
                *Action.ChangeSummary,
                Action.Before.Light.Intensity,
                Action.After.Light.Intensity,
                *Action.Before.Light.IntensityUnit));
        }
        return FText::FromString(FString::Printf(
            TEXT("VISUAL LIGHTING REVIEW\nExposure  %s | Fill  %s | Rim  %s | Confidence %.2f\nCenter luminance  %.3f | Dark pixels  %.1f%% | Clipped pixels  %.1f%%\n\n%s\n\nSummary\n%s\n\nRationale\n%s\n\nApproval revalidates all five Actors and changes intensity only. The level is not saved."),
            *Proposal.Exposure,
            *Proposal.FillBalance,
            *Proposal.RimSeparation,
            Proposal.Confidence,
            Proposal.CenterLuminance,
            Proposal.DarkPixelFraction * 100.0,
            Proposal.ClippedPixelFraction * 100.0,
            *FString::Join(Sections, TEXT("\n\n")),
            *Proposal.Summary,
            *Proposal.Rationale));
    }
    if (State == ERenderMasterLightingRigReviewState::Passed)
        return FText::FromString(FString::Printf(
            TEXT("VISUAL LIGHTING REVIEW PASSED\n%s\n\nNo correction was created. Preview: %s"),
            *Proposal.Summary, *PreviewPath));
    if (State == ERenderMasterLightingRigReviewState::Unresolved)
        return FText::FromString(FString::Printf(
            TEXT("The camera preview could not produce a safe intensity correction.\n\nReason\n%s\n\nMissing capability\n%s\n\nNo level change was made."),
            *Proposal.Rationale,
            Proposal.MissingCapabilities.IsEmpty()
                ? TEXT("Not reported") : *Proposal.MissingCapabilities));
    if (State == ERenderMasterLightingRigReviewState::Failed)
        return FText::FromString(ErrorText);
    if (State == ERenderMasterLightingRigReviewState::Applied)
        return NSLOCTEXT(
            "RenderMasterBot", "RigReviewAppliedSummary",
            "The approved intensity-only visual correction was applied. One Ctrl+Z restores the pre-correction rig; a second Ctrl+Z restores the original lights.");
    if (State == ERenderMasterLightingRigReviewState::Rejected)
        return NSLOCTEXT(
            "RenderMasterBot", "RigReviewRejectedSummary",
            "The visual correction was rejected. Nothing was changed.");
    return NSLOCTEXT(
        "RenderMasterBot", "RigReviewReadySummary",
        "Apply a three-point rig, activate a perspective Level Editor viewport, then evaluate the applied rig from its selected camera.");
}

FText FRenderMasterLightingRigReviewAssistant::GetLogText() const
{
    return FText::FromString(ProcessLog);
}

FLinearColor FRenderMasterLightingRigReviewAssistant::GetStateColor() const
{
    switch (State)
    {
        case ERenderMasterLightingRigReviewState::Proposed:
            return FLinearColor(0.16f, 0.58f, 0.95f, 0.18f);
        case ERenderMasterLightingRigReviewState::Passed:
        case ERenderMasterLightingRigReviewState::Applied:
            return FLinearColor(0.12f, 0.68f, 0.38f, 0.18f);
        case ERenderMasterLightingRigReviewState::Unresolved:
            return FLinearColor(0.95f, 0.60f, 0.12f, 0.18f);
        case ERenderMasterLightingRigReviewState::Failed:
            return FLinearColor(0.85f, 0.18f, 0.18f, 0.18f);
        default:
            return FLinearColor(0.12f, 0.12f, 0.12f, 0.0f);
    }
}

bool FRenderMasterLightingRigReviewAssistant::Tick(float DeltaTime)
{
    (void)DeltaTime;
    if (State == ERenderMasterLightingRigReviewState::Capturing
        && FPlatformTime::Seconds() - CaptureStartedSeconds > CaptureTimeoutSeconds)
    {
        Cancel();
        Fail(TEXT("Timed out while waiting for the Level Editor camera screenshot."));
        return true;
    }
    if (!ProcessHandle.IsValid()) return true;
    ReadProcessOutput();
    if (!FPlatformProcess::IsProcRunning(ProcessHandle)) FinishProcess();
    return true;
}

void FRenderMasterLightingRigReviewAssistant::FinishProcess()
{
    ReadProcessOutput();
    int32 ReturnCode = -1;
    FPlatformProcess::GetProcReturnCode(ProcessHandle, &ReturnCode);
    CloseProcessResources();
    if (ReturnCode != 0)
    {
        Fail(FString::Printf(
            TEXT("Lighting-rig visual review exited with code %d.\n%s"),
            ReturnCode, *ProcessLog));
        return;
    }
    FString Error;
    if (!FRenderMasterLightingRigReviewProposal::LoadFromFile(
            ProposalOutputPath, Proposal, Error))
    {
        Fail(Error);
        return;
    }
    FString PreviewHash;
    if (!HashFileSha256(PreviewPath, PreviewHash)
        || PreviewHash != Proposal.PreviewSha256)
    {
        Fail(TEXT("Lighting-rig review PNG hash does not match the proposal evidence."));
        return;
    }
    if (!ProposalMatchesCapturedEvidence(Error))
    {
        Fail(Error);
        return;
    }
    if (Proposal.Status == TEXT("pass"))
    {
        State = ERenderMasterLightingRigReviewState::Passed;
        AppendLog(TEXT("The visual review passed. No correction was created."));
    }
    else if (Proposal.Status == TEXT("unresolved"))
    {
        State = ERenderMasterLightingRigReviewState::Unresolved;
        AppendLog(TEXT("The visual review reported a bounded capability gap."));
    }
    else
    {
        State = ERenderMasterLightingRigReviewState::Proposed;
        AppendLog(TEXT(
            "An intensity-only correction is ready for explicit approval. "
            "Nothing has been applied."));
    }
}

void FRenderMasterLightingRigReviewAssistant::ReadProcessOutput()
{
    if (StdOutRead != nullptr) AppendLog(FPlatformProcess::ReadPipe(StdOutRead));
    if (StdErrRead != nullptr) AppendLog(FPlatformProcess::ReadPipe(StdErrRead));
}

void FRenderMasterLightingRigReviewAssistant::CloseProcessResources()
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

void FRenderMasterLightingRigReviewAssistant::AppendLog(const FString& Text)
{
    const FString Clean = Text.TrimStartAndEnd();
    if (Clean.IsEmpty()) return;
    if (!ProcessLog.IsEmpty()) ProcessLog += TEXT("\n");
    ProcessLog += Clean;
}

void FRenderMasterLightingRigReviewAssistant::Fail(const FString& Error)
{
    RestoreViewport();
    State = ERenderMasterLightingRigReviewState::Failed;
    ErrorText = Error;
    AppendLog(Error);
}
