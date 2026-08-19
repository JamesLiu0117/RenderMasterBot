#include "RenderMasterLightAssistant.h"

#include "Components/DirectionalLightComponent.h"
#include "Components/LightComponent.h"
#include "Components/LocalLightComponent.h"
#include "Components/PointLightComponent.h"
#include "Components/RectLightComponent.h"
#include "Components/SpotLightComponent.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/Light.h"
#include "Engine/Scene.h"
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
constexpr double LightTolerance = 0.0001;
constexpr double ColorTolerance = 0.0041;

FString QuoteLightArgument(const FString& Value)
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
    {
        return false;
    }
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
        || !FMath::IsFinite(X)
        || !FMath::IsFinite(Y)
        || !FMath::IsFinite(Z))
    {
        return false;
    }
    Out = FVector(X, Y, Z);
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
        || !FMath::IsFinite(R)
        || !FMath::IsFinite(G)
        || !FMath::IsFinite(B)
        || R < 0.0 || R > 1.0
        || G < 0.0 || G > 1.0
        || B < 0.0 || B > 1.0)
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

bool ReadSnapshot(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field,
    FRenderMasterLightSnapshot& Out)
{
    TSharedPtr<FJsonObject> Snapshot;
    FVector RotationAxes;
    if (!ReadObject(Parent, Field, Snapshot)
        || !ReadVector(Snapshot, TEXT("rotation_deg"), RotationAxes)
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
    Out.Rotation = FRotator(RotationAxes.Y, RotationAxes.Z, RotationAxes.X);
    return true;
}

bool OptionalNumbersMatch(
    const TOptional<double>& A,
    const TOptional<double>& B,
    double Tolerance = LightTolerance)
{
    if (A.IsSet() != B.IsSet()) return false;
    return !A.IsSet() || FMath::IsNearlyEqual(A.GetValue(), B.GetValue(), Tolerance);
}

bool RotationsMatch(const FRotator& A, const FRotator& B, double Tolerance = 0.01)
{
    return FMath::Abs(FMath::FindDeltaAngleDegrees(A.Roll, B.Roll)) <= Tolerance
        && FMath::Abs(FMath::FindDeltaAngleDegrees(A.Pitch, B.Pitch)) <= Tolerance
        && FMath::Abs(FMath::FindDeltaAngleDegrees(A.Yaw, B.Yaw)) <= Tolerance;
}

bool ColorsMatch(const FLinearColor& A, const FLinearColor& B)
{
    return FMath::IsNearlyEqual(A.R, B.R, ColorTolerance)
        && FMath::IsNearlyEqual(A.G, B.G, ColorTolerance)
        && FMath::IsNearlyEqual(A.B, B.B, ColorTolerance);
}

bool SnapshotsMatch(
    const FRenderMasterLightSnapshot& A,
    const FRenderMasterLightSnapshot& B)
{
    return RotationsMatch(A.Rotation, B.Rotation)
        && FMath::IsNearlyEqual(A.Intensity, B.Intensity, LightTolerance)
        && A.IntensityUnit == B.IntensityUnit
        && ColorsMatch(A.Color, B.Color)
        && A.bUseTemperature == B.bUseTemperature
        && FMath::IsNearlyEqual(A.TemperatureKelvin, B.TemperatureKelvin, LightTolerance)
        && A.bCastShadows == B.bCastShadows
        && OptionalNumbersMatch(A.AttenuationRadiusCm, B.AttenuationRadiusCm)
        && OptionalNumbersMatch(A.InnerConeDeg, B.InnerConeDeg)
        && OptionalNumbersMatch(A.OuterConeDeg, B.OuterConeDeg);
}

FString LightKind(const ULightComponent* Component)
{
    if (Cast<UDirectionalLightComponent>(Component) != nullptr) return TEXT("directional");
    if (Cast<USpotLightComponent>(Component) != nullptr) return TEXT("spot");
    if (Cast<URectLightComponent>(Component) != nullptr) return TEXT("rect");
    if (Cast<UPointLightComponent>(Component) != nullptr) return TEXT("point");
    return FString();
}

FString LightUnit(const ULightComponent* Component)
{
    if (Cast<UDirectionalLightComponent>(Component) != nullptr) return TEXT("lux");
    const ULocalLightComponent* Local = Cast<ULocalLightComponent>(Component);
    if (Local == nullptr) return FString();
    switch (Local->IntensityUnits)
    {
        case ELightUnits::Candelas: return TEXT("candelas");
        case ELightUnits::Lumens: return TEXT("lumens");
        case ELightUnits::EV: return TEXT("ev");
        case ELightUnits::Nits: return TEXT("nits");
        default: return TEXT("unitless");
    }
}

FRenderMasterLightSnapshot SnapshotLight(
    const ALight* Actor,
    const ULightComponent* Component)
{
    FRenderMasterLightSnapshot Snapshot;
    if (Actor == nullptr || Component == nullptr) return Snapshot;
    Snapshot.Rotation = Actor->GetActorRotation();
    Snapshot.Intensity = Component->Intensity;
    Snapshot.IntensityUnit = LightUnit(Component);
    Snapshot.Color = Component->GetLightColor();
    Snapshot.bUseTemperature = Component->bUseTemperature;
    Snapshot.TemperatureKelvin = Component->Temperature;
    Snapshot.bCastShadows = Component->CastShadows;
    if (const ULocalLightComponent* Local = Cast<ULocalLightComponent>(Component))
    {
        Snapshot.AttenuationRadiusCm = Local->AttenuationRadius;
    }
    if (const USpotLightComponent* Spot = Cast<USpotLightComponent>(Component))
    {
        Snapshot.InnerConeDeg = Spot->InnerConeAngle;
        Snapshot.OuterConeDeg = Spot->OuterConeAngle;
    }
    return Snapshot;
}

bool HasValidTypeShape(const FRenderMasterLightSnapshot& Snapshot, const FString& Kind)
{
    const bool bLocal = Kind == TEXT("point") || Kind == TEXT("spot") || Kind == TEXT("rect");
    const bool bHasCones = Snapshot.InnerConeDeg.IsSet() && Snapshot.OuterConeDeg.IsSet();
    if (bLocal != Snapshot.AttenuationRadiusCm.IsSet()) return false;
    if ((Kind == TEXT("spot")) != bHasCones) return false;
    if (Kind == TEXT("directional") && Snapshot.IntensityUnit != TEXT("lux")) return false;
    if (bLocal && Snapshot.IntensityUnit == TEXT("lux")) return false;
    return true;
}

bool IsBoundedSnapshot(const FRenderMasterLightSnapshot& Snapshot, const FString& Kind)
{
    if (!HasValidTypeShape(Snapshot, Kind)
        || Snapshot.TemperatureKelvin < 1000.0
        || Snapshot.TemperatureKelvin > 20000.0)
    {
        return false;
    }
    const double MaxIntensity = Snapshot.IntensityUnit == TEXT("lux")
        ? 10000000.0
        : 1000000000.0;
    if (Snapshot.IntensityUnit == TEXT("ev"))
    {
        if (Snapshot.Intensity < -20.0 || Snapshot.Intensity > 30.0) return false;
    }
    else if (Snapshot.Intensity < 0.0 || Snapshot.Intensity > MaxIntensity)
    {
        return false;
    }
    if (Snapshot.AttenuationRadiusCm.IsSet()
        && (Snapshot.AttenuationRadiusCm.GetValue() < 1.0
            || Snapshot.AttenuationRadiusCm.GetValue() > 10000000.0))
    {
        return false;
    }
    if (Snapshot.InnerConeDeg.IsSet()
        && (Snapshot.InnerConeDeg.GetValue() < 0.0
            || Snapshot.InnerConeDeg.GetValue() > 89.0
            || Snapshot.OuterConeDeg.GetValue() <= 0.0
            || Snapshot.OuterConeDeg.GetValue() > 89.0
            || Snapshot.InnerConeDeg.GetValue() > Snapshot.OuterConeDeg.GetValue()))
    {
        return false;
    }
    return true;
}

TSet<FString> ChangedProperties(
    const FRenderMasterLightSnapshot& Before,
    const FRenderMasterLightSnapshot& After)
{
    TSet<FString> Changed;
    if (!RotationsMatch(Before.Rotation, After.Rotation)) Changed.Add(TEXT("rotation"));
    if (!FMath::IsNearlyEqual(Before.Intensity, After.Intensity, LightTolerance)) Changed.Add(TEXT("intensity"));
    if (!ColorsMatch(Before.Color, After.Color)) Changed.Add(TEXT("color_rgb"));
    if (Before.bUseTemperature != After.bUseTemperature) Changed.Add(TEXT("use_temperature"));
    if (!FMath::IsNearlyEqual(Before.TemperatureKelvin, After.TemperatureKelvin, LightTolerance)) Changed.Add(TEXT("temperature_kelvin"));
    if (Before.bCastShadows != After.bCastShadows) Changed.Add(TEXT("cast_shadows"));
    if (!OptionalNumbersMatch(Before.AttenuationRadiusCm, After.AttenuationRadiusCm)) Changed.Add(TEXT("attenuation_radius_cm"));
    if (!OptionalNumbersMatch(Before.InnerConeDeg, After.InnerConeDeg)) Changed.Add(TEXT("inner_cone_deg"));
    if (!OptionalNumbersMatch(Before.OuterConeDeg, After.OuterConeDeg)) Changed.Add(TEXT("outer_cone_deg"));
    return Changed;
}

bool SetEquals(const TSet<FString>& A, const TSet<FString>& B)
{
    if (A.Num() != B.Num()) return false;
    for (const FString& Value : A) if (!B.Contains(Value)) return false;
    return true;
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

TSharedRef<FJsonObject> SnapshotJson(const FRenderMasterLightSnapshot& Snapshot)
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

FString OptionalText(const TOptional<double>& Value, const TCHAR* Suffix)
{
    return Value.IsSet()
        ? FString::Printf(TEXT("%.2f%s"), Value.GetValue(), Suffix)
        : TEXT("N/A");
}

FString SnapshotText(const FRenderMasterLightSnapshot& Snapshot)
{
    return FString::Printf(
        TEXT("Intensity  %.3f %s\nColor  R %.3f  G %.3f  B %.3f\nTemperature  %s at %.0f K\nShadows  %s\nAttenuation  %s\nCone  Inner %s  Outer %s\nRotation  Roll %.2f  Pitch %.2f  Yaw %.2f deg"),
        Snapshot.Intensity,
        *Snapshot.IntensityUnit,
        Snapshot.Color.R,
        Snapshot.Color.G,
        Snapshot.Color.B,
        Snapshot.bUseTemperature ? TEXT("enabled") : TEXT("disabled"),
        Snapshot.TemperatureKelvin,
        Snapshot.bCastShadows ? TEXT("enabled") : TEXT("disabled"),
        *OptionalText(Snapshot.AttenuationRadiusCm, TEXT(" cm")),
        *OptionalText(Snapshot.InnerConeDeg, TEXT(" deg")),
        *OptionalText(Snapshot.OuterConeDeg, TEXT(" deg")),
        Snapshot.Rotation.Roll,
        Snapshot.Rotation.Pitch,
        Snapshot.Rotation.Yaw);
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

bool JsonObjectText(const TSharedPtr<FJsonObject>& Object, FString& Out)
{
    if (!Object.IsValid()) return false;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
    return FJsonSerializer::Serialize(Object.ToSharedRef(), Writer);
}

FString MissingCapabilitiesText(const TSharedPtr<FJsonObject>& Root)
{
    const TArray<TSharedPtr<FJsonValue>>* Missing = nullptr;
    TArray<FString> Values;
    if (Root.IsValid() && Root->TryGetArrayField(TEXT("missing_capabilities"), Missing)
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

bool SetLightSnapshot(
    ALight* Actor,
    ULightComponent* Component,
    const FRenderMasterLightSnapshot& Before,
    const FRenderMasterLightSnapshot& After)
{
    if (Actor == nullptr || Component == nullptr) return false;
    const bool bRotationChanged = !RotationsMatch(Before.Rotation, After.Rotation);
    if (bRotationChanged
        && !Actor->SetActorRotation(After.Rotation, ETeleportType::TeleportPhysics))
    {
        return false;
    }
    Component->Intensity = After.Intensity;
    Component->LightColor = After.Color.ToFColorSRGB();
    Component->Temperature = After.TemperatureKelvin;
    Component->bUseTemperature = After.bUseTemperature;
    Component->CastShadows = After.bCastShadows;
    if (ULocalLightComponent* Local = Cast<ULocalLightComponent>(Component))
    {
        Local->AttenuationRadius = After.AttenuationRadiusCm.GetValue();
    }
    if (USpotLightComponent* Spot = Cast<USpotLightComponent>(Component))
    {
        Spot->InnerConeAngle = After.InnerConeDeg.GetValue();
        Spot->OuterConeAngle = After.OuterConeDeg.GetValue();
    }
    return true;
}
}

FString RenderMasterGetLightKind(const ULightComponent* LightComponent)
{
    return LightKind(LightComponent);
}

FString RenderMasterGetLightUnit(const ULightComponent* LightComponent)
{
    return LightUnit(LightComponent);
}

FRenderMasterLightSnapshot RenderMasterSnapshotLight(
    const ALight* LightActor,
    const ULightComponent* LightComponent)
{
    return SnapshotLight(LightActor, LightComponent);
}

bool RenderMasterLightSnapshotsMatch(
    const FRenderMasterLightSnapshot& A,
    const FRenderMasterLightSnapshot& B)
{
    return SnapshotsMatch(A, B);
}

bool RenderMasterIsBoundedLightSnapshot(
    const FRenderMasterLightSnapshot& Snapshot,
    const FString& LightKindValue)
{
    return IsBoundedSnapshot(Snapshot, LightKindValue);
}

bool RenderMasterSetLightSnapshot(
    ALight* LightActor,
    ULightComponent* LightComponent,
    const FRenderMasterLightSnapshot& Before,
    const FRenderMasterLightSnapshot& After)
{
    return SetLightSnapshot(LightActor, LightComponent, Before, After);
}

bool FRenderMasterLightProposal::LoadFromFile(
    const FString& Filename,
    FRenderMasterLightProposal& OutProposal,
    FString& OutError)
{
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *Filename))
    {
        OutError = FString::Printf(TEXT("Could not read light proposal: %s"), *Filename);
        return false;
    }
    return Parse(JsonText, OutProposal, OutError);
}

bool FRenderMasterLightProposal::Parse(
    const FString& JsonText,
    FRenderMasterLightProposal& OutProposal,
    FString& OutError)
{
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        OutError = TEXT("Light proposal is not valid JSON.");
        return false;
    }

    FRenderMasterLightProposal Parsed;
    bool bModifiesScene = false;
    bool bAutoSave = true;
    bool bUndoSupported = false;
    if (!Root->TryGetStringField(TEXT("proposal_id"), Parsed.ProposalId)
        || !Root->TryGetStringField(TEXT("status"), Parsed.Status)
        || !Root->TryGetStringField(TEXT("rationale"), Parsed.Rationale)
        || !Root->TryGetBoolField(TEXT("modifies_editor_scene"), bModifiesScene)
        || !Root->TryGetBoolField(TEXT("auto_save"), bAutoSave)
        || !Root->TryGetBoolField(TEXT("undo_supported"), bUndoSupported)
        || !bModifiesScene || bAutoSave || !bUndoSupported)
    {
        OutError = TEXT("Light proposal is missing its safe approval flags.");
        return false;
    }

    TSharedPtr<FJsonObject> Target;
    TSharedPtr<FJsonObject> TargetLight;
    if (!ReadObject(Root, TEXT("target"), Target)
        || !Target->TryGetStringField(TEXT("actor_name"), Parsed.ActorName)
        || !Target->TryGetStringField(TEXT("actor_path"), Parsed.ActorPath)
        || !Target->TryGetStringField(TEXT("actor_class"), Parsed.ActorClass)
        || !Target->TryGetStringField(TEXT("component_name"), Parsed.ComponentName)
        || !Target->TryGetStringField(TEXT("component_mobility"), Parsed.ComponentMobility)
        || !Target->TryGetStringField(TEXT("light_kind"), Parsed.LightKind)
        || !ReadObject(Target, TEXT("light"), TargetLight)
        || !ReadSnapshot(Root, TEXT("before"), Parsed.Before))
    {
        OutError = TEXT("Light proposal target or Before evidence is incomplete.");
        return false;
    }
    Target->TryGetStringField(TEXT("actor_guid"), Parsed.ActorGuid);
    FRenderMasterLightSnapshot TargetEvidence;
    TSharedPtr<FJsonObject> TargetWrapper = MakeShared<FJsonObject>();
    TargetWrapper->SetObjectField(TEXT("value"), TargetLight);
    if (!ReadSnapshot(TargetWrapper, TEXT("value"), TargetEvidence)
        || !SnapshotsMatch(TargetEvidence, Parsed.Before)
        || !IsBoundedSnapshot(Parsed.Before, Parsed.LightKind))
    {
        OutError = TEXT("Light proposal Before values do not match bounded target evidence.");
        return false;
    }

    const TArray<TSharedPtr<FJsonValue>>* Missing = nullptr;
    if (Root->TryGetArrayField(TEXT("missing_capabilities"), Missing) && Missing != nullptr)
    {
        TArray<FString> Values;
        for (const TSharedPtr<FJsonValue>& Value : *Missing)
        {
            FString Item;
            if (Value.IsValid() && Value->TryGetString(Item) && !Item.IsEmpty()) Values.Add(Item);
        }
        Parsed.MissingCapabilities = FString::Join(Values, TEXT(", "));
    }

    if (Parsed.Status == TEXT("proposed"))
    {
        const TArray<TSharedPtr<FJsonValue>>* Changes = nullptr;
        if (!ReadSnapshot(Root, TEXT("after"), Parsed.After)
            || !IsBoundedSnapshot(Parsed.After, Parsed.LightKind)
            || Parsed.After.IntensityUnit != Parsed.Before.IntensityUnit
            || !Root->TryGetArrayField(TEXT("changes"), Changes)
            || Changes == nullptr || Changes->IsEmpty() || Changes->Num() > 9
            || !Parsed.MissingCapabilities.IsEmpty())
        {
            OutError = TEXT("Proposed light action is missing bounded Before/After changes.");
            return false;
        }
        const TSet<FString> Expected = ChangedProperties(Parsed.Before, Parsed.After);
        TSet<FString> Seen;
        TArray<FString> Summary;
        for (const TSharedPtr<FJsonValue>& Value : *Changes)
        {
            const TSharedPtr<FJsonObject>* Change = nullptr;
            FString Property;
            FString Operation;
            if (!Value.IsValid() || !Value->TryGetObject(Change) || Change == nullptr
                || !(*Change)->TryGetStringField(TEXT("property"), Property)
                || !(*Change)->TryGetStringField(TEXT("operation"), Operation))
            {
                OutError = TEXT("Light proposal contains an invalid change record.");
                return false;
            }
            Property.ToLowerInline();
            Operation.ToLowerInline();
            const bool bScalar = Property == TEXT("intensity")
                || Property == TEXT("attenuation_radius_cm");
            const bool bAddable = Property == TEXT("rotation")
                || Property == TEXT("inner_cone_deg")
                || Property == TEXT("outer_cone_deg");
            const bool bSetOnly = Property == TEXT("color_rgb")
                || Property == TEXT("use_temperature")
                || Property == TEXT("temperature_kelvin")
                || Property == TEXT("cast_shadows");
            const bool bAllowed = (bScalar && (Operation == TEXT("set") || Operation == TEXT("add") || Operation == TEXT("multiply")))
                || (bAddable && (Operation == TEXT("set") || Operation == TEXT("add")))
                || (bSetOnly && Operation == TEXT("set"));
            if (!bAllowed || Seen.Contains(Property))
            {
                OutError = TEXT("Light proposal contains an unsupported or repeated property operation.");
                return false;
            }
            Seen.Add(Property);
            Summary.Add(FString::Printf(TEXT("%s: %s"), *Property, *Operation));
        }
        if (!SetEquals(Expected, Seen)
            || (Parsed.LightKind == TEXT("point") && Seen.Contains(TEXT("rotation")))
            || (Parsed.LightKind == TEXT("directional") && Seen.Contains(TEXT("attenuation_radius_cm")))
            || (Parsed.LightKind != TEXT("spot")
                && (Seen.Contains(TEXT("inner_cone_deg")) || Seen.Contains(TEXT("outer_cone_deg")))))
        {
            OutError = TEXT("Light changes do not match the type-specific Before/After evidence.");
            return false;
        }
        Parsed.ChangeSummary = FString::Join(Summary, TEXT("\n"));
    }
    else if (Parsed.Status == TEXT("unresolved"))
    {
        if (Parsed.MissingCapabilities.IsEmpty())
        {
            OutError = TEXT("Unresolved light action must name a missing capability.");
            return false;
        }
    }
    else
    {
        OutError = FString::Printf(TEXT("Unsupported light proposal status: %s"), *Parsed.Status);
        return false;
    }

    OutProposal = MoveTemp(Parsed);
    return true;
}

bool FRenderMasterLightBatchProposal::LoadFromFile(
    const FString& Filename,
    FRenderMasterLightBatchProposal& OutProposal,
    FString& OutError)
{
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *Filename))
    {
        OutError = FString::Printf(TEXT("Could not read batch light proposal: %s"), *Filename);
        return false;
    }
    return Parse(JsonText, OutProposal, OutError);
}

bool FRenderMasterLightBatchProposal::Parse(
    const FString& JsonText,
    FRenderMasterLightBatchProposal& OutProposal,
    FString& OutError)
{
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        OutError = TEXT("Batch light proposal is not valid JSON.");
        return false;
    }

    FRenderMasterLightBatchProposal Parsed;
    FString SchemaVersion;
    bool bModifiesScene = false;
    bool bAutoSave = true;
    bool bUndoSupported = false;
    if (!Root->TryGetStringField(TEXT("schema_version"), SchemaVersion)
        || SchemaVersion != TEXT("0.1")
        || !Root->TryGetStringField(TEXT("proposal_id"), Parsed.ProposalId)
        || !Root->TryGetStringField(TEXT("status"), Parsed.Status)
        || !Root->TryGetStringField(TEXT("rationale"), Parsed.Rationale)
        || !Root->TryGetBoolField(TEXT("modifies_editor_scene"), bModifiesScene)
        || !Root->TryGetBoolField(TEXT("auto_save"), bAutoSave)
        || !Root->TryGetBoolField(TEXT("undo_supported"), bUndoSupported)
        || !bModifiesScene || bAutoSave || !bUndoSupported)
    {
        OutError = TEXT("Batch light proposal metadata or approval flags are invalid.");
        return false;
    }
    Parsed.Status.ToLowerInline();
    Parsed.MissingCapabilities = MissingCapabilitiesText(Root);

    TSharedPtr<FJsonObject> Selection;
    const TArray<TSharedPtr<FJsonValue>>* SelectedLights = nullptr;
    if (!ReadObject(Root, TEXT("selection"), Selection)
        || !Selection->TryGetArrayField(TEXT("lights"), SelectedLights)
        || SelectedLights == nullptr
        || SelectedLights->IsEmpty()
        || SelectedLights->Num() > 16)
    {
        OutError = TEXT("Batch light proposal has no valid frozen light selection.");
        return false;
    }
    Parsed.SelectedLightCount = SelectedLights->Num();

    const TArray<TSharedPtr<FJsonValue>>* Actions = nullptr;
    if (!Root->TryGetArrayField(TEXT("actions"), Actions) || Actions == nullptr)
    {
        OutError = TEXT("Batch light proposal is missing its action array.");
        return false;
    }
    if (Parsed.Status == TEXT("unresolved"))
    {
        if (!Actions->IsEmpty() || Parsed.MissingCapabilities.IsEmpty())
        {
            OutError = TEXT("Unresolved batch light proposals require a capability gap and no actions.");
            return false;
        }
        OutProposal = MoveTemp(Parsed);
        return true;
    }
    if (Parsed.Status != TEXT("proposed")
        || Actions->Num() != SelectedLights->Num()
        || !Parsed.MissingCapabilities.IsEmpty())
    {
        OutError = TEXT("Proposed batch light actions must cover the complete frozen selection.");
        return false;
    }

    bool bAnyChanged = false;
    for (int32 Index = 0; Index < Actions->Num(); ++Index)
    {
        const TSharedPtr<FJsonObject>* SelectedTarget = nullptr;
        const TSharedPtr<FJsonObject>* Action = nullptr;
        TSharedPtr<FJsonObject> ActionTarget;
        if (!(*SelectedLights)[Index].IsValid()
            || !(*SelectedLights)[Index]->TryGetObject(SelectedTarget)
            || SelectedTarget == nullptr
            || !(*Actions)[Index].IsValid()
            || !(*Actions)[Index]->TryGetObject(Action)
            || Action == nullptr
            || !ReadObject(*Action, TEXT("target"), ActionTarget))
        {
            OutError = TEXT("Batch light proposal contains invalid target evidence.");
            return false;
        }
        FString SelectedText;
        FString ActionTargetText;
        if (!JsonObjectText(*SelectedTarget, SelectedText)
            || !JsonObjectText(ActionTarget, ActionTargetText)
            || SelectedText != ActionTargetText)
        {
            OutError = TEXT("Batch light action target does not match the ordered selection evidence.");
            return false;
        }

        const TArray<TSharedPtr<FJsonValue>>* Changes = nullptr;
        if (!(*Action)->TryGetArrayField(TEXT("changes"), Changes) || Changes == nullptr)
        {
            OutError = TEXT("Batch light action is missing its changes array.");
            return false;
        }

        FRenderMasterLightProposal Single;
        if (Changes->IsEmpty())
        {
            if (!ActionTarget->TryGetStringField(TEXT("actor_name"), Single.ActorName)
                || !ActionTarget->TryGetStringField(TEXT("actor_path"), Single.ActorPath)
                || !ActionTarget->TryGetStringField(TEXT("actor_class"), Single.ActorClass)
                || !ActionTarget->TryGetStringField(TEXT("component_name"), Single.ComponentName)
                || !ActionTarget->TryGetStringField(TEXT("component_mobility"), Single.ComponentMobility)
                || !ActionTarget->TryGetStringField(TEXT("light_kind"), Single.LightKind)
                || !ReadSnapshot(*Action, TEXT("before"), Single.Before)
                || !ReadSnapshot(*Action, TEXT("after"), Single.After))
            {
                OutError = TEXT("No-op batch light action has incomplete evidence.");
                return false;
            }
            ActionTarget->TryGetStringField(TEXT("actor_guid"), Single.ActorGuid);
            TSharedPtr<FJsonObject> TargetLight;
            FRenderMasterLightSnapshot TargetSnapshot;
            TSharedPtr<FJsonObject> TargetWrapper = MakeShared<FJsonObject>();
            if (!ReadObject(ActionTarget, TEXT("light"), TargetLight))
            {
                OutError = TEXT("No-op batch light target has no property snapshot.");
                return false;
            }
            TargetWrapper->SetObjectField(TEXT("value"), TargetLight);
            if (!ReadSnapshot(TargetWrapper, TEXT("value"), TargetSnapshot)
                || !SnapshotsMatch(TargetSnapshot, Single.Before)
                || !SnapshotsMatch(Single.Before, Single.After)
                || !IsBoundedSnapshot(Single.Before, Single.LightKind))
            {
                OutError = TEXT("No-op batch light evidence is not internally consistent.");
                return false;
            }
            Single.ProposalId = Parsed.ProposalId;
            Single.Status = TEXT("proposed");
            Single.Rationale = Parsed.Rationale;
            Single.ChangeSummary = TEXT("No change (already satisfies the request)");
        }
        else
        {
            TSharedRef<FJsonObject> Synthetic = MakeShared<FJsonObject>();
            Synthetic->SetStringField(TEXT("proposal_id"), Parsed.ProposalId);
            Synthetic->SetStringField(TEXT("status"), TEXT("proposed"));
            Synthetic->SetStringField(TEXT("rationale"), Parsed.Rationale);
            Synthetic->SetBoolField(TEXT("modifies_editor_scene"), true);
            Synthetic->SetBoolField(TEXT("auto_save"), false);
            Synthetic->SetBoolField(TEXT("undo_supported"), true);
            Synthetic->SetObjectField(TEXT("target"), ActionTarget.ToSharedRef());
            const TSharedPtr<FJsonObject>* Before = nullptr;
            const TSharedPtr<FJsonObject>* After = nullptr;
            if (!(*Action)->TryGetObjectField(TEXT("before"), Before)
                || Before == nullptr
                || !(*Action)->TryGetObjectField(TEXT("after"), After)
                || After == nullptr)
            {
                OutError = TEXT("Batch light action is missing Before/After evidence.");
                return false;
            }
            Synthetic->SetObjectField(TEXT("before"), *Before);
            Synthetic->SetObjectField(TEXT("after"), *After);
            Synthetic->SetArrayField(TEXT("changes"), *Changes);
            Synthetic->SetArrayField(
                TEXT("missing_capabilities"),
                TArray<TSharedPtr<FJsonValue>>());
            FString SyntheticText;
            if (!JsonObjectText(Synthetic, SyntheticText)
                || !FRenderMasterLightProposal::Parse(SyntheticText, Single, OutError))
            {
                return false;
            }
            bAnyChanged = true;
        }
        Parsed.Actions.Add(MoveTemp(Single));
    }
    if (!bAnyChanged)
    {
        OutError = TEXT("Batch light proposal does not change any selected light.");
        return false;
    }
    OutProposal = MoveTemp(Parsed);
    return true;
}

bool RenderMasterApplyLightProperties(
    ALight* LightActor,
    ULightComponent* LightComponent,
    const FRenderMasterLightSnapshot& Before,
    const FRenderMasterLightSnapshot& After,
    FString& OutError,
    bool bMarkPackageDirty)
{
    if (LightActor == nullptr || LightComponent == nullptr)
    {
        OutError = TEXT("The target light no longer exists.");
        return false;
    }
    const FString Kind = LightKind(LightComponent);
    if (Kind.IsEmpty() || !IsBoundedSnapshot(After, Kind)
        || Before.IntensityUnit != After.IntensityUnit)
    {
        OutError = TEXT("The proposed light state is not valid for this light type.");
        return false;
    }

    FScopedTransaction Transaction(
        NSLOCTEXT("RenderMasterBot", "ApplyAssistantLight", "RenderMasterBot: Apply Light Properties"));
    LightActor->Modify();
    LightComponent->Modify();
    const bool bRotationChanged = !RotationsMatch(Before.Rotation, After.Rotation);
    if (bRotationChanged
        && !LightActor->SetActorRotation(After.Rotation, ETeleportType::TeleportPhysics))
    {
        Transaction.Cancel();
        OutError = TEXT("Unreal rejected the proposed light rotation.");
        return false;
    }
    // This is an Editor property edit, not a runtime light update. Runtime
    // setters intentionally reject some Static/Stationary changes (notably
    // attenuation radius), while the Details panel permits them. Modify the
    // transactional properties directly and let PostEditChange rebuild the
    // render state for every supported mobility mode.
    LightComponent->Intensity = After.Intensity;
    LightComponent->LightColor = After.Color.ToFColorSRGB();
    LightComponent->Temperature = After.TemperatureKelvin;
    LightComponent->bUseTemperature = After.bUseTemperature;
    LightComponent->CastShadows = After.bCastShadows;
    if (ULocalLightComponent* Local = Cast<ULocalLightComponent>(LightComponent))
    {
        Local->AttenuationRadius = After.AttenuationRadiusCm.GetValue();
    }
    if (USpotLightComponent* Spot = Cast<USpotLightComponent>(LightComponent))
    {
        Spot->InnerConeAngle = After.InnerConeDeg.GetValue();
        Spot->OuterConeAngle = After.OuterConeDeg.GetValue();
    }
    LightComponent->PostEditChange();
    LightComponent->MarkRenderStateDirty();
    if (bRotationChanged) LightActor->PostEditMove(true);
    if (bMarkPackageDirty) LightActor->MarkPackageDirty();
    if (GEditor != nullptr) GEditor->RedrawLevelEditingViewports();
    return true;
}

bool RenderMasterApplyLightPropertiesBatch(
    const TArray<ALight*>& LightActors,
    const TArray<ULightComponent*>& LightComponents,
    const TArray<FRenderMasterLightSnapshot>& Before,
    const TArray<FRenderMasterLightSnapshot>& After,
    FString& OutError,
    bool bMarkPackageDirty)
{
    if (LightActors.IsEmpty() || LightActors.Num() > 16
        || LightComponents.Num() != LightActors.Num()
        || Before.Num() != LightActors.Num()
        || After.Num() != LightActors.Num())
    {
        OutError = TEXT("Batch light arrays are empty, oversized, or inconsistent.");
        return false;
    }

    TSet<ALight*> UniqueActors;
    TSet<ULightComponent*> UniqueComponents;
    bool bAnyChanged = false;
    for (int32 Index = 0; Index < LightActors.Num(); ++Index)
    {
        ALight* Actor = LightActors[Index];
        ULightComponent* Component = LightComponents[Index];
        const FString Kind = LightKind(Component);
        if (Actor == nullptr || Component == nullptr || Kind.IsEmpty()
            || UniqueActors.Contains(Actor) || UniqueComponents.Contains(Component)
            || !IsBoundedSnapshot(Before[Index], Kind)
            || !IsBoundedSnapshot(After[Index], Kind)
            || Before[Index].IntensityUnit != After[Index].IntensityUnit)
        {
            OutError = TEXT("Batch light action contains an invalid, repeated, or unbounded target.");
            return false;
        }
        UniqueActors.Add(Actor);
        UniqueComponents.Add(Component);
        bAnyChanged |= !SnapshotsMatch(Before[Index], After[Index]);
    }
    if (!bAnyChanged)
    {
        OutError = TEXT("Batch light action does not change any selected light.");
        return false;
    }

    FScopedTransaction Transaction(
        NSLOCTEXT("RenderMasterBot", "ApplyAssistantLightBatch", "RenderMasterBot: Apply Light Group Properties"));
    TArray<FRenderMasterLightSnapshot> OriginalSnapshots;
    OriginalSnapshots.Reserve(LightActors.Num());
    for (int32 Index = 0; Index < LightActors.Num(); ++Index)
    {
        OriginalSnapshots.Add(SnapshotLight(LightActors[Index], LightComponents[Index]));
        if (SnapshotsMatch(Before[Index], After[Index])) continue;
        LightActors[Index]->Modify();
        LightComponents[Index]->Modify();
    }

    for (int32 Index = 0; Index < LightActors.Num(); ++Index)
    {
        if (SnapshotsMatch(Before[Index], After[Index])) continue;
        if (!SetLightSnapshot(
                LightActors[Index],
                LightComponents[Index],
                Before[Index],
                After[Index]))
        {
            for (int32 RestoreIndex = 0; RestoreIndex <= Index; ++RestoreIndex)
            {
                if (SnapshotsMatch(Before[RestoreIndex], After[RestoreIndex])) continue;
                const FRenderMasterLightSnapshot Current = SnapshotLight(
                    LightActors[RestoreIndex],
                    LightComponents[RestoreIndex]);
                SetLightSnapshot(
                    LightActors[RestoreIndex],
                    LightComponents[RestoreIndex],
                    Current,
                    OriginalSnapshots[RestoreIndex]);
                LightComponents[RestoreIndex]->PostEditChange();
                LightComponents[RestoreIndex]->MarkRenderStateDirty();
                LightActors[RestoreIndex]->PostEditMove(true);
            }
            Transaction.Cancel();
            OutError = FString::Printf(
                TEXT("Unreal rejected the proposed rotation for %s; prior group edits were restored."),
                *LightActors[Index]->GetActorLabel());
            return false;
        }
    }

    for (int32 Index = 0; Index < LightActors.Num(); ++Index)
    {
        if (SnapshotsMatch(Before[Index], After[Index])) continue;
        LightComponents[Index]->PostEditChange();
        LightComponents[Index]->MarkRenderStateDirty();
        if (!RotationsMatch(Before[Index].Rotation, After[Index].Rotation))
            LightActors[Index]->PostEditMove(true);
        if (bMarkPackageDirty) LightActors[Index]->MarkPackageDirty();
    }
    if (GEditor != nullptr) GEditor->RedrawLevelEditingViewports();
    return true;
}

FRenderMasterLightBatchAssistant::FRenderMasterLightBatchAssistant(
    TSharedPtr<FRenderMasterWorkflowController> InWorkflowController)
    : WorkflowController(MoveTemp(InWorkflowController))
{
}

FRenderMasterLightBatchAssistant::~FRenderMasterLightBatchAssistant()
{
    Shutdown();
}

void FRenderMasterLightBatchAssistant::Initialize()
{
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateSP(AsShared(), &FRenderMasterLightBatchAssistant::Tick),
        0.2f);
}

void FRenderMasterLightBatchAssistant::Shutdown()
{
    if (TickHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
        TickHandle.Reset();
    }
    if (ProcessHandle.IsValid()) FPlatformProcess::TerminateProc(ProcessHandle, true);
    CloseProcessResources();
}

bool FRenderMasterLightBatchAssistant::StartProposal(
    const FString& Prompt,
    const TArray<ALight*>& LightActors)
{
    if (!CanStart()) return false;
    const FString CleanPrompt = Prompt.TrimStartAndEnd();
    if (CleanPrompt.IsEmpty())
    {
        Fail(TEXT("Enter a light request before preparing an action."));
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
        TEXT("light_batch_%s"),
        *FDateTime::Now().ToString(TEXT("%Y%m%d_%H%M%S_%s")));
    const FString RequestDirectory = FPaths::Combine(
        Root,
        TEXT("assistant-light-batch"),
        ProposalId);
    IFileManager::Get().MakeDirectory(*RequestDirectory, true);
    const FString PromptPath = FPaths::Combine(RequestDirectory, TEXT("request.txt"));
    const FString ContextPath = FPaths::Combine(
        RequestDirectory,
        TEXT("light_selection_context.json"));
    ProposalOutputPath = FPaths::Combine(
        RequestDirectory,
        TEXT("light_batch_proposal.json"));

    FString Error;
    if (!FFileHelper::SaveStringToFile(
            CleanPrompt,
            *PromptPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)
        || !WriteLightSelectionContext(ContextPath, LightActors, Error))
    {
        Fail(Error.IsEmpty() ? TEXT("Could not write the batch light request.") : Error);
        return false;
    }

    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-light-batch-propose --prompt-file %s --context %s --proposal-id %s --output %s"),
        *QuoteLightArgument(PromptPath),
        *QuoteLightArgument(ContextPath),
        *QuoteLightArgument(ProposalId),
        *QuoteLightArgument(ProposalOutputPath));

    Proposal = FRenderMasterLightBatchProposal();
    ErrorText.Reset();
    ProcessLog.Reset();
    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        Fail(TEXT("Could not create batch light assistant process pipes."));
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
    State = ERenderMasterLightAssistantState::Planning;
    AppendLog(FString::Printf(
        TEXT("Preparing one bounded property proposal for %d light(s) (process %u)."),
        CapturedTargets.Num(),
        ProcessId));
    return true;
}

bool FRenderMasterLightBatchAssistant::WriteLightSelectionContext(
    const FString& Filename,
    const TArray<ALight*>& LightActors,
    FString& OutError)
{
    if (LightActors.IsEmpty() || LightActors.Num() > 16)
    {
        OutError = TEXT("Select between one and 16 supported Light Actors.");
        return false;
    }
    TArray<ALight*> SortedLights = LightActors;
    SortedLights.Sort([](const ALight& Left, const ALight& Right)
    {
        return Left.GetPathName() < Right.GetPathName();
    });

    UWorld* SelectionWorld = nullptr;
    TSet<FString> ActorPaths;
    TSet<FString> ActorGuids;
    TSet<ULightComponent*> Components;
    CapturedTargets.Reset();
    TArray<TSharedPtr<FJsonValue>> LightValues;
    LightValues.Reserve(SortedLights.Num());
    for (ALight* Actor : SortedLights)
    {
        if (Actor == nullptr || Actor->GetWorld() == nullptr || Actor->IsTemplate())
        {
            OutError = TEXT("The light selection contains an invalid or template Actor.");
            CapturedTargets.Reset();
            return false;
        }
        if (SelectionWorld == nullptr) SelectionWorld = Actor->GetWorld();
        if (Actor->GetWorld() != SelectionWorld)
        {
            OutError = TEXT("All selected lights must belong to the same open level.");
            CapturedTargets.Reset();
            return false;
        }
        ULightComponent* Component = Actor->GetLightComponent();
        const FString Kind = LightKind(Component);
        if (Component == nullptr || Kind.IsEmpty())
        {
            OutError = FString::Printf(
                TEXT("%s is not a supported Directional, Point, Spot, or Rect Light."),
                *Actor->GetActorLabel());
            CapturedTargets.Reset();
            return false;
        }
        if (!Actor->IsEditable() || Actor->IsLockLocation())
        {
            OutError = FString::Printf(
                TEXT("%s is not editable or its location is locked."),
                *Actor->GetActorLabel());
            CapturedTargets.Reset();
            return false;
        }

        FRenderMasterCapturedLightTarget Captured;
        Captured.Actor = Actor;
        Captured.Component = Component;
        Captured.ActorName = Actor->GetActorLabel();
        Captured.ActorPath = Actor->GetPathName();
        Captured.ActorClass = Actor->GetClass()->GetName();
        Captured.ActorGuid = Actor->GetActorGuid().IsValid()
            ? Actor->GetActorGuid().ToString(EGuidFormats::Digits)
            : FString();
        Captured.ComponentName = Component->GetName();
        Captured.ComponentMobility = MobilityText(Component);
        Captured.LightKind = Kind;
        Captured.Light = SnapshotLight(Actor, Component);
        if (!IsBoundedSnapshot(Captured.Light, Kind))
        {
            OutError = FString::Printf(
                TEXT("%s has properties outside the supported safety boundary."),
                *Captured.ActorName);
            CapturedTargets.Reset();
            return false;
        }
        if (ActorPaths.Contains(Captured.ActorPath)
            || Components.Contains(Component)
            || (!Captured.ActorGuid.IsEmpty() && ActorGuids.Contains(Captured.ActorGuid)))
        {
            OutError = TEXT("The light selection contains a repeated Actor or component identity.");
            CapturedTargets.Reset();
            return false;
        }
        ActorPaths.Add(Captured.ActorPath);
        Components.Add(Component);
        if (!Captured.ActorGuid.IsEmpty()) ActorGuids.Add(Captured.ActorGuid);

        TSharedRef<FJsonObject> LightJson = MakeShared<FJsonObject>();
        LightJson->SetStringField(TEXT("schema_version"), TEXT("0.1"));
        LightJson->SetStringField(TEXT("project_name"), FApp::GetProjectName());
        LightJson->SetStringField(
            TEXT("level_path"),
            SelectionWorld->GetPackage()->GetName());
        LightJson->SetStringField(TEXT("actor_name"), Captured.ActorName);
        LightJson->SetStringField(TEXT("actor_path"), Captured.ActorPath);
        LightJson->SetStringField(TEXT("actor_class"), Captured.ActorClass);
        if (!Captured.ActorGuid.IsEmpty())
            LightJson->SetStringField(TEXT("actor_guid"), Captured.ActorGuid);
        LightJson->SetStringField(TEXT("component_name"), Captured.ComponentName);
        LightJson->SetStringField(TEXT("light_kind"), Captured.LightKind);
        LightJson->SetStringField(TEXT("component_mobility"), Captured.ComponentMobility);
        LightJson->SetBoolField(TEXT("is_editable"), true);
        LightJson->SetBoolField(TEXT("is_locked"), false);
        LightJson->SetObjectField(TEXT("light"), SnapshotJson(Captured.Light));
        LightValues.Add(MakeShared<FJsonValueObject>(LightJson));
        CapturedTargets.Add(MoveTemp(Captured));
    }

    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Root->SetStringField(TEXT("project_name"), FApp::GetProjectName());
    Root->SetStringField(TEXT("level_path"), SelectionWorld->GetPackage()->GetName());
    Root->SetArrayField(TEXT("lights"), LightValues);
    FString JsonText;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&JsonText);
    if (!FJsonSerializer::Serialize(Root, Writer)
        || !FFileHelper::SaveStringToFile(
            JsonText,
            *Filename,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        OutError = FString::Printf(
            TEXT("Could not write light selection context: %s"),
            *Filename);
        CapturedTargets.Reset();
        return false;
    }
    return true;
}

bool FRenderMasterLightBatchAssistant::ApplyProposal()
{
    FString Error;
    if (!CanApply() || !RevalidateTargets(Error))
    {
        Fail(Error.IsEmpty() ? TEXT("No batch light proposal is ready to apply.") : Error);
        return false;
    }

    TArray<ALight*> Actors;
    TArray<ULightComponent*> Components;
    TArray<FRenderMasterLightSnapshot> Before;
    TArray<FRenderMasterLightSnapshot> After;
    Actors.Reserve(CapturedTargets.Num());
    Components.Reserve(CapturedTargets.Num());
    Before.Reserve(CapturedTargets.Num());
    After.Reserve(CapturedTargets.Num());
    for (int32 Index = 0; Index < CapturedTargets.Num(); ++Index)
    {
        Actors.Add(CapturedTargets[Index].Actor.Get());
        Components.Add(CapturedTargets[Index].Component.Get());
        Before.Add(Proposal.Actions[Index].Before);
        After.Add(Proposal.Actions[Index].After);
    }
    if (!RenderMasterApplyLightPropertiesBatch(
            Actors,
            Components,
            Before,
            After,
            Error))
    {
        Fail(Error);
        return false;
    }
    State = ERenderMasterLightAssistantState::Applied;
    AppendLog(FString::Printf(
        TEXT("Applied the approved property action to %d light(s) in one Undo transaction. The level was not saved."),
        CapturedTargets.Num()));
    return true;
}

bool FRenderMasterLightBatchAssistant::RevalidateTargets(FString& OutError) const
{
    if (CapturedTargets.IsEmpty() || Proposal.Actions.Num() != CapturedTargets.Num())
    {
        OutError = TEXT("The frozen light selection no longer matches the proposal.");
        return false;
    }
    for (int32 Index = 0; Index < CapturedTargets.Num(); ++Index)
    {
        const FRenderMasterCapturedLightTarget& Captured = CapturedTargets[Index];
        const FRenderMasterLightProposal& Action = Proposal.Actions[Index];
        ALight* Actor = Captured.Actor.Get();
        ULightComponent* Component = Captured.Component.Get();
        if (Actor == nullptr || Component == nullptr || Actor->GetWorld() == nullptr
            || Actor->GetLightComponent() != Component)
        {
            OutError = FString::Printf(
                TEXT("Captured light %d no longer exists. Prepare a new action."),
                Index + 1);
            return false;
        }
        const FString CurrentGuid = Actor->GetActorGuid().IsValid()
            ? Actor->GetActorGuid().ToString(EGuidFormats::Digits)
            : FString();
        if (Actor->GetActorLabel() != Captured.ActorName
            || Actor->GetPathName() != Captured.ActorPath
            || Actor->GetClass()->GetName() != Captured.ActorClass
            || CurrentGuid != Captured.ActorGuid
            || Component->GetName() != Captured.ComponentName
            || MobilityText(Component) != Captured.ComponentMobility
            || LightKind(Component) != Captured.LightKind
            || Action.ActorName != Captured.ActorName
            || Action.ActorPath != Captured.ActorPath
            || Action.ActorClass != Captured.ActorClass
            || Action.ActorGuid != Captured.ActorGuid
            || Action.ComponentName != Captured.ComponentName
            || Action.ComponentMobility != Captured.ComponentMobility
            || Action.LightKind != Captured.LightKind)
        {
            OutError = FString::Printf(
                TEXT("Light identity, type, unit, or component state changed for item %d."),
                Index + 1);
            return false;
        }
        if (!Actor->IsEditable() || Actor->IsLockLocation())
        {
            OutError = FString::Printf(
                TEXT("%s is no longer editable or is now locked."),
                *Captured.ActorName);
            return false;
        }
        if (!SnapshotsMatch(Action.Before, Captured.Light)
            || !SnapshotsMatch(SnapshotLight(Actor, Component), Captured.Light))
        {
            OutError = FString::Printf(
                TEXT("The properties of %s changed after planning. Nothing was applied; prepare a new action."),
                *Captured.ActorName);
            return false;
        }
    }
    return true;
}

void FRenderMasterLightBatchAssistant::RejectProposal()
{
    if (State == ERenderMasterLightAssistantState::Planning)
    {
        Cancel();
        CloseProcessResources();
    }
    State = ERenderMasterLightAssistantState::Rejected;
    AppendLog(TEXT("Batch light proposal rejected. No Editor scene change was applied."));
}

void FRenderMasterLightBatchAssistant::Cancel()
{
    if (ProcessHandle.IsValid()) FPlatformProcess::TerminateProc(ProcessHandle, true);
}

bool FRenderMasterLightBatchAssistant::CanStart() const
{
    return !ProcessHandle.IsValid();
}

bool FRenderMasterLightBatchAssistant::CanApply() const
{
    if (State != ERenderMasterLightAssistantState::Proposed
        || CapturedTargets.IsEmpty()
        || Proposal.Actions.Num() != CapturedTargets.Num())
    {
        return false;
    }
    for (const FRenderMasterCapturedLightTarget& Captured : CapturedTargets)
    {
        if (!Captured.Actor.IsValid() || !Captured.Component.IsValid()) return false;
    }
    return true;
}

bool FRenderMasterLightBatchAssistant::IsPlanning() const
{
    return State == ERenderMasterLightAssistantState::Planning;
}

FText FRenderMasterLightBatchAssistant::GetStateText() const
{
    switch (State)
    {
        case ERenderMasterLightAssistantState::Planning: return NSLOCTEXT("RenderMasterBot", "LightBatchPlanning", "Planning");
        case ERenderMasterLightAssistantState::Proposed: return NSLOCTEXT("RenderMasterBot", "LightBatchProposed", "Approval required");
        case ERenderMasterLightAssistantState::Unresolved: return NSLOCTEXT("RenderMasterBot", "LightBatchUnresolved", "Unresolved");
        case ERenderMasterLightAssistantState::Failed: return NSLOCTEXT("RenderMasterBot", "LightBatchFailed", "Failed");
        case ERenderMasterLightAssistantState::Applied: return NSLOCTEXT("RenderMasterBot", "LightBatchApplied", "Applied");
        case ERenderMasterLightAssistantState::Rejected: return NSLOCTEXT("RenderMasterBot", "LightBatchRejected", "Rejected");
        default: return NSLOCTEXT("RenderMasterBot", "LightBatchReady", "Ready");
    }
}

FText FRenderMasterLightBatchAssistant::GetSummaryText() const
{
    if (State == ERenderMasterLightAssistantState::Planning)
    {
        return FText::FromString(FString::Printf(
            TEXT("Interpreting one uniform property request against a frozen selection of %d light(s). Nothing is being changed."),
            CapturedTargets.Num()));
    }
    if (State == ERenderMasterLightAssistantState::Proposed)
    {
        TArray<FString> Sections;
        Sections.Reserve(Proposal.Actions.Num());
        for (int32 Index = 0; Index < Proposal.Actions.Num(); ++Index)
        {
            const FRenderMasterLightProposal& Action = Proposal.Actions[Index];
            Sections.Add(FString::Printf(
                TEXT("Light %d — %s\n%s\nType  %s | Unit  %s\n\nChanges\n%s\n\nBefore\n%s\n\nAfter\n%s"),
                Index + 1,
                *Action.ActorName,
                *Action.ActorPath,
                *Action.LightKind,
                *Action.Before.IntensityUnit,
                *Action.ChangeSummary,
                *SnapshotText(Action.Before),
                *SnapshotText(Action.After)));
        }
        return FText::FromString(FString::Printf(
            TEXT("%d LIGHTS | GROUP ACTION\n\n%s\n\nWhy\n%s\n\nApproval applies the complete selection as one grouped Editor action. If any light is stale, nothing is applied. The level is not saved automatically; one Ctrl+Z undoes the group."),
            Proposal.Actions.Num(),
            *FString::Join(Sections, TEXT("\n\n────────────────────────\n\n")),
            *Proposal.Rationale));
    }
    if (State == ERenderMasterLightAssistantState::Unresolved)
    {
        return FText::FromString(FString::Printf(
            TEXT("%s\n\nMissing capability\n%s"),
            *Proposal.Rationale,
            *Proposal.MissingCapabilities));
    }
    if (State == ERenderMasterLightAssistantState::Failed)
        return FText::FromString(ErrorText);
    if (State == ERenderMasterLightAssistantState::Applied)
    {
        return FText::FromString(FString::Printf(
            TEXT("Applied the approved properties to %d light(s) as one grouped action. The level was not saved automatically. Use Ctrl+Z once to undo the complete group."),
            Proposal.Actions.Num()));
    }
    if (State == ERenderMasterLightAssistantState::Rejected)
    {
        return NSLOCTEXT("RenderMasterBot", "LightBatchRejectedSummary", "The batch light proposal was rejected. No scene change was applied.");
    }
    return NSLOCTEXT("RenderMasterBot", "LightBatchReadySummary", "Select one to 16 supported lights and describe one property change for the complete selection.");
}

FText FRenderMasterLightBatchAssistant::GetLogText() const
{
    return FText::FromString(ProcessLog);
}

FLinearColor FRenderMasterLightBatchAssistant::GetStateColor() const
{
    if (State == ERenderMasterLightAssistantState::Proposed) return FLinearColor(0.95f, 0.55f, 0.12f);
    if (State == ERenderMasterLightAssistantState::Applied) return FLinearColor(0.12f, 0.62f, 0.38f);
    if (State == ERenderMasterLightAssistantState::Failed) return FLinearColor(0.9f, 0.2f, 0.2f);
    if (State == ERenderMasterLightAssistantState::Unresolved) return FLinearColor(0.95f, 0.55f, 0.15f);
    return FLinearColor(0.2f, 0.23f, 0.28f);
}

bool FRenderMasterLightBatchAssistant::Tick(float DeltaTime)
{
    ReadProcessOutput();
    if (ProcessHandle.IsValid() && !FPlatformProcess::IsProcRunning(ProcessHandle))
        FinishProcess();
    return true;
}

void FRenderMasterLightBatchAssistant::FinishProcess()
{
    ReadProcessOutput();
    int32 ExitCode = -1;
    FPlatformProcess::GetProcReturnCode(ProcessHandle, &ExitCode);
    CloseProcessResources();
    if (ExitCode != 0)
    {
        Fail(FString::Printf(
            TEXT("Batch light assistant process exited with code %d.\n%s"),
            ExitCode,
            *ProcessLog));
        return;
    }
    FString Error;
    if (!FRenderMasterLightBatchProposal::LoadFromFile(
            ProposalOutputPath,
            Proposal,
            Error))
    {
        Fail(Error);
        return;
    }
    if (Proposal.Status == TEXT("proposed") && !RevalidateTargets(Error))
    {
        Fail(Error);
        return;
    }
    State = Proposal.Status == TEXT("proposed")
        ? ERenderMasterLightAssistantState::Proposed
        : ERenderMasterLightAssistantState::Unresolved;
}

void FRenderMasterLightBatchAssistant::ReadProcessOutput()
{
    if (StdOutRead != nullptr) AppendLog(FPlatformProcess::ReadPipe(StdOutRead));
    if (StdErrRead != nullptr) AppendLog(FPlatformProcess::ReadPipe(StdErrRead));
}

void FRenderMasterLightBatchAssistant::CloseProcessResources()
{
    if (ProcessHandle.IsValid())
    {
        FPlatformProcess::CloseProc(ProcessHandle);
        ProcessHandle.Reset();
    }
    if (StdOutRead != nullptr || StdOutWrite != nullptr)
        FPlatformProcess::ClosePipe(StdOutRead, StdOutWrite);
    if (StdErrRead != nullptr || StdErrWrite != nullptr)
        FPlatformProcess::ClosePipe(StdErrRead, StdErrWrite);
    StdOutRead = StdOutWrite = StdErrRead = StdErrWrite = nullptr;
}

void FRenderMasterLightBatchAssistant::AppendLog(const FString& Text)
{
    if (Text.IsEmpty()) return;
    ProcessLog += Text;
    if (!ProcessLog.EndsWith(TEXT("\n"))) ProcessLog += TEXT("\n");
    if (ProcessLog.Len() > 12000) ProcessLog.RightInline(12000, EAllowShrinking::No);
}

void FRenderMasterLightBatchAssistant::Fail(const FString& Error)
{
    ErrorText = Error;
    State = ERenderMasterLightAssistantState::Failed;
    AppendLog(Error);
}

FRenderMasterLightAssistant::FRenderMasterLightAssistant(
    TSharedPtr<FRenderMasterWorkflowController> InWorkflowController)
    : WorkflowController(MoveTemp(InWorkflowController))
{
}

FRenderMasterLightAssistant::~FRenderMasterLightAssistant()
{
    Shutdown();
}

void FRenderMasterLightAssistant::Initialize()
{
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateSP(AsShared(), &FRenderMasterLightAssistant::Tick),
        0.2f);
}

void FRenderMasterLightAssistant::Shutdown()
{
    if (TickHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
        TickHandle.Reset();
    }
    if (ProcessHandle.IsValid()) FPlatformProcess::TerminateProc(ProcessHandle, true);
    CloseProcessResources();
}

bool FRenderMasterLightAssistant::StartProposal(const FString& Prompt, ALight* LightActor)
{
    if (!CanStart()) return false;
    const FString CleanPrompt = Prompt.TrimStartAndEnd();
    if (CleanPrompt.IsEmpty())
    {
        Fail(TEXT("Enter a light request before preparing an action."));
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
        TEXT("light_%s"),
        *FDateTime::Now().ToString(TEXT("%Y%m%d_%H%M%S_%s")));
    const FString RequestDirectory = FPaths::Combine(Root, TEXT("assistant-light"), ProposalId);
    IFileManager::Get().MakeDirectory(*RequestDirectory, true);
    const FString PromptPath = FPaths::Combine(RequestDirectory, TEXT("request.txt"));
    const FString ContextPath = FPaths::Combine(RequestDirectory, TEXT("light_context.json"));
    ProposalOutputPath = FPaths::Combine(RequestDirectory, TEXT("light_proposal.json"));

    FString Error;
    if (!FFileHelper::SaveStringToFile(
            CleanPrompt,
            *PromptPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)
        || !WriteLightContext(ContextPath, LightActor, Error))
    {
        Fail(Error.IsEmpty() ? TEXT("Could not write the light request.") : Error);
        return false;
    }
    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-light-propose --prompt-file %s --context %s --proposal-id %s --output %s"),
        *QuoteLightArgument(PromptPath),
        *QuoteLightArgument(ContextPath),
        *QuoteLightArgument(ProposalId),
        *QuoteLightArgument(ProposalOutputPath));

    Proposal = FRenderMasterLightProposal();
    ErrorText.Reset();
    ProcessLog.Reset();
    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        Fail(TEXT("Could not create light assistant process pipes."));
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
    State = ERenderMasterLightAssistantState::Planning;
    AppendLog(FString::Printf(TEXT("Preparing a bounded light proposal (process %u)."), ProcessId));
    return true;
}

bool FRenderMasterLightAssistant::WriteLightContext(
    const FString& Filename,
    ALight* LightActor,
    FString& OutError)
{
    if (LightActor == nullptr || LightActor->GetWorld() == nullptr || LightActor->IsTemplate())
    {
        OutError = TEXT("Select exactly one Directional, Point, Spot, or Rect Light Actor.");
        return false;
    }
    ULightComponent* Component = LightActor->GetLightComponent();
    const FString Kind = LightKind(Component);
    if (Component == nullptr || Kind.IsEmpty())
    {
        OutError = TEXT("The selected Actor is not a supported Directional, Point, Spot, or Rect Light.");
        return false;
    }
    if (!LightActor->IsEditable() || LightActor->IsLockLocation())
    {
        OutError = TEXT("The selected light is not editable or its location is locked.");
        return false;
    }

    CapturedActorPath = LightActor->GetPathName();
    CapturedActorClass = LightActor->GetClass()->GetName();
    CapturedActorGuid = LightActor->GetActorGuid().IsValid()
        ? LightActor->GetActorGuid().ToString(EGuidFormats::Digits)
        : FString();
    CapturedComponentName = Component->GetName();
    CapturedLightKind = Kind;
    CapturedLight = SnapshotLight(LightActor, Component);
    if (!IsBoundedSnapshot(CapturedLight, Kind))
    {
        OutError = TEXT("The selected light has properties outside the supported safety boundary.");
        return false;
    }
    TargetLightActor = LightActor;
    TargetLightComponent = Component;

    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Root->SetStringField(TEXT("project_name"), FApp::GetProjectName());
    Root->SetStringField(TEXT("level_path"), LightActor->GetWorld()->GetPackage()->GetName());
    Root->SetStringField(TEXT("actor_name"), LightActor->GetActorLabel());
    Root->SetStringField(TEXT("actor_path"), CapturedActorPath);
    Root->SetStringField(TEXT("actor_class"), CapturedActorClass);
    if (!CapturedActorGuid.IsEmpty()) Root->SetStringField(TEXT("actor_guid"), CapturedActorGuid);
    Root->SetStringField(TEXT("component_name"), CapturedComponentName);
    Root->SetStringField(TEXT("light_kind"), CapturedLightKind);
    FString Mobility = TEXT("movable");
    if (Component->Mobility == EComponentMobility::Static) Mobility = TEXT("static");
    else if (Component->Mobility == EComponentMobility::Stationary) Mobility = TEXT("stationary");
    Root->SetStringField(TEXT("component_mobility"), Mobility);
    Root->SetBoolField(TEXT("is_editable"), true);
    Root->SetBoolField(TEXT("is_locked"), false);
    Root->SetObjectField(TEXT("light"), SnapshotJson(CapturedLight));

    FString JsonText;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&JsonText);
    if (!FJsonSerializer::Serialize(Root, Writer)
        || !FFileHelper::SaveStringToFile(
            JsonText,
            *Filename,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        OutError = FString::Printf(TEXT("Could not write light context: %s"), *Filename);
        TargetLightActor.Reset();
        TargetLightComponent.Reset();
        return false;
    }
    return true;
}

bool FRenderMasterLightAssistant::ApplyProposal()
{
    FString Error;
    if (!CanApply() || !RevalidateTarget(Error))
    {
        Fail(Error.IsEmpty() ? TEXT("No light proposal is ready to apply.") : Error);
        return false;
    }
    ALight* Actor = TargetLightActor.Get();
    ULightComponent* Component = TargetLightComponent.Get();
    if (!RenderMasterApplyLightProperties(
            Actor,
            Component,
            Proposal.Before,
            Proposal.After,
            Error))
    {
        Fail(Error);
        return false;
    }
    State = ERenderMasterLightAssistantState::Applied;
    AppendLog(FString::Printf(
        TEXT("Applied approved light properties to %s. The level was not saved; use Ctrl+Z to undo."),
        *Actor->GetActorLabel()));
    return true;
}

bool FRenderMasterLightAssistant::RevalidateTarget(FString& OutError) const
{
    ALight* Actor = TargetLightActor.Get();
    ULightComponent* Component = TargetLightComponent.Get();
    if (Actor == nullptr || Component == nullptr || Actor->GetWorld() == nullptr)
    {
        OutError = TEXT("The captured light no longer exists.");
        return false;
    }
    const FString CurrentGuid = Actor->GetActorGuid().IsValid()
        ? Actor->GetActorGuid().ToString(EGuidFormats::Digits)
        : FString();
    if (Actor->GetPathName() != CapturedActorPath
        || Actor->GetClass()->GetName() != CapturedActorClass
        || CurrentGuid != CapturedActorGuid
        || Component->GetName() != CapturedComponentName
        || LightKind(Component) != CapturedLightKind
        || Proposal.ActorPath != CapturedActorPath
        || Proposal.ActorClass != CapturedActorClass
        || Proposal.ActorGuid != CapturedActorGuid
        || Proposal.ComponentName != CapturedComponentName
        || Proposal.LightKind != CapturedLightKind)
    {
        OutError = TEXT("The selected light identity or type changed after the proposal was created.");
        return false;
    }
    if (!Actor->IsEditable() || Actor->IsLockLocation())
    {
        OutError = TEXT("The captured light is no longer editable or is now locked.");
        return false;
    }
    if (!SnapshotsMatch(Proposal.Before, CapturedLight)
        || !SnapshotsMatch(SnapshotLight(Actor, Component), CapturedLight))
    {
        OutError = TEXT("The light properties changed after the proposal was created. Prepare a new action.");
        return false;
    }
    return true;
}

void FRenderMasterLightAssistant::RejectProposal()
{
    if (State == ERenderMasterLightAssistantState::Planning)
    {
        Cancel();
        CloseProcessResources();
    }
    State = ERenderMasterLightAssistantState::Rejected;
    AppendLog(TEXT("Light proposal rejected. No Editor scene change was applied."));
}

void FRenderMasterLightAssistant::Cancel()
{
    if (ProcessHandle.IsValid()) FPlatformProcess::TerminateProc(ProcessHandle, true);
}

bool FRenderMasterLightAssistant::CanStart() const
{
    return !ProcessHandle.IsValid();
}

bool FRenderMasterLightAssistant::CanApply() const
{
    return State == ERenderMasterLightAssistantState::Proposed
        && TargetLightActor.IsValid() && TargetLightComponent.IsValid();
}

bool FRenderMasterLightAssistant::IsPlanning() const
{
    return State == ERenderMasterLightAssistantState::Planning;
}

FText FRenderMasterLightAssistant::GetStateText() const
{
    switch (State)
    {
        case ERenderMasterLightAssistantState::Planning: return NSLOCTEXT("RenderMasterBot", "LightPlanning", "Planning");
        case ERenderMasterLightAssistantState::Proposed: return NSLOCTEXT("RenderMasterBot", "LightProposed", "Approval required");
        case ERenderMasterLightAssistantState::Unresolved: return NSLOCTEXT("RenderMasterBot", "LightUnresolved", "Unresolved");
        case ERenderMasterLightAssistantState::Failed: return NSLOCTEXT("RenderMasterBot", "LightFailed", "Failed");
        case ERenderMasterLightAssistantState::Applied: return NSLOCTEXT("RenderMasterBot", "LightApplied", "Applied");
        case ERenderMasterLightAssistantState::Rejected: return NSLOCTEXT("RenderMasterBot", "LightRejected", "Rejected");
        default: return NSLOCTEXT("RenderMasterBot", "LightReady", "Ready");
    }
}

FText FRenderMasterLightAssistant::GetSummaryText() const
{
    if (State == ERenderMasterLightAssistantState::Planning)
    {
        return NSLOCTEXT("RenderMasterBot", "LightPlanningSummary", "Interpreting the request against the selected light type and frozen intensity unit. The light is not being changed.");
    }
    if (State == ERenderMasterLightAssistantState::Proposed)
    {
        return FText::FromString(FString::Printf(
            TEXT("Target Light\n%s\n%s\nType  %s\n\nRequested changes\n%s\n\nBefore\n%s\n\nAfter\n%s\n\nWhy\n%s\n\nApproval changes only this light in the open level. The level is not saved automatically, and Ctrl+Z is supported."),
            *Proposal.ActorName,
            *Proposal.ActorPath,
            *Proposal.LightKind,
            *Proposal.ChangeSummary,
            *SnapshotText(Proposal.Before),
            *SnapshotText(Proposal.After),
            *Proposal.Rationale));
    }
    if (State == ERenderMasterLightAssistantState::Unresolved)
    {
        return FText::FromString(FString::Printf(
            TEXT("%s\n\nMissing capability\n%s"),
            *Proposal.Rationale,
            *Proposal.MissingCapabilities));
    }
    if (State == ERenderMasterLightAssistantState::Failed) return FText::FromString(ErrorText);
    if (State == ERenderMasterLightAssistantState::Applied)
    {
        return FText::FromString(FString::Printf(
            TEXT("Applied the approved properties to %s. The level was not saved automatically. Use Ctrl+Z to undo."),
            *Proposal.ActorName));
    }
    if (State == ERenderMasterLightAssistantState::Rejected)
    {
        return NSLOCTEXT("RenderMasterBot", "LightRejectedSummary", "The light proposal was rejected. No scene change was applied.");
    }
    return NSLOCTEXT("RenderMasterBot", "LightReadySummary", "Select one Directional, Point, Spot, or Rect Light and describe the property change you want.");
}

FText FRenderMasterLightAssistant::GetLogText() const
{
    return FText::FromString(ProcessLog);
}

FLinearColor FRenderMasterLightAssistant::GetStateColor() const
{
    if (State == ERenderMasterLightAssistantState::Proposed) return FLinearColor(0.95f, 0.55f, 0.12f);
    if (State == ERenderMasterLightAssistantState::Applied) return FLinearColor(0.12f, 0.62f, 0.38f);
    if (State == ERenderMasterLightAssistantState::Failed) return FLinearColor(0.9f, 0.2f, 0.2f);
    if (State == ERenderMasterLightAssistantState::Unresolved) return FLinearColor(0.95f, 0.55f, 0.15f);
    return FLinearColor(0.2f, 0.23f, 0.28f);
}

bool FRenderMasterLightAssistant::Tick(float DeltaTime)
{
    ReadProcessOutput();
    if (ProcessHandle.IsValid() && !FPlatformProcess::IsProcRunning(ProcessHandle)) FinishProcess();
    return true;
}

void FRenderMasterLightAssistant::FinishProcess()
{
    ReadProcessOutput();
    int32 ExitCode = -1;
    FPlatformProcess::GetProcReturnCode(ProcessHandle, &ExitCode);
    CloseProcessResources();
    if (ExitCode != 0)
    {
        Fail(FString::Printf(TEXT("Light assistant process exited with code %d.\n%s"), ExitCode, *ProcessLog));
        return;
    }
    FString Error;
    if (!FRenderMasterLightProposal::LoadFromFile(ProposalOutputPath, Proposal, Error))
    {
        Fail(Error);
        return;
    }
    State = Proposal.Status == TEXT("proposed")
        ? ERenderMasterLightAssistantState::Proposed
        : ERenderMasterLightAssistantState::Unresolved;
}

void FRenderMasterLightAssistant::ReadProcessOutput()
{
    if (StdOutRead != nullptr) AppendLog(FPlatformProcess::ReadPipe(StdOutRead));
    if (StdErrRead != nullptr) AppendLog(FPlatformProcess::ReadPipe(StdErrRead));
}

void FRenderMasterLightAssistant::CloseProcessResources()
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

void FRenderMasterLightAssistant::AppendLog(const FString& Text)
{
    if (Text.IsEmpty()) return;
    ProcessLog += Text;
    if (!ProcessLog.EndsWith(TEXT("\n"))) ProcessLog += TEXT("\n");
    if (ProcessLog.Len() > 12000) ProcessLog.RightInline(12000, EAllowShrinking::No);
}

void FRenderMasterLightAssistant::Fail(const FString& Error)
{
    ErrorText = Error;
    State = ERenderMasterLightAssistantState::Failed;
    AppendLog(Error);
}
