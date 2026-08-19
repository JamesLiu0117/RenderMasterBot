#include "RenderMasterCameraBatchAssistant.h"

#include "RenderMasterWorkflowController.h"

#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
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

void SetOptionalNumber(
    const TSharedRef<FJsonObject>& Json,
    const TCHAR* Field,
    const TOptional<double>& Value)
{
    if (Value.IsSet()) Json->SetNumberField(Field, Value.GetValue());
    else Json->SetField(Field, MakeShared<FJsonValueNull>());
}

TSharedRef<FJsonObject> VectorJson(const FVector& Value)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetNumberField(TEXT("x"), Value.X);
    Json->SetNumberField(TEXT("y"), Value.Y);
    Json->SetNumberField(TEXT("z"), Value.Z);
    return Json;
}

TSharedRef<FJsonObject> SnapshotJson(const FRenderMasterCameraSnapshot& Snapshot)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetObjectField(TEXT("location_cm"), VectorJson(Snapshot.Location));
    Json->SetObjectField(
        TEXT("rotation_deg"),
        VectorJson(FVector(
            Snapshot.Rotation.Roll,
            Snapshot.Rotation.Pitch,
            Snapshot.Rotation.Yaw)));
    SetOptionalNumber(Json, TEXT("field_of_view_deg"), Snapshot.FieldOfViewDeg);
    SetOptionalNumber(Json, TEXT("focal_length_mm"), Snapshot.FocalLengthMm);
    Json->SetNumberField(TEXT("aperture_fstop"), Snapshot.ApertureFstop);
    Json->SetStringField(TEXT("focus_mode"), Snapshot.FocusMode);
    Json->SetNumberField(TEXT("focus_distance_cm"), Snapshot.FocusDistanceCm);
    Json->SetBoolField(
        TEXT("exposure_compensation_enabled"),
        Snapshot.bExposureCompensationEnabled);
    Json->SetNumberField(
        TEXT("exposure_compensation_ev"), Snapshot.ExposureCompensationEv);
    Json->SetNumberField(
        TEXT("post_process_blend_weight"), Snapshot.PostProcessBlendWeight);
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
        TEXT("FOV: %s | Focal length: %s | Aperture: f/%.3f\n")
        TEXT("Focus: %s at %.3f cm | Exposure: %s, %.3f EV"),
        Snapshot.Location.X,
        Snapshot.Location.Y,
        Snapshot.Location.Z,
        Snapshot.Rotation.Roll,
        Snapshot.Rotation.Pitch,
        Snapshot.Rotation.Yaw,
        *OptionalText(Snapshot.FieldOfViewDeg, TEXT(" deg")),
        *OptionalText(Snapshot.FocalLengthMm, TEXT(" mm")),
        Snapshot.ApertureFstop,
        *Snapshot.FocusMode,
        Snapshot.FocusDistanceCm,
        Snapshot.bExposureCompensationEnabled ? TEXT("enabled") : TEXT("disabled"),
        Snapshot.ExposureCompensationEv);
}

FString MobilityText(const UCameraComponent* Component)
{
    if (Component != nullptr && Component->Mobility == EComponentMobility::Static)
        return TEXT("static");
    if (Component != nullptr && Component->Mobility == EComponentMobility::Stationary)
        return TEXT("stationary");
    return TEXT("movable");
}

FString QuoteCameraBatchArgument(const FString& Value)
{
    return FString::Printf(
        TEXT("\"%s\""), *Value.Replace(TEXT("\""), TEXT("\\\"")));
}

bool SerializeJsonObject(const TSharedRef<FJsonObject>& Json, FString& Out)
{
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
    return FJsonSerializer::Serialize(Json, Writer);
}

TSharedRef<FJsonObject> CameraContextJson(
    const FRenderMasterCameraProposal& Capture,
    const FString& ProjectName,
    const FString& LevelPath,
    const FString& Mobility)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Json->SetStringField(TEXT("project_name"), ProjectName);
    Json->SetStringField(TEXT("level_path"), LevelPath);
    Json->SetStringField(TEXT("actor_name"), Capture.ActorName);
    Json->SetStringField(TEXT("actor_path"), Capture.ActorPath);
    Json->SetStringField(TEXT("actor_class"), Capture.ActorClass);
    if (!Capture.ActorGuid.IsEmpty())
        Json->SetStringField(TEXT("actor_guid"), Capture.ActorGuid);
    Json->SetStringField(TEXT("component_name"), Capture.ComponentName);
    Json->SetStringField(TEXT("camera_kind"), Capture.CameraKind);
    Json->SetStringField(TEXT("component_mobility"), Mobility);
    Json->SetStringField(TEXT("projection_mode"), TEXT("perspective"));
    Json->SetBoolField(TEXT("is_editable"), true);
    Json->SetBoolField(TEXT("is_locked"), false);
    SetOptionalNumber(Json, TEXT("min_focal_length_mm"), Capture.Bounds.MinFocalLengthMm);
    SetOptionalNumber(Json, TEXT("max_focal_length_mm"), Capture.Bounds.MaxFocalLengthMm);
    Json->SetNumberField(TEXT("min_aperture_fstop"), Capture.Bounds.MinApertureFstop);
    Json->SetNumberField(TEXT("max_aperture_fstop"), Capture.Bounds.MaxApertureFstop);
    Json->SetNumberField(
        TEXT("minimum_focus_distance_cm"),
        Capture.Bounds.MinimumFocusDistanceCm);
    Json->SetObjectField(TEXT("camera"), SnapshotJson(Capture.Before));
    return Json;
}

TSharedRef<FJsonObject> SyntheticCameraProposal(
    const FString& ProposalId,
    const FString& Request,
    const FString& Rationale,
    const FString& Status,
    const TSharedPtr<FJsonObject>& Target,
    const TSharedPtr<FJsonObject>& Before,
    const TSharedPtr<FJsonObject>& After = nullptr,
    const TArray<TSharedPtr<FJsonValue>>* Changes = nullptr)
{
    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Root->SetStringField(TEXT("proposal_id"), ProposalId);
    Root->SetStringField(TEXT("status"), Status);
    Root->SetStringField(TEXT("request"), Request);
    Root->SetStringField(TEXT("rationale"), Rationale);
    Root->SetBoolField(TEXT("modifies_editor_scene"), true);
    Root->SetBoolField(TEXT("auto_save"), false);
    Root->SetBoolField(TEXT("undo_supported"), true);
    Root->SetObjectField(TEXT("target"), Target);
    Root->SetObjectField(TEXT("before"), Before);
    if (After.IsValid()) Root->SetObjectField(TEXT("after"), After);
    if (Changes != nullptr) Root->SetArrayField(TEXT("changes"), *Changes);
    TArray<TSharedPtr<FJsonValue>> Missing;
    if (Status == TEXT("unresolved"))
    {
        Missing.Add(MakeShared<FJsonValueString>(TEXT("batch evidence validation")));
    }
    Root->SetArrayField(TEXT("missing_capabilities"), Missing);
    return Root;
}

bool ParseSyntheticCameraProposal(
    const TSharedRef<FJsonObject>& Json,
    FRenderMasterCameraProposal& Out,
    FString& OutError)
{
    FString Text;
    if (!SerializeJsonObject(Json, Text))
    {
        OutError = TEXT("Could not serialize nested camera action evidence.");
        return false;
    }
    return RenderMasterParseCameraProposalJson(Text, Out, OutError);
}

bool ParseSelectionCamera(
    const TSharedPtr<FJsonObject>& Camera,
    const FString& ProposalId,
    const FString& Request,
    const FString& Rationale,
    const FString& ProjectName,
    const FString& LevelPath,
    FRenderMasterCameraProposal& Out,
    FString& OutError)
{
    FString CameraProject;
    FString CameraLevel;
    FString Mobility;
    TSharedPtr<FJsonObject> Before;
    if (!Camera.IsValid()
        || !Camera->TryGetStringField(TEXT("project_name"), CameraProject)
        || !Camera->TryGetStringField(TEXT("level_path"), CameraLevel)
        || !Camera->TryGetStringField(TEXT("component_mobility"), Mobility)
        || CameraProject != ProjectName || CameraLevel != LevelPath
        || (Mobility != TEXT("static")
            && Mobility != TEXT("stationary")
            && Mobility != TEXT("movable"))
        || !ReadObject(Camera, TEXT("camera"), Before))
    {
        OutError = TEXT("Camera selection evidence has an invalid project, level, or mobility.");
        return false;
    }
    const TSharedRef<FJsonObject> Synthetic = SyntheticCameraProposal(
        ProposalId,
        Request,
        Rationale,
        TEXT("unresolved"),
        Camera,
        Before);
    if (!ParseSyntheticCameraProposal(Synthetic, Out, OutError)) return false;
    Out.Status = TEXT("proposed");
    Out.MissingCapabilities.Reset();
    Out.After = Out.Before;
    return true;
}

bool CameraEvidenceMatches(
    const FRenderMasterCameraProposal& A,
    const FRenderMasterCameraProposal& B)
{
    return A.ActorName == B.ActorName
        && A.ActorPath == B.ActorPath
        && A.ActorClass == B.ActorClass
        && A.ActorGuid == B.ActorGuid
        && A.ComponentName == B.ComponentName
        && A.CameraKind == B.CameraKind
        && RenderMasterCameraBoundsMatch(A.Bounds, B.Bounds)
        && RenderMasterCameraSnapshotsMatch(A.Before, B.Before);
}

void ReadMissingCapabilities(
    const TSharedPtr<FJsonObject>& Root,
    FString& Out)
{
    const TArray<TSharedPtr<FJsonValue>>* Missing = nullptr;
    if (!Root->TryGetArrayField(TEXT("missing_capabilities"), Missing)
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

bool RenderMasterParseCameraBatchProposalFile(
    const FString& Filename,
    FRenderMasterCameraBatchProposal& OutProposal,
    FString& OutError)
{
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *Filename))
    {
        OutError = FString::Printf(
            TEXT("Could not read camera batch proposal: %s"), *Filename);
        return false;
    }
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        OutError = TEXT("Camera batch proposal is not valid JSON.");
        return false;
    }

    FRenderMasterCameraBatchProposal Parsed;
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
        OutError = TEXT("Camera batch proposal metadata or safety flags are invalid.");
        return false;
    }
    Parsed.Status.ToLowerInline();
    ReadMissingCapabilities(Root, Parsed.MissingCapabilities);

    TSharedPtr<FJsonObject> Selection;
    FString ProjectName;
    FString LevelPath;
    const TArray<TSharedPtr<FJsonValue>>* Cameras = nullptr;
    if (!ReadObject(Root, TEXT("selection"), Selection)
        || !Selection->TryGetStringField(TEXT("project_name"), ProjectName)
        || !Selection->TryGetStringField(TEXT("level_path"), LevelPath)
        || !Selection->TryGetArrayField(TEXT("cameras"), Cameras)
        || Cameras == nullptr || Cameras->Num() < 2 || Cameras->Num() > 16)
    {
        OutError = TEXT("Camera batch selection must contain 2-16 cameras in one level.");
        return false;
    }

    TArray<FRenderMasterCameraProposal> Selected;
    TSet<FString> Paths;
    TSet<FString> Guids;
    for (const TSharedPtr<FJsonValue>& Value : *Cameras)
    {
        const TSharedPtr<FJsonObject>* Camera = nullptr;
        FRenderMasterCameraProposal Evidence;
        if (!Value.IsValid() || !Value->TryGetObject(Camera) || Camera == nullptr
            || !ParseSelectionCamera(
                *Camera,
                Parsed.ProposalId,
                Parsed.Request,
                Parsed.Rationale,
                ProjectName,
                LevelPath,
                Evidence,
                OutError))
        {
            if (OutError.IsEmpty())
                OutError = TEXT("Camera batch selection contains invalid evidence.");
            return false;
        }
        if (Paths.Contains(Evidence.ActorPath)
            || (!Evidence.ActorGuid.IsEmpty() && Guids.Contains(Evidence.ActorGuid)))
        {
            OutError = TEXT("Camera batch selection repeats an Actor identity.");
            return false;
        }
        Paths.Add(Evidence.ActorPath);
        if (!Evidence.ActorGuid.IsEmpty()) Guids.Add(Evidence.ActorGuid);
        Selected.Add(MoveTemp(Evidence));
    }

    const TArray<TSharedPtr<FJsonValue>>* Actions = nullptr;
    if (Parsed.Status == TEXT("unresolved"))
    {
        if (Parsed.MissingCapabilities.IsEmpty()
            || (Root->TryGetArrayField(TEXT("actions"), Actions)
                && Actions != nullptr && !Actions->IsEmpty()))
        {
            OutError = TEXT("Unresolved camera batches require a gap and no actions.");
            return false;
        }
        OutProposal = MoveTemp(Parsed);
        return true;
    }
    if (Parsed.Status != TEXT("proposed")
        || !Parsed.MissingCapabilities.IsEmpty()
        || !Root->TryGetArrayField(TEXT("actions"), Actions)
        || Actions == nullptr || Actions->Num() != Selected.Num())
    {
        OutError = TEXT("Proposed camera batches must cover every selected camera.");
        return false;
    }

    bool bAnyChange = false;
    for (int32 Index = 0; Index < Actions->Num(); ++Index)
    {
        const TSharedPtr<FJsonObject>* Action = nullptr;
        TSharedPtr<FJsonObject> Target;
        TSharedPtr<FJsonObject> Before;
        TSharedPtr<FJsonObject> After;
        const TArray<TSharedPtr<FJsonValue>>* Changes = nullptr;
        if (!(*Actions)[Index].IsValid()
            || !(*Actions)[Index]->TryGetObject(Action) || Action == nullptr
            || !ReadObject(*Action, TEXT("target"), Target)
            || !ReadObject(*Action, TEXT("before"), Before)
            || !ReadObject(*Action, TEXT("after"), After)
            || !(*Action)->TryGetArrayField(TEXT("changes"), Changes)
            || Changes == nullptr || Changes->Num() > 9)
        {
            OutError = TEXT("Camera batch contains an invalid nested action.");
            return false;
        }

        FRenderMasterCameraProposal ActionTargetEvidence;
        if (!ParseSelectionCamera(
                Target,
                Parsed.ProposalId,
                Parsed.Request,
                Parsed.Rationale,
                ProjectName,
                LevelPath,
                ActionTargetEvidence,
                OutError)
            || !CameraEvidenceMatches(Selected[Index], ActionTargetEvidence))
        {
            if (OutError.IsEmpty())
                OutError = TEXT(
                    "Camera batch target does not match the ordered selection evidence.");
            return false;
        }

        FRenderMasterCameraProposal ActionProposal;
        if (Changes->IsEmpty())
        {
            TSharedPtr<FJsonObject> BeforeTarget = MakeShared<FJsonObject>(*Target);
            BeforeTarget->SetObjectField(TEXT("camera"), Before);
            TSharedPtr<FJsonObject> AfterTarget = MakeShared<FJsonObject>(*Target);
            AfterTarget->SetObjectField(TEXT("camera"), After);
            FRenderMasterCameraProposal BeforeEvidence;
            FRenderMasterCameraProposal AfterEvidence;
            if (!ParseSelectionCamera(
                    BeforeTarget,
                    Parsed.ProposalId,
                    Parsed.Request,
                    Parsed.Rationale,
                    ProjectName,
                    LevelPath,
                    BeforeEvidence,
                    OutError)
                || !ParseSelectionCamera(
                    AfterTarget,
                    Parsed.ProposalId,
                    Parsed.Request,
                    Parsed.Rationale,
                    ProjectName,
                    LevelPath,
                    AfterEvidence,
                    OutError)
                || !CameraEvidenceMatches(Selected[Index], BeforeEvidence)
                || !CameraEvidenceMatches(BeforeEvidence, AfterEvidence))
            {
                if (OutError.IsEmpty())
                    OutError = TEXT("Unchanged camera action does not preserve its evidence.");
                return false;
            }
            ActionProposal = MoveTemp(BeforeEvidence);
            ActionProposal.ChangeSummary = TEXT("No change (already at target)");
        }
        else
        {
            const TSharedRef<FJsonObject> Synthetic = SyntheticCameraProposal(
                Parsed.ProposalId,
                Parsed.Request,
                Parsed.Rationale,
                TEXT("proposed"),
                Target,
                Before,
                After,
                Changes);
            if (!ParseSyntheticCameraProposal(Synthetic, ActionProposal, OutError))
                return false;
            bAnyChange = true;
        }
        if (!CameraEvidenceMatches(Selected[Index], ActionProposal))
        {
            OutError = TEXT(
                "Camera batch action order or target evidence does not match the selection.");
            return false;
        }
        Parsed.Actions.Add(MoveTemp(ActionProposal));
    }
    if (!bAnyChange)
    {
        OutError = TEXT("Camera batch proposal does not change any selected camera.");
        return false;
    }
    OutProposal = MoveTemp(Parsed);
    return true;
}

bool RenderMasterApplyCameraPropertiesBatch(
    const TArray<ACameraActor*>& CameraActors,
    const TArray<UCameraComponent*>& CameraComponents,
    const TArray<FRenderMasterCameraProposal>& Actions,
    FString& OutError,
    bool bMarkPackageDirty)
{
    if (CameraActors.Num() < 2 || CameraActors.Num() > 16
        || CameraComponents.Num() != CameraActors.Num()
        || Actions.Num() != CameraActors.Num())
    {
        OutError = TEXT("A camera batch must contain 2-16 complete ordered actions.");
        return false;
    }
    bool bAnyChange = false;
    for (int32 Index = 0; Index < Actions.Num(); ++Index)
    {
        ACameraActor* Actor = CameraActors[Index];
        UCameraComponent* Component = CameraComponents[Index];
        const FRenderMasterCameraProposal& Action = Actions[Index];
        if (Actor == nullptr || Component == nullptr
            || Actor->GetCameraComponent() != Component
            || RenderMasterCameraKind(Component) != Action.CameraKind
            || !RenderMasterCameraBoundsMatch(
                RenderMasterSnapshotCameraBounds(Component), Action.Bounds)
            || !RenderMasterCameraSnapshotsMatch(
                RenderMasterSnapshotCamera(Actor, Component), Action.Before)
            || !RenderMasterIsBoundedCameraSnapshot(
                Action.After, Action.CameraKind, Action.Bounds))
        {
            OutError = FString::Printf(
                TEXT("Camera batch action %d no longer matches its frozen Before state."),
                Index + 1);
            return false;
        }
        bAnyChange = bAnyChange
            || !RenderMasterCameraSnapshotsMatch(Action.Before, Action.After);
    }
    if (!bAnyChange)
    {
        OutError = TEXT("Camera batch does not contain an observable change.");
        return false;
    }

    FScopedTransaction Transaction(NSLOCTEXT(
        "RenderMasterBot",
        "ApplyAssistantCameraBatch",
        "RenderMasterBot: Apply Coordinated Camera Properties"));
    TArray<int32> AppliedIndices;
    for (int32 Index = 0; Index < Actions.Num(); ++Index)
    {
        const FRenderMasterCameraProposal& Action = Actions[Index];
        if (RenderMasterCameraSnapshotsMatch(Action.Before, Action.After)) continue;
        if (!RenderMasterApplyCameraProperties(
                CameraActors[Index],
                CameraComponents[Index],
                Action.CameraKind,
                Action.Bounds,
                Action.Before,
                Action.After,
                OutError,
                false,
                false))
        {
            for (int32 Applied = AppliedIndices.Num() - 1; Applied >= 0; --Applied)
            {
                const int32 RollbackIndex = AppliedIndices[Applied];
                const FRenderMasterCameraProposal& Rollback = Actions[RollbackIndex];
                FString Ignored;
                RenderMasterApplyCameraProperties(
                    CameraActors[RollbackIndex],
                    CameraComponents[RollbackIndex],
                    Rollback.CameraKind,
                    Rollback.Bounds,
                    Rollback.After,
                    Rollback.Before,
                    Ignored,
                    false,
                    false);
            }
            Transaction.Cancel();
            return false;
        }
        AppliedIndices.Add(Index);
    }
    if (bMarkPackageDirty)
    {
        for (int32 Index : AppliedIndices) CameraActors[Index]->MarkPackageDirty();
    }
    return true;
}

FRenderMasterCameraBatchAssistant::FRenderMasterCameraBatchAssistant(
    TSharedPtr<FRenderMasterWorkflowController> InWorkflowController)
    : WorkflowController(MoveTemp(InWorkflowController))
{
}

FRenderMasterCameraBatchAssistant::~FRenderMasterCameraBatchAssistant()
{
    Shutdown();
}

void FRenderMasterCameraBatchAssistant::Initialize()
{
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateSP(
            AsShared(), &FRenderMasterCameraBatchAssistant::Tick),
        0.2f);
}

void FRenderMasterCameraBatchAssistant::Shutdown()
{
    if (TickHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
        TickHandle.Reset();
    }
    if (ProcessHandle.IsValid()) FPlatformProcess::TerminateProc(ProcessHandle, true);
    CloseProcessResources();
}

bool FRenderMasterCameraBatchAssistant::StartProposal(
    const FString& Prompt,
    const TArray<ACameraActor*>& CameraActors)
{
    if (!CanStart()) return false;
    const FString CleanPrompt = Prompt.TrimStartAndEnd();
    if (CleanPrompt.IsEmpty())
    {
        Fail(TEXT("Enter a coordinated camera request before preparing an action."));
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
        TEXT("camera_batch_%s"),
        *FDateTime::Now().ToString(TEXT("%Y%m%d_%H%M%S_%s")));
    const FString RequestDirectory = FPaths::Combine(
        Root, TEXT("assistant-camera-batch"), ProposalId);
    IFileManager::Get().MakeDirectory(*RequestDirectory, true);
    const FString PromptPath = FPaths::Combine(RequestDirectory, TEXT("request.txt"));
    const FString ContextPath = FPaths::Combine(
        RequestDirectory, TEXT("camera_selection_context.json"));
    ProposalOutputPath = FPaths::Combine(
        RequestDirectory, TEXT("camera_batch_proposal.json"));

    Proposal = FRenderMasterCameraBatchProposal();
    ErrorText.Reset();
    ProcessLog.Reset();
    FString Error;
    if (!FFileHelper::SaveStringToFile(
            CleanPrompt,
            *PromptPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)
        || !WriteCameraSelectionContext(ContextPath, CameraActors, Error))
    {
        Fail(Error.IsEmpty() ? TEXT("Could not write the camera batch request.") : Error);
        return false;
    }

    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-camera-batch-propose --prompt-file %s --context %s --proposal-id %s --output %s"),
        *QuoteCameraBatchArgument(PromptPath),
        *QuoteCameraBatchArgument(ContextPath),
        *QuoteCameraBatchArgument(ProposalId),
        *QuoteCameraBatchArgument(ProposalOutputPath));
    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        Fail(TEXT("Could not create camera batch assistant process pipes."));
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
    State = ERenderMasterCameraBatchAssistantState::Planning;
    AppendLog(FString::Printf(
        TEXT("Preparing one coordinated action for %d cameras (process %u)."),
        CameraActors.Num(),
        ProcessId));
    return true;
}

bool FRenderMasterCameraBatchAssistant::WriteCameraSelectionContext(
    const FString& Filename,
    const TArray<ACameraActor*>& CameraActors,
    FString& OutError)
{
    CapturedTargets.Reset();
    TargetCameraActors.Reset();
    TargetCameraComponents.Reset();
    if (CameraActors.Num() < 2 || CameraActors.Num() > 16)
    {
        OutError = TEXT("Select 2-16 Camera or Cine Camera Actors.");
        return false;
    }

    UWorld* World = CameraActors[0] != nullptr ? CameraActors[0]->GetWorld() : nullptr;
    if (World == nullptr)
    {
        OutError = TEXT("The selected cameras do not belong to an editable level.");
        return false;
    }
    const FString ProjectName = FApp::GetProjectName();
    const FString LevelPath = World->GetPackage()->GetName();
    TSet<FString> Paths;
    TSet<FString> Guids;
    TArray<TSharedPtr<FJsonValue>> CameraJson;
    for (ACameraActor* Actor : CameraActors)
    {
        if (Actor == nullptr || Actor->GetWorld() != World || Actor->IsTemplate())
        {
            OutError = TEXT("Every selected camera must belong to the same editable level.");
            return false;
        }
        UCameraComponent* Component = Actor->GetCameraComponent();
        if (Component == nullptr
            || Component->ProjectionMode != ECameraProjectionMode::Perspective)
        {
            OutError = TEXT("Every selected camera must use perspective projection.");
            return false;
        }
        if (!Actor->IsEditable() || Actor->IsLockLocation())
        {
            OutError = FString::Printf(
                TEXT("Camera %s is not editable or its location is locked."),
                *Actor->GetActorLabel());
            return false;
        }
        if (!RenderMasterHasSupportedStandardFocusShape(Component))
        {
            OutError = FString::Printf(
                TEXT("Camera %s has a partial depth-of-field override."),
                *Actor->GetActorLabel());
            return false;
        }

        FRenderMasterCameraProposal Capture;
        Capture.ActorName = Actor->GetActorLabel();
        Capture.ActorPath = Actor->GetPathName();
        Capture.ActorClass = Actor->GetClass()->GetName();
        Capture.ActorGuid = Actor->GetActorGuid().IsValid()
            ? Actor->GetActorGuid().ToString(EGuidFormats::Digits)
            : FString();
        Capture.ComponentName = Component->GetName();
        Capture.CameraKind = RenderMasterCameraKind(Component);
        Capture.Bounds = RenderMasterSnapshotCameraBounds(Component);
        Capture.Before = RenderMasterSnapshotCamera(Actor, Component);
        Capture.After = Capture.Before;
        if (!RenderMasterIsBoundedCameraSnapshot(
                Capture.Before, Capture.CameraKind, Capture.Bounds))
        {
            OutError = FString::Printf(
                TEXT("Camera %s has properties outside the supported safety boundary."),
                *Actor->GetActorLabel());
            return false;
        }
        if (Paths.Contains(Capture.ActorPath)
            || (!Capture.ActorGuid.IsEmpty() && Guids.Contains(Capture.ActorGuid)))
        {
            OutError = TEXT("The camera selection contains a duplicate Actor identity.");
            return false;
        }
        Paths.Add(Capture.ActorPath);
        if (!Capture.ActorGuid.IsEmpty()) Guids.Add(Capture.ActorGuid);

        CameraJson.Add(MakeShared<FJsonValueObject>(CameraContextJson(
            Capture,
            ProjectName,
            LevelPath,
            MobilityText(Component))));
        CapturedTargets.Add(Capture);
        TargetCameraActors.Add(Actor);
        TargetCameraComponents.Add(Component);
    }

    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Root->SetStringField(TEXT("project_name"), ProjectName);
    Root->SetStringField(TEXT("level_path"), LevelPath);
    Root->SetArrayField(TEXT("cameras"), CameraJson);
    FString JsonText;
    if (!SerializeJsonObject(Root, JsonText)
        || !FFileHelper::SaveStringToFile(
            JsonText,
            *Filename,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        OutError = FString::Printf(
            TEXT("Could not write camera selection context: %s"), *Filename);
        CapturedTargets.Reset();
        TargetCameraActors.Reset();
        TargetCameraComponents.Reset();
        return false;
    }
    return true;
}

bool FRenderMasterCameraBatchAssistant::ApplyProposal()
{
    FString Error;
    if (!CanApply() || !RevalidateTargets(Error))
    {
        Fail(Error.IsEmpty()
            ? TEXT("No coordinated camera proposal is ready to apply.")
            : Error);
        return false;
    }

    TArray<ACameraActor*> Actors;
    TArray<UCameraComponent*> Components;
    for (int32 Index = 0; Index < Proposal.Actions.Num(); ++Index)
    {
        Actors.Add(TargetCameraActors[Index].Get());
        Components.Add(TargetCameraComponents[Index].Get());
    }
    if (!RenderMasterApplyCameraPropertiesBatch(
            Actors, Components, Proposal.Actions, Error))
    {
        Fail(Error.IsEmpty()
            ? TEXT("Unreal rejected one camera in the coordinated action.")
            : Error);
        return false;
    }
    State = ERenderMasterCameraBatchAssistantState::Applied;
    AppendLog(FString::Printf(
        TEXT("Applied the approved coordinated edit to %d cameras in one Undo transaction. The level was not saved."),
        Proposal.Actions.Num()));
    return true;
}

bool FRenderMasterCameraBatchAssistant::RevalidateTargets(FString& OutError) const
{
    if (CapturedTargets.Num() < 2
        || CapturedTargets.Num() != TargetCameraActors.Num()
        || CapturedTargets.Num() != TargetCameraComponents.Num()
        || CapturedTargets.Num() != Proposal.Actions.Num())
    {
        OutError = TEXT("Camera batch evidence is incomplete.");
        return false;
    }
    for (int32 Index = 0; Index < CapturedTargets.Num(); ++Index)
    {
        ACameraActor* Actor = TargetCameraActors[Index].Get();
        UCameraComponent* Component = TargetCameraComponents[Index].Get();
        const FRenderMasterCameraProposal& Captured = CapturedTargets[Index];
        const FRenderMasterCameraProposal& Action = Proposal.Actions[Index];
        if (Actor == nullptr || Component == nullptr || Actor->GetWorld() == nullptr)
        {
            OutError = TEXT("One captured camera no longer exists.");
            return false;
        }
        const FString CurrentGuid = Actor->GetActorGuid().IsValid()
            ? Actor->GetActorGuid().ToString(EGuidFormats::Digits)
            : FString();
        if (Actor->GetPathName() != Captured.ActorPath
            || Actor->GetClass()->GetName() != Captured.ActorClass
            || CurrentGuid != Captured.ActorGuid
            || Component->GetName() != Captured.ComponentName
            || RenderMasterCameraKind(Component) != Captured.CameraKind
            || Component->ProjectionMode != ECameraProjectionMode::Perspective
            || !CameraEvidenceMatches(Captured, Action))
        {
            OutError = FString::Printf(
                TEXT("Camera %s changed identity, type, or selection order."),
                *Captured.ActorName);
            return false;
        }
        if (!Actor->IsEditable() || Actor->IsLockLocation()
            || !RenderMasterHasSupportedStandardFocusShape(Component)
            || !RenderMasterCameraBoundsMatch(
                RenderMasterSnapshotCameraBounds(Component), Captured.Bounds)
            || !RenderMasterCameraSnapshotsMatch(
                RenderMasterSnapshotCamera(Actor, Component), Captured.Before))
        {
            OutError = FString::Printf(
                TEXT("Camera %s changed after the proposal was created. Prepare a new action."),
                *Captured.ActorName);
            return false;
        }
    }
    return true;
}

void FRenderMasterCameraBatchAssistant::RejectProposal()
{
    if (State == ERenderMasterCameraBatchAssistantState::Planning)
    {
        Cancel();
        CloseProcessResources();
    }
    State = ERenderMasterCameraBatchAssistantState::Rejected;
    AppendLog(TEXT(
        "Coordinated camera proposal rejected. No Editor scene change was applied."));
}

void FRenderMasterCameraBatchAssistant::Cancel()
{
    if (ProcessHandle.IsValid()) FPlatformProcess::TerminateProc(ProcessHandle, true);
}

bool FRenderMasterCameraBatchAssistant::CanStart() const
{
    return !ProcessHandle.IsValid();
}

bool FRenderMasterCameraBatchAssistant::CanApply() const
{
    return State == ERenderMasterCameraBatchAssistantState::Proposed
        && Proposal.Actions.Num() >= 2;
}

bool FRenderMasterCameraBatchAssistant::IsPlanning() const
{
    return State == ERenderMasterCameraBatchAssistantState::Planning;
}

FText FRenderMasterCameraBatchAssistant::GetStateText() const
{
    switch (State)
    {
        case ERenderMasterCameraBatchAssistantState::Planning:
            return NSLOCTEXT("RenderMasterBot", "CameraBatchPlanning", "Planning");
        case ERenderMasterCameraBatchAssistantState::Proposed:
            return NSLOCTEXT(
                "RenderMasterBot", "CameraBatchProposed", "Approval required");
        case ERenderMasterCameraBatchAssistantState::Unresolved:
            return NSLOCTEXT("RenderMasterBot", "CameraBatchUnresolved", "Unresolved");
        case ERenderMasterCameraBatchAssistantState::Failed:
            return NSLOCTEXT("RenderMasterBot", "CameraBatchFailed", "Failed");
        case ERenderMasterCameraBatchAssistantState::Applied:
            return NSLOCTEXT("RenderMasterBot", "CameraBatchApplied", "Applied");
        case ERenderMasterCameraBatchAssistantState::Rejected:
            return NSLOCTEXT("RenderMasterBot", "CameraBatchRejected", "Rejected");
        default:
            return NSLOCTEXT("RenderMasterBot", "CameraBatchReady", "Ready");
    }
}

FText FRenderMasterCameraBatchAssistant::GetSummaryText() const
{
    if (State == ERenderMasterCameraBatchAssistantState::Planning)
        return FText::FromString(ProcessLog.IsEmpty()
            ? TEXT("Preparing a coordinated camera proposal...")
            : ProcessLog);
    if (State == ERenderMasterCameraBatchAssistantState::Proposed)
    {
        TArray<FString> CameraSummaries;
        for (const FRenderMasterCameraProposal& Action : Proposal.Actions)
        {
            CameraSummaries.Add(FString::Printf(
                TEXT("%s (%s)\n%s\n\nBefore\n%s\n\nAfter\n%s"),
                *Action.ActorName,
                *Action.CameraKind,
                *Action.ChangeSummary,
                *SnapshotText(Action.Before),
                *SnapshotText(Action.After)));
        }
        return FText::FromString(FString::Printf(
            TEXT("Coordinated action for %d cameras\n\n%s\n\nWhy\n%s\n\nNo level change has been applied."),
            Proposal.Actions.Num(),
            *FString::Join(CameraSummaries, TEXT("\n\n----------------\n\n")),
            *Proposal.Rationale));
    }
    if (State == ERenderMasterCameraBatchAssistantState::Unresolved)
        return FText::FromString(FString::Printf(
            TEXT("No coordinated camera action was proposed.\n\nWhy\n%s\n\nMissing capability\n%s"),
            *Proposal.Rationale,
            *Proposal.MissingCapabilities));
    if (State == ERenderMasterCameraBatchAssistantState::Failed)
        return FText::FromString(ErrorText);
    if (State == ERenderMasterCameraBatchAssistantState::Applied)
        return FText::FromString(TEXT(
            "The coordinated camera properties were applied in one transaction. "
            "The level was not saved; use Ctrl+Z once to undo the whole batch."));
    if (State == ERenderMasterCameraBatchAssistantState::Rejected)
        return FText::FromString(TEXT(
            "The coordinated camera proposal was rejected. No scene change was applied."));
    return FText::FromString(TEXT(
        "Select 2-16 Camera or Cine Camera Actors and describe one shared numeric "
        "Transform, lens, focus, or exposure edit."));
}

FLinearColor FRenderMasterCameraBatchAssistant::GetStateColor() const
{
    switch (State)
    {
        case ERenderMasterCameraBatchAssistantState::Proposed:
            return FLinearColor(0.18f, 0.55f, 0.95f, 1.0f);
        case ERenderMasterCameraBatchAssistantState::Applied:
            return FLinearColor(0.12f, 0.55f, 0.28f, 1.0f);
        case ERenderMasterCameraBatchAssistantState::Unresolved:
            return FLinearColor(0.65f, 0.45f, 0.08f, 1.0f);
        case ERenderMasterCameraBatchAssistantState::Failed:
            return FLinearColor(0.70f, 0.14f, 0.12f, 1.0f);
        default:
            return FLinearColor(0.14f, 0.20f, 0.30f, 1.0f);
    }
}

bool FRenderMasterCameraBatchAssistant::Tick(float DeltaTime)
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

void FRenderMasterCameraBatchAssistant::CompleteProcess()
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
                TEXT("Camera batch proposal process exited with code %d."), ReturnCode)
            : ProcessLog);
        return;
    }
    FString Error;
    if (!RenderMasterParseCameraBatchProposalFile(
            ProposalOutputPath, Proposal, Error))
    {
        Fail(Error);
        return;
    }
    if (Proposal.Status == TEXT("proposed"))
    {
        if (Proposal.Actions.Num() != CapturedTargets.Num())
        {
            Fail(TEXT("Camera batch proposal does not cover the captured selection."));
            return;
        }
        for (int32 Index = 0; Index < CapturedTargets.Num(); ++Index)
        {
            if (!CameraEvidenceMatches(CapturedTargets[Index], Proposal.Actions[Index]))
            {
                Fail(TEXT(
                    "Camera batch target evidence does not match the Editor capture."));
                return;
            }
        }
        State = ERenderMasterCameraBatchAssistantState::Proposed;
    }
    else
    {
        State = ERenderMasterCameraBatchAssistantState::Unresolved;
    }
}

void FRenderMasterCameraBatchAssistant::CloseProcessResources()
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

void FRenderMasterCameraBatchAssistant::AppendLog(const FString& Line)
{
    const FString Clean = Line.TrimStartAndEnd();
    if (Clean.IsEmpty()) return;
    if (!ProcessLog.IsEmpty()) ProcessLog += TEXT("\n");
    ProcessLog += Clean;
    constexpr int32 MaxLogChars = 8000;
    if (ProcessLog.Len() > MaxLogChars)
        ProcessLog.RightChopInline(ProcessLog.Len() - MaxLogChars);
}

void FRenderMasterCameraBatchAssistant::Fail(const FString& Error)
{
    ErrorText = Error.IsEmpty()
        ? TEXT("Camera batch proposal failed.")
        : Error;
    State = ERenderMasterCameraBatchAssistantState::Failed;
    AppendLog(ErrorText);
}
