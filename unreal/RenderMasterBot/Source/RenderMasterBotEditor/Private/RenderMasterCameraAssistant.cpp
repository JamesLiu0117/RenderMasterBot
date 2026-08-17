#include "RenderMasterCameraAssistant.h"

#include "RenderMasterWorkflowController.h"

#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "CineCameraComponent.h"
#include "CineCameraSettings.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/World.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "ScopedTransaction.h"

namespace
{
constexpr double CameraTolerance = 1e-4;
constexpr double MaxCameraLocationCm = 10000000.0;
constexpr double MaxCameraFocusDistanceCm = 10000000.0;

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

bool ReadOptionalNumber(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field,
    TOptional<double>& Out)
{
    if (!Parent.IsValid()) return false;
    const TSharedPtr<FJsonValue>* Value = Parent->Values.Find(Field);
    if (Value == nullptr || !Value->IsValid() || (*Value)->Type == EJson::Null)
    {
        Out.Reset();
        return true;
    }
    double Number = 0.0;
    if (!(*Value)->TryGetNumber(Number) || !FMath::IsFinite(Number)) return false;
    Out = Number;
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

bool ReadRotation(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field,
    FRotator& Out)
{
    FVector Axes;
    if (!ReadVector(Parent, Field, Axes)) return false;
    Out = FRotator(Axes.Y, Axes.Z, Axes.X);
    return true;
}

bool ReadSnapshot(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field,
    FRenderMasterCameraSnapshot& Out)
{
    TSharedPtr<FJsonObject> Snapshot;
    if (!ReadObject(Parent, Field, Snapshot)
        || !ReadVector(Snapshot, TEXT("location_cm"), Out.Location)
        || !ReadRotation(Snapshot, TEXT("rotation_deg"), Out.Rotation)
        || !ReadOptionalNumber(Snapshot, TEXT("field_of_view_deg"), Out.FieldOfViewDeg)
        || !ReadOptionalNumber(Snapshot, TEXT("focal_length_mm"), Out.FocalLengthMm)
        || !Snapshot->TryGetNumberField(TEXT("aperture_fstop"), Out.ApertureFstop)
        || !Snapshot->TryGetStringField(TEXT("focus_mode"), Out.FocusMode)
        || !Snapshot->TryGetNumberField(TEXT("focus_distance_cm"), Out.FocusDistanceCm)
        || !Snapshot->TryGetBoolField(
            TEXT("exposure_compensation_enabled"), Out.bExposureCompensationEnabled)
        || !Snapshot->TryGetNumberField(
            TEXT("exposure_compensation_ev"), Out.ExposureCompensationEv)
        || !Snapshot->TryGetNumberField(
            TEXT("post_process_blend_weight"), Out.PostProcessBlendWeight))
    {
        return false;
    }
    Out.FocusMode.ToLowerInline();
    return FMath::IsFinite(Out.ApertureFstop)
        && FMath::IsFinite(Out.FocusDistanceCm)
        && FMath::IsFinite(Out.ExposureCompensationEv)
        && FMath::IsFinite(Out.PostProcessBlendWeight);
}

bool ReadBounds(
    const TSharedPtr<FJsonObject>& Target,
    FRenderMasterCameraBounds& Out)
{
    return ReadOptionalNumber(Target, TEXT("min_focal_length_mm"), Out.MinFocalLengthMm)
        && ReadOptionalNumber(Target, TEXT("max_focal_length_mm"), Out.MaxFocalLengthMm)
        && Target->TryGetNumberField(TEXT("min_aperture_fstop"), Out.MinApertureFstop)
        && Target->TryGetNumberField(TEXT("max_aperture_fstop"), Out.MaxApertureFstop)
        && Target->TryGetNumberField(
            TEXT("minimum_focus_distance_cm"), Out.MinimumFocusDistanceCm)
        && FMath::IsFinite(Out.MinApertureFstop)
        && FMath::IsFinite(Out.MaxApertureFstop)
        && FMath::IsFinite(Out.MinimumFocusDistanceCm);
}

bool OptionalNumbersMatch(const TOptional<double>& A, const TOptional<double>& B)
{
    return A.IsSet() == B.IsSet()
        && (!A.IsSet() || FMath::IsNearlyEqual(A.GetValue(), B.GetValue(), CameraTolerance));
}

bool VectorsMatch(const FVector& A, const FVector& B)
{
    return A.Equals(B, CameraTolerance);
}

bool RotationsMatch(const FRotator& A, const FRotator& B)
{
    return A.Equals(B, CameraTolerance);
}

bool BoundsMatch(const FRenderMasterCameraBounds& A, const FRenderMasterCameraBounds& B)
{
    return OptionalNumbersMatch(A.MinFocalLengthMm, B.MinFocalLengthMm)
        && OptionalNumbersMatch(A.MaxFocalLengthMm, B.MaxFocalLengthMm)
        && FMath::IsNearlyEqual(A.MinApertureFstop, B.MinApertureFstop, CameraTolerance)
        && FMath::IsNearlyEqual(A.MaxApertureFstop, B.MaxApertureFstop, CameraTolerance)
        && FMath::IsNearlyEqual(
            A.MinimumFocusDistanceCm, B.MinimumFocusDistanceCm, CameraTolerance);
}

bool SnapshotsMatch(
    const FRenderMasterCameraSnapshot& A,
    const FRenderMasterCameraSnapshot& B)
{
    return VectorsMatch(A.Location, B.Location)
        && RotationsMatch(A.Rotation, B.Rotation)
        && OptionalNumbersMatch(A.FieldOfViewDeg, B.FieldOfViewDeg)
        && OptionalNumbersMatch(A.FocalLengthMm, B.FocalLengthMm)
        && FMath::IsNearlyEqual(A.ApertureFstop, B.ApertureFstop, CameraTolerance)
        && A.FocusMode == B.FocusMode
        && FMath::IsNearlyEqual(A.FocusDistanceCm, B.FocusDistanceCm, CameraTolerance)
        && A.bExposureCompensationEnabled == B.bExposureCompensationEnabled
        && FMath::IsNearlyEqual(
            A.ExposureCompensationEv, B.ExposureCompensationEv, CameraTolerance)
        && FMath::IsNearlyEqual(
            A.PostProcessBlendWeight, B.PostProcessBlendWeight, CameraTolerance);
}

FString CameraKind(const UCameraComponent* Component)
{
    if (Cast<UCineCameraComponent>(Component) != nullptr) return TEXT("cine_camera");
    return Component != nullptr ? TEXT("camera") : FString();
}

FString FocusMode(const UCineCameraComponent* Component)
{
    if (Component == nullptr) return TEXT("project_default");
    switch (Component->FocusSettings.FocusMethod)
    {
        case ECameraFocusMethod::Manual: return TEXT("manual");
        case ECameraFocusMethod::Tracking: return TEXT("tracking");
        case ECameraFocusMethod::Disable: return TEXT("disabled");
        default: return TEXT("project_default");
    }
}

bool HasSupportedStandardFocusShape(const UCameraComponent* Component)
{
    if (Component == nullptr || Cast<UCineCameraComponent>(Component) != nullptr) return true;
    const FPostProcessSettings& Settings = Component->PostProcessSettings;
    return Settings.bOverride_DepthOfFieldFstop
        == Settings.bOverride_DepthOfFieldFocalDistance;
}

FRenderMasterCameraBounds SnapshotBounds(const UCameraComponent* Component)
{
    FRenderMasterCameraBounds Bounds;
    if (const UCineCameraComponent* Cine = Cast<UCineCameraComponent>(Component))
    {
        Bounds.MinFocalLengthMm = Cine->LensSettings.MinFocalLength;
        Bounds.MaxFocalLengthMm = Cine->LensSettings.MaxFocalLength;
        Bounds.MinApertureFstop = Cine->LensSettings.MinFStop;
        Bounds.MaxApertureFstop = Cine->LensSettings.MaxFStop;
        Bounds.MinimumFocusDistanceCm = Cine->LensSettings.MinimumFocusDistance / 10.0;
    }
    return Bounds;
}

FRenderMasterCameraSnapshot SnapshotCamera(
    const ACameraActor* Actor,
    const UCameraComponent* Component)
{
    FRenderMasterCameraSnapshot Snapshot;
    if (Actor == nullptr || Component == nullptr) return Snapshot;
    Snapshot.Location = Actor->GetActorLocation();
    Snapshot.Rotation = Actor->GetActorRotation();
    const FPostProcessSettings& Settings = Component->PostProcessSettings;
    if (const UCineCameraComponent* Cine = Cast<UCineCameraComponent>(Component))
    {
        Snapshot.FocalLengthMm = Cine->CurrentFocalLength;
        Snapshot.ApertureFstop = Cine->CurrentAperture;
        Snapshot.FocusMode = FocusMode(Cine);
        Snapshot.FocusDistanceCm = Cine->FocusSettings.ManualFocusDistance;
    }
    else
    {
        Snapshot.FieldOfViewDeg = Component->FieldOfView;
        Snapshot.ApertureFstop = Settings.DepthOfFieldFstop;
        Snapshot.FocusMode = Settings.bOverride_DepthOfFieldFstop
            && Settings.bOverride_DepthOfFieldFocalDistance
            ? TEXT("manual") : TEXT("project_default");
        Snapshot.FocusDistanceCm = Settings.DepthOfFieldFocalDistance;
    }
    Snapshot.bExposureCompensationEnabled = Settings.bOverride_AutoExposureBias;
    Snapshot.ExposureCompensationEv = Settings.AutoExposureBias;
    Snapshot.PostProcessBlendWeight = Component->PostProcessBlendWeight;
    return Snapshot;
}

bool IsBoundedSnapshot(
    const FRenderMasterCameraSnapshot& Snapshot,
    const FString& Kind,
    const FRenderMasterCameraBounds& Bounds)
{
    const bool bCine = Kind == TEXT("cine_camera");
    if (Kind != TEXT("camera") && !bCine) return false;
    if (Snapshot.Location.ContainsNaN() || Snapshot.Rotation.ContainsNaN()
        || FMath::Abs(Snapshot.Location.X) > MaxCameraLocationCm
        || FMath::Abs(Snapshot.Location.Y) > MaxCameraLocationCm
        || FMath::Abs(Snapshot.Location.Z) > MaxCameraLocationCm)
    {
        return false;
    }
    if (bCine != Snapshot.FocalLengthMm.IsSet()
        || bCine == Snapshot.FieldOfViewDeg.IsSet()
        || bCine != Bounds.MinFocalLengthMm.IsSet()
        || bCine != Bounds.MaxFocalLengthMm.IsSet())
    {
        return false;
    }
    if (Snapshot.FieldOfViewDeg.IsSet()
        && (Snapshot.FieldOfViewDeg.GetValue() < 5.0
            || Snapshot.FieldOfViewDeg.GetValue() > 170.0))
    {
        return false;
    }
    if (Snapshot.FocalLengthMm.IsSet()
        && (Bounds.MinFocalLengthMm.GetValue() <= 0.0
            || Bounds.MaxFocalLengthMm.GetValue() < Bounds.MinFocalLengthMm.GetValue()
            || Snapshot.FocalLengthMm.GetValue() < Bounds.MinFocalLengthMm.GetValue()
            || Snapshot.FocalLengthMm.GetValue() > Bounds.MaxFocalLengthMm.GetValue()))
    {
        return false;
    }
    if (Bounds.MinApertureFstop < 0.1
        || Bounds.MaxApertureFstop < Bounds.MinApertureFstop
        || Snapshot.ApertureFstop < Bounds.MinApertureFstop
        || Snapshot.ApertureFstop > Bounds.MaxApertureFstop
        || Bounds.MinimumFocusDistanceCm < 0.0
        || Snapshot.FocusDistanceCm < Bounds.MinimumFocusDistanceCm
        || Snapshot.FocusDistanceCm > MaxCameraFocusDistanceCm
        || Snapshot.ExposureCompensationEv < -15.0
        || Snapshot.ExposureCompensationEv > 15.0
        || Snapshot.PostProcessBlendWeight < 0.0
        || Snapshot.PostProcessBlendWeight > 1.0)
    {
        return false;
    }
    if (bCine)
    {
        return Snapshot.FocusMode == TEXT("project_default")
            || Snapshot.FocusMode == TEXT("manual")
            || Snapshot.FocusMode == TEXT("tracking")
            || Snapshot.FocusMode == TEXT("disabled");
    }
    return Snapshot.FocusMode == TEXT("project_default")
        || Snapshot.FocusMode == TEXT("manual");
}

TSet<FString> ChangedProperties(
    const FRenderMasterCameraSnapshot& Before,
    const FRenderMasterCameraSnapshot& After)
{
    TSet<FString> Changed;
    if (!VectorsMatch(Before.Location, After.Location)) Changed.Add(TEXT("location"));
    if (!RotationsMatch(Before.Rotation, After.Rotation)) Changed.Add(TEXT("rotation"));
    if (!OptionalNumbersMatch(Before.FieldOfViewDeg, After.FieldOfViewDeg))
        Changed.Add(TEXT("field_of_view_deg"));
    if (!OptionalNumbersMatch(Before.FocalLengthMm, After.FocalLengthMm))
        Changed.Add(TEXT("focal_length_mm"));
    if (!FMath::IsNearlyEqual(Before.ApertureFstop, After.ApertureFstop, CameraTolerance))
        Changed.Add(TEXT("aperture_fstop"));
    if (Before.FocusMode != After.FocusMode) Changed.Add(TEXT("focus_mode"));
    if (!FMath::IsNearlyEqual(Before.FocusDistanceCm, After.FocusDistanceCm, CameraTolerance))
        Changed.Add(TEXT("focus_distance_cm"));
    if (Before.bExposureCompensationEnabled != After.bExposureCompensationEnabled)
        Changed.Add(TEXT("exposure_compensation_enabled"));
    if (!FMath::IsNearlyEqual(
            Before.ExposureCompensationEv, After.ExposureCompensationEv, CameraTolerance))
        Changed.Add(TEXT("exposure_compensation_ev"));
    if (!FMath::IsNearlyEqual(
            Before.PostProcessBlendWeight, After.PostProcessBlendWeight, CameraTolerance))
        Changed.Add(TEXT("post_process_blend_weight"));
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

void SetOptionalNumber(
    const TSharedRef<FJsonObject>& Json,
    const TCHAR* Field,
    const TOptional<double>& Value)
{
    if (Value.IsSet()) Json->SetNumberField(Field, Value.GetValue());
    else Json->SetField(Field, MakeShared<FJsonValueNull>());
}

TSharedRef<FJsonObject> SnapshotJson(const FRenderMasterCameraSnapshot& Snapshot)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetObjectField(TEXT("location_cm"), VectorJson(Snapshot.Location));
    Json->SetObjectField(
        TEXT("rotation_deg"),
        VectorJson(FVector(Snapshot.Rotation.Roll, Snapshot.Rotation.Pitch, Snapshot.Rotation.Yaw)));
    SetOptionalNumber(Json, TEXT("field_of_view_deg"), Snapshot.FieldOfViewDeg);
    SetOptionalNumber(Json, TEXT("focal_length_mm"), Snapshot.FocalLengthMm);
    Json->SetNumberField(TEXT("aperture_fstop"), Snapshot.ApertureFstop);
    Json->SetStringField(TEXT("focus_mode"), Snapshot.FocusMode);
    Json->SetNumberField(TEXT("focus_distance_cm"), Snapshot.FocusDistanceCm);
    Json->SetBoolField(
        TEXT("exposure_compensation_enabled"), Snapshot.bExposureCompensationEnabled);
    Json->SetNumberField(TEXT("exposure_compensation_ev"), Snapshot.ExposureCompensationEv);
    Json->SetNumberField(TEXT("post_process_blend_weight"), Snapshot.PostProcessBlendWeight);
    return Json;
}

FString OptionalText(const TOptional<double>& Value, const TCHAR* Suffix)
{
    return Value.IsSet()
        ? FString::Printf(TEXT("%.3f%s"), Value.GetValue(), Suffix)
        : TEXT("n/a");
}

FString SnapshotText(const FRenderMasterCameraSnapshot& Snapshot)
{
    return FString::Printf(
        TEXT("Location (cm): X %.3f, Y %.3f, Z %.3f\n")
        TEXT("Rotation (deg): Roll %.3f, Pitch %.3f, Yaw %.3f\n")
        TEXT("FOV: %s\nFocal length: %s\nAperture: f/%.3f\n")
        TEXT("Focus: %s at %.3f cm\nExposure compensation: %s, %.3f EV\n")
        TEXT("Post Process blend: %.3f"),
        Snapshot.Location.X, Snapshot.Location.Y, Snapshot.Location.Z,
        Snapshot.Rotation.Roll, Snapshot.Rotation.Pitch, Snapshot.Rotation.Yaw,
        *OptionalText(Snapshot.FieldOfViewDeg, TEXT(" deg")),
        *OptionalText(Snapshot.FocalLengthMm, TEXT(" mm")),
        Snapshot.ApertureFstop,
        *Snapshot.FocusMode,
        Snapshot.FocusDistanceCm,
        Snapshot.bExposureCompensationEnabled ? TEXT("enabled") : TEXT("disabled"),
        Snapshot.ExposureCompensationEv,
        Snapshot.PostProcessBlendWeight);
}

FString QuoteCameraArgument(const FString& Value)
{
    return FString::Printf(TEXT("\"%s\""), *Value.Replace(TEXT("\""), TEXT("\\\"")));
}
}

bool RenderMasterParseCameraProposalFile(
    const FString& Filename,
    FRenderMasterCameraProposal& OutProposal,
    FString& OutError)
{
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *Filename))
    {
        OutError = FString::Printf(TEXT("Could not read camera proposal: %s"), *Filename);
        return false;
    }
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        OutError = TEXT("Camera proposal is not valid JSON.");
        return false;
    }

    FRenderMasterCameraProposal Parsed;
    FString SchemaVersion;
    bool bModifiesScene = false;
    bool bAutoSave = true;
    bool bUndoSupported = false;
    if (!Root->TryGetStringField(TEXT("schema_version"), SchemaVersion)
        || SchemaVersion != TEXT("0.1")
        || !Root->TryGetStringField(TEXT("proposal_id"), Parsed.ProposalId)
        || !Root->TryGetStringField(TEXT("status"), Parsed.Status)
        || !Root->TryGetStringField(TEXT("request"), Parsed.Request)
        || !Root->TryGetStringField(TEXT("rationale"), Parsed.Rationale)
        || !Root->TryGetBoolField(TEXT("modifies_editor_scene"), bModifiesScene)
        || !Root->TryGetBoolField(TEXT("auto_save"), bAutoSave)
        || !Root->TryGetBoolField(TEXT("undo_supported"), bUndoSupported)
        || !bModifiesScene || bAutoSave || !bUndoSupported)
    {
        OutError = TEXT("Camera proposal metadata or safety flags are invalid.");
        return false;
    }
    Parsed.Status.ToLowerInline();

    TSharedPtr<FJsonObject> Target;
    TSharedPtr<FJsonObject> TargetCamera;
    FString ProjectionMode;
    bool bEditable = false;
    bool bLocked = true;
    if (!ReadObject(Root, TEXT("target"), Target)
        || !Target->TryGetStringField(TEXT("actor_name"), Parsed.ActorName)
        || !Target->TryGetStringField(TEXT("actor_path"), Parsed.ActorPath)
        || !Target->TryGetStringField(TEXT("actor_class"), Parsed.ActorClass)
        || !Target->TryGetStringField(TEXT("component_name"), Parsed.ComponentName)
        || !Target->TryGetStringField(TEXT("camera_kind"), Parsed.CameraKind)
        || !Target->TryGetStringField(TEXT("projection_mode"), ProjectionMode)
        || !Target->TryGetBoolField(TEXT("is_editable"), bEditable)
        || !Target->TryGetBoolField(TEXT("is_locked"), bLocked)
        || ProjectionMode != TEXT("perspective") || !bEditable || bLocked
        || !ReadBounds(Target, Parsed.Bounds)
        || !ReadObject(Target, TEXT("camera"), TargetCamera)
        || !ReadSnapshot(Root, TEXT("before"), Parsed.Before))
    {
        OutError = TEXT("Camera proposal target, bounds, or Before evidence is incomplete.");
        return false;
    }
    Parsed.CameraKind.ToLowerInline();
    Target->TryGetStringField(TEXT("actor_guid"), Parsed.ActorGuid);
    FRenderMasterCameraSnapshot TargetEvidence;
    TSharedPtr<FJsonObject> TargetWrapper = MakeShared<FJsonObject>();
    TargetWrapper->SetObjectField(TEXT("value"), TargetCamera);
    if (!ReadSnapshot(TargetWrapper, TEXT("value"), TargetEvidence)
        || !SnapshotsMatch(TargetEvidence, Parsed.Before)
        || !IsBoundedSnapshot(Parsed.Before, Parsed.CameraKind, Parsed.Bounds))
    {
        OutError = TEXT("Camera proposal Before values do not match bounded target evidence.");
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
            || !IsBoundedSnapshot(Parsed.After, Parsed.CameraKind, Parsed.Bounds)
            || !FMath::IsNearlyEqual(
                Parsed.Before.PostProcessBlendWeight,
                Parsed.After.PostProcessBlendWeight,
                CameraTolerance)
            || !Root->TryGetArrayField(TEXT("changes"), Changes)
            || Changes == nullptr || Changes->IsEmpty() || Changes->Num() > 9
            || !Parsed.MissingCapabilities.IsEmpty())
        {
            OutError = TEXT("Proposed camera action is missing bounded Before/After changes.");
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
                OutError = TEXT("Camera proposal contains an invalid change record.");
                return false;
            }
            Property.ToLowerInline();
            Operation.ToLowerInline();
            const bool bTransform = Property == TEXT("location") || Property == TEXT("rotation");
            const bool bScalar = Property == TEXT("field_of_view_deg")
                || Property == TEXT("focal_length_mm")
                || Property == TEXT("aperture_fstop")
                || Property == TEXT("focus_distance_cm");
            const bool bExposure = Property == TEXT("exposure_compensation_ev");
            const bool bSetOnly = Property == TEXT("focus_mode")
                || Property == TEXT("exposure_compensation_enabled");
            const bool bAllowed = (bTransform && (Operation == TEXT("set") || Operation == TEXT("add")))
                || (bScalar && (Operation == TEXT("set") || Operation == TEXT("add") || Operation == TEXT("multiply")))
                || (bExposure && (Operation == TEXT("set") || Operation == TEXT("add")))
                || (bSetOnly && Operation == TEXT("set"));
            if (!bAllowed || Seen.Contains(Property))
            {
                OutError = TEXT("Camera proposal contains an unsupported or repeated property operation.");
                return false;
            }
            Seen.Add(Property);
            Summary.Add(FString::Printf(TEXT("%s: %s"), *Property, *Operation));
        }
        if (!SetEquals(Expected, Seen)
            || Seen.Contains(TEXT("post_process_blend_weight"))
            || (Parsed.CameraKind == TEXT("camera") && Seen.Contains(TEXT("focal_length_mm")))
            || (Parsed.CameraKind == TEXT("cine_camera") && Seen.Contains(TEXT("field_of_view_deg"))))
        {
            OutError = TEXT("Camera changes do not match type-specific Before/After evidence.");
            return false;
        }
        Parsed.ChangeSummary = FString::Join(Summary, TEXT("\n"));
    }
    else if (Parsed.Status == TEXT("unresolved"))
    {
        if (Parsed.MissingCapabilities.IsEmpty())
        {
            OutError = TEXT("Unresolved camera action must name a missing capability.");
            return false;
        }
    }
    else
    {
        OutError = FString::Printf(TEXT("Unsupported camera proposal status: %s"), *Parsed.Status);
        return false;
    }

    OutProposal = MoveTemp(Parsed);
    return true;
}

bool RenderMasterApplyCameraProperties(
    ACameraActor* CameraActor,
    UCameraComponent* CameraComponent,
    const FString& CameraKindValue,
    const FRenderMasterCameraBounds& Bounds,
    const FRenderMasterCameraSnapshot& Before,
    const FRenderMasterCameraSnapshot& After,
    FString& OutError,
    bool bMarkPackageDirty)
{
    if (CameraActor == nullptr || CameraComponent == nullptr)
    {
        OutError = TEXT("The target camera no longer exists.");
        return false;
    }
    if (CameraKind(CameraComponent) != CameraKindValue
        || !IsBoundedSnapshot(After, CameraKindValue, Bounds)
        || !FMath::IsNearlyEqual(
            Before.PostProcessBlendWeight,
            After.PostProcessBlendWeight,
            CameraTolerance))
    {
        OutError = TEXT("The proposed camera state is not valid for this camera type.");
        return false;
    }

    FScopedTransaction Transaction(
        NSLOCTEXT("RenderMasterBot", "ApplyAssistantCamera", "RenderMasterBot: Apply Camera Properties"));
    CameraActor->Modify();
    CameraComponent->Modify();
    const bool bTransformChanged = !VectorsMatch(Before.Location, After.Location)
        || !RotationsMatch(Before.Rotation, After.Rotation);
    if (bTransformChanged)
    {
        const FTransform NewTransform(
            After.Rotation,
            After.Location,
            CameraActor->GetActorScale3D());
        if (!CameraActor->SetActorTransform(NewTransform, false, nullptr, ETeleportType::TeleportPhysics))
        {
            Transaction.Cancel();
            OutError = TEXT("Unreal rejected the proposed camera Transform.");
            return false;
        }
    }

    FPostProcessSettings& Settings = CameraComponent->PostProcessSettings;
    Settings.bOverride_AutoExposureBias = After.bExposureCompensationEnabled;
    Settings.AutoExposureBias = After.ExposureCompensationEv;
    if (UCineCameraComponent* Cine = Cast<UCineCameraComponent>(CameraComponent))
    {
        FCameraFocusSettings NewFocusSettings = Cine->FocusSettings;
        if (After.FocusMode == TEXT("manual"))
            NewFocusSettings.FocusMethod = ECameraFocusMethod::Manual;
        else if (After.FocusMode == TEXT("tracking"))
            NewFocusSettings.FocusMethod = ECameraFocusMethod::Tracking;
        else if (After.FocusMode == TEXT("disabled"))
            NewFocusSettings.FocusMethod = ECameraFocusMethod::Disable;
        else
            NewFocusSettings.FocusMethod = ECameraFocusMethod::DoNotOverride;
        NewFocusSettings.ManualFocusDistance = After.FocusDistanceCm;
        Cine->SetCurrentFocalLength(After.FocalLengthMm.GetValue());
        Cine->SetCurrentAperture(After.ApertureFstop);
        Cine->SetFocusSettings(NewFocusSettings);
    }
    else
    {
        CameraComponent->FieldOfView = After.FieldOfViewDeg.GetValue();
        Settings.DepthOfFieldFstop = After.ApertureFstop;
        Settings.DepthOfFieldFocalDistance = After.FocusDistanceCm;
        const bool bManual = After.FocusMode == TEXT("manual");
        Settings.bOverride_DepthOfFieldFstop = bManual;
        Settings.bOverride_DepthOfFieldFocalDistance = bManual;
    }

    CameraComponent->PostEditChange();
    CameraComponent->MarkRenderStateDirty();
    if (bTransformChanged) CameraActor->PostEditMove(true);
    if (bMarkPackageDirty) CameraActor->MarkPackageDirty();
    if (GEditor != nullptr) GEditor->RedrawLevelEditingViewports();
    return true;
}

FRenderMasterCameraAssistant::FRenderMasterCameraAssistant(
    TSharedPtr<FRenderMasterWorkflowController> InWorkflowController)
    : WorkflowController(MoveTemp(InWorkflowController))
{
}

FRenderMasterCameraAssistant::~FRenderMasterCameraAssistant()
{
    Shutdown();
}

void FRenderMasterCameraAssistant::Initialize()
{
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateSP(AsShared(), &FRenderMasterCameraAssistant::Tick),
        0.2f);
}

void FRenderMasterCameraAssistant::Shutdown()
{
    if (TickHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
        TickHandle.Reset();
    }
    if (ProcessHandle.IsValid()) FPlatformProcess::TerminateProc(ProcessHandle, true);
    CloseProcessResources();
}

bool FRenderMasterCameraAssistant::StartProposal(
    const FString& Prompt,
    ACameraActor* CameraActor)
{
    if (!CanStart()) return false;
    const FString CleanPrompt = Prompt.TrimStartAndEnd();
    if (CleanPrompt.IsEmpty())
    {
        Fail(TEXT("Enter a camera request before preparing an action."));
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
        TEXT("camera_%s"),
        *FDateTime::Now().ToString(TEXT("%Y%m%d_%H%M%S_%s")));
    const FString RequestDirectory = FPaths::Combine(Root, TEXT("assistant-camera"), ProposalId);
    IFileManager::Get().MakeDirectory(*RequestDirectory, true);
    const FString PromptPath = FPaths::Combine(RequestDirectory, TEXT("request.txt"));
    const FString ContextPath = FPaths::Combine(RequestDirectory, TEXT("camera_context.json"));
    ProposalOutputPath = FPaths::Combine(RequestDirectory, TEXT("camera_proposal.json"));

    FString Error;
    if (!FFileHelper::SaveStringToFile(
            CleanPrompt,
            *PromptPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)
        || !WriteCameraContext(ContextPath, CameraActor, Error))
    {
        Fail(Error.IsEmpty() ? TEXT("Could not write the camera request.") : Error);
        return false;
    }
    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-camera-propose --prompt-file %s --context %s --proposal-id %s --output %s"),
        *QuoteCameraArgument(PromptPath),
        *QuoteCameraArgument(ContextPath),
        *QuoteCameraArgument(ProposalId),
        *QuoteCameraArgument(ProposalOutputPath));

    Proposal = FRenderMasterCameraProposal();
    ErrorText.Reset();
    ProcessLog.Reset();
    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        Fail(TEXT("Could not create camera assistant process pipes."));
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
    State = ERenderMasterCameraAssistantState::Planning;
    AppendLog(FString::Printf(TEXT("Preparing a bounded camera proposal (process %u)."), ProcessId));
    return true;
}

bool FRenderMasterCameraAssistant::WriteCameraContext(
    const FString& Filename,
    ACameraActor* CameraActor,
    FString& OutError)
{
    if (CameraActor == nullptr || CameraActor->GetWorld() == nullptr || CameraActor->IsTemplate())
    {
        OutError = TEXT("Select exactly one Camera Actor or Cine Camera Actor.");
        return false;
    }
    UCameraComponent* Component = CameraActor->GetCameraComponent();
    if (Component == nullptr || Component->ProjectionMode != ECameraProjectionMode::Perspective)
    {
        OutError = TEXT("The selected camera must use perspective projection.");
        return false;
    }
    if (!CameraActor->IsEditable() || CameraActor->IsLockLocation())
    {
        OutError = TEXT("The selected camera is not editable or its location is locked.");
        return false;
    }
    if (!HasSupportedStandardFocusShape(Component))
    {
        OutError = TEXT("The standard camera has a partial depth-of-field override. Enable both focal distance and F-stop, or disable both, before preparing an action.");
        return false;
    }

    CapturedActorPath = CameraActor->GetPathName();
    CapturedActorClass = CameraActor->GetClass()->GetName();
    CapturedActorGuid = CameraActor->GetActorGuid().IsValid()
        ? CameraActor->GetActorGuid().ToString(EGuidFormats::Digits)
        : FString();
    CapturedComponentName = Component->GetName();
    CapturedCameraKind = CameraKind(Component);
    CapturedBounds = SnapshotBounds(Component);
    CapturedCamera = SnapshotCamera(CameraActor, Component);
    if (!IsBoundedSnapshot(CapturedCamera, CapturedCameraKind, CapturedBounds))
    {
        OutError = TEXT("The selected camera has properties outside the supported safety boundary.");
        return false;
    }
    TargetCameraActor = CameraActor;
    TargetCameraComponent = Component;

    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Root->SetStringField(TEXT("project_name"), FApp::GetProjectName());
    Root->SetStringField(TEXT("level_path"), CameraActor->GetWorld()->GetPackage()->GetName());
    Root->SetStringField(TEXT("actor_name"), CameraActor->GetActorLabel());
    Root->SetStringField(TEXT("actor_path"), CapturedActorPath);
    Root->SetStringField(TEXT("actor_class"), CapturedActorClass);
    if (!CapturedActorGuid.IsEmpty()) Root->SetStringField(TEXT("actor_guid"), CapturedActorGuid);
    Root->SetStringField(TEXT("component_name"), CapturedComponentName);
    Root->SetStringField(TEXT("camera_kind"), CapturedCameraKind);
    FString Mobility = TEXT("movable");
    if (Component->Mobility == EComponentMobility::Static) Mobility = TEXT("static");
    else if (Component->Mobility == EComponentMobility::Stationary) Mobility = TEXT("stationary");
    Root->SetStringField(TEXT("component_mobility"), Mobility);
    Root->SetStringField(TEXT("projection_mode"), TEXT("perspective"));
    Root->SetBoolField(TEXT("is_editable"), true);
    Root->SetBoolField(TEXT("is_locked"), false);
    SetOptionalNumber(Root, TEXT("min_focal_length_mm"), CapturedBounds.MinFocalLengthMm);
    SetOptionalNumber(Root, TEXT("max_focal_length_mm"), CapturedBounds.MaxFocalLengthMm);
    Root->SetNumberField(TEXT("min_aperture_fstop"), CapturedBounds.MinApertureFstop);
    Root->SetNumberField(TEXT("max_aperture_fstop"), CapturedBounds.MaxApertureFstop);
    Root->SetNumberField(
        TEXT("minimum_focus_distance_cm"), CapturedBounds.MinimumFocusDistanceCm);
    Root->SetObjectField(TEXT("camera"), SnapshotJson(CapturedCamera));

    FString JsonText;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&JsonText);
    if (!FJsonSerializer::Serialize(Root, Writer)
        || !FFileHelper::SaveStringToFile(
            JsonText,
            *Filename,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        OutError = FString::Printf(TEXT("Could not write camera context: %s"), *Filename);
        TargetCameraActor.Reset();
        TargetCameraComponent.Reset();
        return false;
    }
    return true;
}

bool FRenderMasterCameraAssistant::ApplyProposal()
{
    FString Error;
    if (!CanApply() || !RevalidateTarget(Error))
    {
        Fail(Error.IsEmpty() ? TEXT("No camera proposal is ready to apply.") : Error);
        return false;
    }
    ACameraActor* Actor = TargetCameraActor.Get();
    UCameraComponent* Component = TargetCameraComponent.Get();
    if (!RenderMasterApplyCameraProperties(
            Actor,
            Component,
            CapturedCameraKind,
            CapturedBounds,
            Proposal.Before,
            Proposal.After,
            Error))
    {
        Fail(Error);
        return false;
    }
    State = ERenderMasterCameraAssistantState::Applied;
    AppendLog(FString::Printf(
        TEXT("Applied approved camera properties to %s. The level was not saved; use Ctrl+Z to undo."),
        *Actor->GetActorLabel()));
    return true;
}

bool FRenderMasterCameraAssistant::RevalidateTarget(FString& OutError) const
{
    ACameraActor* Actor = TargetCameraActor.Get();
    UCameraComponent* Component = TargetCameraComponent.Get();
    if (Actor == nullptr || Component == nullptr || Actor->GetWorld() == nullptr)
    {
        OutError = TEXT("The captured camera no longer exists.");
        return false;
    }
    const FString CurrentGuid = Actor->GetActorGuid().IsValid()
        ? Actor->GetActorGuid().ToString(EGuidFormats::Digits)
        : FString();
    if (Actor->GetPathName() != CapturedActorPath
        || Actor->GetClass()->GetName() != CapturedActorClass
        || CurrentGuid != CapturedActorGuid
        || Component->GetName() != CapturedComponentName
        || CameraKind(Component) != CapturedCameraKind
        || Component->ProjectionMode != ECameraProjectionMode::Perspective
        || Proposal.ActorPath != CapturedActorPath
        || Proposal.ActorClass != CapturedActorClass
        || Proposal.ActorGuid != CapturedActorGuid
        || Proposal.ComponentName != CapturedComponentName
        || Proposal.CameraKind != CapturedCameraKind)
    {
        OutError = TEXT("The selected camera identity or type changed after the proposal was created.");
        return false;
    }
    if (!Actor->IsEditable() || Actor->IsLockLocation())
    {
        OutError = TEXT("The captured camera is no longer editable or is now locked.");
        return false;
    }
    if (!HasSupportedStandardFocusShape(Component)
        || !BoundsMatch(Proposal.Bounds, CapturedBounds)
        || !BoundsMatch(SnapshotBounds(Component), CapturedBounds)
        || !SnapshotsMatch(Proposal.Before, CapturedCamera)
        || !SnapshotsMatch(SnapshotCamera(Actor, Component), CapturedCamera))
    {
        OutError = TEXT("The camera properties or lens bounds changed after the proposal was created. Prepare a new action.");
        return false;
    }
    return true;
}

void FRenderMasterCameraAssistant::RejectProposal()
{
    if (State == ERenderMasterCameraAssistantState::Planning)
    {
        Cancel();
        CloseProcessResources();
    }
    State = ERenderMasterCameraAssistantState::Rejected;
    AppendLog(TEXT("Camera proposal rejected. No Editor scene change was applied."));
}

void FRenderMasterCameraAssistant::Cancel()
{
    if (ProcessHandle.IsValid()) FPlatformProcess::TerminateProc(ProcessHandle, true);
}

bool FRenderMasterCameraAssistant::CanStart() const
{
    return !ProcessHandle.IsValid();
}

bool FRenderMasterCameraAssistant::CanApply() const
{
    return State == ERenderMasterCameraAssistantState::Proposed
        && TargetCameraActor.IsValid() && TargetCameraComponent.IsValid();
}

bool FRenderMasterCameraAssistant::IsPlanning() const
{
    return State == ERenderMasterCameraAssistantState::Planning;
}

FText FRenderMasterCameraAssistant::GetStateText() const
{
    switch (State)
    {
        case ERenderMasterCameraAssistantState::Planning: return NSLOCTEXT("RenderMasterBot", "CameraPlanning", "Planning");
        case ERenderMasterCameraAssistantState::Proposed: return NSLOCTEXT("RenderMasterBot", "CameraProposed", "Approval required");
        case ERenderMasterCameraAssistantState::Unresolved: return NSLOCTEXT("RenderMasterBot", "CameraUnresolved", "Unresolved");
        case ERenderMasterCameraAssistantState::Failed: return NSLOCTEXT("RenderMasterBot", "CameraFailed", "Failed");
        case ERenderMasterCameraAssistantState::Applied: return NSLOCTEXT("RenderMasterBot", "CameraApplied", "Applied");
        case ERenderMasterCameraAssistantState::Rejected: return NSLOCTEXT("RenderMasterBot", "CameraRejected", "Rejected");
        default: return NSLOCTEXT("RenderMasterBot", "CameraReady", "Ready");
    }
}

FText FRenderMasterCameraAssistant::GetSummaryText() const
{
    if (State == ERenderMasterCameraAssistantState::Planning)
        return FText::FromString(ProcessLog.IsEmpty() ? TEXT("Preparing a bounded camera proposal...") : ProcessLog);
    if (State == ERenderMasterCameraAssistantState::Proposed)
    {
        return FText::FromString(FString::Printf(
            TEXT("Target\n%s\n%s\nKind: %s\n\nChanged properties\n%s\n\nBefore\n%s\n\nAfter\n%s\n\nWhy\n%s\n\nNo level change has been applied."),
            *Proposal.ActorName,
            *Proposal.ActorPath,
            *Proposal.CameraKind,
            *Proposal.ChangeSummary,
            *SnapshotText(Proposal.Before),
            *SnapshotText(Proposal.After),
            *Proposal.Rationale));
    }
    if (State == ERenderMasterCameraAssistantState::Unresolved)
        return FText::FromString(FString::Printf(TEXT("No camera action was proposed.\n\nWhy\n%s\n\nMissing capability\n%s"), *Proposal.Rationale, *Proposal.MissingCapabilities));
    if (State == ERenderMasterCameraAssistantState::Failed) return FText::FromString(ErrorText);
    if (State == ERenderMasterCameraAssistantState::Applied)
        return FText::FromString(TEXT("The approved camera properties were applied. The level was not saved; use Ctrl+Z to undo."));
    if (State == ERenderMasterCameraAssistantState::Rejected)
        return FText::FromString(TEXT("The camera proposal was rejected. No Editor scene change was applied."));
    return FText::FromString(TEXT("Select one Camera Actor or Cine Camera Actor, describe a numeric Transform, lens, focus, or exposure-compensation edit, then prepare a reviewable action."));
}

FLinearColor FRenderMasterCameraAssistant::GetStateColor() const
{
    switch (State)
    {
        case ERenderMasterCameraAssistantState::Proposed: return FLinearColor(0.18f, 0.55f, 0.95f, 1.0f);
        case ERenderMasterCameraAssistantState::Applied: return FLinearColor(0.12f, 0.55f, 0.28f, 1.0f);
        case ERenderMasterCameraAssistantState::Unresolved: return FLinearColor(0.65f, 0.45f, 0.08f, 1.0f);
        case ERenderMasterCameraAssistantState::Failed: return FLinearColor(0.70f, 0.14f, 0.12f, 1.0f);
        default: return FLinearColor(0.14f, 0.20f, 0.30f, 1.0f);
    }
}

bool FRenderMasterCameraAssistant::Tick(float DeltaTime)
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

void FRenderMasterCameraAssistant::CompleteProcess()
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
            ? FString::Printf(TEXT("Camera proposal process exited with code %d."), ReturnCode)
            : ProcessLog);
        return;
    }
    FString Error;
    if (!RenderMasterParseCameraProposalFile(ProposalOutputPath, Proposal, Error))
    {
        Fail(Error);
        return;
    }
    if (Proposal.ActorPath != CapturedActorPath
        || Proposal.ActorClass != CapturedActorClass
        || Proposal.ActorGuid != CapturedActorGuid
        || Proposal.ComponentName != CapturedComponentName
        || Proposal.CameraKind != CapturedCameraKind
        || !BoundsMatch(Proposal.Bounds, CapturedBounds)
        || !SnapshotsMatch(Proposal.Before, CapturedCamera))
    {
        Fail(TEXT("Camera proposal target evidence does not match the Editor-captured camera."));
        return;
    }
    State = Proposal.Status == TEXT("proposed")
        ? ERenderMasterCameraAssistantState::Proposed
        : ERenderMasterCameraAssistantState::Unresolved;
}

void FRenderMasterCameraAssistant::CloseProcessResources()
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

void FRenderMasterCameraAssistant::AppendLog(const FString& Line)
{
    const FString Clean = Line.TrimStartAndEnd();
    if (Clean.IsEmpty()) return;
    if (!ProcessLog.IsEmpty()) ProcessLog += TEXT("\n");
    ProcessLog += Clean;
    constexpr int32 MaxLogChars = 8000;
    if (ProcessLog.Len() > MaxLogChars) ProcessLog.RightChopInline(ProcessLog.Len() - MaxLogChars);
}

void FRenderMasterCameraAssistant::Fail(const FString& Error)
{
    ErrorText = Error.IsEmpty() ? TEXT("Camera proposal failed.") : Error;
    State = ERenderMasterCameraAssistantState::Failed;
    AppendLog(ErrorText);
}
